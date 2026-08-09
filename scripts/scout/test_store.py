"""Тесты состояния: усечение выдачи, служебные строки, заметки, миграции.

Выделено из `test_scout.py` 08.08.2026: файл дорос до 6300 строк и стал главным
пожирателем контекста у агентов. Раздел про `store` был в нём самым крупным —
полторы тысячи строк.

Проверка при выносе шла не глазами, а числом ЗАРЕГИСТРИРОВАННЫХ тестов по AST:
тест, выпавший из реестра `main`, не краснеет — он исчезает.

    .venv/bin/python -m scripts.scout.test_store
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from . import store
from .model import SUMMARY_ID, Vacancy
from .testutil import fresh as _fresh, patched, stale as _stale

FAILS: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def ok(cond, label):
    if not cond:
        FAILS.append(label)


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


def test_reveal_consume_contact_kinds():
    """Разбор ответа /api/limits/consume: тип контакта определяется по open_url.
    Ошибка здесь молча уезжает в карточку как «ссылка» вместо «телеграм»."""
    from .reveal import parse_consume
    c = parse_consume({"allowed": True, "open_url": "https://t.me/go_hr", "remaining": 4})
    eq((c.allowed, c.kind, c.remaining), (True, "telegram", 4), "t.me → telegram")
    c = parse_consume({"allowed": True, "open_url": "mailto:hr@x.io", "remaining": 0})
    eq((c.kind, c.remaining), ("email", 0),
       "mailto → email; остаток 0 сохраняется, а не путается с «не назвали»")
    c = parse_consume({"allowed": True, "open_url": "tg://resolve?domain=hr"})
    eq(c.kind, "telegram", "tg:// → telegram")
    c = parse_consume({"open_url": "https://x.io/careers"})
    eq((c.allowed, c.kind), (True, "ссылка"),
       "поля allowed может не быть вовсе — живой open_url и есть согласие")


def test_reveal_consume_denied_and_rate_limited():
    """allowed=false и rate_limited — команда обязана остановиться с message."""
    from .reveal import parse_consume
    c = parse_consume({"allowed": False, "message": "Лимит раскрытий исчерпан"})
    eq((c.allowed, c.message), (False, "Лимит раскрытий исчерпан"),
       "allowed=false доносит message")
    c = parse_consume({"rate_limited": True, "allowed": True, "message": "Слишком часто"})
    eq((c.allowed, c.rate_limited), (False, True),
       "rate_limited гасит allowed, что бы там ни лежало")
    c = parse_consume(None)
    eq(c.allowed, False, "не-объект — честный отказ, а не падение")


def test_reveal_job_id_from_url():
    """Команда со списанием обязана отказаться от всего, что не вакансия hirehi."""
    from .reveal import job_id_from_url
    eq(job_id_from_url("https://hirehi.ru/development/x-70186"), "70186",
       "id из хвоста slug")
    eq(job_id_from_url("https://hirehi.ru/vacancies/go,backend"), None,
       "страница поиска — не вакансия")
    eq(job_id_from_url("https://example.com/development/x-70186"), None,
       "чужой хост не принимается, даже с похожим slug")


def test_reveal_page_state_guards():
    """Анонимная страница = НЕ кликать: у анонима кнопка ведёт на форму логина."""
    from .reveal import page_state
    eq(page_state(None)[0], "unknown", "нет VACANCY_DATA — не вакансия hirehi")
    eq(page_state({"is_authenticated": False})[0], "anonymous", "аноним без сессии")
    # contact_ticket у живой сессии ПУСТ: сервер выдаёт билет на сам клик.
    # Пока эта проверка стояла, раскрытие не работало вообще ни разу —
    # is_authenticated=true, лимиты на месте, а команда печатала «протухла».
    eq(page_state({"is_authenticated": True, "contact_ticket": None})[0], "ok",
       "пустой contact_ticket у залогиненного — это норма, а не протухшая сессия")
    eq(page_state({"is_authenticated": True, "contact_ticket": "t-1"})[0], "ok",
       "живая сессия с билетом")
    eq(page_state({"is_authenticated": True,
                   "free_limits": {"direct_left": 0}})[0], "no_limits",
       "лимит раскрытий исчерпан — отдельный исход, не «протухла»")
    eq(page_state({"is_authenticated": True, "has_pro": True,
                   "free_limits": {"direct_left": 0}})[0], "ok",
       "у pro-аккаунта нулевые бесплатные лимиты не блокируют")


def test_tg_rollback_id_forms():
    """Сопоставление чата по id обязано понимать ОБА вида: в именах дампов лежит
    полный peer-id канала (-100…), у сущности Telethon — «голый». Живой откат
    04.08.2026 находил 3 чата из 28 именно из-за этого расхождения."""
    raw = "3275338603"
    by_id = {}
    by_id.setdefault(raw, "dialog")
    by_id.setdefault(f"100{raw}", "dialog")
    eq(by_id.get("1003275338603"), "dialog", "полный peer-id из имени дампа находит чат")
    eq(by_id.get(raw), "dialog", "голый id тоже находит")


def test_period_from_text_understands_slash_forms():
    """«/мес», «/ год», «₽/час» — период, а не отсутствие периода.

    Шаблоны требовали ЛИБО предлога («в месяц»), либо английского слова, и живые
    строки «81 000 EUR / год», «150 000 ₽/мес», «5 500 USD / месяц» не матчились
    ни одним. Период молча оставался неизвестным, и вилка печаталась без суффикса
    — годовая и месячная стояли в таблице неразличимо, при разнице в 12 раз."""
    from .sources import period_from_text

    eq(period_from_text("81,000 — 102,000 EUR / год"), "year", "«/ год»")
    eq(period_from_text("150 000 ₽/мес"), "month", "«/мес»")
    eq(period_from_text("5,500 USD / месяц"), "month", "«/ месяц»")
    eq(period_from_text("$60/час"), "hour", "«/час»")
    # Прежние формы обязаны продолжать работать.
    eq(period_from_text("100k per year"), "year", "per year")
    eq(period_from_text("300000 в месяц"), "month", "в месяц")
    eq(period_from_text("60 000 ₽"), None, "период не назван — остаётся None")


def test_tgvacancy_parses_real_post_shapes():
    """Пост канала → вакансия. Формы взяты из живых дампов 04.08.2026.

    До этого теста телеграм не доезжал до базы вовсе: 1343 кандидата за прогон
    и НОЛЬ строк с телеграмным источником в `vacancy`. Модель была вынуждена
    читать дампы глазами — это и была главная статья расходов прогона."""
    from .tg import classify as tg_classify, parse_dump
    from .tgvacancy import ChatRef, to_vacancy

    chat = ChatRef(chat_id="1003563575071", title="Golang Jobs Top",
                   username="golang_jobs_top")

    # 1. Пост-анкета careered: заголовок, вилка с периодом, локация, формат.
    dump = (
        "[#242] [2026-07-30T15:08:02Z] Golang Jobs Top (@golang_jobs_top): "
        "❇️ Backend Engineer, Platform (Golang, SaaS) · топ пост 🔥\n"
        "\n"
        "Design, build, and operate backend systems managing Grafana Cloud stacks, "
        "ensuring correct stack state and reliability using Golang.\n"
        "\n"
        "Зарплата: 81,000 — 102,000 EUR / год\n"
        "Локация: Ireland\n"
        "Формат работы: Удаленка\n"
        "\n"
        "Контакты: careered.io\n"
        "  [link] https://careered.io/jobs/d5aa8a1e-eeaf-419e-8c8f-7936bb9a9857\n")
    msgs = parse_dump(dump)
    for m in msgs:
        tg_classify(m)
    v = to_vacancy(msgs[0], chat)
    eq(v is not None, True, "пост-анкета — вакансия")
    eq(v.title, "Backend Engineer, Platform (Golang, SaaS)",
       "декор и хвост «· топ пост» сняты, название роли осталось")
    eq(v.source, "tg:golang_jobs_top", "источник — tg:<канал>")
    eq(v.external_id, "242", "external_id — id сообщения")
    eq(v.url, "https://t.me/golang_jobs_top/242", "ссылка на конкретный пост")
    eq((v.salary_from, v.salary_to, v.currency), (81000, 102000, "EUR"), "вилка")
    eq(v.salary_period, "year", "период вилки — год, а не подставленный месяц")
    eq(v.location, "Ireland", "локация из анкеты")
    eq(v.remote, True, "формат «Удаленка» → remote")
    eq(v.employer_url, "https://careered.io/jobs/d5aa8a1e-eeaf-419e-8c8f-7936bb9a9857",
       "прямая ссылка из [link] — не телеграмная")
    eq(v.published_at[:10], "2026-07-30", "дата публикации — дата поста")

    # 2. «Роль в Компании: ссылка» — единственное место, где назван работодатель.
    dump2 = ("[#1566] [2026-07-31T12:52:26Z] Remocate (@remocatedevs): "
             "Engineering team lead (Remote) в Field Materials: "
             "https://www.remocate.app/jobs/engineering-team-lead-remote-field-materials\n"
             "\n"
             "Можно работать удалённо. Зарплата в валюте.\n"
             "Требования: опыт от 5 лет.\n")
    msgs2 = parse_dump(dump2)
    for m in msgs2:
        tg_classify(m)
    v2 = to_vacancy(msgs2[0], ChatRef(chat_id="1002367224760", title="Remocate",
                                      username="remocatedevs"))
    eq(v2.title, "Engineering team lead (Remote)", "роль отделена от компании")
    eq(v2.company, "Field Materials", "работодатель распознан")

    # 3. «ЗП: по результатам интервью» — это ЯВНОЕ «вилки нет». Лезть за числом
    #    в тело поста после такого значит выдать случайное число за зарплату.
    dump3 = ("[#106356] [2026-07-20T16:18:20Z] Irina (@Irina_Poddubnaya): "
             "#вакансия #remote #QA\n"
             "\n"
             "Позиция: QA Engineer\n"
             "Компания: MarfaTech\n"
             "ЗП: по результатам интервью (конкурентно рынку)\n"
             "Формат: #удаленно, гибкий график, 5\\2\n"
             "\n"
             "Требования: Linux, опыт 3 года, английский B2.\n")
    msgs3 = parse_dump(dump3)
    for m in msgs3:
        tg_classify(m)
    v3 = to_vacancy(msgs3[0], chat)
    eq(v3.title, "QA Engineer", "заголовок из поля-анкеты, а не из строки хэштегов")
    eq(v3.company, "MarfaTech", "компания из поля-анкеты")
    eq((v3.salary_from, v3.salary_to), (None, None),
       "«по результатам интервью» — вилки нет, и выдумывать её нельзя")


def test_tgvacancy_perks_are_not_salary():
    """Деньги из соцпакета — не зарплата.

    Живой разбор: «✔️ компенсацию семейных поездок до $2,000 gross в год» уехало
    в колонку «Деньги» как «от 2000 USD/год» у Senior Golang Backend Engineer.
    Это не пустая ячейка, а уверенно напечатанная ложь про зарплату — ошибка
    того же рода, что потерянные три нуля в «350K», ради которой в проекте
    вообще заведён отдельный разбор вилок."""
    from .tgvacancy import _looks_like_salary_line, extract_salary

    eq(_looks_like_salary_line("✔️ компенсацию семейных поездок до $2,000 gross в год"),
       False, "компенсация поездок — льгота, а не вилка")
    eq(_looks_like_salary_line("- оплачиваем обучение и конференции до $1500 в год"),
       False, "оплата обучения — льгота")
    eq(_looks_like_salary_line("ДМС со стоматологией и страховка на $50 000"),
       False, "страховка — льгота")
    eq(_looks_like_salary_line("Мы международная компания с оборотом $300 млн"),
       False, "оборот компании — не зарплата")
    # А настоящие вилки обязаны по-прежнему разбираться.
    eq(_looks_like_salary_line("от 400 000 ₽/мес до налогов"), True, "голые деньги")
    eq(_looks_like_salary_line("Зарплата: 81,000 — 102,000 EUR / год"), True, "анкета")
    eq(_looks_like_salary_line("3500-5500 USD"), True, "вилка без слов")

    body = ("Senior Golang Backend Engineer\n\n"
            "Требования: опыт от 5 лет, Kubernetes, PostgreSQL.\n\n"
            "Что предлагаем:\n"
            "✔️ компенсацию семейных поездок до $2,000 gross в год;\n"
            "✔️ ДМС с первого дня.\n")
    eq(extract_salary(body)[:2], (None, None),
       "в посте без вилки денег быть не должно — соцпакет за неё не считается")


def test_tgvacancy_rejects_are_counted_not_silent():
    """Не-вакансии в базу не идут, но каждая — со СЧЁТЧИКОМ и причиной.

    «Тихо потерял» — худший баг проекта, поэтому reject_reason возвращает
    причину строкой: «отсеяно 641» без единого примера проверить нечем."""
    from .tg import classify as tg_classify, parse_dump
    from .tgvacancy import ChatRef, from_dump, reject_reason, extract_title

    dump = (
        # Промо-подборка каналов: ни хэштега вакансии, ни требований, ни роли.
        "[#1520] [2026-07-30T17:01:17Z] Go Job Offers (@runello_rus_goland): "
        "Хорошие вакансии долго не ждут\n"
        "\n"
        "Собрали в одной папке Telegram-каналы, где каждый день появляются свежие "
        "предложения для специалистов из разных сфер и фрилансеров.\n"
        "\n"
        "Добавить папку себе 👇\n"
        "https://t.me/addlist/aR-DhrnyyBRkZjhi\n"
        "\n"
        # Резюме соискателя.
        "[#1521] [2026-07-31T06:00:30Z] Пётр (@petr): #резюме #cv\n"
        "\n"
        "Ищу работу Go-разработчиком, опыт 6 лет, готов к релокации.\n"
        "\n"
        # Настоящая вакансия.
        "[#1522] [2026-08-03T06:00:30Z] Go Job Offers (@runello_rus_goland): "
        "Golang разработчик\n"
        "\n"
        "Грейд: Senior\n"
        "Стек: Go, Kubernetes, PostgreSQL, Kafka\n"
        "\n"
        "Требования: опыт от 4 лет.\n"
        "#golang #go #senior\n")
    msgs = parse_dump(dump)
    for m in msgs:
        tg_classify(m)
    vs, st = from_dump(msgs, ChatRef(chat_id="1001537669054", title="Go",
                                     username="runello_rus_goland"))
    eq(len(vs), 1, "в базу пошла ровно одна строка — настоящая вакансия")
    eq(vs[0].title, "Golang разработчик", "она же и разобралась")
    eq(st.vacancies, 1, "счётчик вакансий")
    eq(st.rejected, 2, "счётчик отсеянного — печатается всегда")
    eq(st.reasons.get("резюме соискателя"), 1, "резюме отсеяно с причиной")
    eq(len(st.examples) >= 2, True, "у отсева есть ПРИМЕРЫ, а не только цифра")
    eq(reject_reason(msgs[0], extract_title(msgs[0].body)),
       "ни хэштега вакансии, ни разделов требований, ни роли в заголовке",
       "причина отсева промо-поста называется словами")


def test_tgvacancy_strips_hashtag_runs_from_title():
    """Строка хэштегов — не название роли.

    Шаблон снимал хэштеги только СЛИТНЫЕ, а каналы пишут их через пробел.
    Живой результат: заголовком вакансии становилось «#Vacancy #dating #adult
    #Job #Ai #growth #remote #chats», и по такому заголовку не работало НИЧЕГО —
    ни ATS_ROLE_RE, ни ключ дубля, ни таблица «требование → что у тебя»."""
    from .tgvacancy import _clean_title

    eq(_clean_title("#вакансия #remote #QA Senior QA Engineer"), "Senior QA Engineer",
       "пробежка ведущих хэштегов снята целиком, а не по одному")
    eq(_clean_title("Golang разработчик #golang #go #senior"), "Golang разработчик",
       "хвостовые хэштеги сняты")
    eq(_clean_title("2419 #Vacancy #SoftwareEngineer Lead FullStack Engineer"),
       "Lead FullStack Engineer", "номер поста и хэштеги сняты")
    eq(_clean_title("#Vacancy #remote # PerformanceMarketing"), "PerformanceMarketing",
       "одинокая решётка — тоже мусор разметки")
    # Хэштег ВНУТРИ названия — часть названия, вырезать его нельзя.
    eq(_clean_title("Senior #Golang разработчик"), "Senior #Golang разработчик",
       "внутренний хэштег остаётся: без него рвётся само название")

    # Дефис и точка — часть тега. Без этого `#back-end` разбирался как тег
    # `#back` плюс хвост `-end`, и заголовком вакансии становилось слово «end»:
    # чистка ПОРТИЛА название вместо того, чтобы его очистить.
    eq(_clean_title("#вакансия #удаленно #back-end"), "",
       "строка из одних тегов пустеет — разбор идёт к следующей строке")
    eq(_clean_title("#node.js #remote Backend Developer"), "Backend Developer",
       "точка внутри тега не рвёт его пополам")
    eq(_clean_title("#dotNET_developer #Senior_C#"), "",
       "хвостовая решётка тега не остаётся мусором")


def test_tgvacancy_styled_unicode_title_survives():
    """Заголовок «жирным» математическим юникодом обязан свестись к буквам.

    «𝗕𝗮𝗰𝗸𝗲𝗻𝗱 𝗚𝗼 𝗗𝗲𝘃𝗲𝗹𝗼𝗽𝗲𝗿» — это НЕ ASCII, и `\\bgolang\\b` по нему не срабатывает
    никогда. Такой заголовок проходил бы мимо ATS_ROLE_RE, а shortlist.on_profile
    выбрасывал бы его как чужую профессию: вакансия исчезала бы, ни разу
    не показанная. Ровно тот сорт потери, от которого весь модуль и написан."""
    from .shortlist import on_profile
    from .tgvacancy import _clean_title

    styled = "𝗕𝗮𝗰𝗸𝗲𝗻𝗱 𝗚𝗼 𝗗𝗲𝘃𝗲𝗹𝗼𝗽𝗲𝗿"
    eq(on_profile(styled), False, "до нормализации роль не опознаётся (это и был баг)")
    eq(_clean_title(styled), "Backend Go Developer", "NFKC сводит начертание к буквам")
    eq(on_profile(_clean_title(styled)), True, "после — роль профильная")


def test_tg_flood_wait_is_waited_out_or_declared():
    """Короткий FloodWait пережидаем, длинный — объявляем, а не висим молча.

    Обход 28 чатов подряд без пауз ловит FloodWait почти гарантированно, и до
    этого он просто ронял чат в счётчик «упало». Правило проверяется без Telethon:
    тип исключения там, а решение — здесь."""
    from .tgclient import CHAT_PAUSE, FLOOD_WAIT_MAX, flood_wait_seconds

    class FloodWaitError(Exception):
        def __init__(self, seconds):
            self.seconds = seconds

    class OtherError(Exception):
        pass

    eq(flood_wait_seconds(FloodWaitError(30)), 30, "секунды берутся из исключения")
    eq(flood_wait_seconds(OtherError()), None, "чужая ошибка — не FloodWait")
    eq(flood_wait_seconds(FloodWaitError(0)), 0, "нулевое ожидание тоже FloodWait")
    eq(FLOOD_WAIT_MAX > 0 and CHAT_PAUSE > 0, True,
       "потолок ожидания и пауза между чатами заданы явно, а не «как получится»")
    # Потолок нужен именно как ПОТОЛОК: без него прогон висел бы столько,
    # сколько скажет Telegram, и выглядел бы зависшим.
    eq(FLOOD_WAIT_MAX <= 300, True,
       "ждать дольше пяти минут молча нельзя — это неотличимо от зависания")


def test_tg_watermark_is_monotonic_and_resumable():
    """Водяной знак двигается только вперёд и переживает падение прогона.

    Своя граница нужна потому, что read-state — состояние ЧЕЛОВЕКА: пользователь
    открыл канал с телефона, и прежняя выборка («непрочитанное») молча пропускала
    всё, что он пролистал."""
    import os.path
    import tempfile

    from . import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "wm.db")
        with store.connect(db) as conn:
            eq(store.tg_watermarks(conn), {}, "на пустой базе знаков нет")
            # Засев из файла отката: resume_from_id — последнее разобранное.
            seeded, skipped = store.seed_tg_watermarks(conn, {
                "a.txt": {"chat_id": "1001537669054", "title": "Go", "resume_from_id": 1518},
                "b.txt": {"chat_id": "1003861760454", "title": "BlockHire",
                          "resume_from_id": 281},
            })
            eq((seeded, skipped), (2, 0), "оба знака проставлены")
            eq(store.tg_watermarks(conn)["1001537669054"], 1518, "точка возобновления")
        # Прогон разобрал часть чата и упал: знак первого чата уже сохранён.
        with store.connect(db) as conn:
            store.set_tg_watermark(conn, "1001537669054", 1530, chat_title="Go")
        with store.connect(db) as conn:
            eq(store.tg_watermarks(conn)["1001537669054"], 1530,
               "прогресс упавшего прогона не потерян — следующий продолжит с 1530")
            # Повторный засев из того же файла НЕ отматывает набранное.
            seeded, skipped = store.seed_tg_watermarks(conn, {
                "a.txt": {"chat_id": "1001537669054", "title": "Go", "resume_from_id": 1518}})
            eq((seeded, skipped), (0, 1), "существующий знак пропущен, а не сброшен")
            eq(store.tg_watermarks(conn)["1001537669054"], 1530, "знак остался прежним")
            # Знак не откатывается назад и прямой записью меньшего значения:
            # иначе пустой проход по чату обнулил бы границу и следующий прогон
            # выкачал бы канал целиком.
            store.set_tg_watermark(conn, "1001537669054", 0, chat_title="Go")
            eq(store.tg_watermarks(conn)["1001537669054"], 1530,
               "запись меньшего значения знак не двигает")
            # Явный откат руками — можно.
            store.seed_tg_watermarks(conn, {
                "a.txt": {"chat_id": "1001537669054", "title": "Go",
                          "resume_from_id": 1518}}, force=True)
            eq(store.tg_watermarks(conn)["1001537669054"], 1518, "--force отматывает")


def test_tg_mirror_writes_nothing_without_explicit_apply():
    """Зеркало — единственный модуль, который ПИШЕТ в Telegram, и его границы
    проверяются кодом, а не обещанием в комментарии."""
    import inspect
    import os.path
    import re
    import tempfile

    from . import store, tgmirror

    src = inspect.getsource(tgmirror)
    # Разрешена ровно одна пишущая операция — пересылка уже существующего поста.
    for forbidden in ("send_message", "send_file", "edit_message", "delete_messages",
                      "CreateChannelRequest", "JoinChannelRequest", "click("):
        if forbidden in src:
            FAILS.append(f"tg-mirror: найдена запрещённая операция {forbidden!r} — "
                         f"модулю позволена только пересылка")
    if "forward_messages" not in src:
        FAILS.append("tg-mirror: пересылки нет вовсе")
    # Канал создаёт человек, а не скрипт: создание канала — действие от его имени.
    if "создаёт ПОЛЬЗОВАТЕЛЬ" not in src and "создаёт пользователь" not in src.lower():
        FAILS.append("tg-mirror: не сказано, что канал заводит пользователь")
    # Предпросмотр — умолчание: `apply` обязан быть False по умолчанию.
    sig = inspect.signature(tgmirror.run)
    if sig.parameters["apply"].default is not False:
        FAILS.append("tg-mirror: --apply должен быть выключен по умолчанию")

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "m.db")
        with store.connect(db) as conn:
            eq(store.mirrored(conn), set(), "на пустой базе зеркала нет")
            store.save_mirror(conn, "tg:chan", "42", "-1001", 7)
            eq(("tg:chan", "42") in store.mirrored(conn), True,
               "зеркалированное запомнено — второй раз не перешлётся")
            got = store.mirror_of(conn, "tg:chan", "42")
            eq((got["mirror_chat_id"], got["mirror_message_id"]), ("-1001", 7),
               "адрес копии сохранён рядом с вакансией")


def test_merge_collapses_identical_urls_even_without_company():
    """Одна и та же СТРАНИЦА — одна вакансия, даже когда работодатель не назван.

    Живой прогон 05.08.2026: пост `t.me/runello_rus_goland/1527` приехал тремя
    записями — из самого канала, из dreamoffer и из shadowhint (оба агрегатора
    перепечатывают телеграм-посты и отдают ТУ ЖЕ ссылку). Компании ни у одной нет,
    поэтому ни `dup_group`, ни SimHash их не склеивали, и в топ-30 одна вакансия
    занимала три строки из тридцати.

    Совпадение адреса — единственное доказательство дубля, которое не может
    ошибиться в опасную сторону: одна страница не бывает двумя грейдами."""
    from .shortlist import canonical_url, merge, same_url_key

    eq(same_url_key({"url": "https://t.me/ch/1/"}), "https://t.me/ch/1",
       "хвостовой слэш — не другая страница")
    eq(same_url_key({"url": "https://t.me/ch/1#apply"}), "https://t.me/ch/1",
       "якорь — не другая страница")
    eq(same_url_key({"url": ""}), None, "пустой адрес ключом не бывает")
    eq(same_url_key({"url": "не ссылка"}), None, "мусор ключом не бывает")

    # Метки перехода — не часть адреса вакансии. У LinkedIn они меняются при
    # КАЖДОМ обходе, поэтому одна вакансия из linkedin и из dreamoffer
    # не совпадала по адресу никогда: 733 несклеенные группы на живой базе.
    a = canonical_url("https://de.linkedin.com/jobs/view/software-engineer-4446635696"
                      "?position=28&pageNum=0&refId=XM3hux&trackingId=PTBnuk")
    b = canonical_url("https://de.linkedin.com/jobs/view/software-engineer-4446635696")
    eq(a, b, "трекинговые параметры вырезаны — это одна страница")
    eq(canonical_url("https://getmatch.ru/vacancies/35615-go?s=bot&utm_source=g_bot"),
       "https://getmatch.ru/vacancies/35615-go", "utm-метки вырезаны")
    eq(canonical_url("https://HH.ru/Vacancy/1"), "https://hh.ru/Vacancy/1",
       "схема и хост в нижний регистр, ПУТЬ — нет: он бывает регистрозависимым")
    eq(canonical_url("https://x.com/j?b=2&a=1"), "https://x.com/j?a=1&b=2",
       "порядок параметров не делает страницу другой")

    # 🔴 Неизвестный параметр СОХРАНЯЕТСЯ. Направление ошибки выбрано осознанно:
    # лишний раскол стоит одной строки в выдаче, а склейка двух разных вакансий
    # из-за срезанного идентификатора стоит вакансии.
    eq(canonical_url("https://x.com/jobs?jobId=111") !=
       canonical_url("https://x.com/jobs?jobId=222"), True,
       "незнакомый параметр не срезается — иначе разные вакансии слиплись бы")

    rows = [
        {"source": "dreamoffer", "external_id": "1353783", "company": None,
         "title": "Golang разработчик", "first_seen": "2026-08-05T08:16",
         "url": "https://t.me/runello_rus_goland/1527"},
        {"source": "tg:runello_rus_goland", "external_id": "1527", "company": None,
         "title": "Golang разработчик", "first_seen": "2026-08-05T08:17",
         "url": "https://t.me/runello_rus_goland/1527/"},
        {"source": "shadowhint", "external_id": "abc", "company": None,
         "title": "Golang разработчик", "first_seen": "2026-08-04T17:26",
         "url": "https://t.me/runello_rus_backend/4197"},
        {"source": "hh", "external_id": "9", "company": "Ozon",
         "title": "Go Developer", "first_seen": "2026-08-01",
         "url": "https://hh.ru/vacancy/9"},
    ]
    merged = merge(rows)
    eq(len(merged), 3, "три группы: два адреса t.me и один hh")
    same = [g for g in merged if len(g["_sources"]) > 1]
    eq(len(same), 1, "склеилась ровно одна группа")
    eq(sorted(same[0]["_sources"]), ["dreamoffer", "tg:runello_rus_goland"],
       "оба источника сохранены — видно, что вакансия подтверждена дважды")
    # РАЗНЫЕ адреса того же канала склеиваться не должны: это разные посты.
    other = [g for g in merged if g["_urls"] == ["https://t.me/runello_rus_backend/4197"]]
    eq(len(other), 1, "другой пост остался отдельной вакансией")


def test_merge_keeps_every_city_of_a_collapsed_group():
    """Схлопнули по городам — города обязаны выжить.

    Аудит живой базы 08.08.2026: дедуп прячет 1329 строк, и 1100 из них (82%) —
    это ОДНА вакансия одной компании в разных городах. adesso SE даёт «Software
    Engineer Defense» в 30 немецких городах, Bending Spoons — «Graduate software
    engineer» в 20 городах пяти стран. Ложных склеек РАЗНЫХ работодателей в базе
    нет ни одной (три подозрительные пары оказались одной компанией в двух
    написаниях: `1KOMMA5˚`/`1KOMMA5°`, `ГУ "Кызмат"`/`ГУ «Кызмат»`, `СБЕР`/`Сбер`),
    то есть инвариант «ошибаться в сторону разделения» держится.

    Терялось другое: канон группы выбирается по `first_seen`, и в таблицу попадал
    город случайной записи — Штральзунд при том, что та же вакансия открыта
    в Берлине. Решение «не поеду» принималось по факту, которого нет."""
    from .shortlist import merge, render

    rows = [
        {"source": "linkedin", "external_id": "1", "company": "adesso SE",
         "title": "Software Engineer Defense", "first_seen": "2026-08-01",
         "url": "https://x/1", "location": "Штральзунд"},
        {"source": "linkedin", "external_id": "2", "company": "adesso SE",
         "title": "Software Engineer Defense", "first_seen": "2026-08-02",
         "url": "https://x/2", "location": "Берлин"},
        {"source": "linkedin", "external_id": "3", "company": "adesso SE",
         "title": "Software Engineer Defense", "first_seen": "2026-08-03",
         "url": "https://x/3", "location": "Мюнхен"},
    ]
    merged = merge(rows)
    eq(len(merged), 1, "одна вакансия — одна группа")
    eq(merged[0]["_locations"], ["Штральзунд", "Берлин", "Мюнхен"],
       "все три города доехали до группы, а не только город канона")

    g = dict(merged[0], _score=None, _years=None, _rtw="", _worked=[])
    text = render({"rows": [g], "stats": {"groups": 1, "delta": 3, "off_profile": 0,
                                          "collapsed": 2, "with_years": 0,
                                          "worked": 0}})
    eq("+2" in text, True, "в таблице видно, что городов больше одного")
    for city in ("Берлин", "Мюнхен"):
        eq(city in text, True, f"город {city} назван в выдаче, а не съеден склейкой")


def test_simhash_dedup_never_merges_across_grades_or_companies():
    """Третий слой дедупа — по описаниям, и он не имеет права нарушить два
    правила, оплаченных потерянными вакансиями.

    Инцидент SumUp: «Backend Engineer - Cards» и «Senior Backend Engineer - Cards»
    слиплись в одну строку, и младшая позиция исчезла из выдачи совсем. Никакая
    похожесть описаний это правило не отменяет: у двух грейдов одной команды
    описания совпадают почти дословно — тем и опасны."""
    from .shortlist import grade_of, hamming, similar_groups, simhash

    text = ("Мы ищем инженера в команду платежей. Обязанности: разрабатывать "
            "микросервисы на Go, проектировать API, отвечать за надёжность. "
            "Требования: PostgreSQL, Kafka, Kubernetes, опыт highload. "
            "Условия: удалённо, ДМС, оплачиваемый отпуск, обучение. ") * 3
    other = ("Ищем аналитика данных. Обязанности: строить дашборды, считать "
             "метрики продукта, писать SQL. Требования: Python, Tableau, "
             "статистика. Условия: офис, гибкий график. ") * 3

    eq(simhash("") , 0, "пустой текст хэша не даёт")
    eq(hamming(simhash(text), simhash(text)), 0, "тот же текст — то же значение")
    eq(hamming(simhash(text), simhash(other)) > 3, True, "разные тексты далеки")

    eq(grade_of("Senior Backend Engineer"), "senior", "грейд из названия")
    eq(grade_of("Старший инженер"), "senior", "русский грейд — тот же грейд")
    eq(grade_of("Backend Engineer"), "", "грейд не назван")

    base = {"company": "SumUp", "description": text}
    # Один грейд, одна компания, одинаковые описания — это дубль.
    same = similar_groups([
        dict(base, source="hh", external_id="1", title="Senior Backend Engineer"),
        dict(base, source="habr", external_id="2", title="Старший Backend Engineer"),
    ])
    eq(len(same), 1, "одна вакансия на двух площадках склеивается")

    # РАЗНЫЕ грейды — разные вакансии, как бы ни совпадали описания.
    grades = similar_groups([
        dict(base, source="hh", external_id="1", title="Backend Engineer - Cards"),
        dict(base, source="hh", external_id="2",
             title="Senior Backend Engineer - Cards"),
    ])
    eq(grades, [], "разные грейды не склеиваются НИКОГДА — инцидент SumUp")

    # РАЗНЫЕ РОЛИ одной компании — тоже разные вакансии. Улов аудита `dups`
    # 08.08.2026 на живой базе: у Ebury «Senior Software Engineer (Payments)»
    # и «(Money Flows)» описаны одним корпоративным текстом, совпали на 95%+ и
    # слиплись; у Remitly так же слиплись «Software Development Engineer II
    # AppX» и «…Global Money Movement». Грейд у них ОДИН, компания одна — то
    # есть два прежних предохранителя тут не срабатывают вовсе. Это повтор
    # инцидента SumUp другим слоем: младшая позиция исчезает из выдачи.
    teams = similar_groups([
        {"company": "Ebury", "description": text, "source": "linkedin",
         "external_id": "1", "title": "Senior Software Engineer (Payments)"},
        {"company": "Ebury", "description": text, "source": "linkedin",
         "external_id": "2", "title": "Senior Software Engineer (Money Flows)"},
    ])
    eq(teams, [], "разные команды одной компании склеились — это потеря вакансии, "
                  "а похожесть описаний тут ничего не доказывает: текст корпоративный")

    # Обратная сторона правила: юридическая метка пола — это НЕ роль. TOPdesk
    # шлёт «Senior Infrastructure Engineer (m/f/d)*» из Германии и «Senior
    # Infrastructure Engineer» из Нидерландов — одна работа. Шаблон `m/f`
    # оставлял от «m/f/d» хвост «d», и пара расходилась по нему.
    marker = similar_groups([
        {"company": "TOPdesk", "description": text, "source": "adzuna",
         "external_id": "1", "title": "Senior Infrastructure Engineer (m/f/d)*"},
        {"company": "TOPdesk", "description": text, "source": "linkedin",
         "external_id": "2", "title": "Senior Infrastructure Engineer"},
    ])
    eq(len(marker), 1, "«(m/f/d)» — юридическая метка, а не название команды: "
                       "по ней разводить вакансии нельзя")

    # Разные компании не склеиваются, даже при дословно совпадающем описании
    # (агрегаторы перепечатывают один текст под разными нанимателями).
    companies = similar_groups([
        {"company": "Ozon", "description": text, "source": "hh",
         "external_id": "1", "title": "Backend Engineer"},
        {"company": "Wildberries", "description": text, "source": "hh",
         "external_id": "2", "title": "Backend Engineer"},
    ])
    eq(companies, [], "межкомпанейские склейки запрещены")

    # Нераскрытый работодатель — доказательства дубля нет вовсе.
    hidden = similar_groups([
        {"company": "", "description": text, "source": "hh", "external_id": "1",
         "title": "Backend Engineer"},
        {"company": "", "description": text, "source": "hh", "external_id": "2",
         "title": "Backend Engineer"},
    ])
    eq(hidden, [], "без работодателя не склеиваем: под «nda» прячутся десятки разных")

    # Короткое описание не основание: на трёх строках похож любой текст на любой.
    short = similar_groups([
        {"company": "SumUp", "description": "Ищем Go-разработчика.",
         "source": "hh", "external_id": "1", "title": "Backend Engineer"},
        {"company": "SumUp", "description": "Ищем Go-разработчика!",
         "source": "habr", "external_id": "2", "title": "Backend Engineer"},
    ])
    eq(short, [], "короткие описания не сравниваем")


def test_dup_decision_survives_and_respects_human():
    """Решение о дубле сохраняется, а решение ЧЕЛОВЕКА автоматика не перебивает."""
    import os.path
    import tempfile

    from . import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "d.db")
        with store.connect(db) as conn:
            store.save_dup_decision(conn, "hh:2", "habr:1", "same", reason="simhash 0.97")
            # Ключи симметричны: (a,b) и (b,a) — одно решение, а не два разных.
            got = store.dup_decision(conn, "habr:1", "hh:2")
            eq(got["verdict"], "same", "решение находится в любом порядке ключей")
            eq(got["key_a"], "habr:1", "пара хранится в устойчивом порядке")

            store.save_dup_decision(conn, "hh:2", "habr:1", "different",
                                    reason="разные команды", by="human")
            eq(store.dup_decision(conn, "hh:2", "habr:1")["verdict"], "different",
               "человек переопределил автоматику")
            store.save_dup_decision(conn, "hh:2", "habr:1", "same",
                                    reason="simhash 0.97", by="auto")
            eq(store.dup_decision(conn, "hh:2", "habr:1")["verdict"], "different",
               "следующий прогон НЕ отменяет правку человека молча")


def test_other_language_penalty_reads_the_body_too():
    """Роль с нейтральным заголовком и чужим языком в ТЕЛЕ обязана понижаться.

    Штраф смотрел только в заголовок, и «Backend Developer», где по всему тексту
    Java, а Go — одно «будет плюсом», не понижался вовсе. Решение принято
    осознанно и слабее заголовочного: упоминание чужого языка ещё не делает роль
    чужой, штраф даётся только при ЯВНОМ преобладании."""
    from .shortlist import _dominant_other_lang, match_score

    eq(_dominant_other_lang("Java Java Java Spring Java. Знание Go будет плюсом.")[0],
       "Java", "Java вчетверо чаще Go — это язык роли")
    eq(_dominant_other_lang("Go, Golang, микросервисы на Go. Есть Java-сервисы.")[0],
       None, "одно упоминание Java при трёх Go — часть стека, а не язык роли")
    eq(_dominant_other_lang("Go Go Go Kubernetes Postgres")[0], None,
       "чужого языка нет вовсе")
    eq(_dominant_other_lang("")[0], None, "пустой текст вердикта не даёт")

    row = {"title": "Backend Developer"}
    java = {"title": "Backend Developer",
            "description": "Java Java Spring Boot Java Hibernate Java. Kubernetes, "
                           "PostgreSQL, Kafka. Знание Go будет плюсом. " * 3}
    go = {"title": "Backend Developer",
          "description": "Go microservices, Golang, Kubernetes, PostgreSQL, Kafka, "
                         "gRPC. Есть немного Java. " * 3}
    java_score, java_why = match_score(row, java)
    go_score, _ = match_score(row, go)
    eq(java_score < go_score, True,
       "Java-first роль ниже Go-роли при одинаковом заголовке и стеке")
    eq("преобладает Java" in java_why, True, "причина понижения названа словами")


def test_company_name_is_not_a_domain():
    """Название компании — НЕ домен, и выдавать его за домен нельзя.

    Живой случай 08.08.2026: `scout channel "P&P Solutions"` собирал из имени
    «домен» p&p solutions, проверка на агрегатор отвечала «нет», и человек
    читал «p&p solutions — агрегатор, а не работодатель». Уверенный неверный
    вердикт, и ровно на самом ценном пути: поиск контакта БЛИЖЕ к работодателю
    — то, ради чего вообще заводились входы на площадки.

    Правильный ответ здесь «не знаю, передай --site»: догадка о домене по
    названию однажды приведёт письмо в чужую компанию.
    """
    from .channel import domain_of

    for junk in ("P&P Solutions", "ООО Ромашка", "Sp. z o.o.", "tinkoff",
                 "localhost", "", "  ", "Яндекс Технологии"):
        eq(domain_of(junk), "",
           f"{junk!r} принято за домен — дальше это уедет в вердикт о компании")

    # Настоящие домены не пострадали: правило должно резать мусор, а не выдачу.
    eq(domain_of("https://www.p-p.pl/careers"), "p-p.pl", "домен из URL потерян")
    eq(domain_of("ozon.ru"), "ozon.ru", "голый домен потерян")
    eq(domain_of("sub.ozon.ru:443"), "sub.ozon.ru", "поддомен с портом потерян")
    eq(domain_of("careers.job-boards.greenhouse.io"),
       "careers.job-boards.greenhouse.io", "домен с дефисами потерян")


def test_channel_probe_cap_keeps_the_likeliest_candidates():
    """Потолок зондов не должен срезать самых вероятных кандидатов.

    Порядок был «все поддомены со всеми путями, потом пути основного домена»,
    и первые 24 адреса приходились на поддомены: любой потолок ниже 25 отрезал
    бы `example.com/careers` целиком — самый частый случай из всех. Обрезка,
    съедающая наиболее вероятного кандидата, — тихая потеря, а не экономия."""
    from .channel import MAX_PROBES, candidates

    urls = candidates("example.com")
    probed = urls[:MAX_PROBES]
    eq("https://example.com/careers" in probed, True,
       "главный кандидат «/careers» обязан попасть под потолок")
    eq("https://example.com/vacancies" in probed, True, "и «/vacancies» тоже")
    eq("https://career.example.com/" in probed, True, "и корень карьерного поддомена")
    eq(len(urls) > MAX_PROBES, True, "потолок реально что-то отсекает")
    # Отсекается только хвост: контакты и www-дубли.
    cut = urls[MAX_PROBES:]
    eq(any(u.endswith("example.com/") for u in cut), True,
       "главная (источник почты, а не вакансий) уходит в хвост")


def test_detail_cascade_names_the_layer_it_used():
    """JSON-LD → CSS → текст, и `notes` обязаны говорить, каким слоем взято.

    Выжимка читается как факт о вакансии, и «это площадка объявила описанием
    в своей разметке» против «это мы выскребли из HTML вместе с меню» —
    утверждения разной цены."""
    from .detail import _cascade_description

    ld = ('<html><head><script type="application/ld+json">'
          '{"@type":"JobPosting","title":"Senior Go Engineer","datePosted":"2026-08-01",'
          '"hiringOrganization":{"name":"Acme"},'
          '"jobLocation":{"address":{"addressLocality":"Berlin","addressCountry":"DE"}},'
          '"description":"<p>Requirements: 5+ years of Go. Kubernetes, PostgreSQL, '
          'Kafka. You will build distributed systems and own them end to end.</p>"}'
          '</script></head><body><nav>Menu Home About</nav></body></html>')
    desc, layer, fields = _cascade_description(ld)
    eq(layer, "json-ld", "разметка площадки — первый слой")
    eq(fields["company"], "Acme", "работодатель из LD")
    eq(fields["location"], "Berlin, DE", "локация из LD")
    eq(fields["published_at"], "2026-08-01", "дата публикации из LD")
    eq("Menu Home About" in desc, False, "навигация в описание не попала")

    css = ('<html><body><nav>Menu</nav><div class="job-description"><p>'
           + "Requirements: strong Go and Kubernetes. " * 8 +
           '</p></div></body></html>')
    _, layer, _ = _cascade_description(css)
    eq(layer, "css", "знакомый контейнер — второй слой")

    plain = "<html><body><p>" + ("some page text " * 40) + "</p></body></html>"
    desc, layer, _ = _cascade_description(plain)
    eq((desc, layer), (None, None),
       "ни LD, ни контейнера — каскад уступает текстовому разбору")

    # Заглушка вместо описания не должна выдаваться за описание, но поля LD
    # (работодатель!) при этом терять нельзя.
    stub = ('<html><head><script type="application/ld+json">'
            '{"@type":"JobPosting","description":"See website",'
            '"hiringOrganization":{"name":"Acme"}}</script></head><body>x</body></html>')
    desc, layer, fields = _cascade_description(stub)
    eq(layer, None, "«See website» — не описание")
    eq(fields["company"], "Acme", "но работодателя из LD мы всё равно забрали")


def test_apply_options_prefer_direct_and_are_stable():
    """Маршрут отклика: прямой канал бьёт витрину, выбор устойчив между прогонами.

    Два маршрута одного ранга (страница вакансии и страница компании на том же
    агрегаторе) различались только порядком строк в SQL — то есть «лучший
    маршрут» менялся от прогона к прогону на одних и тех же данных."""
    from .applyopt import ATS, EMPLOYER, best, classify, gather

    eq(classify("https://boards.greenhouse.io/acme/jobs/123"), (ATS, True),
       "ATS работодателя — прямой канал: отклик идёт в его воронку")
    eq(classify("https://acme.com/careers/go-dev"), (EMPLOYER, True),
       "сайт компании — прямой")
    eq(classify("https://hh.ru/vacancy/123")[1], False, "витрина — не прямой")
    eq(classify("https://t.me/c/123/45")[1], False, "телеграм — не прямой")

    opts = gather({"employer_url": "https://boards.greenhouse.io/acme/jobs/1",
                   "url": "https://hh.ru/vacancy/999", "raw": None})
    eq(best(opts), "https://boards.greenhouse.io/acme/jobs/1",
       "прямой канал выигрывает у витрины, даже если витрина удобнее")

    # Обе ссылки — агрегатор одного ранга: побеждает та, что ведёт на ВАКАНСИЮ.
    same_rank = gather({
        "employer_url": "https://getmatch.ru/companies/x5-tech",
        "url": "https://t.me/c/1/2",
        "raw": {"links": ["https://getmatch.ru/vacancies/35615-senior-go-developer"]}})
    eq(best(same_rank), "https://getmatch.ru/vacancies/35615-senior-go-developer",
       "страница вакансии, а не витрина компании — с неё можно откликнуться")

    # Устойчивость: перемешанный список даёт тот же ответ.
    shuffled = list(reversed(same_rank))
    eq(best(shuffled), best(same_rank),
       "порядок строк не меняет выбор — иначе он менялся бы от прогона к прогону")


def test_raw_cache_roundtrip_and_scoping():
    """Кэш сырых ответов: ключ включает url, иначе пагинация затирает сама себя."""
    import os.path
    import tempfile

    from . import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "rc.db")
        with store.connect(db) as conn:
            eq(store.raw_cache_get(conn, "hh", "u1"), None, "пустой кэш")
            store.raw_cache_put(conn, "hh", "https://hh.ru/p1", "<page1>")
            store.raw_cache_put(conn, "hh", "https://hh.ru/p2", "<page2>")
            eq(store.raw_cache_get(conn, "hh", "https://hh.ru/p1"), "<page1>",
               "первая страница на месте")
            eq(store.raw_cache_get(conn, "hh", "https://hh.ru/p2"), "<page2>",
               "вторая её НЕ затёрла: без url в ключе кэш терял бы всю пагинацию")
            eq(store.raw_cache_get(conn, "habr", "https://hh.ru/p1"), None,
               "чужой источник в кэш hh не заглядывает")
            eq(store.raw_cache_get(conn, "hh", "https://hh.ru/p1", on="2020-01-01"),
               None, "вчерашний кэш сегодняшним не считается")
            eq(store.raw_cache_stats(conn)["pages"], 2, "счётчик страниц")


def test_research_cache_never_erases_known_facts():
    """Волна, выяснившая только живость, не должна стирать раскрытого
    работодателя, добытого прошлой волной."""
    import os.path
    import tempfile

    from . import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "r.db")
        with store.connect(db) as conn:
            store.save_research(conn, "hirehi", "42",
                                employer_revealed="Ozon", evidence="почта @ozon.ru")
            store.save_research(conn, "hirehi", "42", liveness="alive")
            got = store.research(conn, "hirehi", "42")
            eq(got["employer_revealed"], "Ozon",
               "раскрытый работодатель пережил вторую запись")
            eq(got["liveness"], "alive", "новый факт добавился")
            eq(got["evidence"], "почта @ozon.ru", "подтверждение не потеряно")
            store.save_research(conn, "hirehi", "42", employer_revealed="Ozon Tech")
            eq(store.research(conn, "hirehi", "42")["employer_revealed"], "Ozon Tech",
               "явное новое значение перезаписывает — это не то же, что None")


def test_hh_api_rows_map_fields_and_never_invent_period():
    """Разбор ответа официального API hh.

    Поля у API ДРУГИЕ, чем во встроенном стейте страницы (`id` вместо
    `vacancyId`, `salary` вместо `compensation`, `employer` вместо `company`),
    и перепутать их — значит собрать вакансию с пустыми деньгами и без
    работодателя, не заметив этого: исключения тут не будет."""
    from .sources import Tally, _hh_api_rows

    items = [
        {"id": "123456", "name": "Senior Go Developer",
         "alternate_url": "https://hh.ru/vacancy/123456",
         "area": {"id": "1", "name": "Москва"},
         "salary": {"from": 350000, "to": 450000, "currency": "RUR", "gross": False},
         "employer": {"id": "777", "name": "X5 Tech",
                      "alternate_url": "https://hh.ru/employer/777"},
         "published_at": "2026-08-04T10:00:00+0300",
         "created_at": "2026-08-01T10:00:00+0300",
         "schedule": {"id": "remote", "name": "Удалённая работа"},
         "experience": {"id": "between3And6"},
         "employment": {"id": "full"},
         "professional_roles": [{"id": "96", "name": "Программист"}],
         "snippet": {"requirement": "Опыт Go от 4 лет.",
                     "responsibility": "Разрабатывать микросервисы."}},
        # Вилки нет — период обязан остаться пустым, а не стать «месяцем».
        {"id": "222", "name": "Backend Engineer",
         "alternate_url": "https://hh.ru/vacancy/222",
         "area": {"name": "Санкт-Петербург"},
         "salary": None, "employer": {"name": "Ozon"},
         "published_at": "2026-08-03T09:00:00+0300"},
        # Дубль по id: три формулировки запроса пересекаются, это НЕ потеря.
        {"id": "123456", "name": "Senior Go Developer",
         "alternate_url": "https://hh.ru/vacancy/123456",
         "employer": {"name": "X5 Tech"}},
        # Без id собрать адрес нечем — такую строку честно роняем в счётчик.
        {"name": "Сломанная строка", "employer": {"name": "X"}},
    ]
    out, seen, tally = [], set(), Tally("hh")
    _hh_api_rows(items, "Golang", out, seen, tally)

    eq(len(out), 2, "две настоящие вакансии")
    eq((tally.dupes, tally.dropped), (1, 1), "дубль и битая строка посчитаны отдельно")
    v = out[0]
    eq(v.source, "hh", "источник")
    eq(v.external_id, "123456", "id из поля `id`, а не `vacancyId`")
    eq(v.title, "Senior Go Developer", "название")
    eq(v.company, "X5 Tech", "работодатель из employer.name")
    eq((v.salary_from, v.salary_to, v.currency), (350000, 450000, "RUB"),
       "вилка из salary, RUR нормализован в RUB")
    eq(v.salary_period, "month", "hh отдаёт вилку помесячно")
    eq(v.remote, True, "schedule=remote → удалёнка")
    eq(v.location, "Москва", "регион")
    eq(v.employer_url, "https://hh.ru/employer/777",
       "прямая ссылка на работодателя — в стейте страницы её нет вовсе")
    eq(v.raw.get("experience"), "between3And6",
       "бакет опыта сохранён: по нему hh режет резюме автофильтром")
    eq(v.published_at[:10], "2026-08-04", "дата публикации")
    eq("Опыт Go от 4 лет" in (v.description or ""), True, "сниппет требований")

    eq(out[1].salary_period, None,
       "вилки нет — периода тоже нет; «месяц по умолчанию» это выдумка")


def test_hh_api_needs_token_not_just_keys():
    """Ключи включают API только вместе с пользовательским токеном.

    Ровно на этом различии ломался переход: ключи в .auth/hh.env есть, токена
    нет — и источник уходил в API, получая 403 на каждой странице. Тихий ноль
    вместо выдачи дороже любого падения, поэтому решение принимает usable(),
    а не configured()."""
    from . import hhapi

    eq(hhapi.configured({}), False, "пустой env — ключей нет")
    eq(hhapi.configured({"HH_CLIENT_ID": "x"}), False,
       "половины ключей мало: без секрета токен не получить")
    eq(hhapi.configured({"HH_CLIENT_ID": "x", "HH_CLIENT_SECRET": "y"}), True,
       "оба ключа — ключи на месте")

    keys = {"HH_CLIENT_ID": "x", "HH_CLIENT_SECRET": "y"}
    saved = hhapi.read_token
    try:
        hhapi.read_token = lambda: {}
        eq(hhapi.usable(keys), False, "ключи без токена — API НЕ включается")
        hhapi.read_token = lambda: {"refresh_token": "r"}
        eq(hhapi.usable(keys), True, "протухший access при живом refresh — включается")
        hhapi.read_token = lambda: {"access_token": "a",
                                    "expires_at": time.time() + 86400}
        eq(hhapi.usable(keys), True, "живой access — включается")
        hhapi.read_token = lambda: {"access_token": "a", "expires_at": 0}
        eq(hhapi.usable(keys), False, "мёртвый access без refresh — не включается")
    finally:
        hhapi.read_token = saved

    eq("hh-auth" in hhapi.HOWTO, True,
       "инструкция ведёт в команду входа, а не в кабинет разработчика: "
       "своё приложение на dev.hh.ru не выдают")
    import inspect
    import re
    src = inspect.getsource(hhapi)
    eq(re.search(r"HH_CLIENT_ID\s*=\s*['\"][A-Z0-9]{10,}", src) is None, True,
       "зашитых ключей в модуле нет — только в .auth/hh.env, который не в git")


def test_habr_api_row_maps_fields_and_keeps_date_semantics():
    """Строка фронтового JSON Хабра → Vacancy.

    Дата обязана лечь в updated_at: на карточке Хабра стоит дата ПОДНЯТИЯ, а не
    публикации (карточка показывала 30.07 при datePosted 09.07 на самой
    странице). Разбор HTML кладёт её именно так, и путь через API не имеет права
    решить иначе — иначе одна и та же вакансия датируется по-разному в
    зависимости от того, каким путём приехала."""
    from .sources import Ctx, _habr_api_rows, _cutoff

    row = {
        "id": 1000164714, "href": "/vacancies/1000164714",
        "title": "Golang developer", "qualification": "Senior",
        "company": {"title": "RWB", "alias_name": "rwb"},
        "salary": {"from": 300000, "to": 490000, "currency": "rur",
                   "formatted": "от 300 000 до 490 000 ₽"},
        "publishedDate": {"date": _fresh(2), "title": "5 августа"},
        "remoteWork": True,
        "locations": [{"title": "Москва"}],
        "skills": [{"title": "PostgreSQL"}, {"title": "Kubernetes"}],
    }
    out, tally = [], _tally("habr")
    fresh = _habr_api_rows([row], "Golang", _cutoff(7), out, set(), tally)
    eq(fresh, 1, "строка внутри окна посчитана")
    v = out[0]
    eq((v.title, v.company, v.location), ("Golang developer", "RWB", "Москва"),
       "название, работодатель, город")
    eq((v.salary_from, v.salary_to, v.currency), (300000, 490000, "RUB"),
       "вилка числами, валюта нормализована из «rur»")
    eq(v.url, "https://career.habr.com/vacancies/1000164714", "ссылка собрана из href")
    eq(v.remote, True, "remoteWork → remote")
    eq(bool(v.updated_at) and v.published_at is None, True,
       "дата поднятия ложится в updated_at, published_at пуст")
    eq(v.tags[:2], ["Senior", "PostgreSQL"], "грейд впереди навыков")

    old = dict(row, id=2, href="/vacancies/2",
               publishedDate={"date": "2020-01-01T00:00:00+03:00"})
    out2, t2 = [], _tally("habr")
    eq(_habr_api_rows([old], "Golang", _cutoff(7), out2, set(), t2), 0,
       "старая строка в окно не попала")
    eq((len(out2), t2.skipped_old), (0, 1), "и посчитана как старая, а не потеряна")


def test_trudvsem_maps_registry_fields_and_zero_salary():
    """Работа России: контакт нанимателя сохраняется, ноль в вилке — не зарплата.

    Ноль здесь означает «не указано». Записать его числом значит показать в
    отчёте «0–0 ₽» и отсортировать вакансию как самую дешёвую — враньё, которое
    выглядит как данные."""
    from .sources import Ctx, _trudvsem_rows, _cutoff, _num_or_none

    eq((_num_or_none("150000"), _num_or_none("0"), _num_or_none(None),
        _num_or_none("не указано")), (150000, None, None, None),
       "строка → число, ноль и мусор → None")

    row = {"vacancy": {
        "id": "b92ea548", "vac_url": "https://trudvsem.ru/vacancy/card/1/b92ea548",
        "job-name": "Golang-разработчик", "creation-date": _fresh(24)[:10],
        "salary_min": "150000", "salary_max": "0", "currency": "«руб.»",
        "region": {"name": "Тверская область"},
        "company": {"name": "ООО Ромашка", "email": "hr@example.com",
                    "inn": "6952006840", "hr-agency": False},
        "duty": "Писать сервисы на Go"}}
    out, tally = [], _tally("trudvsem")
    _trudvsem_rows([row], "Golang", _cutoff(7), Ctx(query="Golang"), out, set(), tally)
    eq(len(out), 1, "вакансия записана")
    v = out[0]
    eq((v.salary_from, v.salary_to, v.currency), (150000, None, "RUB"),
       "верхняя граница «0» — это отсутствие границы")
    eq(v.raw["contact"]["email"], "hr@example.com",
       "контакт нанимателя из реестра сохранён — ради него источник и взят")
    eq(v.location, "Тверская область", "регион")

    junk = {"vacancy": dict(row["vacancy"], id="x2", **{"job-name": "Дворник"})}
    out2, t2 = [], _tally("trudvsem")
    _trudvsem_rows([junk], "Golang", _cutoff(7), Ctx(query="Golang"), out2, set(), t2)
    eq((len(out2), t2.skipped_profile), (0, 1),
       "не наша профессия отсеяна фильтром и посчитана, а не выброшена молча")


def _tally(source: str):
    from .sources import Tally
    return Tally(source)


def test_hh_source_picks_api_only_with_token():
    """Развилка src_hh: API — только когда есть токен, иначе разбор HTML.

    Пин нужен именно на развилку. Ключи в .auth/ есть всегда, а токен протухает,
    и «ушёл в API без токена» выглядит как 403 на каждой странице — то есть как
    пустая выдача, а не как поломка."""
    from . import hhapi as A
    from . import sources as S

    real = (A.usable, S.src_hh_api, S._src_hh_html)
    try:
        S.src_hh_api = lambda ctx: ["API"]
        S._src_hh_html = lambda ctx: ["HTML"]
        A.usable = lambda env=None: True
        eq(S.src_hh(S.Ctx(query="Go")), ["API"], "токен есть — идём в API")
        A.usable = lambda env=None: False
        eq(S.src_hh(S.Ctx(query="Go")), ["HTML"], "токена нет — разбор HTML")
    finally:
        A.usable, S.src_hh_api, S._src_hh_html = real


def test_hh_negotiations_from_api_match_html_shape():
    """Отклик из API кладётся в ту же форму, что даёт разбор кабинета.

    И главное: state=response различается на «просмотрен»/«не просмотрен» по
    viewed_by_opponent. Без этого переход на API молча схлопнул бы все
    «не просмотрен» в одно состояние — а это ровно то, по чему видно, дошёл
    отклик до человека или лежит мёртвым."""
    from .hhsync import item_from_api

    vac = {"id": "99", "name": "Go Developer",
           "alternate_url": "https://hh.ru/vacancy/99",
           "employer": {"name": "ACME"}}
    base = {"id": "1", "updated_at": "2026-08-01T12:33:00+0300", "vacancy": vac}

    it = item_from_api({**base, "state": {"id": "discard", "name": "Отказ"}})
    eq(it["status"], "rejection", "отказ")
    eq(it["date"], "2026-08-01", "дата — ISO, без времени и таймзоны")
    eq(it["url"], "https://hh.ru/vacancy/99", "ссылка на вакансию")
    eq(it["company"], "ACME", "работодатель")

    seen = item_from_api({**base, "state": {"id": "response", "name": "Отклик"},
                          "viewed_by_opponent": True})
    unseen = item_from_api({**base, "state": {"id": "response", "name": "Отклик"},
                            "viewed_by_opponent": False})
    eq((seen["status"], unseen["status"]), ("viewed", "not_viewed"),
       "response различается по viewed_by_opponent")

    inv = item_from_api({**base, "state": {"id": "invitation", "name": "Приглашение"}})
    eq(inv["status"], "invitation", "приглашение")

    bare = item_from_api({"state": {}, "vacancy": {}})
    eq((bare["title"], bare["company"], bare["date"]),
       ("(без названия)", None, None),
       "пустой элемент не роняет разбор и не выдумывает полей")


def test_budget_estimates_and_refuses_to_understate():
    """Смета обязана считать кириллицу дороже латиницы и давать ненулевой код,
    когда волна не влезает.

    Потолок, о превышении которого узнают постфактум, — не потолок, а пожелание.
    Формула «символы/4» из задания честна для латиницы, но занижает русский текст
    почти вдвое: смета на наполовину русской выдаче показала бы половину расхода."""
    from .budget import CARD_TOKENS, WAVE_CAP, naive_tokens, render, tokens

    lat = "a" * 400
    cyr = "я" * 400
    eq(naive_tokens(lat), 100, "латиница: символы/4")
    eq(tokens(lat), 100, "латиница считается так же")
    eq(tokens(cyr) > naive_tokens(cyr), True,
       "кириллица дороже, чем символы/4 — иначе смета врёт в свою пользу")
    eq(tokens(cyr), 200, "кириллица ~2 символа на токен")

    # Вердикт и подсказка. Модель читает первую строку и решает, что делать.
    m = {"days": 3, "top": 90, "brief_n": 90, "cards": 90, "delta": 900,
         "groups": 400, "off_profile": 500, "no_text": 12, "no_channel": 30,
         "wave_tokens": 9000, "wave_chars": 8000, "wave_naive": 2000,
         "brief_tokens": 500_000, "brief_chars": 1_000_000, "brief_naive": 250_000,
         "brief_per_row": 5555, "cards_tokens": 90 * CARD_TOKENS, "overhead": 40_000,
         "total": 630_000, "cap": WAVE_CAP, "fits": False, "rows": []}
    text = render(m, suggest=25)
    eq("НЕ ВЛЕЗАЕТ" in text, True, "перерасход назван прямо")
    eq("--top 25" in text, True,
       "сказано, СКОЛЬКО влезает: «не влезает» без числа заставляет подбирать вручную")
    m2 = dict(m, total=81_000, fits=True)
    eq("ВЛЕЗАЕТ" in render(m2), True, "укладывающаяся волна тоже получает вердикт")

    # Смета считает вывод СБОРЩИКА и обязана сказать это вслух: иначе её примут
    # за оценку всей волны вместе с рассуждениями модели.
    eq("Рассуждения модели сюда не входят" in text, True,
       "граница сметы названа явно")


def test_source_health_catches_silent_degradation():
    """Площадка отдала втрое меньше обычного — это авария, а не «ok».

    Покрытие честно ВНУТРИ прогона: видно, кто упал и кого закрыла стена. Но если
    hh начнёт отдавать 3 вакансии вместо 300, статус останется `ok` — площадка
    ответила, парсер отработал, ошибки нет. «Тихо деградировал» — родной брат
    «тихо потерял»."""
    from .health import assess, history, verdict

    # Ноль после стабильных сотен — всегда авария, без всяких порогов.
    v = verdict(0, [300, 280, 310, 295, 305])
    eq(v[0] if v else None, "АВАРИЯ", "ноль после сотен — поломка, а не «вакансий нет»")
    # Падение втрое.
    v = verdict(3, [300, 280, 310])
    eq(v[0] if v else None, "ДЕГРАДАЦИЯ", "3 вместо ~300")
    # Всплеск: обычно это сломанный дедуп, а не удача.
    v = verdict(4000, [300, 280, 310])
    eq(v[0] if v else None, "ВСПЛЕСК", "рост в 13× — сигнал, а не радость")
    # Норма молчит.
    eq(verdict(290, [300, 280, 310]), None, "обычные колебания — не отклонение")
    # Мелкий источник не тревожим: разница между 3 и 1 — шум, а не падение.
    eq(verdict(1, [3, 2, 3]), None, "источник ниже MIN_BASE судится только по нулю")
    v = verdict(0, [3, 2, 3])
    eq(v[0] if v else None, "АВАРИЯ", "но ноль у мелкого — всё равно авария")
    # Сравнивать не с чем — молчим, а не выдумываем вердикт.
    eq(verdict(0, []), None, "первый прогон источника вердикта не получает")

    # Медиана, а не среднее: один провал в истории не должен прятать следующий.
    # Среднее по [300, 0, 300] = 200 и падение до 60 сочло бы нормой; медиана 300.
    v = verdict(60, [300, 0, 300])
    eq(v[0] if v else None, "ДЕГРАДАЦИЯ", "медиана устойчива к одиночному провалу")

    import os.path
    import tempfile

    from . import store

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "h.db")
        with store.connect(db) as conn:
            for found in (300, 290, 310):
                rid = store.start_run(conn, "Golang", {})
                store.record_source(conn, rid, "hh", "ok", found=found)
                # Прогон, где источник упал, базой сравнения быть не может:
                # иначе «раньше было ноль» и деградация не заметится никогда.
                store.record_source(conn, rid, "habr", "error", found=0)
                store.finish_run(conn, rid)
            rid = store.start_run(conn, "Golang", {})
            eq(history(conn, rid)["hh"], [310, 290, 300], "история свежими вперёд")
            eq("habr" in history(conn, rid), False,
               "упавшие прогоны в базу сравнения не идут")
            rows = assess(conn, rid, [{"source": "hh", "status": "ok", "found": 4}])
            eq(len(rows), 1, "отклонение найдено")
            eq(rows[0]["label"], "ДЕГРАДАЦИЯ", "hh отдал 4 вместо ~300")
            eq(assess(conn, rid, [{"source": "hh", "status": "ok", "found": 295}]), [],
               "норма отклонением не считается")


def test_channel_render_fires_on_shells_not_only_on_emptiness():
    """`--render` обязан срабатывать ИМЕННО на каркасе SPA и стене.

    Флаг для того и заведён («добрать браузером, если stdlib увидел каркас»), а
    условие пускало рендер только при ПОЛНОСТЬЮ пустом списке находок. Каркас и
    антибот при этом кладутся в находки — с пометкой «добери --render». Итог
    08.08.2026: `channel --render` для polydev, Авито и Joom честно печатал
    совет добрать рендером ровно там, где рендер и был запрошен, и не делал
    ничего. Три канала найма остались ненайденными при работающем браузере."""
    from . import channel as C

    calls: list[list[str]] = []

    def fake_probe(url, *, timeout=12):
        # stdlib везде видит только каркас — как на живых SPA-сайтах.
        return {"url": url, "status": "КАРКАС SPA", "ats": None, "mails": [],
                "has_jobs": None, "contact_page": False, "why": "каркас"}

    def fake_rendered_many(urls, *, wait=3.0):
        calls.append(list(urls))
        return [{"url": urls[0], "status": "ok", "ats": None, "mails": [],
                 "has_jobs": True, "why": "есть признаки вакансий"}]

    old_probe, old_render = C.probe, C.probe_rendered_many
    C.probe, C.probe_rendered_many = fake_probe, fake_rendered_many
    try:
        res = C.find("Acme", domain="acme.com", render=True)
    finally:
        C.probe, C.probe_rendered_many = old_probe, old_render

    if not calls:
        FAILS.append("--render не дошёл до браузера: находки-каркасы приняты "
                     "за результат, хотя каждая просит добрать рендером")
    if not any(h.get("status") == "ok" for h in res.get("hits", [])):
        FAILS.append("рендер отработал, но настоящая находка не попала в hits")

    # Обратная сторона: рендер сходил и НИЧЕГО не добавил (живой случай — Авито
    # за Cloudflare не отдаётся и браузеру). Сказать «найдено рендером» поверх
    # ненайденного — соврать в отчёте о полноте обхода.
    def empty_rendered(urls, *, wait=3.0):
        return [None for _ in urls]

    C.probe, C.probe_rendered_many = fake_probe, empty_rendered
    try:
        res2 = C.find("Acme", domain="acme.com", render=True)
    finally:
        C.probe, C.probe_rendered_many = old_probe, old_render
    if "найдено рендером" in (res2.get("note") or ""):
        FAILS.append("рендер ничего не добавил, а в отчёте стоит «найдено "
                     "рендером» — это ложный успех")

    # Третья сторона: рендер обязан смотреть КОРЕНЬ домена и страницу контактов.
    # У компании без карьерного раздела почта найма лежит на главной, и
    # перебор одних лишь /vacancies, /careers, career.<домен> проходил мимо
    # (живой счёт 09.08.2026: у Remoby — рекламная платформа на Кипре — есть
    # только info@remoby.com на главной, и `channel --render` отрапортовал
    # «страница закрыта проверкой, нужен заход человека» вместо контакта).
    probed = calls[0] if calls else []
    # Сравнение ТОЧНОЕ, не по подстроке: «https://acme.com/» входит в
    # «https://acme.com/vacancies», и проверка на вхождение зеленела бы при
    # начисто отсутствующем корне (поймано нарочной поломкой при написании).
    if "https://acme.com/" not in probed:
        FAILS.append("рендер не смотрит корень домена — почта с главной "
                     "страницы недостижима")
    if not any("contact" in u for u in probed):
        FAILS.append("рендер не смотрит страницу контактов")


def test_channel_probe_logic():
    """Зондирование канала найма: агрегатор — не работодатель, каркас SPA —
    не «раздела нет», ATS важнее страницы с почтой."""
    from .channel import best, candidates, domain_of, is_employer_domain, looks_like_shell

    eq(domain_of("https://www.selectel.ru/careers/"), "selectel.ru", "домен из URL")
    eq(domain_of(None), "", "пустой URL — пустой домен")
    eq(is_employer_domain("selectel.ru"), True, "домен работодателя")
    eq(is_employer_domain("hh.ru"), False, "агрегатор каналом не считается")
    eq(is_employer_domain("spb.hh.ru"), False, "поддомен агрегатора тоже")
    # Мессенджеры и соцсети: у них есть каналы вакансий, и площадки отдают такую
    # ссылку в employer_url. Правило есть в field-notes, а в коде его не было —
    # и «max.ru/vacancies» лёг в кэш каналом найма Teleport (08.08.2026).
    for dom in ("max.ru", "vk.com", "ok.ru"):
        eq(is_employer_domain(dom), False,
           f"{dom} — витрина с каналами вакансий, а не сайт работодателя")

    cands = candidates("example.com")
    eq(bool("https://career.example.com/" in cands), True, "поддомен career проверяется")
    eq(bool("https://example.com/vacancies" in cands), True, "путь /vacancies проверяется")
    # Живой случай «Фланта»: карьерный сайт на job.<домен>, а список вакансий —
    # на job.<домен>/vacancies/. Зонд по одному корню поддомена его терял.
    eq(bool("https://job.example.com/vacancies/" in cands), True,
       "поддомен проверяется вместе с путями, а не только корнем")
    eq(len(cands), len(set(cands)), "кандидаты не повторяются")

    eq(looks_like_shell('<html><div id="__nuxt"></div></html>'), True,
       "каркас Nuxt — это не ответ «раздела нет»")
    eq(looks_like_shell("<html><body>" + "Вакансии " * 200 + "</body></html>"), False,
       "страница с текстом каркасом не считается")

    from .channel import _CONTACT_PAGES
    eq("/" in _CONTACT_PAGES, True,
       "главная обязана проверяться: у БЮРО 1440 нет careers-раздела, "
       "а join@1440.space стоит именно там")

    hits = [{"url": "u1", "status": "ok", "ats": None, "has_jobs": False,
             "mails": ["hr@x.ru"]},
            {"url": "u2", "status": "ok", "ats": None, "has_jobs": True, "mails": []},
            {"url": "u3", "status": "ok", "ats": "greenhouse", "has_jobs": True,
             "mails": []}]
    eq(best(hits)["url"], "u3", "ATS-доска — лучший канал")
    eq(best(hits[:2])["url"], "u2", "страница с вакансиями лучше страницы с почтой")
    eq(best([]), None, "пусто — значит пусто")
    # Каркас SPA и страница за стеной — это «ещё не знаю», а не найденный канал:
    # записать их в кэш значит соврать про канал найма.
    eq(best([{"url": "u4", "status": "КАРКАС SPA", "ats": None, "has_jobs": None,
              "mails": []},
             {"url": "u5", "status": "АНТИБОТ", "ats": None, "has_jobs": None,
              "mails": []}]), None,
       "каркас и стена кандидатами в канал не считаются")


def test_wall_challenge_state():
    """Разделение «переждать» и «нужен человек» — граница дозволенного:
    фоновую проверку ждём, интерактивную не трогаем."""
    from .wall import challenge_state

    eq(challenge_state("<title>Just a moment...</title>"), "waiting",
       "промежуточная страница Cloudflare — ждём")
    eq(challenge_state("<div>Подождите, проверяем ваш браузер</div>"), "waiting",
       "русская формулировка того же")
    eq(challenge_state("<div class='g-recaptcha'>"), "human",
       "reCAPTCHA — дальше только человек")
    eq(challenge_state("<div>подтвердите, что вы не робот</div>"), "human",
       "интерактивная проверка не переживается ожиданием")
    eq(challenge_state("<html><body>Вакансии компании</body></html>"), "clear",
       "обычная страница")


def test_wave_next_steps():
    """Блок «следующий шаг» обязан называть стены и не выдумывать их."""
    from .wave import next_steps

    res = {"stages": {"collect": {"report": [{"source": "glassdoor", "status": "blocked"},
                                             {"source": "hh", "status": "ok"}]}}}
    sl = {"rows": [{"company": "Ozon", "url": "u", "_channel": "", "_enriched": True}],
          "stats": {}}
    steps = next_steps(res, sl)
    eq(bool(any("glassdoor" in s for s in steps)), True, "стена названа поимённо")
    eq(bool(not any("hh" in s and "Стены" in s for s in steps)), True, "рабочая площадка не в стенах")
    eq(bool(any("scout channel" in s for s in steps)), True, "поиск канала — командой, не агентом")

    quiet = next_steps({"stages": {"collect": {"report": []}}},
                       {"rows": [], "stats": {}})
    eq(bool(not any("Стены" in s for s in quiet)), True, "стен нет — строки про них тоже нет")


def test_shortlist_required_years():
    """Требуемый стаж — главный критерий отбора, и раньше его выяснял человек
    чтением каждой вакансии. Берём МАКСИМУМ порогов: в одном тексте бывают и
    «опыт от 3 лет», и «Go от 5 лет» — решает старшее требование."""
    from .shortlist import required_years

    eq(required_years({"requirements": "Опыт коммерческой разработки от 3 лет"}), 3,
       "русское «от N лет»")
    eq(required_years({"requirements": "Go от 5 лет", "description": "опыт от 3 лет"}), 5,
       "берётся максимум, а не первое совпадение")
    eq(required_years({"requirements": "5+ years of experience with Golang"}), 5,
       "английское N+ years")
    eq(required_years({"requirements": "at least 5 years in software development"}), 5,
       "at least N years")
    eq(required_years({"extra": {"experience": "moreThan6"}}), 6,
       "бакет опыта hh — тоже требование, он режет автофильтром")
    eq(required_years({"requirements": "опыт работы обязателен"}), None,
       "порог не назван — None, а не ноль")
    eq(required_years({"requirements": "стаж 40 лет в отрасли"}), None,
       "нереальные числа не считаются стажем")
    # Живой случай Ozon: требование отделено от слова «опыт» переносами и буллетом.
    eq(required_years({"description": "важен опыт:\n\n• коммерческой бэкенд-разработки "
                                      "от 3 лет\n• написания тестов"}), 3,
       "перенос строки между «опыт» и «от N лет» не должен прятать требование")
    eq(required_years({"extra": {"experience": "3–6 лет"}}), 3,
       "hh отдаёт человекочитаемый бакет, а не машинный ключ")
    eq(required_years({"extra": {"experience": "более 6 лет"}}), 6,
       "«более 6 лет» — тот же бакет словами")


def test_shortlist_match_score():
    """Скоринг обязан искать по границам слов: подстрочный поиск «go» матчит
    «Django», «algorithm» и «Diego» — на живом прогоне Scala-вакансия Codacy
    из-за этого получила 100 из 100."""
    from .shortlist import match_score

    django = {"title": "Backend Developer",
              "requirements": "Django и Django REST Framework, знание алгоритмов "
                              "(algorithms), PostgreSQL, Docker, опыт работы в "
                              "команде Diego, написание тестов и code review"}
    score, why = match_score({"title": "Backend Developer"}, django)
    eq("Go не упомянут" in why, True, "Django и algorithm — не признак Go")

    go_role = {"title": "Senior Go Developer",
               "requirements": "Go, Kubernetes, PostgreSQL, Kafka, gRPC, Docker, "
                               "микросервисы и высоконагруженные системы, "
                               "наблюдаемость и code review"}
    go_score, _ = match_score({"title": "Senior Go Developer"}, go_role)
    eq(go_score > score, True, "настоящая Go-роль набирает больше")
    eq(go_score >= 80, True, "профильная роль с полным стеком — высокий балл")

    scala = {"title": "Backend Software Engineer (Scala)",
             "requirements": "Scala и функциональное программирование, PostgreSQL, "
                             "Docker, Kafka, Kubernetes, микросервисы, code review"}
    scala_score, scala_why = match_score(
        {"title": "Backend Software Engineer (Scala)"}, scala)
    eq(scala_score < go_score, True, "чужой основной язык в названии понижает")
    eq("scala" in scala_why.lower(), True, "причина понижения названа вслух")

    eq(match_score({"title": "X"}, None), (None, "нет данных: выжимки нет, судить не по чему"),
       "нет выжимки — None, а не ноль: неизвестно и не подходит это разные вещи")

    old = {"title": "Senior Go Developer",
           "requirements": "Go, Kubernetes, PostgreSQL, Kafka, gRPC, Docker, "
                           "микросервисы и высоконагруженные системы. "
                           "Опыт коммерческой разработки от 8 лет"}
    old_score, old_why = match_score({"title": "Senior Go Developer"}, old)
    eq(old_score < go_score, True, "стаж выше формальных 5 лет понижает балл")
    eq("8 лет" in old_why, True, "и это сказано в пояснении")


def test_company_field_survives_a_post_without_line_breaks():
    """«Компания: Сбер» посреди сплошного текста — это Сбер, а не весь пост.

    dreamoffer отдаёт пост ОДНОЙ строкой: переносы схлопнуты, разделы помечены
    только `**жирным**` и эмодзи. Поле-анкета читалось построчно, поэтому в
    значение уезжал весь оставшийся текст — и `extract_company` честно
    отбрасывала такое как мусор. Итог 08.08.2026: 7601 вакансия dreamoffer без
    работодателя, включая Сбер и Авито в топе шорт-листа, при том что компания
    названа прямым текстом. Раскрытие работодателя — самая дорогая проверка
    волны, и здесь она была бесплатной и потерянной."""
    from .tgvacancy import extract_company

    sber = ("**Senior Golang / TechLead GО (Platform V Synapse Service Mesh)** "
            "#гибрид #senior Москва **Компания**: Сбер ☑️**Обязанности** "
            "-Развитие инженерных практик в команде, лидирование процессов")
    avito = ("**Go-разработчик в команду коммуникаций** #удаленка #гибрид "
             "**Компания**: Авито **🔹Какие задачи вас ждут:** -проектировать")
    eq(extract_company(sber), "Сбер", "компания из сплошного текста не вытащилась")
    eq(extract_company(avito), "Авито", "компания из сплошного текста не вытащилась")
    # Двоеточие ВНУТРИ разметки: «**Компания:** Americor». Значение начинается
    # с `**`, то есть граница раздела стоит нулевой позицией — и обрезка по ней
    # молча не срабатывала, отдавая назад весь пост под видом работодателя.
    # Мусор в поле компании хуже пустоты: по нему строится ключ дедупа и сверка
    # истории откликов, и склеиваться такая строка не будет ни с чем.
    americor = ("#вакансия #remote **Компания:** Americor **Вакансия**: "
                "Системный аналитик **Локация: **** **Европейская тайм зона")
    eq(extract_company(americor), "Americor",
       "значение, начатое разметкой, не обрезано по границе раздела")
    # Название компании длиннее строки не бывает — это уже пересказ вакансии.
    long_tail = "**Компания:** " + "Очень Длинное Название " * 8
    got = extract_company(long_tail) or ""
    if len(got) > 80:
        FAILS.append(f"в компанию уехало {len(got)} символов — это не название")
    # Обычный многострочный пост читаться не перестал.
    eq(extract_company("Позиция: Go dev\nКомпания: Ozon\nЗП: 300k"), "Ozon",
       "сломан разбор обычной анкеты с переносами")
    # И выдумывать по-прежнему нечего: скрытый работодатель остаётся скрытым.
    eq(extract_company("**Компания**: NDA ☑️**Обязанности** -писать код"), None,
       "NDA принят за название компании")


def test_brief_shows_history_written_under_the_mailbox_name():
    """`brief` обязан найти отказ, записанный от «<Компания> Careers».

    Вторая копия того же бага, найденная ревью собственного диффа: сверку по
    двум ключам я сначала починил только в `shortlist`, а история компании
    печатается в `brief` — и именно её SKILL.md требует прочитать ДО того, как
    предложить отклик. То есть в самом дорогом месте проверка осталась слепой:
    шорт-лист вакансию бы понизил, а карточка всё равно показала бы «пусто —
    контакт холодный» и предложила написать туда, откуда пришёл отказ."""
    import os
    import tempfile

    from . import brief, store
    from .model import Vacancy

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "b.db")
        with store.connect(db) as conn:
            store.upsert(conn, [Vacancy(
                source="wantapply", external_id="1", url="https://x/1",
                title="Backend Tech Lead", company="Infomediji")])
            store.upsert_negotiation(
                conn, title="Thank you for your interest",
                company="Infomediji Careers", status="rejection", source="mail",
                event_at="2026-08-03T01:08:31+00:00")
            text = brief.one(conn, "https://x/1")
    if "rejection" not in text:
        FAILS.append("brief не показал отказ, записанный от имени почтового "
                     "ящика «Infomediji Careers» — карточка предложит "
                     "откликнуться туда, откуда развернули")
    if "контакт холодный" in text:
        FAILS.append("brief объявил компанию с отказом холодным контактом")


def test_free_contact_is_searched_before_spending_the_limit():
    """Прежде чем тратить раскрытие — поискать тот же контакт бесплатно.

    🔴 Требование владельца 09.08.2026: «важно лишний раз не тратить лимит на
    вакансию, которую можно найти в интернете». Первое место, где надо искать,
    — СВОЯ БАЗА: та же компания часто висит ещё и на hh, и на careered, и на
    доске ATS, где контакт открыт бесплатно. Живой счёт: у Remoby с hirehi
    нашлись записи на careered и на hh.

    Ищем по компании, а не по тексту: тексты у площадок переписаны, а имя
    работодателя совпадает. Сама исходная вакансия в результат не попадает."""
    import os
    import tempfile

    from . import store
    from .model import Vacancy
    from .reveal import free_contact_for

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "f.db")
        with store.connect(db) as conn:
            store.upsert(conn, [
                Vacancy(source="hirehi", external_id="1", company="Remoby",
                        url="https://hirehi.ru/development/x-1", title="Go dev"),
                Vacancy(source="hh", external_id="2", company="Remoby",
                        url="https://hh.ru/vacancy/2", title="Go разработчик"),
                Vacancy(source="dreamoffer", external_id="3", company="Remoby",
                        url="https://t.me/ch/9", title="Go dev"),
            ])
            got = free_contact_for(conn, "https://hirehi.ru/development/x-1")
            none = free_contact_for(conn, "https://hh.ru/vacancy/2")

    eq(got, "https://hh.ru/vacancy/2",
       f"бесплатный контакт у той же компании не найден: {got}")
    # Для самой hh-вакансии искать нечего: она и так открыта, а телеграм-пост
    # контактом не является (см. tgpost) — предлагать его как «бесплатный» нельзя.
    eq(none, None, f"телеграм-пост выдан за бесплатный контакт: {none}")

    # 🔴 careered делит вакансии на бесплатные и платные: у платных контакт
    # зарезан даже с живой сессией (mode=preview, links.telegram="#"). Считать
    # такую ссылку «бесплатным контактом» — значит отговорить от раскрытия
    # там, где раскрытие и было единственным путём (живой случай с Remoby,
    # 09.08.2026). Доступность проверяется, а не предполагается по домену.
    from .reveal import careered_contact_open

    eq(careered_contact_open({"mode": "preview",
                              "links": [{"key": "telegram", "value": "#"}]}), False,
       "платная careered-вакансия принята за открытую")
    eq(careered_contact_open({"mode": "full",
                              "links": [{"key": "telegram", "value": "https://t.me/hr"}]}), True,
       "открытый контакт careered не распознан")
    eq(careered_contact_open({"mode": "full",
                              "links": [{"key": "other_apply",
                                         "value": "https://careered.io/jobs/x"}]}), False,
       "ссылка обратно на careered принята за контакт работодателя")


def test_reveal_records_a_debt_when_the_limit_runs_out():
    """Кончился лимит — вакансия не забывается, а становится ДОЛГОМ.

    🔴 Требование владельца 09.08.2026: «была хорошая вакансия, но мы не смогли
    раскрыть контакт — нужно потом вернуться». Лимит у hirehi восстанавливается,
    поэтому единственное, что нужно, — не потерять список. Раньше `reveal`
    просто печатал «лимит исчерпан», и вакансия жила дальше только в памяти
    агента, то есть до конца сессии.

    Долг пишется в `research.verdict`, откуда его показывают `brief` и `card`,
    и оттуда же его берёт следующая волна."""
    import os
    import tempfile

    from . import store
    from .model import Vacancy
    from .reveal import note_debt, pending_reveals

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "r.db")
        with store.connect(db) as conn:
            store.upsert(conn, [Vacancy(source="hirehi", external_id="73281",
                                        url="https://hirehi.ru/development/x-73281",
                                        title="разработчик go", company="Remoby")])
            note_debt(conn, "https://hirehi.ru/development/x-73281",
                      why="лимит раскрытий исчерпан (direct_left=0)")
            debts = pending_reveals(conn)
    eq(len(debts), 1, f"долг не записался или задвоился: {debts}")
    if debts and "лимит" not in (debts[0].get("why") or ""):
        FAILS.append(f"причина долга потеряна: {debts[0]}")
    if debts and debts[0].get("url") != "https://hirehi.ru/development/x-73281":
        FAILS.append(f"долг записан не на ту вакансию: {debts[0]}")


def test_a_debt_is_closed_by_walking_the_twin_not_by_spending_the_limit():
    """Долг закрывается ОБХОДОМ дубля, а не тратой лимита.

    🔴 Живой счёт 09.08.2026: три долга прошлой волны я закрывал руками —
    искал ту же компанию по редким словам описания, вручную звал обход, вручную
    смотрел сайт. Следующая волна принесла бы ту же ручную работу, а «алгоритм
    такой ошибки не допустил бы» — прямые слова владельца про пропущенные
    вакансии.

    Ключевое: контакт лежит НЕ В САМОЙ записи-дубле, а за её ссылками. У
    Teleport дублем был телеграм-пост, пост вёл на страницу вакансии, и уже
    там стоял телеграм рекрутёра. Поэтому дубль ищется на ЛЮБОЙ площадке, а
    не только на тех, где контакт открыт сразу.
    """
    import os
    import tempfile

    from . import crawl as C
    from . import store
    from .model import Vacancy
    from .reveal import note_debt, pending_reveals, resolve_debt

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "r.db")
        with store.connect(db) as conn:
            store.upsert(conn, [
                Vacancy(source="hirehi", external_id="72777",
                        url="https://hirehi.ru/development/x-72777",
                        title="go developer", company="Teleport"),
                # Дубль: телеграм-пост той же компании. Контакта в нём нет —
                # он за ссылкой, и достать его может только обход.
                Vacancy(source="shadowhint", external_id="951",
                        url="https://t.me/backend_frontend_jobs/951",
                        title="Go Developer (Middle+/Senior)", company="Teleport")])
            note_debt(conn, "https://hirehi.ru/development/x-72777",
                      why="лимит раскрытий исчерпан (direct_left=0)")

            walked: list[str] = []

            forced: list[bool] = []

            def fake_crawl(_conn, target, **kw):
                walked.append(target)
                forced.append(bool(kw.get("force")))
                return C.Result(origin=target), []

            def fake_best(res):
                return {"value": "@lenalinmoon", "kind": "telegram",
                        "why": "телеграм рекрутёра со страницы вакансии"}

            old_crawl, old_best = C.walk, C.best_contact
            C.walk, C.best_contact = fake_crawl, fake_best
            try:
                got = resolve_debt(conn, "https://hirehi.ru/development/x-72777", db=db)
            finally:
                C.walk, C.best_contact = old_crawl, old_best

            if not got or got.get("contact") != "@lenalinmoon":
                FAILS.append(f"контакт из обхода дубля не добыт: {got}")
            if "https://t.me/backend_frontend_jobs/951" not in walked:
                FAILS.append(f"обход дубля не запускался: {walked}")
            # Кэш обхода хранит маршруты, а не ник — значит нужен переобход,
            # иначе контакт из тела вакансии до долга не доедет.
            if not all(forced):
                FAILS.append("обход дубля пошёл из кэша: контакта в кэше нет, "
                             "и долг остался бы висеть при найденном контакте")

            # 🔴 Обратная сторона, поймана живым прогоном в тот же день: обход
            # часто отвечает «прямого канала не нашёл, вот витрина». Это НЕ
            # контакт — с витрины мы и пришли, — и долг им закрываться не
            # должен. Иначе долг Teleport «закрылся» вакансией одноимённой
            # американской компании на LinkedIn.
            def showcase(res):
                return {"kind": "витрина", "value": "https://uk.linkedin.com/jobs/view/1",
                        "why": "прямого канала обход не нашёл — отклик через площадку"}

            C.walk, C.best_contact = fake_crawl, showcase
            try:
                weak = resolve_debt(conn, "https://hirehi.ru/development/x-72777", db=db)
            finally:
                C.walk, C.best_contact = old_crawl, old_best
            if weak and "linkedin" in str(weak.get("contact", "")):
                FAILS.append(f"витрина засчитана за контакт и закрыла долг: {weak}")

            # Долг снят и больше не висит.
            from .reveal import clear_debt
            if got:
                clear_debt(conn, "https://hirehi.ru/development/x-72777",
                           contact=got["contact"], why=got["why"])
            left = pending_reveals(conn)
    if left:
        FAILS.append(f"долг остался висеть после того, как контакт найден: {left}")


def test_reveal_refuses_to_spend_the_limit_on_a_dead_or_free_vacancy():
    """Лимит раскрытий тратится только на то, что этого стоит.

    🔴 Требование владельца 09.08.2026: лимит очень маленький, поэтому ДО
    раскрытия надо убедиться, что вакансия жива и что того же контакта нет
    бесплатно — например, та же вакансия лежит на площадке, где контакт открыт.
    Раньше `reveal` тратил лимит на всё, что ему дали, а решение «стоит ли»
    принимал агент по памяти.

    Проверяется предполётная функция: она НЕ ходит в сеть за раскрытием, а
    только говорит, что с каждым URL делать."""
    from .reveal import preflight

    plan = preflight(
        ["https://hirehi.ru/development/x-1",
         "https://hirehi.ru/development/x-2",
         "https://hirehi.ru/development/x-3"],
        liveness={"https://hirehi.ru/development/x-2": "МЕРТВА"},
        free_contact={"https://hirehi.ru/development/x-3":
                      "https://acme.example/careers"},
    )
    by_url = {p["url"]: p for p in plan}
    eq(by_url["https://hirehi.ru/development/x-1"]["spend"], True,
       "живая вакансия без бесплатного контакта — раскрывать стоит")
    eq(by_url["https://hirehi.ru/development/x-2"]["spend"], False,
       "лимит потрачен на МЁРТВУЮ вакансию")
    eq(by_url["https://hirehi.ru/development/x-3"]["spend"], False,
       "лимит потрачен там, где контакт есть бесплатно")
    if "acme.example" not in (by_url["https://hirehi.ru/development/x-3"]["why"] or ""):
        FAILS.append("не сказано, ГДЕ найден бесплатный контакт")


def test_resume_of_a_jobseeker_is_not_a_vacancy():
    """Резюме соискателя — не вакансия, даже если площадка зовёт его job.

    🔴 Живой случай 08.08.2026, и он дошёл до готовой карточки: careered отдал
    запись с `kind: job`, а внутри «Всем привет! Сейчас нахожусь в поиске новых
    возможностей… 8+ лет коммерческого опыта… Немного обо мне». По ней была
    написана карточка с фитом и сопроводительным письмом — то есть предлагалось
    откликнуться на резюме другого разработчика.

    Детектор был, но жил в `tg` и ловил только телеграмные формы (#резюме, «ищу
    работу»). Теперь он общий и применяется ко всем источникам.

    Порог намеренно консервативный: срабатываем на явных признаках первого лица.
    Ложное срабатывание выбрасывает настоящую вакансию, а это дороже (инвариант
    «лишняя вакансия лучше потерянной»)."""
    from .model import looks_like_resume

    resumes = (
        "Всем привет! Сейчас нахожусь в поиске новых возможностей в качестве "
        "Backend Engineer. Немного обо мне: 8+ лет коммерческого опыта.",
        "#резюме Golang developer, 5 лет опыта",
        "Ищу работу Go-разработчиком, рассмотрю удалёнку",
        "Open to work: Senior Go Engineer, 7 years of experience",
        "Обо мне: Python с 2018 года, Go — production опыт. Рассматриваю предложения.",
    )
    for t in resumes:
        if not looks_like_resume(t):
            FAILS.append(f"резюме принято за вакансию: {t[:60]!r}")

    vacancies = (
        "Мы ищем Go-разработчика в команду платформы. Требования: Go от 3 лет.",
        "Senior Backend Engineer (Golang). Обязанности: разработка сервисов. "
        "Мы предлагаем: ДМС, удалёнку.",
        "Компания ищет backend-разработчика. Опыт от 5 лет обязателен.",
        # Опасная форма: вакансия рассказывает о команде от первого лица «мы».
        "О нас: мы строим платформу. Что мы ждём от кандидата: опыт Go.",
    )
    for t in vacancies:
        if looks_like_resume(t):
            FAILS.append(f"настоящая вакансия принята за резюме: {t[:60]!r}")


def test_go_to_market_is_sales_not_the_language():
    """«Go-to-Market» — это продажи, и в списке Go-вакансий ему не место.

    Корень `go` ловится в обороте, которым называют коммерческую функцию, и в
    выдачу приезжают рекрутёры с директорами по развитию: «Lead Recruiter, Go to
    Market», «Senior Director, Go-To-Market Strategy», «Founding Recruiter GTM».
    Четыре штуки в топ-400 одной волны 08.08.2026. Прямой потери вакансий тут
    нет, но список, где среди Go-инженеров стоит рекрутёр, читают с недоверием
    целиком."""
    from .shortlist import on_profile

    for junk in ("Lead Recruiter, Go to Market",
                 "Senior Director, Go-To-Market Strategy & Commercialization",
                 "Founding Recruiter Go-To-Market (GTM) Recruiting"):
        if on_profile(junk):
            FAILS.append(f"продажник принят за Go-вакансию: {junk!r}")
    # И обратная сторона: настоящие Go-роли фильтр не задел.
    for real in ("Senior Go Developer", "Golang-разработчик",
                 "Software Engineer, Infrastructure (Go)"):
        if not on_profile(real):
            FAILS.append(f"настоящая Go-вакансия отсеяна: {real!r}")


def test_worked_index_finds_the_company_behind_the_mailbox_name():
    """Отказ от «Infomediji Careers» обязан найтись по компании «Infomediji».

    🔴 Самая дорогая ошибка скилла в живом виде (08.08.2026). Письмо приходит от
    отображаемого имени ящика — «Infomediji Careers», «Ozon HR», «VK Recruiting»,
    — и ключом истории становилось ОНО. Вакансия той же компании называется
    просто «Infomediji», ключи не совпадали, колонка «История» оставалась пустой,
    и вакансия шла в шорт-лист как нетронутая. То есть карточка предлагала
    откликнуться туда, откуда пять дней назад пришёл отказ.

    Сверка истории — не дедуп: инвариант «ошибаться в сторону разделения» здесь
    не действует. Пропущенный отказ виден человеку сразу и стоит ему лица перед
    работодателем."""
    from .shortlist import worked_index

    idx = worked_index([
        {"company": "Infomediji Careers", "status": "rejection",
         "event_at": "2026-08-03T01:08:31+00:00"},
        {"company": "Ozon HR", "status": "invitation", "event_at": "2026-08-01"},
        {"company": "VK Recruiting Team", "status": "rejection", "event_at": "2026-07-30"},
    ])
    for asked, why in (("Infomediji", "отказ по почте не нашёлся по имени компании"),
                       ("Ozon", "приглашение потеряно из-за хвоста «HR» в имени ящика"),
                       ("VK", "отказ потерян из-за хвоста «Recruiting Team»")):
        from .shortlist import norm
        if not idx.get(norm(asked)):
            FAILS.append(f"{why}: {asked!r} не найден в индексе истории")
    # Обратная сторона: срезать хвост нельзя настолько, чтобы разные компании
    # слиплись. «HR Cloud» — это название продукта, а не отдел кадров облака.
    idx2 = worked_index([{"company": "HR Cloud", "status": "rejection"}])
    from .shortlist import norm as _n
    if idx2.get(_n("Cloud")):
        FAILS.append("«HR Cloud» схлопнулась с «Cloud» — срезано лишнее")


def test_shortlist_dedup_stable_canon():
    """Канон группы — самая ранняя запись. Иначе один и тот же прогон на той же
    базе даёт разные ссылки в карточках: порядок строк SQL не гарантирован."""
    from .shortlist import merge

    rows = [
        {"source": "hirehi", "external_id": "b", "title": "Go Developer",
         "company": "Ozon", "url": "later", "first_seen": "2026-08-04"},
        {"source": "hh", "external_id": "a", "title": "Senior Go разработчик",
         "company": "Ozon", "url": "earliest", "first_seen": "2026-08-01"},
    ]
    eq(merge(rows)[0]["url"], "earliest", "базой стала самая ранняя запись")
    eq(merge(list(reversed(rows)))[0]["url"], "earliest",
       "порядок входа на результат не влияет")


def test_shortlist_dedup_and_profile():
    """Схлопывание дублей и фильтр профессии — то, ради чего команда есть."""
    from .shortlist import dup_group, merge, on_profile

    # Один грейд, разные площадки и формулировки — это одна вакансия.
    a = {"source": "hh", "external_id": "1", "title": "Senior Go разработчик",
         "company": "Ozon", "url": "u1", "salary_from": None}
    b = {"source": "hirehi", "external_id": "2", "title": "Senior Golang Developer",
         "company": "ozon", "url": "u2", "salary_from": 300000, "currency": "RUB"}
    eq(dup_group(a), dup_group(b), "одна роль одной компании с двух площадок — один ключ")

    # 🔴 А РАЗНЫЕ грейды — это разные открытые позиции, и склеивать их нельзя:
    # у SumUp так исчезала младшая из двух реальных вакансий (190 групп на живой
    # базе). Показать две строки не страшно, потерять позицию — самое дорогое.
    junior = {"source": "hh", "external_id": "3", "title": "Backend Engineer - Cards",
              "company": "SumUp", "url": "u3"}
    senior = {"source": "hh", "external_id": "4",
              "title": "Senior Backend Engineer - Cards", "company": "SumUp", "url": "u4"}
    eq(dup_group(junior) != dup_group(senior), True,
       "разные грейды одной команды не схлопываются")
    eq(len(merge([junior, senior])), 2, "обе позиции остаются в выдаче")
    merged = merge([a, b])
    eq(len(merged), 1, "схлопнулось в одну строку")
    eq(merged[0]["salary_from"], 300000, "вилка подтянулась из той площадки, где она есть")
    eq(sorted(merged[0]["_sources"]), ["hh", "hirehi"], "оба источника сохранены")

    hidden = [{"source": "careered", "external_id": "x", "title": "Backend Developer",
               "company": None, "url": "u3"},
              {"source": "careered", "external_id": "y", "title": "Backend Developer",
               "company": None, "url": "u4"}]
    eq(len(merge(hidden)), 2,
       "у нераскрытых работодателей одинаковый заголовок — это РАЗНЫЕ вакансии")

    eq(on_profile("Senior Go Developer"), True, "профильная роль")
    eq(on_profile("Разработчик бэкенда"), True, "русская формулировка")
    eq(on_profile("Senior Android-разработчик"), False, "чужая профессия")
    eq(on_profile("QA Fullstack (Go)"), True, "QA с Go в названии не режем — решит модель")


def test_cookiepush_encrypt_roundtrip():
    """Запись куки обратно в браузер обязана быть точной обратной операцией к чтению:
    что зашифровали — то cookieimport и расшифрует. Иначе браузер молча отвергнет
    куку, и «сессия возвращена» окажется тихим разлогином."""
    try:
        import cryptography  # noqa: F401,PLC0415
    except ImportError:
        return  # без библиотеки шифрования эта ветка не работает нигде
    import hashlib as _h

    from . import cookieimport as CI
    from . import cookiepush as CP

    key = _h.pbkdf2_hmac("sha1", b"test-password", b"saltysalt", 1003, 16)

    # Тег версии берётся у существующей куки, а не проставляется наугад:
    # подменённый тег браузер молча отвергнет — это тихий разлогин.
    eq(CP.version_tag(b"v11" + b"x" * 16), b"v11", "тег v11 сохраняется")
    eq(CP.version_tag(None), b"v10", "нет исходной куки — умолчание v10")
    eq(CP.encrypt_value("t", key, tag=b"v11")[:3], b"v11", "тег пишется в блоб")

    # Предохранитель обязан отказывать при неизвестном браузере, а не разрешать.
    eq(CP.browser_running("неизвестный-браузер"), True,
       "неизвестный источник считается живым: отказ дешевле порчи профиля")
    for host_prefix in (False, True):
        blob = CP.encrypt_value("token-value-42", key, host_key="hirehi.ru",
                                with_host_prefix=host_prefix)
        eq(blob[:3], b"v10", "префикс версии Chromium на месте")
        eq(CI._decrypt_value(blob, key), "token-value-42",
           f"расшифровка вернула исходное значение (префикс хоста: {host_prefix})")
        eq(CP.detect_host_prefix(blob, key, "hirehi.ru"), host_prefix,
           "схема шифрования определяется по самой куке, а не угадывается")


def test_cookiepush_refuses_foreign_domains():
    """Запись в браузер ограничена тем же allowlist, что и чтение: чужой домен
    в состоянии сессии не должен доехать до базы кук."""
    from . import cookieimport as CI

    eq(CI.domain_allowed("hirehi.ru", CI.ALLOWED_DOMAINS), True, "площадка разрешена")
    eq(CI.domain_allowed("accounts.google.com", CI.ALLOWED_DOMAINS), False,
       "google в браузер обратно не пишем")
    eq(CI.domain_allowed("passport.yandex.ru", CI.ALLOWED_DOMAINS), False,
       "паспортные куки Яндекса не наши")


def test_careered_bearer_from_state():
    """Bearer careered достаётся из origins→localStorage сохранённого storage_state.
    Сессия площадки НЕ в куках — куки браузера здесь не помогают вовсе."""
    import json as _json
    import os as _os
    import tempfile
    from . import auth as _auth
    old = _auth.AUTH_DIR
    with tempfile.TemporaryDirectory() as td:
        _auth.AUTH_DIR = td
        try:
            token, why = _auth.bearer_from_state("careered")
            eq(token, None, "нет файла — нет токена")
            if "auth login careered" not in why:
                FAILS.append(f"bearer_from_state без файла не зовёт login: {why!r}")
            state = {"cookies": [], "origins": [
                {"origin": "https://careered.io",
                 "localStorage": [{"name": "access_token", "value": "tok123"}]}]}
            with open(_os.path.join(td, "careered.json"), "w", encoding="utf-8") as f:
                _json.dump(state, f)
            eq(_auth.bearer_from_state("careered")[0], "tok123",
               "токен из origins→localStorage")
            eq(_auth.session_probe("careered")[0], "logged_in",
               "session_probe видит вход по localStorage-токену, без браузера")
        finally:
            _auth.AUTH_DIR = old


def test_fetch_json_accepts_explicit_none_headers():
    """`headers=None`, переданный явно, — легальный вызов fetch_json: так деталка
    careered ходит анонимом без Bearer. Цепочка setdefault() на None падала
    AttributeError и уронила ВСЕ анонимные careered-выжимки прогона 2026-08-04."""
    from . import net as N
    seen: dict = {}

    def fake_fetch(url, **kw):
        seen.update(kw)
        return "{\"ok\": true}", url

    real = N.fetch
    N.fetch = fake_fetch
    try:
        got = N.fetch_json("https://careered.io/api/jobs/x", headers=None,
                           cookies="session=1")
    except AttributeError as e:
        FAILS.append(f"fetch_json(headers=None) упал: {e}")
        return
    finally:
        N.fetch = real
    eq(got, {"ok": True}, "JSON разобран")
    eq(seen.get("headers", {}).get("Accept"), "application/json, text/plain, */*",
       "Accept подставлен и при headers=None")
    eq(seen.get("cookies"), "session=1", "остальные kwargs не потерялись")


# ──────────────────────────────────────────────────────────────────────────────


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
