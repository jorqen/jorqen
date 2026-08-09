"""Куда на самом деле ведёт кнопка «Откликнуться» — без нажатия на неё.

Проблема, ради которой это написано: у агрегаторов под кнопкой отклика часто спрятан
редирект на сайт работодателя, и скилл до него не доходил — боялся нажать и оставался
на витрине. В итоге в карточку шла ссылка на агрегатор там, где существовал прямой путь
в ATS компании.

Разделение, на котором всё держится:

* **Навигация** (перейти по ссылке, раскрыть контакт, пройти редирект) — это чтение.
  Ничего не отправляется, делать можно и нужно.
* **Отправка** (submit формы, POST отклика, callback-кнопка) — действие от имени
  пользователя. Не делается никогда, даже если очень похоже, что «просто откроется».

Резолвер работает только в первой половине: GET и HEAD, никаких POST, никаких форм.
Всё, что он не смог выяснить чтением, честно помечается `unknown` — догадка тут
дороже незнания.
"""

from __future__ import annotations

import html as H
import json
import re
import urllib.parse
from dataclasses import dataclass, asdict

from .net import FetchError, fetch

# Хосты ATS: попасть сюда — значит найти собственный канал найма работодателя,
# то есть приоритет №1 из «Контакт как можно ближе к работодателю».
ATS_HOSTS = {
    "boards.greenhouse.io": "Greenhouse", "job-boards.greenhouse.io": "Greenhouse",
    "boards-api.greenhouse.io": "Greenhouse", "job-boards.eu.greenhouse.io": "Greenhouse EU",
    "jobs.lever.co": "Lever", "hire.lever.co": "Lever",
    "jobs.ashbyhq.com": "Ashby", "apply.workable.com": "Workable",
    "recruitee.com": "Recruitee", "huntflow.io": "Huntflow", "huntflow.ru": "Huntflow",
    "smartrecruiters.com": "SmartRecruiters", "teamtailor.com": "Teamtailor",
    "personio.de": "Personio", "join.com": "JOIN", "applytojob.com": "JazzHR",
    "myworkdayjobs.com": "Workday", "successfactors.com": "SuccessFactors",
    "e.huntflow.ru": "Huntflow", "friendlyjobs.ru": "FriendlyJobs",
}

# Витрины: попасть сюда — значит остаться у посредника и копать дальше.
AGGREGATOR_HOSTS = {
    "hh.ru", "career.habr.com", "geekjob.ru", "hirehi.ru", "careered.io",
    "getmatch.ru", "hack-offer.tech", "find.dreamoffer.app", "wantapply.com",
    "shadowhint.com", "rabota.ru", "linkedin.com", "t.me", "relocate.me",
}

APPLY_WORDS = re.compile(
    r"откликнут|отклик|подать\s*заяв|apply|apply now|submit application|"
    r"перейти\s*к\s*вакансии|на\s*сайте\s*компании|corporate\s*website|"
    r"вакансия\s*на\s*сайте|apply\s*on",
    re.I,
)

# Атрибуты, в которые агрегаторы кладут настоящий адрес отклика.
URL_ATTRS = ("data-apply-link", "data-apply-url", "data-url", "data-href",
             "data-external-url", "data-redirect", "data-target-url", "data-link")

# Те же имена площадки используют и как БУЛЕВ ФЛАГ: у hirehi кнопка размечена
# `data-apply-link="true"`, и «true» склеивалось с адресом страницы в
# `https://hirehi.ru/development/true` — призрак, который попадал в маршруты
# каждой вакансии площадки и обходился краулером впустую (09.08.2026).
# Адрес обязан нести признак адреса: схему, слэш или точку домена.
_NOT_A_URL = re.compile(r"^(?:true|false|yes|no|on|off|1|0|null|none|undefined)$", re.I)


def _looks_like_url(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and not _NOT_A_URL.match(v) and any(c in v for c in "/.?:")


@dataclass
class Target:
    kind: str            # external | ats | aggregator | form-submit | js-only | unknown
    url: str | None
    label: str | None = None
    note: str | None = None
    safe_to_open: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def classify(url: str) -> tuple[str, str | None]:
    h = _host(url)
    if not h:
        return "unknown", None
    for ats_host, name in ATS_HOSTS.items():
        if h == ats_host or h.endswith("." + ats_host):
            return "ats", name
    if any(h == a or h.endswith("." + a) for a in AGGREGATOR_HOSTS):
        return "aggregator", None
    return "external", None


def _abs(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, H.unescape(href.strip()))


def _in_form(html: str, pos: int) -> bool:
    """Лежит ли элемент внутри <form>. Кнопка в форме = отправка отклика, её не трогаем."""
    before = html[:pos]
    return before.rfind("<form") > before.rfind("</form>")


def find_targets(html: str, page_url: str) -> list[Target]:
    """Достаёт кандидатов в «куда ведёт отклик», ничего не нажимая."""
    targets: list[Target] = []
    seen: set[str] = set()

    def add(t: Target):
        k = f"{t.kind}:{t.url}"
        if k not in seen:
            seen.add(k)
            targets.append(t)

    # 1. Ссылки с текстом про отклик.
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html, re.S | re.I):
        attrs, inner = m.group(1), _strip(m.group(2))
        href = _one(r'href\s*=\s*["\']([^"\']+)["\']', attrs)
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            # href="#" + JS-обработчик: адрес подставляется на лету, из разметки не виден.
            if href and APPLY_WORDS.search(inner or ""):
                for a in URL_ATTRS:
                    hidden = _one(rf'{a}\s*=\s*["\']([^"\']+)["\']', attrs)
                    if hidden and _looks_like_url(hidden):
                        u = _abs(page_url, hidden)
                        kind, label = classify(u)
                        add(Target(kind, u, inner, f"адрес взят из {a}"))
                        break
                else:
                    if href.startswith("mailto:"):
                        add(Target("external", href, inner, "почта работодателя"))
                    else:
                        add(Target("js-only", None, inner,
                                   "href-заглушка + JS: адрес в разметке отсутствует, "
                                   "узнать без нажатия нельзя"))
            continue
        if not APPLY_WORDS.search(inner or "") and not APPLY_WORDS.search(attrs):
            continue
        u = _abs(page_url, href)
        if _host(u) == _host(page_url):
            continue  # внутренняя ссылка витрины — не цель
        kind, label = classify(u)
        add(Target(kind, u, inner, label))

    # 2. Кнопки: отличаем «навигацию» от «отправки».
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S | re.I):
        attrs, inner = m.group(1), _strip(m.group(2))
        if not APPLY_WORDS.search(inner or "") and not APPLY_WORDS.search(attrs):
            continue
        hidden = next((v for v in
                       (_one(rf'{a}\s*=\s*["\']([^"\']+)["\']', attrs) for a in URL_ATTRS)
                       if v and _looks_like_url(v)), None)
        if hidden:
            u = _abs(page_url, hidden)
            kind, _ = classify(u)
            add(Target(kind, u, inner, "адрес из data-атрибута кнопки"))
        elif _in_form(html, m.start()):
            add(Target("form-submit", None, inner,
                       "кнопка внутри <form> — нажатие ОТПРАВИТ отклик, не трогать",
                       safe_to_open=False))
        else:
            add(Target("js-only", None, inner, "кнопка без адреса, обработчик на JS"))

    # 3. Встроенный JSON стейт — там адрес лежит чаще всего.
    for u in _from_embedded_json(html, page_url):
        kind, label = classify(u)
        add(Target(kind, u, None, "адрес из встроенного JSON страницы"))

    return targets


# Ключи, под которыми лежит именно адрес отклика или сайт нанимателя.
# Общих `link`/`url` здесь намеренно нет: под ними у площадок лежит вообще всё —
# соцсети, логотипы, формы обратной связи. С ними резолвер один раз выдал
# `setka.ru` как «лучший путь» по вакансии Т-Банка, то есть соврал уверенным тоном.
_JSON_KEYS = ("apply_url", "applyUrl", "applicationUrl", "apply_link", "applyLink",
              "externalUrl", "external_url", "sourceUrl", "source_url",
              "redirectUrl", "originalUrl", "vacancy_url", "vacancyUrl",
              "companySiteUrl", "company_site_url", "employerUrl", "careersUrl",
              "hostedUrl", "careers_url", "careers_apply_url", "jobUrl")

# Хосты, которые не могут быть каналом найма, как бы они ни назывались в JSON.
# Публичное имя: тем же списком отсеивает мусор обход ссылок (`crawl`), и второй
# копии этого списка в проекте быть не должно.
NOISE_HOSTS = re.compile(
    r"(^|\.)(vk\.com|dzen\.ru|ok\.ru|max\.ru|youtube\.com|facebook\.com|instagram\.com|"
    r"twitter\.com|x\.com|setka\.ru|apple\.com|play\.google\.com|itunes\.apple\.com|"
    r"gstatic\.com|googleapis\.com|googletagmanager\.com|yandex\.(ru|net)|"
    r"cdn\w*\.\w+|licdn\.com|habrastorage\.org)$", re.I)


def _from_embedded_json(html: str, page_url: str) -> list[str]:
    """Ищет URL отклика во встроенных стейтах (__NEXT_DATA__, ld+json, HH-Lux, RSC).

    Берём только адреса под «прикладными» ключами и выбрасываем соцсети с CDN:
    лучше не найти ничего, чем предложить откликнуться в чужую соцсеть.
    """
    found: list[str] = []
    blobs: list[str] = []
    for pat in (r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>'):
        blobs += re.findall(pat, html, re.S)
    for raw in blobs:
        try:
            data = json.loads(H.unescape(raw))
        except Exception:  # noqa: BLE001
            continue
        found += _walk(data)
    # RSC-пейлоад Next.js: JSON там экранирован внутри строки, обычным парсером не берётся.
    for m in re.finditer(r'\\?"(?:' + "|".join(_JSON_KEYS) + r')\\?"\s*:\s*\\?"(https?://[^"\\]+)',
                         html):
        found.append(m.group(1))

    out, seen = [], set()
    for u in found:
        u = H.unescape(u)
        h = _host(u)
        if (u.startswith("http") and h and h != _host(page_url)
                and not NOISE_HOSTS.search(h) and u not in seen):
            seen.add(u)
            out.append(u)
    return out[:12]


def _walk(node, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _JSON_KEYS and isinstance(v, str) and v.startswith("http"):
                out.append(v)
            else:
                out += _walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node[:200]:
            out += _walk(v, depth + 1)
    return out


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _one(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.S | re.I)
    return m.group(1) if m else None


# Редирект, который не проходит ни один HTTP-клиент: он написан в разметке.
# Регулярка публичная и одна на проект — по ней же проходит цепочки обход
# ссылок (`crawl`), а разошедшиеся копии одного разбора у нас уже были.
META_REFRESH = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'>]+)',
    re.I)


def follow(url: str, max_hops: int = 5) -> dict:
    """Проходит цепочку редиректов GET-запросами. Ничего не отправляет.

    Короткие ссылки ботов и `/go/<id>` агрегаторов разворачиваются здесь — именно так
    витрина превращается в адрес работодателя.
    """
    chain, current = [url], url
    for _ in range(max_hops):
        try:
            text, final = fetch(current, timeout=25, retries=1)
        except FetchError as e:
            chain.append(f"[{e.reason}]")
            break
        if final != current:
            chain.append(final)
            current = final
        # meta refresh — редирект, который curl не проходит
        meta = META_REFRESH.search(text or "")
        if meta:
            current = _abs(current, meta.group(1))
            chain.append(current)
            continue
        break
    kind, label = classify(current)
    return {"final_url": current, "chain": chain, "kind": kind, "ats": label}


def resolve(page_url: str, *, follow_redirects: bool = True) -> dict:
    """Главная точка входа: страница вакансии → куда ведёт отклик."""
    html, final_url = fetch(page_url)
    targets = find_targets(html, final_url)

    if follow_redirects:
        for t in targets:
            if t.url and t.kind in ("external", "aggregator") and t.safe_to_open:
                if not t.url.startswith("mailto:"):
                    hop = follow(t.url)
                    if hop["final_url"] != t.url:
                        t.note = f"{t.note or ''} → редирект на {hop['final_url']}".strip()
                    if hop["kind"] == "ats":
                        t.kind, t.url, t.label = "ats", hop["final_url"], hop["ats"]

    order = {"ats": 0, "external": 1, "aggregator": 2, "js-only": 3, "form-submit": 4,
             "unknown": 5}
    targets.sort(key=lambda t: order.get(t.kind, 9))

    best = next((t for t in targets if t.kind in ("ats", "external")), None)
    return {
        "page": final_url,
        "best": best.to_dict() if best else None,
        "targets": [t.to_dict() for t in targets],
        "verdict": _verdict(best, targets),
    }


def _verdict(best: Target | None, targets: list[Target]) -> str:
    if best and best.kind == "ats":
        return (f"Прямой путь в ATS работодателя ({best.label}). Это приоритет №1 — "
                f"веди в карточке сюда, а не на агрегатор.")
    if best and best.kind == "external":
        return "Ссылка уходит на внешний домен — проверь, это сайт работодателя или ещё посредник."
    if any(t.kind == "form-submit" for t in targets):
        return ("Отклик оформлен формой на самой площадке: нажатие ОТПРАВЛЯЕТ заявку. "
                "Не нажимать. В карточку идёт ссылка на вакансию у агрегатора плюс "
                "название работодателя отдельной строкой — по нему пользователь зайдёт напрямую.")
    if any(t.kind == "js-only" for t in targets):
        return ("Адрес подставляется на лету скриптом, в разметке его нет — узнать без "
                "нажатия нельзя, значит не узнаём. В карточку: ссылка на вакансию "
                "у агрегатора + раскрытый работодатель.")
    return "Кандидатов на отклик в разметке не нашлось — открывай страницу браузером и смотри глазами."
