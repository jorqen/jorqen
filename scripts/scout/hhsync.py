"""Статусы откликов из кабинета hh.ru — отказы, приглашения, просмотры.

Этого нет больше нигде: API hh для соискателей закрыт, а страница
`hh.ru/applicant/negotiations` живёт только за логином. Поэтому здесь Playwright
с сессией пользователя из `.auth/hh.json` (заведённой через `scout auth login hh`),
обход всех страниц пагинации и запись в таблицу `negotiation` — чтобы скилл видел
«сюда уже отказали» до того, как предложит откликнуться второй раз.

Скрипт только читает список. Ни одного клика по вакансиям, ни одного сообщения
рекрутёру, ни одного отклика — страница со списком открывается и разбирается, всё.

Разбор двухслойный: Lux-стейт (hh кладёт данные страницы в
`<template id="HH-Lux-InitialState">`) и data-qa-вёрстка. Берётся тот слой, который
дал БОЛЬШЕ элементов, а не первый непустой: схема Lux уже менялась молча, и «первый
слой вернул None» выглядело как «слой работает». Ноль распарсенных при непустой
странице — это ПАДЕНИЕ, а не «откликов нет»: молча пустой список — самая дорогая
ошибка сборщика.

Даты приводятся к ISO при разборе. Раньше в базу ложились строки вида «8 July»
и «yesterday» — без года, локале-зависимые, несравнимые с датой письма и
неотличимые друг от друга после Нового года.
"""

from __future__ import annotations

import html as H
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta

from . import store
from .auth import PLATFORMS, have, state_path
from .detail import html_to_text

MAX_PAGES = 25   # страховка от бесконечной пагинации; больше — уже не «мои отклики»

# Канонические статусы. Порядок проверки важен: «Отказ … резюме просмотрено» — отказ,
# «не просмотрен» должен матчиться раньше «просмотрен».
_STATUS_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rejection",  ("отказ", "discard", "rejection", "declin")),
    ("invitation", ("приглашен", "invitation", "invite")),
    ("interview",  ("собеседован", "интервью", "interview")),
    ("not_viewed", ("не просмотрен", "not viewed", "notviewed", "unread")),
    ("viewed",     ("просмотрен", "viewed")),
    ("pending",    ("ожидан", "отклик", "response", "pending")),
)


def canon_status(text: str | None) -> str:
    low = (text or "").lower()
    for status, needles in _STATUS_WORDS:
        if any(n in low for n in needles):
            return status
    return "other"


# ──────────────────────────────────────────────────────────────────────────────
# Даты: в базу только ISO
# ──────────────────────────────────────────────────────────────────────────────

_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_REL = {"сегодня": 0, "today": 0, "вчера": 1, "yesterday": 1,
        "позавчера": 2, "the day before yesterday": 2}
_DAY_MONTH = re.compile(r"\b(\d{1,2})\s+([а-яёa-z]{3,})\.?(?:\s+(\d{4}))?\b", re.I)


def norm_date(raw: str | None, *, today: date | None = None) -> tuple[str | None, str | None]:
    """Строка даты с площадки → (ISO-дата, сырьё-если-не-разобралось).

    Понимает ISO, «8 июля»/«8 July» (год подставляется по правилу «не в будущем»),
    «вчера»/«yesterday». Не разобралось — ISO=None, сырьё уходит в note: пусть
    поле будет пустым, чем в нём окажется несравнимая строка, по которой потом
    отсортируют и получат чушь."""
    if not raw:
        return None, None
    s = re.sub(r"\s+", " ", str(raw)).strip()
    if not s:
        return None, None
    ref = today or datetime.now().date()
    low = s.lower()
    if low in _REL:
        return (ref - timedelta(days=_REL[low])).isoformat(), None
    try:  # уже ISO или что-то, что понимает fromisoformat
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat(), None
    except ValueError:
        pass
    m = _DAY_MONTH.search(low)
    if m:
        mon = _MONTHS.get(m.group(2)[:3])
        if mon:
            day, year = int(m.group(1)), m.group(3)
            try:
                if year:
                    return date(int(year), mon, day).isoformat(), None
                d = date(ref.year, mon, day)
                # Без года: «20 декабря» в январе — это прошлый год, а не будущее.
                if d > ref:
                    d = date(ref.year - 1, mon, day)
                return d.isoformat(), None
            except ValueError:
                pass
    return None, s


# ──────────────────────────────────────────────────────────────────────────────
# Разбор: Lux-стейт
# ──────────────────────────────────────────────────────────────────────────────

def _walk_topics(obj, found: list) -> None:
    """Рекурсивно ищет объекты-переговоры: словарь с vacancy.name внутри.
    Схема стейта не задокументирована и меняется — искать по форме надёжнее,
    чем по пути, который сломается при следующем редизайне."""
    if isinstance(obj, dict):
        vac = obj.get("vacancy")
        if isinstance(vac, dict) and (vac.get("name") or vac.get("title")):
            found.append(obj)
            return
        for v in obj.values():
            _walk_topics(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_topics(v, found)


def _parse_lux(html: str) -> list[dict] | None:
    m = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', html, re.S)
    if not m:
        return None
    try:
        state = json.loads(H.unescape(m.group(1)))
    except (json.JSONDecodeError, ValueError):
        return None
    found: list = []
    _walk_topics(state, found)
    items = []
    for t in found:
        vac = t.get("vacancy") or {}
        title = vac.get("name") or vac.get("title")
        comp = vac.get("company") or vac.get("employer") or {}
        company = (comp.get("visibleName") or comp.get("name")) if isinstance(comp, dict) \
            else (str(comp) or None)
        raw_state = t.get("state") or t.get("lastState") or ""
        if isinstance(raw_state, dict):
            raw_state = raw_state.get("id") or raw_state.get("name") or ""
        status = canon_status(str(raw_state))
        # У hh «просмотрено» — не состояние, а флаг поверх response.
        if status in ("pending", "other"):
            viewed = t.get("viewedByOpponent")
            if viewed is True:
                status = "viewed"
            elif viewed is False:
                status = "not_viewed"
        vid = vac.get("vacancyId") or vac.get("id")
        raw_date = (t.get("lastModified") or t.get("updatedAt") or t.get("createdAt")
                    or t.get("creationTime"))
        iso, unparsed = norm_date(str(raw_date) if raw_date else None)
        items.append({
            "title": str(title), "company": company, "status": status,
            "date": iso, "date_raw": unparsed,
            "url": f"https://hh.ru/vacancy/{vid}" if vid else None,
        })
    return items or None


# ──────────────────────────────────────────────────────────────────────────────
# Разбор: data-qa-вёрстка
# ──────────────────────────────────────────────────────────────────────────────

# Контейнер элемента списка: ровно negotiations-item, возможно с модификаторами
# через пробел. Дочерние negotiations-item-title и т.п. сюда не матчатся.
_ITEM_ANCHOR = re.compile(r'data-qa="negotiations-item(?: [^"]*)?"')
_TITLE_LINK = re.compile(
    r'<a[^>]*href="(?P<href>[^"]*?/vacancy/\d+[^"]*)"[^>]*>(?P<text>.*?)</a>', re.S)
# Текст поля: до первого закрывающего тега. Вложенный <span> внутри поля переживёт
# html_to_text; главное — не уползти в соседний элемент.
_QA_TEXT = re.compile(
    r'data-qa="negotiations-item-(?P<kind>company|date|state)"[^>]*>(?P<text>.{0,400}?)</',
    re.S)
# Живая вёрстка hh (проверено 30.07.2026) пишет статус не текстом в
# `negotiations-item-state`, а МОДИФИКАТОРОМ в data-qa тега:
#   data-qa="negotiations-tag negotiations-item-discard"
#   data-qa="negotiations-tag negotiations-item-viewed" / "…-not-viewed"
# Совпадений `negotiations-item-state` на странице ноль, из-за чего статус брался
# из всего текста карточки — и любая карточка без явного тега уезжала в pending
# по слову «response» из служебной строки «Sorts through 99% of responses».
_STATUS_TAG = re.compile(
    r'data-qa="[^"]*\bnegotiations-item-'
    r'(?P<tag>discard|not-viewed|viewed|invitation|response|interview)\b[^"]*"')
_TAG_STATUS = {"discard": "rejection", "not-viewed": "not_viewed", "viewed": "viewed",
               "invitation": "invitation", "response": "pending", "interview": "interview"}
_DATE_TEXT = re.compile(
    r"\b(\d{1,2}\s+[а-яёa-z]{3,}(?:\s+\d{4})?|вчера|сегодня|yesterday|today)\b", re.I)


def _parse_markup(html: str) -> list[dict] | None:
    anchors = [m.start() for m in _ITEM_ANCHOR.finditer(html)]
    if not anchors:
        return None
    items = []
    for i, start in enumerate(anchors):
        # Чанк — от начала тега контейнера до следующего контейнера.
        chunk_start = html.rfind("<", 0, start)
        chunk = html[chunk_start:anchors[i + 1] if i + 1 < len(anchors) else len(html)]
        link = _TITLE_LINK.search(chunk)
        if not link:
            continue
        title = html_to_text(link.group("text")).replace("\n", " ").strip()
        fields = {m.group("kind"): html_to_text(m.group("text")).strip()
                  for m in _QA_TEXT.finditer(chunk)}
        plain = html_to_text(chunk)
        raw_date = fields.get("date")
        if not raw_date:
            m = _DATE_TEXT.search(plain)
            raw_date = m.group(0) if m else None
        iso, unparsed = norm_date(raw_date)
        # Статус: сначала МАШИННЫЙ тег вёрстки, потом текстовое поле, и только
        # потом — текст чанка без названия вакансии (иначе «Отклик на вакансию…»
        # в заголовке уводит любой статус в pending).
        tag = _STATUS_TAG.search(chunk)
        if tag:
            status = _TAG_STATUS[tag.group("tag")]
            # «Отказ» и «просмотрено» приходят двумя тегами сразу — отказ главнее.
            if status in ("viewed", "not_viewed", "pending") and re.search(
                    r'negotiations-item-discard\b', chunk):
                status = "rejection"
        else:
            status = canon_status(fields.get("state") or plain.replace(title, ""))
        items.append({
            "title": title,
            "company": fields.get("company") or None,
            "status": status,
            "date": iso,
            "date_raw": unparsed,
            "url": urllib.parse.urljoin("https://hh.ru/", H.unescape(link.group("href"))),
        })
    return items or None


# Маркеры честно пустого списка — при них ноль это ноль, а не сломанный парсер.
# Английские варианты обязательны: залогиненный hh отдаёт кабинет с <html lang="en">
# ('Responses', 'Log in'), и на русских иголках пустой список выглядел бы поломкой.
_EMPTY_MARKERS = ("у вас пока нет откликов", "здесь будут ваши отклики",
                  "откликов и приглашений нет", "нет активных откликов",
                  "вы ещё не откликались", "нет откликов и приглашений",
                  "you have no responses", "no responses yet",
                  "you haven't responded", "you have not responded",
                  "there are no responses", "no invitations or responses")


def parse_negotiations(html: str) -> list[dict]:
    """Страница списка откликов → [{title, company, status, date, url}].

    Берётся тот слой разбора, который дал больше элементов: «первый непустой»
    маскирует протухший слой — Lux молча возвращал None при живом стейте, и это
    выглядело как рабочий второй слой.

    ValueError, если страница похожа на список, но не распарсилась: пустой список
    от сломанного парсера неотличим от «откликов нет», и это надо ломать громко."""
    lux = _parse_lux(html) or []
    markup = _parse_markup(html) or []
    items = lux if len(lux) >= len(markup) else markup
    if items:
        return items
    low = html.lower()
    if any(m in low for m in _EMPTY_MARKERS):
        return []
    raise ValueError("не распарсил ни одного отклика и не нашёл маркера пустого списка — "
                     "вёрстка negotiations сменилась, чини hhsync.py")


# ──────────────────────────────────────────────────────────────────────────────
# Путь через API: то же самое, но контрактом, а не вёрсткой
# ──────────────────────────────────────────────────────────────────────────────

def item_from_api(raw: dict) -> dict:
    """Элемент /negotiations → та же форма, что отдаёт разбор HTML.

    Статус берётся из `state.name`, а НЕ из `state.id`: id — это три значения
    (response/invitation/discard), а name несёт то же, что видит человек, и уже
    разбирается общим canon_status. Если hh добавит состояние, name даст шанс
    его узнать, id — молча схлопнет в «other».

    Отдельный случай — «отклик без ответа»: у hh это state=response, а вот
    просмотрели резюме или нет, лежит в viewed_by_opponent. HTML-парсер это
    различает, значит и здесь должно различаться, иначе после перехода на API
    все «не просмотрен» разом станут «ожидание»."""
    vac = raw.get("vacancy") or {}
    emp = vac.get("employer") or {}
    state = raw.get("state") or {}
    status = canon_status(state.get("name") or state.get("id"))
    if status == "pending":
        status = "viewed" if raw.get("viewed_by_opponent") else "not_viewed"
    stamp = raw.get("updated_at") or raw.get("created_at") or ""
    return {"title": (vac.get("name") or "").strip() or "(без названия)",
            "company": (emp.get("name") or "").strip() or None,
            "status": status,
            "url": vac.get("alternate_url") or (
                f"https://hh.ru/vacancy/{vac['id']}" if vac.get("id") else None),
            "date": stamp[:10] or None,
            "date_raw": None if stamp[:10] else (stamp or None)}


def collect_items_api(max_pages: int = MAX_PAGES) -> tuple[list[dict], int]:
    """Все отклики через API. Возвращает (отклики, страниц) — как и браузерный
    сбор, чтобы sync не знал, каким путём они пришли.

    Конец пагинации — по полю `pages` из ответа, а не по «страница пустая»:
    у API есть честный счётчик, и городить эвристику поверх него незачем."""
    from . import hhapi  # noqa: PLC0415

    out: list[dict] = []
    seen: set[str] = set()
    pages_done = 0
    for n in range(max_pages):
        data = hhapi.negotiations_page(n)
        items = data.get("items") or []
        pages_done += 1
        for raw in items:
            it = item_from_api(raw)
            key = str(raw.get("id") or it["url"] or f"{it['title']}|{it['company']}")
            if key not in seen:
                seen.add(key)
                out.append(it)
        if n + 1 >= int(data.get("pages") or 0):
            break
    return out, pages_done


# ──────────────────────────────────────────────────────────────────────────────
# Сам sync: браузер → страницы → база
# ──────────────────────────────────────────────────────────────────────────────

_DEAD = PLATFORMS["hh"]["dead_if"]

NO_SESSION_MSG = (
    "Нет кук hh.ru: ни в браузере, ни в .auth/hh.json.\n"
    "  Проще всего — залогинься на hh.ru в своём браузере, куки читаются оттуда сами.\n"
    "  Иначе: `scout auth login hh` (пароль и код вводишь только ты) либо "
    "`--cookies-from <экспорт.json>`.")


def _hh_storage(cookies_from: str | None = None, use_cache: bool = False):
    """Куки hh — из браузера пользователя; `.auth/hh.json` остаётся оверрайдом.

    Проверяется НАЛИЧИЕ кук домена, а не существование файла: раньше пустой
    `.auth/browser.json` выигрывал у рабочего `.auth/hh.json` просто потому,
    что лежал на диске, и sync падал «нет сессии» при живом входе."""
    from . import cookiesrc  # noqa: PLC0415

    if have("hh"):
        return state_path("hh"), ".auth/hh.json"
    src = cookiesrc.resolve(cookies_from, ("hh.ru",), use_cache=use_cache)
    if not src.cookies:
        return None, src.origin
    return src.storage_for_playwright(), src.line()


def collect_items(max_pages: int = MAX_PAGES, *, cookies_from: str | None = None,
                  use_cache: bool = False) -> tuple[list[dict], int]:
    """Обходит пагинацию кабинета и разбирает каждую страницу на месте.

    Возвращает (все отклики, страниц пройдено). Остановка — по данным, а не по
    вёрстке пейджера: hh на любой page=N за концом списка отдаёт последнюю
    страницу, поэтому «страница не дала ни одного нового элемента» — надёжный
    конец, а маркер `pager-next` может исчезнуть при редизайне молча.

    Гео-поддомен учитывается так: первый заход на hh.ru, дальше — тот хост,
    на который hh сам средиректил (иначе каждая страница — лишний редирект)."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from .net import UA  # noqa: PLC0415

    storage, where = _hh_storage(cookies_from, use_cache)
    if storage is None:
        raise RuntimeError(NO_SESSION_MSG)
    print(f"# hh-sync: {where}", file=sys.stderr)

    all_items: list[dict] = []
    seen: set[str] = set()
    pages_scanned = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(storage_state=storage, locale="ru-RU",
                                      user_agent=UA)
            page = ctx.new_page()
            base = "https://hh.ru"
            for n in range(max_pages):
                page.goto(f"{base}/applicant/negotiations?page={n}",
                          wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                html = page.content()
                if n == 0:
                    u = urllib.parse.urlparse(page.url)
                    base = f"{u.scheme}://{u.netloc}"
                    if "account/login" in page.url or (
                            any(d in html for d in _DEAD)
                            and "negotiations" not in page.url):
                        raise RuntimeError(NO_SESSION_MSG)
                pages_scanned += 1
                try:
                    items = parse_negotiations(html)
                except ValueError:
                    # Первая страница обязана разобраться — иначе это поломка
                    # парсера и молчать нельзя. А вот страница ЗА концом списка
                    # рисует пустое состояние с неизвестным текстом — это конец
                    # пагинации, а не поломка (проверено живьём 30.07.2026).
                    if n == 0:
                        raise
                    break
                fresh = 0
                for it in items:
                    # Ключ дедупликации — id вакансии из url, а не (название,
                    # компания): две разные вакансии с одинаковым названием у
                    # одного работодателя схлопывались, и целая такая страница
                    # оборвала бы пагинацию раньше времени.
                    vid = re.search(r"/vacancy/(\d+)", it.get("url") or "")
                    key = vid.group(1) if vid else \
                        f"{it['title'].lower()}|{(it['company'] or '').lower()}"
                    if key not in seen:
                        seen.add(key)
                        all_items.append(it)
                        fresh += 1
                if fresh == 0:
                    break
        finally:
            browser.close()
    return all_items, pages_scanned


def sync(db_path: str, max_pages: int = MAX_PAGES, *, cookies_from: str | None = None,
         use_cache: bool = False) -> int:
    # API, если есть пользовательский токен: контракт вместо вёрстки, без
    # браузера и без антибота. Падение API — НЕ повод молча отдать ноль: это
    # ровно тот случай, ради которого браузерный разбор остаётся в живых.
    if hhapi_usable():
        try:
            all_items, pages_scanned = collect_items_api(max_pages)
            return _write(db_path, all_items, pages_scanned, how="API")
        except Exception as e:  # noqa: BLE001 — любой отказ API откатывает на HTML
            print(f"# hh-sync: API не сработал ({e}); иду через кабинет",
                  file=sys.stderr)

    try:
        all_items, pages_scanned = collect_items(max_pages, cookies_from=cookies_from,
                                                 use_cache=use_cache)
    except ImportError:
        print("Нужен Playwright: pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 3
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"страница не разобралась: {e}", file=sys.stderr)
        return 1

    return _write(db_path, all_items, pages_scanned, how="кабинет")


def hhapi_usable() -> bool:
    """Отдельной функцией, чтобы тест мог её подменить, а импорт оставался
    ленивым: .auth читается только когда до него дошло дело."""
    from . import hhapi  # noqa: PLC0415

    return hhapi.usable()


def _write(db_path: str, all_items: list[dict], pages_scanned: int, *,
           how: str) -> int:
    """Запись в базу и отчёт. Общая для обоих путей — иначе они разъедутся:
    в отчёте не должно быть разницы, кроме строчки о том, ЧЕМ прочитано."""
    new, changed, same = [], [], 0
    unparsed_dates = 0
    with store.connect(db_path) as conn:
        for it in all_items:
            if it.get("date_raw"):
                unparsed_dates += 1
            what, old = store.upsert_negotiation(
                conn, title=it["title"], company=it["company"], status=it["status"],
                source="hh", url=it["url"], event_at=it["date"],
                note=f"дата на странице: {it['date_raw']}" if it.get("date_raw") else None)
            if what == "new":
                new.append(it)
            elif what == "changed":
                changed.append((it, old))
            else:
                same += 1

    print(f"# hh-sync ({how}): страниц {pages_scanned}, откликов {len(all_items)} "
          f"(новых {len(new)}, сменили статус {len(changed)}, без изменений {same})")
    if unparsed_dates:
        print(f"  дат не разобралось: {unparsed_dates} — сырьё ушло в note, "
              f"event_at оставлен пустым, чтобы сортировка не врала")
    print()
    for it in new:
        print(f"  NEW [{it['status']:<10}] {it['title'][:60]} — {it['company'] or '?'}"
              f"  ({it['date'] or '—'})")
    for it, old in changed:
        print(f"  {old} → {it['status']:<10} {it['title'][:60]} — {it['company'] or '?'}")
    hot = [it for it in new + [c[0] for c in changed]
           if it["status"] in ("rejection", "invitation", "interview")]
    if hot:
        print(f"\nВажное с прошлого sync: {len(hot)} "
              f"(отказы/приглашения — см. строки выше)")
    elif not new and not changed:
        print("  ничего нового с прошлого sync")
    return 0
