"""Общая механика обхода веб-площадок: стены, окно свежести, POST-чтение,
вежливость и предохранители.

Выделено из `sources_web.py` 07.08.2026 переездом БЕЗ изменения поведения. Причина
не косметическая: файл дорос до 2500 строк, и на нём стабильно зависали субагенты,
а вынести из него площадки поодиночке было нельзя — каждая тянет вот эти общие
функции, и любой вынос давал цикл импорта. Теперь общее лежит здесь, площадки
импортируют отсюда, циклов нет.

`sources_web` реэкспортирует все имена, поэтому прежние импорты работают как
работали.
"""

from __future__ import annotations

import html as H
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from .model import Vacancy
from .net import BlockedError, FetchError, fetch, looks_blocked
# Ctx и Tally общие для всех адаптеров и живут в `sources`: счёт «отдано →
# записано» нужен каждому источнику одинаково, а два расходящихся счётчика в
# одном сборщике — это два разных ответа на вопрос «сколько потеряли».
from .sources import Ctx, Tally, _pause  # noqa: F401 — Ctx нужен в аннотациях

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


def nap(seconds: float, *, gate: bool = True) -> None:
    """Пауза между запросами к одной площадке — ОГРАНИЧИТЕЛЬ ЧАСТОТЫ.

    Отдельной функцией, а не `time.sleep` по месту, ровно по двум причинам:
    паузу видно грепом (её нельзя случайно «оптимизировать» из одного парсера)
    и её подменяют тесты — иначе полный прогон test_sources_web занимал бы
    минуты чистого сна.

    Считает время, уже потраченное с прошлой паузы, то есть время самого
    запроса, и спит только остаток. Без этого пауза складывается со временем
    ответа: площадка видит частоту НИЖЕ назначенной, а прогон платит временем.
    Замер 07.08.2026 на LinkedIn — 206 с против 120 с при той же частоте.
    Разбор и оговорки — в докстроке `sources._pause`, здесь тот же механизм для
    веб-площадок.
    """
    _pause(seconds, gate=gate)


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
