"""channel — поиск прямого канала найма работодателя БЕЗ участия модели.

Самый дорогой этап прошлого конвейера. Замер 04.08.2026: 1,72 млн токенов на
18 компаний — подагенты перебирали `<домен>/careers`, `career.<домен>`,
`job.<домен>`, нюхали ATS и искали HR-почту. Перебор кандидатов по шаблону,
проверка HTTP-кодом и поиск почты регуляркой — работа для кода, а не для LLM.

Что делает модуль:

1. **Угадывает домен работодателя** — из поля `employer_url` в базе (его отдают
   многие площадки), иначе из ссылки отклика в выжимке.
2. **Перебирает шаблоны карьерных адресов** — те, что реально встречались:
   `/careers`, `/career`, `/vacancies`, `/vacancy`, `/jobs`, `/job`, `/rabota`,
   `/team`, плюс поддомены `career.`, `careers.`, `job.`, `jobs.`, `rabota.`,
   `team.`, `hr.`.
3. **Нюхает ATS** по разметке найденной страницы (Greenhouse, Lever, Ashby,
   Workable, Recruitee, SmartRecruiters, Huntflow, Talantix, Potok, FriendWork).
4. **Собирает почты найма** — `hr@`, `job@`, `jobs@`, `career@`, `careers@`,
   `join@`, `work@`, `vacancy@`, `recruit@` на домене работодателя.
5. **Кладёт находку в кэш** `employer_channel`, откуда её берут `shortlist`
   и `brief`.

Чего модуль НЕ делает: не решает, что канал «тот самый» — он отдаёт кандидатов
с доказательствами (HTTP-код, найденный ATS, встреченные почты). Решение, писать
ли туда, остаётся за моделью, но перебирать сотни URL она больше не должна.
"""

from __future__ import annotations

import argparse

import random
import re
import time
import sys
import urllib.parse

from .net import BlockedError, FetchError, fetch

# Пути и поддомены, реально встречавшиеся у работодателей из прошлых волн.
_PATHS = ("/careers", "/career", "/vacancies", "/vacancy", "/jobs", "/job",
          "/rabota", "/team", "/careers/all", "/about/career")
_SUBS = ("career", "careers", "job", "jobs", "rabota", "team", "hr", "work")

# Маркеры ATS в разметке карьерной страницы: если он есть, у компании есть доска,
# и вакансия почти наверняка лежит там, а не только на агрегаторе.
_ATS_MARKERS = (
    ("greenhouse", re.compile(r"greenhouse\.io|boards\.greenhouse|job-boards\.greenhouse", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co|lever\.co/", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com|ashbyhq\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com|workable\.com", re.I)),
    ("recruitee", re.compile(r"\.recruitee\.com", re.I)),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com", re.I)),
    ("huntflow", re.compile(r"huntflow\.(?:ru|io)", re.I)),
    ("talantix", re.compile(r"talantix\.ru", re.I)),
    ("potok", re.compile(r"potok\.io", re.I)),
    ("friendwork", re.compile(r"friend\.work|friendwork", re.I)),
    ("hh-widget", re.compile(r"hh\.ru/employer/\d+", re.I)),
)

_MAIL_RE = re.compile(
    r"\b((?:hr|job|jobs|career|careers|join|work|vacancy|vacancies|recruit|"
    r"recruiting|people|talent)[a-z0-9._%+-]*@[a-z0-9.-]+\.[a-z]{2,})", re.I)

# Признаки того, что на странице есть вакансии, а не просто «мы нанимаем».
_HAS_JOBS = re.compile(
    r"ваканси|vacanc|открытые позиции|open positions|apply now|откликнуться|"
    r"join (?:our|the) team|мы ищем|we are hiring|we're hiring", re.I)


def domain_of(url: str | None) -> str:
    """Голый домен из URL. Пусто — если URL не разобрать."""
    if not url:
        return ""
    try:
        host = urllib.parse.urlsplit(url if "//" in url else f"//{url}").netloc
    except ValueError:
        return ""
    return host.lower().removeprefix("www.").split(":")[0]


# Домены агрегаторов: их careers-страница — это НЕ канал работодателя.
_AGGREGATORS = {
    "hh.ru", "career.habr.com", "habr.com", "getmatch.ru", "geekjob.ru",
    "hirehi.ru", "careered.io", "rabota.ru", "hack-offer.tech", "wantapply.com",
    "linkedin.com", "jobicy.com", "arbeitnow.com", "himalayas.app",
    "relocate.me", "jobsdb.com", "europa.eu", "news.ycombinator.com",
    "t.me", "dreamoffer.app", "shadowhint.com",
    # Домены самих ATS: карьерная страница на них принадлежит платформе,
    # а не работодателю. Без этого зонд шёл на careers.job-boards.greenhouse.io
    # и мог засчитать «ATS: greenhouse» просто потому, что домен греенхаусовский
    # (345 живых employer_url на job-boards.greenhouse.io в базе).
    "lever.co", "greenhouse.io", "grnh.se", "ashbyhq.com", "workable.com",
    "recruitee.com", "smartrecruiters.com", "teamtailor.com", "huntflow.ru",
    "talantix.ru", "potok.io", "myworkdayjobs.com", "bamboohr.com",
    # Государственные порталы вакансий — тоже витрины.
    "arbeitsagentur.de", "pole-emploi.fr", "arbetsformedlingen.se",
    "aplitrak.com", "h.careers", "friend.work",
}


def is_employer_domain(dom: str) -> bool:
    """Домен работодателя, а не витрины. Проверка обязательна: раньше careers
    агрегатора уходил в кэш как «канал компании»."""
    if not dom or "." not in dom:
        return False
    return not any(dom == a or dom.endswith("." + a) for a in _AGGREGATORS)


# Страницы, где почта найма живёт даже когда карьерного раздела нет вовсе.
# Живой случай: у БЮРО 1440 нет ни /careers, ни ATS, а `join@1440.space` стоит
# на главной с меткой «HR» — зонд без главной объявлял бы «канала нет».
_CONTACT_PAGES = ("/", "/contacts", "/contact", "/about", "/kontakty")


def candidates(domain: str) -> list[str]:
    """Адреса-кандидаты в порядке правдоподобия, ярусами.

    Ярусы, а не сплошной список, — из-за потолка `MAX_PROBES`. Раньше порядок
    был «все поддомены со всеми путями, потом пути основного домена», и первые
    двадцать четыре адреса приходились на поддомены: любой потолок ниже 25
    отрезал бы `example.com/careers` целиком — самый частый случай из всех.
    Обрезка, съедающая наиболее вероятного кандидата, — это тихая потеря,
    а не экономия.

    Порядок ярусов:
      1. корни карьерных поддоменов (`career.<домен>/`) — у российских компаний
         встречаются чаще, чем `/careers`;
      2. карьерные пути основного домена;
      3. поддомен + путь: у «Фланта» сайт на job.flant.ru, а список вакансий —
         на job.flant.ru/vacancies/, и зонд по одному корню возвращал «канала
         нет» при живом канале;
      4. www-варианты;
      5. главная и контакты — источник почты найма, а не вакансий, поэтому последние.
    """
    base = domain.removeprefix("www.")
    urls: list[str] = [f"https://{sub}.{base}/" for sub in _SUBS]
    urls += [f"https://{base}{p}" for p in _PATHS]
    for sub in _SUBS:
        urls += [f"https://{sub}.{base}/vacancies/", f"https://{sub}.{base}/jobs/"]
    urls += [f"https://www.{base}{p}" for p in _PATHS[:4]]
    urls += [f"https://{base}{p}" for p in _CONTACT_PAGES]
    return list(dict.fromkeys(urls))


# Сколько адресов зондировать по одной компании и с какой паузой.
#
# Это вежливость к чужому домену, а не оптимизация: `candidates()` отдаёт 43
# адреса, и все они летят на ОДИН сайт подряд — `net.fetch` пауз между запросами
# не держит вовсе. Сорок три запроса в секунду по чужому домену это ровно то
# поведение, из-за которого появляются антибот-стены, на которые мы потом жалуемся.
#
# Потолок в 26 покрывает первый и второй ярусы ЦЕЛИКОМ (все восемь карьерных
# поддоменов + все десять путей основного домена) и начало третьего. Отрезается
# хвост третьего яруса (пути у редких поддоменов вроде `hr.`/`work.`), www-дубли
# и страницы контактов: первые повторяют уже проверенное, последние дают почту,
# а не вакансии. Что именно отсечено — попадает в `note`, а не пропадает молча.
MAX_PROBES = 26
PROBE_PAUSE = (0.25, 0.6)


def _pause() -> None:
    time.sleep(random.uniform(*PROBE_PAUSE))


def probe(url: str, *, timeout: int = 12) -> dict | None:
    """Один кандидат: жив ли и похоже ли на страницу вакансий.

    Возвращает None, если страницы нет. Стена — это НЕ «нет страницы»:
    возвращаем находку с пометкой, чтобы её добрал браузерный слой."""
    try:
        html, final = fetch(url, timeout=timeout, retries=0)
    except BlockedError:
        return {"url": url, "status": "АНТИБОТ", "ats": None, "mails": [],
                "has_jobs": None,
                "why": "страница есть, но за антибот-стеной — добери `scout render`"}
    except FetchError:
        return None
    if looks_like_shell(html):
        # 200 + пустой каркас SPA — это «не знаю», а не «раздела нет». Молчать
        # тут нельзя: так терялась собственная страница вакансий Каргономики.
        return {"url": url, "status": "КАРКАС SPA", "ats": None, "mails": [],
                "has_jobs": None, "contact_page": False,
                "why": "сервер отдал каркас без текста — добери `--render`"}
    ats = next((name for name, rx in _ATS_MARKERS if rx.search(html)), None)
    mails = list(dict.fromkeys(m.lower() for m in _MAIL_RE.findall(html)))
    has_jobs = bool(_HAS_JOBS.search(html))
    path = urllib.parse.urlsplit(url).path.rstrip("/") or "/"
    if path in _CONTACT_PAGES and not mails:
        # На главной слова «вакансии» есть у половины сайтов — это ещё не канал.
        # Засчитываем её, только если нашлась настоящая почта найма.
        return None
    if not (ats or mails or has_jobs):
        return None                      # живая, но не про найм — не кандидат
    return {"url": final or url, "status": "ok", "ats": ats, "mails": mails[:4],
            "has_jobs": has_jobs, "contact_page": path in _CONTACT_PAGES,
            "why": ", ".join(x for x in (
                f"ATS: {ats}" if ats else "",
                "есть признаки вакансий" if has_jobs else "",
                f"почты: {', '.join(mails[:2])}" if mails else "") if x)}


# Каркас SPA: сервер отдал 200, но текста нет — судить по нему нельзя.
_SPA_SHELL = re.compile(r"__nuxt|__next|<div id=\"root\"|<div id=\"app\"", re.I)


def looks_like_shell(html: str) -> bool:
    """Пустой каркас SPA. Отдельная проверка нужна, потому что 200 + пустая
    страница неотличимы от «раздела нет», а это разные ответы: Каргономика
    (Nuxt) так теряла свою же страницу вакансий."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html or "",
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return len(" ".join(text.split())) < 400 and bool(_SPA_SHELL.search(html or ""))


def find(company: str, *, domain: str = "", limit: int = 4,
         timeout: int = 12, render: bool = False) -> dict:
    """Кандидаты в канал найма для компании. Сеть — да, модель — нет."""
    dom = domain_of(domain) or domain_of(company)
    result = {"company": company, "domain": dom, "hits": [], "checked": 0,
              "note": ""}
    if not dom:
        result["note"] = ("домена нет: передай --site <домен компании> "
                          "(в базе он лежит в employer_url, если площадка его дала)")
        return result
    if not is_employer_domain(dom):
        result["note"] = f"{dom} — агрегатор, а не работодатель: канал искать не здесь"
        return result
    seen_final: set[str] = set()
    all_candidates = candidates(dom)
    probes = all_candidates[:MAX_PROBES]
    if len(all_candidates) > len(probes):
        # Обрезка обязана быть ВИДНОЙ: «канала нет» после неполного обхода и
        # «канала нет» после полного — разные утверждения.
        result["skipped"] = len(all_candidates) - len(probes)
    for i, url in enumerate(probes):
        if i:
            _pause()
        result["checked"] += 1
        hit = probe(url, timeout=timeout)
        if hit:
            # Разные кандидаты часто редиректят в один адрес (`/career` → `/careers`).
            # Дубль в выдаче выглядит как два независимых подтверждения — враньё.
            key = (hit["url"] or "").rstrip("/")
            if key in seen_final:
                continue
            seen_final.add(key)
            result["hits"].append(hit)
            strong = [h for h in result["hits"]
                      if h.get("status") == "ok"
                      and (h.get("ats")
                           or (h.get("has_jobs") and not h.get("contact_page")))]
            # Сильная находка закрывает вопрос: дальше перебирать 40 адресов
            # незачем, а зонд по чужому домену — это ещё и трафик к площадке.
            if strong or len(result["hits"]) >= limit:
                break
    if not result["hits"] and render:
        # Ничего не нашлось stdlib-слоем — почти всегда это SPA. Добираем
        # настоящим браузером, но только по трём самым правдоподобным адресам:
        # рендер дорогой, а перебирать им все 22 кандидата незачем.
        #
        # ОДНИМ контекстом на все три. Раньше здесь звался `probe_rendered`
        # по одному адресу за раз, и каждый вызов открывал свой браузер:
        # три запуска на компанию, шестьдесят на двадцать компаний.
        urls = [f"https://{dom}/vacancies", f"https://{dom}/careers",
                f"https://career.{dom}/"]
        for hit in probe_rendered_many(urls):
            if hit:
                result["hits"].append(hit)
                result["checked"] += 1
                if len(result["hits"]) >= 2:
                    break
        if result["hits"]:
            result["note"] = "найдено рендером: stdlib видел только каркас SPA"
    if not result["hits"]:
        result["note"] = result["note"] or (
            "карьерных страниц по шаблонам нет — у компании либо их нет вовсе, "
            "либо найм идёт через площадку; это тоже ответ"
            + ("" if render else ". Страница может быть SPA — повтори с --render"))
        if result.get("skipped"):
            result["note"] += (f". Проверено {len(probes)} адресов из "
                               f"{len(all_candidates)} — остальные отсечены "
                               f"потолком MAX_PROBES (хвост редких поддоменов, "
                               f"www-дубли, страницы контактов)")
    return result


def _rendered_hit(html: str, final: str, url: str, state: str) -> dict | None:
    """Разбор отрендеренной страницы в находку. Вынесен, чтобы одиночный и
    пакетный зонды судили по ОДНИМ правилам: две копии этой логики разошлись бы."""
    if state != "clear" or not html:
        return None
    ats = next((name for name, rx in _ATS_MARKERS if rx.search(html)), None)
    mails = list(dict.fromkeys(m.lower() for m in _MAIL_RE.findall(html)))
    if not (ats or mails or _HAS_JOBS.search(html)):
        return None
    return {"url": final or url, "status": "ok", "ats": ats, "mails": mails[:4],
            "has_jobs": bool(_HAS_JOBS.search(html)),
            "why": "подтверждено рендером (SPA)"
                   + (f", ATS: {ats}" if ats else "")}


def probe_rendered(url: str, *, wait: float = 3.0) -> dict | None:
    """Тот же зонд, но через настоящий браузер: для SPA и страниц за стеной."""
    try:
        from .wall import fetch_through  # noqa: PLC0415
        html, final, state = fetch_through(url, wait=wait, ask_human=False)
    except Exception:  # noqa: BLE001 — нет браузера/профиль занят: не наша беда
        return None
    return _rendered_hit(html, final, url, state)


def probe_rendered_many(urls: list[str], *, wait: float = 3.0) -> list[dict | None]:
    """Пакетный зонд ОДНИМ браузерным контекстом. Порядок сохраняется.

    Один запуск браузера на компанию вместо трёх. Разница не косметическая:
    профиль браузера один и он под локом, поэтому каждый лишний запуск — это
    и секунды ожидания, и риск ProfileBusy у соседнего этапа.
    """
    try:
        from .wall import fetch_many_through  # noqa: PLC0415
        results = fetch_many_through(urls, wait=wait)
    except Exception:  # noqa: BLE001 — нет браузера/профиль занят: не наша беда
        return [None] * len(urls)
    return [_rendered_hit(html, final, url, state)
            for url, (html, final, state) in zip(urls, results)]


def best(hits: list[dict]) -> dict | None:
    """Лучший кандидат: ATS > страница с вакансиями > страница с почтой найма.

    Каркасы SPA и страницы за стеной кандидатами не считаются: по ним ещё
    ничего не известно, и записывать их в кэш как «канал найма» — враньё."""
    hits = [h for h in hits if h.get("status") == "ok"]
    for h in hits:
        if h.get("ats"):
            return h
    for h in hits:
        if h.get("has_jobs"):
            return h
    return hits[0] if hits else None


def companies_without_channel(db: str, *, days: int, top: int) -> list[str]:
    """Компании из топа шорт-листа, у которых прямого канала ещё нет.

    Ровно тот список, который `wave` уже печатает строкой «нет прямого канала
    найма у N компаний» — но печатает НАЗВАНИЯМИ, и модель звала `channel` по
    одному на каждую (18 вызовов на волне 04.08.2026). Считает его алгоритм,
    значит и подставлять его должен алгоритм.
    """
    from . import shortlist, store  # noqa: PLC0415

    sl = shortlist.build(db, since=store.since_arg(f"{days}d"), by="seen",
                         sources=None, limit=0)
    seen: dict[str, None] = {}
    for r in (sl.get("rows") or [])[:top]:
        name = (r.get("company") or "").strip()
        if name and not r.get("_channel"):
            seen.setdefault(name, None)
    return list(seen)


def cli(args) -> int:
    from datetime import datetime, timezone  # noqa: PLC0415

    # Пачкой: список компаний считает шорт-лист, а не человек глазами.
    if getattr(args, "from_shortlist", False):
        names = companies_without_channel(args.db, days=getattr(args, "days", 3),
                                          top=getattr(args, "top", 30))
        if not names:
            print("у всех компаний топа канал уже есть — искать нечего")
            return 0
        print(f"компаний без канала: {len(names)}")
        worst = 0
        for i, name in enumerate(names, 1):
            print(f"\n[{i}/{len(names)}] {name}")
            one = argparse.Namespace(**{**vars(args), "company": name,
                                        "site": None, "from_shortlist": False})
            worst = max(worst, cli(one))
        return worst

    # Имя стало необязательным вместе с --from-shortlist, поэтому его отсутствие
    # надо назвать словами: без этого команда печатала заголовок «# None» и
    # выглядела как поломка поиска, а не как забытый аргумент.
    if not getattr(args, "company", None):
        print("укажи компанию: `scout channel \"<название>\"` — или возьми все "
              "сразу: `scout channel --from-shortlist --save`", file=sys.stderr)
        return 2

    from . import shortlist, store  # noqa: PLC0415

    domain = args.site or ""
    if not domain:
        with store.connect(args.db) as conn:
            # Сначала точное совпадение имени, и только потом — подстрока:
            # LIKE-поиск даёт те же коллизии, что и в истории откликов
            # (ALTEN ↔ Altenar), только здесь ценой будет чужой домен.
            row = conn.execute(
                "SELECT employer_url FROM vacancy WHERE lower(company) = lower(?) "
                "AND employer_url IS NOT NULL AND employer_url != '' "
                "ORDER BY last_seen DESC LIMIT 1", (args.company,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT employer_url FROM vacancy WHERE company LIKE ? "
                    "AND employer_url IS NOT NULL AND employer_url != '' "
                    "ORDER BY last_seen DESC LIMIT 1",
                    (f"%{args.company}%",)).fetchone()
        if row:
            domain = row[0]
            print(f"домен взят из базы: {domain}", file=sys.stderr)

    res = find(args.company, domain=domain, timeout=args.timeout,
               render=args.render)
    print(f"# {res['company']} · домен: {res['domain'] or '—'} · "
          f"проверено адресов: {res['checked']}")
    if res["note"]:
        print(f"  {res['note']}")
    for h in res["hits"]:
        mark = "🎯" if h is best(res["hits"]) else " ·"
        print(f"{mark} [{h['status']}] {h['url']}\n     {h['why']}")
        for m in h["mails"]:
            print(f"     почта найма: {m}")

    pick = best(res["hits"])
    if pick and args.save:
        # На главной ценна не сама страница, а почта найма на ней: писать надо
        # на join@/hr@, а не «зайди на сайт компании».
        if pick.get("contact_page") and pick.get("mails"):
            kind, channel = "email", pick["mails"][0]
        elif pick.get("ats"):
            kind, channel = "ats", pick["url"]
        elif pick.get("has_jobs"):
            kind, channel = "careers", pick["url"]
        else:
            kind = "email" if pick.get("mails") else "careers"
            channel = pick["mails"][0] if pick.get("mails") else pick["url"]
        with store.connect(args.db) as conn:
            conn.execute(
                "INSERT INTO employer_channel (company_key, company, channel, kind, "
                "evidence, checked_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(company_key) DO UPDATE SET channel=excluded.channel, "
                "kind=excluded.kind, evidence=excluded.evidence, "
                "checked_at=excluded.checked_at",
                (shortlist.norm(args.company), args.company, channel, kind,
                 f"найдено зондированием: {pick['why']}",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
            conn.commit()
        print(f"\nв кэш: {args.company} → {channel} ({kind})")
    return 0 if res["hits"] else 1
