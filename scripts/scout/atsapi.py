"""Единый доступ к API ATS-досок: Greenhouse, Lever, Ashby, Recruitee, Workable,
SmartRecruiters, BambooHR.

Сюда портированы бывшие scripts/ats/*.sh (check, tr2, fin, sniff) — но в виде одного
слоя данных, которым пользуются сразу три команды: `scout ats`, `scout detail`
и `scout check-links`. Знание о ловушках токенов (greenhouse `insider` — это Business
Insider, SmartRecruiters отдаёт 200 и ноль для несуществующих компаний) живёт
в `.claude/skills/jobs/references/sources-setup.md`; здесь — только механика.

Главное правило то же, что во всём сборщике: **структурный матч локаций**. Фильтр
по одному полю `location` теряет вакансии — Ashby прячет вторую страну
в `secondaryLocations[]`, Greenhouse — в `offices[]` или прямо в заголовке.
Поэтому `BoardJob.locations` собирает ВСЕ поля, где может лежать страна.
"""

from __future__ import annotations

import html as H
import json
import re
import urllib.parse
from dataclasses import dataclass, field

from .net import FetchError, fetch, fetch_json

UA_NOTE = None  # UA задаёт net.py — здесь ничего не переопределяется

ATS_KINDS = ("greenhouse", "lever", "ashby", "recruitee", "workable",
             "smartrecruiters", "bamboohr")

# Синонимы, которыми пользователь называет ATS в командной строке.
ATS_ALIASES = {"gh": "greenhouse", "smart": "smartrecruiters", "sr": "smartrecruiters",
               "bamboo": "bamboohr"}


@dataclass
class BoardJob:
    """Вакансия с доски: ровно то, что нужно для матча и предфлайта, без описаний."""
    id: str
    title: str
    url: str
    # Все поля, где ATS может спрятать страну: основное + secondaryLocations[] (Ashby)
    # + offices[] (Greenhouse) + allLocations (Lever). Заголовок проверяется отдельно.
    locations: list[str] = field(default_factory=list)
    published_at: str | None = None


@dataclass
class Board:
    ats: str
    token: str
    company: str | None
    jobs: list[BoardJob]
    total: int              # сколько вакансий на доске всего (может быть > len(jobs))
    note: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Доски
# ──────────────────────────────────────────────────────────────────────────────

def _board_greenhouse(token: str, query: str | None = None) -> Board:
    # Корень доски отдаёт название компании — обязательная проверка против ловушки
    # «токен совпал, компания не та» (insider = Business Insider).
    meta = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}")
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false")
    jobs = []
    for j in d.get("jobs", []):
        locs = [(j.get("location") or {}).get("name") or ""]
        locs += [o.get("name", "") for o in (j.get("offices") or [])]
        jobs.append(BoardJob(
            id=str(j.get("id")), title=j.get("title") or "",
            url=j.get("absolute_url") or "",
            locations=[x for x in locs if x],
            published_at=j.get("updated_at") or j.get("first_published"),
        ))
    return Board("greenhouse", token, meta.get("name") or None, jobs, len(jobs))


def _board_lever(token: str, query: str | None = None) -> Board:
    d = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if isinstance(d, dict) and d.get("ok") is False:
        raise FetchError(f"lever:{token}", d.get("error") or "доски нет")
    jobs = []
    for j in (d if isinstance(d, list) else []):
        cats = j.get("categories") or {}
        locs = [cats.get("location") or "", j.get("country") or ""]
        locs += [str(x) for x in (cats.get("allLocations") or [])]
        jobs.append(BoardJob(
            id=str(j.get("id")), title=j.get("text") or "",
            url=j.get("hostedUrl") or j.get("applyUrl") or "",
            locations=[x for x in locs if x],
            published_at=str(j.get("createdAt") or "") or None,
        ))
    # Публичный API Lever не отдаёт название компании, а jobs.lever.co закрыт
    # Cloudflare-стеной. Честнее сказать это, чем печатать токен как имя.
    return Board("lever", token, None, jobs, len(jobs),
                 note="название компании API не отдаёт — сверь по hostedUrl")


def _board_ashby(token: str, query: str | None = None) -> Board:
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    jobs = []
    for j in d.get("jobs", []):
        locs = [j.get("location") or ""]
        locs += [s.get("location") or "" for s in (j.get("secondaryLocations") or [])
                 if isinstance(s, dict)]
        if isinstance(j.get("address"), dict):
            addr = ((j["address"].get("postalAddress") or {}) if j["address"] else {})
            locs += [addr.get("addressCountry") or "", addr.get("addressLocality") or ""]
        jobs.append(BoardJob(
            id=str(j.get("id")), title=j.get("title") or "",
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            locations=[x for x in locs if x],
            published_at=j.get("publishedAt"),
        ))
    name, note = d.get("name"), None
    if not name:
        # posting-api перестал отдавать name; заголовок HTML-страницы доски его ещё
        # содержит («Ruby Labs Jobs»). Страница за антибот-стеной — честный None
        # с пометкой, а не токен под видом названия.
        try:
            text, _ = fetch(f"https://jobs.ashbyhq.com/{urllib.parse.quote(token)}",
                            timeout=20, retries=0)
            m = re.search(r"<title>(.*?)</title>", text, re.S)
            if m:
                name = re.sub(r"\s*(Jobs|Careers)\s*$", "", H.unescape(m.group(1)).strip()) or None
        except FetchError:
            note = "posting-api название не отдаёт, HTML-страница доски за антибот-стеной"
    return Board("ashby", token, name, jobs, len(jobs), note=note)


def _board_recruitee(token: str, query: str | None = None) -> Board:
    d = fetch_json(f"https://{token}.recruitee.com/api/offers/")
    jobs, company = [], None
    for j in d.get("offers", []):
        company = company or j.get("company_name")
        locs = [j.get("location") or "", j.get("city") or "", j.get("country") or ""]
        jobs.append(BoardJob(
            id=str(j.get("slug") or j.get("id")), title=j.get("title") or "",
            url=j.get("careers_url") or "",
            locations=[x for x in locs if x],
            published_at=j.get("published_at") or j.get("created_at"),
        ))
    note = None if jobs else "доска отвечает, но вакансий ноль — название компании взять неоткуда"
    return Board("recruitee", token, company, jobs, len(jobs), note=note)


# Между ячейками — ТОЛЬКО горизонтальный пробел, и содержимое ячейки не может
# содержать ни `|`, ни перевод строки.
#
# Почему это важно: раньше разделителем стоял `\s*`, а `\s` матчит и перевод
# строки. Из-за этого первый же матч начинался на СТРОКЕ ЗАГОЛОВКА таблицы
# (`| Title | Department | Location | …`), проезжал сквозь разделитель
# `|-------|-----------|…` и подхватывал ссылку `[View](…)` уже из первой строки
# данных. Доска отдавала фантомную вакансию с title="Title" и чужим id, а
# настоящая первая вакансия пропадала — на каждой доске Workable ровно одна.
# С `[^|\n]` заголовок и разделитель не совпадают вовсе: в них нет ячейки [View].
_H = r"[^\S\n]*"   # горизонтальный пробел: пробел и таб, но не перевод строки
_CELL = r"[^|\n]*?"

_WORKABLE_ROW = re.compile(
    rf"^\|{_H}(?P<title>[^|\n]+?){_H}\|{_H}(?P<dept>{_CELL}){_H}\|{_H}(?P<loc>{_CELL}){_H}\|"
    rf"{_H}(?P<typ>{_CELL}){_H}\|{_H}(?P<salary>{_CELL}){_H}\|{_H}(?P<posted>{_CELL}){_H}\|"
    rf"{_H}\[View\]\((?P<url>https://apply\.workable\.com/[^)\n]+/jobs/view/"
    rf"(?P<sc>[A-Za-z0-9]+)\.md)\){_H}\|{_H}$",
    re.M)


def _board_workable(token: str, query: str | None = None) -> Board:
    # Виджетный API v1 больше не отдаёт вакансии (jobs всегда []), а v3 требует POST.
    # Рабочий GET-путь — markdown-выдача careers-страницы: /{token}/jobs.md.
    acc = fetch_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}")
    url = f"https://apply.workable.com/{urllib.parse.quote(token)}/jobs.md"
    if query:
        url += "?" + urllib.parse.urlencode({"query": query})
    text, _ = fetch(url)
    jobs = []
    for m in _WORKABLE_ROW.finditer(text):
        jobs.append(BoardJob(
            id=m.group("sc"), title=m.group("title"),
            url=f"https://apply.workable.com/{token}/j/{m.group('sc')}/",
            locations=[x for x in (m.group("loc"), m.group("dept")) if x and x != "—"],
            published_at=(m.group("posted") or "").strip() or None,
        ))
    total, note = len(jobs), None
    # Большая доска отдаёт не таблицу, а инструкцию поиска с общим счётчиком —
    # список получают только с ?query=. Молча вернуть ноль здесь нельзя.
    m = re.search(r"has (\d+) open positions", text)
    if m and not jobs:
        total = int(m.group(1))
        note = (f"на доске {total} вакансий, но workable отдаёт список только "
                f"по запросу — повтори с --grep <слово>")
    return Board("workable", token, acc.get("name") or None, jobs, total, note=note)


def _board_smartrecruiters(token: str, query: str | None = None) -> Board:
    jobs, company, offset, total = [], None, 0, None
    while True:
        d = fetch_json("https://api.smartrecruiters.com/v1/companies/"
                       f"{urllib.parse.quote(token)}/postings?limit=100&offset={offset}")
        total = d.get("totalFound") or 0
        for j in d.get("content", []):
            company = company or (j.get("company") or {}).get("name")
            loc = j.get("location") or {}
            locs = [loc.get("city") or "", loc.get("region") or "", loc.get("country") or ""]
            jobs.append(BoardJob(
                id=str(j.get("id")), title=j.get("name") or "",
                url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                locations=[x for x in locs if x],
                published_at=j.get("releasedDate"),
            ))
        offset += len(d.get("content", []))
        if offset >= total or not d.get("content"):
            break
    note = None
    if total == 0:
        # Проверено живьём: несуществующая компания получает HTTP 200 и totalFound: 0.
        note = "0 вакансий — у SmartRecruiters это неотличимо от «доски не существует»"
    return Board("smartrecruiters", token, company, jobs, total, note=note)


def _board_bamboohr(token: str, query: str | None = None) -> Board:
    d = fetch_json(f"https://{token}.bamboohr.com/careers/list")
    jobs = []
    for j in d.get("result", []):
        loc = j.get("location") or {}
        ats_loc = j.get("atsLocation") or {}
        locs = [loc.get("city") or "", loc.get("state") or "",
                ats_loc.get("country") or "", ats_loc.get("city") or ""]
        jobs.append(BoardJob(
            id=str(j.get("id")), title=j.get("jobOpeningName") or "",
            url=f"https://{token}.bamboohr.com/careers/{j.get('id')}",
            locations=[x for x in locs if x],
        ))
    return Board("bamboohr", token, None, jobs, len(jobs),
                 note="API списка не отдаёт название компании")


BOARD_IMPL = {
    "greenhouse": _board_greenhouse,
    "lever": _board_lever,
    "ashby": _board_ashby,
    "recruitee": _board_recruitee,
    "workable": _board_workable,
    "smartrecruiters": _board_smartrecruiters,
    "bamboohr": _board_bamboohr,
}


def board(ats: str, token: str, query: str | None = None) -> Board:
    """Опрашивает доску. Кидает FetchError/BlockedError — вызывающий обязан показать причину."""
    ats = ATS_ALIASES.get(ats, ats)
    if ats not in BOARD_IMPL:
        raise ValueError(f"неизвестный ATS {ats!r}; знаю: {', '.join(BOARD_IMPL)}")
    return BOARD_IMPL[ats](token, query)


# ──────────────────────────────────────────────────────────────────────────────
# Разбор URL вакансии → (ats, token, job_id)
# ──────────────────────────────────────────────────────────────────────────────

_URL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?P<token>[^/?#]+)/jobs/(?P<id>\d+)")),
    ("lever", re.compile(
        r"jobs\.(?:eu\.)?lever\.co/(?P<token>[^/?#]+)/(?P<id>[0-9a-fA-F-]{36})")),
    ("ashby", re.compile(
        r"jobs\.ashbyhq\.com/(?P<token>[^/?#]+)/(?P<id>[0-9a-fA-F-]{36})")),
    ("recruitee", re.compile(
        r"//(?P<token>[a-z0-9-]+)\.recruitee\.com/o/(?P<id>[^/?#]+)")),
    ("workable", re.compile(
        r"apply\.workable\.com/(?P<token>[^/?#]+)/j/(?P<id>[A-Za-z0-9]+)")),
    ("bamboohr", re.compile(
        r"//(?P<token>[a-z0-9-]+)\.bamboohr\.com/careers/(?P<id>\d+)")),
    ("smartrecruiters", re.compile(
        r"jobs\.smartrecruiters\.com/(?P<token>[^/?#]+)/(?P<id>\d+)")),
]

# Greenhouse-embed: адрес вида greenhouse.io/embed/job_app?for=<token>&token=<id>
_GH_EMBED = re.compile(r"greenhouse\.io/embed/job_app")


def parse_job_url(url: str) -> tuple[str, str, str] | None:
    """Распознаёт ссылку на вакансию в ATS. Не распознал — None, без догадок."""
    for ats, pat in _URL_PATTERNS:
        m = pat.search(url)
        if m:
            return ats, m.group("token"), m.group("id")
    if _GH_EMBED.search(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        token, job = (q.get("for") or [None])[0], (q.get("token") or [None])[0]
        if token and job:
            return "greenhouse", token, job
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Сниффер: на каком ATS сидит компания (порт sniff.sh/hosts.sh)
# ──────────────────────────────────────────────────────────────────────────────

SNIFF_RE = re.compile(
    r"boards\.greenhouse\.io/[a-z0-9_-]+|job-boards(?:\.eu)?\.greenhouse\.io/[a-z0-9_-]+|"
    r"greenhouse\.io/embed/job_board[^\"'\s]*|"
    r"jobs\.(?:eu\.)?lever\.co/[a-z0-9_-]+|jobs\.ashbyhq\.com/[a-z0-9_.-]+|"
    r"[a-z0-9-]+\.recruitee\.com|apply\.workable\.com/[a-z0-9_-]+|"
    r"[a-z0-9-]+\.workable\.com|(?:careers|jobs)\.smartrecruiters\.com/[A-Za-z0-9_-]+|"
    r"[a-z0-9-]+\.teamtailor\.com|[a-z0-9-]+\.jobs\.personio\.(?:de|com)|"
    r"[a-z0-9-]+\.bamboohr\.com|[a-z0-9-]+\.applytojob\.com|[a-z0-9-]+\.breezy\.hr|"
    r"[a-z0-9-]+\.freshteam\.com|[a-z0-9.-]*myworkdayjobs\.com/[A-Za-z0-9_-]+|"
    r"[a-z0-9-]+\.zohorecruit\.[a-z]+|[a-z0-9-]+\.pinpointhq\.com|"
    r"jobs\.jobvite\.com/[a-z0-9_-]+|[a-z0-9-]+\.talentlyft\.com|"
    r"[a-z0-9-]+\.rippling\.com|[a-z0-9-]+\.factorialhr\.com|[a-z0-9-]+\.hibob\.com|"
    r"e?\.?huntflow\.(?:ru|io)/[a-z0-9_-]+", re.I)


def sniff(url: str) -> dict:
    """Ищет маркеры ATS в HTML careers-страницы. Возвращает {url, final, hits}."""
    text, final = fetch(url)
    hits = sorted({m.group(0) for m in SNIFF_RE.finditer(text)})
    return {"url": url, "final": final, "hits": hits}


# ──────────────────────────────────────────────────────────────────────────────
# Страны: алиасы для структурного матча локаций
# ──────────────────────────────────────────────────────────────────────────────

COUNTRY_ALIASES: dict[str, list[str]] = {
    "TR": ["turkey", "türkiye", "turkiye", "istanbul", "i̇stanbul", "ankara", "izmir", "i̇zmir"],
    "RU": ["russia", "россия", "moscow", "москва", "petersburg", "петербург", "новосибирск",
           "екатеринбург", "казань"],
    "CY": ["cyprus", "кипр", "limassol", "лимассол", "nicosia", "никосия", "larnaca", "paphos"],
    "RS": ["serbia", "сербия", "belgrade", "белград", "novi sad"],
    "AM": ["armenia", "армения", "yerevan", "ереван"],
    "GE": ["georgia", "грузия", "tbilisi", "тбилиси", "batumi", "батуми"],
    "KZ": ["kazakhstan", "казахстан", "almaty", "алматы", "astana", "астана"],
    "AE": ["united arab emirates", "uae", "dubai", "дубай", "abu dhabi"],
    "DE": ["germany", "германия", "berlin", "берлин", "munich", "münchen", "hamburg"],
    "NL": ["netherlands", "нидерланды", "amsterdam", "амстердам"],
    "PL": ["poland", "польша", "warsaw", "warszawa", "варшава", "krakow", "kraków", "wroclaw",
           "wrocław", "gdansk", "gdańsk", "poznan", "poznań"],
    "PT": ["portugal", "португалия", "lisbon", "lisboa", "лиссабон", "porto"],
    "ES": ["spain", "испания", "madrid", "мадрид", "barcelona", "барселона"],
    "GB": ["united kingdom", "великобритания", "london", "лондон"],
    "US": ["united states", "usa", "u.s.", "new york", "san francisco", "сша"],
    "UZ": ["uzbekistan", "узбекистан", "tashkent", "ташкент"],
}


def country_matcher(cc: str) -> re.Pattern:
    """Регулярка по стране: алиасы из таблицы + сам код/строка, всё по границам слова.

    Границы обязательны. Без них `russia` ловил «Prussia», а матч по заголовку —
    «Backend Java Engineer - Kazakhstan/Russian speaker», вакансию в Европе,
    и счётчик честно писал «по стране RU: 1». Тихая ошибка: в выводе видно, по
    какому полю совпало, но пользователь верит счётчику, а не разбирает строки.

    Матч по алиасам всё ещё грубый — он для отбора, а не для статистики. Поэтому
    в выводе команды всегда есть «всего», по которому потерю видно.
    """
    cc = cc.strip()
    parts = [rf"\b{re.escape(a)}\b" for a in COUNTRY_ALIASES.get(cc.upper(), [])]
    parts.append(rf"\b{re.escape(cc)}\b")
    return re.compile("|".join(parts), re.I)


# Слова, при которых упоминание страны в ЗАГОЛОВКЕ говорит о языке/гражданстве,
# а не о месте работы: «Russian speaker», «German-speaking support».
_LANGUAGE_CONTEXT = re.compile(r"\b(speak\w*|native|language|язык\w*|говорящ\w+)\b", re.I)


def job_matches_country(job: BoardJob, pat: re.Pattern) -> bool:
    """Страна ищется по ВСЕМ структурным полям локаций — см. докстринг модуля.

    Заголовок учитывается только если в нём нет языкового контекста: локация в
    названии («Backend Engineer, Cyprus») — сигнал, требование языка — нет.
    """
    if any(pat.search(x) for x in job.locations):
        return True
    return bool(pat.search(job.title)) and not _LANGUAGE_CONTEXT.search(job.title)
