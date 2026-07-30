"""Адаптеры площадок.

Каждый адаптер — функция `(ctx) -> list[Vacancy]`. Она либо возвращает вакансии,
либо кидает исключение; молча вернуть пустой список, когда площадка сломалась, нельзя —
это ровно тот случай, когда отчёт выглядит полным, а на деле потерял площадку целиком.

Источники разбиты на два класса, и это осознанно:

* **JSON** (`hh`, `careered`, ATS-доски) — структура стабильная, парсер надёжный,
  вакансия приезжает готовой строкой.
* **HTML** (`habr`, `linkedin`, …) — вёрстка меняется без предупреждения. Поэтому у
  каждого такого адаптера есть порог правдоподобия: если страница отдалась, а карточек
  не нашлось — это не «ноль вакансий», а сломанный парсер, и он обязан упасть.

Чего здесь принципиально НЕТ: отбора по релевантности, стоп-слов и склейки дублей.
Это работа модели (см. «Скрипт вреден там, где нужно суждение» в SKILL.md). Сборщик
приносит всё, что отдала площадка, и не решает за человека.
"""

from __future__ import annotations

import html as H
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .model import Vacancy, norm_period, salary_str
from .net import FetchError, fetch, fetch_json, qs

# ──────────────────────────────────────────────────────────────────────────────
# Контекст прогона
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Ctx:
    query: str = "Golang"
    # Дополнительные формулировки: одна не покрывает всё (проверено на shadowhint —
    # `Go` и `Golang` дают несовпадающие наборы).
    extra_queries: tuple[str, ...] = ()
    days: int = 3
    area: str = "113"          # 113 — вся Россия, 1 — Москва, 2 — Санкт-Петербург
    limit: int = 100
    include_foreign: bool = True
    ats_all: bool = False  # не отсекать чужие профессии на ATS-досках

    def queries(self) -> list[str]:
        seen, out = set(), []
        for q in (self.query, *self.extra_queries):
            if q and q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
        return out


# ──────────────────────────────────────────────────────────────────────────────
# Пагинация: общие правила для всех источников
# ──────────────────────────────────────────────────────────────────────────────
#
# Правило первое: УМОЛЧАНИЕ ЗАБИРАЕТ ВСЁ, ЧТО ЕСТЬ В ОКНЕ. Один запрос без `page`
# выглядит как полный обход и молча им не является: hh по «Go» сам пишет
# totalResults 396, а отдаёт 100 — 296 вакансий просто не существовало в отчёте.
# Ровно то же было у Хабра (25 — размер его страницы, а не «столько нашлось»)
# и у careered (100 самых свежих ЛЮБОЙ профессии из ленты в 1797).
#
# Правило второе: `--limit` — ПРЕДОХРАНИТЕЛЬ ОТ БЕСКОНЕЧНОСТИ, а не рабочий режим.
# Умолчание 100 не должно обрезать окно, в котором площадка отдаёт 400: круглое
# число в отчёте («ровно 100») — первый признак того, что выдачу обрезал не поиск,
# а мы сами. Поэтому лимит НИЖЕ штатной глубины источника её не опускает,
# а выше — поднимает потолок.
#
# Правило третье: ЛЮБОЕ ОБРЕЗАНИЕ ВИДНО СТРОКОЙ. Остановились по потолку страниц —
# в сводке появляется «ОБРЕЗАНО», с цифрами и с подсказкой про --limit. Разница
# между «площадка отдала столько» и «мы столько унесли» обязана быть в отчёте,
# иначе её через месяц не восстановит никто.
#
# Правило четвёртое: ПАУЗА МЕЖДУ СТРАНИЦАМИ. Пагинация превращает один запрос
# к площадке в десятки, и без паузы это выглядит для неё как атака. rabota.ru
# забанила нас по TLS после ~25 запросов за 20 минут — это наша вина, а не её.
PAGE_PAUSE = 0.7


def _pause(seconds: float = PAGE_PAUSE) -> None:
    """Пауза между страницами одной площадки. Вежливость, а не осторожность."""
    if seconds > 0:
        time.sleep(seconds)


def _page_budget(ctx: Ctx, per_page: int, default_pages: int) -> int:
    """Сколько страниц источнику разрешено взять.

    `default_pages` — штатная глубина: столько, чтобы типовое окно свежести
    поместилось целиком с запасом. `--limit` меньше этой глубины её НЕ опускает
    (иначе умолчание 100 снова обрежет выдачу в 400), больше — поднимает потолок.
    """
    if ctx.limit and ctx.limit > per_page * default_pages:
        return -(-ctx.limit // per_page)
    return default_pages


def _cutoff(days: int) -> datetime:
    """Край окна свежести. Локальный двойник sources_web.cutoff — импортировать
    оттуда нельзя, `sources_web` сам импортирует из этого модуля (цикл)."""
    return datetime.now(timezone.utc) - timedelta(days=max(days, 0))


def _iso_stamp(value) -> str | None:
    """Дата площадки → ISO-8601. careered отдаёт `posted_at` unix-числом, Хабр —
    строкой; сравнивать с краем окна надо одно и то же, а не «как приехало»."""
    from .model import _iso  # noqa: PLC0415 — тот же разбор, что у самой Vacancy
    return _iso(value)


def _older_than(stamp: str | None, edge: datetime) -> bool:
    """True — карточка старше окна.

    Без даты — НЕ отбрасываем: «дата неизвестна» и «старая» это разные вещи,
    и вторая не должна поглощать первую.
    """
    if not stamp:
        return False
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < edge


def _truncated_note(source_label: str, taken: int, total: int | None) -> str:
    """Строка «ОБРЕЗАНО» для сводки. Тихое обрезание — это потеря вакансий."""
    return (f"{source_label}: ОБРЕЗАНО по потолку страниц — взято {taken}"
            + (f" из {total}" if total else "")
            + "; за остальным нужен --limit больше")


# ──────────────────────────────────────────────────────────────────────────────
# Счётчики прогона
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Tally:
    """Счёт прогона одного источника. Печатается строкой-сводкой в выдаче.

    Инвариант, который проверяется в `row()`:
        offered = dropped + dupes + skipped_kind + parsed
        parsed  = kept + skipped_profile + skipped_old
    Если он нарушен — в сводке появляется «РАСХОЖДЕНИЕ N». Это не педантизм:
    именно так ловится парсер, который молча выбрасывает строки, не считая их.

    Живёт здесь, а не в `sources_web`, потому что счёт нужен КАЖДОМУ адаптеру.
    У источника без сводки потерянная строка неотличима от честного нуля: в отчёте
    «hh 192», и ни один тест не докажет, что площадка отдала не 200.
    """

    source: str
    offered: int = 0          # сколько строк отдал источник
    parsed: int = 0           # сколько превратилось в Vacancy
    dropped: int = 0          # не разобралось (нет id / url / названия)
    dupes: int = 0            # тот же id второй раз
    skipped_kind: int = 0     # это вообще не вакансия (промо-баннер площадки)
    skipped_profile: int = 0  # отсеяно фильтром профессии
    skipped_old: int = 0      # старше окна --days
    kept: int = 0             # записано в выдачу
    pages: int = 0
    requests: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def mismatch(self) -> int:
        """Сколько строк не сошлось. Ноль — значит потерь нет."""
        a = self.offered - (self.dropped + self.dupes + self.skipped_kind + self.parsed)
        b = self.parsed - (self.kept + self.skipped_profile + self.skipped_old)
        return abs(a) + abs(b)

    def row(self) -> Vacancy:
        """Служебная строка сводки.

        Пустой `url` — не косметика: `store.query` режет `_summary` с пустым url,
        поэтому сводка не попадает ни в выдачу, ни в счётчики вакансий.
        """
        parts = [f"отдано {self.offered}", f"разобрано {self.parsed}"]
        if self.dropped:
            parts.append(f"не разобралось {self.dropped}")
        if self.dupes:
            parts.append(f"дублей {self.dupes}")
        if self.skipped_kind:
            parts.append(f"не вакансий {self.skipped_kind}")
        if self.skipped_profile:
            parts.append(f"отсеяно по профессии {self.skipped_profile}")
        if self.skipped_old:
            parts.append(f"старше окна {self.skipped_old}")
        parts.append(f"записано {self.kept}")
        miss = self.mismatch()
        if miss:
            parts.append(f"РАСХОЖДЕНИЕ {miss} — строки потерялись между разбором и записью")
        title = f"[сводка {self.source}] " + ", ".join(parts)
        if self.notes:
            # Точка с запятой, а не пробел: примечания — законченные фразы, и от
            # склейки пробелом в живом прогоне получалось «они отфильтрованы
            # промо-карточек отброшено 4».
            title += ". " + "; ".join(self.notes)
        return Vacancy(
            source=self.source, external_id="_summary", url="", title=title,
            raw={"offered": self.offered, "parsed": self.parsed, "dropped": self.dropped,
                 "dupes": self.dupes, "skipped_kind": self.skipped_kind,
                 "skipped_profile": self.skipped_profile,
                 "skipped_old": self.skipped_old, "kept": self.kept,
                 "pages": self.pages, "requests": self.requests,
                 "mismatch": miss, "notes": self.notes},
        )


# ──────────────────────────────────────────────────────────────────────────────
# Разбор вилок
# ──────────────────────────────────────────────────────────────────────────────

_NUM = r"\d[\d\s   .,]*"
_CUR_SIGN = {"₽": "RUB", "руб": "RUB", "р.": "RUB", "$": "USD", "€": "EUR", "£": "GBP"}


def _num(s: str) -> int | None:
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


# Валюта перед числом — знаком или кодом. Списком, а не `\D*`: «до 5» в тексте
# «от 3 до 5 лет опыта» иначе слиплось бы в вилку.
_CUR_BEFORE = r"(?:[$€£₽฿]|\b(?:RUB|RUR|USD|EUR|GBP|KZT|BYN|UAH|GEL|AMD|PLN|TRY|THB)\b)?\s*"


# Суммы через K пишут geekjob, hirehi и Glassdoor: «от 350K ₽», «EUR 90K - EUR 130K».
# Без разворота множителя разбор отдавал 350 рублей вместо 350 000 — не падение,
# а уверенно напечатанная ложь в колонке «деньги», ошибка ровно в тысячу раз.
#
# Границы жёсткие с обеих сторон: слева не буква (иначе «OK» станет числом),
# справа не буква и не цифра (иначе «K8s» и «100Kb» превратятся в вилку).
_K_SUFFIX = re.compile(r"(?<![\w])(\d+(?:[.,]\d+)?)\s*(?:[KkКк]|тыс\.?)(?![\w])")


def expand_k(text: str | None) -> str | None:
    """«350K» → «350000», «1,5K» → «1500». Не трогает всё остальное."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        return str(int(round(float(m.group(1).replace(",", ".")) * 1000)))

    return _K_SUFFIX.sub(repl, text)


def parse_salary(text: str | None) -> tuple[int | None, int | None, str | None, bool | None]:
    """Разбирает «от 250 000 до 400 000 ₽», «400 000 ₽», «$3000–5000», «2 800—12 500 USD».

    Суффикс тысяч разворачивается ДО разбора: «350K ₽» — это 350 000. Площадки,
    которые так пишут, в реестре есть (geekjob, hirehi, Glassdoor), и раньше
    каждая из них молча теряла три нуля.
    """
    if not text:
        return None, None, None, None
    t = expand_k(H.unescape(text)).replace(" ", " ").replace(" ", " ").strip()
    if not t or re.search(r"з/п не указана|не указан|зарплата не указана", t, re.I):
        return None, None, None, None

    cur = None
    for sign, code in _CUR_SIGN.items():
        if sign in t:
            cur = code
            break
    if not cur:
        m = re.search(r"\b(RUB|RUR|USD|EUR|GBP|KZT|BYN|UAH|GEL|AMD|PLN|TRY)\b", t, re.I)
        if m:
            cur = m.group(1).upper()

    gross = None
    if re.search(r"\bgross\b|до вычета", t, re.I):
        gross = True
    elif re.search(r"\bnet\b|на руки|после вычета", t, re.I):
        gross = False

    # Валюта может стоять у КАЖДОЙ границы, и не только знаком: «$3000 - $5000»,
    # «฿50 000 – ฿75 000», «EUR 90K - EUR 130K» (Glassdoor). Без этого верхняя
    # граница молча терялась, и вилка «90 000–130 000 EUR» превращалась
    # в «от 90 000» — вроде бы не ошибка, а половина условий пропала.
    rng = re.search(rf"({_NUM})\s*(?:—|–|-|до|to|\.\.)\s*{_CUR_BEFORE}({_NUM})", t)
    if rng:
        return _num(rng.group(1)), _num(rng.group(2)), cur, gross
    only_from = re.search(rf"(?:от|from)\s*({_NUM})", t, re.I)
    if only_from:
        return _num(only_from.group(1)), None, cur, gross
    only_to = re.search(rf"(?:до|up to)\s*({_NUM})", t, re.I)
    if only_to:
        return None, _num(only_to.group(1)), cur, gross
    single = re.search(rf"({_NUM})", t)
    if single:
        n = _num(single.group(1))
        # Отсекаем мусор вроде «2026» и номеров — вилка ниже 10 000 в рублях неправдоподобна.
        if n and (n >= 10000 or cur in {"USD", "EUR", "GBP"}):
            return n, None, cur, gross
    return None, None, cur, gross


# Период, названный прямо в строке вилки: «300 000 ₽ в месяц», «$60 per hour».
# Отдельной функцией, а не пятым элементом parse_salary: у разбора вилки уже есть
# свой контракт из четырёх значений, и его ломать незачем.
_PERIOD_TEXT = (
    ("hour", r"в час|за час|почасов|per\s*hour|/\s*(?:hr|hour)|hourly"),
    ("month", r"в месяц|за месяц|per\s*month|/\s*mo(?:nth)?\b|monthly"),
    ("year", r"в год|за год|годов\w*|per\s*year|/\s*(?:yr|year)|yearly|annual"),
)


def period_from_text(text: str | None) -> str | None:
    """Период из текста вилки. Ничего не нашлось — None, и это честнее догадки.

    У habr, hh-текстов и ATS-досок период живёт (если живёт) прямо в строке денег.
    Нет слова — значит площадка периода не назвала: подставлять «месяц» по умолчанию
    нельзя, именно так почасовые ставки и превращаются в «зарплату».
    """
    if not text:
        return None
    t = H.unescape(str(text)).lower()
    for period, pattern in _PERIOD_TEXT:
        if re.search(pattern, t):
            return period
    return None


def _strip_tags(s: str) -> str:
    return H.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def _one(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.S)
    return H.unescape(m.group(group)).strip() if m else None


# ──────────────────────────────────────────────────────────────────────────────
# hh.ru — самый плотный источник, работает анонимно
# ──────────────────────────────────────────────────────────────────────────────

HH_PAGE = 100        # серверный потолок карточек на странице
HH_MAX_PAGES = 20    # 2000 вакансий на формулировку — предохранитель, а не режим


def src_hh(ctx: Ctx) -> list[Vacancy]:
    """Читает встроенный стейт `HH-Lux-InitialState`, а не вёрстку.

    Селекторы карточек на hh отдают пустоту, а в стейте лежит весь JSON: даты, вилки,
    работодатель, формат работы. `search_period` отбирает по публикации-ИЛИ-обновлению —
    ровно то окно, которое нужно, поэтому дополнительно резать по дате не надо.

    ПАГИНАЦИЯ ОБЯЗАТЕЛЬНА, и это не оптимизация. Один запрос без `page` отдаёт
    максимум 100 карточек, а сама площадка в том же ответе пишет, сколько их всего:
    по «Go» за трое суток — 396. Триста вакансий, которых не было в отчёте, выглядели
    как «на hh больше нет». Страницы нумеруются с нуля; выдача кончается либо
    исчерпанием totalResults, либо пустой страницей.
    """
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("hh")
    budget = _page_budget(ctx, HH_PAGE, HH_MAX_PAGES)
    for q in ctx.queries():
        total: int | None = None
        taken = pages = 0
        for page in range(budget):
            if page:
                _pause()
            url = qs("https://hh.ru/search/vacancy", {
                "text": q, "area": ctx.area, "order_by": "publication_time",
                "search_period": ctx.days, "items_on_page": HH_PAGE, "page": page,
            })
            text, final = fetch(url)
            tally.requests += 1
            m = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>',
                          text, re.S)
            if not m:
                raise FetchError(
                    final, "нет HH-Lux-InitialState — вёрстка сменилась или показана капча")
            state = json.loads(H.unescape(m.group(1)))
            result = state.get("vacancySearchResult") or {}
            rows = result.get("vacancies") or []
            if total is None:
                # totalResults плавает между страницами (396 → 398: выдача живая),
                # поэтому в сводку идёт значение первой страницы, а не последней.
                total = result.get("totalResults")
            if not rows:
                break
            pages += 1
            tally.pages += 1
            taken += len(rows)
            _hh_rows(rows, q, out, seen, tally)
            if total and taken >= total:
                break
        else:
            # Бюджет страниц кончился раньше выдачи. Молчать здесь нельзя:
            # «hh 2000» ничем не отличается от «hh отдал ровно 2000».
            if total is None or taken < total:
                tally.note(_truncated_note(f"«{q}»", taken, total))
        tally.note(f"«{q}»: в выдаче {total if total is not None else '?'}, "
                   f"взято {taken} за {pages} стр.")
    tally.note("окно --days применяет сама площадка (search_period): "
               "по публикации-ИЛИ-обновлению")
    out.append(tally.row())
    return out


def _hh_rows(rows: list, q: str, out: list[Vacancy], seen: set[str], tally: Tally) -> None:
    """Разбор одной страницы hh. Вынесен из `src_hh`, чтобы цикл по страницам
    читался целиком, а не тонул в сборке карточки."""
    for v in rows:
        tally.offered += 1
        vid = str(v.get("vacancyId") or "")
        if not vid:
            tally.dropped += 1
            continue
        if vid in seen:
            # Три формулировки запроса пересекаются — это ожидаемо и НЕ потеря.
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        tally.kept += 1
        comp = v.get("compensation") or {}
        company = v.get("company") or {}
        links = v.get("links") or {}
        pub = (v.get("publicationTime") or {})
        out.append(Vacancy(
            source="hh",
            external_id=vid,
            url=links.get("desktop") or f"https://hh.ru/vacancy/{vid}",
            title=v.get("name") or "",
            company=company.get("visibleName") or company.get("name"),
            salary_from=comp.get("from"),
            salary_to=comp.get("to"),
            currency=comp.get("currencyCode"),
            salary_gross=comp.get("gross"),
            # hh кладёт в from/to МЕСЯЧНУЮ сумму всегда, даже когда вилка
            # объявлена за смену: у охранника с mode=SHIFT приезжает
            # from 112 500 / perModeFrom 7 500 (это 15 смен), а на карточке
            # площадка показывает «7 500 – 8 000 ₽ за смену». Мы берём from/to,
            # значит период у нашей вилки — месяц. Сырой mode и perMode* лежат
            # в raw, чтобы этот вывод можно было перепроверить, а не поверить.
            salary_period=("month" if (comp.get("from") or comp.get("to")) else None),
            location=(v.get("area") or {}).get("name"),
            remote=any("REMOTE" in str(f) for f in (v.get("workFormats") or [])),
            published_at=pub.get("$") or pub.get("@timestamp"),
            updated_at=v.get("lastChangeTime"),
            tags=[t for t in (v.get("tags") or []) if isinstance(t, str)],
            raw={"employerId": company.get("id"), "responses": v.get("responsesCount"),
                 "experience": v.get("workExperience"), "query": q,
                 "compensationMode": comp.get("mode"),
                 "perMode": [comp.get("perModeFrom"), comp.get("perModeTo")]
                            if (comp.get("perModeFrom") or comp.get("perModeTo")) else None},
        ))


# ──────────────────────────────────────────────────────────────────────────────
# career.habr.com
# ──────────────────────────────────────────────────────────────────────────────

HABR_PAGE = 25        # фиксированный размер страницы Хабра, менять его нечем
HABR_MAX_PAGES = 40   # 1000 карточек на формулировку
# Сколько страниц подряд без единой НОВОЙ карточки считать зацикленной пагинацией.
# Не одна и не две: формулировки пересекаются («Backend Go» почти целиком лежит
# внутри «Go разработчик»), и страница из одних повторов — это удачный поиск,
# а не поломка. Настоящие границы обхода здесь другие — край окна и rel="next";
# этот счётчик только страхует от бесконечного цикла.
HABR_BARREN_STREAK = 3


# Блок пагинации внизу выдачи. `rel="next"` внутри него — единственный честный
# признак «есть ещё страница»: ссылки `page=N` встречаются и в других местах
# документа, и по ним «Golang» (47 вакансий, 2 страницы) выглядел как 25 страниц.
_HABR_PAGINATION = re.compile(r'<div class="pagination">(.*?)</div>\s*</div>', re.S)


def src_habr(ctx: Ctx) -> list[Vacancy]:
    """Хабр Карьера, выдача сортируется по дате.

    ПАГИНАЦИЯ ОБЯЗАТЕЛЬНА: 25 — это размер страницы Хабра, а не «столько нашлось».
    Проверено на семи формулировках подряд: каждая возвращала ровно 25. При полном
    обходе «Golang» отдаёт 47, «Go разработчик» — 493, «Backend Go» — 389.

    Окно свежести режется здесь же, по basic-date. Это дата ПОДНЯТИЯ карточки
    в выдаче (по ней же выдача и отсортирована), поэтому «старше окна» означает
    «не публиковалась и не поднималась N дней» — ровно та же семантика, что
    у hh с его search_period. Карточка без даты остаётся: «даты нет» и «старая» —
    разные вещи.
    """
    out, seen = [], set()
    tally = Tally("habr")
    edge = _cutoff(ctx.days)
    budget = _page_budget(ctx, HABR_PAGE, HABR_MAX_PAGES)
    for q in ctx.queries():
        pages = barren = 0
        for page in range(1, budget + 1):
            if page > 1:
                _pause()
            url = qs("https://career.habr.com/vacancies",
                     {"q": q, "type": "all", "sort": "date", "page": page})
            text, final = fetch(url)
            tally.requests += 1
            # Карточки бывают с модификаторами (`vacancy-card vacancy-card--featured`),
            # поэтому режем по границе слова, а не по `class="vacancy-card"` целиком:
            # иначе куски слипаются и чипы одной карточки утекают в другую.
            chunks = re.split(r'<div class="vacancy-card[\s"]', text)[1:]
            if not chunks:
                if page > 1:
                    break  # выдача кончилась — это не поломка
                if "vacancy-card" in text:
                    raise FetchError(final,
                                     "карточки есть, но разметка сменилась — парсер надо чинить")
                if not re.search(r"ничего не найдено|вакансий не найдено", text, re.I):
                    raise FetchError(final,
                                     "ноль карточек и нет пометки «не найдено» — похоже на блок")
                tally.note(f"«{q}»: площадка ответила «ничего не найдено»")
                break
            pages += 1
            tally.pages += 1
            fresh, added = _habr_rows(chunks, q, edge, out, seen, tally)
            if added:
                barren = 0
            else:
                barren += 1
            block = _HABR_PAGINATION.search(text)
            has_next = 'rel="next"' in (block.group(1) if block else "")
            if not fresh:
                # Выдача отсортирована по дате: если на странице нет ни одной
                # карточки внутри окна, дальше их и не будет.
                tally.note(f"«{q}»: остановились на выходе за окно --days (стр. {page})")
                break
            if not has_next:
                break
            if barren >= HABR_BARREN_STREAK:
                tally.note(f"«{q}»: {HABR_BARREN_STREAK} стр. подряд без новых карточек "
                           f"(стр. {page}) — обход остановлен")
                break
        else:
            tally.note(_truncated_note(f"«{q}»", pages * HABR_PAGE, None))
        tally.note(f"«{q}»: страниц пройдено {pages}")
    tally.note("--days режется по basic-date (дата поднятия карточки, она же "
               "порядок сортировки); карточки без даты остаются")
    out.append(tally.row())
    return out


def _habr_rows(chunks: list[str], q: str, edge: datetime, out: list[Vacancy],
               seen: set[str], tally: Tally) -> tuple[int, int]:
    """Разбор одной страницы Хабра → (сколько внутри окна, сколько новых записано).

    «Внутри окна» считается по СЫРЫМ карточкам, до отсева дублей: страница целиком
    из повторов — норма для пересекающихся формулировок, и принять её за край окна
    значит оборвать обход на середине.
    """
    fresh = sum(
        1 for c in chunks
        if not _older_than(_one(r'<time class="basic-date" datetime="([^"]+)"', c), edge))
    added = 0
    for c in chunks:
        tally.offered += 1
        vid = _one(r'href="/vacancies/(\d+)"', c)
        if not vid:
            # Карточка без ссылки на вакансию — это не вакансия (баннер,
            # блок «похожие»). Считаем, а не выбрасываем молча.
            tally.dropped += 1
            continue
        if vid in seen:
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        # <time class="basic-date"> на карточке — дата ПОДНЯТИЯ в выдаче,
        # а не публикации: у одной и той же вакансии карточка показывала
        # 30.07, а JSON-LD на её странице — datePosted 09.07. Кладём её
        # в updated_at, чтобы `detail` и `new` не спорили о факте.
        # Исходную дату публикации отдаёт только страница вакансии.
        bumped = _one(r'<time class="basic-date" datetime="([^"]+)"', c)
        if _older_than(bumped, edge):
            tally.skipped_old += 1
            continue
        added += 1
        tally.kept += 1
        sal = _strip_tags(_one(r'class="basic-salary[^"]*">(.*?)</div>', c) or "")
        sf, st, cur, gross = parse_salary(sal)
        # Habr пишет «от 300 000 до 490 000 ₽» без периода — значит период
        # остаётся неизвестным. Он ставит его словами лишь изредка, и вот
        # тогда мы его и берём.
        period = period_from_text(sal)
        chips = [H.unescape(x).strip() for x in
                 re.findall(r'class="chip-with-icon__text">(.*?)</div>', c, re.S)]
        # Чипы идут вперемешку: грейд, формат работы и города. Город — это всё,
        # что не грейд и не «Можно удалённо».
        grades = {"Senior", "Middle", "Junior", "Lead", "Intern", "Стажёр"}
        remote_chip = "Можно удалённо"
        cities = [x for x in chips if x not in grades and x != remote_chip]
        out.append(Vacancy(
            source="habr",
            external_id=vid,
            url=f"https://career.habr.com/vacancies/{vid}",
            title=_strip_tags(_one(r'class="vacancy-card__title-link"[^>]*>(.*?)</a>', c) or ""),
            company=_strip_tags(_one(r'class="vacancy-card__company".*?<a[^>]*>(.*?)</a>', c) or ""),
            salary_from=sf, salary_to=st, currency=cur, salary_gross=gross,
            salary_period=period,
            location=", ".join(cities) or None,
            remote=remote_chip in chips,
            updated_at=bumped,
            tags=[x for x in chips if x in grades],
            raw={"chips": chips, "query": q,
                 "date_kind": "поднятие в выдаче (basic-date)"},
        ))
    return fresh, added


# ──────────────────────────────────────────────────────────────────────────────
# careered.io — чистый JSON, снимает проблему SPA целиком
# ──────────────────────────────────────────────────────────────────────────────

CAREERED_PAGE = 20        # серверный потолок: limit / per_page / count игнорируются
CAREERED_MAX_PAGES = 100  # 2000 записей — вся лента целиком (в ней ~1800)


def src_careered(ctx: Ctx) -> list[Vacancy]:
    """careered.io — чистый JSON, лента отсортирована по дате.

    СЕРВЕРНОГО ПОИСКА У ПЛОЩАДКИ НЕТ ВОВСЕ: `?query=` и `?search=` молча
    игнорируются, `total` остаётся 1797 при любом запросе. Раньше это выглядело
    как поиск: адаптер брал 100 самых свежих записей ЛЮБОЙ профессии, и первыми
    в отчёт ехали QA Engineer и Business Analysis Tech Lead.

    Поэтому здесь два изменения против «взять сотню»: лента читается ДО ВЫХОДА
    ЗА ОКНО --days (она датирована и отсортирована, так что край окна — честная
    граница обхода), а профессия отсекается у нас тем же ATS_ROLE_RE, что на
    ATS-досках. Сколько отдано, сколько под профиль, сколько отсеяно — печатает
    сводка: без этих трёх чисел «careered 60» неотличимо от «в ленте всего 60».
    """
    out: list[Vacancy] = []
    tally = Tally("careered")
    edge = _cutoff(ctx.days)
    budget = _page_budget(ctx, CAREERED_PAGE, CAREERED_MAX_PAGES)
    offset = total = 0
    stopped = "лента кончилась"
    seen: set[str] = set()
    for page in range(budget):
        if page:
            _pause()
        data = fetch_json(f"https://careered.io/api/jobs?offset={offset}")
        tally.requests += 1
        entries = data.get("entries") or []
        total = data.get("total") or total
        if not entries:
            break
        tally.pages += 1
        for e in entries:
            tally.offered += 1
            if e.get("kind") != "job":
                # В ленте есть не только вакансии (kind=company и прочее).
                tally.skipped_kind += 1
                continue
            eid = str(e.get("id") or "")
            if not eid:
                tally.dropped += 1
                continue
            if eid in seen:
                # Обход ленты растянут на десятки запросов, и за это время в неё
                # успевают добавить свежее — тогда сдвиг offset приносит запись
                # второй раз. Это не потеря, но и не вторая вакансия.
                tally.dupes += 1
                continue
            seen.add(eid)
            tally.parsed += 1
            # Вилка приходит разложенной по полям, а не строкой: salary_from / salary_to /
            # salary_currency / salary_period. Разбирать текстом тут нечего и не нужно.
            feats = {f.get("key"): f.get("value") for f in (e.get("features") or [])}
            title = feats.get("name") or feats.get("title") or ""
            posted = _iso_stamp(e.get("posted_at"))
            if _older_than(posted, edge):
                tally.skipped_old += 1
                continue
            if not (ctx.ats_all or ATS_ROLE_RE.search(title)):
                tally.skipped_profile += 1
                continue
            tally.kept += 1
            # Ноль у careered означает «вилка не указана», а не «платят ноль».
            # Записать 0 — значит показать в карточке «0–0 ₽» и соврать про условия.
            def to_int(v):
                s = str(v or "").strip()
                return int(s) if s.isdigit() and int(s) > 0 else None
            loc = feats.get("location") or feats.get("city")
            out.append(Vacancy(
                source="careered",
                external_id=eid,
                url=f"https://careered.io/jobs/{eid}",
                title=title,
                company=feats.get("company") or feats.get("employer"),
                salary_from=to_int(feats.get("salary_from")),
                salary_to=to_int(feats.get("salary_to")),
                currency=feats.get("salary_currency"),
                # hour | month | year — careered отдаёт период прямо полем.
                # Именно здесь жили «19–23 USD» рядом с «168 000–333 500 USD»:
                # почасовая ставка без подписи читается как месячная зарплата.
                salary_period=feats.get("salary_period"),
                location=loc,
                remote="remote" in str(loc or "").lower(),
                published_at=e.get("posted_at"),
                description=feats.get("summary") or feats.get("summary_short"),
                tags=[t for t in [(e.get("tag") or {}).get("name"), feats.get("term")] if t],
                # Контакт у careered за БЕСПЛАТНОЙ регистрацией — это не платный посредник.
                raw={"features": feats, "yoe": feats.get("yoe"),
                     "salary_period": feats.get("salary_period"),
                     "note": "контакт за бесплатной регистрацией"},
            ))
        offset += len(entries)
        # Лента датирована и отсортирована по убыванию даты, поэтому граница
        # обхода честная: как только САМАЯ СТАРАЯ запись страницы вышла за окно,
        # окно кончилось прямо на этой странице и следующая уже вся старше.
        oldest = _iso_stamp((entries[-1] or {}).get("posted_at"))
        if oldest and _older_than(oldest, edge):
            stopped = f"дошли до края окна --days {ctx.days}"
            break
        if total and offset >= total:
            stopped = "лента кончилась"
            break
    else:
        stopped = "потолок страниц"
        tally.note(_truncated_note("лента", offset, total or None))
    tally.note(f"серверного поиска у площадки НЕТ (query/search игнорируются): "
               f"в ленте {total or '?'}, прочитано {offset}, остановка — {stopped}")
    tally.note("отбор по профессии наш (ATS_ROLE_RE): "
               f"под профиль {tally.kept}, отсеяно {tally.skipped_profile}, "
               f"старше окна {tally.skipped_old}")
    out.append(tally.row())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# LinkedIn — гостевой поиск, без логина
# ──────────────────────────────────────────────────────────────────────────────

# По региону «Россия» гостевой поиск отдаёт ноль — вся ценность в зарубежных.
LINKEDIN_REGIONS = ("Germany", "Netherlands", "Poland", "Cyprus", "Portugal", "Spain",
                    "United Kingdom", "European Union", "Türkiye")


LINKEDIN_PAGE = 10        # гостевая выдача отдаёт ровно 10 карточек за запрос
LINKEDIN_MAX_PAGES = 30   # предохранитель: start=0…290 на регион
# Сколько страниц подряд без единой профильной карточки считать концом полезной
# выдачи. Замер по Германии (start=0…250, «под профиль» на страницу):
# 10,9,10,10,10,10,1,3,0,10,1,0,10,0,0,10,1,10,1,0,0,10,10,0,0,0 — выдача
# уезжает вбок рывками, поэтому одна и даже две пустые страницы концом НЕ
# считаются: на start=150 и start=210 профильные снова были. Три подряд —
# считаются: дальше LinkedIn добивает регион чем попало («Pflegehilfskraft»
# по запросу Golang), и это уже не наша выдача.
LINKEDIN_DRY_STREAK = 3
# LinkedIn троттлит охотнее всех остальных, а пагинация превращает 9 запросов
# в сотню. Пауза здесь длиннее общей: дешевле подождать, чем потерять регион.
LINKEDIN_PAUSE = 1.2


def src_linkedin(ctx: Ctx) -> list[Vacancy]:
    """Гостевой поиск LinkedIn: без логина, но и без второй страницы «из коробки».

    ПАГИНАЦИЯ `start=`: один запрос отдаёт 10 карточек, и «linkedin 80» означало
    ровно «мы спросили по одной странице на девять регионов». По одной Германии
    за то же окно start=0…200 даёт 155 уникальных карточек. Конец выдачи виден
    честно — пустой ответ (26 байт), поэтому обход кончается сам, а не по счёту.
    Соседние окна пересекаются (start=40 бывает целиком из уже виденного), так
    что «ноль новых» концом выдачи НЕ считается.

    ФИЛЬТР ПРОФЕССИИ. Гостевой поиск по ключевому слову нечёткий: из 87 карточек
    прошлого прогона 23 были Financial Controller, Head of Finance и HR/Payroll
    Manager. Отсекаем тем же ATS_ROLE_RE, что и на ATS-досках (снимается --ats-all).
    Он же служит второй границей обхода: вглубь выдача уезжает от запроса совсем
    (по «Golang» в Германии со start≈190 идут «Pflegehilfskraft» и «Vorarbeiter
    Maurer»), и три пустые по профилю страницы подряд означают конец полезной
    выдачи, а не потолок.

    ЧЕСТНО ПРО ПОТОЛОК. У больших регионов выдача не кончается и за сорок страниц:
    в живом прогоне Germany, Netherlands и European Union упёрлись именно в потолок
    (по ~400 карточек каждый), причём профильные роли попадались до самого конца.
    Это не «мы всё забрали» — это «дальше не пошли», и ровно так об этом и написано
    в сводке строкой ОБРЕЗАНО с подсказкой про --limit.

    ЦЕНА ГЛУБИНЫ. Соседние окна `start=` пересекаются больше чем наполовину
    (648 повторов на 1160 карточек), поэтому каждая следующая страница приносит
    всё меньше нового, а запросов стоит столько же. Отсюда пауза в 1.2 с и счётчик
    запросов прямо в сводке: глубина здесь оплачивается вежливостью к площадке,
    и цена должна быть видна, а не выясняться по бану.

    Спрашивается ТОЛЬКО основная формулировка: девять регионов на три формулировки —
    это до 540 запросов к площадке, которая банит за меньшее. Об этом пишет сводка.
    """
    tally = Tally("linkedin")
    if not ctx.include_foreign:
        tally.note("--ru-only: гостевой поиск по России отдаёт ноль, регионы не спрашивались")
        return [tally.row()]
    out, seen = [], set()
    # 1 день ≈ r86400; берём с запасом окна.
    seconds = max(ctx.days, 1) * 86400
    lost_regions: list[str] = []
    truncated: list[str] = []
    drifted: list[str] = []
    # Потолок считается ПО ВСЕМ РЕГИОНАМ СРАЗУ, а не по каждому: `--limit 400`
    # означает «принеси примерно четыреста карточек», а не «четыреста из каждой
    # из девяти стран» (это 3 600 карточек и 360 запросов вместо сорока).
    budget = _page_budget(ctx, LINKEDIN_PAGE * len(LINKEDIN_REGIONS), LINKEDIN_MAX_PAGES)
    regions_done = 0
    for region in LINKEDIN_REGIONS:
        cards = dry = 0
        for page in range(budget):
            if tally.requests:
                _pause(LINKEDIN_PAUSE)
            url = qs("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", {
                "keywords": ctx.query, "location": region,
                "start": page * LINKEDIN_PAGE, "f_TPR": f"r{seconds}",
            })
            try:
                text, _ = fetch(url)
                tally.requests += 1
            except FetchError as e:
                # 429 у LinkedIn — норма при частых запросах; регион пропускаем,
                # прогон живёт. Но пропущенный регион — это НЕ ноль вакансий в нём,
                # и молчать об этом нельзя: иначе «linkedin 86» читается как полный
                # обход девяти стран. Уже собранные страницы региона остаются.
                if e.status in (429, 403):
                    lost_regions.append(f"{region} (HTTP {e.status} на стр. {page + 1})")
                    break
                raise
            chunks = text.split('<div class="base-card')[1:]
            if not chunks:
                break  # пустой ответ — выдача региона кончилась
            tally.pages += 1
            cards += len(chunks)
            dry = 0 if _linkedin_rows(chunks, region, ctx, out, seen, tally) else dry + 1
            if dry >= LINKEDIN_DRY_STREAK:
                # Не «обрезано»: площадка перестала отвечать на наш запрос
                # и добивает регион чем попало. Это конец выдачи, а не потолок.
                drifted.append(f"{region} (стр. {page + 1})")
                break
        else:
            truncated.append(f"{region} ({cards})")
        if cards:
            regions_done += 1
    tally.note(f"регионов с выдачей {regions_done}/{len(LINKEDIN_REGIONS)}, "
               f"страниц {tally.pages}, запросов {tally.requests}")
    tally.note(f"формулировка одна («{ctx.query}»): девять регионов на три "
               f"формулировки — это сотни запросов к площадке, которая троттлит")
    if drifted:
        tally.note(f"выдача уехала от запроса (>{LINKEDIN_DRY_STREAK} стр. подряд без "
                   f"профильных ролей), обход региона закончен: {', '.join(drifted)}")
    if truncated:
        tally.note(f"ОБРЕЗАНО по потолку страниц ({budget}): {', '.join(truncated)} — "
                   f"за остальным нужен --limit больше")
    if lost_regions:
        tally.note(f"НЕ ОТДАЛИСЬ регионы: {', '.join(lost_regions)} — "
                   f"это не ноль вакансий в них, а неспрошенная выдача")
    out.append(tally.row())
    return out


def _linkedin_title(chunk: str) -> str:
    return _strip_tags(_one(r'<span class="sr-only">(.*?)</span>', chunk) or "")


def _linkedin_rows(chunks: list[str], region: str, ctx: Ctx, out: list[Vacancy],
                   seen: set[str], tally: Tally) -> int:
    """Разбор страницы региона → сколько карточек ОТНОСЯТСЯ К ПРОФЕССИИ.

    Считается по сырым карточкам, ДО отсева дублей, и это важно: регионы
    пересекаются (в живом прогоне 483 повтора на 910 карточек), и страница
    целиком из уже виденных Go-вакансий — это удачный поиск, а не уход выдачи
    вбок. Считать её пустой значило бы обрывать обход «European Union» на том,
    что он повторяет Германию.
    """
    relevant = sum(1 for c in chunks
                   if ctx.ats_all or ATS_ROLE_RE.search(_linkedin_title(c)))
    for c in chunks:
        tally.offered += 1
        vid = _one(r'data-entity-urn="urn:li:jobPosting:(\d+)"', c)
        if not vid:
            tally.dropped += 1
            continue
        if vid in seen:
            # Окна start= пересекаются — повтор здесь норма, а не потеря.
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        title = _linkedin_title(c)
        if not (ctx.ats_all or ATS_ROLE_RE.search(title)):
            tally.skipped_profile += 1
            continue
        tally.kept += 1
        out.append(Vacancy(
            source="linkedin",
            external_id=vid,
            url=(_one(r'href="(https://[^"]*?/jobs/view/[^"?]+)', c) or
                 f"https://www.linkedin.com/jobs/view/{vid}"),
            title=title,
            company=_strip_tags(_one(r'hidden-nested-link[^>]*>(.*?)</a>', c) or ""),
            location=_strip_tags(_one(r'job-search-card__location">(.*?)</span>', c) or ""),
            published_at=_one(r'<time[^>]*datetime="([^"]+)"', c),
            remote=None,
            # Описание LinkedIn отдаёт анонимно вот по этому адресу (62 КБ без
            # стены), тогда как /jobs/view/ упирается в капчу. Кладём ссылку
            # в raw, чтобы `detail` брал текст оттуда, а не бился в проверку.
            raw={"region": region,
                 "guest_description_api":
                     f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{vid}"},
        ))
    return relevant


# ──────────────────────────────────────────────────────────────────────────────
# ATS-доски работодателей — приоритет №1 по «близости к нанимателю»
# ──────────────────────────────────────────────────────────────────────────────

# Реестр проверенных живыми запросами токенов (см. references/sources-setup.md).
# Токены НЕ угадываются: половина очевидных не существует, часть ведёт не туда
# (greenhouse `insider` — это Business Insider, а не турецкий useInsider).
#
# Движки, которые прогон умеет опрашивать, перечислены в `_ATS_IMPL` — там их
# СЕМЬ, а не четыре: строку `("workable", "<токен>")` теперь можно добавить сюда
# и она отработает. Раньше три движка существовали только для ручных команд,
# и найденная через `ats sniff` компания на Workable в прогон не попадала.
ATS_BOARDS: list[tuple[str, str]] = [
    ("greenhouse", "canonical"), ("greenhouse", "sezzle"), ("greenhouse", "fundraiseup"),
    ("greenhouse", "okx"), ("greenhouse", "bybit"), ("greenhouse", "gitlab"),
    ("greenhouse", "datadog"), ("greenhouse", "platacard"), ("greenhouse", "internalhiring"),
    ("lever", "jobgether"), ("lever", "binance"), ("lever", "appen"), ("lever", "weloglobal"),
    ("ashby", "ruby-labs"), ("ashby", "everai"), ("ashby", "oakslab"),
    ("ashby", "poolside"), ("ashby", "synthflow"),
    ("recruitee", "kodland"), ("recruitee", "nucsai"),
]


def _ats_greenhouse(token: str) -> list[Vacancy]:
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false")
    return [Vacancy(
        source=f"ats:greenhouse:{token}",
        external_id=str(j.get("id")),
        url=j.get("absolute_url") or "",
        title=j.get("title") or "",
        company=token,
        # Greenhouse прячет страну в offices[], а иногда прямо в заголовке.
        location=(j.get("location") or {}).get("name")
                 or ", ".join(o.get("name", "") for o in (j.get("offices") or [])),
        published_at=j.get("updated_at") or j.get("first_published"),
        employer_url=j.get("absolute_url"),
        raw={"ats": "greenhouse", "token": token},
    ) for j in d.get("jobs", [])]


def _ats_lever(token: str) -> list[Vacancy]:
    d = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    return [Vacancy(
        source=f"ats:lever:{token}",
        external_id=str(j.get("id")),
        url=j.get("hostedUrl") or j.get("applyUrl") or "",
        title=j.get("text") or "",
        company=token,
        location=(j.get("categories") or {}).get("location"),
        published_at=j.get("createdAt"),
        employer_url=j.get("applyUrl") or j.get("hostedUrl"),
        tags=[t for t in (j.get("tags") or []) if isinstance(t, str)],
        raw={"ats": "lever", "token": token,
             "workplaceType": j.get("workplaceType"),
             "allLocations": j.get("allLocations")},
    ) for j in (d if isinstance(d, list) else [])]


def _ats_ashby(token: str) -> list[Vacancy]:
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    out = []
    for j in d.get("jobs", []):
        # Ashby прячет вторую страну в secondaryLocations[] — без него теряются вакансии.
        secondary = [s.get("location") for s in (j.get("secondaryLocations") or [])
                     if isinstance(s, dict)]
        out.append(Vacancy(
            source=f"ats:ashby:{token}",
            external_id=str(j.get("id")),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            title=j.get("title") or "",
            company=d.get("name") or token,
            location=" / ".join(filter(None, [j.get("location"), *secondary])),
            published_at=j.get("publishedAt"),
            remote=bool(j.get("isRemote")),
            employer_url=j.get("applyUrl") or j.get("jobUrl"),
            raw={"ats": "ashby", "token": token, "secondaryLocations": secondary,
                 "department": j.get("department")},
        ))
    return out


def _ats_recruitee(token: str) -> list[Vacancy]:
    d = fetch_json(f"https://{token}.recruitee.com/api/offers/")
    return [Vacancy(
        source=f"ats:recruitee:{token}",
        external_id=str(j.get("id")),
        url=j.get("careers_url") or j.get("careers_apply_url") or "",
        title=j.get("title") or "",
        company=token,
        location=j.get("location") or j.get("city"),
        published_at=j.get("published_at") or j.get("created_at"),
        remote=bool(j.get("remote")),
        employer_url=j.get("careers_apply_url") or j.get("careers_url"),
        raw={"ats": "recruitee", "token": token, "department": j.get("department")},
    ) for j in d.get("offers", [])]


def _ats_from_board(kind: str):
    """Адаптер поверх `atsapi.board` для движков, которые уже разобраны там.

    Workable, SmartRecruiters и BambooHR жили ТОЛЬКО в `atsapi` — то есть были
    доступны ручным командам (`ats check`, `ats jobs`, `detail`) и не существовали
    для прогона: компанию на Workable нельзя было добавить в реестр досок, даже
    найдя её через `ats sniff`. Дублировать реализации ради этого не надо, у них
    у всех есть готовый разбор; нужен только перевод BoardJob → Vacancy.

    Реализации там непростые и стоят того, чтобы не переписывать: у Workable
    виджетный API вакансий больше не отдаёт и список берётся из markdown-выдачи
    careers-страницы, у SmartRecruiters — своя пагинация по 100.
    """
    def run(token: str) -> list[Vacancy]:
        from .atsapi import board  # noqa: PLC0415 — не тянуть модуль без нужды
        b = board(kind, token)
        return [Vacancy(
            source=f"ats:{kind}:{token}",
            external_id=j.id,
            url=j.url,
            title=j.title,
            company=b.company or token,
            # `locations` собирает ВСЕ поля, где движок прячет страну, — ровно то,
            # ради чего структурный матч локаций и делался.
            location=" / ".join(dict.fromkeys(x for x in j.locations if x)) or None,
            published_at=j.published_at,
            employer_url=j.url,
            raw={"ats": kind, "token": token, "locations": j.locations,
                 "board_note": b.note},
        ) for j in b.jobs]
    return run


_ATS_IMPL = {"greenhouse": _ats_greenhouse, "lever": _ats_lever,
             "ashby": _ats_ashby, "recruitee": _ats_recruitee,
             # Три движка, которых в прогоне не было вовсе (см. `_ats_from_board`).
             "workable": _ats_from_board("workable"),
             "smartrecruiters": _ats_from_board("smartrecruiters"),
             "bamboohr": _ats_from_board("bamboohr")}


# Роли, которые вообще имеет смысл нести дальше. Двадцать досок отдают ~6 600 вакансий,
# и подавляющее большинство — продажи, поддержка и маркетинг в других странах.
# Это не отбор по фиту (его делает модель), а отсечение заведомо другой профессии.
#
# Русская половина списка появилась после проверки на реальных заголовках из базы:
# фильтр писался под англоязычные ATS-доски, а с приходом hh, geekjob, getmatch,
# hackoffer и dreamoffer через него поехали русские названия — и «Бэкенд-разработчик
# (Go)» отсеивался как чужая профессия, потому что \bbackend\b по «бэкенд» не бьётся.
# Это худший вид потери: вакансия ровно та, что искали, а в отчёте её нет вовсе.
#
# ГОЛОГО «разработчик»/«инженер» здесь нет СОЗНАТЕЛЬНО: они матчат и «Фронтенд-
# разработчик», и «Инженер-конструктор», то есть отменяют фильтр целиком. Пропуск
# лишнего стоит одной строки в отчёте, пропажа своего — потерянной вакансии,
# поэтому список широкий, но не бесконечный.
#
# ПОЧЕМУ ЗДЕСЬ ЛЕЖАТ ИМЕННО ЭТИ КОРНИ. Промахи фильтра оказались не случайными,
# а кучными: одна недописанная формулировка теряла целую компанию. Замер по 7 488
# живым заголовкам с двадцати досок:
#   • `sre` есть, английского `reliability` НЕТ → «Site Reliability Engineer»,
#     «Principal Site Reliability Engineer», «Database Reliability Engineer» —
#     мимо (31 строка);
#   • `software engineer` есть, `software developer` / `software engineering` НЕТ →
#     мимо 53 строки, включая «Principal Software Development Engineer»;
#   • «Member of Technical Staff» / «Member of Engineering» — так poolside называет
#     ВСЕ инженерные позиции, включая Compute и Infra → мимо 24 строки;
#   • `cloud` без хвоста → «CloudOps Engineer» мимо;
#   • голое «<грейд> Engineer» (Canonical, poolside) → «Staff Engineer»,
#     «Principal Engineer — Real-Time Data Systems» мимо 35 строк.
# Итого расширение пускает +212 строк из 7 488 (2.8%). Часть из них — чужие
# («HR Systems Analyst», «Revenue Systems Administrator»: цена голого `system\w*`,
# 93 строки из этих 212). Это сознательный размен: лишняя строка стоит одного
# взгляда, пропавшая вакансия — самой вакансии.
ATS_ROLE_RE = re.compile(
    # Хвост \w* у корней — не педантизм: «Platforms» и «бэкенда» мимо \bplatform\b
    # и \bбэкенд\b проходят, и «Разработчик бэкенда в Яндекс Образование» отсеивался
    # как чужая профессия. Проверено на 3147 заголовках из базы.
    r"\b(go|golang|backend|back-end|back end|platform\w*|infra\w*|sre|reliability|"
    r"devops|distributed|microservice\w*|kubernetes|cloud\w*|system\w*|"
    r"software\s+(?:engineer|developer|develop)\w*|"
    # Так называет инженеров poolside — включая Compute, Infra и Pre-training.
    r"member of (?:technical|engineering)\w*|"
    # Голое «Engineer» брать нельзя (оно матчит и «Sales Engineer»), а вот
    # «<грейд> Engineer» — рабочее название целых инженерных линеек у Canonical.
    r"(?:senior|staff|principal|lead|chief)\s+engineer|"
    r"full[- ]?stack|tech(nical)? lead|team[- ]?lead|architect\w*|"
    # Русские формулировки тех же ролей.
    r"голанг|бэкенд\w*|бекенд\w*|бэк[- ]?энд\w*|"
    r"платформ\w*|инфраструктур\w*|микросервис\w*|"
    r"высоконагруж\w*|распределённ\w*|распределенн\w*|"
    r"систем(?:ный|ного|ные)\s+(?:инженер|программист|разработчик)|"
    r"тимлид\w*|техлид\w*|тим[- ]лид\w*|тех[- ]лид\w*|архитектор\w*|"
    r"надёжност\w*|надежност\w*|девопс)\b",
    re.I,
)


def src_ats(ctx: Ctx) -> list[Vacancy]:
    """Опрашивает реестр досок работодателей. Упавшая доска не роняет остальные.

    Доску опрашиваем ЦЕЛИКОМ, а потом отсекаем заведомо чужие профессии по названию
    роли. Ровно поэтому у Exness нашлись три Go-роли вместо одной: соседние позиции
    видно только при полном опросе.

    Сколько отсеяно — печатается и попадает в `raw`: «тихо потерял» и «отфильтровал»
    должны отличаться, иначе через месяц никто не докажет, что доска отдала больше.
    """
    # Доски независимы, поэтому опрашиваются параллельно: последовательно двадцать
    # штук занимали 47 секунд и делали прогон самым долгим местом всего сбора.
    from .net import parallel  # локальный импорт, чтобы не тянуть пул без нужды

    results = parallel(
        {f"{kind}:{token}": (lambda k=kind, t=token: _ATS_IMPL[k](t))
         for kind, token in ATS_BOARDS},
        workers=10,
    )

    out: list[Vacancy] = []
    failed: list[str] = []
    tally = Tally("ats")
    seen: set[str] = set()
    for board, (ok, payload) in sorted(results.items()):
        if not ok:
            failed.append(f"{board} ({payload})")
            continue
        tally.pages += 1
        for v in payload:
            tally.offered += 1
            key = f"{v.source}:{v.external_id}"
            if v.external_id and key in seen:
                tally.dupes += 1
                continue
            seen.add(key)
            tally.parsed += 1
            if ctx.ats_all or ATS_ROLE_RE.search(v.title or ""):
                tally.kept += 1
                out.append(v)
            else:
                tally.skipped_profile += 1

    if failed and len(failed) == len(ATS_BOARDS):
        raise FetchError("ats", f"все доски упали: {'; '.join(failed[:3])}")

    # Итог прогона по доскам — служебная строка, чтобы счёт был виден в отчёте,
    # а не восстанавливался по памяти. В выдачу и в счётчики она НЕ идёт
    # (store.query её исключает): это метаданные прогона, а не вакансия.
    tally.note(f"досок опрошено {len(ATS_BOARDS) - len(failed)}/{len(ATS_BOARDS)}")
    if failed:
        # Упавшая доска — это не «у компании нет вакансий». Пишем поимённо:
        # молчаливый пропуск доски и есть та потеря, которую иначе не заметить.
        tally.note(f"НЕ ОТДАЛИСЬ доски: {'; '.join(failed[:5])}"
                   + (f" и ещё {len(failed) - 5}" if len(failed) > 5 else ""))
    row = tally.row()
    row.raw["failed"] = failed
    row.raw["boards"] = [f"{k}:{t}" for k, t in ATS_BOARDS]
    out.append(row)
    return out


# Примечание к строке покрытия: у ATS окно свежести не применяется вовсе.
# Без него «collect --days 3 → ats 1026» читается как «1026 свежих за 3 дня»,
# хотя среди них есть вакансии 2023 года — доска отдаётся целиком, у неё нет
# ни фильтра по дате, ни, у половины досок, самой даты публикации.
ATS_DAYS_NOTE = "--days не применяется: доска отдаётся целиком"


# ──────────────────────────────────────────────────────────────────────────────
# Анонимные JSON-агрегаторы: Himalayas, Arbeitnow, Jobicy
# ──────────────────────────────────────────────────────────────────────────────
#
# Все трое отдают JSON без ключа, без регистрации и без кук — ровно то, что можно
# крутить облачной рутиной, где чужих сессий нет и быть не должно.
#
# Remotive сюда СОЗНАТЕЛЬНО не добавлен: его ToS разрешает 4 запроса в сутки
# и запрещает перераспространение выдачи. Формально доступный источник, который
# нельзя использовать так, как нам нужно, — это не источник.

_PERIOD_RU = {"annual": "за год", "yearly": "за год", "year": "за год",
              "monthly": "в месяц", "month": "в месяц",
              "hourly": "в час", "hour": "в час",
              "weekly": "в неделю", "daily": "в день"}


def _salary_with_period(lo, hi, currency, period) -> tuple:
    """Вилка с периодом → (from, to, currency, period, строка-пояснение или None).

    Пока периода в модели не было, единственным способом не соврать было выбросить
    почасовую вилку вовсе: «80–120 USD» в колонке «деньги» рядом с годовыми
    «168 000–333 500 USD» читается как одна и та же зарплата (у Himalayas почасовых
    8% — замер по 100 вакансиям: annual 89, hourly 8, monthly 3). Теперь период
    хранится и печатается («80–120 USD/час»), поэтому час, месяц и год ложатся
    в поля вилки честно и целиком.

    Периоды, для которых подписи нет (неделя, день, пустое поле), по-прежнему
    в поля НЕ идут: вилка без суффикса означает «период неизвестен», и класть
    туда недельную ставку значит вернуть ту же ложь другим путём.
    """
    lo = int(lo) if isinstance(lo, (int, float)) and lo > 0 else None
    hi = int(hi) if isinstance(hi, (int, float)) and hi > 0 else None
    if lo is None and hi is None:
        return None, None, None, None, None
    p = str(period or "").strip().lower()
    canon = norm_period(p)
    if canon:
        return lo, hi, currency, canon, None
    human = _PERIOD_RU.get(p) or (p or "период не указан")
    return None, None, None, None, (
        f"Оплата {human}: {salary_str(lo, hi, currency)}. В вилку не переношу — "
        f"для этого периода нет честной подписи (поддержаны час, месяц, год).")




HIMALAYAS_PAGE = 20     # серверный потолок: limit=200 всё равно отдаёт 20


def src_himalayas(ctx: Ctx) -> list[Vacancy]:
    """himalayas.app — ~97 тысяч удалённых вакансий, анонимный JSON, без ключа.

    Серверного поиска у API НЕТ: limit/offset работают, а search / query / q /
    category молча игнорируются — проверено живьём, выдача и totalCount при них
    не меняются вовсе. Поэтому страницы забираются подряд, а заведомо чужие
    профессии отсекаются по названию роли тем же ATS_ROLE_RE, что и на ATS-досках.
    Это не «отбор по релевантности» (он остаётся за моделью), а то же самое, что
    hh делает параметром text= на своей стороне.

    Окно свежести API тоже не поддерживает: приезжает лента с начала, --days к ней
    неприменим — об этом пишет строка покрытия.
    """
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("himalayas")
    # Потолок в страницах, а не «пока не кончится»: 97 тысяч вакансий по 20 штук —
    # это 4873 запроса к чужому бесплатному API.
    pages = max(1, min(ctx.limit, 400) // HIMALAYAS_PAGE)
    for page in range(pages):
        if page:
            _pause()
        data = fetch_json(f"https://himalayas.app/jobs/api"
                          f"?limit={HIMALAYAS_PAGE}&offset={page * HIMALAYAS_PAGE}")
        tally.requests += 1
        jobs = data.get("jobs") or []
        if not jobs:
            break
        tally.pages += 1
        for j in jobs:
            tally.offered += 1
            guid = str(j.get("guid") or j.get("applicationLink") or "")
            if not guid:
                tally.dropped += 1
                continue
            if guid in seen:
                tally.dupes += 1
                continue
            seen.add(guid)
            tally.parsed += 1
            title = j.get("title") or ""
            if not (ctx.ats_all or ATS_ROLE_RE.search(title)):
                tally.skipped_profile += 1
                continue
            tally.kept += 1
            sf, st, cur, period, money_note = _salary_with_period(
                j.get("minSalary"), j.get("maxSalary"),
                j.get("currency"), j.get("salaryPeriod"))
            locs = [str(x) for x in (j.get("locationRestrictions") or []) if x]
            excerpt = _strip_tags(j.get("excerpt") or "")
            out.append(Vacancy(
                source="himalayas",
                # id вакансии API не отдаёт — берём хвост её постоянного URL.
                external_id=guid.rstrip("/").rsplit("/", 1)[-1],
                url=guid,
                title=title,
                company=j.get("companyName"),
                salary_from=sf, salary_to=st, currency=cur, salary_period=period,
                # Площадка целиком про удалёнку; locationRestrictions — это
                # «откуда можно работать», а не офис.
                location=", ".join(locs) or None,
                remote=True,
                published_at=j.get("pubDate"),
                tags=[str(x) for x in (j.get("seniority") or []) if x],
                description=" ".join(x for x in (money_note, excerpt) if x) or None,
                raw={"employmentType": j.get("employmentType"),
                     "salaryPeriod": j.get("salaryPeriod"),
                     "locationRestrictions": locs,
                     "timezoneRestrictions": j.get("timezoneRestrictions"),
                     "categories": (j.get("categories") or [])[:12],
                     "companySlug": j.get("companySlug"),
                     "expiryDate": j.get("expiryDate")},
            ))
        if (page + 1) * HIMALAYAS_PAGE >= (data.get("totalCount") or 0):
            break
    if not tally.parsed:
        raise FetchError("himalayas", "API ответил, но вакансий ноль — "
                                      "проверь формат ответа, парсер мог отстать")
    tally.note("серверного поиска нет, отбор по названию роли на нашей стороне")
    out.append(tally.row())
    return out


HIMALAYAS_DAYS_NOTE = "--days не применяется: у API нет окна по дате"


def src_arbeitnow(ctx: Ctx) -> list[Vacancy]:
    """arbeitnow.com — доска с уклоном в Германию и ЕС, 175 вакансий на странице.

    Пагинация курсорная: следующая страница берётся из links.next, а не считается
    руками. Параметр search сервером фактически игнорируется (проверено: с ним
    и без него доля описаний со словом Go — 39 и 35 из 175), поэтому чужие
    профессии отсекаются по названию роли у нас.

    Вилок API не отдаёт вовсе — это честный ноль, а не потеря парсера.
    """
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("arbeitnow")
    url = "https://www.arbeitnow.com/api/job-board-api"
    # Тот же потолок в страницах: доска бесконечная, а прогон должен кончаться.
    for page in range(max(1, min(ctx.limit, 400) // 100)):
        if page:
            _pause()
        data = fetch_json(url)
        tally.requests += 1
        rows = data.get("data") or []
        if not rows:
            break
        tally.pages += 1
        for j in rows:
            tally.offered += 1
            slug = str(j.get("slug") or "")
            if not slug:
                tally.dropped += 1
                continue
            if slug in seen:
                tally.dupes += 1
                continue
            seen.add(slug)
            tally.parsed += 1
            title = j.get("title") or ""
            if not (ctx.ats_all or ATS_ROLE_RE.search(title)):
                tally.skipped_profile += 1
                continue
            tally.kept += 1
            out.append(Vacancy(
                source="arbeitnow",
                external_id=slug,
                url=j.get("url") or f"https://www.arbeitnow.com/jobs/{slug}",
                title=title,
                company=j.get("company_name"),
                location=j.get("location"),
                remote=bool(j.get("remote")),
                published_at=j.get("created_at"),
                tags=[str(x) for x in (j.get("tags") or []) if x],
                description=_strip_tags(H.unescape(j.get("description") or ""))[:1000] or None,
                raw={"job_types": j.get("job_types"),
                     "note": "вилку API не отдаёт вовсе"},
            ))
        nxt = (data.get("links") or {}).get("next")
        if not nxt:
            break
        url = nxt
    if not tally.parsed:
        raise FetchError("arbeitnow", "API ответил, но вакансий ноль — "
                                      "проверь формат ответа, парсер мог отстать")
    tally.note("серверного поиска нет, отбор по названию роли на нашей стороне; "
               "вилок у API нет")
    out.append(tally.row())
    return out


ARBEITNOW_DAYS_NOTE = "--days не применяется: у API нет окна по дате"

# Тег ищется сервером и должен быть длиной от трёх символов: `tag=go` отдаёт
# HTTP 400, `tag=golang` — 29 вакансий. Короткие формулировки пропускаем, иначе
# один короткий синоним в --also роняет весь источник.
JOBICY_MIN_TAG = 3
JOBICY_COUNT = 50


def src_jobicy(ctx: Ctx) -> list[Vacancy]:
    """jobicy.com — только удалёнка, зато с НАСТОЯЩИМ серверным фильтром по тегу.

    Единственный из трёх, где отбор по нашему профилю делается на стороне API:
    `?tag=golang` возвращает 29 вакансий из всей базы. Поэтому здесь ATS_ROLE_RE
    не применяется — фильтр уже отработал в запросе.

    ToS площадки требует, чтобы отклик вёл на исходный URL из фида, — он и лежит
    в url, никаких промежуточных ссылок мы не строим.
    """
    out: list[Vacancy] = []
    seen: set[str] = set()
    used: list[str] = []
    tally = Tally("jobicy")
    for q in ctx.queries():
        tag = q.strip().lower().replace(" ", "-")
        if len(tag) < JOBICY_MIN_TAG:
            tally.note(f"«{q}» короче {JOBICY_MIN_TAG} символов — API такой тег не принимает")
            continue
        used.append(tag)
        if tally.requests:
            _pause()
        try:
            data = fetch_json(qs("https://jobicy.com/api/v2/remote-jobs",
                                 {"count": JOBICY_COUNT, "tag": tag}))
            tally.requests += 1
        except FetchError as e:
            # 400 — «такого тега площадка не знает». Это не поломка источника
            # и не повод ронять прогон из-за одной формулировки в --also.
            if e.status == 400:
                tally.note(f"тег «{tag}» площадке неизвестен (HTTP 400)")
                continue
            raise
        tally.pages += 1
        for j in (data.get("jobs") or []):
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
            tally.kept += 1
            sf, st, cur, period, money_note = _salary_with_period(
                j.get("salaryMin"), j.get("salaryMax"),
                j.get("salaryCurrency"), j.get("salaryPeriod"))
            excerpt = _strip_tags(H.unescape(j.get("jobExcerpt") or ""))
            out.append(Vacancy(
                source="jobicy",
                external_id=jid,
                url=j.get("url") or "",
                title=j.get("jobTitle") or "",
                company=j.get("companyName"),
                salary_from=sf, salary_to=st, currency=cur, salary_period=period,
                location=j.get("jobGeo"),
                remote=True,          # площадка только про удалёнку
                published_at=j.get("pubDate"),
                tags=[x for x in [j.get("jobLevel")] if x]
                     + [str(x) for x in (j.get("jobType") or []) if x],
                description=" ".join(x for x in (money_note, excerpt) if x) or None,
                raw={"industry": j.get("jobIndustry"), "level": j.get("jobLevel"),
                     "salaryPeriod": j.get("salaryPeriod"), "tag": tag,
                     "attribution": "ToS Jobicy: отклик ведёт на исходный URL фида"},
            ))
    if not used:
        raise FetchError("jobicy", f"ни одна формулировка запроса не длиннее "
                                   f"{JOBICY_MIN_TAG} символов — тег короче API не принимает")
    tally.note("фильтр по профилю серверный (tag=), ATS_ROLE_RE не применяется")
    out.append(tally.row())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Сырьевые источники: скрипт только приносит текст, разбирает модель
# ──────────────────────────────────────────────────────────────────────────────

# `auth`   — площадка, чьи куки подставить, если вход уже сделан;
# `expect` — по чему видно, что в дампе ЕСТЬ вакансии, а не каркас SPA;
# `render` — страница строится скриптами, stdlib привезёт пустышку;
# `parser` — имя адаптера в SOURCES, который теперь разбирает эту площадку сам.
#
# ВАЖНО, ЧТО ЗДЕСЬ ПОМЕНЯЛОСЬ: у всех семи площадок появился полноценный парсер
# (см. `sources_web` и `sources_auth`), поэтому `raw` перестал быть способом их
# собирать и остался ОТЛАДОЧНЫМ путём: посмотреть глазами, что именно отдала
# площадка, когда парсер принёс не то. Пустой `parser` означал бы «данные добывают
# отсюда» — таких строк больше нет, и в покрытии прогона эти площадки идут
# обычными источниками, а не сноской «разбирать глазами».
RAW_SOURCES: dict[str, dict] = {
    # У getmatch есть открытый JSON API — он и берётся. HTML-страница отдаёт
    # Next.js-каркас: 185 КБ, в которых ноль вакансий и ноль ссылок на них,
    # и «дамп снят» выглядело успехом. Фильтр по запросу у API серверный не
    # работает, поэтому берём страницу целиком и режем у себя.
    "getmatch":   {"url": "https://getmatch.ru/api/offers?sp=all&limit=200",
                   "expect": r'"position"', "parser": "getmatch",
                   "note": "JSON API; параметр поиска сервером игнорируется — "
                           "фильтруй по полю position сам"},
    "geekjob":    {"url": "https://geekjob.ru/vacancies?qs={q}", "auth": "geekjob",
                   "expect": r"/vacancy/[a-f0-9]{6,}", "render": True,
                   "parser": "geekjob"},
    "hirehi":     {"url": "https://hirehi.ru/vacancies/go,backend", "auth": "hirehi",
                   "expect": r"₽|\$\s?\d", "render": True, "parser": "hirehi",
                   "note": "выдача целиком анонимна через API; вход добавляет "
                           "только счётчик раскрытий прямого контакта"},
    "hackoffer":  {"url": "https://hack-offer.tech/job/g/dev",
                   "expect": r"vacancy/\d+", "parser": "hackoffer"},
    "rabota":     {"url": "https://www.rabota.ru/vacancy/?query={q}",
                   "expect": r"vacancy/\d+", "parser": "rabota"},
    "shadowhint": {"url": "https://shadowhint.com/profile/tg-vacancies?search={q}",
                   "auth": "shadowhint", "render": True, "parser": "shadowhint",
                   "note": "без входа 401 и у парсера, и у дампа — сначала "
                           "`scout auth login shadowhint`"},
    "wantapply":  {"url": "https://wantapply.com/?search={q}", "auth": "wantapply",
                   "parser": "wantapply",
                   "note": "сам wantapply.com под Cloudflare; каталог парсер берёт "
                           "с api.wantapply.com, стену мы не трогаем"},
}


class EmptyDumpError(FetchError):
    """В дампе нет ни одной вакансии — это каркас SPA, а не выдача.

    Отдельный класс, потому что exit 0 с 185 КБ пустого каркаса — худший исход
    из возможных: вызывающий уверен, что данные есть, и молча теряет площадку.
    У HTML-адаптеров такой порог правдоподобия был с самого начала, у `raw` — нет.
    """


def raw_dump(name: str, ctx: Ctx, *, use_render: bool = False) -> tuple[str, str]:
    """Отдаёт (текст страницы, URL) для источников, у которых нет надёжного парсера.

    Это сознательный компромисс: у geekjob Vue-SPA, у hirehi Supabase-SPA; писать
    под них парсер значит писать то, что сломается молча. Скрипт снимает с модели
    механику (сходить, дождаться, распаковать), суждение оставляет ей.

    Дамп проверяется на правдоподобие: нет ни одной карточки — EmptyDumpError
    с подсказкой про `--render`, а не «успешно сохранено N символов».
    """
    cfg = RAW_SOURCES[name]
    url = cfg["url"].format(q=ctx.query)
    if use_render:
        from .render import render_page  # noqa: PLC0415 — Playwright опционален
        text, final = render_page(url)
    else:
        cookies = None
        if cfg.get("auth"):
            from .auth import cookie_header  # noqa: PLC0415 — без Playwright тоже работает
            cookies = cookie_header(cfg["auth"])
        text, final = fetch(url, cookies=cookies)

    expect = cfg.get("expect")
    if expect and not re.search(expect, text, re.I):
        hint = ("страница строится скриптами — возьми `scout raw --render` "
                "или `scout render`" if cfg.get("render") and not use_render
                else "площадка отдала каркас без данных; проверь глазами")
        raise EmptyDumpError(
            final, f"в дампе ({len(text)} символов) нет ни одной карточки вакансии — {hint}")
    return text, final


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

_BASE_SOURCES = {
    "hh": src_hh,
    "habr": src_habr,
    "careered": src_careered,
    "linkedin": src_linkedin,
    "ats": src_ats,
    # Анонимные JSON-агрегаторы: без ключа, без регистрации, без кук.
    "himalayas": src_himalayas,
    "arbeitnow": src_arbeitnow,
    "jobicy": src_jobicy,
}

# Примечания к строке покрытия: у части источников окно свежести не применяется,
# и «collect --days 3 → himalayas 84» иначе читается как «84 свежих за три дня».
_BASE_NOTES = {
    "ats": ATS_DAYS_NOTE,
    "himalayas": HIMALAYAS_DAYS_NOTE,
    "arbeitnow": ARBEITNOW_DAYS_NOTE,
}


# ── Домешивание адаптеров из sources_web и sources_auth ───────────────────────
#
# Импорт отложенный, и это не украшательство: `sources_web` и `sources_auth`
# импортируют ИЗ ЭТОГО модуля (Ctx, parse_salary, ATS_ROLE_RE). Обычный импорт
# наверху или внизу файла даёт цикл, который ломается ровно в одну сторону —
# когда первым импортируют не `sources`, а `sources_web`, — и проявляется
# ImportError'ом в тестах, а не в момент написания.
#
# Поэтому реестр — словарь, который дозаполняется при первом же обращении
# (`name in SOURCES`, `list(SOURCES)`, `SOURCES[name]`). К этому моменту оба
# модуля импортируются нормально, в любом порядке. Ошибку импорта НЕ глотаем:
# «источник тихо исчез из реестра» — это ровно та потеря площадки целиком,
# от которой весь сборщик и защищается.
_extra_loaded = False


def _load_extra() -> None:
    global _extra_loaded
    if _extra_loaded:
        return
    _extra_loaded = True  # ставим ДО импорта: повторного захода не будет
    from . import sources_auth, sources_web  # noqa: PLC0415

    dict.update(SOURCES, sources_web.WEB_SOURCES)
    dict.update(SOURCES, sources_web.WEB_REFERENCE)
    dict.update(SOURCES, sources_auth.SOURCES_AUTH)
    dict.update(SOURCE_NOTES, sources_web.WEB_SOURCE_NOTES)
    dict.update(SOURCE_NOTES, sources_auth.SOURCE_NOTES_AUTH)
    dict.update(NEEDS_BROWSER_SET, sources_web.WEB_NEEDS_BROWSER_MAP)


class _Registry(dict):
    """Словарь реестра, дозаполняемый при первом чтении.

    Читающие методы перекрыты все до одного: пропустить `__contains__` значит
    получить «неизвестный источник hirehi» в `collect --sources hirehi`,
    пропустить `__iter__` — тихо потерять половину площадок в обходе по умолчанию.
    """

    def _fill(self) -> None:
        _load_extra()

    def __getitem__(self, key):
        self._fill()
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._fill()
        return dict.__contains__(self, key)

    def __iter__(self):
        self._fill()
        return dict.__iter__(self)

    def __len__(self):
        self._fill()
        return dict.__len__(self)

    def get(self, key, default=None):
        self._fill()
        return dict.get(self, key, default)

    def keys(self):
        self._fill()
        return dict.keys(self)

    def values(self):
        self._fill()
        return dict.values(self)

    def items(self):
        self._fill()
        return dict.items(self)

    def __repr__(self):
        self._fill()
        return dict.__repr__(self)


SOURCES: dict = _Registry(_BASE_SOURCES)
SOURCE_NOTES: dict = _Registry(_BASE_NOTES)

# Источники, которым нужен настоящий браузер (Playwright + профиль пользователя):
# {имя: за чем именно браузер}. Гонять их вместе с остальными в общем пуле нельзя —
# профиль браузера один и он под локом, второй параллельный заход получит
# ProfileBusy и попадёт в отчёт как «УПАЛ», хотя не упал никто.
NEEDS_BROWSER_SET: dict = _Registry({})

# Источники, требующие входа пользователя. Сборщик их не трогает: логинится
# только человек, а куки не выгружаются никуда (см. «Границы» в SKILL.md).
#
# Список короткий не потому, что площадок мало, а потому, что вход просим ТОЛЬКО
# там, где он что-то меняет: сверено по цифрам, что geekjob анонимно отдаёт
# столько же, а hirehi — вообще всё. Лишняя строка здесь обесценивает весь список.
NEEDS_LOGIN = {
    "shadowhint": "https://shadowhint.com/profile/tg-vacancies",
    "wantapply": "https://wantapply.com/?search=Go",
    "hh-negotiations": "https://hh.ru/applicant/negotiations",
    "habr-applications": "https://career.habr.com/",
}

# Что именно даёт вход — чтобы «требует входа» не читалось как «без входа ноль».
LOGIN_VALUE = {
    "shadowhint": "ВСЮ выдачу: анонимно площадка отдаёт 401",
    "wantapply": "только прямую ссылку в ATS работодателя; каталог берётся и без входа",
    "hh-negotiations": "статусы твоих откликов (сами вакансии hh отдаёт анонимно)",
    "habr-applications": "статусы твоих откликов на Хабр Карьере",
}
