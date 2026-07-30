"""Тесты парсеров анонимных площадок (`sources_web`).

Сеть не трогается: у каждого источника лежит ОБРЕЗАННЫЙ реальный ответ, снятый
живьём 30.07.2026. Проверяется не «работает ли сайт», а то, что ломается тихо:

* счётчики сходятся (отдано = разобрано + отсеяно + …), и расхождение видно;
* вилка не съезжает (`maxValue: 0` у rabota — это «не указано», `$150k` у HN —
  это 150 000, а не 150, `฿50,000 – ฿75,000` — диапазон, а не «от»);
* нечёткий поиск не протаскивает чужую профессию (EURES);
* антибот-стена объявляется стеной, а не пустой выдачей (Glassdoor);
* к сырому SQL-эндпоинту (dreamoffer) не уезжает ничего, кроме SELECT;
* обход кончается ОКНОМ свежести, а не круглым числом из `--limit`, и всякое
  обрезание названо словами в сводке (hackoffer, jobsdb, EURES);
* поиск площадки, который на деле не ищет, не принимается на веру: буквальный
  ILIKE (dreamoffer), подмешивающий своё (relocate.me), префиксный (HN);
* между страницами есть пауза, а TLS-обрыв — это троттлинг, а не поломка
  (rabota.ru). Паузы в тестах записываются, а не отсыпаются, см. `_Net.naps`.

    python3 -m scripts.scout.test_sources_web
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from . import sources_web as W
from .net import BlockedError, FetchError
from .sources import Ctx

FAILS: list[str] = []

# Окно свежести считается от «сейчас», поэтому даты в фикстурах, где проверяется
# именно окно, тоже считаются от «сейчас». Зашитая строка «2026-07-30» сделала бы
# такой тест зелёным ровно один день.
NOW = datetime.now(timezone.utc).isoformat()
LONG_AGO = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def true(cond, label):
    if not cond:
        FAILS.append(label)


def jobs_of(rows):
    return [v for v in rows if v.external_id != "_summary"]


def summary_of(rows):
    got = [v for v in rows if v.external_id == "_summary"]
    if len(got) != 1:
        FAILS.append(f"ожидалась ровно одна строка сводки, получено {len(got)}")
        return None
    if got[0].url:
        FAILS.append("сводка с непустым url попадёт в выдачу как вакансия")
    return got[0]


# ──────────────────────────────────────────────────────────────────────────────
# Подмена сети
# ──────────────────────────────────────────────────────────────────────────────

class _Net:
    """Подменяет sources_web.fetch / fetch_json и запоминает спрошенные URL.

    Маршрут выбирается по фрагменту URL, а для POST — дополнительно по телу:
    у dreamoffer оба запроса идут на один и тот же адрес и различаются только SQL.
    """

    def __init__(self, routes: dict, json_routes: dict | None = None):
        self.routes, self.json_routes = routes, json_routes or {}
        self.asked: list[str] = []
        self.bodies: list[str] = []
        # Паузы между страницами записываются, а не отсыпаются: без подмены
        # один прогон этого файла спал бы минуты, а проверить «пауза была»
        # всё равно было бы нечем.
        self.naps: list[float] = []

    def fetch(self, url, **kw):
        self.asked.append(url)
        body = kw.get("data")
        if isinstance(body, bytes):
            self.bodies.append(body.decode())
        for frag, payload in self.routes.items():
            if frag in url or (self.bodies and frag in self.bodies[-1]):
                if isinstance(payload, Exception):
                    raise payload
                return (payload, url)
        raise AssertionError(f"фикстуры под {url} нет")

    def fetch_json(self, url, **kw):
        self.asked.append(url)
        for frag, payload in self.json_routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"json-фикстуры под {url} нет")


def with_net(net, fn):
    real_fetch, real_json, real_nap = W.fetch, W.fetch_json, W.nap
    W.fetch, W.fetch_json = net.fetch, net.fetch_json
    W.nap = net.naps.append
    try:
        return fn()
    finally:
        W.fetch, W.fetch_json, W.nap = real_fetch, real_json, real_nap


def with_render(html, fn, url="https://example.test/page"):
    """Подменяет render.render_page — Playwright в тестах не запускается."""
    from . import render as R
    real = R.render_page
    R.render_page = lambda u, **kw: (html, url)
    try:
        return fn()
    finally:
        R.render_page = real


# ──────────────────────────────────────────────────────────────────────────────
# Счётчики
# ──────────────────────────────────────────────────────────────────────────────

def test_tally_shows_mismatch():
    """Строка, потерянная между разбором и записью, обязана быть видна."""
    t = W.Tally("demo", offered=10, parsed=10, kept=7)
    eq(t.mismatch(), 3, "три строки исчезли — расхождение должно быть посчитано")
    true("РАСХОЖДЕНИЕ 3" in t.row().title, "расхождение не попало в сводку")

    ok = W.Tally("demo", offered=10, dropped=1, dupes=1, skipped_kind=1, parsed=7,
                 kept=5, skipped_profile=1, skipped_old=1)
    eq(ok.mismatch(), 0, "сошедшиеся счётчики не должны давать расхождения")
    true("РАСХОЖДЕНИЕ" not in ok.row().title, "ложное расхождение на сошедшихся счётчиках")
    eq(ok.row().raw["kept"], 5, "счётчики дублируются в raw для отчёта")


# ──────────────────────────────────────────────────────────────────────────────
# Антибот
# ──────────────────────────────────────────────────────────────────────────────

# Настоящая стена Glassdoor (обрезано). Английского «Just a moment» в теле НЕТ —
# заголовок русский, и net.looks_blocked её пропускал.
GLASSDOOR_WALL = """<!DOCTYPE html><html lang="en" dir="ltr"><head>
    <title>Один момент…</title>
    <meta name="robots" content="noindex, nofollow">
    <style>@font-face { font-family: "Glassdoor Sans"; src: url("data:binary/octet-stream;base64,AAAA"); }</style>
</head><body><div class="main-wrapper"><div class="article captcha-container">
<h1 class="h2">Подтвердите, что вы человек</h1></div></div></body></html>"""

LEVELS_WALL = """<!DOCTYPE html><html><head><title>levels.fyi</title>
<script src="https://e7d39a0b83b3.073fd99d.eu-north-1.token.awswaf.com/challenge.js"></script>
</head><body><div id="challenge-container"></div></body></html>"""


def test_wall_is_recognized_not_parsed():
    """Стена — это статус АНТИБОТ, а не «ноль вакансий»."""
    true(W.wall_marker(GLASSDOOR_WALL) is not None,
         "русский челлендж Glassdoor не опознан как стена")
    true(W.wall_marker(LEVELS_WALL) is not None,
         "AWS WAF (challenge.js) не опознан как стена")
    true(W.wall_marker("<html><title>Вакансии Go</title><body>вакансия</body></html>") is None,
         "обычная страница ошибочно объявлена стеной")
    try:
        W.check_wall(GLASSDOOR_WALL, "https://www.glassdoor.com.au/Job/x.htm", 200)
        FAILS.append("check_wall пропустил стену")
    except BlockedError:
        pass


def test_glassdoor_reports_wall_and_never_bypasses():
    """Glassdoor и после рендера остаётся челленджем — источник обязан упасть
    в BlockedError, а не вернуть пустой список «вакансий не найдено»."""
    try:
        with_render(GLASSDOOR_WALL, lambda: W.src_glassdoor(Ctx()))
        FAILS.append("glassdoor: стена не превратилась в BlockedError")
    except BlockedError as e:
        true("антибот" in str(e).lower(), f"причина не названа: {e}")
    except FetchError as e:
        FAILS.append(f"glassdoor: стена приехала как обычная ошибка: {e}")


GLASSDOOR_OPEN = """<html><head><title>Golang Jobs</title></head><body>
<script type="application/ld+json">{"@context":"http://schema.org","@type":"JobPosting",
"title":"Golang Developer","url":"https://www.glassdoor.com.au/job-listing/golang-JV_123.htm",
"identifier":{"value":"123"},"datePosted":"2026-07-29",
"hiringOrganization":{"name":"Raisin"},
"jobLocation":{"address":{"addressLocality":"Berlin","addressCountry":"DE"}},
"baseSalary":{"currency":"EUR","value":{"minValue":70000,"maxValue":90000,"unitText":"YEAR"}},
"description":"<p>Go backend</p>"}</script></body></html>"""


def test_glassdoor_parses_when_wall_is_down():
    """Если пользователь снял стену сам — разбор идёт по ld+json, а период
    вилки берётся из unitText площадки, а не выдумывается."""
    rows = with_render(GLASSDOOR_OPEN, lambda: W.src_glassdoor(Ctx(days=3650)))
    js = jobs_of(rows)
    eq(len(js), 1, "одна вакансия из ld+json")
    v = js[0]
    eq(v.salary_period, "year", "YEAR → year, период отдан площадкой")
    eq(v.salary_str(), "70 000–90 000 EUR/год", "годовая вилка печатается с периодом")
    eq(v.company, "Raisin", "работодатель из hiringOrganization")


# Разметка выдачи, снятая живьём 30.07.2026 (обрезана до двух карточек).
# ld+json на странице ПОИСКА у Glassdoor нет вовсе — он только на страницах
# отдельных вакансий, поэтому единственный путь к 82 найденным вакансиям —
# карточки. Якоря — data-атрибуты: имена классов собираются с хешем и меняются.
GLASSDOOR_CARDS = """<html><head><title>82 golang jobs in Germany | Glassdoor</title></head>
<body><h1 data-test="search-title" class="x">82 Golang jobs in Germany</h1>
<ul aria-label="Jobs List"><li class="JobsList_jobListItem__wjTHv" data-jobid="1010214172206"
 data-test="jobListing"><div id="job-employer-1010214172206"><span
 class="EmployerProfile_compactEmployerName__9MGcV">pyck GmbH</span></div>
<a class="JobCard_jobTitle__GLyJ1" data-test="job-title"
 href="https://www.glassdoor.com.au/job-listing/senior-golang-developer-JV_KO0,27.htm?jl=1010214172206"
 id="job-title-1010214172206">Senior Golang Developer (m/w/d)</a>
<div class="JobCard_location__Ds1fM" data-test="emp-location">Home office</div>
<div class="JobCard_listingAge__jJsuc" data-test="job-age">24h</div></li>
<li class="JobsList_jobListItem__wjTHv" data-jobid="1010193883746" data-test="jobListing">
<div id="job-employer-1010193883746"><span
 class="EmployerProfile_compactEmployerName__9MGcV">WunderGraph</span></div>
<a class="JobCard_jobTitle__GLyJ1" data-test="job-title"
 href="https://www.glassdoor.de/job-listing/senior-staff-golang-engineer-JV_IC2632180.htm?jl=1010193883746"
 id="job-title-1010193883746">Senior/Staff Golang Engineer (EMEA)</a>
<div id="job-salary-1010193883746">EUR&nbsp;90K - EUR&nbsp;130K&nbsp;<span>(Employer provided)</span></div>
<div class="JobCard_location__Ds1fM" data-test="emp-location">Frankfurt am Main</div>
<div class="JobCard_listingAge__jJsuc" data-test="job-age">21d</div></li>
<li class="JobsList_jobListItem__wjTHv" data-jobid="1010200000001" data-test="jobListing">
<div id="job-employer-1010200000001"><span
 class="EmployerProfile_compactEmployerName__9MGcV">Autohaus</span></div>
<a class="JobCard_jobTitle__GLyJ1" data-test="job-title"
 href="https://www.glassdoor.de/job-listing/verkaeufer-JV_IC1.htm?jl=1010200000001"
 id="job-title-1010200000001">Verk&auml;ufer (m/w/d)</a>
<div class="JobCard_listingAge__jJsuc" data-test="job-age">2d</div></li></ul></body></html>"""


def test_glassdoor_parses_search_cards_when_there_is_no_ldjson():
    """На странице поиска ld+json нет — и это не «ноль вакансий», а другой разбор.

    Замер 30.07.2026: утром площадка отдавала Cloudflare-челлендж, вечером в том
    же профиле — 82 вакансии и ни одного JobPosting в разметке. Без разбора
    карточек площадка честно падала бы «разбирать нечего» на живой выдаче.
    """
    rows = with_render(GLASSDOOR_CARDS, lambda: W.src_glassdoor(Ctx(days=3650)))
    js = jobs_of(rows)
    eq([v.external_id for v in js], ["1010214172206", "1010193883746"],
       "продавец отсеян по профессии, две Go-роли остались")
    eq(js[0].company, "pyck GmbH", "работодатель взят из карточки")
    eq(js[0].location, "Home office", "локация взята из карточки")
    v = js[1]
    eq((v.salary_from, v.salary_to, v.currency), (90000, 130000, "EUR"),
       "«EUR 90K - EUR 130K»: суффикс тысяч развёрнут, верхняя граница не потеряна")
    eq(v.salary_period, None,
       "периода Glassdoor в выдаче не называет — «в месяц» тут выдумка")
    true(bool(v.published_at), "дата не вычислена из метки возраста")
    true("вычислена" in (v.raw.get("date_note") or ""),
         "вычисленная дата выдаётся за дату площадки")
    s = summary_of(rows)
    eq(s.raw["mismatch"], 0, "баланс карточек не сошёлся")
    eq(s.raw["offered"], 3, "в сводке не все отданные карточки")
    true("82" in s.title, "заявленное площадкой число вакансий не попало в сводку")


def test_glassdoor_broken_markup_is_a_failure_not_zero():
    """Стены нет, карточек нет — это сменившаяся разметка, а не пустая выдача."""
    try:
        with_render("<html><head><title>Glassdoor</title></head><body>ok</body></html>",
                    lambda: W.src_glassdoor(Ctx()))
        FAILS.append("glassdoor: пустая разметка молча стала нулём вакансий")
    except BlockedError as e:
        FAILS.append(f"glassdoor: обычная страница объявлена стеной: {e}")
    except FetchError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# hack-offer.tech
# ──────────────────────────────────────────────────────────────────────────────

def _hackoffer_html(jobs, total=2216, pages=111):
    payload = {"total": total, "page": 1, "pageSize": 20, "pages": pages,
               "category": None, "city": None, "entitled": False, "jobs": jobs}
    return ('<html><body><script id="vike_pageContext" type="application/json">'
            + json.dumps({"ssrData": {"payload": payload}, "pageId": "/pages"},
                         ensure_ascii=False)
            + "</script></body></html>")


HACKOFFER_JOBS = [
    {"id": "4286", "posted_at": "2026-07-30T06:14:53.000Z", "title": "Senior AI Engineer",
     "company": "Forsyth Barnes", "salary_min": None, "salary_max": None, "currency": None,
     "location": "Germany", "remote": None, "grade": "senior", "employment": "fulltime",
     "specialization": "ai-eng", "country": "de", "relocation_to": "de", "skills": [],
     "lang": "en", "rating": 50, "slug": "4286-senior-ai-engineer",
     "description": "Senior AI Engineer position with relocation to Germany."},
    {"id": "4281", "posted_at": "2026-07-30T05:30:32.000Z", "title": "DevOps-Engineer Junior",
     "company": "SWiP", "salary_min": None, "salary_max": 90000, "currency": "RUB",
     "location": "Москва", "remote": False, "grade": "junior", "specialization": "devops",
     "skills": ["Docker", "Kubernetes"], "slug": "4281-devops-engineer-junior",
     "description": "Джуниор в команду инфраструктуры"},
]


def test_hackoffer_parses_ssr_json():
    """Данные берутся из SSR-JSON, работодатель — уже из списка (в деталку не ходим)."""
    net = _Net({"hack-offer.tech/job/g/dev?page=1": _hackoffer_html(HACKOFFER_JOBS),
                "hack-offer.tech/job/g/dev?page=2": _hackoffer_html([])})
    rows = with_net(net, lambda: W.src_hackoffer(Ctx(days=3650, limit=40)))
    js = jobs_of(rows)
    eq(len(js), 2, "две вакансии из SSR-JSON")
    eq(js[0].url, "https://hack-offer.tech/vacancy/4286-senior-ai-engineer",
       "ссылка собирается из slug")
    eq(js[0].company, "Forsyth Barnes", "работодатель есть уже в списке")
    eq(js[1].salary_str(), "до 90 000 RUB",
       "вилка без периода печатается без суффикса — площадка периода не называет")
    eq(js[1].salary_period, None, "период не выдуман")
    s = summary_of(rows)
    eq(s.raw["offered"], 2, "в сводке — сколько отдала площадка")
    eq(s.raw["mismatch"], 0, "счётчики сошлись")
    true(any("page=2" in u for u in net.asked),
         "пустая страница — единственный признак конца, её обязаны спросить")


def test_hackoffer_stops_on_empty_page_not_on_error():
    """Страница за последней отдаёт jobs: [] — цикл обязан на этом кончиться."""
    net = _Net({"page=1": _hackoffer_html(HACKOFFER_JOBS), "page=": _hackoffer_html([])})
    rows = with_net(net, lambda: W.src_hackoffer(Ctx(days=3650, limit=800)))
    eq(len(jobs_of(rows)), 2, "после пустой страницы обход прекращается")


def test_hackoffer_pages_are_counted_by_window_not_by_limit():
    """Раньше число страниц считалось как `--limit // 20`, и умолчание limit=100
    обрывало обход на пятой странице. Замер 30.07.2026: окно в 3 дня — это
    117 вакансий на 7 страницах, то есть каждый прогон терял 17 свежих.
    Теперь обход кончается ОКНОМ: страницей, где свежих не осталось."""
    fresh = [dict(j, id=f"9{p}{i}", slug=f"9{p}{i}-go", posted_at=NOW)
             for p in range(1, 8) for i, j in enumerate(HACKOFFER_JOBS)]
    pages = {f"page={p}": _hackoffer_html(fresh[2 * (p - 1):2 * p]) for p in range(1, 8)}
    pages["page=8"] = _hackoffer_html(
        [dict(HACKOFFER_JOBS[0], id="old", slug="old", posted_at=LONG_AGO)])
    net = _Net(dict(sorted(pages.items(), key=lambda kv: -len(kv[0]))))
    rows = with_net(net, lambda: W.src_hackoffer(Ctx(days=3, limit=20)))
    eq(len(jobs_of(rows)), 14, "лимит 20 обрезал обход, хотя окно ещё не кончилось")
    true(any("page=8" in u for u in net.asked),
         "восьмая страница не спрошена — окно определяется не лимитом")
    true(len(net.naps) >= 7, "между страницами не было пауз")


def test_hackoffer_says_out_loud_when_the_fuse_cut_the_crawl():
    """Предохранитель может сработать раньше окна — но тогда об этом есть
    строка в сводке. Молчаливое обрезание и честный конец окна в отчёте
    выглядят одинаково, и это самая дорогая потеря."""
    # Каждая страница — свои id и все свежие, то есть окно не кончается
    # никогда: остановить обход может только предохранитель.
    pages = {f"page={p}": _hackoffer_html(
        [dict(HACKOFFER_JOBS[0], id=f"{p}-{i}", slug=f"{p}-{i}-go", posted_at=NOW)
         for i in range(20)]) for p in range(1, 41)}
    net = _Net(dict(sorted(pages.items(), key=lambda kv: -len(kv[0]))))
    rows = with_net(net, lambda: W.src_hackoffer(Ctx(days=3, limit=20)))
    true(any("ОБХОД ОБРЕЗАН" in n for n in summary_of(rows).raw["notes"]),
         "обход упёрся в предохранитель, а в сводке об этом ни слова")


def test_hackoffer_refuses_unknown_group():
    """Несуществующий слаг площадка молча меняет на ВЕСЬ каталог (4287 вместо
    2216). Такой прогон выглядит успешным — поэтому слаг только из белого списка."""
    ctx = Ctx()
    ctx.hackoffer_groups = ("devops",)      # такой группы нет
    try:
        with_net(_Net({}), lambda: W.src_hackoffer(ctx))
        FAILS.append("hackoffer: неизвестная группа не отвергнута")
    except FetchError as e:
        true("каталог" in str(e), f"причина отказа не объяснена: {e}")


def test_hackoffer_warns_when_group_returns_whole_catalog():
    """Если группа вдруг отдала размер всего каталога — это видно в сводке."""
    net = _Net({"page=1": _hackoffer_html(HACKOFFER_JOBS, total=4287),
                "page=2": _hackoffer_html([])})
    rows = with_net(net, lambda: W.src_hackoffer(Ctx(days=3650, limit=40)))
    true(any("весь каталог" in n or "каталога" in n for n in summary_of(rows).raw["notes"]),
         "подозрительный размер выдачи не отмечен в сводке")


# ──────────────────────────────────────────────────────────────────────────────
# dreamoffer
# ──────────────────────────────────────────────────────────────────────────────

DREAMOFFER_ROW = [
    1332534, "2026-07-30T06:00:04+00:00", "tg",
    "https://t.me/runello_rus_backend/4167", "runello_rus_backend",
    "**Golang разработчик**\n\n**Грейд:** Senior\n**Стек:** Go, REST API\n\nЗарплата: от 250 000 ₽ в месяц",
    {"grade": "Senior", "profession": "Backend developer", "work_format": "remote",
     "salary": "от 250 000 ₽ в месяц", "city": "Москва", "country": "Россия", "language": "ru"},
]
DREAMOFFER_LINKEDIN = [
    1332013, "2026-07-30T04:32:45+00:00", "linkedin",
    "https://pt.linkedin.com/jobs/view/software-engineer-golang-4429559624", "empty",
    "Software Engineer (Golang)\nPorto, Portugal",
    {"grade": "unknown", "profession": "Backend developer", "country": "Portugal",
     "city": "Porto", "language": "pt"},
]


def test_dreamoffer_two_step_query_and_period():
    """Окно режется по nn (индекс), а не LIMIT-ом, и свежесть считается по
    time_of_created: time_in_channel в 94.5% строк — sentinel 2025-01-01."""
    net = _Net({"min(nn)": json.dumps({"rows": [[1304586]]}),
                "ILIKE": json.dumps({"rows": [DREAMOFFER_ROW, DREAMOFFER_LINKEDIN]})})
    rows = with_net(net, lambda: W.src_dreamoffer(Ctx(query="Golang", days=3650)))
    js = jobs_of(rows)
    eq(len(js), 2, "обе строки разобрались")
    tg, li = js
    eq(tg.url, "https://t.me/runello_rus_backend/4167",
       "url — прямая ссылка на исходный пост")
    eq(tg.title, "Golang разработчик", "название — первая строка поста без markdown")
    eq((tg.salary_from, tg.currency, tg.salary_period), (250000, "RUB", "month"),
       "вилка и период взяты из строки самой площадки")
    eq(tg.remote, True, "work_format=remote → удалёнка")
    eq(li.raw["source"], "linkedin", "источник строки сохраняется: link не всегда telegram")
    true("time_of_created" in summary_of(rows).raw["notes"][0]
         or any("time_of_created" in n for n in summary_of(rows).raw["notes"]),
         "в сводке не сказано, по какому полю считалась свежесть")
    true(any("min(nn)" in b for b in net.bodies), "граница окна не запрашивалась")
    true(any("is_blocked = 0" in b for b in net.bodies),
         "без is_blocked = 0 приедут авто-дубли")


def test_dreamoffer_searches_by_words_because_ilike_is_literal():
    """Поиск здесь — `ILIKE '%подстрока%'` по тексту поста, а не по смыслу.
    Замер 30.07.2026 в окне 3 дня: «Go разработчик» — РОВНО 0 (такой подстроки
    в постах не бывает), «Backend Go» — 4, «Golang» — 109. То есть две из трёх
    формулировок были мертвы, а их ноль читался как «вакансий нет».
    """
    tally = W.Tally("dreamoffer")
    terms = W._dreamoffer_terms(
        Ctx(query="Golang", extra_queries=("Go разработчик", "Backend Go")), tally)
    for word in ("golang", "backend", "разработчик", "devops", "платформ", "бэкенд"):
        true(word in terms, f"слова «{word}» нет в наборе запросов: {terms}")
    true(all(" " not in t for t in terms), f"в сеть уходит фраза, а не слово: {terms}")
    true(any("фраз" in n for n in tally.notes),
         "в сводке не сказано, что фразы разобраны на слова")
    true(any("go" == t for t in terms) is False,
         "двухбуквенный токен эндпоинт отбрасывает — его слать нельзя")


def test_dreamoffer_filters_alien_professions_from_a_wide_net():
    """Широкое слово тащит чужое: '%devops%' отдаёт 2053 поста, из них 809 —
    QA, тестировщики и проектные менеджеры, у которых devops просто назван
    в стеке. Такой пост обязан быть отсеян и ПОСЧИТАН, а не попасть в отчёт."""
    alien = [1332999, "2026-07-30T06:00:04+00:00", "tg",
             "https://t.me/ch/1", "ch",
             "Senior QA Automation Engineer\n\nСтек: Selenium, немного devops",
             {"profession": "QA engineer"}]
    net = _Net({"min(nn)": json.dumps({"rows": [[1304586]]}),
                "ILIKE": json.dumps({"rows": [DREAMOFFER_ROW, alien]})})
    rows = with_net(net, lambda: W.src_dreamoffer(Ctx(query="Golang", days=3650)))
    eq([v.external_id for v in jobs_of(rows)], ["1332534"],
       "тестировщик приехал в отчёт как Go-вакансия")
    s = summary_of(rows)
    eq(s.raw["skipped_profile"], 1, "отсев по профессии не посчитан")
    eq(s.raw["mismatch"], 0, "счётчики не сошлись")
    true(net.naps and all(p > 0 for p in net.naps),
         "между тяжёлыми ILIKE-запросами нет пауз (один такой стоит серверу до 15 с)")


def test_dreamoffer_title_is_not_a_channel_label():
    """«Position:» — подпись канала, а не часть названия должности.

    Замер живого прогона 30.07.2026: из 2210 записей dreamoffer 2097 (95%)
    приезжали как «Position: Storage Engineer». Это треть всего обхода, и человек
    читал ярлык вместо названия.
    """
    eq(W._first_line("**Position: Storage Engineer**\nGeneva"), "Storage Engineer",
       "ярлык канала уехал в название вакансии")
    eq(W._first_line("вакансия: Backend Go"), "Backend Go", "русский ярлык не срезан")
    # А вот это уже НАЗВАНИЕ, а не подпись: двоеточия нет, резать нечего.
    eq(W._first_line("Требуется Golang-разработчик"), "Требуется Golang-разработчик",
       "срезано начало настоящего названия")
    eq(W._first_line("Senior Golang Engineer"), "Senior Golang Engineer",
       "название без ярлыка изменилось")


def test_dreamoffer_one_dead_word_does_not_kill_the_source():
    """502 на тяжёлом ILIKE — это таймаут шлюза, и он тем вероятнее, чем шире
    окно. Ронять из-за одного слова весь источник нельзя: остальные уже принесли
    тысячи вакансий. Но и молчать нельзя — обход по этому слову НЕ выполнен."""
    calls = {"n": 0}
    real = W._dreamoffer_rows

    def flaky(sql):
        if "'%devops%'" in sql:
            calls["n"] += 1
            raise FetchError(W.DREAMOFFER_API, "HTTP 502", 502)
        return real(sql)

    net = _Net({"min(nn)": json.dumps({"rows": [[1304586]]}),
                "ILIKE": json.dumps({"rows": [DREAMOFFER_ROW]})})
    W._dreamoffer_rows, keep = flaky, W._dreamoffer_rows
    try:
        rows = with_net(net, lambda: W.src_dreamoffer(Ctx(query="Golang", days=3650)))
    finally:
        W._dreamoffer_rows = keep
    eq(calls["n"], 1, "запрос по «devops» не отправлялся")
    true(len(jobs_of(rows)) >= 1, "одно упавшее слово выбросило весь источник")
    true(any("devops" in n and "не отработало" in n
             for n in summary_of(rows).raw["notes"]),
         "пропущенное слово не названо в сводке — это выглядит как «ничего нет»")


def test_dreamoffer_refuses_anything_but_select():
    """Эндпоинт сырой. Всё, что не одиночный SELECT, до сети не доезжает."""
    for bad in ("DELETE FROM vacancies_ai_db",
                "SELECT nn FROM vacancies_ai_db; DROP TABLE vacancies_ai_db",
                "UPDATE vacancies_ai_db SET is_blocked = 1",
                "  insert into vacancies_ai_db values (1)"):
        try:
            W._safe_sql(bad)
            FAILS.append(f"_safe_sql пропустил изменяющий запрос: {bad!r}")
        except FetchError:
            pass
    eq(W._safe_sql("SELECT nn FROM vacancies_ai_db WHERE nn > 1"),
       "SELECT nn FROM vacancies_ai_db WHERE nn > 1", "обычный SELECT проходит")


def test_dreamoffer_quotes_query():
    """Кавычка в запросе не должна ломать SQL — это читающий, но всё же SQL."""
    eq(W._sql_quote("O'Brien"), "O''Brien", "одинарная кавычка удваивается")


# ──────────────────────────────────────────────────────────────────────────────
# rabota.ru
# ──────────────────────────────────────────────────────────────────────────────

RABOTA_HTML = """<html><head><title>Работа backend-разработчиком в Москве</title></head>
<body><h1>Вакансии backend-разработчика в Москве</h1>
<script data-n-head="ssr" type="application/ld+json">[
{"@context":"https://schema.org","@type":"JobPosting","title":"Senior Golang-разработчик",
 "url":"https://www.rabota.ru/vacancy/54335063/",
 "identifier":{"@type":"PropertyValue","name":"СБЕР","value":788450},
 "hiringOrganization":{"@type":"Organization","name":"СБЕР"},
 "jobLocation":{"address":{"streetAddress":"Центральный федеральный округ,Москва"}},
 "baseSalary":{"currency":"RUB","minValue":350000,"maxValue":0,"value":{"unitText":"MONTH"}},
 "datePosted":"2026-07-25T09:57:43.000Z",
 "description":"<p>Мы ищем Go-разработчика</p>"},
{"@context":"https://schema.org","@type":"JobPosting","title":"Backend-разработчик",
 "url":"https://www.rabota.ru/vacancy/50533147/",
 "hiringOrganization":{"@type":"Organization","name":"ООО Ромашка"},
 "jobLocation":{"address":{"streetAddress":"Москва"}},
 "estimatedSalary":{"currency":"RUB","value":{"minValue":150000,"unitText":"MONTH"}},
 "datePosted":"2026-07-24T10:00:00.000Z","description":"<p>Python</p>"}]</script>
</body></html>"""


def test_rabota_reads_ldjson_and_zero_max_is_not_zero():
    """maxValue = 0 означает «сверху не указано». Прямой перенос дал бы «350 000–0»."""
    net = _Net({"rabota.ru": RABOTA_HTML})
    rows = with_net(net, lambda: W.src_rabota(Ctx(query="Golang", days=3650)))
    js = jobs_of(rows)
    eq(len(js), 2, "оба JobPosting разобраны")
    sber = js[0]
    eq(sber.external_id, "54335063", "id взят из url вакансии")
    eq((sber.salary_from, sber.salary_to), (350000, None), "ноль сверху — это «не указано»")
    eq(sber.salary_period, "month", "unitText MONTH → month")
    eq(sber.salary_str(), "от 350 000 RUB/мес", "вилка печатается с периодом площадки")
    eq(js[1].salary_from, 150000, "если baseSalary пуст, берётся estimatedSalary")
    eq(summary_of(rows).raw["mismatch"], 0, "счётчики сошлись")


def test_rabota_skips_two_letter_query():
    """`query=Go` отдаёт ноль — это ловушка, а не «вакансий нет». Такой запрос
    не отправляется вовсе, и в сводке написано почему."""
    net = _Net({"rabota.ru": RABOTA_HTML})
    rows = with_net(net, lambda: W.src_rabota(Ctx(query="Go", days=3650)))
    eq(len(jobs_of(rows)), 0, "короткий запрос не должен ничего приносить")
    true(any("коротк" in n for n in summary_of(rows).raw["notes"]),
         "в сводке не сказано, что запрос пропущен из-за длины")
    eq(net.asked, [], "короткий запрос не должен уходить в сеть")


RABOTA_PAGE2 = """<html><head><title>Работа</title></head><body>
<script type="application/ld+json">[
{"@context":"https://schema.org","@type":"JobPosting","title":"Go-разработчик",
 "url":"https://www.rabota.ru/vacancy/99999999/",
 "hiringOrganization":{"@type":"Organization","name":"Т-Банк"},
 "jobLocation":{"address":{"streetAddress":"Москва"}},
 "datePosted":"2026-07-29T10:00:00.000Z","description":"<p>Go</p>"}]</script>
</body></html>"""


def test_rabota_reads_the_second_page_and_pauses_between_requests():
    """«Пагинации нет» было НЕВЕРНЫМ допущением: по «разработчик» вторая
    страница даёт вакансии, которых на первой нет. И между запросами обязана
    быть пауза — площадка уже закрывала нам TLS за частоту."""
    net = _Net({"page=2": RABOTA_PAGE2, "rabota.ru": RABOTA_HTML})
    rows = with_net(net, lambda: W.src_rabota(Ctx(query="Golang", days=3650)))
    ids = [v.external_id for v in jobs_of(rows)]
    true("99999999" in ids, f"вторая страница не прочитана: {ids}")
    true(any("page=2" in u for u in net.asked), "запрос второй страницы не ушёл")
    true(net.naps and min(net.naps) >= 8,
         f"паузы между запросами короче восьми секунд: {net.naps}")


def test_rabota_tls_drop_is_throttling_not_a_crash():
    """`SSL: UNEXPECTED_EOF_WHILE_READING` — это троттлинг за частые запросы,
    а не поломка парсера. В покрытии он не должен читаться как «УПАЛ»:
    чинится паузой, а не кодом, и точно не обходом защиты."""
    err = FetchError("https://www.rabota.ru/vacancy/",
                     "URLError: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] "
                     "EOF occurred in violation of protocol (_ssl.c:1081)>")
    true(W.throttle_marker(err) is not None, "TLS-обрыв не опознан как троттлинг")
    true(W.throttle_marker(FetchError("u", "HTTP 500")) is None,
         "обычная ошибка объявлена троттлингом")
    net = _Net({"rabota.ru": err})
    try:
        with_net(net, lambda: W.src_rabota(Ctx(query="Golang")))
        FAILS.append("rabota: TLS-обрыв молча стал нулём вакансий")
    except W.ThrottledError as e:
        true("троттлинг" in str(e).lower(), f"причина названа непонятно: {e}")
        true(isinstance(e, BlockedError),
             "троттлинг обязан быть отдельным статусом, а не «упал»")


def test_rabota_keeps_what_it_managed_to_collect_before_the_throttle():
    """Первую страницу успели, вторую не дали — это частичный обход, и он
    честнее нуля. Но в сводке обязано быть сказано, что обход НЕПОЛНЫЙ."""
    err = FetchError("https://www.rabota.ru/vacancy/?page=2",
                     "URLError: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING]>")
    net = _Net({"page=2": err, "rabota.ru": RABOTA_HTML})
    rows = with_net(net, lambda: W.src_rabota(Ctx(query="Golang", days=3650)))
    eq(len(jobs_of(rows)), 2, "собранное до троттлинга выброшено")
    notes = summary_of(rows).raw["notes"]
    true(any("ТРОТТЛИНГ" in n for n in notes), f"троттлинг не назван в сводке: {notes}")
    true(any("НЕПОЛНЫЙ" in n for n in notes), "неполнота обхода не объявлена")


def test_rabota_without_ldjson_is_a_failure():
    """Нет ld+json — это сменившаяся вёрстка или стена, а не пустая выдача.
    Заголовок страницы проверять нельзя: он зашит SEO-текстом и запрос не отражает."""
    net = _Net({"rabota.ru": "<html><h1>Вакансии backend-разработчика</h1></html>"})
    try:
        with_net(net, lambda: W.src_rabota(Ctx(query="Golang")))
        FAILS.append("rabota: страница без ld+json не уронила источник")
    except FetchError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# getmatch.ru
# ──────────────────────────────────────────────────────────────────────────────

GETMATCH_JSON = {
    "meta": {"total": 2, "offset": 0, "limit": 1000},
    "offers": [
        {"id": 29964, "offer_type": "one_day_offer_v3", "position": "Новый трек развития",
         "published_at": "2026-07-18T09:01:05", "salary_hidden": True,
         "company": {"name": "getmatch"}, "url": "/vacancies/29964-trek"},
        {"id": 35521, "offer_type": "vacancy",
         "position": "Go-разработчик (команда надёжности)",
         "company": {"name": "VK", "industry": None},
         "url": "/vacancies/35521-go-razrabotchik",
         "published_at": "2026-07-29T10:00:00", "salary_hidden": True,
         "salary_display_from": None, "salary_display_to": None, "salary_currency": None,
         "salary_taxes": "gross", "skills_objects": [{"name": "Go"}, {"name": "Linux"}],
         "location_items": [{"label": "Россия", "format": "remote"}],
         "offer_description": "<b>Команда</b> ищет Go-разработчика"},
        {"id": 35100, "offer_type": "vacancy", "position": "Менеджер по продажам",
         "company": {"name": "Ромашка"}, "url": "/vacancies/35100-manager",
         "published_at": "2026-07-29T10:00:00",
         "salary_display_from": 120000, "salary_display_to": 180000,
         "salary_currency": "RUB", "salary_taxes": "net", "skills_objects": [],
         "location_items": [{"label": "Москва", "format": "office"}],
         "offer_description": "Продажи"},
    ],
}


def test_getmatch_drops_promo_and_prefixes_url():
    """Промо-баннеры приезжают СВЕРХ meta.total и вакансиями не являются;
    url в списке относительный и без префикса никуда не ведёт."""
    net = _Net({}, {"getmatch.ru/api/offers": GETMATCH_JSON})
    rows = with_net(net, lambda: W.src_getmatch(Ctx(days=3650)))
    js = jobs_of(rows)
    eq([v.external_id for v in js], ["35521"],
       "остаётся одна вакансия: промо отброшено, продажи отсеяны по профессии")
    eq(js[0].url, "https://getmatch.ru/vacancies/35521-go-razrabotchik",
       "относительный url дополнен доменом")
    eq(js[0].salary_period, None, "период площадка не называет — не выдумываем")
    eq(js[0].salary_gross, True, "salary_taxes=gross → gross")
    eq(js[0].remote, True, "формат remote из location_items")
    s = summary_of(rows)
    eq(s.raw["skipped_kind"], 1, "промо посчитано отдельно, а не потеряно")
    eq(s.raw["skipped_profile"], 1, "чужая профессия посчитана отдельно")
    eq(s.raw["mismatch"], 0, "счётчики сошлись")
    true(all("sp=all" in u for u in net.asked),
         "серверные фильтры не используются: неверное значение молча отдаёт весь каталог")


def test_getmatch_ats_all_keeps_everything():
    """--ats-all выключает отсев по профессии, но не отменяет отбраковку промо."""
    net = _Net({}, {"getmatch.ru/api/offers": GETMATCH_JSON})
    rows = with_net(net, lambda: W.src_getmatch(Ctx(days=3650, ats_all=True)))
    eq(len(jobs_of(rows)), 2, "обе настоящие вакансии остались")
    eq(summary_of(rows).raw["skipped_kind"], 1, "промо всё равно отброшено")


# ──────────────────────────────────────────────────────────────────────────────
# EURES
# ──────────────────────────────────────────────────────────────────────────────

EURES_SEARCH = {"numberRecords": 666, "facets": None, "jvs": [
    {"id": "ZWY4-1", "title": "Software Engineer Golang", "description": "Go, Kubernetes",
     "creationDate": "1785317402102", "lastModificationDate": "1785398402360",
     "positionScheduleCodes": ["fulltime"], "source": "PES"},
    {"id": "ZWY4-2", "title": "Auxiliaire Petite Enfance volant H/F",
     "description": "Garde d'enfants", "creationDate": "1785317402102",
     "positionScheduleCodes": ["fulltime"], "source": "PES"},
]}

EURES_DETAIL = {"id": "ZWY4-1", "preferredLanguage": "en", "jvProfiles": {"en": {
    "title": "Software Engineer Golang",
    "employer": {"name": "infomaniak", "website": "https://infomaniak.com"},
    "offeredRemunerationPackage": {"salaries": [
        {"minimumSalary": 60000, "maximumSalary": 80000, "currencyCode": "EUR",
         "payingIntervalCode": "year"}]},
    "locations": [{"cityName": "Geneva", "countryCode": "ch"}],
    "applicationInstructions": [
        '<a href="https://www.jobup.ch/fr/emplois/detail/6152e7b1/" rel="nofollow">jobup</a>'],
}}}


def test_eures_filters_fuzzy_hits_and_takes_period_from_source():
    """Поиск EURES нечёткий: по «golang» он честно отдаёт 666 в счётчике и
    «Auxiliaire Petite Enfance volant» в строках. Такая строка обязана быть
    отсеяна и посчитана, а не попасть в отчёт как найденная вакансия."""
    net = _Net({"jv-search/search": json.dumps(EURES_SEARCH)},
               {"public/jv/id/": EURES_DETAIL})
    rows = with_net(net, lambda: W.src_eures(Ctx(query="Golang", days=3650, limit=50)))
    js = jobs_of(rows)
    eq([v.title for v in js], ["Software Engineer Golang"],
       "нянька по запросу golang в отчёт не идёт")
    v = js[0]
    eq((v.salary_from, v.salary_to, v.currency), (60000, 80000, "EUR"),
       "вилка добирается из карточки")
    eq(v.salary_period, "year", "payingIntervalCode=year — период назван площадкой")
    eq(v.company, "infomaniak", "работодатель из карточки")
    eq(v.employer_url, "https://www.jobup.ch/fr/emplois/detail/6152e7b1/",
       "ссылка на отклик — ближайший к работодателю контакт")
    true(v.url.startswith("https://europa.eu/eures/portal/jv-se/jv-details/"),
         f"ссылка на карточку собрана неверно: {v.url}")
    s = summary_of(rows)
    eq(s.raw["skipped_profile"], 1, "отсев нечёткого совпадения посчитан")
    eq(s.raw["mismatch"], 0, "счётчики сошлись")
    body = json.loads(net.bodies[0])
    eq(body["sortSearch"], "BEST_MATCH",
       "MOST_RECENT отдаёт правильный счётчик и совершенно не те строки")
    eq(body["keywords"][0]["specificSearchCode"], "TITLE",
       "EVERYWHERE раздувает выдачу с 666 до 46 153")


def _eures_page(prefix: str, hits: bool, count: int = W.EURES_PAGE) -> str:
    """Полная страница выдачи: `hits=False` — сплошь чужая профессия."""
    jvs = [{"id": f"{prefix}-{i}",
            "title": "Software Engineer Golang" if hits else "Auxiliaire Petite Enfance",
            "description": "Go" if hits else "Garde d'enfants",
            "creationDate": "1785317402102"} for i in range(count)]
    return json.dumps({"numberRecords": 667, "jvs": jvs})


def test_eures_asks_for_full_pages_only():
    """ЖИВОЙ БАГ: `resultsPerPage = min(50, сколько_осталось)` вместе с page=2
    заставляет сервер отдать 11–20-е строки вместо 51–60-х — вторая страница
    перечитывает начало первой («отдано 60, разобрано 50, дублей 10»).
    Поэтому страница запрашивается только ПОЛНАЯ."""
    net = _Net({"jv-search/search": _eures_page("a", True)}, {"public/jv/id/": EURES_DETAIL})
    with_net(net, lambda: W.src_eures(Ctx(query="Golang", days=3650, limit=10)))
    sizes = {json.loads(b)["resultsPerPage"] for b in net.bodies}
    eq(sizes, {W.EURES_PAGE},
       "запрошена неполная страница — сервер сдвинет окно и вернёт начало выдачи")


def test_eures_does_not_stop_at_the_first_page_without_hits():
    """BEST_MATCH не сортирует по дате, и свежее рассыпано по всей выдаче:
    по «Golang» пустая ровно вторая страница, а на 3–7 снова есть попадания.
    Выход на первой пустой терял весь хвост окна."""
    pages = {}

    def route(body):
        return json.loads(body)["page"]

    class _Pages(_Net):
        def fetch(self, url, **kw):
            self.asked.append(url)
            body = kw.get("data")
            if isinstance(body, bytes):
                self.bodies.append(body.decode())
            page = route(self.bodies[-1])
            pages[page] = pages.get(page, 0) + 1
            # Попадания есть на 1-й и 3-й, вторая — сплошь няньки.
            return (_eures_page(f"p{page}", page in (1, 3)), url)

    net = _Pages({}, {"public/jv/id/": EURES_DETAIL})
    rows = with_net(net, lambda: W.src_eures(Ctx(query="Golang", days=3650, limit=10)))
    true(3 in pages, f"обход бросили на первой пустой странице: спрошены {sorted(pages)}")
    eq(len(jobs_of(rows)), 2 * W.EURES_PAGE,
       "попадания с третьей страницы не доехали до отчёта")
    true(any("подряд без свежих попаданий" in n for n in summary_of(rows).raw["notes"]),
         "в сводке не объяснено, почему обход остановился")


# ──────────────────────────────────────────────────────────────────────────────
# relocate.me
# ──────────────────────────────────────────────────────────────────────────────

def _relocate_page(cards: str) -> str:
    return f'<html><body><div class="jobs-list">{cards}</div></body></html>'


RELOCATE_PROMO = """
<div class="jobs-list__job job_featured"><div class="job__info">
  <div class="job__company text4-medium"><svg width="20"><path d="M9"/></svg><p>Remote</p></div>
  <div class="job__company text4-medium"><p>The Global Move</p></div></div>
  <div class="job__title"><a href="/remote/remote/the-global-move/1000-curated-jobs-10080">
  <b>1000+ Curated Visa Sponsorship Jobs (Paid Option)</b></a></div>
  <p class="job__preview">The Global Move is the new initiative</p></div>"""
RELOCATE_GO = """
<div class="jobs-list__job"><div class="job__info">
  <div class="job__company text4-medium"><svg width="20"><path d="M9"/></svg><p>Japan</p></div>
  <div class="job__company text4-medium"><p>PayPay</p></div></div>
  <div class="job__title"><a href="/japan/tokyo/paypay/backend-engineer-10205">
  <b>Backend Engineer</b></a> in Tokyo</div>
  <p class="job__preview">PayPay is looking for a Backend Engineer</p></div>"""
RELOCATE_ALIEN = """
<div class="jobs-list__job"><div class="job__info">
  <div class="job__company text4-medium"><svg width="20"><path d="M9"/></svg><p>Germany</p></div>
  <div class="job__company text4-medium"><p>Zalando</p></div></div>
  <div class="job__title"><a href="/germany/berlin/zalando/qa-automation-engineer-10333">
  <b>QA Automation Engineer</b></a> in Berlin</div>
  <p class="job__preview">Testing all the things</p></div>"""
RELOCATE_PAGE2 = """
<div class="jobs-list__job"><div class="job__info">
  <div class="job__company text4-medium"><svg width="20"><path d="M9"/></svg><p>Remote</p></div>
  <div class="job__company text4-medium"><p>Wolt</p></div></div>
  <div class="job__title"><a href="/remote/remote/wolt/platform-engineer-10444">
  <b>Platform Engineer</b></a> in Helsinki</div>
  <p class="job__preview">Go and Kubernetes</p></div>"""


def test_relocateme_takes_the_whole_board_and_filters_itself():
    """Поиск площадки НЕЧЕСТНЫЙ: `query=zzzznotaword` отдаёт три настоящие
    вакансии, то есть запрос не фильтрует, а подмешивает своё. Значит доска
    берётся целиком (её всего 31 карточка на трёх страницах), а профессию
    отбираем мы. Промо-карточка — реклама самой доски, не вакансия."""
    # Третья страница — только промо: так у площадки и выглядит конец доски.
    net = _Net({"page=2": _relocate_page(RELOCATE_PROMO + RELOCATE_PAGE2),
                "page=3": _relocate_page(RELOCATE_PROMO),
                "international-jobs": _relocate_page(
                    RELOCATE_PROMO + RELOCATE_GO + RELOCATE_ALIEN)})
    rows = with_net(net, lambda: W.src_relocateme(Ctx(query="Golang")))
    js = jobs_of(rows)
    eq([v.external_id for v in js], ["10205", "10444"],
       "промо и QA не вакансии профиля; вторая страница обязана быть прочитана")
    real = js[0]
    eq(real.company, "PayPay", "работодатель — вторая подпись карточки")
    eq(real.location, "Tokyo, Japan", "город из «in Tokyo», страна из подписи")
    eq(real.salary_str(), "", "вилок площадка не отдаёт — пусто, а не ноль")
    s = summary_of(rows)
    eq(s.raw["skipped_kind"], 1, "промо-карточка не посчитана как «не вакансия»")
    eq(s.raw["dupes"], 2, "промо повторяется на каждой странице — это дубли")
    eq(len(net.asked), 3, "доска должна читаться до страницы без новых карточек")
    eq(s.raw["skipped_profile"], 1, "чужая профессия не посчитана отдельно")
    eq(s.raw["mismatch"], 0, "счётчики сошлись")
    true(all("query=" not in u for u in net.asked),
         "запрос всё ещё уходит площадке, хотя её поиск не фильтрует")
    true(net.naps and net.naps[0] >= 1,
         "между страницами доски нет паузы")


def test_relocateme_stops_when_a_page_brings_nothing_new():
    """Промо повторяется на каждой странице. Признак конца — отсутствие НОВЫХ
    карточек, иначе цикл крутится до потолка и жжёт запросы впустую."""
    net = _Net({"international-jobs": _relocate_page(RELOCATE_PROMO + RELOCATE_GO)})
    rows = with_net(net, lambda: W.src_relocateme(Ctx(query="Golang")))
    eq(len(jobs_of(rows)), 1, "одна вакансия профиля")
    eq(len(net.asked), 2, "обход обязан остановиться на первой повторяющейся странице")


# ──────────────────────────────────────────────────────────────────────────────
# th.jobsdb.com
# ──────────────────────────────────────────────────────────────────────────────

JOBSDB_JSON = {"totalCount": 256, "data": [
    {"id": "93575655", "title": "Senior Backend Developer (Golang)",
     "companyName": "Orcsoft", "employer": {"name": "Orcsoft"},
     "listingDate": "2026-07-27T06:21:30Z", "salaryLabel": "฿50,000 – ฿75,000 per month",
     "locations": [{"label": "Bangkok", "countryCode": "TH"}],
     "workTypes": ["Contract/Temp"],
     "workArrangements": {"data": [{"label": {"text": "Remote"}}]},
     "teaser": "Golang backend"},
    {"id": "93650483", "title": "Sales Manager", "companyName": "Central Group",
     "listingDate": "2026-07-30T03:33:56Z", "salaryLabel": "",
     "locations": [{"label": "Pathum Wan"}], "workTypes": ["Full time"],
     "workArrangements": {"data": []}, "teaser": "Sales"},
]}


def test_jobsdb_salary_range_keeps_upper_bound():
    """«฿50,000 – ฿75,000 per month»: знак валюты у второй границы ломал разбор,
    и вилка молча превращалась в «от 50 000» — верхняя граница терялась."""
    net = _Net({}, {"jobsearch/v5/search": JOBSDB_JSON})
    rows = with_net(net, lambda: W.src_jobsdb(Ctx(query="golang", days=3650, limit=30)))
    js = jobs_of(rows)
    eq(len(js), 1, "менеджер по продажам отсеян по профессии")
    v = js[0]
    eq((v.salary_from, v.salary_to, v.currency), (50000, 75000, "THB"),
       "диапазон разобран целиком, валюта — бат")
    eq(v.salary_period, "month", "«per month» — период назван площадкой")
    eq(v.url, "https://th.jobsdb.com/job/93575655", "ссылка собирается из id")
    eq(v.remote, True, "workArrangements → удалёнка")
    eq(summary_of(rows).raw["skipped_profile"], 1, "отсев по профессии посчитан")
    true(all("th.jobsdb.com/api/" in u for u in net.asked),
         "HTML-страница площадки за Cloudflare — туда ходить нельзя")


def test_jobsdb_sorts_by_date_and_walks_the_whole_window():
    """Умолчание площадки — сортировка по релевантности, и она молча съедала
    окно: замер 30.07.2026 по «golang» — на первой странице свежих 1 из 30,
    даты 30.06…28.07. С `sortmode=ListedDate` — 9 свежих и 24.07…30.07 при том
    же totalCount. Обход идёт до страницы, где свежих не осталось."""
    fresh = [dict(JOBSDB_JSON["data"][0], id=f"{p}{i}", listingDate=NOW)
             for p in range(1, 4) for i in range(30)]
    old = [dict(JOBSDB_JSON["data"][0], id=f"old{i}", listingDate=LONG_AGO)
           for i in range(30)]
    pages = {f"&page={p}&": {"totalCount": 256, "data": fresh[30 * (p - 1):30 * p]}
             for p in range(1, 4)}
    pages["&page=4&"] = {"totalCount": 256, "data": old}
    net = _Net({}, dict(sorted(pages.items(), key=lambda kv: -len(kv[0]))))
    rows = with_net(net, lambda: W.src_jobsdb(Ctx(query="golang", days=3, limit=30)))
    eq(len(jobs_of(rows)), 90, "лимит обрезал обход раньше, чем кончилось окно")
    true(all("sortmode=ListedDate" in u for u in net.asked),
         "выдача запрошена без сортировки по дате")
    true(any("page=4" in u for u in net.asked),
         "страница за окном не проверена — конец окна определяется по датам")
    true(net.naps and min(net.naps) > 0, "между страницами нет пауз")


def test_jobsdb_salary_helper():
    eq(W._jobsdb_salary("฿50,000 – ฿75,000 per month")[:3], (50000, 75000, "THB"),
       "прямой разбор строки вилки")
    eq(W._jobsdb_salary("")[:3], (None, None, None), "пустая строка — пустая вилка")


# ──────────────────────────────────────────────────────────────────────────────
# Hacker News «Who is hiring» — замена мёртвому stackoverflowjobs.com
# ──────────────────────────────────────────────────────────────────────────────

HN_THREAD = {"hits": [
    {"objectID": "48747975", "title": "Ask HN: Who wants to be hired? (July 2026)"},
    {"objectID": "48747976", "title": "Ask HN: Who is hiring? (July 2026)"},
    {"objectID": "48357725", "title": "Ask HN: Who is hiring? (June 2026)"},
    {"objectID": "47975571", "title": "Ask HN: Who is hiring? (May 2026)"},
]}
HN_COMMENTS = {"nbHits": 2, "nbPages": 1, "hits": [
    {"objectID": "48763652", "created_at": "2026-07-02T16:13:19Z", "author": "acme",
     "parent_id": "48747976", "story_id": "48747976",
     "comment_text": "Acme Corp | Senior Go Engineer | REMOTE (EU) | $150k - $200k"
                     "<p>We build things in Golang. Apply: "
                     "<a href=\"https://acme.example/jobs\">https://acme.example/jobs</a>"},
    {"objectID": "48752786", "created_at": "2026-07-01T20:38:37Z", "author": "mongo",
     "parent_id": "48747976", "story_id": "48747976",
     "comment_text": "MongoDB | Senior SRE | ONSITE Hybrid 3 days&#x2F;wk | Full-Time"
                     "<p>Go and Kubernetes."},
]}


def test_hnhiring_takes_direct_employer_link_and_scales_k():
    """`$150k - $200k` — это 150 000–200 000, а не 150–200. И `3 days/wk`
    зарплатой не является: общий parse_salary читал оттуда «3 USD»."""
    net = _Net({}, {"search_by_date": HN_THREAD, "v1/search?tags=comment": HN_COMMENTS})
    rows = with_net(net, lambda: W.src_hnhiring(Ctx(query="golang", limit=20)))
    js = jobs_of(rows)
    eq(len(js), 2, "оба поста разобраны")
    acme, mongo = js
    eq((acme.salary_from, acme.salary_to, acme.currency), (150000, 200000, "USD"),
       "суффикс k развёрнут в тысячи")
    eq(acme.company, "Acme Corp", "компания — первая часть строки поста")
    eq(acme.employer_url, "https://acme.example/jobs",
       "прямая ссылка из поста — контакт ближе к работодателю")
    eq(acme.url, "https://news.ycombinator.com/item?id=48763652", "ссылка на сам пост")
    eq((mongo.salary_from, mongo.salary_to), (None, None),
       "«3 days/wk» не должно превращаться в зарплату")
    true(any("Who is hiring" in n for n in summary_of(rows).raw["notes"]),
         "в сводке не назван тред, из которого взяты посты")


def test_hnhiring_asks_two_threads_by_separate_words():
    """«Go» — самая результативная формулировка на HN (115 попаданий против
    2 по «Golang» и 28 по фразе «Backend Go»: Algolia склеивает слова через И).
    Порог длины выбрасывал её целиком, а фразы уходили в поиск как есть.
    Тредов берётся два: свежий открывается 1-го числа и первую неделю почти пуст."""
    net = _Net({}, {"search_by_date": HN_THREAD, "v1/search?tags=comment": HN_COMMENTS})
    with_net(net, lambda: W.src_hnhiring(
        Ctx(query="Golang", extra_queries=("Backend Go",), limit=20)))
    asked = [u for u in net.asked if "tags=comment" in u]
    words = {u.split("query=")[1].split("&")[0] for u in asked}
    eq(words, {"golang", "backend", "go"},
       "фраза ушла в поиск целиком или двухбуквенное «go» снова выброшено")
    stories = {u.split("story_")[1].split("&")[0] for u in asked}
    eq(stories, {"48747976", "48357725"},
       "должны опрашиваться два последних треда «Who is hiring», и только они")


HN_NOISE = {"nbHits": 3, "nbPages": 2, "hits": [
    {"objectID": "1", "created_at": "2026-07-02T16:13:19Z", "author": "a",
     "parent_id": "48747976", "story_id": "48747976",
     "comment_text": "Acme | Go Engineer | REMOTE | We write Go daily"},
    {"objectID": "2", "created_at": "2026-07-02T17:00:00Z", "author": "b",
     "parent_id": "999", "story_id": "48747976",
     "comment_text": "Gosh, that hourly range is sort of staggeringly wide."},
    {"objectID": "3", "created_at": "2026-07-02T18:00:00Z", "author": "c",
     "parent_id": "48747976", "story_id": "48747976",
     "comment_text": "Governance Analyst | Onsite | Compliance work only"},
]}
HN_PAGE2 = {"nbHits": 3, "nbPages": 2, "hits": [
    {"objectID": "4", "created_at": "2026-07-03T10:00:00Z", "author": "d",
     "parent_id": "48747976", "story_id": "48747976",
     "comment_text": "Wolt | Backend Engineer (Go) | Helsinki | Full-time"},
]}


def test_hnhiring_paginates_and_drops_prefix_noise_and_replies():
    """`hitsPerPage` упирается в 100 и БЕЗ пагинации молча резал выдачу
    (115 попаданий → 100 строк). Плата за широкий невод — шум: Algolia ищет
    по префиксу, поэтому «Go» ловит «Gosh» и «Governance», а половина
    комментариев треда — обсуждение, а не вакансии."""
    net = _Net({}, {"search_by_date": {"hits": HN_THREAD["hits"][:2]},
                    "page=1": HN_PAGE2, "tags=comment": HN_NOISE})
    rows = with_net(net, lambda: W.src_hnhiring(Ctx(query="Go", limit=20)))
    ids = [v.external_id for v in jobs_of(rows)]
    eq(ids, ["1", "4"], "в отчёт попали префиксный шум или ответы на посты")
    s = summary_of(rows)
    eq(s.raw["skipped_kind"], 1, "ответ в треде посчитан вакансией, а не обсуждением")
    eq(s.raw["skipped_profile"], 1, "«Governance» по запросу «Go» — это шум префикса")
    eq(s.raw["mismatch"], 0, "счётчики не сошлись")
    true(any("page=1" in u for u in net.asked), "вторая страница Algolia не спрошена")


def test_hn_salary_helper():
    eq(W._hn_salary("$150k - $200k"), (150000, 200000, "USD"), "диапазон с k")
    eq(W._hn_salary("£70,000 to £90,000"), (70000, 90000, "GBP"), "диапазон без k")
    eq(W._hn_salary("€120k"), (120000, None, "EUR"), "одиночная сумма с k")
    eq(W._hn_salary("ONSITE Hybrid 3 days/wk"), (None, None, None),
       "число без валюты зарплатой не считается")


# ──────────────────────────────────────────────────────────────────────────────
# levels.fyi — справочник, а не вакансии
# ──────────────────────────────────────────────────────────────────────────────

LEVELS_HTML = """<html><head><title>Backend Salary</title></head><body>
<script id="__NEXT_DATA__" type="application/json">""" + json.dumps({"props": {"pageProps": {
    "locationCurrency": "USD",
    "jobTitle": {"name": "Backend Software Engineer", "slug": "backend-software-engineer"},
    "defaultCountryMedian": 194480,
    "serverJobTitlePercentiles": {
        "jobFamily": "Software Engineer", "jobTitle": "Backend Software Engineer",
        "count": 7237,
        "totalCompensation": {"p10": 109000, "p25": 145000, "p50": 194480,
                              "p75": 261000, "p90": 340000},
        "baseSalary": {"p50": 160000}, "bonus": {"p50": 0},
        "stockGrant": {"p50": 17668.758}},
}}}) + """</script></body></html>"""


def test_levels_benchmark_is_a_reference_not_a_vacancy():
    """Функция отдаёт словарь-справочник: суммы годовые и подписаны как годовые,
    иначе медиана 194 480 встанет в колонку «деньги» рядом с месячными вилками."""
    got = with_render(LEVELS_HTML, lambda: W.levels_benchmark("backend"),
                      url="https://www.levels.fyi/t/software-engineer/title/backend")
    eq(got["median_total"], 194480, "медиана взята из стейта, а не из «$194K» вёрстки")
    eq(got["currency"], "USD", "валюта из locationCurrency")
    eq(got["period"], "year", "levels.fyi считает компенсацию за год")
    eq(got["sample_size"], 7237, "размер выборки — часть ответа, без него цифра голая")
    true("levels" not in W.WEB_SOURCES, "справочник зарплат не должен быть источником вакансий")


def test_levels_wall_is_not_data():
    """AWS WAF отдаёт 202 с челленджем. Это АНТИБОТ, а не «нет данных»."""
    try:
        with_render(LEVELS_WALL, lambda: W.levels_benchmark("backend"))
        FAILS.append("levels.fyi: челлендж не превратился в BlockedError")
    except BlockedError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

def test_registry_is_coherent():
    """Каждая площадка вызывается одинаково и подписана в примечаниях."""
    known = {**W.WEB_SOURCES, **W.WEB_REFERENCE}
    for name, fn in known.items():
        true(callable(fn), f"{name}: не функция")
    for name in W.WEB_SOURCE_NOTES:
        true(name in known, f"примечание к неизвестному источнику {name}")
    for name in W.WEB_NEEDS_BROWSER:
        true(name in W.WEB_SOURCES, f"браузерный источник {name} не в реестре")
    for name in W.WEB_NEEDS_BROWSER_MAP:
        true(name in known, f"браузерный источник {name} не в реестре")
    true("stackoverflowjobs" in W.WEB_DEAD,
         "мёртвый stackoverflowjobs должен быть записан, чтобы его не проверяли снова")
    true("hnhiring" in W.WEB_SOURCES, "замена stackoverflowjobs не подключена")


def test_query_relevance_understands_go():
    """«Go Developer» по запросу «Golang» — это попадание, а не промах."""
    rx = W.query_re(Ctx(query="Golang"))
    true(bool(rx.search("Senior Go Developer")), "Go не признан вариантом Golang")
    true(bool(rx.search("Golang-Entwickler")), "Golang не найден в немецком названии")
    true(not rx.search("Auxiliaire Petite Enfance volant"),
         "нянька признана подходящей под запрос Golang")


def main() -> int:
    for fn in (test_tally_shows_mismatch,
               test_wall_is_recognized_not_parsed,
               test_glassdoor_reports_wall_and_never_bypasses,
               test_glassdoor_parses_when_wall_is_down,
               test_glassdoor_parses_search_cards_when_there_is_no_ldjson,
               test_glassdoor_broken_markup_is_a_failure_not_zero,
               test_hackoffer_parses_ssr_json,
               test_hackoffer_stops_on_empty_page_not_on_error,
               test_hackoffer_pages_are_counted_by_window_not_by_limit,
               test_hackoffer_says_out_loud_when_the_fuse_cut_the_crawl,
               test_hackoffer_refuses_unknown_group,
               test_hackoffer_warns_when_group_returns_whole_catalog,
               test_dreamoffer_two_step_query_and_period,
               test_dreamoffer_searches_by_words_because_ilike_is_literal,
               test_dreamoffer_filters_alien_professions_from_a_wide_net,
               test_dreamoffer_title_is_not_a_channel_label,
               test_dreamoffer_one_dead_word_does_not_kill_the_source,
               test_dreamoffer_refuses_anything_but_select,
               test_dreamoffer_quotes_query,
               test_rabota_reads_ldjson_and_zero_max_is_not_zero,
               test_rabota_reads_the_second_page_and_pauses_between_requests,
               test_rabota_tls_drop_is_throttling_not_a_crash,
               test_rabota_keeps_what_it_managed_to_collect_before_the_throttle,
               test_rabota_skips_two_letter_query,
               test_rabota_without_ldjson_is_a_failure,
               test_getmatch_drops_promo_and_prefixes_url,
               test_getmatch_ats_all_keeps_everything,
               test_eures_filters_fuzzy_hits_and_takes_period_from_source,
               test_eures_asks_for_full_pages_only,
               test_eures_does_not_stop_at_the_first_page_without_hits,
               test_relocateme_takes_the_whole_board_and_filters_itself,
               test_relocateme_stops_when_a_page_brings_nothing_new,
               test_jobsdb_salary_range_keeps_upper_bound,
               test_jobsdb_sorts_by_date_and_walks_the_whole_window,
               test_jobsdb_salary_helper,
               test_hnhiring_takes_direct_employer_link_and_scales_k,
               test_hnhiring_asks_two_threads_by_separate_words,
               test_hnhiring_paginates_and_drops_prefix_noise_and_replies,
               test_hn_salary_helper,
               test_levels_benchmark_is_a_reference_not_a_vacancy,
               test_levels_wall_is_not_data,
               test_registry_is_coherent,
               test_query_relevance_understands_go):
        fn()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("все проверки прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())
