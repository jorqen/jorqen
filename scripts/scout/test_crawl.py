"""Обход ссылок поста: глубина, дедуп, зацикливание, бюджеты.

Проверяется тут ровно то, что ломается МОЛЧА и выглядит успехом. Обход,
остановившийся на четвёртой ссылке, печатает карту и выглядит полным. Обход,
попавший в кольцо редиректов, «работает» — просто долго. Обход, срезавший
половину ссылок по бюджету, отдаёт красивый результат, в котором нет главного.
Поэтому тесты смотрят не только на итог, но и на СПИСОК ЗАПРОШЕННЫХ адресов и
на раздел «не пошли»: обход — это факт, а не намерение.

    .venv/bin/python -m scripts.scout.test_crawl
"""

from __future__ import annotations

import sys

from . import applyopt, crawl, store
from .net import BlockedError, FetchError

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def ok(cond, label):
    if not cond:
        FAILS.append(label)


class Web:
    """Подделка сети для обхода и СЧЁТЧИК спрошенных адресов.

    Значение фикстуры: строка — тело страницы; кортеж — (тело, адрес после
    редиректа); исключение — так площадка и ответила. Адреса, которого в
    фикстуре нет, не существует: это 404, а не «забыли положить».
    """

    def __init__(self, pages: dict):
        self.pages, self.asked = pages, []

    def __call__(self, url, *, timeout=20):
        self.asked.append(url)
        page = self.pages.get(url)
        if page is None:
            raise FetchError(url, "HTTP 404", 404)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, tuple):
            return page
        return page, url


def page(*links: str, title: str = "Вакансия", alive: bool = True,
         apply_text: str = "Откликнуться", extra: str = "") -> str:
    """Страница вакансии со ссылками. `alive=False` — снятая вакансия."""
    body = "".join(f'<a href="{u}">{apply_text}</a>' for u in links)
    mark = "Требования и обязанности" if alive else "Вакансия закрыта"
    return (f"<html><head><title>{title}</title></head>"
            f"<body><h1>{title}</h1>{mark}{body}{extra}</body></html>")


# ──────────────────────────────────────────────────────────────────────────────


def test_every_link_of_the_post_is_walked_not_the_first_four():
    """Обходятся ВСЕ ссылки поста.

    До этого модуля из поста брались первые четыре внешние ссылки, и «лучшая»
    выбиралась по домену — то есть по догадке о том, куда она ведёт. Пятая
    ссылка не проверялась вовсе, а в постах с подписями каналов и с несколькими
    вакансиями настоящий контакт как раз бывает не первым.
    """
    seeds = [f"https://firm{i}.example.com/vacancy/{i}" for i in range(6)]
    web = Web({u: page(title=f"Вакансия {i}") for i, u in enumerate(seeds)})
    res = crawl.crawl(seeds, gap=0, fetcher=web)

    eq(len(res.nodes), 6, "обошли не все ссылки поста")
    eq(sorted(web.asked), sorted(seeds), "спрошены не те адреса")
    ok(all(n.liveness == "ЖИВА" for n in res.nodes),
       f"живые страницы не опознаны: {[(n.url, n.liveness) for n in res.nodes]}")
    eq(res.dropped, [], "на ровном месте что-то объявлено пропущенным")


def test_depth_limit_stops_the_walk_and_never_hides_the_tail():
    """Глубина ограничена, и обрезанное НАЗВАНО.

    Молчаливая обрезка хуже отсутствия обхода: карта выглядит полной, а
    четвёртого звена в ней нет и спросить о нём некому.
    """
    a = "https://a.example.com/vacancy/1"
    b = "https://b.example.com/vacancy/2"
    c = "https://c.example.com/vacancy/3"
    d = "https://d.example.com/vacancy/4"
    web = Web({a: page(b), b: page(c), c: page(d), d: page()})

    res = crawl.crawl([a], gap=0, max_depth=2, fetcher=web)
    eq(web.asked, [a, b, c], "обход ушёл глубже предела или не дошёл до предела")
    eq([n.depth for n in res.nodes], [0, 1, 2], "глубина узлов посчитана неверно")
    ok(any(x["url"] == d and "глубже" in x["why"] for x in res.dropped),
       f"ссылка за пределом глубины пропала молча: {res.dropped}")

    # Предел двигается флагом, а не правкой кода.
    web2 = Web({a: page(b), b: page(c), c: page(d), d: page()})
    crawl.crawl([a], gap=0, max_depth=3, fetcher=web2)
    eq(web2.asked, [a, b, c, d], "--depth не поднимает предел")


def test_a_ring_of_links_does_not_spin():
    """Кольцо `a → b → a` останавливается на втором `a`.

    Ключ помечается ДО запроса — иначе две ссылки на один адрес уходят в сеть
    обе, — и повторно проверяется ПОСЛЕ редиректа.
    """
    a = "https://a.example.com/vacancy/1"
    b = "https://b.example.com/vacancy/2"
    web = Web({a: page(b), b: page(a)})
    res = crawl.crawl([a], gap=0, max_depth=5, fetcher=web)

    eq(web.asked, [a, b], "обход закрутился по кольцу ссылок")
    eq(len(res.nodes), 2, "кольцо дало лишние узлы")

    # Редирект в уже пройденное — то же кольцо, но невидимое по адресу ссылки.
    x = "https://x.example.com/vacancy/1"
    y = "https://y.example.com/go/42"
    web2 = Web({x: page(y), y: (page(), x)})
    res2 = crawl.crawl([x], gap=0, max_depth=5, fetcher=web2)
    eq(web2.asked, [x, y], "редирект на пройденную страницу увёл обход на второй круг")
    ok(any("уже пройденный" in n.note for n in res2.nodes),
       f"возврат по редиректу не назван: {[n.note for n in res2.nodes]}")

    # Кольцо из meta-refresh: HTTP-редиректа нет, кольцо есть.
    m1 = "https://m1.example.com/vacancy/1"
    m2 = "https://m2.example.com/vacancy/2"
    meta = '<meta http-equiv="refresh" content="0;url={u}">'
    web3 = Web({m1: page(extra=meta.format(u=m2)),
                m2: page(extra=meta.format(u=m1))})
    crawl.crawl([m1], gap=0, fetcher=web3)
    eq(web3.asked, [m1, m2], "meta-refresh закольцевал обход")


def test_one_page_written_four_ways_is_read_once():
    """Дедуп: схема, `www`, якорь, хвост трекинга и слеш — не разные страницы."""
    canon = "https://firm.example.com/vacancy/7"
    twins = [canon,
             "http://www.firm.example.com/vacancy/7/",
             "https://firm.example.com/vacancy/7?utm_source=telegram&utm_medium=post",
             "https://firm.example.com/vacancy/7#apply"]
    web = Web({canon: page()})
    res = crawl.crawl(twins, gap=0, fetcher=web)

    eq(len(web.asked), 1, f"одна страница прочитана несколько раз: {web.asked}")
    eq(len(res.nodes), 1, "дедуп не свёл написания одного адреса")
    eq(res.deduped, 3, "дедуп не посчитал снятые повторы")

    # А вот значащий параметр разными страницами быть обязан.
    eq(crawl.normalize("https://x.ru/jobs?id=1") == crawl.normalize("https://x.ru/jobs?id=2"),
       False, "дедуп склеил разные вакансии одного сайта")


def test_budgets_never_cut_in_silence():
    """Бюджеты страниц: и общий, и на хост. Оба обязаны назвать срезанное."""
    seeds = [f"https://firm.example.com/vacancy/{i}" for i in range(5)]
    web = Web({u: page() for u in seeds})
    res = crawl.crawl(seeds, gap=0, per_host=2, fetcher=web)
    eq(len(web.asked), 2, "потолок на хост не сработал")
    eq(len(res.dropped), 3, f"срезанное по хосту не названо: {res.dropped}")
    ok(all("firm.example.com" in x["why"] for x in res.dropped),
       f"причина среза не названа хостом: {res.dropped}")

    others = [f"https://firm{i}.example.com/vacancy/{i}" for i in range(5)]
    web2 = Web({u: page() for u in others})
    res2 = crawl.crawl(others, gap=0, max_pages=3, fetcher=web2)
    eq(len(web2.asked), 3, "общий потолок страниц не сработал")
    ok(all("бюджет страниц" in x["why"] for x in res2.dropped),
       f"срез по общему бюджету не назван: {res2.dropped}")


def test_the_walk_stays_out_of_social_networks_and_files():
    """В соцсети, счётчики и файлы обход не ходит — и говорит, почему."""
    start = "https://firm.example.com/vacancy/1"
    web = Web({start: page("https://t.me/some_channel",
                           "https://vk.com/firm",
                           "https://cdn.firm2.example.com/vacancy/opisanie.pdf",
                           "https://jobs.lever.co/firm/42"),
               "https://jobs.lever.co/firm/42": page()})
    res = crawl.crawl([start], gap=0, fetcher=web)

    eq(web.asked, [start, "https://jobs.lever.co/firm/42"],
       "обход ушёл в соцсети или в файл")
    reasons = {x["url"]: x["why"] for x in res.dropped}
    ok(any("t.me" in u for u in reasons), f"телеграм не назван пропущенным: {reasons}")
    ok(any(u.endswith(".pdf") for u in reasons), f"файл не назван пропущенным: {reasons}")


def test_shortener_is_followed_to_where_it_lands():
    """Короткая ссылка проходится, и маршрутом называется КОНЕЦ пути.

    В контакты сокращатель не годится (`tgpost` его выбрасывает: куда ведёт, из
    адреса не видно) — но обход умеет посмотреть, и после него это уже не
    догадка. Вести человека надо на конечный адрес: сокращатель завтра может
    указывать в другое место.
    """
    short = "https://clck.ru/abcdef"
    real = "https://careers.firm.example.com/vacancy/25712"
    web = Web({short: (page(title="Вакансия в Firm"), real)})
    res = crawl.crawl([short], gap=0, fetcher=web)

    eq(len(res.nodes), 1, "сокращатель не пройден")
    eq(res.nodes[0].final_url, real, "конечный адрес короткой ссылки не записан")
    eq([r["url"] for r in crawl.routes(res)], [real],
       "маршрутом назван сокращатель, а не то, куда он ведёт")


def test_a_dead_direct_link_loses_to_a_live_showcase():
    """Мёртвый прямой канал не может быть «лучшим маршрутом».

    🔴 Живой случай 09.08.2026: в посте лежала `career.avito.com/...`, по
    домену — прямой канал работодателя и потому лучший маршрут. Страница
    отдавала 404: вакансию закрыли, а пост остался висеть. Обход это видит,
    и `best` обязан учитывать увиденное.
    """
    dead = "https://career.firm.example.com/vacancies/19383/"
    alive = "https://hh.ru/vacancy/135826128"
    web = Web({alive: page(title="Go-разработчик")})   # dead в фикстуре нет = 404
    res = crawl.crawl([dead, alive], gap=0, fetcher=web)

    by_url = {n.url: n for n in res.nodes}
    eq(by_url[dead].liveness, "МЕРТВА", "404 по прямой ссылке не назван смертью")
    eq(by_url[alive].liveness, "ЖИВА", "живая страница витрины не опознана")

    routes = crawl.routes(res)
    eq(applyopt.best(routes), alive,
       "лучшим маршрутом остался мёртвый прямой канал")
    ok(any("✗МЕРТВА" in line for line in applyopt.render(routes)),
       "мёртвый маршрут напечатан как обычный")

    # Без проверенной живости порядок прежний: незнание — не приговор.
    plain = [{"url": dead, "publisher": applyopt.EMPLOYER, "is_direct": True, "rank": 0},
             {"url": alive, "publisher": applyopt.AGGREGATOR, "is_direct": False, "rank": 1}]
    eq(applyopt.best(plain), dead,
       "непроверенный прямой канал понижен — за отсутствие обхода наказывать нечем")


def test_a_wall_is_not_a_dead_vacancy():
    """Антибот-стена — это молчание площадки, а не приговор вакансии."""
    walled = "https://firm.example.com/vacancy/1"
    web = Web({walled: BlockedError(walled, "антибот-проверка (captcha)", 403)})
    res = crawl.crawl([walled], gap=0, fetcher=web)

    eq(res.nodes[0].liveness, "НЕИЗВЕСТНО", "стена принята за мёртвую вакансию")
    eq(res.nodes[0].state, "captcha", "состояние страницы записано неверно")

    # То же самое, но стена приехала телом страницы с кодом 200.
    web2 = Web({walled: "<html><title>Just a moment</title>Checking your browser</html>"})
    res2 = crawl.crawl([walled], gap=0, fetcher=web2)
    eq(res2.nodes[0].liveness, "НЕИЗВЕСТНО", "стена в теле ответа принята за вакансию")


def test_the_walk_digs_from_the_showcase_to_the_employer():
    """Ради чего всё: витрина → сайт работодателя → доска ATS.

    Один запрос по ссылке из поста этого не даёт: витрина отвечает 200 и
    выглядит ответом, а прямой канал лежит на два перехода дальше.
    """
    post_link = "https://hh.ru/vacancy/135826128"
    site = "https://careers.firm.example.com/vacancy/25712"
    ats = "https://jobs.lever.co/firm/42"
    web = Web({post_link: page(site, title="Go-разработчик на hh"),
               site: page(ats, title="Go-разработчик — Firm"),
               ats: page(title="Apply to Firm")})
    res = crawl.crawl([post_link], gap=0, fetcher=web)

    eq([n.url for n in res.nodes], [post_link, site, ats], "цепочка обхода оборвалась")
    kinds = [n.publisher for n in res.nodes]
    eq(kinds, [applyopt.AGGREGATOR, applyopt.EMPLOYER, applyopt.ATS],
       "публикующая сторона определена неверно")

    s = crawl.summary(res)
    eq(s["liveness"], "ЖИВА", "живость по совокупности пройденного определена неверно")
    # Лучший контакт — собственная страница вакансии работодателя: по
    # `applyopt._RANK` его домен ближе, чем доска ATS. Важно, что это НЕ витрина,
    # с которой начали, и что порядок страниц тут ровно тот же, что у `best`.
    eq(s["best_contact"]["value"], site,
       f"лучшим контактом названа не страница работодателя: {s['best_contact']}")
    eq(applyopt.best(crawl.routes(res)), s["best_contact"]["value"],
       "обход и `applyopt.best` отвечают на один вопрос по-разному")
    ok(s["employer"] and s["employer"]["value"] == "firm",
       f"работодатель не выведен из адреса ATS: {s['employer']}")


def test_markup_garbage_never_kills_a_live_vacancy():
    """🔴 Маркеры ищутся ТОЛЬКО в видимом тексте.

    Живая цена ошибки: по строке Sentry «Method not found» из JS-кода hh были
    объявлены мёртвыми 12 живых вакансий, а по значению
    `"applicant.negotiations.vacancyArchived":"Вакансия в архиве"` из словаря
    локализации — архивной вакансия GS Labs. Поэтому обход не судит сам, а
    зовёт `card.liveness_from_page`; тест стережёт, что он её и зовёт.
    """
    url = "https://hh.ru/vacancy/135826128"
    dirty = (
        '<html><head><title>Go-разработчик</title></head><body>'
        '<script>window.onerror=function(){"Method not found"}</script>'
        '<script>window.L={"applicant.negotiations.vacancyArchived":"Вакансия в архиве"}</script>'
        '<h1>Go-разработчик</h1><p>Обязанности и требования</p>'
        '<a href="#apply">Откликнуться</a></body></html>')
    web = Web({url: dirty})
    res = crawl.crawl([url], gap=0, fetcher=web)

    eq(res.nodes[0].liveness, "ЖИВА",
       "живая вакансия объявлена мёртвой по мусору из разметки")
    eq(res.nodes[0].state, "ok", "состояние страницы посчитано по мусору")


def test_a_showcase_redirector_is_followed_to_the_end():
    """Витрина-редирект своей страницы не имеет — ценен конечный адрес.

    `jobviewtrack.com` (careerjet) и `jooble.org/away/…` увозят на сайт
    работодателя. Проверять надо КОНЕЧНЫЙ адрес, иначе вердикт всегда «не
    похоже на страницу вакансии».
    """
    away = "https://jobviewtrack.com/v2/abc123"
    real = "https://careers.firm.example.com/vacancy/25712"
    web = Web({away: (page(title="Go-разработчик"), real)})
    res = crawl.crawl([away], gap=0, fetcher=web)

    eq(res.nodes[0].final_url, real, "витрина-редирект не пройдена до конца")
    eq([r["url"] for r in crawl.routes(res)], [real],
       "маршрутом названа витрина-редирект, а не то, куда она ведёт")

    # Не раскрылась — это тоже ответ, и он обязан быть назван.
    stuck = "https://jooble.org/away/12345"
    web2 = Web({stuck: "<html><title>Переход…</title><body>Загрузка</body></html>"})
    res2 = crawl.crawl([stuck], gap=0, fetcher=web2)
    ok("не раскрылась" in res2.nodes[0].note,
       f"молчаливый тупик вместо честного «не раскрылась»: {res2.nodes[0].note!r}")


def test_an_spa_shell_is_named_not_guessed():
    """Пустой каркас SPA — «не смогли прочитать», а не «вакансии нет»."""
    url = "https://careered.io/jobs/1"
    shell = ('<html><head><title>Careered</title></head><body>'
             '<div id="__next"></div><script src="/_next/static/app.js"></script>'
             '</body></html>')
    web = Web({url: shell})
    res = crawl.crawl([url], gap=0, fetcher=web)

    node = res.nodes[0]
    ok(node.shell, "каркас SPA не опознан")
    eq(node.liveness, "НЕИЗВЕСТНО", "по пустому каркасу вынесен вердикт о вакансии")
    ok("render" in node.note, f"не сказано, чем каркас раскрывать: {node.note!r}")


def test_a_link_back_to_the_showcase_is_not_a_contact():
    """Ссылка обратно на витрину контактом не является.

    careered кладёт в `links.other_apply` адрес `careered.io/jobs/<тот же id>`.
    Формально это ссылка отклика, практически — та же витрина, с которой мы
    начали, и предлагать её как «прямой контакт» значит соврать.
    """
    job = "https://careered.io/jobs/777"
    site = "https://careers.firm.example.com/vacancy/25712"
    web = Web({job: page(job, site, title="Go-разработчик"),
               site: page(title="Go-разработчик — Firm")})
    res = crawl.crawl([job], gap=0, fetcher=web)

    best = crawl.best_contact(res)
    eq(best["value"], site, f"лучшим контактом названа витрина: {best}")
    eq(best["kind"], "вакансия на сайте работодателя", "вид контакта определён неверно")

    # И то же самое по сохранённым маршрутам — вторая команда за прогон в сеть
    # не идёт, но обязана отвечать так же.
    cached = crawl.contact_from_routes(crawl.routes(res))
    eq(cached["value"], site, f"кэш маршрутов советует витрину: {cached}")


def test_the_hiring_mailbox_beats_the_page_and_the_front_desk_does_not():
    """`hr@` бьёт страницу отклика, `sales@` — не бьёт ничего.

    Письмо человеку доходит быстрее формы, поэтому почта НАЙМА стоит выше любой
    страницы. Письмо в общую приёмную уходит в никуда, поэтому она не стоит
    вообще нигде: лучше форма, чем ящик, который вакансию не читает.
    """
    ats = "https://jobs.lever.co/firm/42"
    site = "https://careers.firm.example.com/vacancy/1"
    web = Web({site: page(ats, title="Firm", extra="Пишите: hr@firm.example.com"),
               ats: page(title="Apply")})
    res = crawl.crawl([site], gap=0, fetcher=web)
    best = crawl.best_contact(res)
    eq(best["value"], "hr@firm.example.com", f"почта найма не выбрана: {best}")

    web2 = Web({site: page(ats, title="Firm", extra="Вопросы: sales@firm.example.com"),
                ats: page(title="Apply")})
    res2 = crawl.crawl([site], gap=0, fetcher=web2)
    best2 = crawl.best_contact(res2)
    ok(best2["value"] in (site, ats),
       f"общая приёмная выдана за контакт по вакансии: {best2}")
    ok("почта" not in best2["kind"], f"вид контакта определён неверно: {best2}")


def test_a_card_shouts_when_the_walk_found_the_vacancy_dead():
    """Обход выяснил, что вакансия мертва — карточка обязана сказать это сверху.

    🔴 Найдено ревью 09.08.2026. Обход по посту Авито честно отвечает «МЕРТВА:
    все пройденные страницы отдают 404», в маршрутах ссылка помечена ✗МЕРТВА и
    лучшей не выбирается. Но во ФЛАГАХ карточки об этом не было ни слова: сверху
    стояло спокойное «🟡 вилки нет, 🟢 требуют 3 лет», и человек, читающий
    карточку сверху вниз, узнавал бы о смерти вакансии в лучшем случае из
    середины таблицы маршрутов.

    Смерть вакансии — самый важный факт о ней: он отменяет и фит, и письмо.
    Место такому флагу — рядом с «работодатель не раскрыт», а не в примечании."""
    from .card import flags

    dead = [{"url": "https://career.example.com/v/1", "liveness": "МЕРТВА",
             "is_direct": True, "publisher": "employer"}]
    got = " ".join(flags({"company": "Acme"}, None, None, "", routes=dead))
    ok("МЕРТВ" in got.upper(),
       f"карточка молчит о том, что вакансия мертва: {got!r}")

    alive = [{"url": "https://career.example.com/v/1", "liveness": "ЖИВА",
              "is_direct": True, "publisher": "employer"}]
    got2 = " ".join(flags({"company": "Acme"}, None, None, "", routes=alive))
    ok("МЕРТВ" not in got2.upper(),
       f"живая вакансия помечена мёртвой: {got2!r}")

    # Часть маршрутов мертва, часть жива — это НЕ смерть вакансии: так бывает,
    # когда одна площадка сняла перепечатку, а у работодателя всё открыто.
    mixed = dead + alive
    got3 = " ".join(flags({"company": "Acme"}, None, None, "", routes=mixed))
    ok("МЕРТВ" not in got3.upper(),
       f"вакансия с живым маршрутом объявлена мёртвой: {got3!r}")


def test_a_stranger_mailbox_on_a_banner_is_not_the_employer_contact():
    """Почта НАЙМА бьёт страницу — но только если она принадлежит НАНИМАТЕЛЮ.

    🔴 Живой случай 09.08.2026, найден на первом же прогоне обхода. Пост вёл на
    вакансию Kaspersky (`careers.kaspersky.ru/vacancy/25712`), а на странице
    висел баннер рейтинга работодателей РБК. Обход дошёл до `hr-rating.rbc.ru`,
    увидел там `hr-forum@rbc.ru`, классифицировал домен как «работодатель» (он
    и правда не агрегатор) и выдал ЭТУ почту лучшим контактом.

    Цена ошибки максимальная из возможных: письмо от имени владельца ушло бы
    чужой компании, а он бы этого не заметил — в карточке стоял бы уверенный
    «ЛУЧШИЙ КОНТАКТ [почта найма]». Поэтому почта засчитывается, только когда
    её домен связан с работодателем вакансии, а не просто найден по дороге."""
    vac = "https://careers.kaspersky.ru/vacancy/25712"
    banner = "https://hr-rating.rbc.ru/"
    web = Web({vac: page(banner, title="Lead Go Developer"),
               banner: page(title="Рейтинг работодателей",
                            extra="Пишите: hr-forum@rbc.ru")})
    res = crawl.crawl([vac], gap=0, fetcher=web)
    best = crawl.best_contact(res)
    ok(best is not None, "контакт не найден вовсе")
    if best and "rbc.ru" in str(best.get("value", "")):
        FAILS.append(f"чужая почта с баннера выдана за контакт работодателя: {best}")
    if best:
        ok(str(best.get("value", "")).startswith("https://careers.kaspersky.ru"),
           f"вместо страницы вакансии работодателя выбрано: {best}")

    # А своя почта найма на своём же домене по-прежнему побеждает страницу.
    own = "https://careers.acme.example/vacancy/7"
    web2 = Web({own: page(title="Go dev", extra="Резюме: hr@acme.example")})
    best2 = crawl.best_contact(crawl.crawl([own], gap=0, fetcher=web2))
    eq(best2["value"], "hr@acme.example",
       f"своя почта найма перестала побеждать страницу: {best2}")


def test_the_deadline_stops_the_walk_and_says_so():
    """Общий дедлайн по времени. Часы подменяются — тест не спит."""
    seeds = [f"https://firm{i}.example.com/vacancy/{i}" for i in range(4)]
    web = Web({u: page() for u in seeds})
    ticks = iter([0, 0, 5, 30, 61, 61, 61, 61, 61])
    res = crawl.crawl(seeds, gap=0, deadline=60, fetcher=web,
                      clock=lambda: next(ticks))

    ok(len(web.asked) < 4, f"дедлайн не остановил обход: спрошено {len(web.asked)}")
    ok(any("дедлайн" in x["why"] for x in res.dropped),
       f"остановка по времени не названа: {res.dropped}")


def test_the_legal_footer_is_not_walked():
    """Политика конфиденциальности есть на каждом сайте и вакансии не касается."""
    site = "https://careers.firm.example.com/vacancy/1"
    web = Web({site: page("https://firm.example.com/privacy-policy",
                          "https://firm.example.com/vacancies/2"),
               "https://firm.example.com/vacancies/2": page()})
    res = crawl.crawl([site], gap=0, fetcher=web)
    ok(all("privacy" not in u for u in web.asked),
       f"обход ушёл в юридический подвал: {web.asked}")
    ok(any("privacy" in x["url"] for x in res.dropped),
       f"пропуск подвала не назван: {res.dropped}")


def test_reveal_does_not_spend_the_limit_when_the_walk_found_a_contact():
    """🔴 Обход идёт ДО раскрытия, и найденный даром контакт отменяет трату.

    Раскрытие — единственная необратимая трата во всём сборщике. Раньше решение
    «тратить или нет» принимал агент по памяти, то есть иногда не принимал.
    """
    from . import crawl as C
    from . import reveal as R
    from .testutil import patched

    url = "https://hirehi.ru/development/go-1"
    site = "https://careers.firm.example.com/vacancy/1"
    found = [{"url": site, "publisher": applyopt.EMPLOYER, "is_direct": True,
              "note": "обход: ссылка вакансии; жива — страница отдаёт вакансию",
              "rank": 0, "liveness": "ЖИВА", "state": "ok"}]

    def plan_with(walk_result):
        # Подменяем ровно три вещи: базу (её тут нет), поиск бесплатного
        # двойника (это отдельный источник, у него свой тест) и обход.
        with patched(R.store, "connect", _NoDB), \
             patched(R, "free_contact_for", lambda conn, u: None), \
             patched(C, "walk", lambda conn, u, **kw: walk_result):
            return R.plan([url], walk=True)

    plan = plan_with((None, found))
    eq([(p["url"], p["spend"]) for p in plan], [(url, False)],
       f"лимит потрачен там, где контакт нашёлся даром: {plan}")
    ok(site in plan[0]["why"], f"не сказано, что именно нашлось: {plan[0]['why']}")

    # И наоборот: обход ничего не дал — значит тратим, это законная трата.
    eq(plan_with((None, []))[0]["spend"], True,
       "раскрытие отменено там, где бесплатного контакта не нашлось")

    # Витрина бесплатным контактом НЕ считается: ради обхода витрины раскрытие
    # и затевается. Картину берём настоящую — ту, что вернёт обход по вакансии,
    # у которой прямого канала не нашлось.
    showcase = crawl.crawl(["https://hh.ru/vacancy/1"], gap=0,
                           fetcher=Web({"https://hh.ru/vacancy/1": page(title="Go")}))
    eq(C.best_contact(showcase)["kind"], "витрина",
       "обход назвал витрину чем-то другим — тест ниже проверял бы не то")
    eq(plan_with((showcase, C.routes(showcase)))[0]["spend"], True,
       "раскрытие отменено ссылкой на витрину — это не контакт работодателя")


class _NoDB:
    """Подстановка `store.connect`: соединения нет, оно тут и не нужно."""

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def test_the_best_route_is_one_you_can_actually_apply_from():
    """🔴 Живой случай 09.08.2026 (PayDepot): «лучшим» выходила главная компании.

    Маршрутами были корень `paydepot.com` и `paydepot.bamboohr.com/careers/24`.
    По домену корень ближе к работодателю — и потому побеждал, хотя откликнуться
    с главной страницы нельзя вовсе.
    """
    def opt(url, publisher, rank):
        return {"url": url, "publisher": publisher, "is_direct": True, "rank": rank}

    eq(applyopt.best([opt("https://paydepot.com/", applyopt.EMPLOYER, 0),
                      opt("https://paydepot.bamboohr.com/careers/24", applyopt.ATS, 1)]),
       "https://paydepot.bamboohr.com/careers/24",
       "лучшим назван корень сайта, с которого откликнуться нельзя")

    # Но среди страниц, где отклик ЕСТЬ, правило прежнее: прямой канал бьёт витрину.
    eq(applyopt.best([opt("https://hh.ru/vacancy/1", applyopt.AGGREGATOR, 0),
                      opt("https://acme.com/careers/go-dev", applyopt.EMPLOYER, 1)]),
       "https://acme.com/careers/go-dev",
       "витрина обогнала прямой канал — оба ведут на вакансию, порядок должен решать домен")

    # И пост не обгоняет сайт работодателя только потому, что оба «без отклика».
    eq(applyopt.best([opt("https://t.me/ch/1", applyopt.TELEGRAM, 0),
                      opt("https://acme.com/", applyopt.EMPLOYER, 1)]),
       "https://acme.com/", "телеграм-пост поставлен выше сайта работодателя")


def test_the_employer_slug_comes_from_the_right_part_of_the_ats_url():
    """🔴 У разных досок ATS слаг компании лежит в РАЗНОМ месте.

    Живой случай 09.08.2026 (пост cyprusithr/108287): по адресу
    `paydepot.bamboohr.com/careers/24` работодателем был назван «careers» —
    первый сегмент пути. У BambooHR, Huntflow, Recruitee компания сидит в
    поддомене, у Lever и Greenhouse — в пути.
    """
    cases = [("https://paydepot.bamboohr.com/careers/24", "paydepot"),
             ("https://job-boards.greenhouse.io/aptoslabs/jobs/4702283005", "aptoslabs"),
             ("https://jobs.lever.co/firm/42", "firm"),
             ("https://careers.smartrecruiters.com/AcmeInc/123", "AcmeInc")]
    for url, want in cases:
        web = Web({url: page(title="Apply")})
        res = crawl.crawl([url], gap=0, fetcher=web)
        got = (crawl.employer_guess(res) or {}).get("value")
        eq(got, want, f"работодатель из {url}")


def test_a_stranger_domain_is_not_guessed_as_the_employer():
    """Имя компании известно → домен, с ним не связанный, работодателем не назвать.

    Живой счёт 09.08.2026: вакансия Teleport, обход дошёл до `vseti.app` (витрина
    вакансий), `ya.ru` и статьи на `vc.ru` — и все трое считались «собственным
    доменом работодателя» просто потому, что их нет в списке агрегаторов.
    «Не витрина» — это не улика. Догадка без улики хуже молчания: она уводит
    письмо в чужую компанию ровно так же, как чужая почта с баннера.
    """
    url = "https://www.vseti.app/vakansii/123"
    web = Web({url: page(title="Вакансия Go Developer, компания Teleport")})
    res = crawl.crawl([url], gap=0, fetcher=web)
    got = crawl.employer_guess(res, company="Teleport")
    if got:
        FAILS.append(f"чужой домен назван работодателем: {got.get('value')}")

    # Обратная сторона: связь есть — догадка обязана остаться.
    own = "https://careers.kaspersky.ru/vacancy/1"
    web2 = Web({own: page(title="Вакансия")})
    res2 = crawl.crawl([own], gap=0, fetcher=web2)
    got2 = (crawl.employer_guess(res2, company="Kaspersky") or {}).get("value")
    eq(got2, "kaspersky.ru", "свой домен работодателя потерян из-за проверки")

    # И третья: имени нет вовсе — ради этого случая догадка и заведена.
    res3 = crawl.crawl([own], gap=0, fetcher=Web({own: page(title="Вакансия")}))
    if not crawl.employer_guess(res3, company=None):
        FAILS.append("при скрытом работодателе догадка пропала — а она тут и нужна")


def test_opened_but_speechless_is_not_called_unreachable():
    """«Открылась, но это не вакансия» ≠ «не открылась».

    Живой случай 09.08.2026: у PayDepot открылись и главная, и доска BambooHR,
    а итог гласил «ни одна страница не открылась (стены или сеть)» — то есть
    отчёт советовал чинить доступ там, где надо было посмотреть глазами.
    """
    url = "https://paydepot.example.com/"
    web = Web({url: "<html><title>PayDepot</title><body>Финансовая точность</body></html>"})
    res = crawl.crawl([url], gap=0, fetcher=web)

    live, why = crawl.liveness(res)
    eq(live, "НЕИЗВЕСТНО", "по странице без признаков вакансии вынесен вердикт")
    ok("открылись" in why, f"открывшаяся страница названа недоступной: {why!r}")

    # А вот когда правда не открылась (сеть, стена) — так и говорим.
    dead_net = "https://firm.example.com/vacancy/1"
    web2 = Web({dead_net: FetchError(dead_net, "TimeoutError: истекло время")})
    res2 = crawl.crawl([dead_net], gap=0, fetcher=web2)
    ok("не открылась" in crawl.liveness(res2)[1],
       f"недоступная страница названа открывшейся: {crawl.liveness(res2)[1]!r}")


def test_a_dry_run_shows_the_bill_and_never_opens_the_browser():
    """`reveal --dry-run` печатает план и выходит ДО браузера.

    Раскрытие — единственная необратимая трата в сборщике, и посмотреть на счёт
    до того, как он выставлен, должно быть можно без риска. Выход стоит раньше
    импорта playwright: сухой прогон обязан работать и там, где раскрытие не
    работает вовсе (нет сессии, нет браузера).
    """
    import contextlib
    import io

    from . import reveal as R
    from .testutil import patched

    url = "https://hirehi.ru/development/go-1"
    steps = [{"url": url, "spend": True,
              "why": "живая, бесплатного контакта не нашлось"}]
    out = io.StringIO()
    with patched(R, "plan", lambda urls, **kw: steps), \
         contextlib.redirect_stdout(out):
        code = R.reveal([url], dry_run=True)

    eq(code, 0, "сухой прогон вернул код ошибки")
    text = out.getvalue()
    ok("СПИСАТЬ" in text and url in text, f"план не напечатан:\n{text}")
    ok("лимит не тронут" in text, f"не сказано, что трат не было:\n{text}")


def test_check_links_asks_the_final_address_of_a_showcase_redirect():
    """`check-links` проверяет живость КОНЕЧНОГО адреса витрины-редиректа.

    У `jobviewtrack.com` (careerjet) и `jooble.org/away/…` своей страницы
    вакансии нет вовсе, и по самой витрине вердикт всегда выходил «не похоже на
    страницу вакансии». Кто такая витрина, знает `crawl.is_redirector` — один
    список на проект; этот тест стережёт, что `check-links` спрашивает именно
    его и идёт до конца, а не проверяет посредника.
    """
    import contextlib
    import io
    from types import SimpleNamespace

    from . import cli, net, resolve
    from .testutil import patched

    via = "https://jobviewtrack.com/v2/abcdef"
    dest = "https://careers.firm.example.com/vacancy/1"
    asked: list[str] = []

    def fake_fetch(url, **kw):
        asked.append(url)
        return page(title="Go-разработчик"), url

    out = io.StringIO()
    with patched(resolve, "follow", lambda u, **kw: {"chain": [u, dest]}), \
         patched(net, "fetch", fake_fetch), \
         contextlib.redirect_stdout(out):
        code = cli.cmd_check_links(SimpleNamespace(urls=[via], db=":memory:"))

    text = out.getvalue()
    eq(asked, [dest], f"живость спрошена не у конечного адреса: {asked}")
    eq(code, 0, f"живая вакансия за витриной названа проблемой:\n{text}")
    ok("ЖИВА" in text and dest in text, f"вердикт не про конечный адрес:\n{text}")


def test_an_inconclusive_walk_never_overwrites_a_known_verdict():
    """🔴 «НЕИЗВЕСТНО» в кэш ресёрча не пишется.

    Стена и страница без признаков вакансии — это отсутствие знания. В таблице
    при этом уже может лежать «ЖИВА», проверенная человеком или прошлой волной,
    и затирать её незнанием значит заставить получать дорогой вердикт заново —
    ровно то, ради чего таблица и заведена.
    """
    from . import crawl as C

    url = "https://firm.example.com/vacancy/1"
    walled = "<html><title>Just a moment</title>Checking your browser</html>"
    with store.connect(":memory:") as conn:
        conn.execute(
            "INSERT INTO vacancy (source, external_id, url, title, company, "
            "first_seen, last_seen) VALUES ('tg','1',?,'Go','Firm','x','x')", (url,))
        store.save_research(conn, "tg", "1", liveness="ЖИВА",
                            evidence="проверено человеком")
        C.walk(conn, url, depth=0, gap=0, fetcher=Web({url: walled}))
        got = store.research(conn, "tg", "1")
        marks = [o["liveness"] for o in store.apply_options(conn, "tg", "1")]

    eq(got["liveness"], "ЖИВА", "проверенный вердикт затёрт стеной")
    eq(got["evidence"], "проверено человеком", "пояснение вердикта затёрто")
    # А по самому маршруту неопределённость записана — там она никому не мешает
    # и объясняет, почему этот маршрут не выбран лучшим.
    eq(marks, ["НЕИЗВЕСТНО"], "живость маршрута не записана")


def test_crawl_facts_survive_the_next_cheap_command():
    """Живость, добытая обходом, не затирается дешёвым пересчётом маршрутов.

    `brief` пересчитывает маршруты по доменам и пишет их в ту же таблицу. С
    прежним `INSERT OR REPLACE` результат обхода жил до первой такой команды —
    то есть дорогая проверка пропадала, и это было бы не видно никак.
    """
    url = "https://career.firm.example.com/vacancies/19383/"
    with store.connect(":memory:") as conn:
        store.save_apply_options(conn, "tg", "1", [
            {"url": url, "publisher": applyopt.EMPLOYER, "is_direct": True,
             "note": "обход: ссылка из поста; мертва — HTTP 404",
             "rank": 0, "liveness": "МЕРТВА", "state": "gone"}])
        # Тот же адрес, но знанием попроще: живость неизвестна.
        store.save_apply_options(conn, "tg", "1", [
            {"url": url, "publisher": applyopt.EMPLOYER, "is_direct": True,
             "note": "ссылка отклика ИЗ ТЕЛА поста (пост — витрина)", "rank": 0}])
        got = store.apply_options(conn, "tg", "1")

    eq(len(got), 1, "маршрут раздвоился")
    eq(got[0]["liveness"], "МЕРТВА", "результат обхода затёрт дешёвым пересчётом")
    eq(got[0]["state"], "gone", "состояние страницы затёрто дешёвым пересчётом")
    eq(got[0]["note"], "ссылка отклика ИЗ ТЕЛА поста (пост — витрина)",
       "пояснение не обновилось — свежее знание об источнике маршрута потеряно")


def test_all_post_links_become_routes_not_the_first_four():
    """Из поста в маршруты идут ВСЕ ссылки, а не первые четыре.

    Отсечка `found[:4]` стояла в `gather` с первого дня и молча теряла хвост:
    в постах с подписями каналов и с несколькими вакансиями настоящий контакт
    бывает пятым. Обход это чинит только вместе с `gather` — обходить нечего,
    если ссылку выбросили до него.
    """
    from . import applyopt as A
    from .testutil import patched

    links = [f"https://firm{i}.example.com/vacancy/{i}" for i in range(6)]
    with patched(A, "fetch_apply_links", lambda url, **kw: (links, "из тела поста")):
        opts = A.gather({"url": "https://t.me/ch/519", "source": "tg"})

    got = [o["url"] for o in opts]
    missing = [u for u in links if u not in got]
    eq(missing, [], f"ссылки поста потеряны до обхода: {missing}")


def test_a_double_escaped_link_is_unpacked_before_the_walk():
    """🔴 Телеграм экранирует href ДВАЖДЫ, и одной распаковки мало.

    Живой случай 09.08.2026 (пост job_web3/3757): в веб-версии лежало
    `...?source=web3.career&amp;amp;gh_src=...`, после одной распаковки —
    `&amp;gh_src`, и Greenhouse получал параметр с именем `amp;gh_src`.
    Якорь при этом срезается: документа он не адресует.
    """
    from . import tgpost

    html = ('<div data-post="ch/1"><a href="https://job-boards.greenhouse.io/acme/'
            'jobs/1?source=web3.career&amp;amp;gh_src=web3.career#application_form">'
            'Откликнуться</a></div>')
    eq(tgpost.apply_links_from_post(html, "ch", 1),
       ["https://job-boards.greenhouse.io/acme/jobs/1?source=web3.career&gh_src=web3.career"],
       "ссылка из поста осталась экранированной или с якорем")

    # А hash-маршрут SPA — это и есть адрес страницы, его срезать нельзя.
    eq(crawl.clean_url("https://acme.com/app#/jobs/42"), "https://acme.com/app#/jobs/42",
       "срезан hash-маршрут — по обрезку страница не откроется")


def test_post_links_reach_the_walk_with_shorteners_kept():
    """Стартовые ссылки берутся из тела поста, сокращатели — сохраняются.

    Для карточки сокращатель контактом не считается, для обхода это дорога.
    Разница живёт одним флагом в одном месте, а не вторым списком доменов.
    """
    from . import tgpost

    html = ('<div data-post="ch/519">'
            '<a href="https://t.me/ch">канал</a>'
            '<a href="https://clck.ru/abcdef">Откликнуться</a>'
            '<a href="https://careers.firm.example.com/vacancy/1">сайт</a>'
            '</div><div data-post="ch/520"><a href="https://other.example.com/x">чужой</a></div>')

    strict = tgpost.apply_links_from_post(html, "ch", 519)
    wide = tgpost.apply_links_from_post(html, "ch", 519, keep_shorteners=True)
    eq(strict, ["https://careers.firm.example.com/vacancy/1"],
       "строгий фильтр контактов изменился")
    eq(wide, ["https://clck.ru/abcdef", "https://careers.firm.example.com/vacancy/1"],
       "обход не получил короткую ссылку")
    ok("https://other.example.com/x" not in wide,
       "взята ссылка из СОСЕДНЕГО поста — это подмена работодателя")


def main() -> int:
    # Тесты собираются АВТОМАТИЧЕСКИ — все `test_*` этого модуля, в порядке
    # определения. Ручной список означал, что забытое имя = тест, который не
    # запускается и потому «зелёный» всегда: 09.08.2026 так молча не работали
    # сразу две новые проверки, и обе ловили настоящие дефекты.
    import inspect as _inspect
    import sys as _sys
    mod = _sys.modules[__name__]
    tests = [f for _, f in _inspect.getmembers(mod, _inspect.isfunction)
             if f.__name__.startswith("test_") and f.__module__ == __name__
             and not any(pr.default is pr.empty
                         for pr in _inspect.signature(f).parameters.values())]
    tests.sort(key=lambda f: f.__code__.co_firstlineno)
    for fn in tests:
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
