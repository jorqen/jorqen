"""levels.fyi — СПРАВОЧНИК ЗАРПЛАТ, а не источник вакансий.

Вынесен из `sources_web.py` 07.08.2026 переездом БЕЗ изменения поведения.
Разделение не косметическое и повторяет то, что уже сказано в реестре: всё,
что лежит в WEB_SOURCES, обязано отдавать вакансии, а медиана рынка — не
вакансия, и однажды подставленная в карточку как зарплата она станет ложью
о конкретном предложении.
"""

from __future__ import annotations

import re

from .model import Vacancy
from .net import FetchError, fetch
from .sources import Ctx
from .webcommon import check_wall

# ──────────────────────────────────────────────────────────────────────────────
# levels.fyi — СПРАВОЧНИК ЗАРПЛАТ, не вакансии
# ──────────────────────────────────────────────────────────────────────────────

LEVELS_TITLES = {
    "backend": "software-engineer/title/backend-software-engineer",
    "software-engineer": "software-engineer",
    "sre": "software-engineer/title/site-reliability-engineer",
    "devops": "software-engineer/title/devops-engineer",
    "engineering-manager": "engineering-manager",
    # Локационные срезы живут на тех же маршрутах и отдают ЕВРО, а не доллары —
    # отсюда нормализация валюты в разборе.
    "germany": "software-engineer/locations/germany",
}

# Лицензия площадки требует атрибуции в любой производной работе. Строка уходит
# в raw КАЖДОЙ записи, а не печатается один раз в лог прогона: сводка потом живёт
# в базе и в карточке отдельно от лога, и там цифра без источника читается как наша.
LEVELS_ATTRIBUTION = "Data source: Levels.fyi (https://www.levels.fyi)"

# Что мы ПОТЕРЯЛИ, уйдя с `__NEXT_DATA__` на `.md`. Держим строкой и тащим
# в raw и в пометку источника: молчаливая потеря поля — ровно та болезнь,
# от которой этот источник и слёг (см. levels_benchmark).
LEVELS_LOST = ("в .md нет p10/p99, разбивки base/bonus/stock и размера выборки — "
               "есть только медиана, p25/p75, p90 и дата обновления")

# Символ валюты → код. Площадка ставит и символ («$194,000»), и код в шапке
# («**Currency:** EUR (€)»); код надёжнее, символ — запасной путь.
_LEVELS_CUR = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY",
               "₽": "RUB", "₺": "TRY", "₩": "KRW", "₪": "ILS", "R$": "BRL"}

_LEVELS_MONEY_RE = re.compile(
    r"(?P<sym>R\$|[$€£₹¥₽₺₩₪])?\s*(?P<num>\d[\d\s ,.]*\d|\d)\s*(?P<mult>[KkMm])?")


def _levels_money(text: str | None) -> tuple[int | None, str | None]:
    """«$194,000» → (194000, "USD"), «€82 546» → (82546, "EUR").

    Разделитель тысяч зависит от локали, которую площадка подставляет сама:
    точка в «68.194» — это разделитель тысяч, а не копейки, и прочитать её
    дробной значит ошибиться в тысячу раз. Отсюда правило: точка ровно
    с тремя цифрами после неё — разделитель, всё прочее — дробная часть.
    Суффикс K/M разворачивается: «$194K» в шапках FAQ встречается.
    """
    if not text:
        return None, None
    m = _LEVELS_MONEY_RE.search(text)
    if not m:
        return None, None
    num = re.sub(r"[\s ,]", "", m.group("num"))
    if re.fullmatch(r"\d+(?:\.\d{3})+", num):
        num = num.replace(".", "")
    try:
        value = float(num)
    except ValueError:
        return None, None
    value *= {"k": 1_000, "m": 1_000_000}.get((m.group("mult") or "").lower(), 1)
    return int(round(value)), _LEVELS_CUR.get(m.group("sym") or "")


def _levels_section(text: str, title: str) -> str:
    """Тело раздела по его заголовку (## или ###) до следующего заголовка.

    Разбор идёт по заголовкам, а не по номерам строк: между «Aggregate Highlights»
    и таблицами площадка уже вставляет разное (у роли нет «Key Breakdowns», у страны
    есть ещё «Top Paying Titles»), и жёсткие индексы сломались бы на первом же срезе.
    Разделы ищутся по всему тексту, а не внутри родительского: вложенность в
    markdown задаётся только числом решёток, и «раздел до следующей решётки»
    у `## Key Breakdowns` дал бы пустоту — сразу за ним идёт `### Top Paying …`.
    """
    m = re.search(rf"^(#{{2,3}})\s*{re.escape(title)}\s*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end():]
    # Конец раздела — заголовок ТОГО ЖЕ или более высокого уровня.
    nxt = re.search(rf"^#{{1,{len(m.group(1))}}}\s+\S", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def _levels_bullets(body: str) -> dict[str, str]:
    """«- Median Total Compensation: $194,000» → {"median total compensation": "$194,000"}."""
    out = {}
    for line in body.splitlines():
        m = re.match(r"\s*[-*]\s*(.+?)\s*:\s*(.+?)\s*$", line)
        if m:
            out[m.group(1).strip().lower()] = m.group(2).strip()
    return out


def _levels_pick(bullets: dict[str, str], *keys: str) -> str | None:
    """Значение по ПОДСТРОКЕ метки. Точное совпадение здесь хрупко: «25th / 75th
    Percentile» площадка уже переименовывала, а «25th» в метке остаётся."""
    for key in keys:
        for label, value in bullets.items():
            if key in label:
                return value
    return None


def _levels_table(body: str) -> list[dict]:
    """Строки markdown-таблицы «| 1 | Anthropic | $870,000 |» в список словарей.

    Шапка и разделитель отсеиваются по первой ячейке: у настоящей строки там ранг.
    """
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        median, cur = _levels_money(cells[2])
        rows.append({"rank": int(cells[0]), "name": cells[1],
                     "median_total": median, "currency": cur})
    return rows


def parse_levels_md(text: str, url: str) -> dict:
    """Разбор `.md`-версии страницы зарплат. Чистая функция — сеть не трогает.

    Отдельно от `levels_benchmark`, чтобы проверяться на сохранённой выдаче:
    источник уже один раз сломался молча, и тест обязан ловить смену разметки
    без похода в сеть.
    """
    head = dict(re.findall(r"^\*\*(.+?):\*\*\s*(.+?)\s*$", text, re.M))
    highlights = _levels_bullets(_levels_section(text, "Aggregate Highlights"))
    median, sym_cur = _levels_money(_levels_pick(highlights, "median total"))
    if median is None:
        raise FetchError(url, "в .md нет медианы — разметка сменилась или роли нет")

    p25, p75 = (None, None)
    quartiles = _levels_pick(highlights, "25th") or ""
    if "/" in quartiles:
        left, right = quartiles.split("/", 1)
        p25, _ = _levels_money(left)
        p75, _ = _levels_money(right)

    # Код валюты из шапки («EUR (€)») надёжнее символа: у страновых срезов
    # символ у чисел тот же €, а у $-стран площадка пишет и A$, и C$.
    cur_code = re.match(r"([A-Z]{3})", (head.get("Currency") or "").strip())
    # «# Levels.fyi – Backend Software Engineer Salary in Germany» → роль отдельно,
    # страна отдельно (она уже лежит в **Location:**). Хвост «Salary…» английский
    # даже под ru-локалью, где переведено само название роли.
    title = re.search(r"^#\s*Levels\.fyi\s*[–—-]\s*(.+?)\s*$", text, re.M)
    job_title = (re.sub(r"\s+salary(?:\s+in\s+.+)?$", "", title.group(1), flags=re.I)
                 if title else None)
    return {
        "source": "levels.fyi",
        "url": head.get("URL") or url,
        "fetched_url": url,
        "job_title": job_title,
        "location": head.get("Location"),
        "scope": head.get("Scope"),
        "currency": (cur_code.group(1) if cur_code else sym_cur) or "USD",
        "median_total": median,
        "p25": p25,
        "p75": p75,
        "p90": _levels_money(_levels_pick(highlights, "90th"))[0],
        "updated": _levels_pick(highlights, "last updated"),
        "generated": head.get("Generated"),
        "top_companies": _levels_table(_levels_section(text, "Top Paying Companies")),
        "top_locations": _levels_table(_levels_section(text, "Top Paying Locations")),
        "top_titles": _levels_table(_levels_section(text, "Top Paying Titles")),
        # Период везде годовой: levels.fyi считает total comp за год.
        "period": "year",
        # Размер выборки площадка больше не отдаёт. Ключ оставлен явным None,
        # а не выброшен: «поля нет» должно быть видно в raw, иначе медиана без
        # выборки читается как медиана с выборкой, которую забыли показать.
        "sample_size": None,
        "attribution": LEVELS_ATTRIBUTION,
        "lost": LEVELS_LOST,
        "note": "справочник рынка, не вакансия; суммы годовые. Страну площадка "
                "подставляет сама — сверяй location, прежде чем переносить цифру "
                "в карточку. " + LEVELS_LOST,
    }


def levels_benchmark(role: str = "backend") -> dict:
    """Бенчмарк зарплат по роли: медиана полной компенсации за год, p25/p75, p90.

    Это НЕ источник вакансий и в `WEB_SOURCES` не входит — функция отдаёт словарь,
    а не список Vacancy. Нужна ровно для одного: когда в вакансии вилки нет,
    подпереть колонку «деньги» цифрой рынка вместо догадки.

    ПОЧЕМУ РАЗБОР ДРУГОЙ. Раньше читался `__NEXT_DATA__`, поле
    `pageProps.serverJobTitlePercentiles`. Площадка это поле убрала (в стейте
    остались `jobFamily`, `jobFamilies`, `defaultCountryMedian`, `companiesWithLevels`),
    и источник лежал НЕ из-за стены: он падал бы и с браузером, на разборе. Урок
    ровно в этом — проверять надо не стену, а поле: «упал» и «сменилась разметка»
    выглядят одинаково ровно до того момента, когда посмотришь в ответ.

    Теперь берётся `.md`-версия страницы: к маршруту дописывается `.md`, и площадка
    отдаёт готовый markdown обычному stdlib-GET, 200 и text/markdown. Браузер
    не нужен вовсе — Playwright из этого источника ушёл.

    Платой за переход стали p10/p99, разбивка base/bonus/stock и размер выборки:
    в `.md` их нет (см. `LEVELS_LOST`), и потеря названа в пометке источника,
    а не спрятана. Лицензия требует атрибуции — `LEVELS_ATTRIBUTION` едет в raw.
    """
    path = LEVELS_TITLES.get(role, role)
    url = f"https://www.levels.fyi/t/{path}.md"
    # Явный английский: на нашем стандартном ru-заголовке площадка редиректит
    # на /ru-ru/… и переводит НАЗВАНИЕ роли («Программный инженер»), оставляя
    # метки английскими. Разбору это не мешает, а вот сводка меняла бы название
    # роли от прогона к прогону — и «медиана по backend» перестала бы склеиваться.
    text, final = fetch(url, headers={"Accept-Language": "en-US,en;q=0.9"})
    check_wall(text, final)
    data = parse_levels_md(text, final)
    data["role"] = role
    return data


LEVELS_NOTE = ("не вакансии, а медиана рынка за год: медиана, p25/p75, p90 и дата "
               "обновления с .md-версии страницы (браузер не нужен). Поэтому "
               "«найдено 0» — это норма, цифры лежат в сводке. " + LEVELS_LOST)


def src_levels(ctx: Ctx) -> list[Vacancy]:
    """Обёртка над `levels_benchmark` для общего обхода — БЕЗ вакансий.

    Нужна ровно затем, чтобы levels.fyi не выпадал из таблицы покрытия молча.
    Площадка вакансий не отдаёт вовсе, поэтому обёртка возвращает единственную
    строку-сводку с пустым url: `store.query` такие режет, в базу и в счётчик
    «найдено» справочник не попадает. Требование «в покрытии видны ВСЕ площадки»
    и требование «в базе только вакансии» здесь не конфликтуют.

    Размера выборки в строке больше нет — площадка его не отдаёт (`LEVELS_LOST`).
    Писать вместо него «выборка None» значит показать пустоту цифрой.
    """
    data = levels_benchmark("backend")
    money = data.get("median_total")
    cur = data.get("currency") or "USD"
    head = (f"[сводка levels] медиана {money:,} {cur}/год".replace(",", " ")
            if isinstance(money, int) else "[сводка levels] медианы нет")
    return [Vacancy(
        source="levels", external_id="_summary", url="",
        title=(f"{head} по роли {data.get('job_title') or 'backend'}; "
               f"p25 {data.get('p25')}, p75 {data.get('p75')}, p90 {data.get('p90')}, "
               f"обновлено {data.get('updated') or '?'}. {LEVELS_NOTE}"),
        raw=data,
    )]
