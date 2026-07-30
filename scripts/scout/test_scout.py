"""Тесты на то, что ломается тихо.

Здесь проверяется не «работает ли сеть», а разбор вилок и ключ дубля — места, где
ошибка не падает, а молча уезжает в карточку. Неверно разобранная вилка выглядит как
факт о зарплате и врёт пользователю уверенным тоном.

    python3 -m scripts.scout.test_scout
"""

from __future__ import annotations

import sys

from .atsapi import country_matcher, parse_job_url
from .detail import html_to_text, md_to_text
from .model import Vacancy, dup_key, norm_currency, salary_str
from .resolve import classify, find_targets
from .sources import parse_salary
from .tg import classify as tg_classify, parse_dump

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def test_salary():
    # Реальные строки с площадок, а не выдуманные примеры.
    cases = [
        ("до 500 000 ₽",            (None, 500000, "RUB")),
        ("от 300 000 до 490 000 ₽", (300000, 490000, "RUB")),
        ("400 000 ₽",               (400000, None, "RUB")),
        ("от 250 000 ₽",            (250000, None, "RUB")),
        ("200 000 — 250 000 ₽",     (200000, 250000, "RUB")),
        ("$3000 - $5000",           (3000, 5000, "USD")),
        ("2 800—12 500 USD",        (2800, 12500, "USD")),
        # Суффикс тысяч: так пишут geekjob, hirehi и Glassdoor. Без разворота
        # множителя это 350 рублей — уверенно напечатанная ложь в тысячу раз.
        ("от 350K ₽",               (350000, None, "RUB")),
        ("150K — 200K ₽",           (150000, 200000, "RUB")),
        # Валюта КОДОМ у второй границы (Glassdoor): без этого верхняя граница
        # молча терялась и вилка читалась как «от 90 000».
        ("EUR 90K - EUR 130K",      (90000, 130000, "EUR")),
        ("з/п не указана",          (None, None, None)),
        ("",                        (None, None, None)),
        (None,                      (None, None, None)),
    ]
    for text, (wf, wt, wc) in cases:
        f, t, c, _ = parse_salary(text)
        eq((f, t, c), (wf, wt, wc), f"parse_salary({text!r})")

    # Год публикации не должен превратиться в зарплату.
    f, t, _, _ = parse_salary("Опубликовано в 2026")
    eq((f, t), (None, None), "parse_salary не путает год с вилкой")


def test_zero_is_not_a_salary():
    """Ноль у careered означает «не указано». Нельзя показать «0–0 ₽» как условия."""
    v = Vacancy(source="careered", external_id="1", url="u", title="t",
                salary_from=None, salary_to=None, currency="RUB")
    eq(v.salary_str(), "", "нет вилки → пустая строка, а не «0–0»")


def test_salary_str():
    eq(Vacancy(source="s", external_id="1", url="u", title="t", salary_from=200000,
               salary_to=250000, currency="RUR").salary_str(),
       "200 000–250 000 RUB", "вилка форматируется с пробелами и нормализованной валютой")
    eq(Vacancy(source="s", external_id="1", url="u", title="t",
               salary_from=350000, currency="₽").salary_str(),
       "от 350 000 RUB", "открытая снизу вилка")


def test_currency():
    eq(norm_currency("RUR"), "RUB", "RUR → RUB")
    eq(norm_currency("₽"), "RUB", "знак рубля")
    eq(norm_currency(None), None, "пусто остаётся пустым")


def test_dup_key():
    """Ключ дубля — подсказка, а не автосклейка: он обязан быть консервативным."""
    a = dup_key("Т-Банк", "Senior Golang-разработчик")
    b = dup_key("Т-Банк", "Golang разработчик")
    # Порядок слов и пунктуация ключ менять не должны…
    eq(dup_key("Т-Банк", "Golang разработчик"),
       dup_key("Т-Банк", "разработчик, Golang!"),
       "порядок слов и пунктуация развели одну вакансию")
    # …а вот ГРЕЙД — должен, и это осознанная смена контракта 30.07.2026.
    #
    # Раньше грейд лежал в `_NOISE`, и «Junior Software Engineer with Accounting
    # Experience» получал тот же ключ, что «Senior …» — `run_enrich` выбрасывал
    # вторую как дубль первой. Замер по базе (4091 запись): так терялось 42
    # записи (Sezzle Junior/Senior в четырёх странах, Canonical Junior/Senior
    # Ubuntu, OKX Junior/Senior PM, Авито «Python-разработчик»/«Старший Python»).
    # Это разные вакансии: разные требования и разные деньги.
    #
    # Цена размена проверена на тех же данных и оказалась нулевой: все 15 групп
    # НАСТОЯЩИХ кросс-площадочных дублей склеиваются по-прежнему (в живых
    # заголовках грейд у одной и той же вакансии совпадает — см.
    # test_bare_key_call_stays_conservative в test_sources_auth).
    if a == b:
        FAILS.append("dup_key: Junior и Senior одной роли склеились в один ключ")
    c = dup_key("Ozon", "Golang разработчик")
    if a == c:
        FAILS.append("dup_key: разные компании склеились в один ключ")


def test_resolver_ignores_social():
    """Соцсеть из футера не может стать «лучшим путём отклика» — на этом уже обожглись."""
    html = """
    <a href="https://setka.ru/">Откликнуться</a>
    <a href="https://boards.greenhouse.io/acme/jobs/42">Apply</a>
    <form><button>Откликнуться</button></form>
    """
    targets = find_targets(html, "https://hh.ru/vacancy/1")
    kinds = {t.kind for t in targets}
    if "ats" not in kinds:
        FAILS.append("resolve: не распознал ATS-ссылку Greenhouse")
    submit = [t for t in targets if t.kind == "form-submit"]
    if not submit or submit[0].safe_to_open:
        FAILS.append("resolve: кнопка внутри <form> должна быть помечена как неотправляемая")


def test_classify():
    eq(classify("https://job-boards.greenhouse.io/x/jobs/1")[0], "ats", "greenhouse → ats")
    eq(classify("https://jobs.lever.co/acme/1")[0], "ats", "lever → ats")
    eq(classify("https://hh.ru/vacancy/1")[0], "aggregator", "hh → витрина")
    eq(classify("https://tbank.ru/career/")[0], "external", "сайт компании → external")


def test_tg_split_on_header_only():
    """Пустая строка ВНУТРИ тела не должна резать сообщение: граница — только заголовок.

    Иначе одна вакансия с абзацами посчитается тремя, и счётчики соврут."""
    dump = (
        "[#1] [2026-07-28T07:00:00.000Z] Автор Один: #vacancy Go-разработчик\n"
        "\n"
        "абзац после пустой строки — всё ещё сообщение #1\n"
        "\n"
        "[#2] [2026-07-28T08:00:00.000Z] Автор Два (@nick): #резюме\n"
        "Должность: тестировщик\n"
        "Опыт: 5 лет\n"
        "\n"
        "[#3] [2026-07-28T09:00:00.000Z] Канал: пост с меткой erid: ABC123\n"
    )
    msgs = parse_dump(dump)
    eq(len(msgs), 3, "tg: три заголовка → три сообщения")
    eq(msgs[0].id, "1", "tg: id первого")
    if "абзац после пустой строки" not in msgs[0].body:
        FAILS.append("tg: тело с внутренней пустой строкой развалилось")
    for m in msgs:
        tg_classify(m)
    eq(msgs[0].category, "candidate", "tg: вакансия — кандидат")
    if "GO" not in msgs[0].tags:
        FAILS.append("tg: не поставил [GO] на Go-разработчика")
    eq(msgs[1].category, "resume", "tg: #резюме + анкета → резюме")
    eq(msgs[2].category, "ad", "tg: erid: → реклама")


def test_tg_resume_form_without_hashtag():
    """Анкета «Должность:…Опыт:…» — резюме и без хэштега."""
    dump = ("[#7] [2026-07-28T07:00:00.000Z] Кто-то: Ищу интересный проект\n"
            "Должность: аналитик\nЗП: по договорённости\nОпыт: 3 года\n")
    msgs = parse_dump(dump)
    tg_classify(msgs[0])
    eq(msgs[0].category, "resume", "tg: анкетная форма без #резюме")


def test_html_to_text():
    got = html_to_text("<div><h3>Стек</h3><ul><li>Go</li><li>PostgreSQL</li></ul>"
                       "<p>Оформление по &laquo;ТК&raquo;</p><script>var x=1;</script></div>")
    if "• Go" not in got or "• PostgreSQL" not in got:
        FAILS.append(f"html_to_text: потерял пункты списка: {got!r}")
    if "var x" in got:
        FAILS.append("html_to_text: скрипт утёк в текст")
    if "«ТК»" not in got:
        FAILS.append(f"html_to_text: не раскодировал entities: {got!r}")
    got = md_to_text("![](https://x/logo.png)\n\n**Роль**\n\n[сайт](https://a.b)")
    if "logo.png" in got or "https://a.b" in got:
        FAILS.append(f"md_to_text: мусор из markdown-ссылок: {got!r}")


def test_html_to_text_drops_template_state():
    """hh прячет ~470 КБ Redux-стейта в <template id="HH-Lux-InitialState">.

    Живой замер на hh.ru/vacancy/135737130: html_to_text(<body>) без вырезания
    template = 381 494 символа сырого JSON, с вырезанием = 4 729. Мина была
    замаскирована тем, что generic сначала берёт <main>; на первом фолбэке
    на <body> JSON уехал бы в выжимку — а _flag_skeleton нашёл бы в нём
    «требовани» и посчитал страницу разобранной.
    """
    state = '{"vacancyView":{"requirement":"требования","salary":null},"x":' \
            + ",".join(f'"{i}"' for i in range(500)) + "}"
    page = (f"<body><main><h1>Go-разработчик</h1><p>Требования: Go, PostgreSQL</p></main>"
            f'<template id="HH-Lux-InitialState">{state}</template></body>')
    got = html_to_text(page)
    if "vacancyView" in got or '"499"' in got:
        FAILS.append(f"html_to_text: Redux-стейт из <template> утёк в текст "
                     f"({len(got)} символов)")
    if "Go-разработчик" not in got or "PostgreSQL" not in got:
        FAILS.append(f"html_to_text: вместе с template потерян текст вакансии: {got!r}")
    if len(got) > 200:
        FAILS.append(f"html_to_text: выжимка раздута до {len(got)} символов — "
                     f"template вырезан не целиком")


def test_html_to_text_keeps_form_questions():
    """`form` вырезать НЕЛЬЗЯ: в форме отклика живут вопросы работодателя.

    По ним модель решает, что писать вместо сопроводительного, — потерять их
    дороже, чем стерпеть разметку формы в тексте."""
    page = ("<body><form action='/apply'><label>Почему вы хотите к нам?</label>"
            "<textarea></textarea><label>Ваши зарплатные ожидания</label>"
            "<input name='salary'></form></body>")
    got = html_to_text(page)
    for want in ("Почему вы хотите к нам?", "Ваши зарплатные ожидания"):
        if want not in got:
            FAILS.append(f"html_to_text: потерян вопрос формы {want!r}: {got!r}")


def test_parse_job_url():
    cases = [
        ("https://job-boards.greenhouse.io/gitlab/jobs/8503792002",
         ("greenhouse", "gitlab", "8503792002")),
        ("https://boards.greenhouse.io/sezzle/jobs/123?utm=x", ("greenhouse", "sezzle", "123")),
        ("https://jobs.lever.co/binance/1e480836-71de-48e5-887c-733a76e7013b",
         ("lever", "binance", "1e480836-71de-48e5-887c-733a76e7013b")),
        ("https://jobs.ashbyhq.com/ruby-labs/9ce107d0-c518-44ee-9a94-73e5f44d6866",
         ("ashby", "ruby-labs", "9ce107d0-c518-44ee-9a94-73e5f44d6866")),
        ("https://kodland.recruitee.com/o/some-slug", ("recruitee", "kodland", "some-slug")),
        ("https://apply.workable.com/covergo/j/87F974058D/", ("workable", "covergo", "87F974058D")),
        ("https://flo.bamboohr.com/careers/399", ("bamboohr", "flo", "399")),
        ("https://hh.ru/vacancy/1", None),
    ]
    for url, want in cases:
        eq(parse_job_url(url), want, f"parse_job_url({url})")


def test_country_matcher_structural():
    """Страна из secondaryLocations должна находиться — ровно тот случай Ruby Labs,
    из-за которого фильтр по одному полю терял 16 вакансий из 39."""
    pat = country_matcher("TR")
    for loc in ("Istanbul", "Türkiye", "Remote - Turkey", "TR"):
        if not pat.search(loc):
            FAILS.append(f"country_matcher(TR) не нашёл {loc!r}")
    if pat.search("Austria"):
        FAILS.append("country_matcher(TR): Austria — ложное срабатывание")


def test_salary_str_function():
    eq(salary_str(None, None, "RUB"), "", "нет вилки → пустая строка")
    eq(salary_str(3000, 5000, "USD"), "3 000–5 000 USD", "вилка в долларах")
    eq(salary_str(150000, 250000, "RUR", False), "150 000–250 000 RUB net",
       "RUR нормализуется, net дописывается")


# ──────────────────────────────────────────────────────────────────────────────
# hh-sync: разбор страницы откликов
# ──────────────────────────────────────────────────────────────────────────────

# Фикстура-минимум: рука об руку с реальной вёрсткой (data-qa-атрибуты hh),
# но без мегабайта обвязки. Если hh сменит вёрстку, парсер обязан упасть громко —
# это проверяет test_negotiations_broken_page_raises.
_NEGOTIATIONS_HTML = """
<html><body>
<div data-qa="negotiations-item">
  <a data-qa="negotiations-item-title" href="/vacancy/111?from=negotiations">
    Golang-разработчик</a>
  <span data-qa="negotiations-item-company">Т-Банк</span>
  <span data-qa="negotiations-item-state">Отказ</span>
  <span data-qa="negotiations-item-date">28 июля</span>
</div>
<div data-qa="negotiations-item negotiations-item_invitation">
  <a data-qa="negotiations-item-title" href="https://hh.ru/vacancy/222">
    Backend Go Engineer</a>
  <span data-qa="negotiations-item-company"><span>Ozon Tech</span></span>
  <span data-qa="negotiations-item-state">Приглашение</span>
  <span data-qa="negotiations-item-date">29 июля</span>
</div>
<div data-qa="negotiations-item">
  <a data-qa="negotiations-item-title" href="/vacancy/333">Go Developer</a>
  <span data-qa="negotiations-item-company">Acme</span>
  <span data-qa="negotiations-item-state">Резюме не просмотрено</span>
</div>
</body></html>
"""


def test_parse_negotiations_markup():
    from .hhsync import parse_negotiations
    items = parse_negotiations(_NEGOTIATIONS_HTML)
    eq(len(items), 3, "negotiations: три элемента списка")
    eq(items[0]["status"], "rejection", "negotiations: Отказ → rejection")
    eq(items[0]["company"], "Т-Банк", "negotiations: компания первого")
    eq(items[0]["url"], "https://hh.ru/vacancy/111?from=negotiations",
       "negotiations: относительный href разворачивается")
    eq(items[1]["status"], "invitation", "negotiations: Приглашение → invitation")
    eq(items[1]["company"], "Ozon Tech", "negotiations: компания во вложенном span")
    eq(items[2]["status"], "not_viewed",
       "negotiations: «не просмотрено» не должно уехать в viewed")
    # Дата приводится к ISO: «28 июля» без года несравнима ни с чем и после
    # Нового года становится неоднозначной.
    if not (items[0]["date"] or "").endswith("-07-28"):
        FAILS.append(f"negotiations: дата не нормализовалась в ISO: {items[0]['date']!r}")


def test_parse_negotiations_lux():
    """Второй слой разбора: Lux-стейт. Схема ищется по форме (vacancy.name),
    а не по пути — путь сломается при первом же редизайне."""
    import json as _json
    from .hhsync import parse_negotiations
    state = {"applicantNegotiations": {"topicList": [
        {"vacancy": {"name": "Go разработчик", "vacancyId": 42,
                     "company": {"visibleName": "Т-Банк"}},
         "state": {"id": "DISCARD"}, "lastModified": "2026-07-28T10:00:00"},
        {"vacancy": {"name": "Senior Golang", "vacancyId": 43,
                     "company": {"name": "Ozon"}},
         "state": {"id": "RESPONSE"}, "viewedByOpponent": False},
    ]}}
    html = ('<template id="HH-Lux-InitialState">'
            + _json.dumps(state, ensure_ascii=False) + "</template>")
    items = parse_negotiations(html)
    eq(len(items), 2, "lux: два переговора")
    eq(items[0]["status"], "rejection", "lux: DISCARD → rejection")
    eq(items[0]["url"], "https://hh.ru/vacancy/42", "lux: url из vacancyId")
    eq(items[1]["status"], "not_viewed", "lux: response + viewedByOpponent=False")


def test_negotiations_empty_markers_english():
    """Залогиненный hh отдаёт кабинет с <html lang="en">. Пустой список должен
    распознаваться независимо от языка, иначе «откликов нет» = «парсер сломан»."""
    from .hhsync import parse_negotiations
    eq(parse_negotiations("<html><body><h2>You have no responses yet</h2></body></html>"),
       [], "negotiations: английское пустое состояние распозналось")


# Живая вёрстка hh: статус — модификатор в data-qa тега, а не текст поля
# negotiations-item-state (его на странице нет вовсе).
_NEGOTIATIONS_LIVE = """
<html lang="en"><body>
<div data-qa="negotiations-item">
  <a href="/vacancy/777">Golang Developer</a>
  <span data-qa="negotiations-tag negotiations-item-discard">Rejection</span>
  <span>Sorts through 99% of responses</span>
</div>
<div data-qa="negotiations-item">
  <a href="/vacancy/778">Backend Go</a>
  <span data-qa="negotiations-tag negotiations-item-not-viewed">Not viewed</span>
  <span>Sorts through 99% of responses</span>
</div>
</body></html>
"""


def test_hh_status_from_tag():
    from .hhsync import parse_negotiations
    items = parse_negotiations(_NEGOTIATIONS_LIVE)
    eq(len(items), 2, "negotiations: живая вёрстка разобралась")
    eq(items[0]["status"], "rejection", "статус берётся из тега negotiations-item-discard")
    # Без тега сюда уезжало pending по слову «response» из служебной строки.
    eq(items[1]["status"], "not_viewed", "not-viewed из тега, а не из текста карточки")


def test_db_flag_before_subcommand():
    """`--db` до подкоманды обязан долетать до команды.

    argparse копирует объект Action в подпарсер и потом переносит его namespace
    поверх основного — вместе с ЗНАЧЕНИЕМ ПО УМОЛЧАНИЮ. Из-за этого
    `scout --db своя.db status` молча работал с `.scout/scout.db`: команда читала
    и писала не ту базу, о которой её просили."""
    from .cli import build_parser
    from .store import DEFAULT_DB
    p = build_parser()
    eq(p.parse_args(["--db", "/tmp/a.db", "status", "--query", "x"]).db, "/tmp/a.db",
       "--db до подкоманды")
    eq(p.parse_args(["status", "--db", "/tmp/b.db", "--query", "x"]).db, "/tmp/b.db",
       "--db после подкоманды")
    eq(p.parse_args(["status", "--query", "x"]).db, DEFAULT_DB, "--db по умолчанию")
    eq(p.parse_args(["--db", "/tmp/c.db", "collect"]).db, "/tmp/c.db",
       "--db до подкоманды работает не только у status")


def test_enrich_order_freshest_first():
    """Внутри категории свежие идут раньше старых.

    Дата лежала прямо в возрастающем кортеже сортировки, поэтому «свежие»
    означало «самые старые»: лимит выжимок целиком уходил на объявления
    2022–2024 годов, а свежие Go-вакансии с вилкой оставались без описания."""
    from .cli import _by_relevance
    rows = [
        {"title": "Backend Java Engineer", "published_at": "2022-10-17", "url": "u1"},
        {"title": "Golang Engineer", "published_at": "2026-07-30", "url": "u2"},
        {"title": "Backend Engineer", "published_at": "2024-01-31", "url": "u3"},
        {"title": "Менеджер по продажам", "published_at": "2026-07-30", "url": "u4"},
        {"title": "Go разработчик", "published_at": "2026-07-29",
         "salary_from": 300000, "url": "u5"},
    ]
    got = [r["url"] for r in _by_relevance(rows)]
    # u5 — профильная И с вилкой; дальше профильные по убыванию даты; непрофильная в конце.
    eq(got, ["u5", "u2", "u3", "u1", "u4"], "порядок: профильные → с вилкой → свежие")
    dates = [r.get("published_at") for r in _by_relevance(rows) if r["url"] != "u5"][:3]
    eq(dates, sorted(dates, reverse=True), "внутри категории даты убывают")


def test_negotiations_empty_and_broken():
    """Ноль распарсенных при непустой странице — падение, а не «откликов нет»."""
    from .hhsync import parse_negotiations
    empty = "<html><body><h1>У вас пока нет откликов</h1></body></html>"
    eq(parse_negotiations(empty), [], "negotiations: честно пустой список")
    try:
        parse_negotiations("<html><body><div>какая-то другая страница</div></body></html>")
        FAILS.append("negotiations: нераспознанная страница обязана кидать ValueError")
    except ValueError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# mail-sync: классификатор писем
# ──────────────────────────────────────────────────────────────────────────────

def test_classify_mail():
    from .mailsync import classify_mail
    cases = [
        # Реальные формы тем; отправитель из списка найма ловится и без hiring-слов.
        ("no-reply@hh.ru", "Отказ по отклику на вакансию Golang-разработчик", "rejection"),
        ("no-reply@hh.ru", "Приглашение на собеседование", "invitation"),
        ("Acme <no-reply@greenhouse-mail.io>", "Thank you for applying to Acme", "applied"),
        ("recruiting@exness.com",
         "Unfortunately we will not be moving forward", "rejection"),
        ("hr@somestartup.io", "Interview with SomeStartup — please pick a time",
         "invitation"),
        ("talent@unknown-corp.com", "Your application to Unknown Corp", "other"),
        # Не про найм: незнакомый домен и тема без hiring-слов → None, в базу не пишем.
        ("newsletter@shop.example", "Скидка 50% только сегодня", None),
        # «после интервью … отказ» — отказ, а не приглашение: порядок правил.
        ("no-reply@lever.co", "Update after your interview: we decided not to proceed",
         "rejection"),
    ]
    for sender, subject, want in cases:
        eq(classify_mail(sender, subject), want, f"classify_mail({subject[:40]!r})")


def test_classify_mail_body():
    """ТЕЛО письма решает: у зарубежных ATS тема нейтральная, а исход — в первом
    абзаце. Все случаи ниже — реальные письма из аудита, каждое до правки
    классификатора падало в `other`."""
    from .mailsync import classify_mail
    cases = [
        ("noreply@epam.com", "Your application results",
         "Dear candidate, unfortunately, at the moment we are not considering "
         "candidates from your location.", "rejection"),
        ("no-reply@ashbyhq.com", "Thank you for your interest in adjoe",
         "After careful review we decided to proceed with other candidates.",
         "rejection"),
        ("no-reply@lever.co", "An update on your application to Synthesia",
         "We've decided not to move forward with your candidacy at this time.",
         "rejection"),
        ("hrplatform@sberbank.ru", "Пройдите AI-интервью на вакансию «Go-разработчик»",
         "Пройдите короткое первичное интервью с ГигаРекрутером.", "invitation"),
        ("noreply@corp.mail.ru", "Отклик на вакансию Golang Developer",
         "Спасибо за резюме, мы обязательно его рассмотрим.", "applied"),
        ("no-reply@habr.com", "Вы откликнулись на вакансию на Хабр Карьере",
         "Вы откликнулись на вакансию «Go-разработчик» компании VK", "applied"),
        # Незнакомый домен + нейтральная тема: hiring-слова только в теле.
        ("hr@magnit.ru", "Senior Golang разработчик в Magnit Tech",
         "Спасибо за ваш отклик и интерес к нашей вакансии. К сожалению, мы "
         "остановились на другом кандидате.", "rejection"),
    ]
    for sender, subject, body, want in cases:
        eq(classify_mail(sender, subject, body), want,
           f"classify_mail+body({subject[:38]!r})")


def test_classify_mail_false_positives():
    """Ложные срабатывания дороже пропусков — здесь ровно те формулировки,
    на которых классификатор врал в аудите."""
    from .mailsync import classify_mail
    # Условное наклонение внутри ПОДТВЕРЖДЕНИЯ отклика не делает его отказом.
    eq(classify_mail("careers@n26.com", "Your application as Backend Developer",
                     "Thanks for applying! We have received your application. "
                     "If unfortunately you don't make it past the CV review, "
                     "we will let you know."),
       "applied", "N26: «if unfortunately» в подтверждении — не отказ")
    eq(classify_mail("hr@betterme.world", "Thank you for applying",
                     "We have received your CV. If you don't hear back from us, "
                     "it means we've decided to move forward with other candidates."),
       "applied", "BetterMe: условный отказ в хвосте — не отказ")
    # «next steps» в подтверждении — не приглашение.
    eq(classify_mail("no-reply@ashbyhq.com", "Thanks for applying to Sumsub",
                     "Thank you for your application. We will be in touch soon "
                     "on next steps."),
       "applied", "Sumsub: «next steps» не должно давать приглашение")
    # Шум в базу не пишется вовсе.
    for subj in ("Код подтверждения: 123456", "Вакансии по подписке: Golang",
                 "Your security code", "Грант до 75% на ИТ-образование",
                 "Privacy notice update"):
        eq(classify_mail("no-reply@hh.ru", subj), None,
           f"NOISE не должен попадать в базу: {subj[:32]!r}")
    eq(classify_mail("api@hh.ru", "Ваша заявка на регистрацию приложения рассмотрена"),
       None, "api@hh.ru — не про найм")


def test_parse_vacancy_from_body():
    """Настоящие вакансия и компания из тела. Без них ключ строился по шаблонной
    теме и площадке, и 25 разных отказов схлопывались в одну строку."""
    from .mailsync import parse_vacancy
    t, c = parse_vacancy("Работодатель не готов пригласить вас\n"
                         "Вакансия: Golang Developer компании: STARTRIBE LTD.")
    eq((t, c), ("Golang Developer", "STARTRIBE LTD"), "hh: «Вакансия: X компании: Y»")
    t, c = parse_vacancy("Вы откликнулись на вакансию «Go-разработчик» компании «VK»")
    eq((t, c), ("Go-разработчик", "VK"), "habr: «на вакансию X компании Y»")
    eq(parse_vacancy("Просто письмо без структуры"), (None, None),
       "не разобралось — честные None, а не догадка")


def test_company_guess():
    from .mailsync import company_guess
    eq(company_guess('"Ozon Tech" <no-reply@greenhouse-mail.io>', "any"), "Ozon Tech",
       "company_guess: display-имя отправителя")
    got = company_guess("no-reply@hh.ru", "Отказ: отклик в Яндекс на вакансию")
    eq(got, "Яндекс", "company_guess: «в <Компания>» из темы")
    # Тело важнее домена площадки: иначе компанией становится «hh.ru».
    eq(company_guess("no-reply@hh.ru", "Работодатель не готов пригласить вас",
                     "Вакансия: Go Developer компании: STARTRIBE LTD."),
       "STARTRIBE LTD", "company_guess: тело письма важнее домена площадки")


def test_mail_key_does_not_collapse():
    """Два РАЗНЫХ письма с одинаковой шаблонной темой не должны стать одной
    строкой. Живьём так терялось 82 письма из 184."""
    import os
    import tempfile
    from . import store
    from .mailsync import MailItem, record_items
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        items = [
            MailItem(subject="Работодатель не готов пригласить вас",
                     sender="no-reply@hh.ru", date="2026-07-20", kind="rejection",
                     msg_id="<a@hh>"),
            MailItem(subject="Работодатель не готов пригласить вас",
                     sender="no-reply@hh.ru", date="2026-07-21", kind="rejection",
                     msg_id="<b@hh>"),
        ]
        record_items(db, items)
        with store.connect(db) as conn:
            rows = store.negotiations(conn)
        eq(len(rows), 2, "две разных отписки не схлопнулись в одну строку")

    # А вот распознанные вакансия+компания склеиваться ДОЛЖНЫ: одна вакансия,
    # пришедшая и из hh, и из почты, — одна строка.
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        record_items(db, [MailItem(subject="тема раз", sender="no-reply@hh.ru",
                                   date="2026-07-20", kind="applied",
                                   vacancy="Go Developer", company="Acme",
                                   msg_id="<1@x>"),
                          MailItem(subject="тема два", sender="no-reply@hh.ru",
                                   date="2026-07-22", kind="rejection",
                                   vacancy="Go Developer", company="Acme",
                                   msg_id="<2@x>")])
        with store.connect(db) as conn:
            rows = store.negotiations(conn)
        eq(len(rows), 1, "одна вакансия из двух писем — одна строка")
        eq(rows[0]["status"], "rejection", "статус обновился на более поздний")


# ──────────────────────────────────────────────────────────────────────────────
# negotiation-таблица и сверка «уже отработано»
# ──────────────────────────────────────────────────────────────────────────────

def test_negotiation_upsert():
    import os
    import tempfile
    from . import store
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        with store.connect(db) as conn:
            what, old = store.upsert_negotiation(
                conn, title="Go разработчик", company="Acme", status="pending",
                source="hh")
            eq((what, old), ("new", None), "negotiation: первая запись — new")
            what, old = store.upsert_negotiation(
                conn, title="Go разработчик", company="Acme", status="pending",
                source="hh")
            eq(what, "same", "negotiation: без изменений — same")
            what, old = store.upsert_negotiation(
                conn, title="Go Разработчик", company="ACME", status="rejection",
                source="mail")
            eq((what, old), ("changed", "pending"),
               "negotiation: смена статуса ловится сквозь регистр ключа")
            rows = store.negotiations(conn)
            eq(len(rows), 1, "negotiation: одна строка, а не три")
            eq(rows[0]["status"], "rejection", "negotiation: статус обновился")


def test_summary_rows_are_stored_but_not_counted():
    """Служебная сводка источника — не вакансия, и в «новых» ей места нет.

    Живой прогон 30.07.2026 печатал «найдено 6273, новых: 6294»: лишняя двадцать
    одна штука — это ровно число отработавших площадок, чьи строки-сводки
    посчитались как вакансии. Строка отчёта противоречила сама себе.
    """
    import os
    import tempfile
    from . import store
    from .model import SUMMARY_ID
    vs = [Vacancy(source="hh", external_id="1", url="u1", title="Go разработчик"),
          Vacancy(source="hh", external_id="2", url="u2", title="Бэкенд"),
          Vacancy(source="hh", external_id=SUMMARY_ID, url="", title="сводка hh"),
          Vacancy(source="habr", external_id=SUMMARY_ID, url="", title="сводка habr")]
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "s.db")
        with store.connect(db) as conn:
            new, updated = store.upsert(conn, vs)
        eq((new, updated), (2, 0), "сводки посчитались как новые вакансии")
        with store.connect(db) as conn:
            new, updated = store.upsert(conn, vs)
            total = conn.execute("SELECT count(*) FROM vacancy").fetchone()[0]
            saved = conn.execute("SELECT count(*) FROM vacancy WHERE external_id=?",
                                 (SUMMARY_ID,)).fetchone()[0]
        eq((new, updated), (0, 2), "сводки посчитались как обновлённые вакансии")
        # Не считать — не значит выбросить: в сводках лежат счётчики обхода.
        eq(total, 4, "строк в базе стало не четыре — сводку потеряли или задвоили")
        eq(saved, 2, "сводки перестали храниться — счётчики обхода потеряны")


def test_match_processed_conservative():
    from .cli import match_processed
    negs = [{"title": "Golang-разработчик", "company": "Т-Банк",
             "status": "rejection", "source": "hh"},
            {"title": "Ваш отклик в Ozon получен", "company": "Ozon",
             "status": "applied", "source": "mail"}]
    cands = [
        {"title": "Senior Golang разработчик", "company": "Т-Банк", "source": "hh"},
        # Та же компания, но другая профессия — слов названия не пересекает, матча нет.
        {"title": "Аналитик данных", "company": "Т-Банк", "source": "hh"},
        # Телеграм-кандидат: компания из статуса упоминается в теле поста.
        {"title": "Ищем Go-инженера", "company": None, "chat": "jobs",
         "body": "Ищем Go-инженера в команду Т-Банк, офис в Москве"},
        {"title": "Rust Developer", "company": "Совсем Другая", "source": "habr"},
    ]
    got = match_processed(cands, negs)
    matched_titles = {m["candidate"]["title"] for m in got}
    if "Senior Golang разработчик" not in matched_titles:
        FAILS.append("match: не нашёл дубль по компании+словам названия")
    if "Аналитик данных" in matched_titles:
        FAILS.append("match: склеил разные профессии одной компании — слишком агрессивно")
    if "Ищем Go-инженера" not in matched_titles:
        FAILS.append("match: не нашёл компанию из статуса в telegram-посте")
    if "Rust Developer" in matched_titles:
        FAILS.append("match: ложное срабатывание на чужой компании")


# ──────────────────────────────────────────────────────────────────────────────
# scan: сборка отчёта
# ──────────────────────────────────────────────────────────────────────────────

def test_build_scan_report():
    from .cli import build_scan_report
    stages = {
        "collect": {"status": "ok", "found": 120, "new": 7, "updated": 100,
                    "report": [
                        {"source": "hh", "status": "ok", "found": 100, "error": None},
                        {"source": "wantapply", "status": "blocked", "found": 0,
                         "error": "антибот-проверка (just a moment)"},
                        {"source": "habr", "status": "error", "found": 0,
                         "error": "HTTP 500"}]},
        "telegram": {"status": "no_creds", "text": "",
                     "note": "нет telethon/кредов/сессии"},
        "enrich": {"status": "ok", "ok": 2, "blocked": 0, "failed": 1, "delta": 7,
                   "digests": ["── Вакансия раз — Acme", "── Вакансия два — Ozon"],
                   "fails": ["hh:1 УПАЛ: timeout"]},
        "hh": {"status": "ok", "text": "# hh-sync: страниц 2, откликов 31", "found": 31},
    }  # mail отсутствует вовсе — отчёт обязан собраться и сказать «НЕ ЗАПУСКАЛСЯ»
    matches = [{"candidate": {"title": "Go dev", "company": "Acme", "source": "hh"},
                "negotiation": {"title": "Go dev", "company": "Acme",
                                "status": "rejection", "source": "hh"},
                "why": "компания + пересечение слов названия"}]
    rep = build_scan_report(stages, generated_at="2026-07-30T09:00:00+00:00",
                            days=3, matches=matches)
    for needle in ("## Покрытие", "## Кандидаты из Telegram",
                   "## Дельта площадок", "## Статусы откликов",
                   "## Уже отработано", "## Стены и ошибки",
                   "wantapply", "АНТИБОТ", "УПАЛ", "НЕТ КРЕДОВ", "НЕ ЗАПУСКАЛСЯ",
                   "возможный дубль", "Вакансия раз", "hh-sync: страниц 2",
                   "hh:1 УПАЛ"):
        if needle not in rep:
            FAILS.append(f"scan-report: в отчёте нет {needle!r}")
    # Пустой прогон (все этапы отсутствуют) тоже обязан собраться.
    rep2 = build_scan_report({}, generated_at="2026-07-30T09:00:00+00:00", days=3)
    if "## Покрытие" not in rep2 or "## Стены и ошибки" not in rep2:
        FAILS.append("scan-report: отчёт без этапов не собрался")


def test_scan_report_survives_broken_stages():
    """Отчёт обязан собраться при ЛЮБОМ исходе этапов — это его смысл.
    Сюда специально поданы этапы с None вместо текста, отсутствующими ключами
    и упавшим collect: падение сборки отчёта обнуляет весь прогон."""
    from .cli import build_scan_report
    stages = {
        "collect": {"status": "error", "error": "ConnectionError: сеть отвалилась"},
        "telegram": {"status": "error", "text": None, "error": "FloodWait"},
        "enrich": {"status": "error", "error": "sqlite3.OperationalError: locked"},
        "hh": {"status": "no_dep", "note": "нет playwright"},
        "mail": {"status": "no_creds", "note": "нет .auth/gmail.env"},
    }
    rep = build_scan_report(stages, generated_at="2026-07-30T09:00:00+00:00", days=3,
                            matches=[], delta_rows=[])
    for needle in ("## Покрытие", "collect (весь этап)", "УПАЛ", "НЕТ ЗАВИСИМОСТИ",
                   "НЕТ КРЕДОВ", "## Стены и ошибки", "Дельта пуста"):
        if needle not in rep:
            FAILS.append(f"scan-report(сломанные этапы): нет {needle!r}")


def test_scan_report_has_full_delta_table():
    """Главное требование к отчёту: по нему можно писать карточки, не открывая
    ничего ещё. Значит ВСЯ дельта должна быть в таблице, а не только 20 дайджестов."""
    from .cli import build_scan_report
    rows = [{"title": f"Go Developer {i}", "company": f"Acme {i}",
             "salary_from": 300000 if i else None, "salary_to": None,
             "currency": "RUB", "salary_gross": None, "location": "Москва",
             "remote": 1, "source": "hh", "url": f"https://hh.ru/vacancy/{i}"}
            for i in range(30)]
    rep = build_scan_report({"enrich": {"status": "ok", "digests": ["── дайджест"],
                                        "delta": 30, "ok": 1}},
                            generated_at="2026-07-30T09:00:00+00:00", days=3,
                            delta_rows=rows)
    if "Всего в дельте: 30" not in rep:
        FAILS.append("scan-report: нет счётчика всей дельты")
    for needle in ("Go Developer 29", "https://hh.ru/vacancy/29", "300 000 RUB",
                   "| роль | компания | деньги |"):
        if needle not in rep:
            FAILS.append(f"scan-report: в таблице дельты нет {needle!r}")


def test_match_processed_groups_by_candidate():
    """Одна строка на кандидата, а не декартово произведение; `other` в сверке
    не участвует (среди них рекламные рассылки)."""
    from .cli import match_processed
    negs = [{"title": "Golang-разработчик", "company": "Т-Банк",
             "status": "rejection", "source": "hh"},
            {"title": "Senior Golang разработчик", "company": "Т-Банк",
             "status": "viewed", "source": "hh"},
            {"title": "Грант до 75% на ИТ-образование", "company": "Т-Банк",
             "status": "other", "source": "mail"}]
    got = match_processed([{"title": "Senior Golang разработчик", "company": "Т-Банк",
                            "source": "hh", "url": "https://hh.ru/vacancy/1"}], negs)
    eq(len(got), 1, "match: один кандидат — одна строка")
    eq(len(got[0]["hits"]), 2, "match: рекламный `other` в сверку не попал")
    eq(got[0]["hits"][0]["negotiation"]["status"], "rejection",
       "match: отказ показывается первым")


def test_match_processed_short_company_no_false_positive():
    """«Mira» ↔ «Miratech» — совпадение по четырём буквам, порог поднят до 5."""
    from .cli import match_processed
    negs = [{"title": "Your application was received!", "company": "Mira",
             "status": "applied", "source": "mail"}]
    got = match_processed([{"title": "Senior AI Deployment Engineer",
                            "company": "Miratech", "source": "careered"}], negs)
    eq(got, [], "match: короткое имя компании не даёт ложного дубля")


# ──────────────────────────────────────────────────────────────────────────────
# cookieimport: мерж профиля и фильтр доменов
# ──────────────────────────────────────────────────────────────────────────────

def test_cookie_domain_filter_rejects_wildcard():
    from .cookieimport import ALLOWED_DOMAINS, parse_domains
    # Дефолт — встроенный allowlist.
    eq(parse_domains(None), ALLOWED_DOMAINS, "domains: пусто → дефолтный allowlist")
    eq(parse_domains(["HH.RU", ".getmatch.ru"]), ("hh.ru", "getmatch.ru"),
       "domains: нормализуются регистр и ведущая точка")
    for bad in (["*"], ["hh.ru", "*"], ["all"]):
        try:
            parse_domains(bad)
            FAILS.append(f"domains: звёздочка {bad!r} должна отклоняться")
        except ValueError:
            pass


def test_cookie_domain_allowed():
    from .cookieimport import domain_allowed
    doms = ("hh.ru", "getmatch.ru")
    if not domain_allowed(".hh.ru", doms):
        FAILS.append("domain_allowed: ведущая точка не распозналась")
    if not domain_allowed("spb.hh.ru", doms):
        FAILS.append("domain_allowed: поддомен площадки не распознан")
    if domain_allowed("yandex.ru", doms):
        FAILS.append("domain_allowed: чужой домен просочился")
    if domain_allowed("evilhh.ru", doms):
        FAILS.append("domain_allowed: hh.ru как суффикс без точки — ложный матч")


def test_cookie_merge():
    from .cookieimport import merge_cookies
    base = [{"domain": ".hh.ru", "path": "/", "name": "sess", "value": "OLD"},
            {"domain": "getmatch.ru", "path": "/", "name": "gm", "value": "keep"}]
    incoming = [{"domain": "hh.ru", "path": "/", "name": "sess", "value": "NEW"},
                {"domain": "career.habr.com", "path": "/", "name": "hbr", "value": "add"}]
    merged = merge_cookies(base, incoming)
    by = {(c["domain"].lstrip("."), c["name"]): c["value"] for c in merged}
    eq(by[("hh.ru", "sess")], "NEW", "merge: свежая кука вытеснила старую (ведущая точка неважна)")
    eq(by[("getmatch.ru", "gm")], "keep", "merge: не тронутая кука осталась")
    eq(by[("career.habr.com", "hbr")], "add", "merge: новая кука добавилась")
    eq(len(merged), 3, "merge: sess не задвоился")


def test_cookie_expires_and_samesite():
    from .cookieimport import _chromium_expires, _to_cookie
    eq(_chromium_expires(0), -1, "expires: 0 → сессионная (-1)")
    # 13350000000000000 мкс с 1601 ≈ 2023 — должно дать положительный unix.
    if _chromium_expires(13_350_000_000_000_000) <= 0:
        FAILS.append("expires: реальная дата не сконвертировалась в unix")
    # sameSite=None без Secure Playwright не примет — понижаем до Lax.
    row = (".hh.ru", "n", b"", "/", 0, 0, 0, 0)  # samesite=0(None), is_secure=0
    c = _to_cookie(row, key=b"\x00" * 16)
    eq(c["sameSite"], "Lax", "samesite: None без Secure понижается до Lax")


def test_mail_dump_classification():
    from .mailsync import items_from_dump
    dump = [
        {"sender": "no-reply@hh.ru", "subject": "Отказ по вакансии Go",
         "date": "2026-07-29", "snippet": "к сожалению"},
        {"sender": "hr@acme.io", "subject": "Re: ваш отклик",
         "snippet": "приглашаем на собеседование во вторник"},
        {"sender": "news@shop.example", "subject": "Скидки", "snippet": "-50%"},
    ]
    items = items_from_dump(dump)
    eq(len(items), 2, "mail-ingest: рекламное письмо отсеяно")
    kinds = {it.kind for it in items}
    if "rejection" not in kinds:
        FAILS.append("mail-ingest: отказ не распознан")
    if "invitation" not in kinds:
        FAILS.append("mail-ingest: приглашение из сниппета не распознано")


# ──────────────────────────────────────────────────────────────────────────────
# mailsync поверх imap-tools: разбор письма, а не сеть
# ──────────────────────────────────────────────────────────────────────────────

def _raw_mail(*, headers: str, body: bytes) -> bytes:
    return headers.replace("\n", "\r\n").encode("ascii") + b"\r\n" + body + b"\r\n"


def _mail_message(raw: bytes):
    """Письмо → imap_tools.MailMessage. Пропускается, если пакета нет: ядро
    сборщика stdlib-only, imap-tools — опциональный путь."""
    try:
        from imap_tools.message import MailMessage
    except ImportError:
        return None
    return MailMessage.from_bytes(raw)


def test_mail_body_decodes_legacy_charsets():
    """koi8-r + base64 и cp1251 + quoted-printable — реальные письма Авито и Хабра.

    Ручной разбор кодировок жил в mailsync.body_text и ломался на каждой новой;
    теперь его делает imap-tools. Тест сторожит именно ту границу, ради которой
    библиотека взята: тело должно приезжать читаемым текстом, а не мусором."""
    import base64
    import quopri

    koi = _mail_message(_raw_mail(
        headers=("From: =?koi8-r?B?4dfJ1M8=?= <no-reply@avito.ru>\n"
                 "Subject: =?koi8-r?B?7yDXwdvFzSDP1MvMycvF?=\n"
                 "Date: Tue, 29 Jul 2026 10:00:00 +0300\n"
                 "Message-ID: <a1@avito.ru>\n"
                 "Content-Type: text/plain; charset=koi8-r\n"
                 "Content-Transfer-Encoding: base64\n"),
        body=base64.b64encode("К сожалению, мы остановились на другом кандидате"
                              .encode("koi8-r"))))
    win = _mail_message(_raw_mail(
        headers=("From: Habr Career <no-reply@career.habr.com>\n"
                 "Subject: =?windows-1251?Q?=CF=F0=E8=E3=EB=E0=F8=E5=ED=E8=E5?=\n"
                 "Date: Wed, 30 Jul 2026 09:00:00 +0300\n"
                 "Content-Type: text/plain; charset=windows-1251\n"
                 "Content-Transfer-Encoding: quoted-printable\n"),
        body=quopri.encodestring("Приглашаем вас на собеседование".encode("cp1251"))))
    if koi is None or win is None:
        return  # imap-tools не установлен — путь опциональный, молча пропускаем
    from .mailsync import _message_item, body_text, date_of, sender_of

    if "остановились на другом кандидате" not in body_text(koi):
        FAILS.append(f"body_text: koi8-r + base64 не раскодировались: {body_text(koi)!r}")
    if "Приглашаем" not in body_text(win):
        FAILS.append(f"body_text: cp1251 + quoted-printable не раскодировались: "
                     f"{body_text(win)!r}")
    # Отправитель нужен ЦЕЛИКОМ («Имя <адрес>»): по домену работает отбор,
    # по имени — company_guess. Голый адрес ломает второе.
    if sender_of(koi) != "Авито <no-reply@avito.ru>":
        FAILS.append(f"sender_of: потеряно имя отправителя: {sender_of(koi)!r}")
    eq(date_of(koi), "2026-07-29T07:00:00+00:00", "date_of: дата приведена к UTC")

    item = _message_item(koi, own_address="me@gmail.com")
    eq(item.kind, "rejection", "письмо в koi8-r классифицировано как отказ")
    eq(item.msg_id, "<a1@avito.ru>", "Message-ID сохранён — ключ строки от него страхуется")
    eq(_message_item(win, own_address="me@gmail.com").kind, "invitation",
       "письмо в cp1251 классифицировано как приглашение")


def test_mail_body_falls_back_to_html():
    """Нет text/plain — берём text/html и чистим. Правило наше, а не библиотеки."""
    msg = _mail_message(_raw_mail(
        headers=("From: HR <hr@acme.io>\nSubject: =?utf-8?B?0JLQsNGIINC+0YLQutC70LjQug==?=\n"
                 "Content-Type: text/html; charset=utf-8\n"),
        body="<html><body><p>Приглашаем вас на интервью</p>"
             "<script>var x=1</script></body></html>".encode()))
    if msg is None:
        return
    from .mailsync import body_text
    got = body_text(msg)
    if "Приглашаем вас на интервью" not in got:
        FAILS.append(f"body_text: HTML-фолбэк потерял текст: {got!r}")
    if "var x" in got:
        FAILS.append(f"body_text: скрипт из HTML утёк в тело: {got!r}")


def test_mail_own_letters_skipped():
    """Ящик открыт как All Mail — свои отправленные письма туда попадают.
    Классифицировать собственное сопроводительное как входящий статус нельзя."""
    msg = _mail_message(_raw_mail(
        headers=("From: Me <me@gmail.com>\nSubject: Application for Go role\n"
                 "Content-Type: text/plain; charset=utf-8\n"),
        body="Добрый день, отправляю резюме".encode()))
    if msg is None:
        return
    from .mailsync import _message_item, is_candidate, sender_of
    if _message_item(msg, own_address="me@gmail.com") is not None:
        FAILS.append("_message_item: своё отправленное письмо не отсеяно")
    if is_candidate(sender_of(msg), "Отклик на вакансию Go", own_address="me@gmail.com"):
        FAILS.append("is_candidate: своё письмо попало в кандидаты на выкачку тела")


# ──────────────────────────────────────────────────────────────────────────────
# Новые анонимные источники: разбор ФИКСТУР ответов, без сети
# ──────────────────────────────────────────────────────────────────────────────

class _FakeJSON:
    """Подменяет sources.fetch_json и запоминает, какие URL спросили.

    Смысл теста именно в этом: у трёх источников три разных способа
    пагинации и ровно один настоящий серверный фильтр — их и надо стеречь."""

    def __init__(self, routes: dict, default=None):
        self.routes, self.default, self.asked = routes, default, []

    def __call__(self, url, **kw):
        self.asked.append(url)
        for frag, payload in self.routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        if self.default is None:
            raise AssertionError(f"фикстуры под {url} нет")
        return self.default


def _with_fake_json(fake, fn):
    from . import sources as S
    real = S.fetch_json
    S.fetch_json = fake
    try:
        return fn()
    finally:
        S.fetch_json = real


def test_himalayas_parses_fixture():
    """Himalayas: id из хвоста guid, вилка в поля ВМЕСТЕ с периодом.

    Замер по 100 живым вакансиям: annual 89, hourly 8, monthly 3. Пока периода
    в модели не было, почасовые приходилось выбрасывать: «80–120 USD» рядом
    с годовыми «168 000–333 500 USD» читается как одна и та же зарплата. Теперь
    период хранится и печатается, поэтому почасовые возвращены в вилку с пометкой
    `hour` — потери данных больше нет."""
    from .sources import Ctx, src_himalayas

    page = {"totalCount": 3, "limit": 20, "offset": 0, "jobs": [
        {"title": "Senior Backend Engineer (Go)", "companyName": "NVIDIA",
         "guid": "https://himalayas.app/companies/nvidia/jobs/senior-backend-engineer-3394809120",
         "minSalary": 168000, "maxSalary": 333500, "currency": "USD",
         "salaryPeriod": "annual", "seniority": ["Senior"],
         "locationRestrictions": ["Canada", "United States"],
         "categories": ["Go", "Backend"], "pubDate": 1785389697,
         "excerpt": "Мы ищем инженера на Go", "employmentType": "Full Time"},
        {"title": "Platform Engineer", "companyName": "Acme",
         "guid": "https://himalayas.app/companies/acme/jobs/platform-engineer",
         "minSalary": 80, "maxSalary": 120, "currency": "USD",
         "salaryPeriod": "hourly", "seniority": [], "locationRestrictions": [],
         "pubDate": 1785389000, "excerpt": ""},
        {"title": "Registered Nurse", "companyName": "Clinic",
         "guid": "https://himalayas.app/companies/clinic/jobs/registered-nurse",
         "minSalary": None, "maxSalary": None, "salaryPeriod": "annual",
         "pubDate": 1785380000, "excerpt": ""},
    ]}
    fake = _FakeJSON({"himalayas.app/jobs/api": page})
    got = _with_fake_json(fake, lambda: src_himalayas(Ctx(limit=20)))

    jobs = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"]
    eq(len(jobs), 2, "медсестра отсеяна по названию роли, две IT-роли остались")
    eq(len(summary), 1, "служебная сводка «отдано / под профиль» на месте")
    if summary and summary[0].url:
        FAILS.append("сводка с непустым url попадёт в выдачу как вакансия")
    # Счёт единый для всех источников (sources.Tally): отдано → разобрано → записано,
    # и расхождение обязано быть нулём — иначе строка потерялась между разбором и выдачей.
    eq(summary[0].raw["offered"], 3, "в сводке — сколько отдал API, а не сколько оставили")
    eq(summary[0].raw["kept"], 2, "записано — только то, что прошло фильтр профессии")
    eq(summary[0].raw["skipped_profile"], 1, "медсестра отсеяна и посчитана, а не забыта")
    eq(summary[0].raw["mismatch"], 0, "баланс сошёлся: ни одна строка не потерялась")

    go, hourly = jobs[0], jobs[1]
    eq(go.external_id, "senior-backend-engineer-3394809120", "id взят из хвоста guid")
    eq(go.url, "https://himalayas.app/companies/nvidia/jobs/senior-backend-engineer-3394809120",
       "url — постоянная ссылка вакансии")
    eq((go.salary_from, go.salary_to, go.currency), (168000, 333500, "USD"),
       "годовая вилка перенесена в поля")
    eq(go.salary_period, "year", "annual → year, а не молчание")
    eq(go.salary_str(), "168 000–333 500 USD/год",
       "годовая вилка печатается с периодом")
    eq(go.remote, True, "площадка целиком про удалёнку")
    eq(go.location, "Canada, United States", "locationRestrictions — откуда можно работать")
    if not (go.published_at or "").startswith("20"):
        FAILS.append(f"pubDate (unix) не привёлся к ISO: {go.published_at!r}")

    eq((hourly.salary_from, hourly.salary_to), (80, 120),
       "почасовая ставка больше не выбрасывается — период есть, где её подписать")
    eq(hourly.salary_period, "hour", "hourly → hour")
    eq(hourly.salary_str(), "80–120 USD/час",
       "почасовая видна почасовой, а не «зарплатой 80–120»")


def test_himalayas_empty_answer_is_a_failure():
    """API ответил, но вакансий ноль — это сломанный парсер, а не «ничего нет».
    Молча вернуть пустой список здесь значит потерять источник целиком."""
    from .net import FetchError
    from .sources import Ctx, src_himalayas

    fake = _FakeJSON({"himalayas.app": {"totalCount": 0, "jobs": []}})
    try:
        _with_fake_json(fake, lambda: src_himalayas(Ctx(limit=20)))
        FAILS.append("himalayas: пустая выдача не уронила источник")
    except FetchError:
        pass


def test_arbeitnow_follows_cursor_pagination():
    """Arbeitnow: следующая страница берётся из links.next, а не считается руками."""
    from .sources import Ctx, src_arbeitnow

    p1 = {"data": [
        {"slug": "go-dev-1", "company_name": "TBS GmbH", "title": "Backend Developer Go",
         "remote": True, "url": "https://www.arbeitnow.com/jobs/go-dev-1",
         "tags": ["Software Development"], "job_types": ["berufserfahren"],
         "location": "Berlin", "created_at": 1785394835,
         "description": "<p>Wir suchen einen <b>Go</b>-Entwickler</p>"},
        {"slug": "kraftfahrer-2", "company_name": "Spedition", "title": "Kraftfahrer (m/w/d)",
         "remote": False, "url": "https://www.arbeitnow.com/jobs/kraftfahrer-2",
         "tags": [], "job_types": [], "location": "Böblingen",
         "created_at": 1785394000, "description": "LKW"},
    ], "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"}, "meta": {}}
    p2 = {"data": [
        {"slug": "sre-3", "company_name": "Zalando", "title": "Senior Backend Engineer (SRE)",
         "remote": True, "url": "https://www.arbeitnow.com/jobs/sre-3", "tags": ["SRE"],
         "job_types": [], "location": "Remote", "created_at": 1785390000,
         "description": "Kubernetes"},
    ], "links": {"next": None}, "meta": {}}
    fake = _FakeJSON({"page=2": p2, "job-board-api": p1})
    got = _with_fake_json(fake, lambda: src_arbeitnow(Ctx(limit=400)))

    jobs = [v for v in got if v.external_id != "_summary"]
    eq([v.external_id for v in jobs], ["go-dev-1", "sre-3"],
       "вторая страница пройдена, водитель отсеян по названию роли")
    if not any("page=2" in u for u in fake.asked):
        FAILS.append(f"links.next не пройден, спрошено: {fake.asked}")
    eq(jobs[0].remote, True, "флаг remote взят у площадки")
    eq(jobs[0].salary_from, None, "вилок Arbeitnow не отдаёт — честный ноль")
    if "<b>" in (jobs[0].description or ""):
        FAILS.append(f"разметка утекла в описание: {jobs[0].description!r}")


def test_jobicy_filters_on_the_server():
    """Jobicy — единственный из трёх с настоящим серверным фильтром.

    Проверяется троякое: тег уходит в запрос (значит ATS_ROLE_RE не нужен),
    формулировка короче трёх символов пропускается (`tag=go` отдаёт HTTP 400),
    а 400 по незнакомому тегу не роняет прогон."""
    from .net import FetchError
    from .sources import Ctx, src_jobicy

    payload = {"jobCount": 1, "jobs": [
        {"id": 144845, "url": "https://jobicy.com/jobs/144845-golang-engineer",
         "jobTitle": "Golang Engineer", "companyName": "Canonical Ltd.",
         "jobGeo": "Anywhere", "jobLevel": "Senior", "jobType": ["Full-Time"],
         "jobIndustry": ["Engineering"], "pubDate": "2026-07-21T16:19:49+00:00",
         "salaryMin": 191360, "salaryMax": 287040, "salaryCurrency": "USD",
         "salaryPeriod": "yearly", "jobExcerpt": "Работа с Go и Kubernetes"},
    ]}
    fake = _FakeJSON({"tag=golang": payload,
                      "tag=backend-go": FetchError("jobicy", "HTTP 400", status=400)})
    ctx = Ctx(query="Golang", extra_queries=("Go", "Backend Go"))
    got = _with_fake_json(fake, lambda: src_jobicy(ctx))

    # Сводка есть и здесь: серверный фильтр не отменяет вопроса «сколько площадка
    # отдала и сколько мы унесли» — на нём и ловится молча пропущенный тег.
    jobs = [x for x in got if x.external_id != "_summary"]
    summary = [x for x in got if x.external_id == "_summary"]
    eq(len(jobs), 1, "одна вакансия: фильтр серверный, отсеивать по названию нечего")
    eq(len(summary), 1, "служебная сводка на месте")
    eq(summary[0].raw["skipped_profile"], 0, "у jobicy фильтр профессии не применяется")
    eq(summary[0].raw["mismatch"], 0, "баланс сошёлся")
    if not any("короче" in n for n in summary[0].raw["notes"]):
        FAILS.append("пропущенная короткая формулировка не названа в сводке — "
                     "«Go» просто исчез из обхода")
    if not any("tag=golang" in u for u in fake.asked):
        FAILS.append(f"тег не ушёл в запрос: {fake.asked}")
    if any("tag=go&" in u or u.endswith("tag=go") for u in fake.asked):
        FAILS.append(f"короткий тег `go` отправлен — API отдаёт на него 400: {fake.asked}")
    if not any("tag=backend-go" in u for u in fake.asked):
        FAILS.append("многословный запрос не превратился в тег через дефис")

    v = jobs[0]
    eq(v.external_id, "144845", "id вакансии из поля id")
    eq(v.url, "https://jobicy.com/jobs/144845-golang-engineer",
       "url исходный — этого требует ToS площадки")
    eq((v.salary_from, v.salary_to, v.currency), (191360, 287040, "USD"), "годовая вилка")
    eq(v.salary_period, "year", "jobicy: yearly → year")
    eq(v.salary_str(), "191 360–287 040 USD/год", "период виден в строке денег")
    eq(v.remote, True, "площадка только про удалёнку")
    if "Senior" not in v.tags:
        FAILS.append(f"грейд не попал в теги: {v.tags}")


# ──────────────────────────────────────────────────────────────────────────────
# Период вилки: хранение, разбор у каждого источника, печать
# ──────────────────────────────────────────────────────────────────────────────

def test_period_normalization():
    """Каждая площадка называет период по-своему; свести их надо к трём значениям.

    Всё, для чего честной подписи нет (неделя, смена, пусто), обязано давать None:
    «месяц по умолчанию» — это и есть та ложь, из-за которой почасовые 19–23 USD
    стояли в одной колонке с годовыми 168 000–333 500 USD."""
    from .model import norm_period
    cases = [("annual", "year"), ("yearly", "year"), ("year", "year"),
             ("per-year-salary", "year"), ("Annual Salary", "year"),
             ("monthly", "month"), ("month", "month"), ("MONTH", "month"),
             ("hourly", "hour"), ("hour", "hour"), ("per hour", "hour"),
             ("weekly", None), ("daily", None), ("SHIFT", None),
             ("", None), (None, None)]
    for raw, want in cases:
        eq(norm_period(raw), want, f"norm_period({raw!r})")


def test_salary_str_shows_period():
    """Три периода печатаются подписью, неизвестный — БЕЗ подписи.

    Живьём в одной выдаче стояли «2 500–7 000 USD» (месяц), «168 000–333 500 USD»
    (год) и «19–23 USD» (час) — расхождение до 12 раз, и ни одного признака,
    по которому читающий мог бы их различить."""
    eq(salary_str(2500, 7000, "USD", period="monthly"), "2 500–7 000 USD/мес",
       "месячная вилка")
    eq(salary_str(168000, 333500, "USD", period="annual"), "168 000–333 500 USD/год",
       "годовая вилка")
    eq(salary_str(19, 23, "USD", period="hourly"), "19–23 USD/час", "почасовая ставка")
    eq(salary_str(60000, 90000, "RUR"), "60 000–90 000 RUB",
       "период неизвестен → без суффикса, месяц НЕ подставляется")
    eq(salary_str(60000, 90000, "RUR", period="weekly"), "60 000–90 000 RUB",
       "неподдержанный период не превращается в выдуманный")
    eq(salary_str(150000, 250000, "RUR", False, "month"), "150 000–250 000 RUB/мес net",
       "период и gross/net уживаются в одной строке")
    eq(salary_str(None, None, "USD", period="hourly"), "",
       "нет вилки — нет и строки, период сам по себе не деньги")


def test_db_migration_adds_period_to_old_base():
    """Старая база (колонки нет) обязана открыться, доехать до схемы и работать.

    `CREATE TABLE IF NOT EXISTS` существующую таблицу не трогает, поэтому без
    отдельного ALTER первый же INSERT в живую базу с полутора тысячами вакансий
    падал бы на «no such column: salary_period»."""
    import os
    import sqlite3
    import tempfile
    from . import store
    from .model import Vacancy

    legacy = """
    CREATE TABLE vacancy (
        source TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT NOT NULL,
        title TEXT NOT NULL, company TEXT, salary_from INTEGER, salary_to INTEGER,
        currency TEXT, salary_gross INTEGER, location TEXT, remote INTEGER,
        published_at TEXT, updated_at TEXT, employer_url TEXT, tags TEXT,
        description TEXT, raw TEXT, dup_key TEXT, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, PRIMARY KEY (source, external_id));
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "old.db")
        old = sqlite3.connect(db)
        old.executescript(legacy)
        old.execute("INSERT INTO vacancy (source, external_id, url, title, salary_from, "
                    "salary_to, currency, first_seen, last_seen) "
                    "VALUES ('hh','1','https://hh.ru/vacancy/1','Go dev',200000,300000,"
                    "'RUB','2026-07-01','2026-07-01')")
        old.commit()
        old.close()

        with store.connect(db) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(vacancy)")}
            row = conn.execute("SELECT * FROM vacancy WHERE external_id='1'").fetchone()
            store.upsert(conn, [Vacancy(source="careered", external_id="2", url="u2",
                                        title="Support", salary_from=19, salary_to=23,
                                        currency="USD", salary_period="hour")])
            fresh = conn.execute("SELECT salary_period FROM vacancy "
                                 "WHERE external_id='2'").fetchone()
            rows = store.query(conn)
        eq("salary_period" in cols, True, "миграция добавила колонку в старую базу")
        eq(row["salary_period"], None,
           "у старых строк период НЕ придуман задним числом — он и правда неизвестен")
        eq(row["salary_from"], 200000, "старые данные на месте, база не пересоздана")
        eq(fresh["salary_period"], "hour", "новая запись сохраняет период")
        eq(len(rows), 2, "query читает обе строки после миграции")

        # Повторное открытие не должно пытаться добавить колонку второй раз.
        with store.connect(db) as conn:
            eq(store.migrate(conn), [], "миграция идемпотентна")


class _FakeFetch:
    """Подменяет sources.fetch фикстурами страниц и СЧИТАЕТ спрошенные URL.

    Считать обязательно: с пагинацией «источник отработал» и «источник обошёл
    выдачу целиком» — разные вещи, и отличает их только список запросов.
    Фрагменты проверяются в порядке объявления, поэтому «page=2» надо класть
    ВЫШЕ общего фрагмента.
    """

    def __init__(self, pages: dict):
        self.pages, self.asked = pages, []

    def __call__(self, url, **kw):
        self.asked.append(url)
        for frag, payload in self.pages.items():
            if frag in url:
                return payload, url
        raise AssertionError(f"фикстуры под {url} нет")


def _with_fake_fetch(pages: dict, fn, *, keep_pause: bool = False):
    """Подменяет sources.fetch фикстурами страниц (hh и habr читают HTML).

    Заодно глушится пауза между страницами: тест не должен спать по-настоящему,
    но ВЫЗОВЫ паузы считаются — вежливость к площадке проверяется отдельным тестом.
    """
    from . import sources as S
    real_fetch, real_pause = S.fetch, S._pause
    fake = pages if isinstance(pages, _FakeFetch) else _FakeFetch(pages)
    naps: list[float] = []
    S.fetch = fake
    if not keep_pause:
        S._pause = lambda seconds=S.PAGE_PAUSE: naps.append(seconds)
    try:
        result = fn()
    finally:
        S.fetch, S._pause = real_fetch, real_pause
    fake.naps = naps
    return result


def _fresh(hours: int = 1) -> str:
    """Момент внутри окна свежести. Фиксированная дата в фикстуре — мина: тест
    зеленеет сегодня и краснеет через неделю сам по себе."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _stale(days: int = 30) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_hh_period_is_month_even_for_shift_rates():
    """hh кладёт в from/to месячную сумму всегда — значит период у нашей вилки месяц.

    Проверено живьём: у охранника с mode=SHIFT приезжает from 112 500 при
    perModeFrom 7 500 (15 смен), а карточка показывает «7 500 – 8 000 ₽ за смену».
    Взять mode как период нашей вилки значило бы соврать в 15 раз."""
    import json as J
    from .sources import Ctx, src_hh

    state = {"vacancySearchResult": {"vacancies": [
        {"vacancyId": 111, "name": "Go разработчик", "company": {"visibleName": "Acme"},
         "compensation": {"from": 250000, "to": 400000, "currencyCode": "RUR",
                          "gross": False, "mode": "MONTH"},
         "area": {"name": "Москва"}, "links": {"desktop": "https://hh.ru/vacancy/111"},
         "publicationTime": {"$": "2026-07-29T10:00:00+03:00"}},
        {"vacancyId": 222, "name": "Охранник", "company": {"visibleName": "ЧОП"},
         "compensation": {"from": 112500, "to": 120000, "currencyCode": "RUR",
                          "gross": False, "perModeFrom": 7500, "perModeTo": 8000,
                          "mode": "SHIFT"},
         "area": {"name": "Москва"}, "links": {"desktop": "https://hh.ru/vacancy/222"},
         "publicationTime": {"$": "2026-07-29T10:00:00+03:00"}},
        {"vacancyId": 333, "name": "Go без вилки", "company": {"visibleName": "Acme"},
         "compensation": {"noCompensation": {}},
         "area": {"name": "Москва"}, "links": {"desktop": "https://hh.ru/vacancy/333"},
         "publicationTime": {"$": "2026-07-29T10:00:00+03:00"}},
    ]}}
    page = ('<html><template id="HH-Lux-InitialState">'
            + J.dumps(state, ensure_ascii=False) + "</template></html>")
    got = _with_fake_fetch({"hh.ru/search/vacancy": page},
                           lambda: src_hh(Ctx(query="Golang")))

    by_id = {v.external_id: v for v in got}
    eq(by_id["111"].salary_period, "month", "mode=MONTH → месяц")
    eq(by_id["111"].salary_str(), "250 000–400 000 RUB/мес net", "период виден в строке")
    eq(by_id["222"].salary_period, "month",
       "mode=SHIFT: в from/to всё равно месячная сумма, период — месяц")
    eq(by_id["222"].raw.get("perMode"), [7500, 8000],
       "посменная ставка сохранена в raw — вывод про месяц можно перепроверить")
    eq(by_id["333"].salary_period, None, "нет вилки — нет и периода")


def test_habr_period_only_when_named():
    """У habr период живёт (если живёт) прямо в строке вилки — и только оттуда берётся."""
    from .sources import Ctx, src_habr

    def card(vid, salary):
        return (f'<div class="vacancy-card ">'
                f'<a href="/vacancies/{vid}" class="vacancy-card__title-link">Go dev</a>'
                f'<div class="vacancy-card__company"><a href="/c/x">Acme</a></div>'
                f'<div class="basic-salary basic-salary--list">{salary}</div>'
                f'<div class="chip-with-icon__text">Senior</div>'
                f'<time class="basic-date" datetime="2026-07-29T10:00:00+03:00">29 июля</time>'
                f'</div>')

    page = "<html>" + card(1, "от 300 000 до 490 000 ₽") \
           + card(2, "от 2 000 до 3 000 ₽ в час") + "</html>"
    got = _with_fake_fetch({"career.habr.com/vacancies": page},
                           lambda: src_habr(Ctx(query="Golang")))
    by_id = {v.external_id: v for v in got}
    eq(by_id["1"].salary_period, None,
       "площадка периода не назвала — не выдумываем месяц")
    eq(by_id["1"].salary_str(), "300 000–490 000 RUB", "вилка без суффикса")
    eq(by_id["2"].salary_period, "hour", "«в час» в тексте вилки прочитан")
    eq(by_id["2"].salary_str(), "2 000–3 000 RUB/час", "почасовая подписана")


def _careered_entry(jid, title, lo, hi, period, posted=None):
    return {"kind": "job", "id": jid, "posted_at": posted or _fresh(),
            "features": [{"key": "name", "value": title},
                         {"key": "company", "value": "Acme"},
                         {"key": "salary_from", "value": lo},
                         {"key": "salary_to", "value": hi},
                         {"key": "salary_currency", "value": "USD"},
                         {"key": "salary_period", "value": period},
                         {"key": "location", "value": "Remote"}]}


def test_careered_takes_period_from_its_own_field():
    """careered отдаёт вилку разложенной по полям, период в их числе.

    Именно здесь жили «19–23 USD» и «60–60 USD» — почасовые ставки, неотличимые
    в отчёте от месячных зарплат."""
    from .sources import Ctx, src_careered

    payload = {"total": 3, "entries": [
        _careered_entry("a", "Systems Support Specialist I", "19", "23", "hour"),
        _careered_entry("b", "Full Stack Engineer Logistics SaaS", "2500", "7000", "month"),
        _careered_entry("c", "Senior Backend Engineer (Go)", "168000", "333500", "year"),
    ]}
    fake = _FakeJSON({"careered.io/api/jobs": payload})
    got = _with_fake_json(fake, lambda: src_careered(Ctx(limit=20)))
    by_id = {v.external_id: v for v in got}
    eq(by_id["a"].salary_str(), "19–23 USD/час", "почасовая подписана часом")
    eq(by_id["b"].salary_str(), "2 500–7 000 USD/мес", "месячная подписана месяцем")
    eq(by_id["c"].salary_str(), "168 000–333 500 USD/год", "годовая подписана годом")


def test_new_and_report_print_period():
    """Обе таблицы — `new` и отчёт `scan` — печатают вилку одним и тем же способом.

    Раньше расхождение в 12 раз между почасовой и месячной вилкой было невидимо
    в обеих: колонка «деньги» показывала голое число с валютой."""
    from .cli import _delta_table, _money
    rows = [
        {"title": "Support", "company": "—", "source": "careered", "url": "u1",
         "salary_from": 19, "salary_to": 23, "currency": "USD", "salary_period": "hour"},
        {"title": "Network Engineer", "company": "NVIDIA", "source": "himalayas", "url": "u2",
         "salary_from": 168000, "salary_to": 333500, "currency": "USD",
         "salary_period": "year"},
        {"title": "Go dev", "company": "Acme", "source": "habr", "url": "u3",
         "salary_from": 300000, "salary_to": 490000, "currency": "RUB",
         "salary_period": None},
    ]
    eq(_money(rows[0]), "19–23 USD/час", "_money подписывает почасовую")
    eq(_money(rows[1]), "168 000–333 500 USD/год", "_money подписывает годовую")
    eq(_money(rows[2]), "300 000–490 000 RUB", "период неизвестен — без суффикса")
    table = "\n".join(_delta_table(rows))
    for want in ("19–23 USD/час", "168 000–333 500 USD/год"):
        if want not in table:
            FAILS.append(f"в таблице отчёта нет строки {want!r}")


# ──────────────────────────────────────────────────────────────────────────────
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
    from .sources import Ctx, src_hh

    # Фрагмент с амперсандом — не педантизм: «page=1» встречается и внутри
    # «items_on_page=100», и фикстура второй страницы отвечала на все запросы.
    pages = {
        "&page=0": _hh_page([_hh_vac(i) for i in range(1, 101)], 250),
        "&page=1": _hh_page([_hh_vac(i) for i in range(101, 201)], 250),
        "&page=2": _hh_page([_hh_vac(i) for i in range(201, 251)], 250),
        "&page=3": _hh_page([], 250),
    }
    fake = _FakeFetch(pages)
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

    p1 = _habr_page([_habr_card(i, _fresh(1)) for i in range(1, 4)], has_next=False)
    fake = _FakeFetch({"career.habr.com/vacancies": p1})
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
    regions = len(__import__("scripts.scout.sources", fromlist=["x"]).LINKEDIN_REGIONS)
    eq(len(jobs), 2, "два региона не размножают карточки: id общий, повтор — это дубль")
    eq([v.external_id for v in jobs], ["1", "3"],
       "Head of Finance и HR/Payroll Manager отсеяны фильтром профессии")
    eq(summary.raw["skipped_profile"], 2, "отсеянные по профессии посчитаны")
    eq(summary.raw["mismatch"], 0, "баланс сошёлся")
    eq(len(fake.asked), 3 * regions, "по каждому региону: две страницы с карточками "
                                     "и третья пустая — она и есть конец выдачи")
    if not any("start=10" in u for u in fake.asked):
        FAILS.append(f"вторая страница региона не спрошена: {fake.asked[:5]}")
    if len(fake.naps) != len(fake.asked) - 1:
        FAILS.append(f"пауз {len(fake.naps)} на {len(fake.asked)} запросов — "
                     f"площадку, которая троттлит охотнее всех, долбим без передышки")
    api = jobs[0].raw.get("guest_description_api")
    if not api or "jobs-guest/jobs/api/jobPosting/1" not in api:
        FAILS.append(f"нет анонимной ссылки на описание: {api!r} — "
                     f"detail пойдёт на /jobs/view/, где капча")


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
    regions = len(S.LINKEDIN_REGIONS)
    eq(len(fake.asked), 6 * regions,
       "три пустые подряд (стр. 4–6) — конец выдачи; седьмую уже не просим")
    if not any("уехала от запроса" in n for n in summary.raw["notes"]):
        FAILS.append(f"причина остановки не названа: {summary.raw['notes']}")
    if any("ОБРЕЗАНО" in n for n in summary.raw["notes"]):
        FAILS.append("уход выдачи вбок назван обрезанием — это разные вещи, "
                     "и второе зовёт поднять --limit там, где брать уже нечего")


def test_linkedin_limit_counts_all_regions_together():
    """`--limit 400` — это «принеси около четырёхсот», а не «по четыреста из
    каждой из девяти стран»: последнее даёт 3 600 карточек и 360 запросов
    к площадке, которая троттлит охотнее всех."""
    from .sources import (Ctx, LINKEDIN_MAX_PAGES, LINKEDIN_PAGE, LINKEDIN_REGIONS,
                          _page_budget)
    per_run = LINKEDIN_PAGE * len(LINKEDIN_REGIONS)
    eq(_page_budget(Ctx(limit=400), per_run, LINKEDIN_MAX_PAGES), LINKEDIN_MAX_PAGES,
       "штатный лимит не углубляет обход каждого региона в разы")
    eq(_page_budget(Ctx(limit=per_run * LINKEDIN_MAX_PAGES * 2), per_run, LINKEDIN_MAX_PAGES),
       LINKEDIN_MAX_PAGES * 2, "осознанно большой лимит потолок всё-таки поднимает")


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
]

# То, что фильтр обязан отсекать и после расширения. Иначе он перестаёт быть
# фильтром: доски отдают 7 488 строк, и продажи с поддержкой — это 6 000 из них.
ATS_ROLE_MUST_FAIL = [
    "Account Executive", "Customer Support Specialist", "Recruiter",
    "Marketing Manager", "Financial Controller", "Head of Finance",
    "HR & Payroll Manager", "Менеджер по продажам", "Бухгалтер",
    "Graphic Designer", "Sales Engineer", "Legal Counsel",
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


def test_every_ats_engine_is_wired_into_the_run():
    """Движок, разобранный в atsapi, но не подключённый к прогону, — это компания,
    которая не попадёт в обход, даже если найти её через `ats sniff`."""
    from .atsapi import ATS_KINDS
    from .sources import _ATS_IMPL
    for kind in ATS_KINDS:
        if kind not in _ATS_IMPL:
            FAILS.append(f"движок {kind} есть в atsapi, но прогон его не опрашивает")


def test_new_sources_are_in_the_registry():
    """Источник, которого нет в реестре, не попадает ни в collect, ни в дельту,
    ни в покрытие — и его отсутствие ничем не видно."""
    from .sources import SOURCE_NOTES, SOURCES
    for name in ("himalayas", "arbeitnow", "jobicy"):
        if name not in SOURCES:
            FAILS.append(f"источник {name} не в реестре SOURCES")
    for name in ("himalayas", "arbeitnow"):
        if name not in SOURCE_NOTES:
            FAILS.append(f"{name}: нет примечания про неприменимость --days")


# ──────────────────────────────────────────────────────────────────────────────
# tg-dm: форматирование личной переписки (без сети и без сессии)
# ──────────────────────────────────────────────────────────────────────────────

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_tg_dm_format_marks_direction_and_files():
    """Строка сообщения в личке: формат тот же, что у дампа канала, плюс вложение.

    Вложение здесь несёт весь смысл команды: вопрос «отправлял ли он уже резюме»
    решается прикреплённым файлом, а в тексте сообщения от него не остаётся
    ничего — без строки [файл] дамп показал бы «(медиа без текста)»."""
    from datetime import datetime, timezone as tz

    from .tgclient import _file_line, _format_message

    sent = _Obj(id=42, date=datetime(2026, 7, 28, 9, 30, tzinfo=tz.utc),
                message="Добрый день! Отправляю резюме.", entities=None,
                reply_markup=None, sender=_Obj(first_name="Пётр", last_name=None,
                                               username="pjobseeker"),
                file=_Obj(name="resume.pdf", size=204800, mime_type="application/pdf"))
    line = _format_message(sent, "Рекрутёр")
    if not line.startswith("[#42] [2026-07-28T09:30:00Z] Пётр (@pjobseeker): "):
        FAILS.append(f"tg-dm: сломан формат строки дампа: {line!r}")
    if "[файл] resume.pdf, 200 КБ" not in line:
        FAILS.append(f"tg-dm: вложение потеряно — именно по нему видно, "
                     f"что резюме уже отправлено: {line!r}")

    # Сообщение без файла не должно обрастать пустой строкой [файл].
    plain = _Obj(id=43, date=datetime(2026, 7, 28, 10, 0, tzinfo=tz.utc),
                 message="Спасибо, посмотрим", entities=None, reply_markup=None,
                 sender=_Obj(first_name="Анна", last_name=None, username=None), file=None)
    if "[файл]" in _format_message(plain, "Рекрутёр"):
        FAILS.append("tg-dm: строка [файл] появилась у сообщения без вложения")
    eq(_file_line(plain), None, "нет вложения — нет строки про файл")


def test_tg_dm_header_says_who_is_who():
    """Шапка дампа. В формате «Автор: текст» исходящие от входящих отличаются
    только именем, а решение «писать или нет» зависит именно от этого."""
    from .tgclient import DMResult, render_dm

    res = DMResult(peer="Анна HR (@anna)", kind="user", me="Пётр (@pjobseeker)",
                   messages=3, lines=["[#1] [2026-07-28T09:30:00Z] Пётр (@pjobseeker): привет"])
    out = render_dm(res, "@anna", 50)
    for want in ("# tg-dm: Анна HR (@anna) [user] — сообщений 3",
                 "# я в этой переписке: Пётр (@pjobseeker)",
                 "прочитанным НЕ помечено"):
        if want not in out:
            FAILS.append(f"tg-dm: в шапке нет {want!r}:\n{out}")
    if "переписка длиннее" in out:
        FAILS.append("tg-dm: ложное предупреждение об усечении на 3 сообщениях")

    # Упёрлись в --limit — об этом надо сказать, иначе «вот вся переписка» соврёт.
    cut = DMResult(peer="Анна", kind="user", me="Пётр", messages=50,
                   lines=[], truncated=True)
    if "переписка длиннее" not in render_dm(cut, "@anna", 50):
        FAILS.append("tg-dm: усечение по --limit не названо вслух")

    # Не личка — «я» здесь один из многих, и это меняет смысл прочитанного.
    grp = DMResult(peer="Go Jobs", kind="group", me="Пётр", messages=2, lines=[])
    if "это не личная переписка" not in render_dm(grp, "gojobs", 50):
        FAILS.append("tg-dm: группа выдана за личную переписку")


def test_tg_dm_never_marks_as_read():
    """Отметка прочитанным в личке видна собеседнику — это действие ОТ ИМЕНИ
    пользователя. В коде команды не должно быть ни одного её вызова."""
    import inspect

    from . import tgclient

    for fn in (tgclient.read_dm, tgclient.render_dm):
        src = inspect.getsource(fn)
        for banned in ("send_read_acknowledge", "_mark_read"):
            if banned in src:
                FAILS.append(f"tg-dm: {fn.__name__} вызывает {banned} — "
                             f"это отметка прочитанным от имени пользователя")


def test_generic_text_picker_rules():
    """Правила выбора между нашим разбором и readability.

    Опасность у readability ровно одна: короткий чистый вывод и потерянная
    страница выглядят одинаково. Поэтому каждая ветка решения — отдельный случай,
    и проверяются они подменой самого извлекателя, без сети и без пакета."""
    from . import detail as D

    ours = "Требования: Go, PostgreSQL. " * 40           # ~1100 символов с маркером
    real = D._readability_text
    try:
        # 1. Пакета нет — молча остаёмся на своём разборе.
        D._readability_text = lambda html: None
        eq(D._pick_generic_text(ours, "<html/>"), (ours, None),
           "нет readability — берём наш текст, без пометки")

        # 2. Вывод короче и с маркерами вакансии — берём readability.
        clean = "Обязанности: писать сервисы на Go. " * 15  # ~500, короче нашего
        eq(D._pick_generic_text(ours, "<html/>"), (ours, None), "контроль подмены")
        D._readability_text = lambda html: clean
        eq(D._pick_generic_text(ours, "<html/>"), (clean, "readability"),
           "короче и с маркерами — берём readability")

        # 3. Вывод без маркеров вакансии — это не «чисто», это потеря страницы.
        D._readability_text = lambda html: "Cookie policy. Follow us on LinkedIn. " * 20
        got, took = D._pick_generic_text(ours, "<html/>")
        eq((got, took), (ours, "readability-rejected"),
           "нет признаков вакансии — readability отброшен")

        # 4. Вывод короче порога каркаса — тоже отброс: _flag_skeleton иначе
        #    поднял бы тревогу на пустом месте.
        D._readability_text = lambda html: "Требования: Go"
        eq(D._pick_generic_text(ours, "<html/>")[1], "readability-rejected",
           "слишком короткий вывод — readability отброшен")

        # 5. Вывод ДЛИННЕЕ нашего — значит прихватил навигацию и подвал.
        D._readability_text = lambda html: ours + "Требования: " + "хвост " * 500
        eq(D._pick_generic_text(ours, "<html/>")[1], "readability-longer",
           "длиннее нашего — остаёмся на своём")
    finally:
        D._readability_text = real


def test_generic_text_cuts_boilerplate():
    """Стык с readability-lxml целиком: пакет на месте, импорт ленивый, вывод —
    описание вакансии без обвязки страницы.

    Проверяется то, ради чего библиотека взята: список чужих вакансий в навигации
    и юридический подвал в выжимку не попадают. Именно они забивали контекст —
    на живой странице Greenhouse наш разбор давал 13 847 символов против 7 369
    у readability. Пропускается, если пакета нет: путь опциональный."""
    try:
        import readability  # noqa: F401
    except ImportError:
        return
    from .detail import _pick_generic_text

    nav = "<nav>" + "".join(f'<a href="/j/{i}">Vacancy number {i} in our company</a> '
                            for i in range(120)) + "</nav>"
    side = "<aside>" + "".join(f'<p><a href="/c/{i}">Company {i}</a></p>'
                               for i in range(80)) + "</aside>"
    art = ("<article><h1>Go Engineer</h1>"
           + "<p>Requirements: production experience with Go and PostgreSQL, "
             "distributed systems and gRPC. You will design services and own them "
             "in production.</p>" * 10 + "</article>")
    html = (f"<html><body>{nav}{art}{side}<footer>"
            + "Cookie policy and legal notice. " * 40 + "</footer></body></html>")

    ours = html_to_text(html)
    plain, took = _pick_generic_text(ours, html)
    eq(took, "readability", "readability не выбран на странице с навигацией и подвалом")
    if "Go and PostgreSQL" not in plain:
        FAILS.append(f"readability потерял описание вакансии: {plain[:200]!r}")
    for junk, label in (("Vacancy number 3 ", "список чужих вакансий из навигации"),
                        ("Cookie policy", "юридический подвал")):
        if junk in plain:
            FAILS.append(f"readability оставил в выжимке {label}")
    if len(plain) >= len(ours):
        FAILS.append(f"выжимка не сократилась: {len(ours)} → {len(plain)}")


def test_mail_candidate_filter():
    """Отбор кандидатов на второй проход. Правила те же, что были в fetch_mail, —
    вынесены в функцию, чтобы их можно было проверить без сети."""
    from .mailsync import is_candidate
    cases = [
        ("Acme <no-reply@greenhouse-mail.io>", "Your application results", True,
         "знакомый ATS-домен"),
        ("HR <hr@unknown-corp.xyz>", "Re: Golang Dev", True,
         "короткая тема от незнакомого домена — тело всё равно тянем"),
        ("hh <no-reply@hh.ru>", "Новые вакансии по подписке", False,
         "подписочная рассылка — шум"),
        ("Shop <news@shop.example>",
         "Гигантская распродажа этой недели только для вас, успейте купить всё сразу",
         False, "длинная тема, незнакомый домен, ни одного слова про найм"),
    ]
    for sender, subject, want, label in cases:
        eq(is_candidate(sender, subject, own_address="me@gmail.com"), want, label)


# ──────────────────────────────────────────────────────────────────────────────
# cookiesrc: выбор браузера, форматы json-кук, отсутствие кэша
# ──────────────────────────────────────────────────────────────────────────────

def test_cookie_file_formats():
    """Оба формата экспорта распознаются. Молча вернуть пустой профиль здесь —
    это «залогинен, но ходим анонимом», самая дорогая из тихих ошибок."""
    import json as _json
    import os
    import tempfile
    from .cookiesrc import parse_cookie_file

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Playwright storage_state
        p1 = os.path.join(tmp, "state.json")
        with open(p1, "w") as f:
            _json.dump({"cookies": [{"name": "hhtoken", "value": "V1",
                                     "domain": ".hh.ru", "path": "/",
                                     "expires": 1893456000, "httpOnly": True,
                                     "secure": True, "sameSite": "Lax"}],
                        "origins": [{"origin": "https://hh.ru", "localStorage": []}]}, f)
        st = parse_cookie_file(p1)
        eq(len(st["cookies"]), 1, "storage_state: кука разобралась")
        eq(st["cookies"][0]["value"], "V1", "storage_state: значение на месте")
        eq(len(st["origins"]), 1, "storage_state: origins сохранены")

        # 2. Выгрузка расширения (EditThisCookie / Cookie-Editor)
        p2 = os.path.join(tmp, "ext.json")
        with open(p2, "w") as f:
            _json.dump([
                {"domain": ".hh.ru", "expirationDate": 1893456000.77, "hostOnly": False,
                 "httpOnly": True, "name": "hhuid", "path": "/",
                 "sameSite": "no_restriction", "secure": True, "session": False,
                 "value": "V2"},
                {"domain": "career.habr.com", "hostOnly": True, "httpOnly": False,
                 "name": "sess", "path": "/", "sameSite": "unspecified",
                 "secure": False, "session": True, "value": "V3"},
            ], f)
        st = parse_cookie_file(p2)
        by = {c["name"]: c for c in st["cookies"]}
        eq(len(st["cookies"]), 2, "экспорт расширения: обе куки разобрались")
        eq(by["hhuid"]["expires"], 1893456000.77, "expirationDate → expires")
        eq(by["hhuid"]["sameSite"], "None", "no_restriction → None")
        eq(by["sess"]["expires"], -1, "session: true → -1")
        eq(by["sess"]["sameSite"], "Lax", "unspecified → Lax")

        # 3. Мусор — внятная ошибка, а не пустой профиль
        p3 = os.path.join(tmp, "junk.json")
        with open(p3, "w") as f:
            _json.dump({"something": "else"}, f)
        try:
            parse_cookie_file(p3)
            FAILS.append("cookie-file: мусор должен кидать ValueError, а не пустой профиль")
        except ValueError:
            pass


def test_cookie_samesite_none_without_secure():
    """sameSite=None без Secure Playwright не примет — понижаем до Lax."""
    from .cookiesrc import _ext_cookie
    c = _ext_cookie({"name": "n", "value": "v", "domain": "hh.ru",
                     "sameSite": "no_restriction", "secure": False})
    eq(c["sameSite"], "Lax", "экспорт: None без Secure → Lax")


def test_choose_browser_picks_widest():
    """`auto` берёт ОДИН браузер — тот, что покрывает больше нужных доменов.
    Не «все сразу»: комбинирование только по явному указанию пользователя."""
    from . import cookiesrc

    fake = {"yandex": {"hh.ru": 20, "career.habr.com": 5},
            "chrome": {"hh.ru": 40},
            "claude": {}}
    real_cov, real_db = cookiesrc.coverage_without_keychain, cookiesrc._db_exists
    real_path = cookiesrc.ci._db_path
    try:
        cookiesrc.coverage_without_keychain = lambda b, d: fake.get(b, {})
        cookiesrc._db_exists = lambda b: b in fake
        cookiesrc.ci._db_path = lambda b: __file__      # чтобы getmtime не падал
        pick, per = cookiesrc.choose_browser(("hh.ru", "career.habr.com"))
        eq(pick, "yandex", "auto: побеждает покрытие доменов, а не число кук")
        eq(set(per), {"yandex", "chrome", "claude"}, "auto: посчитаны все браузеры")
        # Ни одного покрытия — честный None, а не случайный браузер.
        fake2 = {"chrome": {}}
        cookiesrc.coverage_without_keychain = lambda b, d: fake2.get(b, {})
        cookiesrc._db_exists = lambda b: b in fake2
        pick, _ = cookiesrc.choose_browser(("hh.ru",))
        eq(pick, None, "auto: нет кук ни в одном браузере → None")
    finally:
        cookiesrc.coverage_without_keychain = real_cov
        cookiesrc._db_exists = real_db
        cookiesrc.ci._db_path = real_path


def test_cookie_source_reports_missing_without_grabbing_all():
    """Не хватило доменов — говорим ЧТО именно и предлагаем точную команду.
    Автоматически в остальные браузеры не лезем."""
    from .cookiesrc import CookieSource

    src = CookieSource({"cookies": [{"domain": ".hh.ru", "name": "a", "value": "1"}]},
                       "yandex", needed=("hh.ru", "geekjob.ru"),
                       covered={"hh.ru": 1}, tried=["yandex"])
    eq(src.missing, ["geekjob.ru"], "видно, какого домена не хватает")
    line = src.line()
    if "1/2" not in line or "yandex" not in line:
        FAILS.append(f"строка источника должна быть «источник: …, покрыто 1/2»: {line!r}")
    hint = src.hint() or ""
    if "geekjob.ru" not in hint or "--cookies-from" not in hint:
        FAILS.append(f"подсказка должна называть домен и команду добора: {hint!r}")
    full = CookieSource({"cookies": []}, "yandex", needed=("hh.ru",),
                        covered={"hh.ru": 3})
    eq(full.hint(), None, "всё покрыто — никаких подсказок и просьб логиниться")


def test_cookie_header_from_source():
    """Заголовок Cookie строится из тех же кук, что и браузерный контекст —
    иначе stdlib-сборщик ходит анонимом при живом входе."""
    from .cookiesrc import CookieSource
    src = CookieSource({"cookies": [
        {"domain": ".hh.ru", "name": "hhtoken", "value": "V", "expires": -1},
        {"domain": ".hh.ru", "name": "old", "value": "X", "expires": 1},  # протухла
        {"domain": "geekjob.ru", "name": "gj", "value": "G", "expires": -1},
    ]}, "yandex", needed=("hh.ru",))
    got = src.cookie_header()
    eq(got, "hhtoken=V", "cookie_header: только домен задачи, без протухших")


def test_missing_cache_breaks_nothing():
    """Удаление `.auth/browser.json` не должно ломать ничего: он кэш, а не
    источник правды. Раньше его отсутствие делало render молча анонимным,
    а hh-sync — падающим при живом входе."""
    import os
    import tempfile
    from . import auth, cookiesrc

    real_dir, real_state = auth.AUTH_DIR, auth.BROWSER_STATE
    real_names = cookiesrc.BROWSER_NAMES
    try:
        with tempfile.TemporaryDirectory() as tmp:
            auth.AUTH_DIR = tmp
            auth.BROWSER_STATE = os.path.join(tmp, "browser.json")
            cookiesrc.BROWSER_STATE = auth.BROWSER_STATE
            cookiesrc.BROWSER_NAMES = ()          # браузеров тоже нет
            eq(cookiesrc.cache_state(), None, "нет кэша — None, не исключение")
            src = cookiesrc.resolve("auto", ("hh.ru",), use_cache=True)
            eq(src.cookies, [], "без кэша и браузеров — пустой профиль")
            eq(src.missing, ["hh.ru"], "и честно сказано, чего нет")
            if not src.line():
                FAILS.append("строка источника должна быть даже при пустом профиле")
            # resolve_storage без оверрайда обязан вернуть None, а не путь к
            # несуществующему файлу — иначе Playwright падает на старте.
            eq(auth.resolve_storage(), None, "resolve_storage без оверрайда → None")
            eq(auth.cookie_header("hh"), None, "cookie_header без кук → None, не падение")
    finally:
        auth.AUTH_DIR, auth.BROWSER_STATE = real_dir, real_state
        cookiesrc.BROWSER_STATE = real_state
        cookiesrc.BROWSER_NAMES = real_names


def test_cookie_merge_prefers_fresher():
    """При импорте из двух браузеров побеждает СВЕЖАЯ кука, а не та, чей браузер
    стоит позже в словаре: иначе шестидневный Chrome затирает сегодняшний Яндекс."""
    from .cookieimport import META_TS, merge_cookies, strip_meta
    old = [{"domain": "hh.ru", "path": "/", "name": "s", "value": "OLD", META_TS: 200}]
    new = [{"domain": "hh.ru", "path": "/", "name": "s", "value": "NEW", META_TS: 100}]
    merged = merge_cookies(old, new)
    eq(merged[0]["value"], "OLD", "merge: устаревшая входящая не вытесняет свежую")
    merged = merge_cookies(new, old)
    eq(merged[0]["value"], "OLD", "merge: свежая входящая вытесняет старую")
    eq([k for k in strip_meta(merged)[0] if k.startswith("_scout")], [],
       "strip_meta: служебные поля не уезжают в Playwright")


def test_filter_state_and_origins():
    """browse/login сохраняют ТОЛЬКО домены площадок и ДОПОЛНЯЮТ origins."""
    from .cookieimport import filter_state, merge_origins
    state = {"cookies": [{"domain": ".hh.ru", "name": "a", "value": "1"},
                         {"domain": ".yandex.ru", "name": "yandexuid", "value": "2"},
                         {"domain": "mc.yandex.ru", "name": "t", "value": "3"}],
             "origins": [{"origin": "https://hh.ru", "localStorage": [1]},
                         {"origin": "https://mc.yandex.ru", "localStorage": [2]}]}
    got = filter_state(state)
    eq([c["domain"] for c in got["cookies"]], [".hh.ru"],
       "filter_state: трекерные куки Яндекса не попадают в профиль")
    eq([o["origin"] for o in got["origins"]], ["https://hh.ru"],
       "filter_state: origins тоже фильтруются")
    merged = merge_origins([{"origin": "https://career.habr.com", "localStorage": [9]}],
                           got["origins"])
    eq(len(merged), 2, "merge_origins: localStorage невизитированной площадки уцелел")


# ──────────────────────────────────────────────────────────────────────────────
# store: усечение, служебные строки, заметки
# ──────────────────────────────────────────────────────────────────────────────

def test_query_count_and_summary_row():
    """`new` обязан знать, сколько строк ВСЕГО, и не считать служебную сводку ATS."""
    import os
    import tempfile
    from . import store
    from .model import Vacancy
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        vs = [Vacancy(source="hh", external_id=str(i), url=f"https://hh.ru/vacancy/{i}",
                      title=f"Go {i}") for i in range(5)]
        vs.append(Vacancy(source="ats", external_id="_summary", url="",
                          title="[сводка ATS] досок опрошено 20/20"))
        with store.connect(db) as conn:
            store.upsert(conn, vs)
            total = store.count(conn)
            rows = store.query(conn, limit=2)
        eq(total, 5, "count: служебная строка _summary не считается вакансией")
        eq(len(rows), 2, "query: limit применился")
        if any(r["external_id"] == "_summary" for r in rows):
            FAILS.append("query: служебная сводка ATS попала в выдачу")


def test_decide_keeps_note():
    """Повторный mark без --note не должен стирать прежнюю заметку."""
    import os
    import tempfile
    from . import store
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        with store.connect(db) as conn:
            store.decide(conn, "hh", "111", "applied", "отклик 30.07")
            store.decide(conn, "hh", "111", "skipped", None)
            row = conn.execute("SELECT state, note FROM decision").fetchone()
        eq(row["state"], "skipped", "decide: статус обновился")
        eq(row["note"], "отклик 30.07", "decide: заметка пережила повторный mark")


def test_blocked_retry_window():
    """Стена повторяется, но не каждый прогон — иначе капчи вечно съедают лимит."""
    import os
    import tempfile
    from . import store
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        with store.connect(db) as conn:
            store.save_detail(conn, "linkedin", "1", "u", "blocked", error="captcha")
            store.save_detail(conn, "hh", "2", "u", "ok", payload={"x": 1})
            fresh = store.have_details(conn, [("linkedin", "1"), ("hh", "2")])
            # Тот же набор, но окно повтора нулевое — стена снова в работе.
            aged = store.have_details(conn, [("linkedin", "1"), ("hh", "2")],
                                      retry_blocked_after_days=0)
        eq(("linkedin", "1") in fresh, True, "свежая стена не берётся повторно сразу")
        eq(("linkedin", "1") in aged, False, "по истечении окна стена пробуется снова")
        eq(("hh", "2") in aged, True, "успешная выжимка не перекачивается никогда")


def test_search_negotiations():
    """`status --query` обязан видеть отказы — ради этого таблица и заведена."""
    import os
    import tempfile
    from . import store
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        with store.connect(db) as conn:
            store.upsert_negotiation(conn, title="Go Developer", company="Ozon Tech",
                                     status="rejection", source="hh")
            got = store.search_negotiations(conn, "Ozon")
        eq(len(got), 1, "search_negotiations: нашёл по компании")
        eq(got[0]["status"], "rejection", "search_negotiations: статус на месте")


def test_country_matcher_word_boundaries():
    """«Russian speaker» — это язык, а не локация; «Prussia» — не Россия."""
    from .atsapi import BoardJob, country_matcher, job_matches_country
    pat = country_matcher("RU")
    if pat.search("Prussia"):
        FAILS.append("country_matcher(RU): «Prussia» — ложное срабатывание")
    job = BoardJob(id="1", title="Backend Java Engineer - Russian speaker",
                   url="u", locations=["Europe", "GB"])
    if job_matches_country(job, pat):
        FAILS.append("country RU: требование языка в заголовке не должно быть локацией")
    ok = BoardJob(id="2", title="Backend Engineer", url="u",
                  locations=["Remote - Russia"])
    if not job_matches_country(ok, pat):
        FAILS.append("country RU: настоящая локация не нашлась")


def test_split_requirements():
    """Требования вырезаются из сплошного описания — у greenhouse/ashby секция
    в тексте есть, а поле requirements оставалось null и читалось как «нет»."""
    from .detail import split_requirements
    text = ("About the role\nWe build things.\n\nRequirements\n"
            "• 5+ years with Go\n• Kubernetes in production\n• SQL\n\n"
            "What we offer\n• Stock options\n")
    got = split_requirements(text) or ""
    if "5+ years with Go" not in got:
        FAILS.append(f"split_requirements: не нашёл блок требований: {got!r}")
    if "Stock options" in got:
        FAILS.append("split_requirements: захватил секцию условий")
    eq(split_requirements("Просто текст без секций, довольно длинный, но плоский " * 3),
       None, "нет секции — None, а не выдуманный блок")


def test_hh_date_normalization():
    from .hhsync import norm_date
    from datetime import date as _date
    today = _date(2026, 7, 30)
    eq(norm_date("yesterday", today=today)[0], "2026-07-29", "yesterday → ISO")
    eq(norm_date("вчера", today=today)[0], "2026-07-29", "вчера → ISO")
    eq(norm_date("8 July", today=today)[0], "2026-07-08", "«8 July» → ISO с годом")
    eq(norm_date("20 декабря", today=today)[0], "2025-12-20",
       "месяц в будущем — значит прошлый год")
    iso, raw = norm_date("какая-то ерунда")
    eq((iso, raw), (None, "какая-то ерунда"),
       "не разобралось — ISO пустой, сырьё сохранено")


def main() -> int:
    for fn in (test_salary, test_zero_is_not_a_salary, test_salary_str, test_currency,
               test_dup_key, test_resolver_ignores_social, test_classify,
               test_tg_split_on_header_only, test_tg_resume_form_without_hashtag,
               test_html_to_text, test_parse_job_url, test_country_matcher_structural,
               test_salary_str_function, test_parse_negotiations_markup,
               test_parse_negotiations_lux, test_negotiations_empty_and_broken,
               test_classify_mail, test_company_guess, test_negotiation_upsert,
               test_summary_rows_are_stored_but_not_counted,
               test_match_processed_conservative, test_build_scan_report,
               test_cookie_domain_filter_rejects_wildcard, test_cookie_domain_allowed,
               test_cookie_merge, test_cookie_expires_and_samesite,
               test_mail_dump_classification,
               # ── добавлено по разбору аудита ────────────────────────────
               test_classify_mail_body, test_classify_mail_false_positives,
               test_parse_vacancy_from_body, test_mail_key_does_not_collapse,
               test_cookie_file_formats, test_cookie_samesite_none_without_secure,
               test_choose_browser_picks_widest,
               test_cookie_source_reports_missing_without_grabbing_all,
               test_cookie_header_from_source, test_missing_cache_breaks_nothing,
               test_cookie_merge_prefers_fresher, test_filter_state_and_origins,
               test_query_count_and_summary_row, test_decide_keeps_note,
               test_blocked_retry_window, test_search_negotiations,
               test_country_matcher_word_boundaries, test_split_requirements,
               test_hh_date_normalization,
               test_scan_report_survives_broken_stages,
               test_scan_report_has_full_delta_table,
               test_match_processed_groups_by_candidate,
               test_match_processed_short_company_no_false_positive,
               test_negotiations_empty_markers_english,
               test_hh_status_from_tag,
               # ── найдено на приёмке ────────────────────────────────────
               test_db_flag_before_subcommand, test_enrich_order_freshest_first,
               # ── разбор двух исследований готовых библиотек ─────────────
               test_html_to_text_drops_template_state,
               test_html_to_text_keeps_form_questions,
               test_mail_body_decodes_legacy_charsets, test_mail_body_falls_back_to_html,
               test_mail_own_letters_skipped, test_mail_candidate_filter,
               test_generic_text_picker_rules, test_generic_text_cuts_boilerplate,
               test_himalayas_parses_fixture, test_himalayas_empty_answer_is_a_failure,
               # ── период вилки: без него колонка «деньги» врала ──────────
               test_period_normalization, test_salary_str_shows_period,
               test_db_migration_adds_period_to_old_base,
               test_hh_period_is_month_even_for_shift_rates,
               test_habr_period_only_when_named,
               test_careered_takes_period_from_its_own_field,
               test_new_and_report_print_period,
               test_arbeitnow_follows_cursor_pagination, test_jobicy_filters_on_the_server,
               test_new_sources_are_in_the_registry,
               # ── полнота обхода: пагинация, окно и фильтр профессии ─────
               test_hh_walks_every_page, test_hh_truncation_is_never_silent,
               test_hh_limit_below_default_does_not_shrink_the_window,
               test_habr_paginates_until_the_window_edge,
               test_habr_stops_where_the_site_says_it_ends,
               test_careered_filters_profession_and_reads_to_the_window_edge,
               test_linkedin_paginates_by_start_and_drops_other_professions,
               test_linkedin_stops_where_the_search_drifts_off_topic,
               test_linkedin_limit_counts_all_regions_together,
               test_linkedin_ru_only_still_reports_itself,
               test_ats_role_filter_covers_the_audit_list,
               test_every_ats_engine_is_wired_into_the_run,
               test_tg_dm_format_marks_direction_and_files,
               test_tg_dm_header_says_who_is_who, test_tg_dm_never_marks_as_read):
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
