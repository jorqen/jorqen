"""Статусы откликов из кабинета Хабр Карьеры — отказы, просмотры, избранное.

Аналог hhsync для career.habr.com, но без Playwright: страница
`career.habr.com/responses` — серверный HTML, хватает stdlib-GET с куками
пользователя (браузер или `.auth/habr.json`). Скрипт только читает список:
ни откликов, ни сообщений, ни единого POST.

Обходятся ОБА списка — основные и удалённые (`/responses/deleted`): удалённый
отклик всё равно означает «сюда уже откликался», без него сверка дырявая.

Статус берётся из ТЕКСТА `div.status`, а не из CSS-классов: классы врут о
смысле — живьём (30.07.2026) «Отказ» приходит с классом `readed`, «В избранном»
— с `favorite`, и разбор по классам превращал бы отказ в «просмотрено».

Дата в таблице кабинета — дата ОТКЛИКА, не смены статуса: в `event_at` уходит
именно она, «когда работодатель отказал» площадка не показывает.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from . import store
from .auth import have, state_path
from .detail import html_to_text

BASE = "https://career.habr.com"
MAX_PAGES = 40   # по 30 строк на страницу; больше 1200 откликов — уже не «мои отклики»

NO_SESSION_MSG = (
    "Нет сессии career.habr.com: страница откликов отдана без входа.\n"
    "  Проще всего — залогинься на career.habr.com в своём браузере, "
    "куки читаются оттуда сами.\n"
    "  Иначе: `scout auth login habr` (пароль и код вводишь только ты) либо "
    "`--cookies-from <экспорт.json>`.")


def logged_in(html: str) -> bool:
    """Признак живой сессии — "sign_out" в HTML: ссылка выхода есть только
    в залогиненном кабинете (проверено живьём 30.07.2026)."""
    return "sign_out" in html


# ──────────────────────────────────────────────────────────────────────────────
# Статус и дата: текст страницы → канон
# ──────────────────────────────────────────────────────────────────────────────

# Порядок проверки важен: «не прочитано» обязано матчиться раньше «прочитано».
_STATUS_WORDS: tuple[tuple[str, str, str | None], ...] = (
    ("отказ",       "rejection",  None),
    ("не прочитан", "not_viewed", None),
    ("прочитан",    "viewed",     None),
    ("в избранном", "viewed",     "работодатель добавил в избранное"),
)


def canon_status(text: str | None) -> tuple[str, str | None]:
    """Текст `div.status` → (канонический статус, note).

    Незнакомый текст не глотается: уходит в other с сырьём в note — пусть отчёт
    покажет новое слово площадки, чем молча переврёт его в «просмотрено»."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    low = raw.lower()
    for needle, status, note in _STATUS_WORDS:
        if needle in low:
            return status, note
    return "other", raw or None


_DATE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")


def norm_date(raw: str | None) -> tuple[str | None, str | None]:
    """«28.07.2026» → ('2026-07-28', None); не разобралось → (None, сырьё).

    Сырьё уходит в note: пусть event_at будет пустым, чем в нём окажется
    несравнимая строка, по которой потом отсортируют и получат чушь."""
    s = re.sub(r"\s+", " ", raw or "").strip()
    if not s:
        return None, None
    m = _DATE.search(s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d.%m.%Y").date().isoformat(), None
        except ValueError:
            pass  # «31.02.2026» — битая дата, сырьём в note честнее
    return None, s


# ──────────────────────────────────────────────────────────────────────────────
# Разбор таблицы откликов
# ──────────────────────────────────────────────────────────────────────────────

_TABLE = re.compile(
    r'<table[^>]*class="[^"]*\bmy_responses\b[^"]*"[^>]*>(?P<body>.*?)</table>', re.S)
_ROW_SPLIT = re.compile(r"<tr[^>]*>", re.I)
_TITLE = re.compile(
    r'<div[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>\s*'
    r'<a[^>]*href="[^"]*?/vacancies/(?P<vid>\d+)[^"]*"[^>]*>(?P<text>.*?)</a>', re.S)
# «comapny» — НЕ опечатка в этом файле: класс в живой вёрстке career.habr.com
# действительно называется так (проверено 30.07.2026). Починят у себя —
# добавить второй вариант, а не заменить этот.
_COMPANY = re.compile(
    r'<span[^>]*class="[^"]*\bcomapny\b[^"]*"[^>]*>(?P<body>.*?)</span>', re.S)
_A_TEXT = re.compile(r"<a[^>]*>(?P<text>.*?)</a>", re.S)
_CREATED = re.compile(
    r'<td[^>]*class="[^"]*\bcreated_at\b[^"]*"[^>]*>(?P<text>.*?)</td>', re.S)
_STATUS_DIV = re.compile(
    r'<div[^>]*class="[^"]*\bstatus\b[^"]*"[^>]*>(?P<text>.*?)</div>', re.S)

# Маркеры честно пустого списка — при них ноль строк это ноль, а не сломанный парсер.
_EMPTY_MARKERS = ("нет откликов", "вы ещё не откликались")

_TAB_MAIN = re.compile(r"основные\s*\((\d+)\)", re.I)
_TAB_DELETED = re.compile(r"удал[её]нные\s*\((\d+)\)", re.I)


def declared_counts(html: str) -> tuple[int | None, int | None]:
    """Счётчики из табов «Основные (N)» / «Удалённые (M)» — сверка полноты обхода."""
    low = html.lower()
    m1, m2 = _TAB_MAIN.search(low), _TAB_DELETED.search(low)
    return (int(m1.group(1)) if m1 else None, int(m2.group(1)) if m2 else None)


def _parse_row(chunk: str) -> dict | None:
    link = _TITLE.search(chunk)
    if not link:
        return None  # шапка таблицы или служебная строка
    title = html_to_text(link.group("text")).replace("\n", " ").strip()
    company = None
    comp = _COMPANY.search(chunk)
    if comp:
        a = _A_TEXT.search(comp.group("body"))
        company = html_to_text(a.group("text") if a else comp.group("body")).strip() or None
    created = _CREATED.search(chunk)
    iso, raw = norm_date(html_to_text(created.group("text")) if created else None)
    st = _STATUS_DIV.search(chunk)
    status, note = canon_status(html_to_text(st.group("text")) if st else None)
    return {"vid": link.group("vid"), "title": title, "company": company,
            "status": status, "note": note, "date": iso, "date_raw": raw}


def parse_responses(html: str, own_tab: str = r"основные") -> list[dict]:
    """Страница списка откликов → [{vid, title, company, status, note, date}].

    `own_tab` — регэксп имени СВОЕГО таба: пустота подтверждается нулём в его
    счётчике. Проверять оба таба нельзя: «Удалённые (0)» в шапке ОСНОВНОЙ
    страницы прятал бы сломанный парсер таблицы за «откликов нет».

    ValueError, если страница похожа на кабинет, но не разобралась: пустой
    список от сломанного парсера неотличим от «откликов нет», и это надо
    ломать громко."""
    m = _TABLE.search(html)
    body = m.group("body") if m else html
    items = [it for it in map(_parse_row, _ROW_SPLIT.split(body)[1:]) if it]
    if items:
        return items
    low = html.lower()
    if any(mk in low for mk in _EMPTY_MARKERS) or re.search(own_tab + r"\s*\(0\)", low):
        return []
    raise ValueError("не распарсил ни одной строки откликов и не нашёл маркера "
                     "пустого списка — вёрстка career.habr.com/responses "
                     "сменилась, чини habrsync.py")


# ──────────────────────────────────────────────────────────────────────────────
# Сам sync: куки → страницы → база
# ──────────────────────────────────────────────────────────────────────────────

def _habr_cookies(cookies_from: str | None = None,
                  use_cache: bool = False) -> tuple[str | None, str]:
    """Заголовок Cookie для career.habr.com + строка «откуда взяли».

    `.auth/habr.json` (после `scout auth login habr`) — оверрайд, но выигрывает
    только если в нём есть куки нужного домена: пустой файл не должен перебивать
    живой вход в браузере — на этих граблях уже стоял hh-sync."""
    from . import cookiesrc  # noqa: PLC0415

    if have("habr"):
        try:
            with open(state_path("habr"), encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        if isinstance(state, dict) and state.get("cookies"):
            hdr = cookiesrc.CookieSource(state, ".auth/habr.json").cookie_header(
                ("career.habr.com",))
            if hdr:
                return hdr, ".auth/habr.json"
    src = cookiesrc.resolve(cookies_from, ("career.habr.com",), use_cache=use_cache)
    return src.cookie_header(("career.habr.com",)), src.line()


def collect_items(max_pages: int = MAX_PAGES, *, cookies_from: str | None = None,
                  use_cache: bool = False,
                  ) -> tuple[list[dict], int, tuple[int | None, int | None]]:
    """Обходит оба списка кабинета и разбирает каждую страницу на месте.

    Возвращает (отклики, страниц пройдено, счётчики табов с первой страницы).
    Остановка — по данным, а не по вёрстке пейджера: страница без строк или
    без единого нового id вакансии — конец списка."""
    from .net import fetch  # noqa: PLC0415

    header, where = _habr_cookies(cookies_from, use_cache)
    if not header:
        raise RuntimeError(NO_SESSION_MSG)
    print(f"# habr-sync: {where}", file=sys.stderr)

    all_items: list[dict] = []
    seen: set[str] = set()
    pages_scanned = 0
    declared: tuple[int | None, int | None] = (None, None)
    for path, own_tab, deleted in (("/responses", r"основные", False),
                                   ("/responses/deleted", r"удал[её]нные", True)):
        for n in range(1, max_pages + 1):
            url = f"{BASE}{path}" + (f"?page={n}" if n > 1 else "")
            html, _ = fetch(url, cookies=header)
            if not logged_in(html):
                raise RuntimeError(NO_SESSION_MSG)
            if pages_scanned == 0:
                declared = declared_counts(html)
            pages_scanned += 1
            try:
                items = parse_responses(html, own_tab)
            except ValueError:
                # Первая страница списка обязана разобраться — молчать нельзя.
                # А страница ЗА концом пагинации вправе быть неузнаваемой —
                # это конец списка, а не поломка.
                if n == 1:
                    raise
                break
            fresh = 0
            for it in items:
                if it["vid"] in seen:
                    continue
                seen.add(it["vid"])
                it["deleted"] = deleted
                all_items.append(it)
                fresh += 1
            if fresh == 0:
                break
    return all_items, pages_scanned, declared


def sync(db_path: str, max_pages: int = MAX_PAGES, *, cookies_from: str | None = None,
         use_cache: bool = False) -> int:
    """Кабинет Хабр Карьеры → таблица negotiation. `event_at` — дата ОТКЛИКА
    (колонка created_at на странице), не дата смены статуса.

    Коды: 0 — прошло; 2 — нет сессии; 1 — сеть или сменившаяся вёрстка."""
    from .net import FetchError  # noqa: PLC0415
    try:
        all_items, pages_scanned, declared = collect_items(
            max_pages, cookies_from=cookies_from, use_cache=use_cache)
    except FetchError as e:
        print(f"career.habr.com не отдал страницу: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"страница не разобралась: {e}", file=sys.stderr)
        return 1

    new, changed, same = [], [], 0
    unparsed_dates = 0
    with store.connect(db_path) as conn:
        for it in all_items:
            if it.get("date_raw"):
                unparsed_dates += 1
            bits = [it.get("note"),
                    "отклик удалён из списка" if it.get("deleted") else None,
                    f"дата на странице: {it['date_raw']}" if it.get("date_raw") else None]
            what, old = store.upsert_negotiation(
                conn, title=it["title"], company=it["company"], status=it["status"],
                source="habr", url=f"{BASE}/vacancies/{it['vid']}",
                event_at=it["date"], note="; ".join(b for b in bits if b) or None)
            if what == "new":
                new.append(it)
            elif what == "changed":
                changed.append((it, old))
            else:
                same += 1

    print(f"# habr-sync: страниц {pages_scanned}, откликов {len(all_items)} "
          f"(новых {len(new)}, сменили статус {len(changed)}, без изменений {same})")
    total = (declared[0] or 0) + (declared[1] or 0)
    if (declared[0] is not None or declared[1] is not None) and len(all_items) < total:
        print(f"  РАСХОЖДЕНИЕ: в шапке заявлено {total} откликов "
              f"(основные {declared[0] if declared[0] is not None else '?'}, "
              f"удалённые {declared[1] if declared[1] is not None else '?'}), "
              f"распарсено {len(all_items)} — часть списка не прочитана, "
              f"проверь пагинацию и вёрстку")
    if unparsed_dates:
        print(f"  дат не разобралось: {unparsed_dates} — сырьё ушло в note, "
              f"event_at оставлен пустым, чтобы сортировка не врала")
    print()
    for it in new:
        print(f"  NEW [{it['status']:<10}] {it['title'][:60]} — {it['company'] or '?'}"
              f"  ({it['date'] or '—'})")
    for it, old in changed:
        print(f"  {old} → {it['status']:<10} {it['title'][:60]} — {it['company'] or '?'}")
    hot = [it for it in new + [c[0] for c in changed] if it["status"] == "rejection"]
    if hot:
        print(f"\nВажное с прошлого sync: {len(hot)} (отказы — см. строки выше)")
    elif not new and not changed:
        print("  ничего нового с прошлого sync")
    return 0
