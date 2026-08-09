"""applyopt — маршруты отклика как ДАННЫЕ, а не как знание в голове модели.

Зачем. У одной вакансии обычно несколько путей отклика: карточка на агрегаторе,
доска ATS работодателя, страница на его собственном сайте, бот в Telegram.
Какой из них прямой, модель выясняла заново в каждой волне — и это знание
умирало вместе с контекстом. Здесь оно записывается рядом с вакансией и живёт
между прогонами.

Правило приоритета одно и то же, что во всём проекте: **контакт как можно ближе
к работодателю**. Прямой канал (сайт компании, её ATS) бьёт агрегатора всегда,
даже если агрегатор удобнее: через агрегатора отклик уходит в общую воронку,
а по прямой ссылке — в ту же систему, но с именем, которое видит нанимающий
менеджер.

Классификация домена НЕ дублируется: список агрегаторов и ATS один на проект
и живёт в `channel`. Две регулярки на один вопрос расходятся всегда — это
в проекте уже проверено на фильтре ролей.
"""

from __future__ import annotations

import re
import urllib.parse

from .channel import _AGGREGATORS, is_employer_domain
from .tgpost import fetch_apply_links

# Кто публикует. Порядок в кортеже = порядок предпочтения при выборе best_url.
EMPLOYER = "employer"        # собственный сайт работодателя
ATS = "ats"                  # доска ATS: greenhouse/lever/ashby/… — тоже прямой
AGGREGATOR = "aggregator"    # hh, habr, getmatch и прочие витрины
TELEGRAM = "telegram"        # пост или бот в Telegram
UNKNOWN = "unknown"

# Чем меньше число, тем ближе к работодателю.
_RANK = {EMPLOYER: 0, ATS: 1, AGGREGATOR: 3, TELEGRAM: 4, UNKNOWN: 5}

_ATS_DOMAINS = (
    "lever.co", "greenhouse.io", "grnh.se", "ashbyhq.com", "workable.com",
    "recruitee.com", "smartrecruiters.com", "teamtailor.com", "huntflow.ru",
    "talantix.ru", "potok.io", "myworkdayjobs.com", "bamboohr.com",
    "join.com", "personio.de", "jazzhr.com", "breezy.hr", "workatastartup.com",
)


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def classify(url: str) -> tuple[str, bool]:
    """(кто публикует, прямой ли канал).

    ATS считается ПРЯМЫМ, хотя домен принадлежит платформе: отклик через
    greenhouse работодателя попадает в его собственную воронку, а не в общую
    базу витрины. Это принципиально другой путь, чем «откликнуться на hh».
    """
    host = _host(url)
    if not host:
        return UNKNOWN, False
    if host == "t.me" or host.endswith(".t.me") or host == "telegram.me":
        return TELEGRAM, False
    if any(host == a or host.endswith("." + a) for a in _ATS_DOMAINS):
        return ATS, True
    if any(host == a or host.endswith("." + a) for a in _AGGREGATORS):
        return AGGREGATOR, False
    if is_employer_domain(host):
        return EMPLOYER, True
    return UNKNOWN, False


# Признаки, что ссылка ведёт на КОНКРЕТНУЮ вакансию, а не на витрину компании.
# Живой случай: у поста getmatch две ссылки одного ранга — на вакансию и на
# страницу компании, — и «лучшим маршрутом» становилась вторая. Формально это
# тот же работодатель, практически — страница, с которой откликнуться нельзя.
_JOB_URL = ("/vacanc", "/job", "/jobs/", "/position", "/opening", "/careers/",
            "/vakans", "/hh.ru/vacancy", "/apply")
_ORG_URL = ("/compan", "/employer", "/org", "/about", "/team")


def _specificity(url: str) -> int:
    """0 — ссылка на вакансию, 1 — неясно, 2 — страница компании."""
    u = (url or "").lower()
    if any(p in u for p in _JOB_URL):
        return 0
    if any(p in u for p in _ORG_URL):
        return 2
    return 1


def best(options: list[dict]) -> str | None:
    """Лучший маршрут: прямой канал, если он есть, иначе агрегатор.

    Порядок ключей: близость к работодателю → ссылка на саму вакансию, а не на
    витрину компании → порядок обнаружения. Последний ключ обязателен: без него
    два маршрута одного ранга упорядочивались как придётся (в базе — по rowid),
    и «лучший маршрут» менялся от прогона к прогону на одних и тех же данных.
    """
    if not options:
        return None
    ranked = sorted(
        enumerate(options),
        key=lambda p: (_RANK.get(p[1].get("publisher"), 9),
                       _specificity(p[1].get("url", "")),
                       p[1].get("rank", p[0])))
    return ranked[0][1].get("url")


def gather(row: dict, payload: dict | None = None) -> list[dict]:
    """Все известные маршруты по вакансии, без повторов и без выдумок.

    Источники маршрутов, по убыванию достоверности:
      1. `employer_url` — площадка САМА назвала сайт/доску работодателя;
      2. `apply_url` из выжимки — куда ведёт кнопка «Откликнуться»;
      3. ссылки из тела вакансии (у телеграм-постов это единственный путь);
      4. собственный url вакансии — он всегда рабочий, и потому идёт последним:
         это гарантированный, но самый дальний от работодателя маршрут.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(url: str | None, note: str) -> None:
        if not url or not str(url).startswith("http"):
            return
        u = str(url).strip()
        if u in seen:
            return
        seen.add(u)
        publisher, direct = classify(u)
        # `rank` — порядок обнаружения. Он переживает запись в базу и обратное
        # чтение, и только благодаря ему «лучший маршрут» одинаков в каждом
        # прогоне: в SQL порядок строк без ORDER BY не определён.
        out.append({"url": u, "publisher": publisher, "is_direct": direct,
                    "note": note, "rank": len(out)})

    add(row.get("employer_url"), "площадка назвала сайт работодателя")
    if payload:
        add(payload.get("apply_url"), "кнопка «Откликнуться» из выжимки")
    raw = row.get("raw")
    if isinstance(raw, dict):
        for link in (raw.get("links") or [])[:6]:
            add(link, "ссылка из текста вакансии")

    # 🔴 Телеграм-пост контактом НЕ является: настоящая ссылка спрятана внутри
    # него под словом «Откликнуться», а часть агрегаторов её ещё и подменяет
    # («Доступно в источнике» у dreamoffer), так что в базе URL нет вовсе.
    # Достаём из веб-версии поста — это единственный путь к нанимателю.
    # Живой счёт 09.08.2026: у Kaspersky внутри поста лежала прямая вакансия
    # careers.kaspersky.ru, у Авито — career.avito.com, уже мёртвая (404).
    # Раньше это делалось руками и потому делалось не всегда.
    url = str(row.get("url") or "")
    if re.match(r"https?://t\.me/(?:s/)?[A-Za-z0-9_]+/\d+", url):
        try:
            found, _why = fetch_apply_links(url)
        except Exception:  # noqa: BLE001 — сеть не должна ронять сбор маршрутов
            found = []
        for link in found[:4]:
            add(link, "ссылка отклика ИЗ ТЕЛА поста (пост — витрина)")

    add(row.get("url"), "карточка на площадке")
    return out


def render(options: list[dict], best_url: str | None = None) -> list[str]:
    """Строки для brief/card. Прямой канал помечен явно — ради него всё и есть."""
    if not options:
        return ["  маршруты отклика: не определены"]
    best_url = best_url or best(options)
    out = ["  маршруты отклика:"]
    for o in options:
        mark = "🎯" if o["url"] == best_url else ("·" if not o["is_direct"] else "→")
        direct = "прямой" if o["is_direct"] else "через витрину"
        out.append(f"    {mark} [{o['publisher']}, {direct}] {o['url'][:96]}")
        if o["url"] == best_url:
            out[-1] += "   ← ЛУЧШИЙ"
    return out
