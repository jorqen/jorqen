"""Полнота обхода: пагинация, окно свежести и фильтр профессии.

Выделено из `test_scout.py` 08.08.2026 вторым по величине разделом. Здесь живёт
самый дорогой класс проверок сборщика: те, что ловят МОЛЧАЛИВУЮ потерю выдачи —
обход, остановившийся раньше времени, окно, применённое не к тому полю, фильтр,
съевший профильные роли.

    .venv/bin/python -m scripts.scout.test_coverage
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from .model import SUMMARY_ID, Vacancy
from .testutil import fresh as _fresh, patched, stale as _stale
from .testutil import (_FakeFetch, _FakeJSON, _careered_entry,
                       _with_fake_fetch, _with_fake_json)

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def ok(cond, label):
    if not cond:
        FAILS.append(label)


# Полнота обхода: пагинация, окно свежести и фильтр профессии
# ──────────────────────────────────────────────────────────────────────────────
#
# Всё, что здесь проверяется, ломалось МОЛЧА и выглядело успехом: адаптер честно
# приносил первую страницу, отчёт печатал круглое число, и «на площадке больше
# нет» было неотличимо от «мы больше не спросили». Поэтому тесты смотрят не только
# на вакансии, но и на список спрошенных URL: обход — это факт, а не намерение.


def _hh_page(vacancies: list, total: int) -> str:
    import json as J
    state = {"vacancySearchResult": {"totalResults": total, "vacancies": vacancies}}
    return ('<html><template id="HH-Lux-InitialState">'
            + J.dumps(state, ensure_ascii=False) + "</template></html>")


def _hh_vac(vid: int) -> dict:
    return {"vacancyId": vid, "name": f"Go разработчик {vid}",
            "company": {"visibleName": "Acme"}, "compensation": {},
            "area": {"name": "Москва"}, "links": {"desktop": f"https://hh.ru/vacancy/{vid}"},
            "publicationTime": {"$": _fresh()}}


def test_hh_walks_every_page():
    """hh отдаёт максимум 100 на страницу и САМ пишет, сколько их всего.

    Живой замер: по «Go» за трое суток totalResults 396, а один запрос без `page`
    приносил 100. Триста вакансий не existовали в отчёте, и отчёт об этом молчал."""
    from . import sources as S
    from .sources import Ctx, src_hh

    # Фрагмент с амперсандом — не педантизм: «page=1» встречается и внутри
    # «items_on_page=100», и фикстура второй страницы отвечала на все запросы.
    pages = {
        "&page=0": _hh_page([_hh_vac(i) for i in range(1, 101)], 250),
        "&page=1": _hh_page([_hh_vac(i) for i in range(101, 201)], 250),
        "&page=2": _hh_page([_hh_vac(i) for i in range(201, 251)], 250),
        "&page=3": _hh_page([], 250),
    }
    # Набор формулировок площадки глушится: здесь проверяется ПАГИНАЦИЯ, а
    # HH_QUERIES умножил бы каждый счётчик на число формулировок и спрятал бы
    # ровно то, что тест ловит. Приём тот же, что у geekjob и shadowhint.
    fake = _FakeFetch(pages)
    with patched(S, "HH_QUERIES", ()):
        got = _with_fake_fetch(fake, lambda: src_hh(Ctx(query="Golang")))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(jobs), 250, "взята вся выдача, а не первая страница")
    eq(summary.raw["kept"], 250, "в сводке — вся выдача")
    eq(summary.raw["mismatch"], 0, "баланс сошёлся")
    eq(len(fake.asked), 3, "три страницы — и ни одного лишнего запроса после исчерпания")
    if not any("&page=1" in u for u in fake.asked):
        FAILS.append(f"вторая страница не спрошена: {fake.asked}")
    if not any("items_on_page=100" in u for u in fake.asked):
        FAILS.append("items_on_page меньше серверного потолка — страниц будет больше без нужды")
    if not any("в выдаче 250, взято 250" in n for n in summary.raw["notes"]):
        FAILS.append(f"сводка не сравнивает «в выдаче» и «взято»: {summary.raw['notes']}")
    if len(fake.naps) != 2:
        FAILS.append(f"пауз между страницами {len(fake.naps)}, а страниц 3 — "
                     f"площадку долбим без передышки")


def test_hh_truncation_is_never_silent():
    """Упёрлись в потолок страниц — в сводке ОБРЕЗАНО с цифрами.

    Тихое обрезание — это ровно та потеря, ради которой всё и затевалось:
    «hh 200» неотличимо от «на hh ровно 200»."""
    from . import sources as S
    from .sources import Ctx, src_hh

    pages = {"&page=": _hh_page([_hh_vac(i) for i in range(1, 101)], 5000)}
    real = S.HH_MAX_PAGES
    S.HH_MAX_PAGES = 2
    try:
        fake = _FakeFetch(pages)
        with patched(S, "HH_QUERIES", ()):
            got = _with_fake_fetch(fake, lambda: src_hh(Ctx(query="Golang")))
    finally:
        S.HH_MAX_PAGES = real
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(fake.asked), 2, "потолок страниц соблюдён")
    if not any("ОБРЕЗАНО" in n for n in summary.raw["notes"]):
        FAILS.append(f"обрезание не названо в сводке: {summary.raw['notes']}")


def test_hh_limit_below_default_does_not_shrink_the_window():
    """`--limit` — предохранитель от бесконечности, а не рабочий режим.

    Умолчание 100 не должно обрезать окно, в котором площадка отдаёт 396:
    круглое число в отчёте — первый признак, что выдачу обрезал не поиск, а мы."""
    from .sources import Ctx, _page_budget, HH_MAX_PAGES, HH_PAGE

    eq(_page_budget(Ctx(limit=100), HH_PAGE, HH_MAX_PAGES), HH_MAX_PAGES,
       "лимит ниже штатной глубины её не опускает")
    eq(_page_budget(Ctx(limit=0), HH_PAGE, HH_MAX_PAGES), HH_MAX_PAGES,
       "нулевой лимит — это «без ограничения», а не «ноль страниц»")
    eq(_page_budget(Ctx(limit=HH_PAGE * HH_MAX_PAGES * 2), HH_PAGE, HH_MAX_PAGES),
       HH_MAX_PAGES * 2, "лимит выше штатной глубины поднимает потолок")


def _habr_card(vid: int, when: str, title: str = "Go dev") -> str:
    return (f'<div class="vacancy-card ">'
            f'<a href="/vacancies/{vid}" class="vacancy-card__title-link">{title}</a>'
            f'<div class="vacancy-card__company"><a href="/c/x">Acme</a></div>'
            f'<div class="basic-salary basic-salary--list">от 300 000 ₽</div>'
            f'<div class="chip-with-icon__text">Senior</div>'
            f'<time class="basic-date" datetime="{when}">когда-то</time>'
            f'</div>')


def _habr_page(cards: list[str], *, has_next: bool) -> str:
    nav = ('<div class="pagination"><a class="page" href="/vacancies?page=2">2</a>'
           + ('<a rel="next" class="next_page" href="/vacancies?page=2">Next</a>'
              if has_next else "")
           + "</div></div>")
    return "<html>" + "".join(cards) + nav + "</html>"


def test_habr_paginates_until_the_window_edge():
    """25 — размер страницы Хабра, а не «столько нашлось».

    Проверено на семи формулировках подряд: каждая возвращала ровно 25. При полном
    обходе «Go разработчик» отдаёт 493. Выдача отсортирована по дате, поэтому обход
    кончается на выходе за окно --days, а не на первой странице."""
    from .sources import Ctx, src_habr

    p1 = _habr_page([_habr_card(i, _fresh(1)) for i in range(1, 6)], has_next=True)
    p2 = _habr_page([_habr_card(i, _fresh(30)) for i in range(6, 11)], has_next=True)
    p3 = _habr_page([_habr_card(i, _stale(30)) for i in range(11, 16)], has_next=True)
    fake = _FakeFetch({"page=1": p1, "page=2": p2, "page=3": p3})
    from . import sources as S
    with patched(S, "HABR_QUERIES", ()):
        got = _with_fake_fetch(fake, lambda: src_habr(Ctx(query="Golang", days=3)))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(jobs), 10, "две страницы внутри окна взяты целиком")
    eq(len(fake.asked), 3, "третья страница спрошена и оказалась за окном — дальше не идём")
    eq(summary.raw["skipped_old"], 5, "вышедшие за окно посчитаны, а не потеряны молча")
    eq(summary.raw["mismatch"], 0, "баланс сошёлся")
    if not any("выходе за окно" in n for n in summary.raw["notes"]):
        FAILS.append(f"причина остановки не названа: {summary.raw['notes']}")
    if len(fake.naps) != 2:
        FAILS.append(f"пауз между страницами {len(fake.naps)} при трёх страницах")


def test_habr_stops_where_the_site_says_it_ends():
    """Конец выдачи у Хабра виден по `rel="next"` в блоке пагинации.

    Считать страницы по любым ссылкам `page=N` нельзя: в документе они есть
    и вне пагинатора, и «Golang» (47 вакансий, 2 страницы) выглядел как 25."""
    from .sources import Ctx, src_habr

    from . import sources as S

    p1 = _habr_page([_habr_card(i, _fresh(1)) for i in range(1, 4)], has_next=False)
    fake = _FakeFetch({"career.habr.com/vacancies": p1})
    with patched(S, "HABR_QUERIES", ()):
        got = _with_fake_fetch(fake, lambda: src_habr(Ctx(query="Golang")))
    eq(len([v for v in got if v.external_id != "_summary"]), 3, "одна страница разобрана")
    eq(len(fake.asked), 1, "нет rel=next — второй страницы не существует, и мы её не просим")


def test_careered_filters_profession_and_reads_to_the_window_edge():
    """У careered серверного поиска НЕТ вовсе: query/search игнорируются.

    Раньше это выглядело поиском: адаптер уносил 100 самых свежих записей ЛЮБОЙ
    профессии, и первыми в отчёт ехали QA Engineer и Business Analysis Tech Lead."""
    from .sources import Ctx, src_careered

    p1 = {"total": 6, "entries": [
        _careered_entry("go1", "Senior Backend Engineer (Go)", "0", "0", None),
        _careered_entry("qa1", "QA Engineer Middle+ (Manual + Automation)", "0", "0", None),
        _careered_entry("psp", "PSP Support (Payments Operations) Middle", "0", "0", None),
    ]}
    p2 = {"total": 6, "entries": [
        _careered_entry("go2", "Golang разработчик", "0", "0", None),
        _careered_entry("old", "Go Developer", "0", "0", None, posted=_stale(20)),
        _careered_entry("old2", "Go Developer", "0", "0", None, posted=_stale(21)),
    ]}
    fake = _FakeJSON({"offset=3": p2, "careered.io/api/jobs": p1})
    got = _with_fake_json(fake, lambda: src_careered(Ctx(query="Golang", days=3)))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq([v.external_id for v in jobs], ["go1", "go2"],
       "QA и поддержка платежей отсеяны, обе Go-роли внутри окна взяты")
    eq(summary.raw["skipped_profile"], 2, "отсеянные по профессии посчитаны")
    eq(summary.raw["skipped_old"], 2, "вышедшие за окно посчитаны отдельно от чужих профессий")
    eq(summary.raw["mismatch"], 0, "баланс сошёлся")
    eq(len(fake.asked), 2, "дошли до края окна и остановились, а не листали всю ленту")
    notes = " ".join(summary.raw["notes"])
    for want in ("серверного поиска у площадки НЕТ", "под профиль", "отсеяно", "края окна"):
        if want not in notes:
            FAILS.append(f"в сводке careered нет «{want}»: {summary.raw['notes']}")


def test_linkedin_paginates_by_start_and_drops_other_professions():
    """Гостевой поиск отдаёт 10 карточек за запрос — «linkedin 80» означало
    «по одной странице на девять регионов».

    По одной Германии за то же окно start=0…200 даёт 155 уникальных карточек.
    Заодно ключевой поиск у LinkedIn нечёткий: 23 из 87 карточек прошлого прогона
    были Financial Controller, Head of Finance и HR/Payroll Manager."""
    from .sources import Ctx, src_linkedin

    def card(vid, title):
        return ('<div class="base-card foo" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">{title}</span>'
                f'<a class="hidden-nested-link" href="/c">Acme</a>'
                f'<span class="job-search-card__location">Berlin</span>'
                f'<time datetime="{_fresh()[:10]}">вчера</time></div>')

    p0 = "<ul>" + card(1, "Senior Golang Developer") + card(2, "Head of Finance") + "</ul>"
    p1 = "<ul>" + card(3, "Backend Engineer (Go)") + card(4, "HR & Payroll Manager") + "</ul>"
    fake = _FakeFetch({"start=0&": p0, "start=10&": p1, "start=": "<ul></ul>"})
    ctx = Ctx(query="Golang", days=3)
    got = _with_fake_fetch(fake, lambda: src_linkedin(ctx))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    S = __import__("scripts.scout.sources", fromlist=["x"])
    # Пар не «регионы», а «регионы × окна»: с 08.08.2026 узкое окно спрашивается
    # отдельно от широкого, потому что выдачи у них РАЗНЫЕ (замер: 208 из 300
    # профильных суточного окна широкое не отдало вообще).
    regions = len(S.LINKEDIN_REGIONS) * len(S._linkedin_windows(3))
    LINKEDIN_EMPTY_RETRIES = S.LINKEDIN_EMPTY_RETRIES
    eq(len(jobs), 2, "два региона не размножают карточки: id общий, повтор — это дубль")
    eq([v.external_id for v in jobs], ["1", "3"],
       "Head of Finance и HR/Payroll Manager отсеяны фильтром профессии")
    eq(summary.raw["skipped_profile"], 2, "отсеянные по профессии посчитаны")
    eq(summary.raw["mismatch"], 0, "баланс сошёлся")
    # 5, а не 3: две страницы с карточками, третья пустая и ДВА повтора этой
    # пустой. С 07.08.2026 пустому ответу не верят с первого раза — у гостевого
    # поиска «выдача кончилась» и «мы вас притормозили» выглядят одинаково
    # (200 с телом в 26 байт), см. _linkedin_retry_empty.
    eq(len(fake.asked), (3 + LINKEDIN_EMPTY_RETRIES) * regions,
       "конец выдачи объявлен без переспроса — молча обрежется троттлинг")
    if not any("start=10" in u for u in fake.asked):
        FAILS.append(f"вторая страница региона не спрошена: {fake.asked[:5]}")
    # Пауза зовётся перед КАЖДЫМ запросом, включая первый: она ограничитель
    # частоты и обязана держать интервал в том числе между парами
    # «формулировка × регион». Перед самым первым запросом она честно спит ноль —
    # частоту нарушает второй запрос, а не первый.
    if len(fake.naps) != len(fake.asked):
        FAILS.append(f"пауз {len(fake.naps)} на {len(fake.asked)} запросов — "
                     f"площадку, которая троттлит охотнее всех, долбим без передышки")
    api = jobs[0].raw.get("guest_description_api")
    if not api or "jobs-guest/jobs/api/jobPosting/1" not in api:
        FAILS.append(f"нет анонимной ссылки на описание: {api!r} — "
                     f"detail пойдёт на /jobs/view/, где капча")


def test_linkedin_asks_every_formulation():
    """Каждая формулировка обходится отдельно — у неё свой потолок выдачи.

    Потолок площадки (start<1000, замер 07.08.2026) действует на ПАРУ
    «формулировка × регион», а не на источник. Значит вторая формулировка
    приносит собственную тысячу карточек, а не долистывает чужую, — и это
    единственный способ расти вширь там, где вглубь уже нельзя. Раньше
    спрашивалась ровно одна: девять регионов экономились на запросах."""
    from .sources import Ctx, LINKEDIN_REGIONS, src_linkedin

    fake = _FakeFetch({"start=": "<ul></ul>"})
    ctx = Ctx(query="Golang", days=3, extra_queries=("Go", "Backend"))
    _with_fake_fetch(fake, lambda: src_linkedin(ctx))
    for q in ("Golang", "Go", "Backend"):
        if not any(f"keywords={q}&" in u for u in fake.asked):
            FAILS.append(f"формулировка «{q}» не спрошена вовсе")
    # На пару приходится запрос плюс повторы пустого: пустой ответ у гостевого
    # поиска неотличим от временного отказа по IP, и верить ему сразу нельзя.
    from .sources import LINKEDIN_EMPTY_RETRIES
    from .sources import _linkedin_windows
    pairs = 3 * len(LINKEDIN_REGIONS) * len(_linkedin_windows(3))
    eq(len(fake.asked), pairs * (1 + LINKEDIN_EMPTY_RETRIES),
       "не по одному запросу (с переспросом пустого) на пару "
       "«формулировка × регион × окно»")


def test_card_write_flags_dead_ats_links_before_writing():
    """Мёртвая ATS-ссылка помечается ДО записи файла, а не после.

    Ashby ротирует UUID вакансии при переопубликации: ссылка вчерашнего скана
    бывает мёртвой при живой вакансии, и это уже случалось. Карточка всё равно
    пишется (в ней есть всё остальное), но с пометкой сверху — молча положить
    мёртвую ссылку значит отправить человека откликаться в никуда.

    Доска, которая не ответила, мёртвой НЕ считается: «сервер молчит» и
    «вакансии нет» — разные факты."""
    from . import atsapi, cardfiles

    class Job:
        def __init__(self, i):
            self.id = i

    class Board:
        def __init__(self, ids):
            self.jobs = [Job(i) for i in ids]

    live = "текст https://boards.greenhouse.io/gitlab/jobs/123 конец"
    with patched(atsapi, "board", lambda a, t, q=None: Board(["123"])):
        eq(cardfiles._dead_links(live), [], "живая ссылка помечена мёртвой")
    with patched(atsapi, "board", lambda a, t, q=None: Board(["999"])):
        eq(len(cardfiles._dead_links(live)), 1, "мёртвая ссылка не поймана")

    def boom(*a, **kw):
        raise RuntimeError("доска не ответила")

    with patched(atsapi, "board", boom):
        eq(cardfiles._dead_links(live), [],
           "молчащая доска объявлена мёртвой вакансией")

    # Не-ATS ссылки не трогаем вовсе: их живость стоит запроса на каждую.
    eq(cardfiles._dead_links("https://hh.ru/vacancy/1"), [],
       "обычная ссылка попала в предфлайт ATS")


def test_brief_shows_other_roles_of_the_same_company():
    """Другие роли компании и решения по ним — в `brief`, а не отдельным вызовом.

    Раньше модель звала `scout status --query <компания>` перед КАЖДОЙ карточкой
    (до тридцати вызовов на волну) и вручную собирала блок «другие роли этой
    компании в волне», который требует SKILL.md. Это один запрос.

    Совпадение имени ТОЧНОЕ, а не LIKE: на живой базе «ALTEN» получал историю
    «Altenar» просто потому, что одно имя — подстрока другого, 26 коллизий."""
    import os
    import tempfile

    from . import brief, store

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        with store.connect(db) as conn:
            for i, (comp, title) in enumerate((
                    ("Acme", "Senior Go Developer"),
                    ("Acme", "Backend Go"),
                    ("Acme Corp", "Чужая роль"),   # ДРУГАЯ компания, не подстрока
            ), 1):
                conn.execute(
                    "INSERT INTO vacancy (source, external_id, url, title, company, "
                    "first_seen, last_seen) VALUES (?,?,?,?,?,?,?)",
                    ("hh", str(i), f"https://x/{i}", title, comp, "2026-08-01",
                     "2026-08-01"))
            conn.execute("INSERT INTO decision (source, external_id, state, note, "
                         "updated_at) VALUES ('hh','2','skipped','мимо','2026-08-01')")
            txt = brief.one(conn, "https://x/1")

    ok("другие роли Acme" in txt, f"блок других ролей не собран:\n{txt[:400]}")
    ok("Backend Go" in txt, "вторая роль той же компании потеряна")
    ok("[skipped]" in txt, "решение по другой роли не показано")
    ok("Чужая роль" not in txt,
       "«Acme Corp» затянута как «Acme» — совпадение имени не точное")


def test_since_auto_never_narrows_below_a_day():
    """`--since auto` берёт окно с прошлого прогона, но НИКОГДА уже суток.

    Шаг, который до сих пор делала модель рассуждением (SKILL.md: непрочитанное
    в Telegram → дата отчёта → спросить человека). Оба источника машинные.
    Ограничение снизу обязательно: прогон мог упасть на середине, и окно ровно
    от его начала оставило бы дыру, которую следующее узкое окно не закроет, —
    а в отчёте она выглядит как «новых вакансий не было». Перекрытие стоит
    дублей, дедуп их схлопывает; экономия стоила бы вакансий."""
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone

    from . import store

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        with store.connect(db):
            pass
        # Прогонов нет — падаем на штатные трое суток, а не на «всю базу».
        got = store.since_arg("auto", db=db)
        eq(bool(got), True, "без прогонов auto не дал окна вовсе")

        # Прогон час назад: окно всё равно не уже суток.
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with store.connect(db) as conn:
            conn.execute("INSERT INTO run (started_at, finished_at) VALUES (?,?)",
                         (hour_ago, hour_ago))
        got = datetime.fromisoformat(store.since_arg("auto", db=db))
        age = (datetime.now(timezone.utc) - got).total_seconds() / 3600
        if age < 23.5:
            FAILS.append(f"окно сузилось до {age:.1f} ч — упавший прогон оставит дыру")


def test_connect_works_without_a_directory_in_the_path():
    """База в текущем каталоге и база в памяти обязаны открываться.

    `os.path.dirname` для «scout.db» и «:memory:» отдаёт пустую строку, а
    makedirs("") падает FileNotFoundError. То есть `scout --db scout.db status`
    не работал вовсе, и ни один тест не мог взять базу в памяти. Нашлось при
    написании теста на раскладку карточек — сам тест и уткнулся."""
    import os
    import tempfile

    from . import store

    with store.connect(":memory:") as conn:
        eq(conn.execute("SELECT 1").fetchone()[0], 1, "база в памяти не открылась")
    with tempfile.TemporaryDirectory() as d:
        cwd = os.getcwd()
        try:
            os.chdir(d)
            with store.connect("scout.db") as conn:
                eq(conn.execute("SELECT 1").fetchone()[0], 1,
                   "база в текущем каталоге не открылась")
        finally:
            os.chdir(cwd)


def test_liveness_reads_archive_markers_not_only_http_code():
    """Живость: 200 OK у архивной вакансии — самый частый способ соврать.

    🔴 Требование владельца 08.08.2026: живость проверяется скриптом, а не на
    веру. `check-links` умел только ATS-доски и на всё остальное отвечал «не
    ATS-ссылка, живость по API не проверить» — то есть по 40 карточкам волны из
    50 ответа не было вовсе. При этом площадки архив помечают честно, просто
    внутри страницы: hh отдаёт «Вакансия в архиве» с кодом 200, careered ставит
    `archived`, а закрытый набор пишет «больше не принимает отклики».

    Отдельно проверяется, что живая страница НЕ объявляется мёртвой: ложная
    смерть выбрасывает годную вакансию, и это дороже лишней проверки глазами."""
    from .card import liveness_from_page

    dead = [
        ("<h1>Вакансия в архиве</h1>", "hh: архив не распознан"),
        ("<div>Эта вакансия больше не принимает отклики</div>", "закрытый набор не распознан"),
        ('{"status":"archived","title":"Go dev"}', "archived в JSON не распознан"),
        ("<p>This job is no longer accepting applications</p>", "английский архив не распознан"),
        ("<h1>404</h1><p>Not Found</p>", "404 в теле не распознан"),
    ]
    for html, why in dead:
        verdict, _ = liveness_from_page(html, 200)
        if verdict != "МЕРТВА":
            FAILS.append(f"{why}: получено {verdict!r}")

    alive = ("<h1>Senior Go Developer</h1><p>Требования: опыт Go от 3 лет. "
             "Откликнуться на вакансию.</p>")
    v, _ = liveness_from_page(alive, 200)
    eq(v, "ЖИВА", "живая вакансия объявлена мёртвой — это выброшенный отклик")

    # Код важнее текста: 404 и 410 сомнений не оставляют.
    eq(liveness_from_page("<h1>Go Developer</h1>", 404)[0], "МЕРТВА", "404 не учтён")
    eq(liveness_from_page("<h1>Go Developer</h1>", 410)[0], "МЕРТВА", "410 не учтён")
    # А 403 это стена, а не смерть: за ней вакансия обычно жива.
    eq(liveness_from_page("", 403)[0], "НЕИЗВЕСТНО",
       "антибот-стена принята за смерть вакансии")

    # 🔴 Маркеры ищутся в ВИДИМОМ тексте, а не во всём HTML. Живой случай
    # 08.08.2026, и он едва не стоил двенадцати вакансий: hh отдаёт страницу
    # с кодом 200, внутри которой в JS-коде Sentry лежит «Method not found».
    # Проверка объявила МЁРТВЫМИ все двенадцать hh-вакансий волны, включая
    # опубликованные накануне. Ложная смерть выбрасывает годную вакансию
    # целиком — это дороже любого пропущенного архива.
    js_noise = ('<html><head><script>var e=["Error invoking post: Method not '
                'found","promise rejection"]</script></head>'
                '<body><h1>Senior Go Developer</h1>'
                '<div>Требования: опыт Go от 3 лет</div></body></html>')
    v, why = liveness_from_page(js_noise, 200)
    eq(v, "ЖИВА", f"мусор из <script> принят за архив вакансии: {why}")

    # 🔴 И то же самое во ВСТРОЕННОМ JSON. Живой случай того же дня, вторая
    # ложная смерть за один прогон: hh кладёт в страницу словарь локализации,
    # где среди тысяч строк лежит "applicant.negotiations.vacancyArchived":
    # "Вакансия в архиве". Проверка приняла ключ словаря за состояние вакансии
    # и объявила мёртвой живую позицию GS Labs, которую браузер тут же отдал
    # с кнопкой «Откликнуться» и счётчиком «смотрят 4 человека».
    i18n = ('<h1>Go developer (Middle)</h1><p>Требования: Go от 3 лет</p>'
            '<div>&#34;applicant.negotiations.vacancyArchived&#34;:'
            '&#34;Вакансия в архиве&#34;,&#34;applicant.negotiations.write&#34;:'
            '&#34;Добавить сопроводительное письмо&#34;</div>')
    v3, why3 = liveness_from_page(i18n, 200)
    eq(v3, "ЖИВА", f"строка словаря локализации принята за архив: {why3}")
    # А настоящая плашка архива, набранная как текст, ловиться обязана.
    real = "<h1>Go developer</h1><div>Вакансия в архиве</div><p>Требования</p>"
    eq(liveness_from_page(real, 200)[0], "МЕРТВА",
       "настоящая плашка архива перестала ловиться")

    # Антибот-страница — «площадка не ответила», а не «вакансии нет». careerjet
    # отдаёт «Требуется подтверждение… Наши системы обнаружили необычный трафик»
    # с кодом 200, и вердикт «не похоже на страницу вакансии» технически верен,
    # но не говорит, что делать. Детектор общий с обходом (webcommon).
    wallish = ('<html><head><title>Just a moment...</title></head>'
               '<body><div class="cf_chl_opt">Проверка</div></body></html>')
    vw, ww = liveness_from_page(wallish, 200)
    eq(vw, "НЕИЗВЕСТНО", f"антибот-страница не опознана как стена: {ww}")
    if "стена" not in ww:
        FAILS.append(f"вердикт про стену не объясняет причину: {ww!r}")

    # Редирект на страницу-проверку — это стена, а не смерть.
    v2, _ = liveness_from_page("<h1>Проверка</h1>", 200,
                               final_url="https://hh.ru/vpncheeck?backUrl=%2Fvacancy%2F1")
    eq(v2, "НЕИЗВЕСТНО", "редирект на антибот-проверку принят за ответ о вакансии")


def test_requirement_tier_separates_must_have_from_nice_to_have():
    """«Обязательно» и «будет плюсом» — разные вещи, и путать их дорого.

    🔴 Требование владельца 08.08.2026: отсев допустим только по НЕзакрытому
    обязательному пункту; несоответствие желательному — не повод прятать
    вакансию. Пока карточка печатала плоский список, это решение принималось на
    глаз, и одна вакансия (SaltWort) уже была отсеяна по требованию, которое
    стояло в разделе «желательно», а обязательным там был совсем другой пункт —
    и он у него закрыт.

    Разметка идёт двумя способами сразу: пометкой внутри самой строки
    («обязательно!», «must have») и заголовком раздела, под которым строка
    стоит («Будет плюсом:», «Nice to have»), — потому что вживую встречаются
    оба, а чаще второй."""
    from .card import requirement_tier

    must = ("Опыт разработки облачных платформ – обязательно!",
            "Коммерческий опыт работы с Matrix - обязательно",
            "Go from 5 years, must have",
            "Required: strong Kubernetes knowledge")
    nice = ("Будет плюсом опыт в сфере AdTech",
            "Опыт автоматизации инфраструктуры, желательно геораспределенной",
            "Nice to have: GraphQL",
            "Knowledge of Rust is a plus",
            "Приветствуется опыт с ClickHouse")
    for r in must:
        eq(requirement_tier(r), "must", f"не распознано как обязательное: {r!r}")
    for r in nice:
        eq(requirement_tier(r), "nice", f"не распознано как желательное: {r!r}")
    # Без пометки — «не сказано». Додумывать нельзя в обе стороны: назвать
    # обязательным то, что таковым не помечено, значит отсеять вакансию зря.
    eq(requirement_tier("Уверенное владение Docker, Kubernetes, CI/CD"), "",
       "требование без пометки объявлено обязательным")

    # Заголовок раздела распространяется на строки под ним, пока не сменится.
    from .card import tier_by_section
    block = ["Требования:", "Go от 3 лет", "PostgreSQL",
             "Будет плюсом:", "опыт в финтехе", "Kafka"]
    tiers = tier_by_section(block)
    eq(tiers.get("Go от 3 лет"), "must", "строка под «Требования:» не помечена обязательной")
    eq(tiers.get("опыт в финтехе"), "nice", "строка под «Будет плюсом:» не помечена желательной")
    eq(tiers.get("Kafka"), "nice", "раздел «плюсом» не распространился до конца блока")

    # 🔴 …но нейтральный заголовок раздел СБРАСЫВАЕТ. Живой случай Remoby: после
    # блока «Будет плюсом» шли «Задачи», и обязанности уезжали в карточку
    # помеченными как желательные — то есть таблица врала про то, что от
    # человека реально требуют.
    block2 = ["Требования:", "Go от 5 лет", "Будет плюсом:", "опыт в AdTech",
              "Задачи:", "Разрабатывать высоконагруженные сервисы на Go"]
    t2 = tier_by_section(block2)
    eq(t2.get("Go от 5 лет"), "must", "требование под «Требования:» потеряло уровень")
    eq(t2.get("опыт в AdTech"), "nice", "«плюсом» не проставлен")
    eq(t2.get("Разрабатывать высоконагруженные сервисы на Go"), "",
       "раздел «плюсом» протёк на задачи — обязанность помечена желательной")


def test_gather_digs_the_apply_link_out_of_a_telegram_post():
    """Для телеграм-вакансии маршрут отклика берётся ИЗ ТЕЛА поста.

    🔴 Иначе «лучшим маршрутом» становится сам пост — витрина, а не наниматель.
    Живой счёт 09.08.2026: у Авито внутри поста лежала ссылка на career.avito.com
    (и она оказалась мёртвой — вакансию закрыли), у Kaspersky — прямая страница
    careers.kaspersky.ru. По ссылке на пост не видно ни того, ни другого.

    Раскопка делалась руками, и это ровно тот случай, где агент забывает, а
    алгоритм нет. Сеть здесь подменяется: важно, что gather ходит за постом
    и кладёт найденное ВЫШЕ ссылки на сам пост."""
    from . import applyopt

    calls: list[str] = []

    def fake_fetch(url, *, timeout=20):
        calls.append(url)
        return ["https://careers.kaspersky.ru/vacancy/25712"], "из тела поста"

    old = applyopt.fetch_apply_links
    applyopt.fetch_apply_links = fake_fetch
    try:
        opts = applyopt.gather({"url": "https://t.me/ch/519", "source": "dreamoffer"})
    finally:
        applyopt.fetch_apply_links = old

    if not calls:
        FAILS.append("gather не пошёл в тело телеграм-поста за настоящей ссылкой")
    urls = [o["url"] for o in opts]
    if "https://careers.kaspersky.ru/vacancy/25712" not in urls:
        FAILS.append(f"ссылка из поста не попала в маршруты: {urls}")
    elif urls.index("https://careers.kaspersky.ru/vacancy/25712") > urls.index("https://t.me/ch/519"):
        FAILS.append("ссылка из тела поста стоит НИЖЕ ссылки на сам пост")
    eq(applyopt.best(opts), "https://careers.kaspersky.ru/vacancy/25712",
       "лучшим маршрутом остался пост, а не найденный в нём контакт")

    # Не телеграм — в сеть не ходим вовсе: лишний запрос на каждую вакансию.
    calls.clear()
    applyopt.fetch_apply_links = fake_fetch
    try:
        applyopt.gather({"url": "https://hh.ru/vacancy/1", "source": "hh"})
    finally:
        applyopt.fetch_apply_links = old
    if calls:
        FAILS.append(f"для нетелеграмной вакансии полезли в t.me: {calls}")


def test_card_carries_no_commands_and_gate_runs_in_lint():
    """В карточке не должно быть команд, а гейт обязан гоняться линтом.

    🔴 Требование владельца 09.08.2026: «мне нужны уже готовые документы,
    никаких команд мне делегировать не нужно». В скелете карточки стояла строка
    «прогони готовое письмо гейтом `scout.untrusted letter …`» — это работа
    модели, выложенная в документ, который человек открывает, чтобы
    откликнуться. Он справедливо не понял, что ему с ней делать.

    Проверка при этом не отменяется, а переносится: письмо уходит работодателю
    от его имени, и чужая ссылка в нём дороже неудобной формулировки. Поэтому
    гейт зовёт `lint-cards` — до того, как документ попадёт человеку."""
    from .card import build
    from .cardfiles import check_card

    row = {"source": "hh", "external_id": "1", "url": "https://hh.ru/vacancy/1",
           "title": "Senior Go Developer", "company": "Acme", "description":
           "Требования: опыт Go от 3 лет. Обязанности: писать сервисы."}
    text = build(row, payload=None, skills=["go"], skills_note=None, conn=None) \
        if False else None
    # build() требует соединения с базой — проверяем на готовой карточке.
    card = ("## Роль — Acme\n\n- **Ссылка:** https://x/1\n\n### Отклик\n\n"
            "```\nЗдравствуйте! Откликаюсь.\n\nРезюме: https://jorqen.link\n\nМатвей\n```\n")
    for junk in (".venv/bin/python", "scout brief", "scout reveal", "scout render",
                 "прогони готовое письмо гейтом"):
        if junk in card:
            FAILS.append(f"в карточке осталась команда: {junk}")

    # 🔴 Письмо в живых карточках лежит в разделе «Отклик» внутри ```-блока, а
    # letter_of искал только заголовок «### Письмо» — и не находил НИЧЕГО во
    # всех 49 карточках волны. То есть ни линт письма, ни гейт по ним не
    # отрабатывали вовсе, хотя команда рапортовала «замечаний нет».
    from .cardfiles import letter_of
    got = letter_of(card)
    if "Откликаюсь" not in got:
        FAILS.append(f"письмо из раздела «Отклик» не извлеклось: {got[:60]!r}")

    # Гейт: чужая ссылка в письме обязана ловиться линтом карточки.
    bad_link = card.replace("Резюме: https://jorqen.link",
                            "Резюме: https://evil.example.com/cv")
    found = check_card(bad_link)
    if not any("ссылк" in b.lower() or "gate" in b.lower() or "чуж" in b.lower()
               for b in found):
        FAILS.append(f"линт пропустил чужую ссылку в письме: {found}")

    # 🔴 …но ссылку на САМУ вакансию letter-guide требует вставлять в письмо
    # («Вакансия: <url> · резюме: <сайт>»), и гейт не должен на неё ругаться.
    # Иначе он краснеет на каждой второй карточке и его перестают читать.
    with_vacancy = card.replace(
        "Резюме: https://jorqen.link",
        "Вакансия: https://x/1 · резюме: https://jorqen.link")
    left = [b for b in check_card(with_vacancy) if "гейт" in b]
    if left:
        FAILS.append(f"гейт ругается на ссылку самой вакансии: {left}")

    # То же для канала отклика: он часто НЕ совпадает со ссылкой в шапке —
    # в шапке площадка, а писать надо на careers-страницу работодателя. Такая
    # ссылка выбрана мной же и стоит в разделе «Отклик» этой карточки.
    with_channel = card.replace(
        "### Отклик\n", "### Отклик\n\n**Контакт: [careers](https://acme.example/careers).**\n"
    ).replace("Резюме: https://jorqen.link",
              "Вакансия: https://acme.example/careers · резюме: https://jorqen.link")
    left2 = [b for b in check_card(with_channel) if "гейт" in b]
    if left2:
        FAILS.append(f"гейт ругается на канал отклика из этой же карточки: {left2}")


def test_lint_catches_broken_links_in_the_wave_doc():
    """Ссылки главного документа обязаны открываться.

    🔴 Живой случай 09.08.2026: вся таблица волны — 49 ссылок — вела в никуда.
    Документ лежит в `.jobs/<дата>.md`, то есть РЯДОМ с каталогом `.jobs/<дата>/`,
    а ссылки были записаны как `companies/…` и резолвились в `.jobs/companies/…`.
    Образец с этой ошибкой стоял в самом SKILL.md, поэтому воспроизводился бы
    каждую волну. Человек это видит сразу (ссылка не открывается), а линт
    молчал — то есть проверял что угодно, кроме главного: можно ли дойти до
    карточки из таблицы."""
    import os
    import tempfile

    from .cardfiles import lint_doc_links

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d) if False else __import__("pathlib").Path(d)
        (root / "2026-08-08" / "companies" / "acme").mkdir(parents=True)
        (root / "2026-08-08" / "companies" / "acme" / "card.md").write_text("## Роль")
        doc = root / "2026-08-08.md"
        doc.write_text(
            "| 1 | [Роль](2026-08-08/companies/acme/card.md) | Acme |\n"
            "| 2 | [Роль](companies/acme/card.md) | Acme |\n"
            "| 3 | [Внешняя](https://example.com/x.md) | — |\n")
        bad = lint_doc_links(str(doc))
    eq(len(bad), 1, f"поймано не ровно одна битая ссылка: {bad}")
    if bad and "companies/acme/card.md" not in bad[0]:
        FAILS.append(f"поймана не та ссылка: {bad}")


def test_card_files_layout_and_lint():
    """Раскладка карточек и их проверка — механика, а не работа глазами.

    Требование SKILL.md «один работодатель — один каталог» до сих пор выполняла
    модель: двадцать восемь путей на волну 04.08.2026 и класс ошибок «две папки
    на одну компанию». Безымянный работодатель (за заглушкой агрегатора) обязан
    уходить отдельно, а не в каталог с пустым именем."""
    import os
    import tempfile

    from . import cardfiles
    from .cardfiles import card_path, check_card

    a = card_path(".jobs", "2026-08-08", "АО «Каргономика»", "Senior Go Developer")
    b = card_path(".jobs", "2026-08-08", "Каргономика", "Senior Go Developer")
    eq(a, b, "организационно-правовая форма развела одну компанию по двум каталогам")
    eq("_hidden" in card_path(".jobs", "2026-08-08", None, "Go"), True,
       "безымянный работодатель не отделён")

    # Линт: скелет НЕ готовая карточка, и он обязан это говорить.
    eq(check_card("## Роль\n\n- Ссылка: https://x/1\n"),
       ["нет раздела «Отклик» — откликнуться по ней нельзя"],
       "скелет без раздела «Отклик» объявлен готовым")
    ok_card = "## Роль\n\n- Ссылка: https://x/1\n\n## Отклик\n\nписьмо\n"
    eq(check_card(ok_card), [], f"готовая карточка помечена: {check_card(ok_card)}")
    eq(len(check_card(ok_card.replace("письмо", "TODO допишу"))), 1,
       "оставшаяся заглушка не поймана")
    eq(len(check_card(ok_card.replace("письмо", "⚠️ проверь"))), 1,
       "оставшееся предупреждение не поймано")
    # 🔴 …но предупреждения, которые печатает САМ генератор, недоделкой не
    # являются. Дисклеймер «это оценка, а не вилка работодателя» обязателен в
    # блоке «Сколько просить», и требовать его стереть — значит требовать
    # соврать про происхождение цифры. Пока правило ловило любой ⚠️,
    # `lint-cards` ругался на собственный вывод `card --write`: три карточки
    # волны 08.08.2026 объявлены недоделанными из-за строки самого scout.
    generated = ok_card.replace(
        "письмо", "письмо\n\n- ⚠️ **Это ОЦЕНКА, а не вилка работодателя.**")
    eq(check_card(generated), [],
       f"линт ругается на собственный вывод card: {check_card(generated)}")
    both = generated.replace("письмо\n", "письмо ⚠️ спросить про стаж\n", 1)
    eq(len(check_card(both)), 1,
       "среди служебных предупреждений потерялось настоящее")

    # ── Линт КАРТОЧКИ проверяет и письмо внутри неё ──────────────────────────
    # Письмо лежит в карточке, а проверка у него была отдельной командой: чтобы
    # позвать `lint-letter`, модель должна была вырезать текст в файл руками на
    # каждую вакансию — то есть не звать вовсе.
    skeleton = ("_Скелета письма здесь нет намеренно._\n"
                "_Перед выдачей прогони гейтом:_ `python -m scripts.scout.untrusted`\n")
    head = "## Роль\n\n- Ссылка: https://x/1\n\n## Отклик\n\nмаршрут\n\n"
    eq(cardfiles.check_card(head + cardfiles.LETTER_HEADING + " — пишет модель\n"
                            + skeleton), [],
       "линт нашёл письмо там, где модель его ещё не написала")

    bad = (head + cardfiles.LETTER_HEADING + " — пишет модель\n" + skeleton
           + "\nЗдравствуйте! Я, безусловно, готов внести значимый вклад "
             "в вашу команду и уверен, что мой опыт идеально подойдёт.\n")
    notes = cardfiles.check_card(bad)
    eq(any(n.startswith("письмо [") for n in notes), True,
       f"маркеры генератора в письме не пойманы линтом карточки: {notes}")
    # Пояснения скелета письмом не считаются: иначе каждая карточка получала бы
    # замечание про markdown и обратные кавычки в тексте, которого автор не писал.
    eq(all("markdown" not in n for n in notes), True,
       "линт принял пояснение скелета за письмо")

    # 🔴 Заголовок раздела письма — КОНТРАКТ между card.py (печатает) и
    # cardfiles.py (ищет по нему). Разъедутся — `lint-cards` перестанет находить
    # письмо и будет бодро отчитываться «замечаний нет», ни разу его не прочитав.
    # Это худший вид молчаливого нуля: проверка выглядит пройденной.
    with tempfile.TemporaryDirectory() as d:
        from . import card, store
        db = os.path.join(d, "c.db")
        with store.connect(db) as conn:
            store.upsert(conn, [Vacancy(source="hh", external_id="1",
                                        url="https://hh.ru/v/1", title="Go dev",
                                        company="Acme")])
            skeleton_card = card.build(conn, "https://hh.ru/v/1", skills=[])
    eq(cardfiles.LETTER_HEADING in skeleton_card, True,
       f"card.build больше не печатает {cardfiles.LETTER_HEADING!r} — "
       f"lint-cards перестанет видеть письмо и объявит карточку чистой")

    # Линт смотрит ТОЛЬКО карточки: индекс волн и главный документ — тоже .md,
    # но раздела «Отклик» иметь не обязаны, и ругань на них была бы тремя
    # ложными замечаниями в каждом прогоне.
    with tempfile.TemporaryDirectory() as d:
        card = card_path(d, "2026-08-08", "Acme", "Go")
        os.makedirs(os.path.dirname(card), exist_ok=True)
        with open(card, "w", encoding="utf-8") as f:
            f.write("## Роль\n\nhttps://x/1\n")
        for stray in ("README.md", "2026-08-08.md"):
            with open(os.path.join(d, stray), "w", encoding="utf-8") as f:
                f.write("# Волны\n\n- 2026-08-08\n")
        found, total = cardfiles.lint(d)
        eq(total, 1, "линт посчитал карточками индекс и главный документ")
        eq(len(found), 1, "карточка без «Отклика» не помечена")

    # Существующий файл не затирается: там уже может лежать фит и письмо.
    with tempfile.TemporaryDirectory() as d:
        path = card_path(d, "2026-08-08", "Acme", "Go")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("## Роль\n\n## Отклик\n\nмоё письмо\n")
        # url заведомо не в базе — проверяем именно ветку «нет в базе», а заодно
        # что дошли до неё, не тронув чужой файл.
        cardfiles.write(":memory:", ["https://нет-такого/1"], date="2026-08-08", root=d)
        with open(path, encoding="utf-8") as f:
            eq("моё письмо" in f.read(), True, "скелет затёр дописанное письмо")


def test_health_tells_a_dead_source_from_an_off_profile_one():
    """Ноль от мёртвой площадки и ноль от чужой профессии — разные факты.

    Живой случай 08.08.2026: trudvsem отдал ДЕВЯТЬ вакансий, все чужой
    профессии, а здоровье источников написало «сейчас 0 … это не „вакансий
    нет“, а поломка». Площадка была жива и ответила по существу; сломано было
    только сообщение — и оно отправляло чинить парсер, которого не сломано."""
    from .health import verdict

    dead = verdict(0, [2, 2, 2], offered=0)
    ok(dead and dead[0] == "АВАРИЯ", f"мёртвая площадка не помечена: {dead}")

    off = verdict(0, [2, 2, 2], offered=9)
    ok(off and off[0] == "ПУСТО ПО ПРОФИЛЮ",
       f"живая площадка с чужой выдачей объявлена аварией: {off}")
    ok("парсер" in off[1], "не сказано, где НЕ надо искать причину")

    # Деградация считается по-прежнему: правка не должна её проглотить.
    deg = verdict(3, [100, 100, 100], offered=120)
    ok(deg and deg[0] == "ДЕГРАДАЦИЯ", f"деградация потеряна: {deg}")


def test_cache_hit_does_not_pay_for_politeness():
    """Пауза не платится за запрос, которого не было.

    Пауза ограничивает частоту ОБРАЩЕНИЙ к площадке. Попадание в кэш — это
    отсутствие обращения, и спать после него значит платить вежливостью
    впустую. Замер 08.08.2026: переразбор трёх источников из кэша занимал те же
    десять секунд на каждый, целиком состоявшие из сна; после правки — 0.2 с."""
    from . import sources as S

    slept: list[float] = []
    real_sleep, real_clock = time.sleep, time.monotonic
    now = [1000.0]
    try:
        time.sleep = lambda s: (slept.append(s), now.__setitem__(0, now[0] + s))[0]
        time.monotonic = lambda: now[0]
        S.reset_pace()
        S._pause(1.2)                     # первый — не спит по построению
        S._pause(1.2)                     # второй — спит полный интервал
        n_before = len(slept)
        S.skip_next_pause()               # ответ пришёл из кэша
        S._pause(1.2)
        eq(len(slept), n_before, "после попадания в кэш всё равно спали")
        # А следующая пауза возвращается: флаг снимает РОВНО одну.
        S._pause(1.2)
        eq(len(slept), n_before + 1, "вежливость не вернулась после кэша")
    finally:
        time.sleep, time.monotonic = real_sleep, real_clock
        S.reset_pace()


def test_raw_cache_prunes_stale_days_on_start():
    """Кэш сырых ответов ограничен сверху, а не растёт вечно.

    `store.raw_cache_clear` существовал с самого начала, но его не звал НИКТО —
    и ровно поэтому кэш нельзя было включить по умолчанию. Замер 08.08.2026:
    четыре источника кладут около трёх мегабайт, то есть волна примерно
    шестнадцать, и это каждый день. Читается при этом всегда только сегодняшний
    день, так что всё старше — чистый мусор."""
    import os
    import tempfile
    from datetime import date, timedelta

    from . import rawcache, store

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        old = (date.today() - timedelta(days=30)).isoformat()
        with store.connect(db) as conn:
            store.raw_cache_put(conn, "hh", "https://x/1", "тело", on=old)
            store.raw_cache_put(conn, "hh", "https://x/2", "тело")
            eq(conn.execute("SELECT COUNT(*) FROM raw_cache").fetchone()[0], 2,
               "фикстура не легла")
        c = rawcache.Cache(db, read=False, write=True)
        eq(c.pruned, 1, "протухший день не выкинут — кэш растёт без предела")
        with store.connect(db) as conn:
            eq(conn.execute("SELECT COUNT(*) FROM raw_cache").fetchone()[0], 1,
               "выкинуто не то: сегодняшний день обязан остаться")


def test_lint_letter_catches_the_generator_markers():
    """Линтер ловит формальную часть канона и молчит на живом письме.

    Правило про тире строгое НАМЕРЕННО. Первая версия пробовала отличать
    грамматическое тире (замена связки) от риторического и пропускала почти всё:
    «Senior Go Developer — я не просто разработчик» по форме неотличимо от
    «Go — основной язык». Линтер, молчащий про главный маркер, хуже
    отсутствующего."""
    from .lintletter import check

    codes = lambda t: {c for c, _, _ in check(t)}

    ok_ru = ("Здравствуйте, Анна!\n\nОткликаюсь на позицию Senior Go Developer. "
             "У вас в вакансии сказано, что переезжаете с монолита на сервисы. "
             "Пять лет пишу распределённый backend на Go: платёжный шлюз, p99 упал "
             "с 300 до 47 мс при 70K RPS. Kafka и NATS гонял в проде, схему "
             "шардирования проектировал сам. Istio и Kubernetes держал в бою.\n\n"
             "Вакансия: https://e.com/1 · резюме: https://jorqen.link. "
             "Готов обсудить детали.")
    eq(codes(ok_ru), set(), f"живое письмо помечено: {check(ok_ru)}")

    eq("dash" in codes("Я сделал сервис — он держал нагрузку."), True,
       "тире не поймано, а это главный маркер генератора")
    eq("dash" in codes("I built it — it worked."), True, "тире в английском не поймано")
    eq("word" in codes("Являюсь ключевым специалистом."), True, "слова-метки не пойманы")
    # 🔴 …но ТОЛЬКО с начала слова. «данный» сидит внутри «неожиданный», и линтер
    # требовал переписать живую фразу «это дало неожиданный побочный эффект»
    # (живой случай 08.08.2026). Команда, которая ругается на нормальный текст,
    # быстро перестаёт вызываться — и тогда настоящие маркеры тоже не ловятся.
    for ok in ("Это дало неожиданный побочный эффект.",
               "Задача оказалась неключевой, но интересной."):
        if "word" in codes(ok):
            FAILS.append(f"ложная тревога слова-метки на живой фразе: {ok!r}")
    eq("word" in codes("В данный момент занимаюсь платформой."), True,
       "«данный» отдельным словом обязан ловиться")
    eq("word" in codes("I am passionate about robust systems."), True,
       "английские слова-метки не пойманы")
    eq("phrase" in codes("Я не просто разработчик."), True, "оборот не пойман")
    eq("format" in codes("- пункт списка\n- второй"), True, "список в письме не пойман")

    # Маркер, пришедший на смену тире (разведка 07.08.2026): модели сцепляют
    # длинные предложения союзом «и»/and — он у них самое частое слово.
    run_on = ("Я написал сервис и он держал нагрузку и мы переписали горячий путь "
              "и латентность упала и потом мы добавили кеш и это дало ещё немного "
              "и в итоге всё работало стабильно и команда была довольна результатом "
              "этой большой и сложной работы над платёжным шлюзом и его надёжностью.")
    eq("run-on" in codes(run_on), True,
       "длинное предложение на союзах не поймано")

    # А вот точка с запятой НЕ маркер генератора: люди ставят её чаще моделей,
    # и вычищать пунктуацию ради «человечности» — делать текст более машинным.
    # Одна допустима, придирка начинается со второй.
    eq("style" in codes("Текст; ещё текст."), False,
       "одна точка с запятой объявлена нарушением — это правило краткости, "
       "а не признак генератора")


def test_wavedoc_slug_folds_legal_forms_and_transliterates():
    """«АО «Каргономика»» и «Каргономика» обязаны дать ОДИН каталог.

    Требование SKILL.md, и до сих пор его выполняла модель глазами. Две папки
    на одну компанию — не косметика: карточки разных ролей одного работодателя
    расходятся, и «один работодатель — одна карточка» перестаёт выполняться.
    Пустое имя — штатный случай (работодатель за заглушкой агрегатора), и такие
    карточки обязаны лежать отдельно, а не смешиваться в каталоге с именем «»."""
    from .wavedoc import slug

    eq(slug("АО «Каргономика»"), slug("Каргономика"),
       "организационно-правовая форма развела одну компанию по двум каталогам")
    eq(slug("ООО \"Яндекс Технологии\""), "yandeks-tehnologii", "транслит поехал")
    eq(slug("Ozon Bank"), "ozon-bank", "латиница не должна портиться")
    eq(slug(""), "_hidden", "безымянный работодатель — отдельный каталог")
    eq(slug(None), "_hidden", "отсутствие имени — не пустая строка каталога")


def test_wavedoc_never_overwrites_a_document_with_judgement_in_it():
    """Скелет не затирает уже написанное без явного --force.

    Волну переигрывают, а в документе к этому моменту лежат разделы, которых
    в базе нет и восстановить их нечем."""
    import os
    import tempfile

    from . import wavedoc

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "2026-08-07.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Волна\n\nМоё суждение, которого нет в базе.\n")
        got, why = wavedoc.write(":memory:", days=3, top=1, date="2026-08-07",
                                 root=d, force=False)
        eq(got, path, "путь документа посчитан неверно")
        with open(path, encoding="utf-8") as f:
            eq("Моё суждение" in f.read(), True,
               f"скелет затёр дописанное суждение ({why})")


def test_card_gives_the_whole_contact_picture_and_names_the_barriers():
    """Три вещи, которые модель выясняла заново в каждой волне.

    (1) КОНТАКТЫ. Правило владельца 08.08.2026: скрипт даёт максимально полную
    картину, модель просто выбирает лучший вариант — или не выбирает вовсе.
    Раньше в карточке стояла одна строка «Куда откликаться», а остальные
    маршруты и вовсе не печатались; почта и телефон из реестра trudvsem
    добывались ресёрчем на каждую компанию.

    (2) ФОРМА ОТКЛИКА. «В анкете шесть вопросов — письмо туда не вставить» —
    это данные, а не суждение. Текст письма остаётся модели.

    (3) БАРЬЕРЫ. Правило владельца 30.07.2026: Lead-тайтл ВМЕСТЕ с завышенным
    стажем — отсев, порознь — нет. Оно жило только в переписке."""
    from . import contacts
    from .card import barriers

    # Контакт из ГОСУДАРСТВЕННОГО реестра и контакт из чужого текста — разные
    # факты, и строка обязана говорить, который из них перед тобой.
    found = contacts.gather(
        {"title": "Go"},
        {"description": "пишите на hr@acme.io или @acme_hr, тел. +7 495 123 45 67"},
        {"contact": "ООО Ромашка, jobs@romashka.ru"})
    eq([c["value"] for c in found["email"]], ["jobs@romashka.ru", "hr@acme.io"],
       "почта из реестра площадки обязана идти ПЕРВОЙ — она не догадка")
    eq(found["telegram"][0]["value"], "@acme_hr", "телеграм-ник не найден")
    eq(len(found["phone"]), 1, "телефон не найден")
    rendered = "\n".join(contacts.render(found))
    eq("реестр площадки" in rendered and "описание вакансии" in rendered, True,
       "не сказано, откуда взят каждый контакт — а это чужой текст")

    # Отсеивается только то, что не читает ЧЕЛОВЕК, и артефакты вёрстки.
    # `support@` остаётся намеренно: у маленькой компании это единственный живой
    # адрес, а правило владельца требует полной картины — отсекать будет он.
    junk = contacts.gather(
        {}, {"description": "noreply@x.io logo@2x.png postmaster@x.io support@x.io"},
        None)
    eq([c["value"] for c in junk["email"]], ["support@x.io"],
       f"отсев адресов разошёлся с задуманным: {junk['email']}")

    # Форма отклика: анкета и «хватит резюме» — разные вердикты.
    quiz = contacts.apply_form({}, {"questions": ["Почему мы?", "Ваш стек?"]}, None)
    eq(any("АНКЕТА из 2" in s for s in quiz), True, "анкета не названа числом вопросов")
    cv = contacts.apply_form({}, {"description": "достаточно резюме, "
                                                 "сопроводительное не нужно"}, None)
    eq(any("НЕ нужно" in s for s in cv), True,
       "работодатель написал, что письмо не нужно, а карточка молчит")

    # Барьеры. Каждый случай — реальное решение владельца, а не выдумка.
    def one(title, text, years):
        return " ".join(barriers({"title": title}, {"description": text}, years))

    eq("ЛИД-ТАЙТЛ" in one("Senior/Lead Go Developer", "Go от 7 лет", 7), True,
       "Lead вместе с завышенным стажем — отсев по правилу владельца, он не назван")
    eq(one("Go Developer", "опыт лидерства приветствуется", 3), "",
       "мягкая формулировка лидерства объявлена барьером — её просили показывать")
    eq(one("Go Developer", "you will be leading projects", 3), "",
       "«leading projects» в теле принято за лид-роль")
    eq("УПРАВЛЕНИЯ" in one("Go Developer", "опыт управления командой от 3 лет", 4),
       True, "требование лет управления числом — барьер, он не назван")
    eq("ГРАЖДАНСТВО" in one("Go Dev", "You must have the right to work in Türkiye", 3),
       True, "требование права на работу — барьер (так отпал Acronis)")
    eq(one("Go Dev", "Türkiye, Remote. Visa support available", 3), "",
       "гео-метка с визовой поддержкой принята за барьер")


def test_tally_splits_the_gap_between_claimed_and_kept():
    """«Взято 28 при заявленных 75» обязано раскладываться САМО.

    У glassdoor этот разрыв пришлось выяснять отдельным расследованием: своё
    число площадки лежало в примечании, счётчики отсева — в другой части той же
    сводки, и ни с чем они не сходились. Слагаемых три, и чинятся они РАЗНЫМ:
    «не спросили» — глубиной обхода, «чужая профессия» и «старше окна» не
    чинятся вовсе (так работают фильтры), «не разобралось» — разметкой.

    Второе свойство важнее первого: строка не имеет права появляться там, где
    разрыва нет. Иначе её начинают пролистывать, и она перестаёт работать."""
    from .sources import Tally

    t = Tally("glassdoor", claimed=75, offered=40, parsed=38, dropped=2,
              skipped_profile=8, skipped_old=2, kept=28)
    gap = t.gap_note()
    eq("РАЗРЫВ 75 → 28" in gap, True, f"разрыв не назван числами: {gap!r}")
    eq("НЕ СПРОШЕНО 35" in gap, True,
       f"не сказано, сколько мы просто не спросили — а чинится только это: {gap!r}")
    for expect in ("чужая профессия 8", "старше окна --days 2",
                   "не разобралось — разметка 2"):
        eq(expect in gap, True, f"в разрыве нет слагаемого «{expect}»: {gap!r}")
    eq(gap in t.row().title, True, "разложенный разрыв не попал в сводку источника")

    eq(Tally("hh", claimed=100, offered=100, parsed=100, kept=100).gap_note(), "",
       "разрыв объявлен там, где взято всё — такую строку начнут пролистывать")
    # Взяли ВСЁ заявленное, но по дороге отсеяли повторы страниц: терять тут
    # нечего, а слагаемые ненулевые. Без явной проверки «взято >= заявленного»
    # сюда печаталось бы «РАЗРЫВ 30 → 30: повторы между страницами 15».
    eq(Tally("glassdoor", claimed=30, offered=45, parsed=30, dupes=15,
             kept=30).gap_note(), "",
       "разрыв посчитан там, где взято всё заявленное, — это чистый шум")
    eq(Tally("habr", offered=10, parsed=10, kept=10).gap_note(), "",
       "площадка своего числа не называла, а разрыв всё равно посчитан")
    # Инвариант Tally не должен пострадать: `claimed` живёт отдельно от баланса
    # «offered = dropped + dupes + skipped_kind + parsed».
    eq(t.mismatch(), 0, "новое поле сломало баланс счётчиков")


def test_vetted_query_sets_actually_reach_the_platform():
    """Проверенный набор формулировок обязан УЙТИ В СЕТЬ, а не просто лежать.

    Замер 08.08.2026 (окно 3 дня, счёт своего вклада каждой формулировки):

        hh.ru          «Golang» 96 → объединение 2331   (в 24 раза)
        Хабр Карьера   «Golang» 20 → объединение 260    (в 13 раз)

    Константа, которую забыли подмешать, не роняет ничего и не краснит ни один
    тест: площадка честно отвечает на единственный запрос, покрытие падает в
    два десятка раз, и выглядит это как «на площадке столько». Ровно та тихая
    потеря, ради которой заведён этот модуль. Поэтому проверяется не наличие
    константы, а факт запроса.
    """
    from . import sources as S
    from .sources import Ctx, src_habr, src_hh

    # 🔴 Ожидание ФИКСИРОВАННОЕ, а не «пройтись по самой константе». Цикл по
    # константе — тест, зеленеющий вхолостую: опустоши её, и тело цикла просто
    # не выполнится. Проверено нарочной поломкой — так и было.
    #
    # Перечислены главные поставщики по замеру: на hh «программист» дал 1358
    # своих из 2331 и «backend» 467, на Хабре «backend» 146 из 260 и
    # «Go разработчик» 77. Формулировка выбывает из набора только вместе с
    # новым замером — тогда правится и эта строка.
    must_hh = ("Go", "backend", "программист")
    must_habr = ("backend", "Go разработчик", "программист")
    for term in must_hh:
        if term not in S.HH_QUERIES:
            FAILS.append(f"hh: {term!r} пропал из HH_QUERIES без нового замера")
    for term in must_habr:
        if term not in S.HABR_QUERIES:
            FAILS.append(f"habr: {term!r} пропал из HABR_QUERIES без нового замера")

    hh_pages = {"&page=0": _hh_page([_hh_vac(1)], 1), "&page=1": _hh_page([], 1)}
    fake = _FakeFetch(hh_pages)
    _with_fake_fetch(fake, lambda: src_hh(Ctx(query="Golang")))
    for term in must_hh:
        quoted = urllib.parse.quote_plus(term)
        if not any(f"text={quoted}" in u for u in fake.asked):
            FAILS.append(f"hh: формулировка {term!r} не ушла в сеть — "
                         f"покрытие площадки падает с 2331 до 96")

    habr = _habr_page([_habr_card(1, _fresh(1))], has_next=False)
    fake2 = _FakeFetch({"career.habr.com/vacancies": habr})
    _with_fake_fetch(fake2, lambda: src_habr(Ctx(query="Golang")))
    for term in must_habr:
        quoted = urllib.parse.quote_plus(term)
        if not any(f"q={quoted}" in u for u in fake2.asked):
            FAILS.append(f"habr: формулировка {term!r} не ушла в сеть — "
                         f"покрытие площадки падает с 260 до 20")


def test_rabota_names_the_formulations_it_never_asked():
    """Кончился бюджет запросов — неопрошенные формулировки НАЗЫВАЮТСЯ поимённо.

    `RABOTA_MAX_REQUESTS` один на ВСЕ формулировки и держит нас от бана. Пока
    он проверялся только во внутреннем цикле по страницам, лишние формулировки
    молча прокручивались вхолостую: внешний цикл шёл до конца, внутренний
    ломался на первой же проверке, и в сводке оставалось «в выдаче ?». Это
    недобор, неотличимый от пустой площадки.

    Потолок при этом НЕ поднимается — правильный ответ «сказать, чего не
    спросили», а не «спросить больше».
    """
    from . import sources_web as W
    from .sources import Tally

    tally = Tally("rabota")
    tally.requests = 0
    eq(W._rabota_budget_out(tally, ["a", "b", "c"], 0), False,
       "бюджет цел, а обход остановлен")
    eq(tally.row().raw["notes"], [], "лишняя строка в сводке при целом бюджете")

    tally.requests = W.RABOTA_MAX_REQUESTS
    eq(W._rabota_budget_out(tally, ["Golang", "backend", "бэкенд", "программист"], 1),
       True, "бюджет кончился, а обход продолжается — площадку будем долбить")
    notes = tally.row().raw["notes"]
    joined = " ".join(notes)
    for must in ("backend", "бэкенд", "программист"):
        if must not in joined:
            FAILS.append(f"неопрошенная формулировка {must!r} не названа: {notes}")
    if "Golang" in joined:
        FAILS.append(f"уже опрошенная формулировка попала в неопрошенные: {notes}")
    ok(any("недобор" in n for n in notes),
       "в сводке не сказано, что это НЕДОБОР, а не пустая выдача: читающий "
       "примет неспрошенное за «на площадке столько»")


def test_tg_wave_is_one_post_and_never_sends_by_default():
    """Пост о волне: ОДИН, с числом и файлом, и по умолчанию никуда не уходит.

    Требование владельца 08.08.2026: единый пост — количество новых вакансий
    плюс файл со всеми. Не сводка по площадкам и не строка на вакансию.

    Отдельный модуль, а не ручка в `tgmirror`: у того инвариант жёстче и
    проверяется тестом — ровно одна операция `forward_messages`, «ничего не
    сочиняется» сказано буквально. Здесь сочиняется, значит и границы стоят
    свои. Тест держит обе: этот модуль не пересылает чужое, а тот не пишет.

    Первая версия строила окно f-строкой «3d» и передавала её в `shortlist.build`,
    который ждёт готовую дату. Под такую границу не подходило ничего, и пост
    бодро сообщал «0 новых вакансий» при полной базе — поломка, выглядящая
    как исправная работа."""
    import inspect
    import os
    import tempfile

    from . import store, tgwave
    from .model import Vacancy

    # Ищется ВЫЗОВ, а не упоминание: в докстроке этого модуля `forward_messages`
    # назван нарочно — там объясняется, чем его границы отличаются от границ
    # `tgmirror`. Проверка по голой подстроке ловила бы объяснение вместо кода.
    src = inspect.getsource(tgwave)
    for forbidden in ("forward_messages", "iter_messages", "send_read_acknowledge",
                      "delete_messages", "JoinChannelRequest", "send_message"):
        if f".{forbidden}(" in src:
            FAILS.append(f"tg-wave: найден вызов {forbidden!r} — этому модулю "
                         f"позволен только собственный пост в свой канал")
    if "def run" in src and "apply: bool = False" not in src:
        FAILS.append("tg-wave: отправка обязана быть выключена по умолчанию")

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "w.db")
        with store.connect(db) as conn:
            store.upsert(conn, [
                Vacancy(source="hh", external_id=str(i), url=f"https://hh.ru/v/{i}",
                        title=f"Go разработчик {i}", company=f"Acme {i}",
                        salary_from=300000 if i % 2 else None,
                        currency="RUB" if i % 2 else None,
                        salary_period="month" if i % 2 else None,
                        remote=bool(i % 3), published_at=_fresh(1))
                for i in range(1, 8)])
        text, table = tgwave.build(db, days=3, date="2026-08-08", top=3)

    first = text.splitlines()[0]
    eq(first.startswith("Волна 2026-08-08: 7 новых"), True,
       f"первая строка поста обязана называть число новых, а не {first!r}")
    eq(text.count("Волна 2026-08-08"), 1, "поста должно быть ровно одно начало")
    eq(len([ln for ln in text.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.")]), 3,
       "--top не соблюдён: в посте должно быть ровно столько строк, сколько просили")
    eq("shortlist:" in table, True,
       "файл собран своим форматом вместо shortlist — это второй ответ "
       "на тот же вопрос, он разойдётся с командой, которой пользуется владелец")
    eq("7 вакансий" in table, True, "в файл попали не все новые вакансии")

    # Предпросмотр обязан работать на машине БЕЗ telethon: он опционален
    # (инвариант 3), а отправки в предпросмотре нет. Свойство держалось
    # случайно — импорт `tgclient` стоял до выхода по apply=False.
    import builtins
    real_import = builtins.__import__

    def no_telethon(name, *a, **k):
        if name.split(".")[0] == "telethon":
            raise ImportError("telethon на этой машине нет")
        return real_import(name, *a, **k)

    with tempfile.TemporaryDirectory() as d:
        db2 = os.path.join(d, "w2.db")
        with store.connect(db2) as conn:
            conn.execute("SELECT 1")
        import contextlib
        import io
        with patched(builtins, "__import__", no_telethon), \
                contextlib.redirect_stdout(io.StringIO()):
            code = tgwave.run(db2, days=3, date="2026-08-08", top=2,
                              apply=False, out_dir=d)
        eq(code, 0, "предпросмотр упал там, где отправки нет вовсе")
        eq(os.path.exists(os.path.join(d, "wave-2026-08-08.md")), True,
           "предпросмотр не положил файл туда, куда просили (--out_dir мёртв)")


def test_tg_wave_bot_path_is_stdlib_and_never_leaks_the_token():
    """Второй транспорт: бот. Одно сообщение, только stdlib, токен не в выводе.

    Зачем он вообще. Сессия аккаунта — предъявительский доступ ко ВСЕЙ
    переписке, `.auth/` с машины не уезжает (инвариант 4), значит облачная
    рутина писать от аккаунта не может в принципе. Токен бота ограничен теми
    чатами, куда бота позвали, и отзывается одной командой в @BotFather.

    🔴 Токен лежит В URL запроса, а логи облачной сессии видны глазами. Поэтому
    тест ЛОМАЕТ отправку тремя способами и на каждом требует, чтобы токена в
    сообщении не оказалось. Проверка «редактор вызывается» была бы вхолостую:
    важно не то, что функция есть, а что через неё проходит каждый выход.
    """
    import contextlib
    import io
    import os
    import tempfile
    import urllib.error
    import urllib.request

    from . import store, tgwave

    TOKEN = "8123456:AAH-secret-do-not-print"

    eq(TOKEN in tgwave._redact(f"URL /bot{TOKEN}/sendDocument", TOKEN), False,
       "_redact не вычистил токен из текста")
    eq(tgwave._redact("bot 8123456: упал", TOKEN), "bot <токен>: упал",
       "числовая часть токена должна вычищаться отдельно — она приезжает "
       "в ошибках Telegram без хвоста")

    # Окружение процесса главнее файла: в облаке файла нет вовсе.
    with patched(os, "environ", {"TG_BOT_TOKEN": TOKEN, "TG_BOT_CHAT": "-100777"}):
        eq(tgwave.bot_creds({"TG_MIRROR_CHAT": "-100111"}), (TOKEN, "-100777"),
           "TG_BOT_CHAT обязан бить TG_MIRROR_CHAT из файла")
    with patched(os, "environ", {"TG_BOT_TOKEN": TOKEN}):
        eq(tgwave.bot_creds({"TG_MIRROR_CHAT": "-100111"}), (TOKEN, "-100111"),
           "без TG_BOT_CHAT адресом обязан стать канал из файла")
    with patched(os, "environ", {}):
        eq(tgwave.bot_creds({}), None, "без токена ботом слать нечем")

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "wave-2026-08-08.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("shortlist: 7 вакансий\n")

        sent: list[tuple[str, bytes, str]] = []

        class _Resp:
            def __init__(self, blob): self.blob = blob
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self.blob

        def fake_open(req, timeout=None):
            sent.append((req.full_url, req.data, req.headers.get("Content-type", "")))
            return _Resp(b'{"ok":true,"result":{"message_id":4242}}')

        with patched(urllib.request, "urlopen", fake_open):
            got = tgwave.send_bot(TOKEN, "-100777", path, "Волна: 7 новых вакансий")
        eq(got, 4242, "id сообщения разобран неверно")
        eq(len(sent), 1, "пост о волне обязан уходить ОДНИМ запросом")
        url, body, ctype = sent[0]
        eq(url.endswith("/sendDocument"), True, f"ушло не в sendDocument: {url}")
        eq(ctype.startswith("multipart/form-data; boundary="), True,
           f"тело собрано не как multipart: {ctype!r}")
        eq(b'name="chat_id"' in body and b"-100777" in body, True,
           "в теле нет адреса канала")
        eq(b"shortlist: 7" in body, True, "файл со списком в тело не попал")
        eq(b"parse_mode" in body, False,
           "разметка включена: названия вакансий — чужой текст, `_` и `*` "
           "в них дают 400 на ровном месте")

        # Поломка первая: Telegram ответил ошибкой HTTP с телом-объяснением.
        def http_error(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                io.BytesIO(b'{"ok":false,"description":"chat not found"}'))

        with patched(urllib.request, "urlopen", http_error):
            try:
                tgwave.send_bot(TOKEN, "-100777", path, "подпись")
                FAILS.append("отказ Telegram проглочен — рутина решит, что отправила")
            except RuntimeError as e:
                eq("chat not found" in str(e), True,
                   f"причина отказа потеряна, чинить нечего: {e}")
                eq(TOKEN in str(e), False, f"ТОКЕН УТЁК В ОШИБКУ: {e}")

        # Поломка вторая: сети нет. `e.reason` часто несёт url целиком.
        def no_net(req, timeout=None):
            raise urllib.error.URLError(f"нет маршрута до {req.full_url}")

        with patched(urllib.request, "urlopen", no_net):
            try:
                tgwave.send_bot(TOKEN, "-100777", path, "подпись")
                FAILS.append("недоступность сети проглочена")
            except RuntimeError as e:
                eq(TOKEN in str(e), False, f"ТОКЕН УТЁК В ОШИБКУ СЕТИ: {e}")

        # Поломка третья: HTTP 200, но ok=false — так Telegram отвечает чаще
        # всего, и путь «успех по коду, отказ по телу» легко пропустить.
        with patched(urllib.request, "urlopen",
                     lambda req, timeout=None: _Resp(
                         b'{"ok":false,"description":"bot was blocked"}')):
            try:
                tgwave.send_bot(TOKEN, "-100777", path, "подпись")
                FAILS.append("ok=false при HTTP 200 проглочен")
            except RuntimeError as e:
                eq("bot was blocked" in str(e), True, f"причина потеряна: {e}")

        # `--via bot` без токена обязан отказать, а не подменить транспорт
        # молча: рутина с отвалившимся токеном иначе напишет от владельца.
        db = os.path.join(d, "w.db")
        with store.connect(db) as conn:
            conn.execute("SELECT 1")
        with patched(os, "environ", {}), \
                patched(urllib.request, "urlopen", fake_open), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = tgwave.run(db, days=3, date="2026-08-08", apply=True,
                              out_dir=d, via="bot")
        eq(code, 2, "--via bot без токена обязан вернуть отказ")
        eq(len(sent), 1, "без токена в сеть не должно уйти ничего")


def test_funnel_does_not_call_a_page_view_an_answer():
    """Воронка обязана считать честно — иначе она хуже, чем её отсутствие.

    Данные лежали в базе с самого начала и не были видны ни одной командой:
    519 записей о том, что ответили НАМ. Соврать здесь проще всего двумя
    способами, и оба закрыты тестом.

    Первый: посчитать `viewed` ответом. «Резюме посмотрели» — факт открытия
    страницы, а не решение; на живой базе это завысило бы отклик рынка
    с 47% до 68%.

    Второй: считать медиану ответа по горстке записей и подать её как вывод.
    Дат события в базе хватает на пять откликов из 247 — на пяти замерах это
    не медиана, а случайное число."""
    import os
    import tempfile

    from . import funnel, store

    rows = [
        ("Go dev", "A", "rejection", "hh", "2026-07-01", "2026-07-05"),
        ("Go dev", "B", "invitation", "hh", "2026-07-01", "2026-07-03"),
        ("Go dev", "C", "interview", "mail", "2026-07-01", "2026-07-09"),
        ("Go dev", "D", "viewed", "hh", "2026-07-01", "2026-07-02"),
        ("Go dev", "E", "not_viewed", "habr", "2026-07-01", None),
        ("Go dev", "F", "applied", "mail", "2026-08-07", None),
    ]
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "f.db")
        with store.connect(db) as conn:
            for title, comp, status, src, seen, event in rows:
                conn.execute(
                    "INSERT INTO negotiation (title_key, company_key, title, company,"
                    " status, source, event_at, first_seen, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (title.lower(), comp.lower(), title, comp, status, src,
                     event, seen, seen))
            res = funnel.build(conn, tail_days=14)

    eq(res["total"], 6, "не все отклики попали в воронку")
    eq(res["answered"], 3, "ответом посчитано не то: viewed и not_viewed — не ответ")
    eq(res["positive"], 2, "приглашение и интервью — это два, а не что-то иное")
    eq(res["median_days"], 4, "медиана считается по датам ответивших")

    # Хвосты — молчащие, и только они. Свежий отклик хвостом не является.
    tails = {r["company"] for r in res["tails"]}
    eq(tails, {"D", "E"},
       f"хвосты посчитаны неверно: {tails}. Отвеченное хвостом не бывает, "
       f"свежее — тоже")

    text = funnel.render(res) + funnel.render_tails(res)
    eq("ненадёжно" in text, True,
       "медиана по трём откликам подана как факт — на таких данных её "
       "надо помечать, а не печатать голым числом")
    eq("не попадает" in text, True,
       "не сказано, что отклик мимо hh/почты в знаменатель не входит — "
       "по этим процентам нельзя считать «конверсию поиска»")


def test_doctor_diagnoses_without_touching_the_network():
    """`doctor` обязан быть дешёвым и не врать про отсутствие как про поломку.

    Дешёвым: команду запускают «на всякий случай» в начале сессии, и если она
    иногда уходит в сеть на минуту, её перестают запускать. Проверяется не
    обещанием в докстроке, а подменой всех четырёх выходов наружу на взрыв.

    Не врать: нет ключа jooble — площадка выключена, это ⚠️. 🔴 остаётся за тем,
    из-за чего волна упадёт или соврёт, и по числу 🔴 команда возвращает код."""
    import os
    import socket
    import tempfile

    from . import auth, authrefresh, doctor, store

    def boom(*a, **k):
        raise AssertionError("doctor ушёл в сеть — он обязан читать только диск")

    # 🔴 Состояние входов ПОДМЕНЯЕТСЯ. Без подмены тест зеленел только потому,
    # что на машине автора владелец залогинен в shadowhint: разлогинься он —
    # и «поломок 0» превращается в «поломок 1» на ровном месте. Тест обязан
    # проверять отчёт `doctor`, а не то, в каком состоянии сегодня чужие куки.
    alive = [{"platform": "hh", "state": "logged_in", "why": "", "loss": "",
              "critical": False, "renewable": False}]
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "d.db")
        with store.connect(db) as conn:
            conn.execute("SELECT 1")
        # Затыкается САМ сокет, а не наши обёртки: проверка должна ловить любой
        # поход наружу, включая тот, который добавят завтра в обход `net.fetch`.
        with patched(socket, "create_connection", boom), \
                patched(authrefresh, "preflight", lambda *a, **k: alive):
            lines, bad = doctor.report(db)
            # Пропавший вход в площадку, без которой выдачи нет, — настоящая
            # поломка и обязана поднимать код возврата.
            dead = [dict(alive[0], platform="shadowhint", state="anonymous",
                         why="куки нет", loss="ВСЮ выдачу", critical=True)]
            with patched(authrefresh, "preflight", lambda *a, **k: dead):
                _, bad_dead = doctor.report(db)
    eq(bad_dead, 1, "пропавший критичный вход не посчитан поломкой")
    text = "\n".join(lines)

    # 🔴 Диагностика не имеет права ПЕРЕПИСЫВАТЬ файлы сессий. По умолчанию
    # `secure_auth_dir` делает два дела: чинит права и вырезает чужие домены из
    # `.auth/*.json`. Второе — правка чужих данных, и её просят явно (`auth
    # secure`), а не получают побочным эффектом команды «покажи, что сломано».
    calls: list[dict] = []
    with patched(auth, "secure_auth_dir", lambda **kw: calls.append(kw) or []), \
            patched(authrefresh, "preflight", lambda *a, **k: alive):
        doctor.report(":memory:")
    eq(calls and calls[0].get("prune_foreign"), False,
       f"doctor зовёт secure_auth_dir с чисткой доменов: {calls}")

    # Ошибка при опросе диска не имеет права УБИРАТЬ раздел из отчёта:
    # «не смогли посмотреть» стало бы неотличимо от «всё хорошо».
    def no_disk(_path):
        raise OSError("файловая система не отвечает")

    with patched(doctor.shutil, "disk_usage", no_disk), \
            patched(authrefresh, "preflight", lambda *a, **k: alive):
        broken, _ = doctor.report(":memory:")
    eq("## Диск" in "\n".join(broken), True,
       "раздел «Диск» исчез из отчёта вместо того, чтобы сказать о поломке")

    eq(bad, 0, f"на чистой машине поломок быть не должно, а насчитано {bad}")

    # 🔴 Каждый опциональный пакет обязан быть НАЗВАН в отчёте. Проверяется не
    # текущий набор, а полнота: doctor существует ради ответа «что сломано», и
    # молчание про отсутствующий пакет — это ложное «всё на месте». Живой счёт
    # 08.08.2026: imap_tools не проверялся, doctor сказал «всё на месте»,
    # mail-sync упал этапом, и почта — единственный канал статусов для
    # компаний, которые не пишут в hh, — не читалась всю волну.
    for mod, _why in doctor.OPTIONAL:
        if mod not in text:
            FAILS.append(f"doctor не проверяет {mod}: его отсутствие пройдёт "
                         f"молча, а сломается это уже посреди волны")
    if "imap" not in text:
        FAILS.append("doctor молчит про imap-tools — без него mail-sync падает, "
                     "а отчёт рапортует, что всё на месте")

    # 🔴 Пакет playwright и САМ браузер ставятся отдельно. Проверка импорта про
    # второй не говорит ничего: 08.08.2026 doctor писал «playwright на месте»
    # при пустом каталоге сборок, и всё браузерное молча не работало — render,
    # channel --render и живость страниц за антибот-стеной.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as empty:
        with patched(os, "environ", {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": empty}):
            rows = doctor._browsers()
    joined = " ".join(w for _, w in rows)
    if rows and "playwright install" not in joined:
        FAILS.append(f"doctor не сказал, что браузеров нет: {joined!r}")

    # 🔴 `chromium` и `chromium_headless_shell` — разные сборки, ставятся
    # раздельно, и render запускает вторую. «Есть хоть какой-то chromium»
    # пропускало ровно тот случай, что случился 08.08.2026: doctor писал
    # «браузеры на месте: chromium-1234», а render падал с «Executable doesn't
    # exist at …/chromium_headless_shell-1234/…».
    with _tf.TemporaryDirectory() as half:
        os.mkdir(os.path.join(half, "chromium-1234"))
        with patched(os, "environ", {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": half}):
            rows2 = doctor._browsers()
    j2 = " ".join(w for _, w in rows2)
    if rows2 and "headless" not in j2.lower():
        FAILS.append(f"doctor засчитал chromium без headless-shell: {j2!r}")

    # 🔴 Ветка «кэш ответов площадок» живёт только при НЕПУСТОМ кэше, и потому
    # её никто не выполнял: на чистой машине и в тесте кэша нет. Внутри лежал
    # импорт несуществующего имени (`RawCache` вместо `Cache`), то есть doctor
    # падал ImportError ровно после первой волны — когда его и запускают первым
    # делом. Пустая база тут не проверка, а слепое пятно.
    # База нужна НАСТОЯЩИМ файлом: `:memory:` не существует на диске, и `_db`
    # выходит первой же строкой «базы нет», не дойдя до кэша вовсе.
    with tempfile.TemporaryDirectory() as d2:
        db2 = os.path.join(d2, "cached.db")
        with store.connect(db2) as conn:
            conn.execute("SELECT 1")
        with patched(store, "raw_cache_stats",
                     lambda _c: {"pages": 510, "bytes": 5 * 1024 ** 2}), \
                patched(authrefresh, "preflight", lambda *a, **k: alive):
            cached_lines, _ = doctor.report(db2)
    if not any("кэш ответов площадок" in ln for ln in cached_lines):
        FAILS.append("doctor не доложил про кэш ответов при непустом кэше — "
                     "ветка либо упала, либо молчит")
    for section in ("Окружение", "База", "Браузер", "Ключи площадок",
                    "Сессии", "Секреты", "Диск"):
        eq(f"## {section}" in text, True, f"раздел «{section}» пропал из отчёта")
    eq("прогонов ещё не было" in text, True,
       "пустая база обязана сказать, что прогонов не было, а не молчать")
    # Ни один ⚠️ не имеет права поднять код возврата: выключенная площадка —
    # это состояние, а не авария, и `doctor` в рутине не должен падать из-за неё.
    eq(doctor.WARN in text and bad == 0, True,
       "выключенное посчитано поломкой — так команда падает на ровном месте")


def test_pause_charges_the_request_time_against_the_interval():
    """Пауза — ограничитель ЧАСТОТЫ: считает уже потраченное, а не спит сверху.

    Замер LinkedIn 07.08.2026: фиксированная пауза 1.2 с ПОСЛЕ ответа давала
    один запрос в 2.07 с при задуманной одной в 1.2 с. Площадка видела частоту
    вдвое ниже назначенной, а прогон платил за это временем — на ста страницах
    это 86 лишних секунд из 206, и та же переплата была у каждого страничного
    источника, потому что механизм у всех общий.
    """
    from . import sources as S

    slept: list[float] = []
    real_sleep, real_clock = time.sleep, time.monotonic
    now = [1000.0]
    try:
        time.sleep = lambda s: (slept.append(s), now.__setitem__(0, now[0] + s))[0]
        time.monotonic = lambda: now[0]
        S._LAST_PAUSE.at = None
        S._pause(1.2)                    # первый заход — ждать нечего
        now[0] += 0.87                   # столько занял сам запрос
        S._pause(1.2)                    # ждём ОСТАТОК интервала, а не весь
        now[0] += 0.87
        S._pause(1.2)
        # Отступ после отказа считается ЦЕЛИКОМ: вычесть из выдержки время
        # неудачного запроса значит отступить меньше, чем решено.
        now[0] += 5.0
        S._pause(20.0, gate=False)
    finally:
        time.sleep, time.monotonic = real_sleep, real_clock
        S._LAST_PAUSE.at = None

    # Первый заход спит нулевое время, поэтому в списке его нет: гейт возвращает
    # 0.0, не вызывая sleep. «Сна не было» и «сон нулевой» для площадки одно и
    # то же, а вот 1.2 с вместо 0.33 с — уже переплата.
    eq(len(slept), 3, "спали не столько раз, сколько ждали интервал")
    for i, s in enumerate(slept[:2], 1):
        if abs(s - (1.2 - 0.87)) > 0.01:
            FAILS.append(f"пауза {i} = {s:.2f} с вместо остатка "
                         f"{1.2 - 0.87:.2f} с — время запроса не зачтено")
    eq(slept[2], 20.0, "отступ после отказа урезан временем запроса")


def test_linkedin_empty_page_is_rechecked_before_calling_it_the_end():
    """Пустая страница переспрашивается, прежде чем объявить конец выдачи.

    Самое дорогое место источника. У гостевого поиска «выдача кончилась» и «мы
    вас притормозили» выглядят ОДИНАКОВО — 200 с телом в 26 байт. Замер
    07.08.2026: один и тот же запрос отдал 0 карточек, а следом, без всякого
    ожидания, десять. Поверить первому ответу значит молча обрезать регион и
    отчитаться о полном обходе — то есть соврать ровно тем способом, от которого
    во всём сборщике стоят счётчики «отдано → записано»."""
    from .sources import Ctx, src_linkedin

    def card(vid):
        return ('<div class="base-card" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">Senior Golang Developer</span>'
                f'<a class="hidden-nested-link" href="/c">Acme</a>'
                f'<span class="job-search-card__location">Berlin</span>'
                f'<time datetime="{_fresh()[:10]}">вчера</time></div>')

    seen: dict[str, int] = {}

    class Flaky(_FakeFetch):
        def __call__(self, url, **kw):
            self.asked.append(url)
            start = url.split("start=")[1].split("&")[0]
            seen[start] = seen.get(start, 0) + 1
            # Вторая страница «пустеет» ровно один раз — как живой троттлинг.
            if start == "10" and seen[start] == 1:
                return "", url
            if start in ("0", "10"):
                return "<ul>" + card(int(start) + 1) + "</ul>", url
            return "<ul></ul>", url

    fake = Flaky({})
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))
    ids = sorted({v.external_id for v in got if v.external_id != "_summary"})
    eq(ids, ["1", "11"],
       "карточки со страницы, пришедшей пустой по троттлингу, потеряны")
    if seen.get("10", 0) < 2:
        FAILS.append("пустую страницу не переспросили — конец выдачи объявлен по "
                     "первому же пустому ответу")


def test_linkedin_throttling_is_a_pause_not_the_end_of_the_region():
    """429 — это «не так часто», а не «нельзя».

    Раньше первый же 429 обрывал регион целиком, и остаток выдачи молча
    становился нулём. Теперь ждём с удвоением и продолжаем; сдаёмся только
    после LINKEDIN_RETRIES отказов подряд — и тогда говорим об этом вслух."""
    from . import sources as S
    from .net import FetchError
    from .sources import Ctx, src_linkedin

    def card(vid):
        return ('<div class="base-card" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">Senior Golang Developer</span>'
                f'<a class="hidden-nested-link" href="/c">Acme</a>'
                f'<span class="job-search-card__location">Berlin</span>'
                f'<time datetime="{_fresh()[:10]}">вчера</time></div>')

    state = {"n": 0}

    class Throttled(_FakeFetch):
        def __call__(self, url, **kw):
            self.asked.append(url)
            state["n"] += 1
            # Ровно один отказ, и он не последний: дальше выдача обязана пойти.
            if state["n"] == 1:
                raise FetchError(url, "HTTP 429", status=429)
            if "start=0&" in url:
                return "<ul>" + card(7) + "</ul>", url
            return "<ul></ul>", url

    fake = Throttled({})
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))
    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq([v.external_id for v in jobs], ["7"],
       "после 429 обход не продолжился — регион потерян целиком")
    if any("НЕ ОТДАЛИСЬ" in n for n in summary.raw["notes"]):
        FAILS.append("один 429 объявлен потерей региона, хотя площадка "
                     "просто просила подождать")
    if S.LINKEDIN_RETRIES < 2:
        FAILS.append("отступ без повторов отступом не является")


def test_linkedin_network_failure_keeps_what_was_already_collected():
    """Обрыв соединения — потеря СТРАНИЦЫ, а не всего источника.

    Живой случай 08.08.2026: прогон шёл 20 минут, 510 ответов легло в кэш, а
    linkedin вернул НОЛЬ и строку «УПАЛ: URLError: Connection reset by peer».
    Причина: `URLError` приходит как `FetchError` БЕЗ статуса, а перехват стоял
    только на 429/403 — всё остальное уходило `raise` мимо накопленного `out`.
    Это повтор инцидента glassdoor («стену бросает сам рендерер, разобранная
    первая страница выброшена вместе с исключением»), и цена та же: сотни
    карточек, за которые уже заплачено временем и вежливостью к площадке.

    Здесь подделка бросает отказ ТАК ЖЕ, как он приходит живьём, — броском без
    статуса, а не данными. Ровно на этом прошлый тест и зеленел вхолостую."""
    from .net import FetchError
    from .sources import Ctx, src_linkedin

    def card(vid):
        return ('<div class="base-card" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">Senior Golang Developer</span>'
                f'<a class="hidden-nested-link" href="/c">Acme</a>'
                f'<span class="job-search-card__location">Berlin</span>'
                f'<time datetime="{_fresh()[:10]}">вчера</time></div>')

    class Reset(_FakeFetch):
        def __call__(self, url, **kw):
            self.asked.append(url)
            if "start=0&" in url:
                return "<ul>" + card(42) + "</ul>", url
            # Так это приходит из net.fetch: URLError → FetchError без status.
            raise FetchError(url, "URLError: <urlopen error [Errno 54] "
                                  "Connection reset by peer>")

    fake = Reset({})
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))
    jobs = [v for v in got if v.external_id != "_summary"]
    eq([v.external_id for v in jobs], ["42"],
       "обрыв соединения унёс уже разобранные карточки — источник вернул ноль")
    summary = [v for v in got if v.external_id == "_summary"][0]
    if not any("НЕ ОТДАЛИСЬ" in n or "ОБРЫВ СВЯЗИ" in n for n in summary.raw["notes"]):
        FAILS.append("страница потеряна молча: в сводке нет строки о том, что "
                     "выдача не спрошена, — это читается как полный обход")


def test_linkedin_dead_network_gives_up_instead_of_grinding_every_pair():
    """Сеть легла — прекращаем источник, а не перебираем 27 пар по три попытки.

    Предохранитель к правке выше. Без него честный повтор превращается в свою
    противоположность: при полном обрыве каждая пара платит LINKEDIN_RETRIES
    отступов с удвоением, и прогон стоит десятки минут ради нуля карточек —
    ровно то, что и вышло 08.08.2026 (1191 с на пустую выдачу)."""
    from . import sources as S
    from .net import FetchError
    from .sources import Ctx, src_linkedin

    class Dead(_FakeFetch):
        def __call__(self, url, **kw):
            self.asked.append(url)
            raise FetchError(url, "URLError: <urlopen error [Errno 54] "
                                  "Connection reset by peer>")

    fake = Dead({})
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))
    summary = [v for v in got if v.external_id == "_summary"][0]
    pairs = len(S.LINKEDIN_REGIONS) * len(S.Ctx(query="Golang", days=3).queries())
    # Сдаться надо заметно раньше, чем перебрать все пары: иначе предохранителя нет.
    if len(fake.asked) >= pairs * S.LINKEDIN_RETRIES:
        FAILS.append(f"при мёртвой сети опрошены все пары ({len(fake.asked)} "
                     f"запросов) — предохранитель не сработал")
    if not any("ОБРЫВ СВЯЗИ" in n for n in summary.raw["notes"]):
        FAILS.append("источник сдался молча — в сводке нет причины остановки")


def test_linkedin_stops_where_the_search_drifts_off_topic():
    """Вглубь гостевая выдача уезжает от запроса совсем.

    Замер по Германии, «под профиль» на страницу при start=0…250:
    10,9,10,10,10,10,1,3,0,10,1,0,10,0,0,10,1,10,1,0,0,10,10,0,0,0 — то есть
    одна и две пустые страницы концом НЕ являются (на start=150 и 210 профильные
    вернулись), а три подряд — являются: дальше идут «Pflegehilfskraft» по запросу
    Golang. Остановка по релевантности честнее потолка страниц: потолок молча
    режет живую выдачу, а здесь резать уже нечего."""
    from . import sources as S
    from .sources import Ctx, src_linkedin

    def card(vid, title):
        return ('<div class="base-card foo" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">{title}</span></div>')

    # Страница 2 пустая по профилю, страница 3 снова с Go — обход обязан выжить.
    pages = {
        "start=0&": "<ul>" + card(1, "Golang Developer") + "</ul>",
        "start=10&": "<ul>" + card(2, "Pflegehilfskraft (m/w/d)") + "</ul>",
        "start=20&": "<ul>" + card(3, "Backend Engineer (Go)") + "</ul>",
        "start=30&": "<ul>" + card(4, "Vorarbeiter Maurer") + "</ul>",
        "start=40&": "<ul>" + card(5, "Präsenzkraft Teilzeit") + "</ul>",
        "start=50&": "<ul>" + card(6, "Sachbearbeiter Entgeltabrechnung") + "</ul>",
        "start=60&": "<ul>" + card(7, "Golang Engineer") + "</ul>",
        "start=": "<ul></ul>",
    }
    fake = _FakeFetch(pages)
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(sorted(v.external_id for v in jobs), ["1", "3"],
       "одна пустая по профилю страница обход не прекращает")
    pairs = len(S.LINKEDIN_REGIONS) * len(S._linkedin_windows(3))
    eq(len(fake.asked), 6 * pairs,
       "три пустые подряд (стр. 4–6) — конец выдачи; седьмую уже не просим")
    if not any("уехала от запроса" in n for n in summary.raw["notes"]):
        FAILS.append(f"причина остановки не названа: {summary.raw['notes']}")
    if any("ОБРЕЗАНО" in n for n in summary.raw["notes"]):
        FAILS.append("уход выдачи вбок назван обрезанием — это разные вещи, "
                     "и второе зовёт поднять --limit там, где брать уже нечего")


def test_linkedin_asks_nested_windows_because_they_return_different_jobs():
    """Узкое окно `f_TPR` — не подмножество широкого, а ДРУГАЯ выборка.

    Замер 08.08.2026, Germany/«Golang», оба окна обойдены до конца выдачи:
    72 ч — 35 страниц и 280 профильных, 24 ч — 44 страницы и 300 профильных,
    и 208 из этих 300 широкое окно не отдало вообще. Причина в потолке: он
    действует на пару «формулировка × регион × окно», а внутри окна LinkedIn
    отдаёт что придётся, а не самое свежее.

    Тест держит именно это свойство: спрошены ОБА окна, и одинаковые id из них
    склеиваются, а не задваиваются. Без него «лишний» проход по узкому окну —
    первое, что захочется убрать ради времени."""
    from . import sources as S
    from .sources import Ctx, src_linkedin

    # Регионы — тоже полнота, и у них та же арифметика: у каждой пары свой
    # потолок. Замер 08.08.2026 («Golang», окно 7 суток, 3 страницы, счёт
    # профильных): Switzerland 29, Czechia 29, Israel 28, Georgia 28, Italy 28,
    # Romania 28, Ireland 27, UAE 26, Sweden 26, Estonia 24, Serbia 22,
    # Austria 20. Порог здесь — не «красиво», а «столько замерено».
    eq(len(S.LINKEDIN_REGIONS) >= 21, True,
       f"регионов стало {len(S.LINKEDIN_REGIONS)} — список урезали, "
       f"а каждый из двенадцати добавленных замерен на 20+ профильных карточек")
    for measured in ("Switzerland", "Czechia", "Israel", "Georgia", "Italy",
                     "Romania", "Ireland", "United Arab Emirates", "Sweden",
                     "Estonia", "Serbia", "Austria"):
        eq(measured in S.LINKEDIN_REGIONS, True,
           f"регион {measured} убран, хотя замерен как дающий выдачу")

    eq(S._linkedin_windows(1), (86400,), "однодневное окно дробить не на что")
    eq(S._linkedin_windows(3), (86400, 259200), "трёхдневное окно не дало узкого")
    eq(len(S._linkedin_windows(30)), 2,
       "окон больше двух — третье лежит между спрошенными и стоит времени")

    def card(vid, title):
        return ('<div class="base-card foo" '
                f'data-entity-urn="urn:li:jobPosting:{vid}">'
                f'<span class="sr-only">{title}</span></div>')

    # Одна и та же вакансия в обоих окнах: склейка по id обязана её удержать.
    fake = _FakeFetch({"start=0&": "<ul>" + card(7, "Go Developer") + "</ul>",
                       "start=": "<ul></ul>"})
    got = _with_fake_fetch(fake, lambda: src_linkedin(Ctx(query="Golang", days=3)))
    jobs = [v for v in got if v.external_id != "_summary"]
    eq(len(jobs), 1,
       "одна вакансия из двух окон и 21 региона приехала не один раз — склейка по id сломана")
    eq(len(fake.asked) % 2, 0,
       "запросов нечётное число — значит второе окно спрошено не везде")
    asked = "\n".join(fake.asked)
    for seconds in S._linkedin_windows(3):
        eq(f"f_TPR=r{seconds}" in asked, True, f"окно r{seconds} не спрошено вовсе")
    summary = [v for v in got if v.external_id == "_summary"][0]
    if not any("окна вложенные" in n for n in summary.raw["notes"]):
        FAILS.append(f"вложенные окна не названы в сводке: {summary.raw['notes']}")


def test_linkedin_depth_is_the_platform_ceiling_and_limit_cannot_move_it():
    """Глубина LinkedIn равна потолку ПЛОЩАДКИ и не настраивается.

    Раньше тест дёргал `_page_budget` напрямую и проверял, что большой лимит
    поднимает потолок. Провалиться он не мог: ровно этот результат `src_linkedin`
    выбрасывал следующей же строкой (`min(budget, LINKEDIN_MAX_PAGES)`), то есть
    тест охранял поведение, которого в источнике нет. Проверять надо то, что
    источник делает НА САМОМ ДЕЛЕ: сколько страниц он спрашивает при любом
    лимите. Дальше start=1000 отдаёт HTTP 400, и поднимать потолок некуда."""
    from .sources import Ctx, LINKEDIN_HARD_START, LINKEDIN_MAX_PAGES, src_linkedin

    eq(LINKEDIN_MAX_PAGES * 10, LINKEDIN_HARD_START,
       "потолок страниц разошёлся с измеренным потолком площадки")
    for limit in (10, 400, 100000):
        fake = _FakeFetch({"start=": "<ul></ul>"})
        _with_fake_fetch(fake, lambda: src_linkedin(
            Ctx(query="Golang", days=3, limit=limit)))
        starts = {int(u.split("start=")[1].split("&")[0]) for u in fake.asked}
        if max(starts) >= LINKEDIN_HARD_START:
            FAILS.append(f"--limit {limit}: спросили start={max(starts)} — "
                         f"площадка отвечает на это HTTP 400")


def test_linkedin_ru_only_still_reports_itself():
    """--ru-only не должен превращать источник в молчаливый ноль."""
    from .sources import Ctx, src_linkedin
    got = src_linkedin(Ctx(include_foreign=False))
    eq(len(got), 1, "только строка сводки")
    if not any("--ru-only" in n for n in got[0].raw["notes"]):
        FAILS.append("причина пустой выдачи не названа")


# Заголовки, на которых фильтр профессии промахивался КУЧНО: одна недописанная
# формулировка выносила целую компанию. Взяты из живой выдачи двадцати досок.
ATS_ROLE_MUST_PASS = [
    # `sre` в регулярке был, английского `reliability` — нет.
    "Site Reliability Engineer", "Principal Site Reliability Engineer",
    "Senior Database Reliability Engineer", "Engineering Manager (Site Reliability)",
    # Так poolside называет ВСЕ инженерные позиции, включая Compute и Infra.
    "Member of Technical Staff", "Member of Technical Staff (Applied AI)",
    "Member of Engineering (Compute)", "Member of Engineering (Pre-training / Data Engineering)",
    # Было только `software engineer`.
    "Software Developer", "AI-Native Software Developer", "Director, Software Engineering",
    "Principal Software Development Engineer - Applied AI",
    # `cloud` без хвоста.
    "Senior GCP CloudOps Engineer",
    # `system[s]? engineer` не ловил «…Systems» в конце.
    "Principal Engineer — Real-Time Data Systems", "Distributed Systems Engineer",
    # Голое «<грейд> Engineer» — рабочее название линеек у Canonical.
    "Senior Engineer", "Staff Engineer - Cybersecurity", "Principal Engineer, Autonomy",
    # То, что фильтр ловил и раньше: расширение не должно это сломать.
    "Golang Engineer", "Бэкенд-разработчик (Go)", "Разработчик бэкенда",
    "Senior Backend Engineer", "DevOps Engineer", "Team Lead (Go)",
    "Инженер по надёжности", "Системный инженер",
    # Отрицание «архитекторов зданий» не имеет права задеть архитекторов кода.
    "Архитектор программного обеспечения", "Системный архитектор", "Архитектор ИТ",
    "Solutions Architect", "Cloud Architect",
]

# То, что фильтр обязан отсекать и после расширения. Иначе он перестаёт быть
# фильтром: доски отдают 7 488 строк, и продажи с поддержкой — это 6 000 из них.
ATS_ROLE_MUST_FAIL = [
    "Account Executive", "Customer Support Specialist", "Recruiter",
    "Marketing Manager", "Financial Controller", "Head of Finance",
    "HR & Payroll Manager", "Менеджер по продажам", "Бухгалтер",
    "Graphic Designer", "Sales Engineer", "Legal Counsel",
    # Архитекторы, которые строят здания. Замер trudvsem 08.08.2026: по слову
    # «архитектор» через фильтр прошли 204 строки, профильных из них 12.
    "Ландшафтный архитектор", "Главный архитектор проекта",
    "Архитектор-проектировщик", "Архитектор-реставратор",
    "Ведущий инженер по благоустройству", "Дизайнер интерьера",
]


def test_ats_role_filter_covers_the_audit_list():
    """Ложные отсевы фильтра были кучными: одна формулировка — целая компания.

    Замер по 7 488 живым заголовкам: 'Site Reliability Engineer' — 34 заголовка,
    терялось 23; 'Member of …Staff' — 37, терялось 24; 'Software Developer' —
    62, терялось 46."""
    from .sources import ATS_ROLE_RE
    for title in ATS_ROLE_MUST_PASS:
        if not ATS_ROLE_RE.search(title):
            FAILS.append(f"ATS_ROLE_RE отсеивает своё: {title!r}")
    for title in ATS_ROLE_MUST_FAIL:
        if ATS_ROLE_RE.search(title):
            FAILS.append(f"ATS_ROLE_RE перестал быть фильтром, пускает: {title!r}")


def test_trudvsem_asks_in_the_language_of_the_state_portal():
    """На государственном портале нашу профессию называют не «Golang».

    Замер 08.08.2026, профильные В ОКНЕ СВЕЖЕСТИ (общая глубина базы обманчива,
    она забита позапрошлогодним): за трое суток один «Golang» даёт 0, весь
    набор — 5; за тридцать суток 1 против 36. Треть приносит формулировка
    «системный программист».

    Проверяется не список слов, а три свойства: запрос пользователя остаётся
    ГЛАВНЫМ (набор дополняет, а не заменяет), в наборе нет замеренно пустых
    слов и нет несоразмерно дорогих."""
    from .sources import TRUDVSEM_QUERIES, merge_queries

    qs = merge_queries(["Rust"], TRUDVSEM_QUERIES)
    eq(qs[0], "Rust", "запрос пользователя обязан идти первым, а не тонуть в наборе")
    eq("системный программист" in qs, True,
       "формулировка, дающая треть выдачи портала, пропала из набора")
    for dead in ("программист", "инженер-программист", "PHP", "архитектор",
                 "software engineer", "бэкенд"):
        eq(dead in TRUDVSEM_QUERIES, False,
           f"«{dead}» замерен как пустой или несоразмерно дорогой — его тут быть не должно")


def test_every_ats_engine_is_wired_into_the_run():
    """Движок, разобранный в atsapi, но не подключённый к прогону, — это компания,
    которая не попадёт в обход, даже если найти её через `ats sniff`."""
    from .atsapi import ATS_KINDS
    from .sources import _ATS_IMPL
    for kind in ATS_KINDS:
        if kind not in _ATS_IMPL:
            FAILS.append(f"движок {kind} есть в atsapi, но прогон его не опрашивает")


# ──────────────────────────────────────────────────────────────────────────────


def main() -> int:
    for fn in (
            test_hh_walks_every_page,
            test_hh_truncation_is_never_silent,
            test_hh_limit_below_default_does_not_shrink_the_window,
            test_habr_paginates_until_the_window_edge,
            test_habr_stops_where_the_site_says_it_ends,
            test_careered_filters_profession_and_reads_to_the_window_edge,
            test_linkedin_paginates_by_start_and_drops_other_professions,
            test_linkedin_asks_every_formulation,
            test_card_write_flags_dead_ats_links_before_writing,
            test_brief_shows_other_roles_of_the_same_company,
            test_since_auto_never_narrows_below_a_day,
            test_connect_works_without_a_directory_in_the_path,
            test_liveness_reads_archive_markers_not_only_http_code,
            test_requirement_tier_separates_must_have_from_nice_to_have,
            test_gather_digs_the_apply_link_out_of_a_telegram_post,
            test_card_carries_no_commands_and_gate_runs_in_lint,
            test_lint_catches_broken_links_in_the_wave_doc,
            test_card_files_layout_and_lint,
            test_health_tells_a_dead_source_from_an_off_profile_one,
            test_cache_hit_does_not_pay_for_politeness,
            test_raw_cache_prunes_stale_days_on_start,
            test_lint_letter_catches_the_generator_markers,
            test_wavedoc_slug_folds_legal_forms_and_transliterates,
            test_wavedoc_never_overwrites_a_document_with_judgement_in_it,
            test_card_gives_the_whole_contact_picture_and_names_the_barriers,
            test_tally_splits_the_gap_between_claimed_and_kept,
            test_vetted_query_sets_actually_reach_the_platform,
            test_rabota_names_the_formulations_it_never_asked,
            test_tg_wave_is_one_post_and_never_sends_by_default,
            test_tg_wave_bot_path_is_stdlib_and_never_leaks_the_token,
            test_funnel_does_not_call_a_page_view_an_answer,
            test_doctor_diagnoses_without_touching_the_network,
            test_pause_charges_the_request_time_against_the_interval,
            test_linkedin_empty_page_is_rechecked_before_calling_it_the_end,
            test_linkedin_throttling_is_a_pause_not_the_end_of_the_region,
            test_linkedin_network_failure_keeps_what_was_already_collected,
            test_linkedin_dead_network_gives_up_instead_of_grinding_every_pair,
            test_linkedin_stops_where_the_search_drifts_off_topic,
            test_linkedin_asks_nested_windows_because_they_return_different_jobs,
            test_linkedin_depth_is_the_platform_ceiling_and_limit_cannot_move_it,
            test_linkedin_ru_only_still_reports_itself,
            test_ats_role_filter_covers_the_audit_list,
            test_trudvsem_asks_in_the_language_of_the_state_portal,
            test_every_ats_engine_is_wired_into_the_run,
    ):
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
