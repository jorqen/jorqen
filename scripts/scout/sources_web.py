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
браузера страницы нет вовсе (попытка Glassdoor).
"""

from __future__ import annotations

import html as H
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from .detail import html_to_text
from .model import SUMMARY_ID, Vacancy, norm_period
from .net import (BlockedError, FetchError, HostPacer, fetch, fetch_json,
                  looks_blocked, parallel, qs)
# Tally общий для всех адаптеров и живёт в `sources`: счёт «отдано → записано»
# нужен каждому источнику одинаково, а два расходящихся счётчика в одном сборщике
# — это два разных ответа на вопрос «сколько потеряли».
from .sources import ATS_ROLE_RE, Ctx, Tally, parse_salary, period_from_text


# ──────────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────────
# Общая механика — переехала в webcommon.py
# ──────────────────────────────────────────────────────────────────────────────
#
# Реэкспорт: прежние импорты `from .sources_web import post_json` работают как
# работали. Переезд сделан отдельно от функциональности и поведения не менял.
from .webcommon import (  # noqa: F401 — реэкспорт общей механики
    ThrottledError, _cut_note, _ld_json_blocks, _long_queries, _job_postings,
    _strip_tags, check_wall, cutoff, nap, older_than, post_json, query_re,
    row_budget, throttle_marker, wall_marker,
)



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
# Проверенный набор формулировок rabota.ru. Замер 08.08.2026, окно 3 дня, счёт
# СВОЕГО вклада к объединению предыдущих:
#
#   Golang        1   своего 1     ← одна формулировка = 1 из 13
#   backend       4   своего 3
#   бэкенд        5   своего 1
#   программист  10   своего 8
#   ИТОГО объединение 13
#
# 🔴 «бэкенд» здесь В НАБОРЕ, хотя на hh и Хабре он отдавал своего 0. Площадки
# ищут по-разному: те приводят кириллицу и латиницу к одному токену, эта —
# нет. Набор одной площадки на другую не переносится, только замер.
#
# «Go» в набор не попадает не по замеру, а по длине: RABOTA_MIN_QUERY отсекает
# двухбуквенное, потому что площадка отвечает на него нулём.
RABOTA_QUERIES = ("backend", "бэкенд", "программист")
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


RABOTA_API = "https://api.rabota.ru/v4/vacancies/search.json"
RABOTA_API_LIMIT = 100


def src_rabota(ctx: Ctx) -> list[Vacancy]:
    """rabota.ru: сначала официальный JSON API, при отказе — разбор JSON-LD.

    Почему API стоило искать снова. В докстринге ниже написано «JSON-эндпоинты
    сидят за антибот-стеной» — это было верно про `/v5/vacancy/search` на
    www-хосте. Отдельный хост `api.rabota.ru` (v4) отдаёт выдачу БЕЗ ключа, кук
    и Origin, и приносит то, чего в JSON-LD нет вовсе:

      * `contact_person.email` — почта живого рекрутёра. Проверено 05.08.2026:
        5 из 5 записей по «Golang», у Сбера в выдаче стоит адрес конкретного
        человека. Это ровно тот «прямой контакт работодателя», ради которого
        существует `reveal.py`, — здесь он приезжает бесплатно;
      * `salary.pay_type` (net/gross) — в JSON-LD этого нет, и вилка ехала без
        пометки «на руки»;
      * сортировка по дате на стороне площадки, а не угадывание окна.

    ЛОВУШКА, которая стоила бы тихого нуля: тело обязано быть в обёртке
    `{"request": {...}}`. Без неё API не ругается, а молча отдаёт выдачу по
    ПУСТОМУ запросу в Москве — то есть «нашлось 5 вакансий», просто не тех.

    Троттлинг никуда не делся: api-хост так же рвёт TLS, если частить (поймано
    при первой же проверке). Паузы и потолок запросов сохранены.

    Про robots.txt честно: `api.rabota.ru/robots.txt` — `Disallow: /`. HTML-путь
    у той же площадки закрыт тем же robots (`Disallow: /*?`), так что смена
    транспорта ничего не меняет в отношениях с площадкой: читаем в один поток,
    с паузами, для одного человека.
    """
    try:
        return _src_rabota_api(ctx)
    except (FetchError, ThrottledError) as e:
        rows = _src_rabota_html(ctx)
        for v in rows:
            if v.external_id == SUMMARY_ID:
                v.raw.setdefault("notes", []).insert(
                    0, f"API не сработал ({str(e)[:120]}) — выдача снята из JSON-LD")
        return rows


def _rabota_api_page(query: str, offset: int) -> dict:
    """Один POST. Обёртка `request` обязательна — см. ловушку в src_rabota."""
    payload = json.dumps({"request": {
        "query": query, "limit": RABOTA_API_LIMIT, "offset": offset,
        "sort": {"field": "date", "direction": "desc"}}}).encode("utf-8")
    data = fetch_json(RABOTA_API, method="POST", data=payload,
                      headers={"Content-Type": "application/json"})
    resp = data.get("response")
    if not isinstance(resp, dict):
        raise FetchError(RABOTA_API, "в ответе нет объекта response — API сменился")
    return resp



def _rabota_budget_out(tally: Tally, queries: list[str], i: int) -> bool:
    """Кончился ли бюджет запросов, и если да — НАЗВАТЬ неопрошенное.

    Бюджет `RABOTA_MAX_REQUESTS` один на все формулировки, а проверялся он
    только во ВНУТРЕННЕМ цикле по страницам. Из-за этого лишние формулировки
    молча прокручивались вхолостую: внешний цикл шёл до конца, внутренний
    ломался на первой же проверке, и в сводке оставалось «в выдаче ?» — то
    есть потеря выдачи, неотличимая от пустой площадки.

    Потолок при этом НЕ поднимается: он держит нас от бана. Правильный ответ —
    сказать вслух, чего не спросили.
    """
    if tally.requests < RABOTA_MAX_REQUESTS:
        return False
    tally.note(f"не опрошены формулировки {list(queries[i:])}: бюджет "
               f"{RABOTA_MAX_REQUESTS} запросов кончился. Это НЕ «на площадке "
               f"больше нет» — это недобор, потолок держит нас от бана")
    return True


def _src_rabota_api(ctx: Ctx) -> list[Vacancy]:
    """Официальный v4. Окно --days режется обрывом: выдача отсортирована по дате."""
    tally = Tally("rabota")
    edge = cutoff(ctx.days)
    out: list[Vacancy] = []
    seen: set[str] = set()
    queries = _long_queries(ctx, RABOTA_MIN_QUERY, tally,
                            "двухбуквенный запрос отдаёт ноль, это не «вакансий нет»",
                            vetted=RABOTA_QUERIES)
    for i, q in enumerate(queries):
        if _rabota_budget_out(tally, queries, i):
            break
        relevant = None
        for page in range(RABOTA_MAX_PAGES):
            if tally.requests >= RABOTA_MAX_REQUESTS:
                _cut_note(tally, "запросов к площадке", tally.requests,
                          RABOTA_MAX_REQUESTS,
                          fix="потолок держит нас от бана — остальное доберётся "
                              "следующим прогоном")
                break
            if tally.requests:
                nap(RABOTA_PAUSE)
            resp = _rabota_api_page(q, page * RABOTA_API_LIMIT)
            tally.requests += 1
            rows = resp.get("vacancies") or []
            if relevant is None:
                relevant = resp.get("relevant")
            if not rows:
                break
            tally.pages += 1
            if not _rabota_api_rows(rows, q, edge, out, seen, tally):
                tally.note(f"«{q}»: остановились на выходе за окно --days")
                break
            if len(rows) < RABOTA_API_LIMIT:
                break
        tally.note(f"«{q}»: в выдаче {relevant if relevant is not None else '?'} [API]")
    tally.note("официальный api.rabota.ru/v4 (POST, обёртка request обязательна); "
               "почта контактного лица кладётся в raw.contact")
    out.append(tally.row())
    return out


def _rabota_api_rows(rows: list, q: str, edge, out: list[Vacancy], seen: set[str],
                     tally: Tally) -> int:
    """Строки страницы → Vacancy. Возвращает, сколько попало в окно."""
    # «Сколько строк В ОКНЕ» считается по СЫРЫМ строкам, ДО отсева дублей, и это
    # не мелочь. Раньше счёт шёл после `if vid in seen: continue`, а `seen` общий
    # на все формулировки — первая же страница второй формулировки, целиком
    # лежащая внутри первой, давала fresh == 0. Обход обрывался, а в сводке
    # печаталось «остановились на выходе за окно --days» — то есть неправда:
    # ноль свежих при нуле просроченных. Замер 06.08.2026: 100 строк, затем 0
    # при 100 дублях и 0 старых. Так же считают Хабр и LinkedIn.
    fresh = sum(1 for r in rows
                if not older_than((r or {}).get("modified_date")
                                  or (r or {}).get("created_at"), edge))
    for r in rows:
        tally.offered += 1
        vid = str(r.get("id") or "")
        title = (r.get("title") or "").strip()
        if not vid or not title:
            tally.dropped += 1
            continue
        if vid in seen:
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        when = r.get("modified_date")
        if older_than(when, edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        sal = r.get("salary") or {}
        # `to: 0` у площадки означает «сверху не указано» — ровно та же ловушка,
        # что и в JSON-LD с maxValue: 0. Ноль в вилке это НЕ зарплата.
        sf = sal.get("from") or None
        st = sal.get("to") or None
        place = (r.get("places") or [{}])[0]
        comp = r.get("company") or {}
        contact = r.get("contact_person") or {}
        out.append(Vacancy(
            source="rabota",
            external_id=vid,
            url=f"https://www.rabota.ru/vacancy/{vid}/",
            title=title,
            company=(comp.get("name") or "").strip() or None,
            salary_from=sf, salary_to=st,
            currency="RUB" if (sf or st) else None,
            salary_period="month" if (sf or st) else None,
            salary_gross=(sal.get("pay_type") == "gross") if sal.get("pay_type") else None,
            location=(place.get("location") or {}).get("name") or place.get("address"),
            updated_at=when,
            description=html_to_text(r.get("description") or "") or None,
            raw={"query": q, "path": "api",
                 # Почта рекрутёра — то, ради чего источник и переведён на API.
                 "contact": {k: contact.get(k) for k in ("name", "email", "phones")
                             if contact.get(k)},
                 "company_slug": comp.get("slug"),
                 "pay_type": sal.get("pay_type"),
                 "is_promoted": r.get("is_promoted")},
        ))
    return fresh


def _src_rabota_html(ctx: Ctx) -> list[Vacancy]:
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
                            "двухбуквенный запрос отдаёт ноль, это не «вакансий нет»",
                            vetted=RABOTA_QUERIES)
    for i, q in enumerate(queries):
        if throttle or _rabota_budget_out(tally, queries, i):
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
EURES_MAX_PAGES = 10   # потолок страниц ПО УМОЛЧАНИЮ: --limit его поднимает
# Насколько разрешено поднять потолок ВСЛЕД ЗА numberRecords, который площадка
# отдаёт с первой страницы. Нужен, потому что число это чужое: с
# specificSearchCode=TITLE оно живёт в сотнях (659 по «Golang»), но EVERYWHERE
# даёт 46 153 — то есть без верхней границы одна формулировка увела бы прогон
# в девятьсот запросов. 40 страниц это 2000 строк: заведомо больше любого
# реального окна и заведомо меньше побега.
EURES_RECORDS_CAP = 40
EURES_PATIENCE = 3
EURES_PAUSE = 1.5
# Карточки добираются по одной (вилка, работодатель, ссылка на отклик). Это
# самый дорогой кусок источника: замер 30.07.2026 — 2.2–2.7 с на запрос, то есть
# шестьдесят карточек это две с половиной минуты. Отсюда собственный потолок,
# и он НАЗВАН в сводке, когда срабатывает: вакансия без вилки из-за потолка
# и вакансия без вилки у работодателя — разные вещи.
# Потолок карточек. Стоял 60, когда они добирались ПО ОЧЕРЕДИ по 2.5 с — это
# были две с половиной минуты. С параллельным добором под тем же пейсером хоста
# цена карточки упала вчетверо, и держать прежний потолок значило бы платить
# полнотой за ограничение, которого больше нет: у вакансий сверх него вилка,
# работодатель и ссылка на отклик остаются ПУСТЫМИ.
EURES_DETAIL_CAP = 200
EURES_DETAIL_PAUSE = 0.25
# Потоки добора карточек. Частоту к хосту держит HostPacer, потоки лишь
# перекрывают ожидание ответа — четыре при задержке 2.5 с и слоте 0.25 с
# упираются в пейсер, а не в сервер.
EURES_DETAIL_WORKERS = 4


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
    # --limit умеет ПОДНЯТЬ потолок страниц (контракт row_budget); floor держит
    # умолчание в EURES_MAX_PAGES страниц. Жёсткий min(EURES_MAX_PAGES, …) здесь
    # молча съедал --limit (прогон 04.08.2026: 500 строк из 4987 при --limit 20000,
    # и сводка врала «подними --limit»). От бесконечности страхует PATIENCE ниже:
    # три страницы подряд без свежих попаданий останавливают обход сами.
    floor_pages = max(1, row_budget(ctx, EURES_PAGE * EURES_MAX_PAGES) // EURES_PAGE)

    for q in _long_queries(ctx, 3, tally, "поиск по двум буквам осмысленной выдачи не даёт"):
        # Потолок считается ЗАНОВО на каждую формулировку. Раньше он объявлялся
        # снаружи цикла и поднимался внутри — то есть работал храповиком: после
        # формулировки с numberRecords=1900 (38 страниц) следующая, у которой
        # записей 200, уходила с чужим потолком 38, и строка «ОБРЕЗАНО» сравнивала
        # её выдачу с чужими 38×50. В тестах не видно: обе проверки гоняют одну
        # формулировку.
        max_pages = floor_pages
        off_target = 0
        dry_pages = 0
        records = None
        # while, а не range: потолок поднимается ВНУТРИ цикла, когда площадка
        # назовёт numberRecords, а range вычисляется один раз при входе — с ним
        # поднятый потолок молча ни на что не влиял.
        page = 0
        while page < max_pages:
            page += 1
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
            # Сколько страниц НУЖНО, площадка говорит сама — с первой же. Замер
            # 07.08.2026: по «Golang» numberRecords=659, то есть 14 страниц, а
            # умолчание в EURES_MAX_PAGES=10 забирало 500 и молчало про 159
            # оставшихся. Серверного потолка у EURES нет вовсе (страница 11
            # отдаёт полные 50, а 20-я пуста просто потому, что выдача кончилась),
            # так что упираться в своё же число, зная настоящее, — чистая потеря.
            if records:
                need = -(-int(records) // EURES_PAGE)
                max_pages = max(max_pages, min(need, EURES_RECORDS_CAP))
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
    #
    # Идут ПАРАЛЛЕЛЬНО под общим пейсером хоста, и это не разгон. Замер прогона
    # #10 (05.08.2026): eures съел 419 с ради 28 вакансий — 10% времени волны за
    # 0.7% результата, потому что каждая карточка отвечает 2.2–2.7 с, а ждали их
    # по очереди. Частота к europa.eu при этом остаётся прежней: `HostPacer`
    # выдаёт слоты по одному на EURES_DETAIL_PAUSE секунд и занимает слот под
    # локом. Меняется только то, что ожидание ответа перекрывается ожиданием
    # следующего слота, а не складывается с ним, — тот же приём, что в `enrich`.
    todo = out[:EURES_DETAIL_CAP]
    rest = out[EURES_DETAIL_CAP:]
    for v in rest:
        v.raw["detail_error"] = "карточка не запрашивалась: потолок деталок"
    if rest:
        tally.note(f"карточки добраны только у первых {EURES_DETAIL_CAP} из "
                   f"{len(out)}: у остальных вилка, работодатель и ссылка на "
                   f"отклик ПУСТЫЕ — это потолок запросов, а не «нет данных»")
    if todo:
        pacer = HostPacer(EURES_DETAIL_PAUSE)

        def fetch_one(v=None):
            pacer.wait(EURES_API)
            _eures_enrich(v)
            return True

        got = parallel({str(i): (lambda v=v: fetch_one(v))
                        for i, v in enumerate(todo)},
                       workers=EURES_DETAIL_WORKERS)
        for i, v in enumerate(todo):
            ok, payload = got.get(str(i), (False, RuntimeError("не запускалась")))
            if ok:
                tally.requests += 1
            else:
                v.raw["detail_error"] = str(payload)

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
# glassdoor — переехал в sources_glassdoor.py (стена, нужен настоящий браузер)
# ──────────────────────────────────────────────────────────────────────────────
from .sources_glassdoor import (  # noqa: F401 — реэкспорт: прежние импорты живы
    GLASSDOOR_GRAPH, GLASSDOOR_MAX_PAGES, GLASSDOOR_PAGE, GLASSDOOR_URL,
    _gd_api_rows, _gd_posted, _gd_script, _glassdoor_cards, src_glassdoor,
    _src_glassdoor_api, _src_glassdoor_html,
)



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
# levels.fyi — переехал в reference_levels.py (справочник, не источник вакансий)
# ──────────────────────────────────────────────────────────────────────────────
from .reference_levels import (  # noqa: F401 — реэкспорт: прежние импорты живы
    LEVELS_ATTRIBUTION, LEVELS_LOST, LEVELS_NOTE, LEVELS_TITLES, _levels_bullets,
    _levels_money, _levels_pick, _levels_section, _levels_table, levels_benchmark,
    parse_levels_md, src_levels,
)



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
# levels отсюда УБРАН: HTML-страницы /t/* по-прежнему за AWS WAF, но данные
# берутся с `.md`-маршрута обычным GET, и браузер источнику больше не нужен.
WEB_NEEDS_BROWSER_MAP = {
    "glassdoor": "Cloudflare: и GET, и рендер упираются в проверку",
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
