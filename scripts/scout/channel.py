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

# Общая приёмная компании: писать туда хуже, чем в отдел найма, но лучше, чем
# никуда. 🔴 Слова владельца: «если не можешь найти конкретную ссылку — можно
# просто отправить резюме им на почту». Живой счёт 09.08.2026: у Remoby весь
# контакт — `info@remoby.com` в подвале главной, почты найма нет вовсе, и зонд
# отвечал «канала нет» при полностью доступном канале.
_ANY_MAIL_RE = re.compile(
    r"\b((?:info|hello|contact|contacts|office|mail|welcome|ask|team)"
    r"[a-z0-9._%+-]*@[a-z0-9.-]+\.[a-z]{2,})", re.I)

# Признаки того, что на странице есть вакансии, а не просто «мы нанимаем».
_HAS_JOBS = re.compile(
    r"ваканси|vacanc|открытые позиции|open positions|apply now|откликнуться|"
    r"join (?:our|the) team|мы ищем|we are hiring|we're hiring", re.I)


# Как выглядит домен: метки через точку, latin/цифры/дефис, зона от двух букв.
# Нужна потому, что `domain_of` зовут и от НАЗВАНИЯ КОМПАНИИ тоже, а название
# доменом не является. Живой случай 08.08.2026: «P&P Solutions» проезжало через
# urlsplit как netloc, `is_employer_domain` отвечал «нет», и пользователь читал
# «p&p solutions — агрегатор, а не работодатель» — уверенный неверный вердикт
# на самом ценном пути (поиск канала ближе к работодателю).
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _org_of(host: str) -> str:
    """Две последние метки домена: careers.remoby.com → remoby.com.

    Нужна, чтобы общая почта засчитывалась только СВОЯ. `info@` с чужого
    домена — это тот же случай, что чужая почта найма с баннера рейтинга.
    """
    parts = [p for p in (host or "").lower().removeprefix("www.").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def domain_of(url: str | None) -> str:
    """Голый домен из URL. Пусто — если это не домен.

    🔴 Пусто и для того, что доменом лишь притворяется: «P&P Solutions» или
    «ООО Ромашка». Раньше такое возвращалось как есть и уезжало в проверку на
    агрегатор, а та отвечала «не работодатель» — то есть догадка выдавалась за
    факт. Здесь «не знаю» честнее: вызывающий тогда просит `--site`.
    """
    if not url:
        return ""
    try:
        host = urllib.parse.urlsplit(url if "//" in url else f"//{url}").netloc
    except ValueError:
        return ""
    host = host.lower().removeprefix("www.").split(":")[0].strip().rstrip(".")
    return host if _DOMAIN_RE.match(host) else ""


# Домены агрегаторов: их careers-страница — это НЕ канал работодателя.
_AGGREGATORS = {
    "hh.ru", "career.habr.com", "habr.com", "getmatch.ru", "geekjob.ru",
    "hirehi.ru", "careered.io", "rabota.ru", "hack-offer.tech", "wantapply.com",
    "linkedin.com", "jobicy.com", "arbeitnow.com", "himalayas.app",
    "relocate.me", "jobsdb.com", "europa.eu", "news.ycombinator.com",
    "t.me", "dreamoffer.app", "shadowhint.com", "vseti.app",
    # vseti.app — витрина вакансий («Сети»), не работодатель. Пока её тут
    # не было, обход считал её страницу «вакансией на сайте работодателя»
    # и выдавал ссылку на витрину как ЛУЧШИЙ КОНТАКТ (09.08.2026).
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
    # Соцсети и мессенджеры с каналами вакансий. Правило давно записано в
    # field-notes («ссылки на телеграм-канал, max.ru, vk.com, ok.ru контактами
    # не считаются»), но в коде его не было — и `max.ru/vacancies` уехал в кэш
    # как «канал найма Teleport» (08.08.2026). Витрина мессенджера в карточке
    # хуже пустого поля: по ней человек пойдёт откликаться не туда.
    "max.ru", "vk.com", "vk.ru", "ok.ru",
}


# Витрины, у которых своя доска в КАЖДОЙ стране: adzuna.de, adzuna.pl,
# adzuna.co.uk, glassdoor.de… Перечислять зоны бессмысленно — их десятки, и
# новая появляется молча. Здесь имя второго уровня, зона любая.
# 🔴 Живой счёт 09.08.2026: в базе 1342 вакансии на jooble.org, 251 на
# jobviewtrack.com и 322 на adzuna.* считались «доменом работодателя» просто
# потому, что их не было в списке. В карточках это печаталось как
# «[employer, прямой]» — то есть человеку обещали прямой канал в компанию,
# а вела ссылка на витрину.
_AGGREGATOR_NAMES = {
    "jooble", "jobviewtrack", "adzuna", "careerjet", "glassdoor", "arbeitnow",
    "neuvoo", "trudvsem", "indeed", "monster", "ziprecruiter", "simplyhired",
    "jobsora", "jobrapido", "trovit", "mitula", "learn4good", "whatjobs",
}


# Домены, которые работодателем не бывают в принципе: отзывы, поисковики,
# медиа, справочники. Витринами вакансий они тоже не являются — но для вопроса
# «прямой ли это канал в компанию» ответ тот же: нет.
# 🔴 Живой счёт 09.08.2026: в маршрутах вакансии Wirex стоял
# `trustpilot.com/review/wantapply.com` — отзыв о САМОЙ ПЛОЩАДКЕ — с пометкой
# «[employer, прямой]». До этого так же попадали `ya.ru` и статья на `vc.ru`.
_NOT_EMPLOYER_NAMES = {
    "trustpilot", "glassdoor", "wikipedia", "vc", "habr", "medium", "reddit",
    "youtube", "google", "yandex", "ya", "bing", "duckduckgo", "twitter", "x",
    "facebook", "instagram", "tiktok", "telegram", "whatsapp", "quora",
}


def is_aggregator_domain(dom: str) -> bool:
    """Витрина ли это. Обратная сторона `is_employer_domain`, но отдельным
    именем: `applyopt` спрашивает именно «витрина ли», и второго списка витрин
    в проекте быть не должно — две регулярки на один вопрос расходятся всегда.
    """
    if not dom:
        return False
    dom = dom.lower().removeprefix("www.")
    if any(dom == a or dom.endswith("." + a) for a in _AGGREGATORS):
        return True
    parts = [x for x in dom.split(".") if x]
    names = _AGGREGATOR_NAMES | _NOT_EMPLOYER_NAMES
    return any(len(parts) >= -i and parts[i] in names for i in (-2, -3))


def is_employer_domain(dom: str) -> bool:
    """Домен работодателя, а не витрины. Проверка обязательна: раньше careers
    агрегатора уходил в кэш как «канал компании»."""
    if not dom or "." not in dom:
        return False
    return not is_aggregator_domain(dom)


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


# Статусы, при которых stdlib-слой ответа НЕ дал: страница есть, но её
# содержимое доступно только настоящему браузеру. Именно они пускают `--render`.
_NEEDS_BROWSER = ("КАРКАС SPA", "АНТИБОТ")


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
    # Общая приёмная — запасной канал, а не основной: годится, только когда
    # ни ATS, ни почты найма, ни списка вакансий на сайте нет вовсе.
    any_mails = [m for m in dict.fromkeys(x.lower() for x in _ANY_MAIL_RE.findall(html))
                 if _org_of(m.split("@")[-1]) == _org_of(urllib.parse.urlsplit(url).hostname or "")]
    has_jobs = bool(_HAS_JOBS.search(html))
    path = urllib.parse.urlsplit(url).path.rstrip("/") or "/"
    if path in _CONTACT_PAGES and not mails and not any_mails:
        # На главной слова «вакансии» есть у половины сайтов — это ещё не канал.
        # Засчитываем её, только если нашлась почта: найма или хотя бы общая.
        return None
    if not (ats or mails or any_mails or has_jobs):
        return None                      # живая, но не про найм — не кандидат
    return {"url": final or url, "status": "ok", "ats": ats, "mails": mails[:4],
            "any_mails": any_mails[:2],
            "has_jobs": has_jobs, "contact_page": path in _CONTACT_PAGES,
            "why": ", ".join(x for x in (
                f"ATS: {ats}" if ats else "",
                "есть признаки вакансий" if has_jobs else "",
                f"почты: {', '.join(mails[:2])}" if mails else "",
                f"только общая приёмная ({', '.join(any_mails[:2])}) — для "
                f"отклика не годится" if any_mails and not mails else "") if x)}


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


_NAME_ZONES = (".com", ".ru", ".io", ".tech", ".app", ".dev")


def domains_from_name(company: str) -> list[str]:
    """Домены-кандидаты из ИМЕНИ компании: Remoby → remoby.com, remoby.ru, …

    🔴 Заведено по живому счёту 09.08.2026. У вакансии Remoby с hirehi в базе
    стоял домен `max.ru` — витрина, на которой пост и висел, — и `channel`
    честно отвечал «агрегатор, канал искать не здесь». Домена компании не было
    ни в одной записи, и весь её контакт (`info@remoby.com`) достался вручную:
    я просто проверил четыре зоны. Ровно это и делает функция.

    Только латиница и только ОДНО слово. «Лаборатория Касперского» так не
    угадывается, и выдумывать домен по ней нельзя. Склейка из нескольких слов
    тоже запрещена: `ppsolutions.com` для «P&P Solutions» — это уже чужой сайт
    с вероятностью больше половины, а канал найма чужой компании ничем не лучше
    чужой почты с баннера.
    """
    if len(str(company or "").split()) != 1:
        return []
    name = re.sub(r"[^a-z0-9-]+", "", (company or "").strip().lower())
    if not (3 <= len(name) <= 24) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
        return []
    return [name + z for z in _NAME_ZONES]


def domain_exists(dom: str) -> bool:
    """Существует ли домен. Только DNS — ни одного HTTP-запроса.

    Проверять существование зондом содержимого нельзя: `remoby.com` отдаёт
    страницу лишь браузеру, и stdlib-зонд отвечает «страницы нет» на живом
    сайте компании (09.08.2026). Здесь нужен ровно факт «домен есть» — что на
    нём лежит, дальше выясняют полноценные зонды и рендер.
    """
    import socket  # noqa: PLC0415 — нужен только здесь

    try:
        socket.getaddrinfo(dom, None)
        return True
    except OSError:
        return False


def find(company: str, *, domain: str = "", limit: int = 4,
         timeout: int = 12, render: bool = False) -> dict:
    """Кандидаты в канал найма для компании. Сеть — да, модель — нет."""
    dom = domain_of(domain) or domain_of(company)
    if dom and not is_employer_domain(dom):
        # Домен из базы оказался витриной — это не повод сдаться: у самой
        # компании домен может просто нигде не встретиться. Пробуем угадать
        # его по имени, и уже если не вышло — честно говорим про витрину.
        guessed = next((d for d in domains_from_name(company) if domain_exists(d)), "")
        if guessed:
            dom = guessed
    if not dom:
        dom = next((d for d in domains_from_name(company) if domain_exists(d)), "")
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
    # Что считается «stdlib не справился»: либо находок нет вовсе, либо все они
    # — заглушки, которые сами просят добрать браузером (каркас SPA, стена).
    # 🔴 Условие было `not hits`, и это ровно исключало случай, ради которого
    # флаг существует: каркас и антибот КЛАДУТСЯ в находки. `--render` для
    # polydev, Авито и Joom печатал совет «повтори с --render» и не запускал
    # браузер (08.08.2026) — три канала найма остались ненайденными.
    unresolved = all(h.get("status") in _NEEDS_BROWSER for h in result["hits"])
    if unresolved and render:
        # Добираем настоящим браузером, но только по трём самым правдоподобным
        # адресам: рендер дорогой, а перебирать им все 22 кандидата незачем.
        #
        # ОДНИМ контекстом на все три. Раньше здесь звался `probe_rendered`
        # по одному адресу за раз, и каждый вызов открывал свой браузер:
        # три запуска на компанию, шестьдесят на двадцать компаний.
        # Корень домена и страница контактов — обязательные адреса, а не
        # довесок: у компании без карьерного раздела почта найма лежит именно
        # там. Перебор одних карьерных путей проходил мимо (09.08.2026: у
        # Remoby весь контакт — info@remoby.com на главной, и рендер отвечал
        # «закрыта проверкой, нужен заход человека»). Корень идёт первым: он
        # существует всегда, а /vacancies у половины компаний — 404.
        urls = [f"https://{dom}/", f"https://{dom}/careers",
                f"https://{dom}/vacancies", f"https://{dom}/contact",
                f"https://career.{dom}/"]
        before = len(result["hits"])
        for hit in probe_rendered_many(urls):
            # Дедуп по КОНЕЧНОМУ адресу — тот же, что у stdlib-ветки выше:
            # `/careers` и `/vacancies` часто редиректят в корень, и без него
            # одна страница печаталась дважды, выглядя двумя подтверждениями.
            if hit and (hit.get("url") or "").rstrip("/") not in seen_final:
                seen_final.add((hit.get("url") or "").rstrip("/"))
                result["hits"].append(hit)
                result["checked"] += 1
                if len(result["hits"]) - before >= 2:
                    break
        # Сообщать об успехе можно только по ПРИБАВКЕ. Считать по непустому
        # списку нельзя: там уже лежат заглушки-стены, из-за которых рендер и
        # звался, и «найдено рендером» печаталось бы поверх ненайденного —
        # Авито за Cloudflare не отдаётся и браузеру тоже.
        if len(result["hits"]) > before:
            result["note"] = "найдено рендером: stdlib видел только каркас SPA"
        elif not result["note"]:
            result["note"] = ("рендер тоже не прошёл: страница есть, но закрыта "
                              "проверкой или осталась каркасом — заход человека")
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
    any_mails = [m for m in dict.fromkeys(x.lower() for x in _ANY_MAIL_RE.findall(html))
                 if _org_of(m.split("@")[-1]) == _org_of(urllib.parse.urlsplit(final or url).hostname or "")]
    if not (ats or mails or any_mails or _HAS_JOBS.search(html)):
        return None
    return {"url": final or url, "status": "ok", "ats": ats, "mails": mails[:4],
            "any_mails": any_mails[:2],
            "has_jobs": bool(_HAS_JOBS.search(html)),
            "why": "подтверждено рендером (SPA)"
                   + (f", ATS: {ats}" if ats else "")
                   + (f", почты: {', '.join(mails[:2])}" if mails else "")
                   + (f", только общая приёмная ({', '.join(any_mails[:2])}) — "
                      f"для отклика не годится"
                      if any_mails and not mails else "")}


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
    for h in hits:
        if h.get("mails"):
            return h
    # 🔴 Находка, где нашлась ТОЛЬКО общая приёмная (`info@`, `contact@`),
    # каналом не становится: «почта должна быть специальная, для откликов» —
    # прямое уточнение владельца 09.08.2026. Резюме в общую приёмную уходит в
    # никуда, и записать её каналом значит соврать в карточке. В выдаче она
    # остаётся справкой: видно, что у компании есть адрес, но не для найма.
    return next((h for h in hits if not h.get("any_mails")
                 or h.get("mails") or h.get("ats") or h.get("has_jobs")), None)


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
