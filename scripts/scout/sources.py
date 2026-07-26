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
from dataclasses import dataclass

from .model import Vacancy
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
# Разбор вилок
# ──────────────────────────────────────────────────────────────────────────────

_NUM = r"\d[\d\s   .,]*"
_CUR_SIGN = {"₽": "RUB", "руб": "RUB", "р.": "RUB", "$": "USD", "€": "EUR", "£": "GBP"}


def _num(s: str) -> int | None:
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def parse_salary(text: str | None) -> tuple[int | None, int | None, str | None, bool | None]:
    """Разбирает «от 250 000 до 400 000 ₽», «400 000 ₽», «$3000–5000», «2 800—12 500 USD»."""
    if not text:
        return None, None, None, None
    t = H.unescape(text).replace(" ", " ").replace(" ", " ").strip()
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

    # Знак валюты может стоять у каждой границы: «$3000 - $5000». Без этого
    # верхняя граница молча терялась, и вилка выглядела как «от 3000».
    rng = re.search(rf"({_NUM})\s*(?:—|–|-|до|to|\.\.)\s*[$€£₽]?\s*({_NUM})", t)
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


def _strip_tags(s: str) -> str:
    return H.unescape(re.sub(r"<[^>]+>", " ", s or ""))


def _one(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text, re.S)
    return H.unescape(m.group(group)).strip() if m else None


# ──────────────────────────────────────────────────────────────────────────────
# hh.ru — самый плотный источник, работает анонимно
# ──────────────────────────────────────────────────────────────────────────────

def src_hh(ctx: Ctx) -> list[Vacancy]:
    """Читает встроенный стейт `HH-Lux-InitialState`, а не вёрстку.

    Селекторы карточек на hh отдают пустоту, а в стейте лежит весь JSON: даты, вилки,
    работодатель, формат работы. `search_period` отбирает по публикации-ИЛИ-обновлению —
    ровно то окно, которое нужно, поэтому дополнительно резать по дате не надо.
    """
    out: list[Vacancy] = []
    seen: set[str] = set()
    for q in ctx.queries():
        url = qs("https://hh.ru/search/vacancy", {
            "text": q, "area": ctx.area, "order_by": "publication_time",
            "search_period": ctx.days, "items_on_page": min(ctx.limit, 100),
        })
        text, final = fetch(url)
        m = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', text, re.S)
        if not m:
            raise FetchError(final, "нет HH-Lux-InitialState — вёрстка сменилась или показана капча")
        state = json.loads(H.unescape(m.group(1)))
        result = state.get("vacancySearchResult") or {}
        for v in result.get("vacancies", []):
            vid = str(v.get("vacancyId") or "")
            if not vid or vid in seen:
                continue
            seen.add(vid)
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
                location=(v.get("area") or {}).get("name"),
                remote=any("REMOTE" in str(f) for f in (v.get("workFormats") or [])),
                published_at=pub.get("$") or pub.get("@timestamp"),
                updated_at=v.get("lastChangeTime"),
                tags=[t for t in (v.get("tags") or []) if isinstance(t, str)],
                raw={"employerId": company.get("id"), "responses": v.get("responsesCount"),
                     "experience": v.get("workExperience"), "query": q},
            ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# career.habr.com
# ──────────────────────────────────────────────────────────────────────────────

def src_habr(ctx: Ctx) -> list[Vacancy]:
    out, seen = [], set()
    for q in ctx.queries():
        url = qs("https://career.habr.com/vacancies", {"q": q, "type": "all", "sort": "date"})
        text, final = fetch(url)
        # Карточки бывают с модификаторами (`vacancy-card vacancy-card--featured`),
        # поэтому режем по границе слова, а не по `class="vacancy-card"` целиком:
        # иначе куски слипаются и чипы одной карточки утекают в другую.
        chunks = re.split(r'<div class="vacancy-card[\s"]', text)[1:]
        if not chunks:
            if "vacancy-card" in text:
                raise FetchError(final, "карточки есть, но разметка сменилась — парсер надо чинить")
            if not re.search(r"ничего не найдено|вакансий не найдено", text, re.I):
                raise FetchError(final, "ноль карточек и нет пометки «не найдено» — похоже на блок")
            continue
        for c in chunks:
            vid = _one(r'href="/vacancies/(\d+)"', c)
            if not vid or vid in seen:
                continue
            seen.add(vid)
            sal = _one(r'class="basic-salary[^"]*">(.*?)</div>', c)
            sf, st, cur, gross = parse_salary(_strip_tags(sal or ""))
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
                location=", ".join(cities) or None,
                remote=remote_chip in chips,
                published_at=_one(r'<time class="basic-date" datetime="([^"]+)"', c),
                tags=[x for x in chips if x in grades],
                raw={"chips": chips, "query": q},
            ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# careered.io — чистый JSON, снимает проблему SPA целиком
# ──────────────────────────────────────────────────────────────────────────────

def src_careered(ctx: Ctx) -> list[Vacancy]:
    out: list[Vacancy] = []
    offset = 0
    while offset < max(ctx.limit, 60):
        data = fetch_json(f"https://careered.io/api/jobs?offset={offset}")
        entries = data.get("entries") or []
        if not entries:
            break
        for e in entries:
            if e.get("kind") != "job":
                continue
            # Вилка приходит разложенной по полям, а не строкой: salary_from / salary_to /
            # salary_currency / salary_period. Разбирать текстом тут нечего и не нужно.
            feats = {f.get("key"): f.get("value") for f in (e.get("features") or [])}
            # Ноль у careered означает «вилка не указана», а не «платят ноль».
            # Записать 0 — значит показать в карточке «0–0 ₽» и соврать про условия.
            def to_int(v):
                s = str(v or "").strip()
                return int(s) if s.isdigit() and int(s) > 0 else None
            loc = feats.get("location") or feats.get("city")
            out.append(Vacancy(
                source="careered",
                external_id=str(e.get("id")),
                url=f"https://careered.io/jobs/{e.get('id')}",
                title=feats.get("name") or feats.get("title") or "",
                company=feats.get("company") or feats.get("employer"),
                salary_from=to_int(feats.get("salary_from")),
                salary_to=to_int(feats.get("salary_to")),
                currency=feats.get("salary_currency"),
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
        if offset >= (data.get("total") or 0):
            break
    return out


# ──────────────────────────────────────────────────────────────────────────────
# LinkedIn — гостевой поиск, без логина
# ──────────────────────────────────────────────────────────────────────────────

# По региону «Россия» гостевой поиск отдаёт ноль — вся ценность в зарубежных.
LINKEDIN_REGIONS = ("Germany", "Netherlands", "Poland", "Cyprus", "Portugal", "Spain",
                    "United Kingdom", "European Union", "Türkiye")


def src_linkedin(ctx: Ctx) -> list[Vacancy]:
    if not ctx.include_foreign:
        return []
    out, seen = [], set()
    # 1 день ≈ r86400; берём с запасом окна.
    seconds = max(ctx.days, 1) * 86400
    for region in LINKEDIN_REGIONS:
        url = qs("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", {
            "keywords": ctx.query, "location": region, "start": 0, "f_TPR": f"r{seconds}",
        })
        try:
            text, _ = fetch(url)
        except FetchError as e:
            # 429 у LinkedIn — норма при частых запросах; регион пропускаем, прогон живёт.
            if e.status in (429, 403):
                continue
            raise
        for c in text.split('<div class="base-card')[1:]:
            vid = _one(r'data-entity-urn="urn:li:jobPosting:(\d+)"', c)
            if not vid or vid in seen:
                continue
            seen.add(vid)
            out.append(Vacancy(
                source="linkedin",
                external_id=vid,
                url=(_one(r'href="(https://[^"]*?/jobs/view/[^"?]+)', c) or
                     f"https://www.linkedin.com/jobs/view/{vid}"),
                title=_strip_tags(_one(r'<span class="sr-only">(.*?)</span>', c) or ""),
                company=_strip_tags(_one(r'hidden-nested-link[^>]*>(.*?)</a>', c) or ""),
                location=_strip_tags(_one(r'job-search-card__location">(.*?)</span>', c) or ""),
                published_at=_one(r'<time[^>]*datetime="([^"]+)"', c),
                remote=None,
                raw={"region": region},
            ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# ATS-доски работодателей — приоритет №1 по «близости к нанимателю»
# ──────────────────────────────────────────────────────────────────────────────

# Реестр проверенных живыми запросами токенов (см. references/sources-setup.md).
# Токены НЕ угадываются: половина очевидных не существует, часть ведёт не туда
# (greenhouse `insider` — это Business Insider, а не турецкий useInsider).
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


_ATS_IMPL = {"greenhouse": _ats_greenhouse, "lever": _ats_lever,
             "ashby": _ats_ashby, "recruitee": _ats_recruitee}


# Роли, которые вообще имеет смысл нести дальше. Двадцать досок отдают ~6 600 вакансий,
# и подавляющее большинство — продажи, поддержка и маркетинг в других странах.
# Это не отбор по фиту (его делает модель), а отсечение заведомо другой профессии.
ATS_ROLE_RE = re.compile(
    r"\b(go|golang|backend|back-end|back end|platform|infrastructure|infra|sre|"
    r"devops|distributed|microservice|kubernetes|cloud|system[s]? engineer|"
    r"software engineer|full[- ]?stack|tech(nical)? lead|architect)\b",
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
    total = skipped = 0
    for board, (ok, payload) in sorted(results.items()):
        if not ok:
            failed.append(f"{board} ({payload})")
            continue
        total += len(payload)
        for v in payload:
            if ctx.ats_all or ATS_ROLE_RE.search(v.title or ""):
                out.append(v)
            else:
                skipped += 1

    if failed and len(failed) == len(ATS_BOARDS):
        raise FetchError("ats", f"все доски упали: {'; '.join(failed[:3])}")

    # Итог прогона по доскам — служебная строка, чтобы счёт был виден в отчёте,
    # а не восстанавливался по памяти.
    out.append(Vacancy(
        source="ats", external_id="_summary", url="",
        title=f"[сводка ATS] досок опрошено {len(ATS_BOARDS) - len(failed)}/{len(ATS_BOARDS)}, "
              f"вакансий {total}, под профиль {total - skipped}, отсеяно по профессии {skipped}",
        raw={"failed": failed, "total": total, "skipped": skipped,
             "boards": [f"{k}:{t}" for k, t in ATS_BOARDS]},
    ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Сырьевые источники: скрипт только приносит текст, разбирает модель
# ──────────────────────────────────────────────────────────────────────────────

# `auth` — площадка, чьи куки подставить из `.auth/`, если вход уже сделан.
RAW_SOURCES: dict[str, dict] = {
    "getmatch":   {"url": "https://getmatch.ru/vacancies?sp=all&s={q}"},
    "geekjob":    {"url": "https://geekjob.ru/vacancies?qs={q}", "auth": "geekjob"},
    "hirehi":     {"url": "https://hirehi.ru/vacancies/go,backend"},
    "hackoffer":  {"url": "https://hack-offer.tech/job/g/dev"},
    "rabota":     {"url": "https://www.rabota.ru/vacancy/?query={q}"},
    # Ниже — только после входа пользователя (`scout auth login`). Без сессии
    # отдадут форму логина, и это будет видно в дампе, а не притворится нулём.
    "shadowhint": {"url": "https://shadowhint.com/profile/tg-vacancies?search={q}",
                   "auth": "shadowhint"},
    "wantapply":  {"url": "https://wantapply.com/?search={q}", "auth": "wantapply"},
}


def raw_dump(name: str, ctx: Ctx) -> tuple[str, str]:
    """Отдаёт (текст страницы, URL) для источников, у которых нет надёжного парсера.

    Это сознательный компромисс. У getmatch данные лежат в RSC-пейлоаде Next.js,
    у geekjob — Vue-SPA; писать под них парсер значит писать то, что сломается молча.
    Скрипт снимает с модели механику (сходить, дождаться, распаковать), а суждение
    оставляет ей — ровно как требует «Инструменты пиши сам» в SKILL.md.
    """
    cfg = RAW_SOURCES[name]
    url = cfg["url"].format(q=ctx.query)
    cookies = None
    if cfg.get("auth"):
        from .auth import cookie_header  # локальный импорт: без Playwright тоже работает
        cookies = cookie_header(cfg["auth"])
    text, final = fetch(url, cookies=cookies)
    return text, final


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

SOURCES = {
    "hh": src_hh,
    "habr": src_habr,
    "careered": src_careered,
    "linkedin": src_linkedin,
    "ats": src_ats,
}

# Источники, требующие входа пользователя. Сборщик их не трогает: логинится
# только человек, а куки не выгружаются никуда (см. «Границы» в SKILL.md).
NEEDS_LOGIN = {
    "shadowhint": "https://shadowhint.com/profile/tg-vacancies",
    "wantapply": "https://wantapply.com/?search=Go",
    "hh-negotiations": "https://hh.ru/applicant/negotiations",
    "habr-applications": "https://career.habr.com/",
}
