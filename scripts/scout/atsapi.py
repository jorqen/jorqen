"""Единый доступ к API ATS-досок: Greenhouse, Lever, Ashby, Recruitee, Workable,
SmartRecruiters, BambooHR, Teamtailor, Personio, JazzHR, Workday.

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

import datetime as dt
import email.utils
import html as H
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .net import FetchError, fetch, fetch_json

UA_NOTE = None  # UA задаёт net.py — здесь ничего не переопределяется

ATS_KINDS = ("greenhouse", "lever", "ashby", "recruitee", "workable",
             "smartrecruiters", "bamboohr", "teamtailor", "personio",
             "jazzhr", "workday")

# Синонимы, которыми пользователь называет ATS в командной строке. Имя хоста —
# тоже синоним: `ats sniff` печатает находки доменами (`acme.applytojob.com`),
# и переспрашивать «а как это называется у тебя» после собственной же подсказки
# было бы издевательством.
ATS_ALIASES = {"gh": "greenhouse", "smart": "smartrecruiters", "sr": "smartrecruiters",
               "bamboo": "bamboohr", "tt": "teamtailor", "jazz": "jazzhr",
               "applytojob": "jazzhr", "wd": "workday",
               "myworkdayjobs": "workday", "personio.de": "personio"}


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


# ── Teamtailor ────────────────────────────────────────────────────────────────

# Пространство имён, в котором Teamtailor отдаёт локации внутри своего RSS.
_TT_NS = {"tt": "https://teamtailor.com/locations"}


def _rfc2822_iso(value: str | None) -> str | None:
    """RFC-2822 (`Wed, 13 May 2026 17:12:26 +0200`) → ISO.

    Все остальные доски здесь отдают ISO, и смешивать форматы в одном поле нельзя:
    свежесть сравнивается СТРОКАМИ, и «Wed, …» окажется старше любого «2026-…».
    """
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return None


def _board_teamtailor(token: str, query: str | None = None) -> Board:
    """Список берётся из RSS, а НЕ из /jobs.json — хотя JSON у Teamtailor тоже есть.

    /jobs.json — это JSON Feed 1.1 (id, title, url, date_published, content_html),
    и локаций в нём НЕТ ВООБЩЕ: ни поля, ни страны, ни города. С таким источником
    структурный матч локаций (см. докстринг модуля) отваливается молча — вакансия
    в Стокгольме выглядит вакансией без места и по стране не находится никогда.
    В /jobs.rss те же самые вакансии приходят с <tt:locations>, где есть и город,
    и страна. Второе отличие в ту же сторону: <link> в RSS ведёт на собственный
    careers-домен компании (career.anyfin.com), а url в JSON — на поддомен
    teamtailor, то есть на промежуточную страницу.

    Несуществующий поддомен отдаёт честный 404 — ловушки «200 и ноль» здесь нет.
    """
    url = f"https://{urllib.parse.quote(token)}.teamtailor.com/jobs.rss"
    text, _ = fetch(url)
    # Разбор через stdlib-ET, а не регуляркой: у локаций своё пространство имён,
    # и вытаскивать вложенные <tt:location> регуляркой — это тот же способ
    # промахнуться мимо строки, на котором уже обожглись в Workable.
    try:
        channel = ET.fromstring(text).find("channel")
    except ET.ParseError as e:
        raise FetchError(url, f"ответ не разобрался как RSS: {e}") from e
    if channel is None:
        raise FetchError(url, "в ответе нет <channel> — это не лента Teamtailor")

    jobs = []
    for item in channel.findall("item"):
        locs: list[str] = []
        for loc in item.findall("tt:locations/tt:location", _TT_NS):
            locs += [loc.findtext("tt:name", "", _TT_NS),
                     loc.findtext("tt:city", "", _TT_NS),
                     loc.findtext("tt:country", "", _TT_NS)]
        # У полностью удалённой вакансии <tt:locations> обычно пуст, и без этой
        # строки она уезжает в отчёт вообще без места — как будто поле потеряли.
        if (item.findtext("remoteStatus") or "").strip().lower() == "fully":
            locs.append("Remote")
        jobs.append(BoardJob(
            id=(item.findtext("guid") or "").strip(),
            title=(item.findtext("title") or "").strip(),
            url=(item.findtext("link") or "").strip(),
            locations=[x.strip() for x in locs if x and x.strip()],
            published_at=_rfc2822_iso(item.findtext("pubDate")),
        ))
    return Board("teamtailor", token, (channel.findtext("title") or "").strip() or None,
                 jobs, len(jobs),
                 note=None if jobs else "доска отвечает, но открытых вакансий нет")


# ── Personio ──────────────────────────────────────────────────────────────────

def _board_personio(token: str, query: str | None = None) -> Board:
    """search.json на {token}.jobs.personio.de, с откатом на .com.

    Оба домена живые и принадлежат разным арендаторам, а по названию компании
    угадать нужный нельзя — поэтому .de пробуется первым (их большинство), и
    только его 404 отправляет на .com.

    ДАТЫ ПУБЛИКАЦИИ ЗДЕСЬ НЕТ ВОВСЕ. search.json отдаёт id/name/office/offices/
    department/keywords/seniority — и всё. `createdAt` есть только в /xml, но
    /xml включён не у всех: проверено живьём — у personio он отвечает, а у
    getsafe и hometogo 404 при полностью рабочем search.json. Поэтому
    published_at пуст у всех вакансий, и это сказано в note, а не подменено
    датой скана: подставленная дата выглядела бы фактом о свежести.
    """
    last: FetchError | None = None
    base = d = None
    for tld in ("de", "com"):
        base = f"https://{urllib.parse.quote(token)}.jobs.personio.{tld}"
        try:
            d = fetch_json(f"{base}/search.json")
            break
        except FetchError as e:
            last = e
            if e.status == 429:
                # 429 прилетает и на заведомо несуществующий поддомен — значит
                # лимит стоит на весь хост jobs.personio.* и считается по нашему
                # IP, а не по компании. Отличить «доски нет» от «нас придержали»
                # в этот момент нечем, и молчаливый ноль тут был бы враньём.
                raise FetchError(e.url, "HTTP 429 — Personio придерживает по IP весь хост "
                                        "jobs.personio.*, а не эту доску; повтори позже",
                                 429) from e
            if e.status != 404:
                raise
    else:
        raise last or FetchError(f"personio:{token}", "доски нет ни на .de, ни на .com")

    rows = d if isinstance(d, list) else []
    jobs = []
    for j in rows:
        # `office` — это НЕ один офис, а склейка через запятую («Kaunas,Vilnius,
        # Kaunas/Vilnius»), тот же набор, что в offices[]. Берём оба и режем по
        # запятой: целиком такая строка не совпадёт ни с одной страной.
        locs = [str(x) for x in (j.get("offices") or [])]
        locs += [p for p in re.split(r"\s*,\s*", j.get("office") or "") if p]
        jid = str(j.get("id") or "")
        jobs.append(BoardJob(
            id=jid, title=j.get("name") or "",
            url=f"{base}/job/{jid}",
            locations=[x for x in dict.fromkeys(locs) if x],
        ))
    # subcompany заполнено далеко не у всех (у hometogo пусто, у personio —
    # «Personio SE & Co. KG»); пустое — это None, а не токен под видом названия.
    company = next((r.get("subcompany") for r in rows if r.get("subcompany")), None)
    note = "search.json не отдаёт дату публикации — published_at пуст у всех вакансий"
    if not jobs:
        note = "доска отвечает, но открытых вакансий нет"
    return Board("personio", token, company, jobs, len(jobs), note=note)


# ── JazzHR ────────────────────────────────────────────────────────────────────

# Строка таблицы вакансий. Публичного JSON/RSS у JazzHR нет (проверены
# /apply/jobs/rss, /apply/jobs.json, /apply/jobs/feed, ?rss=1, ?format=json —
# всё либо 404, либо та же HTML-страница), XML-фид выдаётся из личного кабинета
# и привязан к аккаунту. Зато сама таблица разложена аккуратно и стабильно.
_JAZZHR_ROW = re.compile(r'<tr id="row_job_(?P<ts>\d{14})_[A-Za-z0-9]+"[^>]*>(?P<body>.*?)</tr>',
                         re.S)
_JAZZHR_LINK = re.compile(
    r'<a[^>]*class="job_title_link"[^>]*href="/apply/jobs/details/(?P<code>[A-Za-z0-9]+)[^"]*"'
    r'[^>]*>(?P<title>.*?)</a>', re.S)
_JAZZHR_DEPT = re.compile(r'<span class="resumator_department">(?P<dept>.*?)</span>', re.S)
_JAZZHR_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LD_JSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)


def _flat_text(raw: str | None) -> str:
    """Ячейка таблицы → строка: без тегов, без сущностей, без переносов."""
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def _jazzhr_date(raw: str) -> str | None:
    """Дата из id строки таблицы: `row_job_20260604213845_…`.

    Больше её брать негде — список JazzHR не печатает дат вообще: ни колонкой,
    ни в разметке, ни в ld+json (там только Organization). В id лежит время
    создания записи о вакансии, и оно не обязано совпадать с датой публикации:
    у DTEX Systems строка row_job_20260604213845 против datePosted 2026-06-05
    на самой вакансии — разница в сутки. Для отбора по свежести годится,
    для утверждения «опубликовано ровно тогда-то» — нет.
    """
    try:
        return dt.datetime.strptime(raw, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def _board_jazzhr(token: str, query: str | None = None) -> Board:
    url = f"https://{urllib.parse.quote(token)}.applytojob.com/apply/jobs"
    text, final = fetch(url)
    # НЕСУЩЕСТВУЮЩИЙ поддомен не отдаёт 404: applytojob молча уводит на
    # маркетинговый www.jazzhr.com, где таблицы просто нет. Без этой проверки
    # опечатка в токене возвращалась бы как «доска жива, вакансий ноль».
    if "applytojob.com" not in urllib.parse.urlsplit(final).netloc.lower():
        raise FetchError(url, f"доски нет: applytojob увёл на {final}")

    # Одна и та же таблица отрисована в странице дважды (широкая и узкая вёрстка).
    # Без склейки по коду вакансии удваиваются — и удваиваются ПРАВДОПОДОБНО,
    # с теми же id, то есть на глаз это не поломка, а «активная компания».
    seen: dict[str, BoardJob] = {}
    for m in _JAZZHR_ROW.finditer(text):
        body = m.group("body")
        link = _JAZZHR_LINK.search(body)
        if not link:
            continue
        cells = _JAZZHR_CELL.findall(body)
        dept = _JAZZHR_DEPT.search(body)
        # Первая ячейка — название со ссылкой, последняя — локация.
        locs = [_flat_text(cells[-1]) if len(cells) > 1 else "",
                _flat_text(dept.group("dept")) if dept else ""]
        code = link.group("code")
        seen.setdefault(code, BoardJob(
            id=code, title=_flat_text(link.group("title")),
            url=f"https://{token}.applytojob.com/apply/{code}",
            locations=[x for x in locs if x],
            published_at=_jazzhr_date(m.group("ts")),
        ))

    company = None
    for blob in _LD_JSON.findall(text):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Organization":
            company = data.get("name") or None
            break

    note = None
    if not seen:
        # Выключенная доска отвечает 200 и полноценной страницей — просто без
        # вакансий. Это не «ноль вакансий», это «сюда ходить больше незачем».
        note = ("доска выключена (JazzHR: Inactive Career Page)"
                if "Inactive Career Page" in text else
                "таблица вакансий не найдена — вёрстка JazzHR могла поменяться")
    return Board("jazzhr", token, company, list(seen.values()), len(seen), note=note)


# ── Workday ───────────────────────────────────────────────────────────────────

# limit=21 отвечает HTTP 400: двадцать — жёсткий потолок страницы, не наш выбор.
_WORKDAY_PAGE = 20
# Потолок обхода. У PwC на доске 4586 вакансий — это 230 запросов по 20 штук,
# то есть один работодатель съедает прогон целиком. Обрезка не молчаливая:
# сколько взяли из скольких, всегда написано в note.
_WORKDAY_CAP = 500

# «3 Locations» вместо места — счётчик, а не локация (см. _board_workday).
_WORKDAY_LOC_COUNT = re.compile(r"^\d+\s+locations?$", re.I)

# Workday переводит СВОИ служебные строки по Accept-Language, а сборщик ходит
# с ru-RU (так требует hh). В результате тот же счётчик приезжал как
# «Количество месторасположений: 6» — мимо регулярки выше, и счётчик уезжал
# в locations как будто это место. Названия вакансий это не трогает: они
# лежат в данных как есть (у PwC заголовки по-португальски и остаются такими).
_WORKDAY_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}

# Тенант + номер хоста wdN + site id. Принимается и адрес доски целиком, и
# компактная запись через `:`, `/` или `.`.
_WORKDAY_SPEC = re.compile(
    r"(?:https?://)?(?P<tenant>[a-z0-9][a-z0-9-]*)[.:/]"
    r"(?P<host>wd\d+)(?:\.myworkdayjobs\.com)?"
    r"(?:/[a-z]{2}(?:-[A-Za-z]{2})?(?=/))?"
    r"[.:/](?P<site>[A-Za-z0-9_-]+)", re.I)


def _workday_parts(token: str) -> tuple[str, str, str]:
    """Разбирает «токен» Workday — а он у него тройной.

    Одного имени доски, как у всех остальных движков, здесь не существует: адрес
    складывается из тенанта, номера хоста (wd1…wd12) и site id, и ни номер, ни
    site id по названию компании не угадываются — их берут с careers-страницы.
    Поэтому принимаем и готовый URL, и компактную запись.
    """
    m = _WORKDAY_SPEC.search(token.strip())
    if not m:
        raise ValueError(
            "токен Workday состоит из трёх частей — тенант, хост wdN и site id: "
            "'nvidia:wd5:NVIDIAExternalCareerSite' либо адрес доски целиком "
            "'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'; "
            f"получено {token!r}")
    return m.group("tenant").lower(), m.group("host").lower(), m.group("site")


def _board_workday(token: str, query: str | None = None) -> Board:
    """Приватный cxs-API careers-сайта: POST, без ключей, отвечает всем.

    Даты публикации в выдаче списка нет. `postedOn` — человеческая фраза на языке
    заголовка Accept-Language («Posted 2 Days Ago», у нас — «Опубликовано
    сегодня»), а не дата; настоящий `startDate` лежит только в деталке, то есть
    стоит отдельного запроса на каждую вакансию. Класть фразу в published_at
    нельзя: поле сравнивается как дата и молча сломает сортировку по свежести.
    """
    tenant, host, site = _workday_parts(token)
    base = f"https://{tenant}.{host}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"

    jobs: list[BoardJob] = []
    offset, total, counted_only = 0, 0, 0
    while True:
        d = fetch_json(api, method="POST", headers=_WORKDAY_HEADERS,
                       data={"appliedFacets": {}, "limit": _WORKDAY_PAGE,
                             "offset": offset, "searchText": query or ""})
        # total считается ТОЛЬКО на первой странице. Дальше Workday присылает
        # `total: 0` при полной странице вакансий, и наивное `total = d["total"]`
        # на каждом витке останавливало обход после второго запроса: у NVIDIA
        # получалось 40 вакансий из 2000, причём с пометкой «ноль вакансий».
        # Самая дорогая из возможных поломок — тихая недостача в полсотни раз.
        if offset == 0:
            total = int(d.get("total") or 0)
        batch = d.get("jobPostings") or []
        for j in batch:
            path = j.get("externalPath") or ""
            loc = (j.get("locationsText") or "").strip()
            # «3 Locations» — это СЧЁТЧИК, а не место: стран в выдаче списка нет
            # вовсе, они только в деталке вакансии. Положить счётчик в locations
            # значит получить заполненное с виду поле, по которому не совпадёт
            # ни одна страна, — ровно тот молчаливый промах, ради которого
            # locations и собирает все поля разом.
            if _WORKDAY_LOC_COUNT.match(loc):
                counted_only += 1
                loc = ""
            bullets = [str(x) for x in (j.get("bulletFields") or []) if x]
            jobs.append(BoardJob(
                # bulletFields[0] — номер реквизиции (JR2017740, R169992, 729320WD);
                # проверено одинаковым у nvidia, adobe, cisco, pwc и самого Workday.
                id=bullets[0] if bullets else path,
                title=j.get("title") or "",
                url=f"{base}/{site}{path}" if path else "",
                locations=[loc] if loc else [],
            ))
        offset += len(batch)
        # Выход ОБЯЗАН упираться в total. Offset за пределами выдачи не отдаёт
        # пустую страницу, а заворачивается на первую — то есть «идти, пока
        # страница полная» здесь крутится вечно, каждый раз добавляя те же
        # двадцать вакансий (проверено: total=22, offset=40 → снова первые 20).
        if not batch or offset >= total or offset >= _WORKDAY_CAP:
            break

    notes = []
    if total == 0:
        # Живой тенант с чужим site id отвечает 200 и total: 0 — проверено на
        # dell/External. То есть ноль здесь неотличим от «site id не тот».
        notes.append("0 вакансий при HTTP 200 — у Workday так отвечает и живой тенант "
                     "с неверным site id")
    elif len(jobs) < total:
        notes.append(f"взяты первые {len(jobs)} из {total}: Workday отдаёт по "
                     f"{_WORKDAY_PAGE} за запрос — сузь выдачу через --grep")
    if counted_only:
        notes.append(f"у {counted_only} вакансий вместо места стоит счётчик «N Locations» — "
                     f"страны есть только в деталке")
    notes.append("название компании API не отдаёт — сверь по тенанту "
                 f"{tenant}.{host}.myworkdayjobs.com")
    return Board("workday", token, None, jobs, total or len(jobs), note="; ".join(notes))


BOARD_IMPL = {
    "greenhouse": _board_greenhouse,
    "lever": _board_lever,
    "ashby": _board_ashby,
    "recruitee": _board_recruitee,
    "workable": _board_workable,
    "smartrecruiters": _board_smartrecruiters,
    "bamboohr": _board_bamboohr,
    "teamtailor": _board_teamtailor,
    "personio": _board_personio,
    "jazzhr": _board_jazzhr,
    "workday": _board_workday,
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
    ("teamtailor", re.compile(
        r"//(?P<token>[a-z0-9-]+)\.teamtailor\.com/jobs/(?P<id>\d+)")),
    ("personio", re.compile(
        r"//(?P<token>[a-z0-9-]+)\.jobs\.personio\.(?:de|com)/job/(?P<id>\d+)")),
    # `/apply/{code}` — канонический адрес вакансии, `/apply/jobs/details/{code}` —
    # то, чем на неё ссылается сама таблица. Отрицательный просмотр отсекает
    # `/apply/jobs` — это СПИСОК, и принять его за вакансию с id «jobs» значит
    # потом молча дёргать деталку несуществующей вакансии.
    ("jazzhr", re.compile(
        r"//(?P<token>[a-z0-9-]+)\.applytojob\.com/apply/"
        r"(?:jobs/details/)?(?P<id>(?!jobs(?:/|$))[A-Za-z0-9]+)")),
    # Токен Workday — сам кусок адреса до /job/: тенант, хост и site id вместе,
    # ровно в том виде, который понимает _workday_parts.
    ("workday", re.compile(
        r"(?P<token>[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com"
        r"(?:/[a-z]{2}(?:-[A-Za-z]{2})?(?=/))?/[A-Za-z0-9_-]+)/(?P<id>job/[^?#\s]+)", re.I)),
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
    # Северная Европа добавлена 05.08.2026 вместе с teamtailor: это шведский ATS,
    # и его доски (Anyfin, Tibber, Quinyx) пишут город без страны. Без этих
    # строк «Stockholm» не совпадал ни с чем, и вакансия отсеивалась как
    # «страна не наша» — то есть площадку подключили бы, а выдачу потеряли.
    "SE": ["sweden", "швеция", "stockholm", "стокгольм", "gothenburg", "göteborg",
           "malmo", "malmö"],
    "NO": ["norway", "норвегия", "oslo", "осло", "bergen"],
    "FI": ["finland", "финляндия", "helsinki", "хельсинки", "espoo"],
    "DK": ["denmark", "дания", "copenhagen", "københavn", "копенгаген"],
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
