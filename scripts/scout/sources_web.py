"""Парсеры площадок, которые берутся АНОНИМНО — без сессии пользователя.

Отдельный модуль от `sources.py` по одной причине: всё, что здесь, можно крутить
облачной рутиной. Ни одна функция не подмешивает куки, не логинится и не трогает
`.auth/` — источник либо отдаёт данные анониму, либо честно объявляется закрытым.

Три правила, из которых вырос весь модуль:

1. **Счётчики без потерь.** У каждого парсера есть `Tally`: сколько строк отдал
   источник, сколько разобралось, сколько отсеяно фильтром профиля, сколько
   выпало из окна свежести, сколько записано. Расхождение печатается в отчёте
   («РАСХОЖДЕНИЕ»), а не растворяется. Без этого «площадка отдала 40» и «мы
   потеряли 60 из 100» выглядят одинаково.

2. **Тихий фильтр — главный враг.** Половина здешних API на неверное значение
   фильтра отвечает НЕ ошибкой, а полной выдачей (getmatch, wantapply, hirehi)
   или, наоборот, отдаёт нужное число в счётчике и мусор в строках (EURES
   с `MOST_RECENT`). Поэтому у каждого источника, где это проверено живьём,
   стоит защита: белый список значений, сверка с базовым total или проверка,
   что в названиях вернувшихся вакансий вообще есть искомое слово.

3. **Антибот не обходится.** Стена → `BlockedError` (статус АНТИБОТ в отчёте),
   и на этом всё: капчу решает человек, TLS не подменяется, прокси не строятся.
   Пустая выдача при живом ответе — честный ноль с пояснением, а не «сломалось».
   Троттлинг (площадка рвёт соединение за частоту) — отдельный `ThrottledError`:
   это не поломка и не капча, лечится паузой, и обозвать его «упал» значит
   послать человека чинить работающий код.

4. **Глубина обхода задаётся ОКНОМ, а не лимитом.** `--limit` — предохранитель
   от бесконечности (`row_budget`), поднять потолок он может, опустить ниже
   проверенного размера окна — нет. Всё, что обход всё-таки не добрал, обязано
   быть названо строкой в сводке (`_cut_note`), иначе «100 из 117» выглядит
   удачным прогоном. И между страницами есть пауза (`nap`): rabota.ru уже
   закрыла нам TLS после серии запросов, и это была наша вина, а не её.

Ядро — stdlib. Playwright подтягивается ленивым импортом и только там, где без
браузера страницы нет вовсе (levels.fyi, попытка Glassdoor).
"""

from __future__ import annotations

import html as H
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from .model import Vacancy, norm_period
from .net import BlockedError, FetchError, fetch, fetch_json, looks_blocked, qs
# Tally общий для всех адаптеров и живёт в `sources`: счёт «отдано → записано»
# нужен каждому источнику одинаково, а два расходящихся счётчика в одном сборщике
# — это два разных ответа на вопрос «сколько потеряли».
from .sources import ATS_ROLE_RE, Ctx, Tally, parse_salary, period_from_text


# ──────────────────────────────────────────────────────────────────────────────
# Общая механика: стены, окно свежести, POST-чтение
# ──────────────────────────────────────────────────────────────────────────────

# Маркеры стен, которых нет в net.looks_blocked. Проверены живьём 30.07.2026:
# Glassdoor отдаёт русскоязычный «Один момент…» (английского «Just a moment»
# в теле нет вовсе), levels.fyi — AWS WAF с challenge.js. Оба случая net.py
# пропускал молча, и парсер получал 344 КБ «страницы», в которой ноль вакансий.
_WALL_TITLES = (
    "один момент", "just a moment", "attention required", "security | glassdoor",
    "проверка браузера", "checking your browser", "access denied", "доступ ограничен",
)
_WALL_BODY = (
    "awswaf.com", "challenge-container", "captcha-container", "cf_chl_opt",
    "datadome", "px-captcha", "_incapsula_resource",
)


def wall_marker(text: str, status: int | None = None) -> str | None:
    """Маркер антибот-стены или None. Дополняет net.looks_blocked, не заменяет его."""
    marker = looks_blocked(text, status)
    if marker:
        return marker
    title = re.search(r"<title[^>]*>(.*?)</title>", text[:4000], re.S | re.I)
    if title:
        low = H.unescape(title.group(1)).strip().lower()
        for m in _WALL_TITLES:
            if m in low:
                return f"заголовок страницы: {m}"
    head = text[:20000].lower()
    for m in _WALL_BODY:
        if m in head:
            return m
    return None


def check_wall(text: str, url: str, status: int | None = None) -> None:
    """Стена → BlockedError. Проверку не проходим и капчу не решаем."""
    marker = wall_marker(text, status)
    if marker:
        raise BlockedError(url, f"антибот-проверка ({marker}) — проверку проходит "
                                f"человек, зайди браузером сам", status)


def cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(days, 0))


def older_than(published_at: str | None, edge: datetime) -> bool:
    """True — вакансия старше окна. Без даты — НЕ отбрасываем: «дата неизвестна»
    и «старая» это разные вещи, и вторая не должна поглощать первую."""
    if not published_at:
        return False
    try:
        return datetime.fromisoformat(published_at) < edge
    except ValueError:
        return False


def post_json(url: str, payload: dict, *, headers: dict | None = None,
              timeout: int = 45, tries: int = 3, pause: float = 2.0):
    """POST с телом-JSON и своим бэкоффом. Читающий запрос, ничего не изменяет.

    Почему не `fetch(retries=…)`: в net.fetch переменная тела запроса
    переиспользуется под тело ошибки (`body = ""` в ветке HTTPError), поэтому
    ЛЮБОЙ повтор после 5xx уходит со строкой вместо байтов и падает в TypeError.
    Отсюда `retries=0` и собственный цикл — заодно он нужен и по делу: у
    dreamoffer 502 означает таймаут шлюза, а не поломку.
    """
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    h.update(headers or {})
    body = json.dumps(payload, ensure_ascii=False).encode()
    last: Exception | None = None
    for attempt in range(tries):
        try:
            text, final = fetch(url, method="POST", data=body, headers=h,
                                timeout=timeout, retries=0)
        except BlockedError:
            raise
        except FetchError as e:
            last = e
            # 4xx кроме 429 повторять бессмысленно: тело запроса не изменится.
            if e.status and 400 <= e.status < 500 and e.status != 429:
                raise
        else:
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise FetchError(final, f"ответ не JSON: {e}") from e
        if attempt < tries - 1:
            time.sleep(pause * (attempt + 1))
    raise last or FetchError(url, "unknown")


# ──────────────────────────────────────────────────────────────────────────────
# Вежливость к площадке и предохранители обхода
# ──────────────────────────────────────────────────────────────────────────────
#
# Три правила, которые здесь описаны кодом, а не намерением:
#
# 1. Между страницами одной площадки — пауза. Не «на всякий случай»: rabota.ru
#    уже закрыла нам TLS после ~25 запросов за 20 минут, и это наша вина.
# 2. Обход кончается ОКНОМ свежести, а не круглым числом. `--limit` — стоп от
#    бесконечности, а не рабочий режим: никакое умолчание не должно резать окно
#    из 117 вакансий (замер hackoffer 30.07.2026), см. `row_budget`.
# 3. Если обход всё-таки обрезан — про это есть СТРОКА В СВОДКЕ. Молча
#    недобранная площадка и честный ноль в отчёте выглядят одинаково.


def nap(seconds: float) -> None:
    """Пауза между запросами к одной площадке.

    Отдельной функцией, а не `time.sleep` по месту, ровно по двум причинам:
    паузу видно грепом (её нельзя случайно «оптимизировать» из одного парсера)
    и её подменяют тесты — иначе полный прогон test_sources_web занимал бы
    минуты чистого сна.
    """
    if seconds > 0:
        time.sleep(seconds)


# Обрыв на уровне TLS/сокета. Это НЕ поломка парсера и НЕ капча: так площадка
# отвечает на слишком частые запросы. Проверено на rabota.ru 30.07.2026 —
# `URLError: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in
# violation of protocol>` на каждом запросе после серии наших же обращений.
_THROTTLE_MARKERS = (
    "unexpected_eof_while_reading", "eof occurred in violation of protocol",
    "connection reset by peer", "connectionreseterror", "remote end closed connection",
    "ssleoferror", "sslerror", "bad handshake", "tlsv1",
)


class ThrottledError(BlockedError):
    """Площадка перестала нас пускать из-за частоты запросов.

    Наследник BlockedError СОЗНАТЕЛЬНО: и то и другое — «площадка нас не пустила,
    кодом это не чинится», и в покрытии обе ситуации обязаны отличаться от «УПАЛ».
    Разница в лечении, и она написана словами в самом сообщении: капчу снимает
    человек браузером, троттлинг — пауза и меньшая частота, никаких прокси
    и подмены TLS.
    """


def throttle_marker(err: BaseException) -> str | None:
    """Маркер троттлинга в тексте ошибки сети или None."""
    text = f"{type(err).__name__}: {err}".lower()
    for m in _THROTTLE_MARKERS:
        if m in text:
            return m
    return None


def row_budget(ctx: Ctx, floor: int) -> int:
    """Предохранитель на число строк: сколько МОЖНО унести, а не сколько нужно.

    `--limit` умеет только ПОДНЯТЬ потолок выше проверенного размера окна, но не
    опустить его. Иначе умолчание `limit=100` обрезает всё подряд на круглом
    числе: hackoffer — 100 из 117 свежих, jobsdb — 3 страницы из 10, eures —
    200 строк из 667. Пользователь просил обратного: забирать всё, что есть
    в окне, а лимит держать предохранителем от бесконечности.
    """
    return max(int(getattr(ctx, "limit", 0) or 0), floor)


def _cut_note(tally: Tally, what: str, seen: int, cap: int,
              fix: str = "подними --limit") -> None:
    """Обрезание обхода — строкой в сводке. Молчать здесь нельзя.

    Слово «ОБРЕЗАН» не украшение: по нему `cli._limit_hit` находит признак
    в сводке и печатает предупреждение в покрытии. `fix` — что с этим делать,
    и он не всегда «подними --limit»: у площадки, которая банит за частоту,
    потолок стоит не ради скорости, и поднимать его нельзя.
    """
    tally.note(f"ОБХОД ОБРЕЗАН предохранителем: {what} дошло до {seen} при потолке "
               f"{cap} — окно свежести покрыто НЕ целиком, {fix}")


def _strip_tags(s: str | None) -> str:
    return H.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def _ld_json_blocks(html: str) -> list:
    """Все блоки <script type="application/ld+json">, разобранные в объекты.

    `strict=False` — не прихоть: relocate.me кладёт в описание сырые переводы
    строк, и строгий json.loads роняется на «Invalid control character».
    """
    out = []
    for raw in re.findall(r'type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            out.append(json.loads(raw, strict=False))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _job_postings(html: str) -> list[dict]:
    """JobPosting из ld+json. Блок бывает и объектом, и массивом."""
    out = []
    for block in _ld_json_blocks(html):
        items = block if isinstance(block, list) else [block]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                out.append(it)
    return out


# Синонимы, без которых проверка «вернулось ли то, что просили» врёт: вакансия
# «Go Developer» по запросу «Golang» — это попадание, а не промах.
_QUERY_ALIASES = {
    "golang": ("golang", "go"),
    "go": ("go", "golang"),
    "разработчик": ("разработчик", "developer", "engineer", "entwickler"),
    "backend": ("backend", "back-end", "бэкенд"),
}


def query_re(ctx: Ctx) -> re.Pattern:
    """Регулярка «вакансия хоть как-то про то, что спрашивали».

    Нужна там, где серверный поиск нечёткий и уезжает вбок: EURES по запросу
    golang честно пишет 666 в счётчике, а на второй странице отдаёт «Auxiliaire
    Petite Enfance volant» — совпало по «volant». Это не отбор по фиту (его
    делает модель), а та же защита, что ATS_ROLE_RE у ATS-досок: не тащить
    в отчёт заведомо другую профессию под видом найденного.
    """
    terms: set[str] = set()
    for q in ctx.queries():
        for tok in re.split(r"\W+", q.lower()):
            if len(tok) < 2:
                continue
            terms.update(_QUERY_ALIASES.get(tok, (tok,)))
    if not terms:
        return re.compile(r".")
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in sorted(terms)) + r")\b", re.I)


def _long_queries(ctx: Ctx, minlen: int, tally: Tally, why: str) -> list[str]:
    """Формулировки длиннее порога. Двухбуквенные площадки не ищут вовсе."""
    ok, short = [], []
    for q in ctx.queries():
        (ok if len(q.strip()) >= minlen else short).append(q.strip())
    if short:
        tally.note(f"пропущены короткие запросы {short}: {why}")
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# hack-offer.tech — SSR-JSON внутри HTML
# ──────────────────────────────────────────────────────────────────────────────

# Реальных групп ровно шесть, и они взяты со ссылок самой страницы.
# ЛОВУШКА: несуществующий слаг (`devops`, `qa`, `ml`, `data`, `pm`) НЕ даёт
# ошибки — площадка молча отдаёт весь каталог (4287 вместо 2216 профильных),
# и это выглядит удачным прогоном. Поэтому слаг только из белого списка.
HACKOFFER_GROUPS = ("dev", "gamedev", "management", "people", "analytics", "design")
HACKOFFER_PAGE = 20          # серверный потолок: pageSize=100 игнорируется
HACKOFFER_CATALOG_HINT = 4000  # выдача такого размера у группы = слаг «не поймали»
HACKOFFER_MAX_PAGES = 40
# Нижняя граница предохранителя: 600 строк = 30 страниц. Замер 30.07.2026:
# окно в 3 дня по группе dev — 117 вакансий на 7 страницах (7-я уже вся старше).
# Прежний расчёт `--limit // 20` при умолчании limit=100 давал 5 страниц и терял
# 17 свежих КАЖДЫЙ прогон, причём молча: обход заканчивался «по лимиту».
HACKOFFER_FLOOR = 600
HACKOFFER_PAUSE = 1.0        # пауза между страницами: площадка маленькая, не давим


def _hackoffer_payload(url: str) -> dict:
    text, final = fetch(url)
    check_wall(text, final)
    m = re.search(r'<script id="vike_pageContext"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        raise FetchError(final, "нет vike_pageContext — вёрстка сменилась, парсер надо чинить")
    try:
        ctxdata = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise FetchError(final, f"vike_pageContext не разбирается: {e}") from e
    payload = (ctxdata.get("ssrData") or {}).get("payload")
    if payload is None:
        # Путь /job/g/dev/2 отдаёт страницу вообще без payload — так выглядит
        # неверный способ пагинации, и он не должен считаться пустой выдачей.
        raise FetchError(final, "в pageContext нет ssrData.payload — так отвечает "
                                "неверный путь; пагинация только через ?page=N")
    return payload


def src_hackoffer(ctx: Ctx) -> list[Vacancy]:
    """hack-offer.tech — 2216 вакансий в разработке, чистый GET без кук.

    Данные лежат в SSR-JSON внутри HTML. Работодатель есть уже в списке, поэтому
    в карточку вакансии за ним ходить не нужно; в деталке добавляются только
    `source_channel` и `source_text` (см. `hackoffer_detail`).

    Страницы идут от свежих к старым, поэтому обход прекращается, как только
    на странице не осталось НИ ОДНОЙ свежей вакансии, — это и есть окно свежести,
    которого у площадки нет параметром. Число страниц считается по окну, а не по
    `--limit`: лимит здесь только предохранитель (см. `row_budget`).
    """
    groups = tuple(getattr(ctx, "hackoffer_groups", ("dev",)))
    unknown = [g for g in groups if g not in HACKOFFER_GROUPS]
    if unknown:
        raise FetchError("hackoffer", f"неизвестная группа {unknown}: площадка на такой "
                                      f"слаг молча отдаёт ВЕСЬ каталог. Есть: "
                                      f"{', '.join(HACKOFFER_GROUPS)}")

    tally = Tally("hackoffer")
    edge = cutoff(ctx.days)
    out: list[Vacancy] = []
    seen: set[str] = set()
    max_pages = min(HACKOFFER_MAX_PAGES,
                    max(1, row_budget(ctx, HACKOFFER_FLOOR) // HACKOFFER_PAGE))

    for group in groups:
        for page in range(1, max_pages + 1):
            if page > 1:
                nap(HACKOFFER_PAUSE)
            payload = _hackoffer_payload(
                f"https://hack-offer.tech/job/g/{group}?page={page}")
            tally.requests += 1
            total = payload.get("total")
            if page == 1 and isinstance(total, int) and total >= HACKOFFER_CATALOG_HINT:
                tally.note(f"группа {group} отдала {total} — это размер всего каталога, "
                           f"проверь слаг: похоже, фильтр не применился")
            jobs = payload.get("jobs") or []
            # Признак конца — ПУСТОЙ список, а не ошибка. Без этой проверки
            # цикл крутится вечно на странице за последней.
            if not jobs:
                break
            tally.pages += 1
            page_fresh = 0
            for j in jobs:
                tally.offered += 1
                jid = str(j.get("id") or "")
                slug = j.get("slug") or ""
                if not jid or not slug:
                    tally.dropped += 1
                    continue
                if jid in seen:
                    tally.dupes += 1
                    continue
                seen.add(jid)
                tally.parsed += 1
                published = j.get("posted_at") or j.get("created_at")
                v = Vacancy(
                    source="hackoffer",
                    external_id=jid,
                    url=f"https://hack-offer.tech/vacancy/{slug}",
                    title=j.get("title") or "",
                    company=j.get("company"),
                    salary_from=j.get("salary_min"),
                    salary_to=j.get("salary_max"),
                    currency=j.get("currency"),
                    # Периода площадка не называет ни одним полем — значит его нет.
                    # Подставить «месяц» здесь означало бы выдать догадку за факт.
                    salary_period=None,
                    location=j.get("location") or j.get("city") or j.get("country"),
                    remote=j.get("remote") if isinstance(j.get("remote"), bool) else None,
                    published_at=published,
                    tags=[str(x) for x in (j.get("skills") or []) if x]
                         + [x for x in (j.get("grade"), j.get("specialization")) if x],
                    description=j.get("description"),
                    raw={"group": group, "specialization": j.get("specialization"),
                         "employment": j.get("employment"), "industry": j.get("industry"),
                         "relocation_to": j.get("relocation_to"), "lang": j.get("lang"),
                         "rating": j.get("rating"), "slug": slug,
                         "note": "source_channel и source_text — только в деталке "
                                 "/vacancy/<slug>, см. hackoffer_detail"},
                )
                if older_than(v.published_at, edge):
                    tally.skipped_old += 1
                    continue
                page_fresh += 1
                tally.kept += 1
                out.append(v)
            # Конец окна — страница, на которой не осталось ни одной свежей
            # вакансии. Считаем именно свежие, а не «все старые»: строка без id
            # до проверки даты не доходит, и по ней страница выглядела бы «не
            # целиком старой», из-за чего обход шёл бы дальше в глубину зря.
            if page_fresh == 0:
                tally.note(f"группа {group}: окно кончилось на странице {page} — "
                           f"дальше только старше {ctx.days} дн.")
                break
        else:
            # Ни окно, ни конец каталога — значит страницы кончились
            # предохранителем. Молчать нельзя: именно так «100 из 117»
            # выглядит удачным прогоном.
            _cut_note(tally, f"группа {group}: страниц", max_pages, max_pages)

    if not tally.offered:
        raise FetchError("hackoffer", "страница отдалась, но вакансий ноль — "
                                      "проверь формат ssrData.payload, парсер мог отстать")
    tally.note(f"каталог группы(групп) {', '.join(groups)}; страница = "
               f"{HACKOFFER_PAGE} вакансий, серверный потолок не поднимается")
    out.append(tally.row())
    return out


def hackoffer_detail(slug: str) -> dict:
    """Деталка вакансии: `source_channel` + `source_text` (из какого канала пост).

    Отдельной функцией и НЕ в общем обходе: 2216 вакансий это 2216 запросов,
    а работодатель и так есть в списке. `source_url` у площадки всегда null —
    прямой ссылки на исходный пост из неё не собрать (raw_id внутренний).
    """
    payload = _hackoffer_payload(f"https://hack-offer.tech/vacancy/{slug}")
    job = payload.get("job") or {}
    return {"source_channel": job.get("source_channel"), "source_text": job.get("source_text"),
            "raw_id": job.get("raw_id"), "dedup_key": job.get("dedup_key"),
            "contact": job.get("contact"), "apply_url": job.get("apply_url"),
            "status": job.get("status")}


HACKOFFER_DAYS_NOTE = "--days применяется на нашей стороне: у площадки нет фильтра по дате"


# ──────────────────────────────────────────────────────────────────────────────
# dreamoffer — сырой SQL-эндпоинт, только SELECT
# ──────────────────────────────────────────────────────────────────────────────

DREAMOFFER_API = "https://api.dreamoffer.app/db/pg/read"
DREAMOFFER_COLS = ("nn", "time_of_created", "source", "link", "tg_name_channel",
                   "vacancy_text", "vacancy_info")
# Имён колонок в ответе НЕТ — приезжают позиционные массивы. Порядок держится
# исключительно этим кортежем, поэтому он один на запрос и на разбор.
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|"
    r"vacuum|call|do|merge)\b", re.I)


def _safe_sql(sql: str) -> str:
    """Пропускает только один SELECT. Эндпоинт сырой, границы держим у себя.

    Мы ходим на ЧТЕНИЕ и ничего не изменяем: всё, что не начинается с SELECT
    или содержит второй оператор, до сети не доезжает.
    """
    s = " ".join(sql.split())
    if not s.lower().startswith("select"):
        raise FetchError(DREAMOFFER_API, "разрешён только SELECT — это читающий источник")
    if ";" in s.rstrip(";") or _SQL_FORBIDDEN.search(s):
        raise FetchError(DREAMOFFER_API, "в запросе есть изменяющая операция — отказ")
    return s


def _dreamoffer_rows(sql: str) -> list[list]:
    data = post_json(DREAMOFFER_API, {"sql": _safe_sql(sql)})
    rows = data.get("rows")
    if rows is None:
        raise FetchError(DREAMOFFER_API, f"в ответе нет rows: {str(data)[:200]}")
    return rows


# Поиск здесь — буквальный `ILIKE '%фраза%'` по ТЕКСТУ поста, и это меняет всё.
# Замер 30.07.2026 в окне 3 дня: «Go разработчик» — РОВНО 0 (такой подстроки
# в постах не бывает), «Backend Go» — 4, «Golang» — 109. То есть из трёх наших
# формулировок работала одна, а две молча возвращали пустоту, неотличимую от
# «вакансий нет». Отдельные слова в том же окне: backend 1701, devops 2053,
# платформ 41, бэкенд 6, go-разработчик 3 — объединение 3367 против 111.
DREAMOFFER_WORDS = ("golang", "backend", "бэкенд", "go-разработчик", "devops", "платформ")
DREAMOFFER_MIN_TERM = 3   # запрос короче трёх символов эндпоинт отбрасывает сам
DREAMOFFER_PAUSE = 1.0    # ILIKE по 136 тысячам строк стоит серверу 1.5–15 с


def _dreamoffer_terms(ctx: Ctx, tally: Tally) -> list[str]:
    """Формулировки → отдельные слова: под механику ILIKE, а не под человека.

    Фраза целиком до сети не доезжает СОЗНАТЕЛЬНО: она ищется как подстрока
    и даёт ноль, который потом читается как «на площадке ничего нет».
    """
    terms: list[str] = []
    phrases: list[str] = []
    short: list[str] = []
    for q in ctx.queries():
        toks = [t for t in re.split(r"[^\w\-]+", q.lower()) if t]
        if len(toks) > 1:
            phrases.append(q)
        for t in toks:
            if len(t) < DREAMOFFER_MIN_TERM:
                short.append(t)
            elif t not in terms:
                terms.append(t)
    for w in DREAMOFFER_WORDS:
        if w not in terms:
            terms.append(w)
    if phrases:
        tally.note(f"фразы {phrases} разобраны на слова: ILIKE ищет подстроку, "
                   f"и «Go разработчик» целиком даёт ровно 0")
    if short:
        tally.note(f"слова короче {DREAMOFFER_MIN_TERM} символов пропущены {sorted(set(short))}: "
                   f"эндпоинт такие запросы отбрасывает")
    return terms


def _sql_quote(value: str) -> str:
    """Экранирование для литерала: одинарная кавычка удваивается, `%` и `_` в LIKE
    теряют особый смысл через ESCAPE. Запрос всё равно только читающий."""
    return value.replace("'", "''").replace("\\", "\\\\")


def src_dreamoffer(ctx: Ctx) -> list[Vacancy]:
    """find.dreamoffer.app — 136 тысяч постов из Telegram и LinkedIn за ~5 недель.

    Три вещи, на которых этот источник врёт молча, и что с ними сделано:

    * `time_in_channel` — фальшивка в 94.5% строк (sentinel 2025-01-01), фильтр
      по нему выкидывает почти всё и выглядит как «вакансий мало». Свежесть
      считается ТОЛЬКО по `time_of_created`.
    * `LIMIT` игнорируется, если есть WHERE или ORDER BY, — поэтому окно режется
      диапазоном `nn` (индексированный ключ), а не лимитом.
    * 502 означает два разных события: синтаксис вне белого списка и таймаут
      шлюза на тяжёлом ILIKE. Лечится сужением по `nn`, а не повтором, поэтому
      сначала берётся граница окна `min(nn)` по дате, и лишь потом ILIKE.
    * поиск буквальный (`ILIKE '%фраза%'` по тексту поста), поэтому в сеть уходят
      ОТДЕЛЬНЫЕ СЛОВА, а не формулировки человека: см. `_dreamoffer_terms`.
      Широкий невод компенсируется отсевом по профессии на нашей стороне —
      он считается в `skipped_profile`, а не растворяется.
    """
    tally = Tally("dreamoffer")
    edge = cutoff(ctx.days)
    since = edge.date().isoformat()

    floor_rows = _dreamoffer_rows(
        f"SELECT min(nn) FROM vacancies_ai_db WHERE time_of_created >= '{_sql_quote(since)}'")
    tally.requests += 1
    floor = floor_rows[0][0] if floor_rows and floor_rows[0] else None
    if floor is None:
        tally.note(f"за окно с {since} база не отдала ни одной строки")
        floor = 0
    tally.note(f"окно nn >= {floor} (граница посчитана по time_of_created >= {since})")

    cols = ", ".join(DREAMOFFER_COLS)
    out: list[Vacancy] = []
    seen: set[str] = set()
    terms = _dreamoffer_terms(ctx, tally)
    failed: list[str] = []
    for i, q in enumerate(terms):
        if i:
            nap(DREAMOFFER_PAUSE)
        try:
            rows = _dreamoffer_rows(
                f"SELECT {cols} FROM vacancies_ai_db "
                f"WHERE nn >= {int(floor)} AND is_blocked = 0 "
                f"AND vacancy_text ILIKE '%{_sql_quote(q)}%'")
        except FetchError as e:
            # 502 здесь — таймаут шлюза на тяжёлом ILIKE, и он тем вероятнее,
            # чем шире окно: «devops» за три дня это 2053 строки, за сто
            # двадцать — десятки тысяч. Ронять из-за одного слова весь источник
            # нельзя: остальные шесть слов уже принесли тысячи вакансий.
            failed.append(f"{q} ({e.reason[:60]})")
            tally.note(f"слово «{q}» не отработало: {e.reason[:80]}. Обход по нему "
                       f"НЕ выполнен — это не «по нему ничего нет»")
            continue
        tally.requests += 1
        for row in rows:
            tally.offered += 1
            if len(row) != len(DREAMOFFER_COLS):
                tally.dropped += 1
                continue
            rec = dict(zip(DREAMOFFER_COLS, row))
            nn = str(rec.get("nn") or "")
            if not nn:
                tally.dropped += 1
                continue
            if nn in seen:
                tally.dupes += 1
                continue
            seen.add(nn)
            tally.parsed += 1
            info = rec.get("vacancy_info")
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except json.JSONDecodeError:
                    info = {}
            info = info if isinstance(info, dict) else {}
            text = rec.get("vacancy_text") or ""
            sal = info.get("salary")
            sf, st, cur, gross = parse_salary(sal if isinstance(sal, str) else None)
            v = Vacancy(
                source="dreamoffer",
                external_id=nn,
                # link — прямая ссылка на исходный пост (t.me/<канал>/<id>)
                # или на LinkedIn. Это единственный путь к первоисточнику:
                # своего URL у вакансии нет.
                url=rec.get("link") or "",
                title=_first_line(text) or (info.get("profession") or "вакансия"),
                company=None,   # компании в таблице нет ни одним полем
                salary_from=sf, salary_to=st, currency=cur, salary_gross=gross,
                salary_period=period_from_text(sal if isinstance(sal, str) else None),
                location=", ".join(str(x) for x in (info.get("city"), info.get("country")) if x)
                         or None,
                remote=(str(info.get("work_format") or "").lower() == "remote") or None,
                published_at=rec.get("time_of_created"),
                tags=[str(x) for x in (info.get("grade"), info.get("profession"),
                                       info.get("employment_type")) if x],
                description=text,
                raw={"source": rec.get("source"), "channel": rec.get("tg_name_channel"),
                     "vacancy_info": info, "query": q,
                     "note": "свежесть по time_of_created; time_in_channel в 94.5% "
                             "строк — sentinel 2025-01-01 и фильтровать по нему нельзя"},
            )
            if older_than(v.published_at, edge):
                tally.skipped_old += 1
                continue
            # Широкий ILIKE — это не только Go-вакансии. Замер: '%devops%' тащит
            # 2053 поста, из них 809 — QA, тестировщики и проектные менеджеры,
            # у которых devops просто назван в стеке. Отсев идёт по ШАПКЕ поста
            # и по профессии из vacancy_info, а не по всему тексту: по тексту
            # проверка вырождается (слово там есть по построению запроса).
            if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")
                    or ATS_ROLE_RE.search(str(info.get("profession") or ""))):
                tally.skipped_profile += 1
                continue
            tally.kept += 1
            out.append(v)

    if failed and len(failed) == len(terms):
        # Ни одно слово не отработало — про площадку не известно ничего,
        # и молчаливый ноль здесь был бы враньём.
        raise FetchError(DREAMOFFER_API, f"ни один запрос не прошёл: {'; '.join(failed[:3])}")
    tally.note(f"слова поиска: {', '.join(terms)} — по одному запросу на слово, "
               f"объединение по nn")
    out.append(tally.row())
    return out


# Ярлык, которым канал подписывает строку с должностью. Не часть названия:
# замер 30.07.2026 — из 2210 записей dreamoffer 2097 начинались с «Position:»,
# и человек читал «Position: Storage Engineer» вместо «Storage Engineer».
# Режется ТОЛЬКО ярлык с двоеточием: «Требуется Golang-разработчик» (18 записей)
# — это уже название, а не подпись, и трогать его нельзя.
_LABEL_PREFIX = re.compile(
    r"^(?:position|vacancy|job|role|title|должность|вакансия|позиция|роль|"
    r"публикатор|publisher)\s*:\s*", re.I)


def _first_line(text: str) -> str:
    """Первая содержательная строка поста — она же название вакансии."""
    for line in (text or "").splitlines():
        clean = re.sub(r"[*_`#]+", "", line).strip()
        clean = _LABEL_PREFIX.sub("", clean).strip()
        if len(clean) >= 3:
            return clean[:200]
    return ""


DREAMOFFER_DAYS_NOTE = "--days применяется по time_of_created (глубина базы ~5 недель)"


# ──────────────────────────────────────────────────────────────────────────────
# rabota.ru — JSON-LD на странице поиска
# ──────────────────────────────────────────────────────────────────────────────

RABOTA_MIN_QUERY = 3   # `query=Go` отдаёт ноль: двухбуквенный токен поиск не берёт
# Пауза и бюджет запросов — прямое следствие нашего же прогона 30.07.2026:
# после ~25 запросов за 20 минут площадка закрыла соединение на уровне TLS
# (`SSL: UNEXPECTED_EOF_WHILE_READING`) и держала блок часами. Это была НАША
# вина, поэтому 12 секунд между страницами и жёсткий потолок на прогон.
RABOTA_PAUSE = 12.0
RABOTA_MAX_PAGES = 4
RABOTA_MAX_REQUESTS = 9


def _rabota_postings(url: str, tally: Tally) -> list[dict]:
    """Страница выдачи → список JobPosting. TLS-обрыв поднимается ThrottledError."""
    try:
        # retries=0 намеренно: повтор в упор — это ещё один запрос к площадке,
        # которая только что сказала «хватит», и именно так зарабатывается
        # блокировка вместо данных. Паузу держим мы, а не net.fetch.
        text, final = fetch(url, retries=0)
    except BlockedError:
        raise
    except FetchError as e:
        marker = throttle_marker(e)
        if not marker:
            raise
        # Первые же слова — про троттлинг: в таблице покрытия видно только
        # начало причины (42 символа), и «SSL: UNEXPECTED_EOF» там читается
        # как поломка, хотя это площадка нас притормозила.
        raise ThrottledError(
            url, f"ТРОТТЛИНГ: площадка рвёт TLS за частые запросы ({marker}) — это "
                 f"не поломка парсера и не капча, обходить нечего. Лечится паузой: "
                 f"следующий прогон через час-другой, частоту мы уже снизили "
                 f"(пауза {RABOTA_PAUSE:.0f} с, не больше {RABOTA_MAX_REQUESTS} "
                 f"запросов за прогон)") from e
    tally.requests += 1
    check_wall(text, final)
    if not _ld_json_blocks(text):
        # H1 и <title> у площадки зашиты SEO-текстом и запрос не отражают,
        # поэтому правдоподобие проверяется наличием ld+json, а не заголовком.
        raise FetchError(final, "на странице нет ни одного ld+json — вёрстка "
                                "сменилась или показана стена")
    return _job_postings(text)


def src_rabota(ctx: Ctx) -> list[Vacancy]:
    """rabota.ru — маленькая, но с уникальными вакансиями (тот же Сбер от 350K).

    Берётся не разметка карточек, а JSON-LD: один блок на страницу, внутри массив
    JobPosting с ПОЛНЫМ описанием, работодателем, вилкой и датой. Проверено
    браузером — за JS ничего не прячется, `--render` здесь не нужен.

    Пагинация ЕСТЬ: прежнее «вся выдача на первой странице» было неверным
    допущением — по «разработчик» вторая страница даёт 9 вакансий, которых
    на первой нет. Признак конца — страница без единого НОВОГО id, а не ошибка.

    JSON-эндпоинты (`/v5/vacancy/search` и соседние) сидят за антибот-стеной —
    мы туда не ходим, обычная HTML-страница отдаётся свободно.

    Отдельный исход — троттлинг: площадка рвёт TLS, если спрашивать часто.
    Это `ThrottledError`, а не «упал»: обход не сломан, площадка нас притормозила
    и лечится это паузой. Уже собранное при этом НЕ выбрасывается.
    """
    tally = Tally("rabota")
    edge = cutoff(ctx.days)
    out: list[Vacancy] = []
    seen: set[str] = set()
    throttle: ThrottledError | None = None

    queries = _long_queries(ctx, RABOTA_MIN_QUERY, tally,
                            "двухбуквенный запрос отдаёт ноль, это не «вакансий нет»")
    for q in queries:
        if throttle:
            break
        for page in range(1, RABOTA_MAX_PAGES + 1):
            if tally.requests >= RABOTA_MAX_REQUESTS:
                _cut_note(tally, "запросов к площадке", tally.requests, RABOTA_MAX_REQUESTS,
                          fix="потолок держит нас от бана, поднимать его нельзя — "
                              "остальное доберётся следующим прогоном")
                break
            if tally.requests:
                nap(RABOTA_PAUSE)
            url = qs("https://www.rabota.ru/vacancy/",
                     {"query": q, "page": page if page > 1 else None})
            try:
                postings = _rabota_postings(url, tally)
            except ThrottledError as e:
                # Уже собранное не выбрасываем: «страницу 1 успели, страницу 2
                # не дали» — это частичный обход, и он честнее нуля.
                throttle = e
                tally.note(f"ТРОТТЛИНГ на «{q}», странице {page}: {e.reason[:120]}")
                break
            tally.pages += 1
            if not postings:
                if page == 1:
                    tally.note(f"по «{q}» площадка отдала ld+json без JobPosting — "
                               f"честный ноль")
                else:
                    tally.note(f"по «{q}» выдача кончилась на странице {page}")
                break
            page_new = 0
            for j in postings:
                tally.offered += 1
                jurl = j.get("url") or ""
                m = re.search(r"/vacancy/(\d+)", jurl)
                if not m:
                    tally.dropped += 1
                    continue
                vid = m.group(1)
                if vid in seen:
                    tally.dupes += 1
                    continue
                seen.add(vid)
                page_new += 1
                tally.parsed += 1
                base = j.get("baseSalary") or {}
                est = j.get("estimatedSalary") or {}
                money = base if (base.get("minValue") or base.get("maxValue")) else est
                val = money.get("value") if isinstance(money.get("value"), dict) else {}
                lo = money.get("minValue") or val.get("minValue")
                hi = money.get("maxValue") or val.get("maxValue")
                # maxValue = 0 — «вилка сверху не указана», а не «ноль рублей».
                lo = int(lo) if isinstance(lo, (int, float)) and lo > 0 else None
                hi = int(hi) if isinstance(hi, (int, float)) and hi > 0 else None
                org = j.get("hiringOrganization") or {}
                ident = j.get("identifier") or {}
                addr = ((j.get("jobLocation") or {}).get("address") or {})
                v = Vacancy(
                    source="rabota",
                    external_id=vid,
                    url=jurl,
                    title=j.get("title") or "",
                    company=org.get("name") or ident.get("name"),
                    salary_from=lo, salary_to=hi,
                    currency=money.get("currency") or val.get("currency"),
                    # unitText площадка отдаёт сама (MONTH) — это факт источника,
                    # а не догадка, поэтому период проставляется.
                    salary_period=val.get("unitText"),
                    location=addr.get("streetAddress") or addr.get("addressLocality"),
                    published_at=j.get("datePosted"),
                    description=_strip_tags(j.get("description")),
                    raw={"query": q, "page": page, "employerId": ident.get("value"),
                         "note": "выдача не чистая по запросу — фильтровать по названию "
                                 "и описанию приходится самим"},
                )
                if older_than(v.published_at, edge):
                    tally.skipped_old += 1
                    continue
                tally.kept += 1
                out.append(v)
            # Конец выдачи — страница, не принёсшая ни одного НОВОГО id.
            # Проверять надо именно новизну: у площадки хвост выдачи повторяет
            # предыдущую страницу, и по «есть карточки» цикл шёл бы до потолка,
            # тратя запросы, за которые нас потом и отключают.
            if page_new == 0:
                tally.note(f"по «{q}» новых вакансий на странице {page} нет — "
                           f"это конец выдачи")
                break
        else:
            if not throttle:
                _cut_note(tally, f"«{q}»: страниц", RABOTA_MAX_PAGES, RABOTA_MAX_PAGES)

    tally.note(f"пагинация по ?page=N, пауза {RABOTA_PAUSE:.0f} с между запросами: "
               f"площадка закрывает TLS, если спрашивать часто")
    if throttle:
        if not out:
            # Ни одной строки — значит про площадку в этом прогоне не известно
            # НИЧЕГО. Возвращать пустой список здесь — врать «вакансий нет».
            raise throttle
        tally.note("обход НЕПОЛНЫЙ: площадка притормозила нас на середине, "
                   "остальные формулировки не спрашивались")
    out.append(tally.row())
    return out


RABOTA_NOTE = (f"пауза {RABOTA_PAUSE:.0f} с между страницами и не больше "
               f"{RABOTA_MAX_REQUESTS} запросов за прогон: площадка закрывает TLS "
               f"за частоту. Статус «ТРОТТЛИНГ» — это не поломка, лечится паузой")


# ──────────────────────────────────────────────────────────────────────────────
# getmatch.ru — открытый JSON API
# ──────────────────────────────────────────────────────────────────────────────

GETMATCH_API = "https://getmatch.ru/api/offers"
GETMATCH_LIMIT = 1000     # весь каталог одним запросом (740+ вакансий)


def src_getmatch(ctx: Ctx) -> list[Vacancy]:
    """getmatch.ru — весь каталог одним GET, без ключа и кук.

    Серверные фильтры (`sp`, `se`, `l`) у площадки есть, но СОЗНАТЕЛЬНО не
    используются: неверное значение фильтра не даёт ни ошибки, ни пустого ответа —
    возвращается ПОЛНАЯ выдача (`l=spb`, `se=principal` — все отдали те же 740).
    Опечатка в фильтре выглядит как «фильтр применился, просто нашлось много»,
    и заметить это по одному ответу нельзя. Каталог маленький, поэтому берём
    его целиком и режем у себя — счётчики тогда честные по построению.

    Из выдачи выбрасываются промо-баннеры `offer_type != vacancy` («Новый трек
    развития для техлидов…»): они приезжают СВЕРХ meta.total и вилки не имеют.
    """
    tally = Tally("getmatch")
    edge = cutoff(ctx.days)
    data = fetch_json(qs(GETMATCH_API, {"sp": "all", "limit": GETMATCH_LIMIT}))
    tally.requests += 1
    offers = data.get("offers")
    if offers is None:
        raise FetchError(GETMATCH_API, "в ответе нет offers — формат API сменился")
    meta_total = (data.get("meta") or {}).get("total")
    if meta_total and len(offers) > meta_total:
        tally.note(f"объектов {len(offers)} при meta.total {meta_total} — "
                   f"лишние это промо-карточки, они отфильтрованы")

    out: list[Vacancy] = []
    seen: set[str] = set()
    promos = 0
    for o in offers:
        tally.offered += 1
        if o.get("offer_type") != "vacancy":
            promos += 1
            tally.skipped_kind += 1
            continue
        oid = str(o.get("id") or "")
        if not oid:
            tally.dropped += 1
            continue
        if oid in seen:
            tally.dupes += 1
            continue
        seen.add(oid)
        tally.parsed += 1
        company = (o.get("company") or {})
        skills = [s.get("name") for s in (o.get("skills_objects") or [])
                  if isinstance(s, dict) and s.get("name")]
        formats = [str((li or {}).get("format") or "") for li in (o.get("location_items") or [])]
        labels = [str((li or {}).get("label") or "") for li in (o.get("location_items") or [])]
        taxes = o.get("salary_taxes")
        v = Vacancy(
            source="getmatch",
            external_id=oid,
            # url в списке ОТНОСИТЕЛЬНЫЙ — без префикса ссылка никуда не ведёт.
            url=urllib.parse.urljoin("https://getmatch.ru", o.get("url") or ""),
            title=o.get("position") or "",
            company=company.get("name"),
            salary_from=o.get("salary_display_from"),
            salary_to=o.get("salary_display_to"),
            currency=o.get("salary_currency"),
            salary_gross=(taxes == "gross") if taxes in ("gross", "net") else None,
            # Периода площадка не называет ни одним полем — оставляем пустым.
            salary_period=None,
            location=", ".join(x for x in labels if x) or None,
            remote=any("remote" in f for f in formats) or None,
            published_at=o.get("published_at"),
            tags=skills,
            # description_html в списке ВСЕГДА null, offer_description — тизер
            # на ~470 символов. Полный текст только в /api/offers/<id>.
            description=_strip_tags(o.get("offer_description")),
            raw={"formats": formats, "industry": company.get("industry"),
                 "english_level": o.get("english_level"),
                 "salary_hidden": o.get("salary_hidden"),
                 "source": o.get("source"), "is_active": o.get("is_active"),
                 "note": "описание — тизер; полное только GET /api/offers/<id> "
                         "(см. getmatch_detail)"},
        )
        if older_than(v.published_at, edge):
            tally.skipped_old += 1
            continue
        if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")
                or any(ATS_ROLE_RE.search(s) for s in skills)):
            tally.skipped_profile += 1
            continue
        tally.kept += 1
        out.append(v)

    if not tally.parsed:
        raise FetchError(GETMATCH_API, "API ответил, но вакансий ноль — парсер мог отстать")
    tally.note(f"промо-карточек отброшено {promos}; серверные фильтры не используются — "
               f"неверное значение молча отдаёт весь каталог")
    out.append(tally.row())
    return out


def getmatch_detail(offer_id: str | int) -> dict:
    """Полное описание вакансии. Слеш на конце даёт 307, /api/vacancies/<id> — 401."""
    return fetch_json(f"{GETMATCH_API}/{offer_id}")


# ──────────────────────────────────────────────────────────────────────────────
# EURES — портал вакансий ЕС
# ──────────────────────────────────────────────────────────────────────────────

EURES_API = "https://europa.eu/eures/api/jv-searchengine"
EURES_PAGE = 50           # 100 отдаёт HTTP 400, потолок именно 50
# ЛОВУШКА, ради которой здесь белый список: sortSearch=MOST_RECENT отдаёт
# правильный numberRecords и СОВЕРШЕННО НЕ ТЕ строки — по запросу golang
# приезжают «Assistant Ménager» и «Magazijnmedewerker» (замер: 1 из 10 названий
# с искомым словом против 10 из 10 при BEST_MATCH). Ключевое слово в этом режиме
# влияет только на счётчик. Поэтому сортировка ровно одна.
EURES_SORT = "BEST_MATCH"
EURES_SEARCH_CODE = "TITLE"   # EVERYWHERE даёт 46 153 против 666 по названию
# Глубина обхода. BEST_MATCH не сортирует по дате, поэтому свежее рассыпано по
# ВСЕЙ выдаче (замер 30.07.2026, окно 3 дня: по «Go разработчик» свежие попадания
# на страницах 1–6 подряд — 5, 5, 10, 7, 2, 1). Останавливаться на первой
# странице без попаданий нельзя: по «Golang» пустая ровно вторая, а на 3–7
# снова есть. Отсюда «терпение» в страницах, а не немедленный выход.
EURES_MAX_PAGES = 10
EURES_PATIENCE = 3
EURES_PAUSE = 1.5
# Карточки добираются по одной (вилка, работодатель, ссылка на отклик). Это
# самый дорогой кусок источника: замер 30.07.2026 — 2.2–2.7 с на запрос, то есть
# шестьдесят карточек это две с половиной минуты. Отсюда собственный потолок,
# и он НАЗВАН в сводке, когда срабатывает: вакансия без вилки из-за потолка
# и вакансия без вилки у работодателя — разные вещи.
EURES_DETAIL_CAP = 60
EURES_DETAIL_PAUSE = 0.25


def src_eures(ctx: Ctx) -> list[Vacancy]:
    """europa.eu/eures — общеевропейский портал, анонимно, вилки чаще российских.

    Сам портал — SPA, которая висит на «Loading…»; рабочий путь — его же
    поисковый эндпоинт. Список отдаёт название, описание и id, но НЕ отдаёт
    ни вилку, ни ссылку на работодателя: за ними идёт отдельный запрос карточки
    (`/public/jv/id/<id>`), и там же лежит `applicationInstructions` — прямая
    ссылка в ATS или на национальную биржу, то есть контакт ближе к нанимателю.

    Пагинация здесь — отдельная история и живой баг, который стоил нам почти
    всей выдачи. `resultsPerPage` НЕЛЬЗЯ считать как «сколько осталось добрать»:
    при `resultsPerPage=10` и `page=2` сервер отдаёт 11–20-е строки, а не 51–60-е,
    то есть вторая страница перечитывает начало первой («отдано 60, разобрано 50,
    дублей 10»). Поэтому страница всегда ПОЛНАЯ — ровно `EURES_PAGE`.
    """
    tally = Tally("eures")
    edge = cutoff(ctx.days)
    relevant = query_re(ctx)
    out: list[Vacancy] = []
    seen: set[str] = set()
    max_pages = min(EURES_MAX_PAGES, max(1, row_budget(ctx, EURES_PAGE * EURES_MAX_PAGES)
                                         // EURES_PAGE))

    for q in _long_queries(ctx, 3, tally, "поиск по двум буквам осмысленной выдачи не даёт"):
        off_target = 0
        dry_pages = 0
        records = None
        for page in range(1, max_pages + 1):
            if page > 1:
                nap(EURES_PAUSE)
            data = post_json(f"{EURES_API}/public/jv-search/search", {
                "keywords": [{"keyword": q, "specificSearchCode": EURES_SEARCH_CODE}],
                # Только полная страница: см. докстроку — «добрать остаток»
                # сервер понимает как «сдвинь окно», и вторая страница
                # оказывается началом первой.
                "resultsPerPage": EURES_PAGE,
                "page": page,
                "sortSearch": EURES_SORT,
            })
            tally.requests += 1
            jvs = data.get("jvs")
            if jvs is None:
                raise FetchError(EURES_API, "в ответе нет jvs — формат API сменился")
            if not jvs:
                break
            records = data.get("numberRecords")
            tally.pages += 1
            hits_on_page = 0
            for j in jvs:
                tally.offered += 1
                jid = str(j.get("id") or "")
                if not jid:
                    tally.dropped += 1
                    continue
                if jid in seen:
                    tally.dupes += 1
                    continue
                seen.add(jid)
                tally.parsed += 1
                title = j.get("title") or ""
                v = Vacancy(
                    source="eures",
                    external_id=jid,
                    url="https://europa.eu/eures/portal/jv-se/jv-details/"
                        + urllib.parse.quote(jid, safe="") + "?lang=en",
                    title=title,
                    company=((j.get("employer") or {}) if isinstance(j.get("employer"), dict)
                             else {}).get("name"),
                    published_at=_eures_date(j.get("creationDate")),
                    updated_at=_eures_date(j.get("lastModificationDate")),
                    tags=[str(x) for x in (j.get("positionScheduleCodes") or []) if x],
                    description=_strip_tags(j.get("description")),
                    raw={"query": q, "numberRecords": data.get("numberRecords"),
                         "euresFlag": j.get("euresFlag"), "source": j.get("source"),
                         "note": "вилка, работодатель и ссылка на отклик — в карточке "
                                 "/public/jv/id/<id>, см. eures_detail"},
                )
                if older_than(v.published_at, edge):
                    tally.skipped_old += 1
                    continue
                # Поиск у EURES нечёткий: по «golang» в выдачу заезжают
                # «Auxiliaire Petite Enfance volant» и «Nurse (Poland)».
                # Совпадение проверяется по названию и описанию — то, что
                # площадка отдала, но профессия совсем другая, не пойдёт в отчёт.
                if not (ctx.ats_all or relevant.search(title)
                        or relevant.search(v.description or "")):
                    tally.skipped_profile += 1
                    off_target += 1
                    continue
                hits_on_page += 1
                tally.kept += 1
                out.append(v)
            if len(jvs) < EURES_PAGE:
                break
            # Пустая страница НЕ означает конец выдачи: сортировки по дате здесь
            # нет, и по «Golang» ровно вторая страница пустая, а третья–седьмая
            # снова с попаданиями. Выходим только после EURES_PATIENCE пустых
            # подряд — иначе теряется весь хвост окна.
            #
            # Считаются именно ЗАПИСАННЫЕ (свежие и по профилю): вопрос, ради
            # которого идём вглубь, — «есть ли там ещё свежее», а не «есть ли
            # там ещё хоть что-то про Go». Дальше по релевантности вакансии
            # обычно ещё встречаются, но все старше окна.
            dry_pages = 0 if hits_on_page else dry_pages + 1
            if dry_pages >= EURES_PATIENCE:
                tally.note(f"по «{q}»: {EURES_PATIENCE} страницы подряд без свежих "
                           f"попаданий (до {page}-й включительно) — обход остановлен")
                break
        else:
            if records and max_pages * EURES_PAGE < records:
                _cut_note(tally, f"«{q}»: строк", max_pages * EURES_PAGE, records)
        if off_target:
            tally.note(f"по «{q}» отсеяно {off_target} вакансий без совпадения "
                       f"по названию и описанию — поиск площадки нечёткий")

    # Карточки добираются только для того, что реально оставили: это N запросов,
    # и делать их ради выброшенных строк незачем.
    for i, v in enumerate(out):
        if i >= EURES_DETAIL_CAP:
            tally.note(f"карточки добраны только у первых {EURES_DETAIL_CAP} из "
                       f"{len(out)}: у остальных вилка, работодатель и ссылка на "
                       f"отклик ПУСТЫЕ — это потолок запросов, а не «нет данных»")
            v.raw["detail_error"] = "карточка не запрашивалась: потолок деталок"
            continue
        if i:
            nap(EURES_DETAIL_PAUSE)
        try:
            _eures_enrich(v)
            tally.requests += 1
        except FetchError as e:
            v.raw["detail_error"] = str(e)

    tally.note(f"сортировка {EURES_SORT} и поиск по {EURES_SEARCH_CODE}: другие режимы "
               f"молча возвращают выдачу без учёта запроса")
    out.append(tally.row())
    return out


def _eures_date(value) -> str | None:
    """creationDate приезжает и числом, и строкой миллисекунд."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def eures_detail(jv_id: str) -> dict:
    """Карточка вакансии. Возвращает профиль на предпочтительном языке."""
    enc = urllib.parse.quote(jv_id, safe="")
    return fetch_json(f"{EURES_API}/public/jv/id/{enc}?requestLang=en&preferredLang=en")


_PERIOD_EURES = {"hour": "hour", "month": "month", "year": "year"}


def _eures_enrich(v: Vacancy) -> None:
    """Дописывает вилку, работодателя и ссылку на отклик из карточки."""
    data = eures_detail(v.external_id)
    profiles = data.get("jvProfiles") or {}
    prof = profiles.get(data.get("preferredLanguage")) or next(iter(profiles.values()), {})
    if not isinstance(prof, dict):
        return
    emp = prof.get("employer") or {}
    if isinstance(emp, dict) and emp.get("name"):
        v.company = emp["name"]
        if emp.get("website"):
            v.raw["employer_website"] = emp["website"]
    pack = prof.get("offeredRemunerationPackage") or {}
    salaries = pack.get("salaries") if isinstance(pack, dict) else None
    if salaries:
        s = salaries[0] or {}
        lo, hi = s.get("minimumSalary"), s.get("maximumSalary")
        v.salary_from = int(lo) if isinstance(lo, (int, float)) and lo > 0 else None
        v.salary_to = int(hi) if isinstance(hi, (int, float)) and hi > 0 else None
        v.currency = s.get("currencyCode")
        # payingIntervalCode площадка называет прямо (hour/month/year). Периоды,
        # для которых у нас нет честной подписи (день, неделя), в поле НЕ идут.
        v.salary_period = _PERIOD_EURES.get(str(s.get("payingIntervalCode") or "").lower())
        if v.salary_period is None and s.get("payingIntervalCode"):
            v.raw["salary_interval"] = s["payingIntervalCode"]
    locs = prof.get("locations") or []
    names = []
    for loc in locs if isinstance(locs, list) else []:
        if not isinstance(loc, dict):
            continue
        addr = loc.get("address") if isinstance(loc.get("address"), dict) else loc
        for key in ("cityName", "city", "countryCode", "country", "regionName"):
            val = addr.get(key)
            if isinstance(val, str) and val and val not in names:
                names.append(val)
    if names:
        v.location = ", ".join(names[:3])
    # Прямая ссылка «куда откликаться» — обычно на биржу страны или сайт компании.
    instructions = " ".join(prof.get("applicationInstructions") or [])
    link = re.search(r'href="(https?://[^"]+)"', instructions) \
        or re.search(r"(https?://\S+)", instructions)
    if link:
        v.employer_url = H.unescape(link.group(1))


EURES_DAYS_NOTE = "--days применяется на нашей стороне: у поиска нет окна по дате"


# ──────────────────────────────────────────────────────────────────────────────
# relocate.me — релокация, объём крошечный
# ──────────────────────────────────────────────────────────────────────────────

RELOCATE_MAX_PAGES = 10
RELOCATE_PAUSE = 2.0


def src_relocateme(ctx: Ctx) -> list[Vacancy]:
    """relocate.me — вакансии с переездом; по Go их единицы, и это не поломка.

    Доска берётся ЦЕЛИКОМ, без `query`, и фильтруется у нас. Причина не в лени,
    а в замере 30.07.2026: поиск площадки НЕЧЕСТНЫЙ — `query=zzzznotaword`
    возвращает три НАСТОЯЩИЕ вакансии. Значит запрос не отбирает, а подмешивает
    своё, и «найдено по Golang» ничего не гарантирует. Доска при этом крошечная:
    31 карточка на трёх страницах, то есть весь обход — три запроса. По нашему
    фильтру профессии из них проходит 18 против 3, которые отдавал «поиск».

    Карточки отдаются сервером (SPA нет), поэтому обычный GET. Вилки на карточке
    нет вовсе — честный None, а не ноль. На каждой странице первой идёт
    ПРОМО-карточка (`job_featured`, «1000+ Curated Visa Sponsorship…»): это
    платное объявление самой площадки, а не вакансия работодателя, поэтому она
    считается как «не вакансия» (`skipped_kind`) и в выдачу не идёт.
    """
    tally = Tally("relocateme")
    out: list[Vacancy] = []
    seen: set[str] = set()
    promo = 0

    # Одна доска — один проход. Раньше проход шёл на КАЖДУЮ формулировку и давал
    # втрое больше запросов ради тех же карточек.
    for page in range(1, RELOCATE_MAX_PAGES + 1):
        if page > 1:
            nap(RELOCATE_PAUSE)
        url = qs("https://relocate.me/international-jobs",
                 {"page": page if page > 1 else None})
        text, final = fetch(url)
        tally.requests += 1
        check_wall(text, final)
        # SVG вырезаются до разбора: у каждой карточки два инлайновых значка
        # на полторы тысячи символов, и они рвут любую регулярку по карточке.
        clean = re.sub(r"<svg.*?</svg>", " ", text, flags=re.S)
        chunks = re.split(r'<div class="jobs-list__job', clean)[1:]
        if not chunks:
            if "jobs-list" in clean:
                raise FetchError(final, "список есть, а карточек ноль — вёрстка сменилась")
            tally.note(f"страница {page} без карточек — конец доски")
            break
        tally.pages += 1
        new_on_page = 0
        for c in chunks:
            tally.offered += 1
            featured = c.lstrip().startswith("job_featured")
            m = re.search(r'<div class="job__title">\s*<a href="([^"]+)"', c)
            if not m:
                tally.dropped += 1
                continue
            href = H.unescape(m.group(1))
            vid_m = re.search(r"-(\d+)$", href.rstrip("/"))
            if not vid_m:
                tally.dropped += 1
                continue
            vid = vid_m.group(1)
            if vid in seen:
                tally.dupes += 1
                continue
            seen.add(vid)
            new_on_page += 1
            if featured:
                # Платное объявление площадки — не вакансия работодателя.
                # Считается отдельной строкой, как промо у getmatch, и в отчёт
                # не идёт: иначе одна и та же реклама «1000+ Curated Jobs»
                # приезжает вакансией в каждом прогоне.
                promo += 1
                tally.skipped_kind += 1
                continue
            tally.parsed += 1
            # В job__info лежат ровно две подписи и в этом порядке:
            # страна (или Remote) и работодатель.
            info = [_strip_tags(x).strip() for x in
                    re.findall(r'<div class="job__company[^"]*">(.*?)</div>', c, re.S)]
            country = info[0] if info else None
            title_m = re.search(r"<b>(.*?)</b>", c, re.S)
            # Город приписан после названия открытым текстом: «<b>…</b> in Tokyo».
            city_m = re.search(r"</b>\s*(?:</a>)?\s*in\s+([^<]{2,60})", c)
            preview_m = re.search(r'<p class="job__preview">(.*?)</p>', c, re.S)
            v = Vacancy(
                source="relocateme",
                external_id=vid,
                url=urllib.parse.urljoin("https://relocate.me", href),
                title=_strip_tags(title_m.group(1)) if title_m else "",
                company=info[1] if len(info) > 1 else None,
                # Вилки на карточке нет ни в каком виде — не выдумываем.
                location=", ".join(x for x in (
                    (city_m.group(1).strip() if city_m else None), country) if x) or None,
                remote=(country.lower() == "remote") if country else None,
                description=_strip_tags(preview_m.group(1)) if preview_m else None,
                raw={"page": page, "featured": featured,
                     "note": "доска взята целиком: серверный поиск площадки "
                             "не фильтрует (zzzznotaword даёт три реальные "
                             "вакансии), отбор профессии — наш"},
            )
            if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")):
                tally.skipped_profile += 1
                continue
            tally.kept += 1
            out.append(v)
        # Конец доски — страница без единой новой карточки. Промо повторяется
        # на каждой странице, поэтому считать надо именно новые id, иначе
        # «страница есть» держит цикл до потолка.
        if new_on_page == 0:
            break

    if promo:
        tally.note(f"промо-карточек площадки {promo} — это реклама самой доски, "
                   f"в вакансии она не идёт")
    tally.note("дат публикации и вилок на карточках нет — --days не применяется")
    out.append(tally.row())
    return out


RELOCATE_DAYS_NOTE = "--days не применяется: дат публикации на карточках нет"


# ──────────────────────────────────────────────────────────────────────────────
# th.jobsdb.com — SEEK, публичный поисковый API
# ──────────────────────────────────────────────────────────────────────────────

JOBSDB_API = "https://th.jobsdb.com/api/jobsearch/v5/search"
JOBSDB_PAGE = 30
# Умолчание выдачи — релевантность, и это тихо съедало окно: замер 30.07.2026
# по «golang» — на первой странице свежих 1 из 30 и разброс дат 30.06…28.07.
# С `sortmode=ListedDate` та же первая страница даёт 9 свежих и 24.07…30.07,
# при том же totalCount=256. То есть параметр не сужает выдачу, а раскладывает
# её по дате — ровно то, что нужно окну `--days`.
JOBSDB_SORT = "ListedDate"
JOBSDB_MAX_PAGES = 20
JOBSDB_PAUSE = 1.5
# ฿ у parse_salary в словаре нет, а вилки здесь почти всегда в батах.
_JOBSDB_CUR = (("฿", "THB"), ("RM", "MYR"), ("S$", "SGD"), ("$", "USD"))


def _jobsdb_salary(label: str) -> tuple:
    """«฿50,000 – ฿75,000 per month» → (50000, 75000, 'THB', None).

    Знак валюты выкусывается ДО разбора: parse_salary про ฿ не знает, а знак
    у второй границы («– ฿75,000») ломает разбор диапазона, и вилка молча
    съезжает в «от 50 000» — верхняя граница теряется целиком.
    """
    cur = None
    clean = label or ""
    for sign, code in _JOBSDB_CUR:
        if sign in clean:
            cur = cur or code
            clean = clean.replace(sign, " ")
    sf, st, parsed_cur, gross = parse_salary(clean)
    return sf, st, (parsed_cur or cur), gross


def src_jobsdb(ctx: Ctx) -> list[Vacancy]:
    """th.jobsdb.com — Таиланд, площадка SEEK.

    HTML-страница (`/Go-jobs`) сидит за Cloudflare и отдаёт «Just a moment…» —
    туда мы не ходим и стену не обходим. Публичный API того же сайта отвечает
    обычному GET без кук: 256 вакансий по golang, с вилкой прямо в строке
    (`฿50,000 – ฿75,000 per month`) и периодом словами.

    Выдача запрашивается ПО ДАТЕ (`sortmode=ListedDate`), и обход идёт до выхода
    за окно `--days`, а не до круглого числа страниц: при сортировке по
    релевантности свежее размазано по всей выдаче, и три прочитанные страницы
    из десяти давали четыре свежих вакансии вместо девяти.
    """
    tally = Tally("jobsdb")
    edge = cutoff(ctx.days)
    out: list[Vacancy] = []
    seen: set[str] = set()
    max_pages = min(JOBSDB_MAX_PAGES,
                    max(1, row_budget(ctx, JOBSDB_PAGE * JOBSDB_MAX_PAGES) // JOBSDB_PAGE))

    for q in _long_queries(ctx, 2, tally, "SEEK не ищет по одной букве"):
        for page in range(1, max_pages + 1):
            if page > 1:
                nap(JOBSDB_PAUSE)
            data = fetch_json(qs(JOBSDB_API, {
                "siteKey": "TH-Main", "sourcesystem": "houston", "locale": "en-TH",
                "keywords": q, "page": page, "pageSize": JOBSDB_PAGE,
                "sortmode": JOBSDB_SORT,
            }))
            tally.requests += 1
            rows = data.get("data")
            if rows is None:
                raise FetchError(JOBSDB_API, "в ответе нет data — формат API сменился")
            if not rows:
                break
            tally.pages += 1
            page_fresh = 0
            for j in rows:
                tally.offered += 1
                jid = str(j.get("id") or "")
                if not jid:
                    tally.dropped += 1
                    continue
                if jid in seen:
                    tally.dupes += 1
                    continue
                seen.add(jid)
                tally.parsed += 1
                label = j.get("salaryLabel") or ""
                sf, st, cur, gross = _jobsdb_salary(label)
                locs = [str((l or {}).get("label") or "") for l in (j.get("locations") or [])]
                arrangements = [(((a or {}).get("label") or {}).get("text") or "")
                                for a in ((j.get("workArrangements") or {}).get("data") or [])]
                emp = j.get("employer") if isinstance(j.get("employer"), dict) else {}
                v = Vacancy(
                    source="jobsdb",
                    external_id=jid,
                    url=f"https://th.jobsdb.com/job/{jid}",
                    title=j.get("title") or "",
                    company=j.get("companyName") or emp.get("name")
                            or (j.get("advertiser") or {}).get("description"),
                    salary_from=sf, salary_to=st, currency=cur, salary_gross=gross,
                    # Период площадка пишет словами прямо в вилке («per month»),
                    # поэтому он берётся из неё, а не подставляется.
                    salary_period=period_from_text(label),
                    location=", ".join(x for x in locs if x) or None,
                    remote=any("remote" in a.lower() for a in arrangements) or None,
                    published_at=j.get("listingDate"),
                    tags=[x for x in (j.get("workTypes") or []) if isinstance(x, str)]
                         + [a for a in arrangements if a],
                    description=j.get("teaser"),
                    raw={"query": q, "totalCount": data.get("totalCount"),
                         "salaryLabel": label, "companyUrl": emp.get("companyUrl"),
                         "roleId": j.get("roleId")},
                )
                if older_than(v.published_at, edge):
                    tally.skipped_old += 1
                    continue
                page_fresh += 1
                if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")):
                    tally.skipped_profile += 1
                    continue
                tally.kept += 1
                out.append(v)
            if len(rows) < JOBSDB_PAGE:
                break
            # Выдача идёт от свежих к старым, поэтому страница без единой свежей
            # вакансии — это конец окна. Считаем свежие ДО фильтра профессии:
            # страница из тридцати свежих продавцов означает, что окно ещё
            # не кончилось, и обрывать обход на ней нельзя.
            if page_fresh == 0:
                tally.note(f"по «{q}» окно кончилось на странице {page} "
                           f"(сортировка {JOBSDB_SORT})")
                break
        else:
            _cut_note(tally, f"«{q}»: страниц", max_pages, max_pages)

    tally.note(f"выдача запрошена с sortmode={JOBSDB_SORT}: по умолчанию площадка "
               f"сортирует по релевантности и свежее размазано по всей выдаче")
    out.append(tally.row())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# glassdoor.com.au — стена
# ──────────────────────────────────────────────────────────────────────────────

GLASSDOOR_URL = ("https://www.glassdoor.com.au/Job/"
                 "germany-golang-jobs-SRCH_IL.0,7_IN96_KO8,14.htm")


# Карточка выдачи. Якоря — только data-атрибуты (`data-jobid`, `data-test`):
# имена CSS-классов у Glassdoor собираются с хешем (`JobCard_jobTitle__GLyJ1`)
# и меняются каждым релизом, а data-test держится их же тестами.
_GD_SPLIT = re.compile(r'(?=<li[^>]*\bdata-jobid="\d+")')
_GD_ID = re.compile(r'data-jobid="(\d+)"')
_GD_HREF = re.compile(r'data-test="job-title"[^>]*href="([^"]+)"')
_GD_HREF2 = re.compile(r'href="([^"]+)"[^>]*data-test="job-title"')
_GD_TITLE = re.compile(r'data-test="job-title"[^>]*>(.*?)</a>', re.S)
_GD_EMP = re.compile(r'compactEmployerName[^>]*>(.*?)</span>', re.S)
_GD_LOC = re.compile(r'data-test="emp-location"[^>]*>(.*?)</div>', re.S)
_GD_AGE = re.compile(r'data-test="job-age"[^>]*>(.*?)</div>', re.S)
_GD_AGE_NUM = re.compile(r"(\d+)\s*([dhmн]|дн|ч)", re.I)


def _gd_posted(age: str | None, now: datetime | None = None) -> tuple[str | None, str | None]:
    """«15d» / «24h» → дата и честная оговорка.

    Точной даты публикации в выдаче нет вовсе — есть возраст объявления. Считаем
    из него дату и ГОВОРИМ, что она вычислена: иначе «опубликовано 15.07» выглядит
    фактом с площадки, которого площадка не сообщала.
    """
    m = _GD_AGE_NUM.search(age or "")
    if not m:
        return None, None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = timedelta(hours=n) if unit in ("h", "ч") else timedelta(days=n)
    when = (now or datetime.now(timezone.utc)) - delta
    return when.isoformat(timespec="seconds"), f"дата вычислена из метки «{age.strip()}»"


def _glassdoor_cards(html: str) -> list[dict]:
    """Карточки выдачи в структуру. Пустой список — значит разметка сменилась."""
    out = []
    for chunk in _GD_SPLIT.split(html):
        mid = _GD_ID.search(chunk[:400])
        if not mid:
            continue
        href = _GD_HREF.search(chunk) or _GD_HREF2.search(chunk)
        title = _GD_TITLE.search(chunk)
        sal = re.search(rf'id="job-salary-{mid.group(1)}"[^>]*>(.*?)</div>', chunk, re.S)
        emp = _GD_EMP.search(chunk)
        loc = _GD_LOC.search(chunk)
        age = _GD_AGE.search(chunk)
        out.append({
            "id": mid.group(1),
            "url": H.unescape(href.group(1)) if href else None,
            "title": _strip_tags(title.group(1)).strip() if title else None,
            "company": _strip_tags(emp.group(1)).strip() if emp else None,
            "location": _strip_tags(loc.group(1)).strip() if loc else None,
            "salary": _strip_tags(sal.group(1)).strip() if sal else None,
            "age": _strip_tags(age.group(1)).strip() if age else None,
        })
    return out


def src_glassdoor(ctx: Ctx) -> list[Vacancy]:
    """glassdoor.com.au — только через браузер пользователя, и только если стена снята.

    История площадки в двух замерах. 30.07.2026 утром: stdlib-GET → HTTP 403
    «Security | Glassdoor», рендер настоящим Chromium → 344 КБ страницы
    «Один момент…» с `captcha-container`. Тем же вечером, в том же профиле,
    стена не показалась и страница отдалась целиком — 82 вакансии по golang.

    Отсюда всё устройство функции: **проверку мы не проходим и капчу не решаем**,
    но если браузер пользователя её не увидел, выдачу надо разобрать, а не
    выбросить. Стена → BlockedError («АНТИБОТ» в покрытии, чинится заходом
    человека). Нет стены и нет карточек → FetchError: это сменившаяся разметка,
    а не «ноль вакансий».

    Разбор двухслойный: сначала ld+json (JobPosting) — его Glassdoor отдаёт
    на страницах отдельных вакансий, — потом карточки выдачи по data-атрибутам.
    """
    tally = Tally("glassdoor")
    url = getattr(ctx, "glassdoor_url", GLASSDOOR_URL)
    from .render import render_page  # noqa: PLC0415 — Playwright опционален

    html, final = render_page(url, wait=5.0)
    tally.requests += 1
    check_wall(html, final)          # иногда именно здесь всё и заканчивается

    out: list[Vacancy] = []
    edge = cutoff(ctx.days)
    postings = _job_postings(html)
    cards = _glassdoor_cards(html) if not postings else []
    if not postings and not cards:
        raise FetchError(final, "стена не сработала, но и вакансий в разметке нет — "
                                "разбирать нечего, проверь страницу глазами")

    for j in postings:
        tally.offered += 1
        jurl = j.get("url") or ""
        if not jurl:
            tally.dropped += 1
            continue
        tally.parsed += 1
        org = j.get("hiringOrganization") or {}
        addr = ((j.get("jobLocation") or {}).get("address") or {})
        base = j.get("baseSalary") or {}
        val = base.get("value") if isinstance(base.get("value"), dict) else {}
        v = Vacancy(
            source="glassdoor",
            external_id=str(j.get("identifier", {}).get("value")
                            or re.sub(r"\D", "", jurl)[-12:] or jurl),
            url=jurl,
            title=j.get("title") or "",
            company=org.get("name"),
            salary_from=val.get("minValue"), salary_to=val.get("maxValue"),
            currency=base.get("currency"),
            salary_period=norm_period(val.get("unitText")),
            location=", ".join(str(x) for x in (addr.get("addressLocality"),
                                                addr.get("addressCountry")) if x) or None,
            published_at=j.get("datePosted"),
            description=_strip_tags(j.get("description")),
            raw={"note": "снято рендером; площадка обычно закрыта антибот-стеной",
                 "shape": "ld+json"},
        )
        if older_than(v.published_at, edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        out.append(v)

    for c in cards:
        tally.offered += 1
        if not (c["url"] and c["title"]):
            tally.dropped += 1
            continue
        tally.parsed += 1
        # «EUR 90K - EUR 130K (Employer provided)»: суффикс тысяч разворачивает
        # общий parse_salary, а вот период Glassdoor в выдаче не называет вовсе —
        # и подставлять «в месяц» тут значит выдумать условия за работодателя.
        sf, st, cur, gross = parse_salary(c["salary"])
        posted, note = _gd_posted(c["age"])
        v = Vacancy(
            source="glassdoor",
            external_id=c["id"],
            url=c["url"],
            title=c["title"],
            company=c["company"],
            salary_from=sf, salary_to=st, currency=cur, salary_gross=gross,
            salary_period=period_from_text(c["salary"]),
            location=c["location"],
            published_at=posted,
            raw={"shape": "карточка выдачи", "age_label": c["age"],
                 "salary_label": c["salary"], "date_note": note,
                 "note": "снято браузером пользователя; ld+json на странице поиска нет"},
        )
        if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")):
            tally.skipped_profile += 1
            continue
        if older_than(v.published_at, edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        out.append(v)

    if cards:
        tally.note("ld+json на странице поиска нет — разобраны карточки выдачи "
                   "по data-атрибутам; дата вычислена из метки возраста («15d»)")
    total = re.search(r'data-test="search-title"[^>]*>(\d+)', html)
    if total:
        tally.note(f"площадка заявила {total.group(1)} вакансий по этому запросу")
    tally.note("снято браузером (render.py); анонимный GET площадка не отдаёт")
    out.append(tally.row())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Hacker News «Who is hiring» — замена мёртвому stackoverflowjobs.com
# ──────────────────────────────────────────────────────────────────────────────
#
# stackoverflowjobs.com закрыт в 2022-м и сейчас просто редиректит на ленту
# вопросов — парсить там нечего. Взамен взят ежемесячный тред «Ask HN: Who is
# hiring?»: посты пишут САМИ работодатели, в тексте почти всегда прямая ссылка
# и почта — то есть контакт ближе к нанимателю, чего у агрегаторов нет.
# Поиск идёт через открытый Algolia-индекс HN: без ключа, без кук, без лимитов.

HN_ALGOLIA = "https://hn.algolia.com/api/v1"
# Порог 2, а не 3. Замер 30.07.2026 по июльскому треду: «Go» — 115 попаданий,
# «Golang» — 2, «Backend Go» — 28. То есть прежний HN_MIN_QUERY=3 выбрасывал
# ровно ту формулировку, которой пишут на HN, и оставлял почти пустую выдачу.
HN_MIN_QUERY = 2
HN_PAGE = 100          # серверный потолок hitsPerPage; дальше только page=
HN_MAX_PAGES = 5
# Тредов берём два. Причина не в жадности: тред открывается 1-го числа, и первую
# неделю месяца свежий почти пуст, а живые посты лежат в предыдущем. С одним
# тредом источник каждый месяц на несколько дней превращался бы в честный ноль.
HN_THREADS = 2
HN_PAUSE = 0.5

_HN_SIGN = {"$": "USD", "€": "EUR", "£": "GBP"}
_HN_RANGE = re.compile(
    r"([$€£])\s?(\d[\d.,]*)\s*([kK])?\s*(?:[-–—]|to)\s*[$€£]?\s?(\d[\d.,]*)\s*([kK])?")
_HN_SINGLE = re.compile(r"([$€£])\s?(\d[\d.,]*)\s*([kK])\b")


def _hn_amount(num: str, k: str | None) -> int | None:
    try:
        val = float(num.replace(",", "").replace(" ", "").rstrip("."))
    except ValueError:
        return None
    return int(val * 1000) if k else int(val)


def _hn_salary(text: str) -> tuple[int | None, int | None, str | None]:
    """Вилка из текста поста. Берём только уверенные формы, иначе ничего.

    Общий parse_salary здесь врёт дважды: «ONSITE Hybrid 3 days/wk» он читает
    как «3 USD», а «$150k – $200k» — как «150–200», то есть ошибается в тысячу
    раз. Поэтому суффикс k разворачивается явно, а одиночное число без k
    зарплатой не считается вовсе.
    """
    m = _HN_RANGE.search(text or "")
    if m:
        lo = _hn_amount(m.group(2), m.group(3))
        hi = _hn_amount(m.group(4), m.group(5) or m.group(3))
        if lo and hi and hi >= lo:
            return lo, hi, _HN_SIGN.get(m.group(1))
    m = _HN_SINGLE.search(text or "")
    if m:
        return _hn_amount(m.group(2), m.group(3)), None, _HN_SIGN.get(m.group(1))
    return None, None, None


def _hn_terms(ctx: Ctx, tally: Tally) -> list[str]:
    """Формулировки → отдельные слова: Algolia склеивает слова через И.

    Замер 30.07.2026 по июльскому треду: «Go» — 115 попаданий, «backend» — 84,
    а фраза «Backend Go» — 28, то есть ровно пересечение. Фраза здесь всегда
    строго беднее своих слов, поэтому в поиск идут слова.
    """
    terms: list[str] = []
    short: list[str] = []
    for q in ctx.queries():
        for tok in re.split(r"[^\w\-]+", q.lower()):
            if not tok:
                continue
            if len(tok) < HN_MIN_QUERY:
                short.append(tok)
            elif tok not in terms:
                terms.append(tok)
    if short:
        tally.note(f"слова короче {HN_MIN_QUERY} символов пропущены {sorted(set(short))}: "
                   f"по одной букве Algolia вернёт весь тред")
    return terms


def _hn_threads(count: int = HN_THREADS) -> list[tuple[str, str]]:
    """Последние треды «Who is hiring», от свежего к старому.

    В ленте автора лежат ещё «Who wants to be hired?» и «Freelancer?» — они
    отфильтрованы по названию: там резюме кандидатов, а не вакансии.
    """
    data = fetch_json(f"{HN_ALGOLIA}/search_by_date?tags=story,author_whoishiring&hitsPerPage=20")
    out: list[tuple[str, str]] = []
    for hit in data.get("hits") or []:
        title = hit.get("title") or ""
        if "who is hiring" in title.lower():
            out.append((str(hit.get("objectID")), title))
        if len(out) >= count:
            break
    return out


def src_hnhiring(ctx: Ctx) -> list[Vacancy]:
    """news.ycombinator.com — треды «Who is hiring», через Algolia.

    Вакансии, которых нет ни на одном агрегаторе, и написаны они самим
    нанимателем. Три вещи, из-за которых источник отдавал крохи:

    * `hitsPerPage` упирается в 100 и БЕЗ пагинации молча резал выдачу
      (по «Go» — 115 попаданий, приезжало 100). Теперь листается по `page=`.
    * порог длины запроса выбрасывал «Go» — самую результативную формулировку
      (115 против 2 по «Golang»), а фразы уходили в поиск целиком, хотя Algolia
      склеивает слова через И и «Backend Go» — это всего 28 из тех же 115.
    * брался ровно один тред, и в первые дни месяца это почти пустая выдача.

    Плата за широкий невод — шум, и он отсеивается двумя проверками, а не одной:
    Algolia ищет по ПРЕФИКСУ, поэтому по «Go» приезжает «Gosh, that hourly
    range…»; и в самом треде половина комментариев — обсуждение и отклики
    соискателей, а не вакансии. Первое ловится `query_re` по тексту, второе —
    признаком «это корневой комментарий» (`parent_id == story_id`).
    """
    tally = Tally("hnhiring")
    threads = _hn_threads()
    tally.requests += 1
    if not threads:
        raise FetchError(HN_ALGOLIA, "не нашёлся тред «Who is hiring» — "
                                     "проверь формат ответа Algolia")
    tally.note("треды: " + "; ".join(t for _, t in threads))

    relevant = query_re(ctx)
    out: list[Vacancy] = []
    seen: set[str] = set()
    queries = _hn_terms(ctx, tally)
    for story_id, story_title in threads:
        for q in queries:
            for page in range(HN_MAX_PAGES):
                if tally.requests > 1:
                    nap(HN_PAUSE)
                data = fetch_json(qs(f"{HN_ALGOLIA}/search", {
                    "tags": f"comment,story_{story_id}", "query": q,
                    "hitsPerPage": HN_PAGE, "page": page}))
                tally.requests += 1
                hits = data.get("hits") or []
                if not hits:
                    break
                tally.pages += 1
                for hit in hits:
                    tally.offered += 1
                    cid = str(hit.get("objectID") or "")
                    if not cid:
                        tally.dropped += 1
                        continue
                    if cid in seen:
                        tally.dupes += 1
                        continue
                    seen.add(cid)
                    # Вакансия в этом треде — КОРНЕВОЙ комментарий. Ответы это
                    # обсуждение («Gosh, that hourly range is staggeringly wide»)
                    # и самореклама соискателей; вакансией они не являются,
                    # поэтому идут в «не вакансии», а не в потери.
                    if str(hit.get("parent_id") or "") != str(hit.get("story_id") or story_id):
                        tally.skipped_kind += 1
                        continue
                    tally.parsed += 1
                    text = _strip_tags(hit.get("comment_text"))
                    head = _first_line(text)
                    # Формат треда устоявшийся: «Компания | роль | локация | ссылка».
                    bits = [b.strip() for b in head.split("|") if b.strip()]
                    sf, st, cur = _hn_salary(text)
                    link = re.search(r"https?://[^\s<>\"]+", text)
                    v = Vacancy(
                        source="hnhiring",
                        external_id=cid,
                        url=f"https://news.ycombinator.com/item?id={cid}",
                        title=" | ".join(bits[:3]) or head[:120],
                        company=bits[0] if bits else None,
                        salary_from=sf, salary_to=st, currency=cur,
                        # Период в постах называют редко; не назвали — оставляем пустым.
                        salary_period=period_from_text(text) if (sf or st) else None,
                        location=next((b for b in bits[1:] if re.search(
                            r"remote|onsite|hybrid|relocat", b, re.I)), None),
                        remote=bool(re.search(r"\bremote\b", head, re.I)) or None,
                        published_at=hit.get("created_at"),
                        # Прямая ссылка из поста — это сайт или ATS работодателя.
                        employer_url=link.group(0).rstrip(".,);") if link else None,
                        description=text,
                        raw={"query": q, "story": story_id, "story_title": story_title,
                             "author": hit.get("author"), "nbHits": data.get("nbHits")},
                    )
                    # Поиск по префиксу: «Go» ловит «Gosh» и «Governance».
                    # Проверяем, что искомое слово в посте действительно есть.
                    if not (ctx.ats_all or relevant.search(text)):
                        tally.skipped_profile += 1
                        continue
                    tally.kept += 1
                    out.append(v)
                if page + 1 >= (data.get("nbPages") or 1):
                    break
            else:
                _cut_note(tally, f"«{q}»: страниц", HN_MAX_PAGES, HN_MAX_PAGES)

    out.append(tally.row())
    return out


HN_DAYS_NOTE = ("--days не применяется: тред один на месяц, берутся два последних "
                "целиком (в начале месяца свежий тред почти пуст)")


# ──────────────────────────────────────────────────────────────────────────────
# levels.fyi — СПРАВОЧНИК ЗАРПЛАТ, не вакансии
# ──────────────────────────────────────────────────────────────────────────────

LEVELS_TITLES = {
    "backend": "software-engineer/title/backend-software-engineer",
    "software-engineer": "software-engineer",
    "sre": "software-engineer/title/site-reliability-engineer",
    "devops": "software-engineer/title/devops-engineer",
    "engineering-manager": "engineering-manager",
}


def levels_benchmark(role: str = "backend", *, wait: float = 5.0) -> dict:
    """Бенчмарк зарплат по роли: p10…p99 полной компенсации, база, бонус, акции.

    Это НЕ источник вакансий и в `WEB_SOURCES` не входит — функция отдаёт словарь,
    а не список Vacancy. Нужна ровно для одного: когда в вакансии вилки нет,
    подпереть колонку «деньги» цифрой рынка вместо догадки.

    Нужен браузер: страницы `/t/…` закрыты AWS WAF (`challenge.js`), обычному GET
    отвечают 202 с челленджем. Рендер идёт настоящим Chromium с UA и сессией
    пользователя; если после ожидания страница осталась челленджем — это АНТИБОТ
    и остановка, капчу мы не решаем.

    Данные лежат в `__NEXT_DATA__`, а не в вёрстке таблицы: числа на странице
    отформатированы («$194K»), а в стейте — точные.
    """
    path = LEVELS_TITLES.get(role, role)
    url = f"https://www.levels.fyi/t/{path}"
    from .render import render_page  # noqa: PLC0415 — Playwright опционален

    html, final = render_page(url, wait=wait)
    check_wall(html, final)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise FetchError(final, "нет __NEXT_DATA__ — страница отдана не целиком")
    props = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {})
    pct = props.get("serverJobTitlePercentiles") or {}
    if not pct.get("totalCompensation"):
        raise FetchError(final, "в стейте нет перцентилей — разметка сменилась "
                                "или роль не существует")
    title = props.get("jobTitle") or {}
    # Площадка подставляет страну и локаль сама (редирект на /ru-ru/…?country=NN)
    # и молча меняет ими выборку. Финальный URL и размер выборки возвращаются
    # наружу, чтобы «медиана 194 480» не читалась как цифра неизвестно чего.
    country = urllib.parse.parse_qs(urllib.parse.urlsplit(final).query).get("country", [None])[0]
    return {
        "source": "levels.fyi",
        "url": final,
        "role": role,
        "job_title": pct.get("jobTitle") or title.get("name"),
        "job_family": pct.get("jobFamily"),
        "sample_size": pct.get("count"),
        "country_param": country,
        "currency": props.get("locationCurrency") or "USD",
        "median_total": (pct.get("totalCompensation") or {}).get("p50"),
        "total_compensation": pct.get("totalCompensation"),
        "base_salary": pct.get("baseSalary"),
        "bonus": pct.get("bonus"),
        "stock_grant": pct.get("stockGrant"),
        # Период везде годовой: levels.fyi считает total comp за год.
        "period": "year",
        "note": "справочник рынка, не вакансия; суммы годовые. Страну и локаль "
                "площадка подставляет сама — сверяй url и sample_size, прежде чем "
                "переносить цифру в карточку",
    }


LEVELS_NOTE = ("не вакансии, а медиана рынка: p10…p99 полной компенсации за год. "
               "Поэтому «найдено 0» — это норма, цифры лежат в сводке")


def src_levels(ctx: Ctx) -> list[Vacancy]:
    """Обёртка над `levels_benchmark` для общего обхода — БЕЗ вакансий.

    Нужна ровно затем, чтобы levels.fyi не выпадал из таблицы покрытия молча.
    Площадка вакансий не отдаёт вовсе, поэтому обёртка возвращает единственную
    строку-сводку с пустым url: `store.query` такие режет, в базу и в счётчик
    «найдено» справочник не попадает. Требование «в покрытии видны ВСЕ площадки»
    и требование «в базе только вакансии» здесь не конфликтуют.

    Стена AWS WAF пролетает наружу BlockedError'ом — это статус «АНТИБОТ»
    в покрытии, а не «упал» и не «ноль».
    """
    data = levels_benchmark("backend")
    tc = data.get("total_compensation") or {}
    money = data.get("median_total")
    cur = data.get("currency") or "USD"
    head = (f"[сводка levels] медиана {money:,} {cur}/год".replace(",", " ")
            if isinstance(money, int) else "[сводка levels] медианы нет")
    return [Vacancy(
        source="levels", external_id="_summary", url="",
        title=(f"{head} по роли {data.get('job_title') or 'backend'}, "
               f"выборка {data.get('sample_size')}; p10 {tc.get('p10')}, "
               f"p90 {tc.get('p90')}. {LEVELS_NOTE}"),
        raw=data,
    )]


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

WEB_SOURCES = {
    "hackoffer": src_hackoffer,
    "dreamoffer": src_dreamoffer,
    "rabota": src_rabota,
    "getmatch": src_getmatch,
    "eures": src_eures,
    "relocateme": src_relocateme,
    "jobsdb": src_jobsdb,
    "hnhiring": src_hnhiring,
    # Ходит через браузер и почти всегда упирается в стену — держим отдельно,
    # чтобы не тормозить общий прогон (см. WEB_NEEDS_BROWSER).
    "glassdoor": src_glassdoor,
}

# Справочники: обходятся вместе с площадками и стоят в покрытии отдельной строкой,
# но вакансий не приносят и в WEB_SOURCES не входят. Разделение не косметическое:
# всё, что лежит в WEB_SOURCES, обязано отдавать вакансии, и смешивать с этим
# медиану рынка значит однажды подставить её в карточку как зарплату.
WEB_REFERENCE = {
    "levels": src_levels,
}

# Кому нужен настоящий браузер и ЗАЧЕМ. Словарь, а не множество: причина нужна
# в покрытии, когда прогон идёт с --no-browser и площадка честно помечена
# «пропущена», а не исчезает.
WEB_NEEDS_BROWSER_MAP = {
    "glassdoor": "Cloudflare: и GET, и рендер упираются в проверку",
    "levels": "AWS WAF на /t/*: обычному GET отвечает 202 с челленджем",
}

# Источники вакансий, которым нужен Playwright. Остальные — чистый stdlib.
WEB_NEEDS_BROWSER = {n for n in WEB_NEEDS_BROWSER_MAP if n in WEB_SOURCES}

# Примечания к строке покрытия: где окно свежести не применяется вовсе или
# применяется на нашей стороне.
WEB_SOURCE_NOTES = {
    "hackoffer": HACKOFFER_DAYS_NOTE,
    "dreamoffer": DREAMOFFER_DAYS_NOTE,
    "eures": EURES_DAYS_NOTE,
    "relocateme": RELOCATE_DAYS_NOTE,
    "hnhiring": HN_DAYS_NOTE,
    "rabota": RABOTA_NOTE,
    "levels": LEVELS_NOTE,
}

# Площадки, проверенные и признанные закрытыми. Живут здесь, а не в чьей-то
# памяти: иначе каждый следующий прогон тратит время на те же 403.
WEB_DEAD = {
    "stackoverflowjobs": "домен редиректит на ленту вопросов Stack Overflow; "
                         "сервис закрыт в 2022 — заменён на hnhiring",
    "glassdoor": "Cloudflare-челлендж и при GET, и при рендере с сессией "
                 "пользователя; проверку проходит человек",
}
