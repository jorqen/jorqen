"""Нормализованная выжимка страницы вакансии — чтобы модель читала 30 строк, а не мегабайт.

Раньше «открыть вакансию целиком» означало привезти в контекст HTML-дамп; на пятнадцати
вакансиях это съедало весь бюджет скана. Здесь каждая страница превращается в плоскую
структуру: заголовок, деньги, локация, формат, даты, путь отклика, требования и описание
чистым текстом. Вопросы формы отклика выводятся всегда, где API их отдаёт, — по ним
модель решает, что писать вместо сопроводительного.

Честность важнее полноты: если парсера под источник нет, страница отдаётся generic-текстом
с явной пометкой «разбери глазами», а не выдаётся за разобранную.
"""

from __future__ import annotations

import html as H
import json
import re
import urllib.parse
from dataclasses import dataclass, field, asdict

from . import atsapi
from .model import PERIOD_SUFFIX, _iso, norm_period, salary_str
from .net import BlockedError, FetchError, fetch, fetch_json
from .resolve import classify, find_targets, follow

# ──────────────────────────────────────────────────────────────────────────────
# HTML → чистый текст
# ──────────────────────────────────────────────────────────────────────────────

_BLOCK_TAGS = re.compile(r"</?(?:p|div|h[1-6]|tr|table|ul|ol|blockquote|section|article)\b[^>]*>",
                         re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_LI = re.compile(r"<li\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
# `template` здесь не для красоты. hh кладёт в <template id="HH-Lux-InitialState">
# ~470 КБ Redux-стейта; без вырезания html_to_text(<body>) даёт 381 174 символа
# сырого JSON вместо 2 381 символа текста. Пока generic сначала берёт <main>, мина
# не видна — но на первом же фолбэке на <body> JSON уезжает в выжимку, и проверка
# правдоподобия находит в нём «требовани» и считает страницу разобранной.
#
# `form` НЕ вырезается сознательно: в форме отклика живут вопросы работодателя
# («Почему вы хотите к нам?», анкета самоидентификации) — по ним модель решает,
# что писать вместо сопроводительного. Потерять их дороже, чем стерпеть разметку.
_SCRIPTS = re.compile(r"<(script|style|noscript|svg|iframe|template)\b.*?</\1>", re.S | re.I)
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def html_to_text(s: str | None) -> str:
    """HTML → читаемый текст: <li> в маркеры, блочные теги в переводы строк."""
    if not s:
        return ""
    s = _SCRIPTS.sub(" ", s)
    s = _BR.sub("\n", s)
    s = _LI.sub("\n• ", s)
    s = _BLOCK_TAGS.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = H.unescape(s).replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    out, blank = [], 0
    for ln in lines:
        blank = blank + 1 if not ln else 0
        if blank <= 1:
            out.append(ln)
    return "\n".join(out).strip()


def md_to_text(s: str | None) -> str:
    """Markdown-выдача (workable, careered) → текст: картинки долой, ссылки в подпись."""
    if not s:
        return ""
    s = _MD_IMG.sub("", s)
    s = _MD_LINK.sub(r"\1", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = s.replace("**", "").replace("\\-", "-")
    return html_to_text(s) if "<" in s else re.sub(r"\n{3,}", "\n\n", s).strip()


# ──────────────────────────────────────────────────────────────────────────────
# Результат
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Detail:
    source: str                      # hh | habr | careered | getmatch | ats:<kind> | generic
    url: str
    title: str | None = None
    company: str | None = None
    salary: str | None = None        # человекочитаемая строка или None = «не указана»
    location: str | None = None
    work_format: str | None = None   # remote / hybrid / office, как назвал источник
    published_at: str | None = None
    updated_at: str | None = None
    apply_url: str | None = None
    apply_note: str | None = None
    requirements: str | None = None  # чистый текст; None = источник не разделяет
    description: str | None = None   # чистый текст
    questions: list[str] = field(default_factory=list)  # вопросы формы отклика
    extra: dict = field(default_factory=dict)
    status: str = "ok"               # ok | generic | generic-empty
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Путь отклика: переиспользуем резолвер на уже скачанном HTML
# ──────────────────────────────────────────────────────────────────────────────

def _apply_from_html(html_text: str, page_url: str, follow_hops: bool = True) -> tuple[str | None, str | None]:
    """(apply_url, note) из разметки страницы. Ничего не нажимает — только читает."""
    targets = find_targets(html_text, page_url)
    if follow_hops:
        followed = 0
        for t in targets:
            if (t.url and t.kind in ("external", "aggregator") and t.safe_to_open
                    and not t.url.startswith("mailto:") and followed < 3):
                followed += 1
                hop = follow(t.url, max_hops=3)
                if hop["kind"] == "ats":
                    t.kind, t.url, t.label = "ats", hop["final_url"], hop["ats"]
    order = {"ats": 0, "external": 1, "aggregator": 2, "js-only": 3, "form-submit": 4}
    targets.sort(key=lambda t: order.get(t.kind, 9))
    best = next((t for t in targets if t.kind in ("ats", "external") and t.url), None)
    if best:
        label = f" ({best.label})" if best.label and best.kind == "ats" else ""
        return best.url, f"{best.kind}{label}"
    if any(t.kind == "form-submit" for t in targets):
        return None, "отклик — форма на самой площадке; НЕ НАЖИМАТЬ, отправляет пользователь"
    if any(t.kind == "js-only" for t in targets):
        return None, "адрес отклика подставляется скриптом — без нажатия не узнать"
    return None, None


# ──────────────────────────────────────────────────────────────────────────────
# hh.ru (и гео-поддомены)
# ──────────────────────────────────────────────────────────────────────────────

_HH_EXPERIENCE = {"noExperience": "без опыта", "between1And3": "1–3 года",
                  "between3And6": "3–6 лет", "moreThan6": "6+ лет"}


def _detail_hh(url: str) -> Detail:
    text, final = fetch(url)
    m = re.search(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', text, re.S)
    if not m:
        raise FetchError(final, "нет HH-Lux-InitialState — вёрстка сменилась или это не вакансия")
    state = json.loads(H.unescape(m.group(1)))
    vv = state.get("vacancyView") or {}
    if not vv.get("vacancyId"):
        raise FetchError(final, "в стейте нет vacancyView — страница не вакансии")

    comp = vv.get("compensation") or {}
    company = vv.get("company") or {}
    skills = ((vv.get("keySkills") or {}).get("keySkill")) or []
    apply_url, apply_note = _apply_from_html(text, final)
    d = Detail(
        source="hh", url=final,
        title=vv.get("name"),
        company=company.get("visibleName") or company.get("name"),
        # from/to у hh — всегда месячная сумма, даже при mode=SHIFT (перводная
        # ставка лежит отдельно в perModeFrom/perModeTo). Период поэтому месяц,
        # а исходный режим уезжает в extra, чтобы его было чем перепроверить.
        salary=salary_str(comp.get("from"), comp.get("to"), comp.get("currencyCode"),
                          comp.get("gross"),
                          "month" if (comp.get("from") or comp.get("to")) else None) or None,
        location=(vv.get("area") or {}).get("name"),
        work_format=" / ".join(vv.get("workFormats") or []) or None,
        published_at=vv.get("publicationDate"),
        apply_url=apply_url,
        apply_note=apply_note or "отклик через форму hh — отправляет пользователь",
        description=html_to_text(vv.get("description")),
        extra={"key_skills": skills,
               "experience": _HH_EXPERIENCE.get(vv.get("workExperience"),
                                                vv.get("workExperience")),
               "employment": vv.get("employmentForm"),
               "compensation_mode": comp.get("mode"),
               "compensation_per_mode": [comp.get("perModeFrom"), comp.get("perModeTo")]
                                        if (comp.get("perModeFrom")
                                            or comp.get("perModeTo")) else None},
    )
    if vv.get("contactInfo"):
        d.extra["contacts"] = vv["contactInfo"]
    if vv.get("closedForApplicants"):
        d.notes.append("вакансия ЗАКРЫТА для откликов")
    return d


# ──────────────────────────────────────────────────────────────────────────────
# career.habr.com
# ──────────────────────────────────────────────────────────────────────────────

def _detail_habr(url: str) -> Detail:
    text, final = fetch(url)
    vac, ld = None, None
    for blob in re.findall(r'<script type="application/json" data-ssr-state="true">(.*?)</script>',
                           text, re.S):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("vacancy"), dict):
            vac = data["vacancy"]
    for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.S):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            ld = data
    if not vac and not ld:
        raise FetchError(final, "ни ssr-state, ни JSON-LD не нашлись — вёрстка сменилась")

    vac = vac or {}
    ld = ld or {}
    sal = vac.get("salary") or {}
    locations = [x.get("title") for x in (vac.get("locations") or []) if x.get("title")]
    if not locations:
        locations = [p.get("address") for p in (ld.get("jobLocation") or [])
                     if isinstance(p, dict) and p.get("address")]
    apply_url, apply_note = _apply_from_html(text, final)
    d = Detail(
        source="habr", url=final,
        title=vac.get("title") or ld.get("title"),
        company=((vac.get("company") or {}).get("title")
                 or (ld.get("hiringOrganization") or {}).get("name")),
        # Habr периода не отдаёт ни полем, ни в JSON-LD: в «formatted» лежит
        # «от 300 000 ₽» без единого слова о том, за что. Значит период неизвестен —
        # строка идёт как есть, без выдуманного «/мес».
        salary=(sal.get("formatted") or "").strip()
               or salary_str(sal.get("from"), sal.get("to"), sal.get("currency")) or None,
        location=", ".join(locations) or None,
        work_format="remote" if vac.get("remoteWork") else None,
        published_at=ld.get("datePosted"),
        apply_url=apply_url,
        apply_note=apply_note or "отклик через форму площадки — отправляет пользователь",
        description=html_to_text(ld.get("description")),
        # updated_at — «поднято в выдаче»: ровно та дата, что стоит на карточке
        # в списке. Без этого detail и collect показывали по одной вакансии даты
        # с разницей в три недели и оба выглядели правдой.
        updated_at=(vac.get("publishedDate") or {}).get("title"),
        extra={"grade": (vac.get("salaryQualification") or {}).get("title"),
               "employment": vac.get("employment"),
               "published_label": (vac.get("publishedDate") or {}).get("title")},
    )
    if d.published_at and d.updated_at:
        d.notes.append("две даты: «опубл.» — исходная публикация из JSON-LD, "
                       "«обновл.» — поднятие в выдаче (её же показывает список)")
    if not d.description:
        d.notes.append("описание не нашлось ни в LD, ни в ssr-state")
    return d


# ──────────────────────────────────────────────────────────────────────────────
# careered.io
# ──────────────────────────────────────────────────────────────────────────────

def _detail_careered(url: str) -> Detail:
    m = re.search(r"careered\.io/jobs/([0-9a-fA-F-]+)", url)
    if not m:
        raise FetchError(url, "не разобрал id вакансии careered из URL")
    jid = m.group(1)
    e = fetch_json(f"https://careered.io/api/jobs/{jid}")
    feats = {f.get("key"): f.get("value") for f in (e.get("features") or [])}
    to_int = lambda v: int(v) if str(v or "").strip().isdigit() and int(v) > 0 else None
    content = (e.get("content") or "").replace("&;&;", "\n")
    return Detail(
        source="careered", url=f"https://careered.io/jobs/{jid}",
        title=feats.get("name") or feats.get("title"),
        company=feats.get("company") or feats.get("employer"),
        salary=salary_str(to_int(feats.get("salary_from")), to_int(feats.get("salary_to")),
                          feats.get("salary_currency"),
                          period=feats.get("salary_period")) or None,
        location=feats.get("location") or feats.get("city"),
        work_format="remote" if "remote" in str(feats.get("location") or "").lower() else None,
        published_at=_iso(e.get("posted_at")),
        apply_note="контакт за бесплатной регистрацией на careered.io",
        description=md_to_text(content),
        extra={"tag": (e.get("tag") or {}).get("name"), "yoe": feats.get("yoe"),
               "salary_period": feats.get("salary_period")},
    )


# ──────────────────────────────────────────────────────────────────────────────
# getmatch.ru
# ──────────────────────────────────────────────────────────────────────────────

def _detail_getmatch(url: str) -> Detail:
    m = re.search(r"getmatch\.ru/vacancies/(\d+)", url)
    if not m:
        raise FetchError(url, "в slug нет числового id — открой страницу глазами (raw)")
    o = fetch_json(f"https://getmatch.ru/api/offers/{m.group(1)}")
    locs = [li.get("label") for li in (o.get("location_items") or []) if li.get("label")]
    formats = sorted({li.get("format") for li in (o.get("location_items") or [])
                      if li.get("format")})
    desc = o.get("description_html") or o.get("offer_description") or o.get("description")
    d = Detail(
        source="getmatch", url=urllib.parse.urljoin("https://getmatch.ru/", o.get("url") or url),
        title=o.get("position"),
        company=(o.get("company") or {}).get("name"),
        salary=salary_str(o.get("salary_display_from"), o.get("salary_display_to"),
                          o.get("salary_currency"),
                          {"net": False, "gross": True}.get(o.get("salary_taxes"))) or None,
        location=", ".join(locs) or None,
        work_format=" / ".join(formats) or None,
        published_at=o.get("published_at"),
        apply_note="отклик — форма getmatch; отправляет пользователь",
        description=html_to_text(desc),
        extra={"seniority": o.get("seniority"), "english": o.get("english_level"),
               "experience_years": o.get("required_years_of_experience"),
               "stack": html_to_text(o.get("stack_description"))[:300] or None,
               "skills": [s.get("name") for s in (o.get("skills_objects") or [])
                          if isinstance(s, dict)]},
    )
    if o.get("cover_letter_required"):
        d.questions.append("Сопроводительное письмо (обязательное поле формы)")
    if o.get("is_active") is False:
        d.notes.append("вакансия помечена НЕАКТИВНОЙ")
    return d


# ──────────────────────────────────────────────────────────────────────────────
# ATS-деталки по API
# ──────────────────────────────────────────────────────────────────────────────

def _q_label(q: dict) -> str:
    req = " (обязательный)" if q.get("required") else ""
    return f"{q.get('label', '?')}{req}"


# Заголовки секции требований в описании. У Lever требования лежат отдельным
# полем, у Greenhouse/Ashby/hh — тем же текстом, что и всё остальное, и поле
# requirements оставалось пустым: в --json это читалось как «требований нет».
_REQ_HEAD = re.compile(
    r"^[\s#*_>-]{0,6}(?:"
    r"требовани\w*|ожидани\w*|что мы ждём|чего мы ждём|наши ожидания|"
    r"мы ожидаем|что нужно|ты нам подойдёшь|вам подойдёт|наш кандидат|"
    r"(?:what|who) (?:we|you)['’]?\w* (?:looking for|expect|need|are)|"
    r"requirements?|qualifications?|must[- ]haves?|your (?:profile|experience|skills)|"
    r"what you(?:'ll| will)? (?:bring|need)|skills? (?:and|&) (?:experience|qualifications)|"
    r"about you|ideal candidate"
    r")\s*[:.]?\s*$", re.I | re.M)
# Секции, на которых блок требований заканчивается.
_REQ_STOP = re.compile(
    r"^[\s#*_>-]{0,6}(?:"
    r"услови\w*|мы предлагаем|что мы предлагаем|бонус\w*|будет плюсом|"
    r"о компании|о нас|benefits?|what we offer|perks?|compensation|"
    r"nice[- ]to[- ]haves?|about (?:us|the company)|our stack|responsibilities"
    r")\s*[:.]?\s*$", re.I | re.M)


def split_requirements(text: str | None) -> str | None:
    """Вырезает блок требований из сплошного описания.

    None — секции не нашлось (а не «требований нет»): отличать эти два случая
    важно, иначе пустое поле в --json выглядит фактом о вакансии."""
    if not text or len(text) < 80:
        return None
    m = _REQ_HEAD.search(text)
    if not m:
        return None
    tail = text[m.end():]
    stop = _REQ_STOP.search(tail)
    block = tail[:stop.start()] if stop else tail
    block = block.strip()
    # Слишком короткий кусок — это заголовок без содержимого, а не требования.
    return f"{m.group(0).strip()}\n{block}" if len(block) >= 40 else None


def _detail_greenhouse(token: str, jid: str) -> Detail:
    j = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}?questions=true")
    offices = ", ".join(o.get("name", "") for o in (j.get("offices") or []) if o.get("name"))
    loc = (j.get("location") or {}).get("name")
    questions = [_q_label(q) for q in (j.get("questions") or [])]
    # content приезжает HTML-эскейпнутым — сначала unescape, потом чистка тегов.
    desc = html_to_text(H.unescape(j.get("content") or ""))
    pay = j.get("pay_input_ranges") or []
    # Период Greenhouse отдельным полем не отдаёт — он бывает назван в title вилки
    # («Annual Salary»). Есть слово — ставим суффикс, нет — вилка идёт без него.
    pay_s = "; ".join(
        f"{p.get('min_cents', 0) // 100:,}–{p.get('max_cents', 0) // 100:,} "
        f"{p.get('currency_type', '')}".replace(",", " ")
        + PERIOD_SUFFIX.get(norm_period(p.get("title")) or "", "")
        for p in pay) or None
    return Detail(
        source="ats:greenhouse", url=j.get("absolute_url") or "",
        title=j.get("title"), company=j.get("company_name") or token,
        salary=pay_s,
        location=", ".join(x for x in (loc, offices) if x) or None,
        published_at=j.get("first_published"), updated_at=j.get("updated_at"),
        apply_url=j.get("absolute_url"),
        apply_note="форма Greenhouse на той же странице; вопросы ниже",
        description=desc, questions=questions,
        extra={"departments": [d.get("name") for d in (j.get("departments") or [])]},
    )


def _detail_lever(token: str, jid: str) -> Detail:
    j = fetch_json(f"https://api.lever.co/v0/postings/{token}/{jid}")
    cats = j.get("categories") or {}
    lists = j.get("lists") or []
    req_parts = [f"## {l.get('text')}\n{html_to_text(l.get('content'))}" for l in lists]
    sal = j.get("salaryRange") or {}
    return Detail(
        source="ats:lever", url=j.get("hostedUrl") or "",
        title=j.get("text"), company=None,
        # Lever называет период в salaryRange.interval («per-year-salary»).
        salary=salary_str(sal.get("min"), sal.get("max"), sal.get("currency"),
                          period=sal.get("interval")) or None,
        location=", ".join(x for x in (cats.get("location"), j.get("country")) if x) or None,
        work_format=j.get("workplaceType"),
        published_at=_iso(j.get("createdAt")),
        apply_url=j.get("applyUrl"),
        apply_note="страница /apply — форма Lever; вопросы публичный API не отдаёт",
        requirements="\n\n".join(req_parts) or None,
        description=(j.get("descriptionPlain") or "").strip()
                    + ("\n\n" + (j.get("additionalPlain") or "").strip()
                       if j.get("additionalPlain") else ""),
        extra={"team": cats.get("team"), "commitment": cats.get("commitment")},
        notes=["название компании Lever API не отдаёт"],
    )


def _detail_ashby(token: str, jid: str) -> Detail:
    b = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    j = next((x for x in b.get("jobs", []) if str(x.get("id")) == jid), None)
    if not j:
        raise FetchError(f"ashby:{token}", f"вакансии {jid} нет на доске "
                         f"(Ashby ротирует UUID при переопубликации — проверь check-links)")
    secondary = [s.get("location") for s in (j.get("secondaryLocations") or [])
                 if isinstance(s, dict) and s.get("location")]
    comp = (j.get("compensation") or {})
    comp_s = (comp.get("scrapeableCompensationSalarySummary")
              or comp.get("compensationTierSummary"))
    return Detail(
        source="ats:ashby", url=j.get("jobUrl") or "",
        title=j.get("title"), company=b.get("name") or token,
        salary=comp_s,
        location=" / ".join([x for x in [j.get("location"), *secondary] if x]) or None,
        work_format=("remote" if j.get("isRemote") else j.get("workplaceType")),
        published_at=j.get("publishedAt"),
        apply_url=j.get("applyUrl"),
        apply_note="форма Ashby; вопросы публичный API не отдаёт",
        description=(j.get("descriptionPlain") or "").strip()
                    or html_to_text(j.get("descriptionHtml")),
        extra={"department": j.get("department"), "team": j.get("team"),
               "employment": j.get("employmentType")},
    )


def _detail_recruitee(token: str, slug: str) -> Detail:
    d = fetch_json(f"https://{token}.recruitee.com/api/offers/{slug}")
    j = d.get("offer") or d
    questions = []
    for q in (j.get("open_questions") or []):
        if isinstance(q, dict):
            questions.append(_q_label({"label": q.get("body") or q.get("title"),
                                       "required": q.get("required")}))
    for opt, label in (("options_cv", "Резюме"), ("options_cover_letter", "Сопроводительное")):
        v = j.get(opt)
        if v and v != "off":
            questions.append(f"{label} ({'обязательное' if v == 'required' else 'опциональное'} поле)")
    return Detail(
        source="ats:recruitee", url=j.get("careers_url") or "",
        title=j.get("title"), company=j.get("company_name") or token,
        # У Recruitee период лежит в salary.period («month»); без него та же
        # вилка 185–250 USD читалась бы как месячная зарплата, а не ставка.
        salary=(j.get("salary") or {}).get("min") and salary_str(
            (j.get("salary") or {}).get("min"), (j.get("salary") or {}).get("max"),
            (j.get("salary") or {}).get("currency"),
            period=(j.get("salary") or {}).get("period")) or None,
        location=", ".join(x for x in (j.get("city"), j.get("country")) if x) or None,
        work_format="remote" if j.get("remote") else ("hybrid" if j.get("hybrid") else None),
        published_at=j.get("published_at") or j.get("created_at"),
        apply_url=j.get("careers_apply_url"),
        apply_note="форма Recruitee",
        requirements=html_to_text(j.get("requirements")),
        description=html_to_text(j.get("description")),
        questions=questions,
        extra={"department": j.get("department"), "employment": j.get("employment_type_code")},
    )


def _detail_workable(token: str, shortcode: str) -> Detail:
    # Единственный GET-путь к деталке — markdown-версия страницы вакансии.
    text, final = fetch(f"https://apply.workable.com/{token}/jobs/view/{shortcode}.md")
    if text.lstrip().startswith("<"):
        raise FetchError(final, "вместо markdown пришёл HTML — вакансии с таким shortcode нет")
    title = md_meta = workplace = None
    sections: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("# ") and not title:
            title = ln[2:].strip()
        elif ln.startswith("> ") and not md_meta:
            md_meta = ln[2:].strip()
        elif ln.startswith("**Workplace:**"):
            workplace = ln.split("**Workplace:**")[-1].strip()
        elif ln.startswith("## "):
            cur = ln[3:].strip().lower()
            sections[cur] = []
        elif cur is not None:
            sections[cur].append(ln)
    meta_parts = [p.strip() for p in (md_meta or "").split("·")]
    sec = lambda name: md_to_text("\n".join(sections.get(name, []))) or None
    return Detail(
        source="ats:workable", url=f"https://apply.workable.com/{token}/j/{shortcode}/",
        title=title,
        company=meta_parts[0] if meta_parts else None,
        salary=(meta_parts[2] if len(meta_parts) > 2 and meta_parts[2] != "—" else None),
        location=meta_parts[1] if len(meta_parts) > 1 else None,
        work_format=workplace,
        published_at=next((p.replace("Posted", "").strip() for p in meta_parts
                           if p.startswith("Posted")), None),
        apply_url=f"https://apply.workable.com/{token}/j/{shortcode}/",
        apply_note="форма Workable на странице вакансии; вопросы GET-API не отдаёт",
        requirements=sec("requirements"),
        description=sec("description"),
        extra={"benefits": (sec("benefits") or "")[:600] or None},
    )


def _detail_bamboohr(token: str, jid: str) -> Detail:
    d = fetch_json(f"https://{token}.bamboohr.com/careers/{jid}/detail")
    r = d.get("result") or {}
    j = r.get("jobOpening") or {}
    loc = j.get("location") or {}
    questions = []
    for key, f in (r.get("formFields") or {}).items():
        if isinstance(f, dict) and f.get("label"):
            questions.append(_q_label({"label": f["label"], "required": f.get("isRequired")}))
    return Detail(
        source="ats:bamboohr", url=j.get("jobOpeningShareUrl") or "",
        title=j.get("jobOpeningName"), company=token,
        salary=(j.get("compensation") if j.get("compensation") not in (None, "None") else None),
        location=", ".join(x for x in (loc.get("city"), loc.get("state"),
                                       loc.get("addressCountry")) if x) or None,
        work_format={"0": None, "1": "remote", "2": "hybrid"}.get(str(j.get("locationType"))),
        published_at=j.get("datePosted"),
        apply_url=j.get("jobOpeningShareUrl"),
        apply_note="форма BambooHR; поля формы — в вопросах ниже",
        description=html_to_text(j.get("description")),
        questions=questions,
        extra={"department": j.get("departmentLabel"),
               "experience": j.get("minimumExperience"),
               "employment": j.get("employmentStatusLabel")},
        notes=["название компании API не отдаёт — в поле company подставлен токен"],
    )


_ATS_DETAIL = {"greenhouse": _detail_greenhouse, "lever": _detail_lever,
               "ashby": _detail_ashby, "recruitee": _detail_recruitee,
               "workable": _detail_workable, "bamboohr": _detail_bamboohr}


# ──────────────────────────────────────────────────────────────────────────────
# Generic-fallback: честный «разбери глазами»
# ──────────────────────────────────────────────────────────────────────────────

_GENERIC_LIMIT = 9000

READABILITY_HINT = ("readability-lxml не установлен — выжимка собрана нашим разбором; "
                    "`.venv/bin/pip install readability-lxml` обычно режет её вдвое")


def _readability_text(html: str) -> str | None:
    """Основной текст страницы через readability-lxml. None — «не получилось».

    Пакет опционален и импортируется лениво: ядро сборщика обязано работать
    на голом stdlib, а lxml и без того стоит ради Playwright-ветки. Ничего не
    решает сам — решение принимает _pick_generic_text.
    """
    try:
        from readability import Document  # noqa: PLC0415
    except ImportError:
        return None
    try:
        return html_to_text(Document(html).summary()) or None
    except Exception:  # noqa: BLE001 — сторонний парсер не имеет права ронять detail
        return None


def _pick_generic_text(ours: str, html: str) -> tuple[str, str | None]:
    """Выбирает выжимку: наша или readability. Возвращает (текст, что взяли).

    Живой замер на странице Greenhouse: наш html_to_text — 13 847 символов,
    из которых хвост в 6 КБ занимает американская анкета самоидентификации
    (инвалидность, ветеранский статус, восемь выпадашек «Select...»); readability —
    7 369 символов того же описания вакансии без анкеты.

    Правило приёмки жёсткое, потому что «чище» и «потеряли страницу» выглядят
    одинаково: readability берётся, только если в его выводе остались маркеры
    описания вакансии, он не короче порога каркаса И он короче нашего. Последнее
    страхует обратный случай: у страницы был аккуратный <main>, а readability
    прихватил к нему навигацию и подвал.
    """
    got = _readability_text(html)
    if got is None:
        return ours, None
    if len(got) < _SKELETON_MIN_CHARS or not _JOB_MARKERS.search(got):
        return ours, "readability-rejected"
    if len(got) >= len(ours):
        return ours, "readability-longer"
    return got, "readability"


def _detail_generic(url: str, use_render: bool = False, *,
                    cookies_from: str | None = None, use_cache: bool = False) -> Detail:
    if use_render:
        # Рендер браузером — для SPA, где обычный GET привозит пустой каркас.
        # Импорт локальный: Playwright опционален, а detail без --render обязан
        # работать на голом stdlib.
        from .render import render_page  # noqa: PLC0415
        text, final = render_page(url, cookies_from=cookies_from, use_cache=use_cache)
    else:
        text, final = fetch(url)
    m = re.search(r"<main\b[^>]*>(.*?)</main>", text, re.S | re.I)
    body = m.group(1) if m else (re.search(r"<body\b[^>]*>(.*)</body>", text, re.S | re.I)
                                 or [None, text])[1]
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    apply_url, apply_note = _apply_from_html(text, final)
    ours = html_to_text(body)
    plain, took = _pick_generic_text(ours, text)
    d = Detail(
        source="generic", url=final,
        title=H.unescape(title.group(1)).strip() if title else None,
        apply_url=apply_url, apply_note=apply_note,
        description=plain[:_GENERIC_LIMIT],
        status="generic",
        notes=["generic: парсера под источник нет — разбери текст глазами"
               + (" (отрендерено браузером)" if use_render else "")],
    )
    # Чем собран текст — в notes: выжимка читается как факт о вакансии, и по ней
    # надо понимать, что именно могло не доехать.
    if took == "readability":
        d.notes.append(f"основной текст выделен readability-lxml: "
                       f"{len(ours)} → {len(plain)} символов (обычно уходят навигация, "
                       f"подвал и анкеты формы отклика — их смотри в браузере)")
    elif took == "readability-rejected":
        d.notes.append("readability-lxml отброшен: в его выводе не осталось признаков "
                       "описания вакансии — взят наш разбор")
    if len(plain) > _GENERIC_LIMIT:
        d.notes.append(f"текст обрезан: {len(plain)} → {_GENERIC_LIMIT} символов, "
                       f"полная страница — через `scout raw` или браузер")
        if took is None:
            d.notes.append(READABILITY_HINT)
    _flag_skeleton(d, plain, text, use_render)
    return d


# Признаки, что перед нами каркас SPA, а не страница вакансии: мало текста ЛИБО
# текст есть, но состоит из меню и кнопки «Apply now». Живой случай: страница
# exness-careers.com отдавала «A great workplace… / Join us / All locations /
# Apply now», выглядела наполненной и помечалась разобранной — а вакансий в ней
# ноль. С --render та же страница даёт «39 jobs» и полный список.
_SKELETON_MIN_CHARS = 400
_JOB_MARKERS = re.compile(
    r"обязанност|требовани|условия|responsibilit|requirement|qualification|"
    r"what you.{0,20}(do|bring)|стек|зарплат|salary|опыт работы|experience with", re.I)


def _flag_skeleton(d: Detail, plain: str, html: str, use_render: bool) -> None:
    body = plain.strip()
    hint = ("и даже рендер не помог — страница может требовать входа"
            if use_render else "возьми `scout detail --render` или `scout render`")
    if not body:
        d.notes.append(f"после чистки текста не осталось — страница строится "
                       f"скриптами, {hint}")
        return
    if len(body) < _SKELETON_MIN_CHARS or not _JOB_MARKERS.search(body):
        d.status = "generic-empty"
        d.notes.append(
            f"⚠️  ПОХОЖЕ НА КАРКАС SPA: {len(body)} символов текста и ни одного "
            f"признака описания вакансии (обязанности/требования/условия). "
            f"Считать эту выжимку разобранной вакансией НЕЛЬЗЯ — {hint}")


# ──────────────────────────────────────────────────────────────────────────────
# Диспетчер
# ──────────────────────────────────────────────────────────────────────────────

def get_detail(url: str, *, use_render: bool = False,
               cookies_from: str | None = None, use_cache: bool = False) -> Detail:
    """Страница вакансии → нормализованная выжимка. Кидает FetchError/BlockedError.

    `use_render` касается только generic-случаев: у hh/habr/ATS данные приезжают
    из API или встроенного стейта, браузер там ничего не добавит. Раньше флаг
    в этих ветках молча игнорировался — пользователь ждал рендера, получал обычный
    GET и не имел никакого способа это заметить. Теперь об этом пишется в notes.
    """
    d = _dispatch(url, use_render, cookies_from=cookies_from, use_cache=use_cache)
    if use_render and d.source != "generic":
        d.notes.append("--render не применялся: данные этого источника приходят "
                       "из API/встроенного стейта, браузер их не меняет")
    if d.requirements is None and d.description:
        got = split_requirements(d.description)
        if got:
            d.requirements = got
            d.notes.append("требования выделены из описания эвристикой "
                           "(источник не отдаёт их отдельным полем)")
    return d


def _dispatch(url: str, use_render: bool, *, cookies_from: str | None = None,
              use_cache: bool = False) -> Detail:
    gkw = {"cookies_from": cookies_from, "use_cache": use_cache}
    host = _host(url)
    ats = atsapi.parse_job_url(url)
    if ats:
        kind, token, jid = ats
        if kind in _ATS_DETAIL:
            return _ATS_DETAIL[kind](token, jid)
        # smartrecruiters и прочие распознанные, но не реализованные — генериком с пометкой.
        d = _detail_generic(url, use_render, **gkw)
        d.notes.append(f"ATS {kind} распознан, но деталка по API не реализована")
        return d
    if host == "hh.ru" or host.endswith(".hh.ru"):
        if re.search(r"/vacancy/\d+", url):
            return _detail_hh(url)
    if host == "career.habr.com" and re.search(r"/vacancies/\d+", url):
        return _detail_habr(url)
    if host == "careered.io":
        return _detail_careered(url)
    if host == "getmatch.ru" and "/vacancies/" in url:
        return _detail_getmatch(url)
    return _detail_generic(url, use_render, **gkw)


# ──────────────────────────────────────────────────────────────────────────────
# Печать
# ──────────────────────────────────────────────────────────────────────────────

def _clip_lines(text: str | None, limit: int) -> list[str]:
    if not text:
        return []
    lines = [ln for ln in text.split("\n")]
    if limit and len(lines) > limit:
        return lines[:limit] + [f"… ещё {len(lines) - limit} строк (--json даст целиком)"]
    return lines


def format_detail(d: Detail, *, req_lines: int = 0, desc_lines: int = 0) -> str:
    """Текстовый вид. req_lines/desc_lines = 0 — без обрезки."""
    out = []
    out.append(f"# {d.title or '(без названия)'} — {d.company or 'компания не раскрыта'}")
    out.append(f"URL: {d.url}")
    facts = [f"деньги: {d.salary or 'не указаны'}"]
    if d.location:
        facts.append(f"локация: {d.location}")
    if d.work_format:
        facts.append(f"формат: {d.work_format}")
    if d.extra.get("experience"):
        facts.append(f"опыт: {d.extra['experience']}")
    if d.extra.get("grade") or d.extra.get("seniority"):
        facts.append(f"грейд: {d.extra.get('grade') or d.extra.get('seniority')}")
    out.append(" · ".join(facts))
    dates = [x for x in ((f"опубл. {str(d.published_at)[:10]}" if d.published_at else None),
                         (f"обновл. {str(d.updated_at)[:10]}" if d.updated_at else None)) if x]
    if dates:
        out.append(" · ".join(dates))
    if d.apply_url:
        out.append(f"Отклик: {d.apply_url}" + (f"  [{d.apply_note}]" if d.apply_note else ""))
    elif d.apply_note:
        out.append(f"Отклик: {d.apply_note}")
    if d.extra.get("key_skills") or d.extra.get("skills"):
        skills = d.extra.get("key_skills") or d.extra.get("skills")
        out.append("Скиллы: " + ", ".join(skills[:15]))
    if d.questions:
        out.append("\n## Вопросы формы отклика")
        out += [f"  {i}. {q}" for i, q in enumerate(d.questions, 1)]
    if d.requirements:
        out.append("\n## Требования")
        out += _clip_lines(d.requirements, req_lines)
    if d.description:
        out.append("\n## Описание")
        out += _clip_lines(d.description, desc_lines)
    for n in d.notes:
        out.append(f"\n⚠️  {n}")
    return "\n".join(out)


def digest(d: Detail, *, body_lines: int = 8) -> str:
    """Компактный дайджест для enrich: одна вакансия — 10–20 строк."""
    out = [f"── {d.title or '(без названия)'} — {d.company or '?'}"]
    facts = [d.salary or "вилки нет", d.location or "локация не указана"]
    if d.work_format:
        facts.append(d.work_format)
    if d.published_at:
        facts.append(f"опубл. {str(d.published_at)[:10]}")
    out.append("   " + " · ".join(facts))
    out.append(f"   {d.url}")
    if d.apply_url and d.apply_url != d.url:
        out.append(f"   отклик: {d.apply_url}" + (f" [{d.apply_note}]" if d.apply_note else ""))
    elif d.apply_note:
        out.append(f"   отклик: {d.apply_note}")
    skills = d.extra.get("key_skills") or d.extra.get("skills")
    if skills:
        out.append("   скиллы: " + ", ".join(skills[:12]))
    if d.questions:
        out.append(f"   вопросы формы ({len(d.questions)}): "
                   + "; ".join(q[:60] for q in d.questions[:4]))
    body = d.requirements or d.description
    if body:
        for ln in [x for x in body.split("\n") if x.strip()][:body_lines]:
            out.append("   | " + ln[:110])
    for n in d.notes:
        out.append(f"   ⚠️  {n}")
    return "\n".join(out)
