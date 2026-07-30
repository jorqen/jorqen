"""E2E: доказательство, что вакансия не теряется между площадкой и отчётом.

Модульные тесты (`test_scout`, `test_sources_web`, `test_sources_auth`) отвечают
на вопрос «правильно ли разобрана страница». Здесь вопрос другой и он главный:
**не потерялось ли что-нибудь по дороге**. Потеря тихая — вот чего мы боимся:
источник отдал 300 строк, парсер разобрал 292, в базу легло 39, а в отчёте
написано «ok 39», и никто никогда не узнает про остальные.

Что проверяется:

1. **Баланс каждого источника.** offered = dropped + dupes + skipped_kind + parsed
   и parsed = kept + skipped_profile + skipped_old. Расхождение → падение.
2. **Сохранность в базе.** Число записей прогона = число уникальных
   (source, external_id) в собранном. Кросс-площадочные дубли по dup_key
   НЕ схлопываются — это разные объявления у разных нанимателей, и склеить их
   значит потерять одно из них.
3. **Фильтр профессии не режет своё.** Заведомо релевантные заголовки (взяты
   из реальной базы, русские и английские) обязаны пройти; заведомо чужие —
   отсеяться.
4. **Дельта не режет молча.** `new` без limit отдаёт всё, с limit — честно
   сообщает, сколько осталось за кадром.
5. **Детали доезжают.** Выжимка по выборке из каждого источника: доля рабочих
   и поимённый список тех, где приезжает каркас.
6. **Сквозной отчёт.** В отчёте `scan` есть строка КАЖДОГО источника из реестра.

Сетевые тесты помечены `[СЕТЬ]` и устойчивы: недоступная площадка фиксируется
как «недоступна» и не роняет прогон, но площадка, которая ОТДАЛА данные
с битым балансом, роняет обязательно. Иначе тест зелёный ровно тогда, когда
всё сломано.

    python3 -m scripts.scout.test_e2e            # всё, включая сеть и браузер
    python3 -m scripts.scout.test_e2e --fast     # без браузерных площадок
    python3 -m scripts.scout.test_e2e --offline  # только то, что не ходит в сеть
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

from . import store
from .cli import _delta_table, build_scan_report, cmd_new, run_collect
from .detail import get_detail
from .model import Vacancy
from .net import BlockedError, FetchError, looks_blocked, parallel
from .sources import ATS_ROLE_RE, NEEDS_BROWSER_SET, SOURCES, Ctx, Tally
from .sources_web import WEB_REFERENCE

FAILS: list[str] = []
NOTES: list[str] = []


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def true(cond, label):
    if not cond:
        FAILS.append(label)


def note(text: str) -> None:
    NOTES.append(text)
    print(f"   · {text}")


# Площадки, которые пользователь назвал поимённо. Список зашит СПЕЦИАЛЬНО:
# «источник тихо исчез из реестра» — самая дорогая потеря из возможных, и она
# ничем не видна в выводе. Ключ — имя в реестре, значение — как площадка
# называется у человека.
WANTED = {
    "hh": "hh.ru", "habr": "Хабр Карьера", "careered": "careered.io",
    "linkedin": "LinkedIn", "ats": "ATS-доски работодателей",
    "himalayas": "himalayas.app", "arbeitnow": "arbeitnow.com", "jobicy": "jobicy.com",
    "hackoffer": "hack-offer.tech", "hirehi": "hirehi.ru", "wantapply": "wantapply.com",
    "dreamoffer": "dreamoffer", "jobsdb": "jobsdb", "eures": "EURES",
    "glassdoor": "glassdoor.com.au", "relocateme": "relocate.me",
    "shadowhint": "shadowhint.com", "geekjob": "geekjob.ru", "getmatch": "getmatch.ru",
    "rabota": "rabota.ru", "levels": "levels.fyi",
}


# ──────────────────────────────────────────────────────────────────────────────
# Единственный живой прогон на весь набор
# ──────────────────────────────────────────────────────────────────────────────

class Live:
    """Один живой обход площадок, который переиспользуют все сетевые тесты.

    Гонять collect по разу на тест — это не «тщательнее», а «дольше в пять раз
    и с разными данными в каждом тесте»: сравнить их между собой уже нельзя.
    """

    def __init__(self, *, fast: bool):
        self.dir = tempfile.mkdtemp(prefix="scout-e2e-")
        self.db = os.path.join(self.dir, "e2e.db")
        self.fast = fast
        self.res: dict | None = None
        self.started_at: str | None = None

    def run(self) -> dict:
        if self.res is not None:
            return self.res
        ctx = Ctx(query="Golang", extra_queries=("Go разработчик", "Backend Go"),
                  days=7, limit=40)
        names = list(SOURCES)
        print(f"\n[СЕТЬ] обход {len(names)} площадок, окно 7 дней, лимит 40"
              + (" (без браузерных)" if self.fast else "") + " …", flush=True)
        self.started_at = store.now()
        t0 = time.time()
        self.res = run_collect(ctx, names, workers=8, db=self.db,
                               no_browser=self.fast,
                               args_dict={"cmd": "test_e2e"})
        print(f"[СЕТЬ] обход занял {time.time() - t0:.0f}s, "
              f"вакансий {self.res['total']}", flush=True)
        return self.res

    def jobs(self) -> list[dict]:
        return [v.to_dict() for v in self.run()["vacancies"]
                if v.external_id != "_summary"]

    def summaries(self) -> dict[str, Vacancy]:
        return {v.source: v for v in self.run()["vacancies"]
                if v.external_id == "_summary"}

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


LIVE: Live | None = None


# ──────────────────────────────────────────────────────────────────────────────
# 1. Баланс: отдано = разобрано + отсеяно + потери
# ──────────────────────────────────────────────────────────────────────────────

def test_tally_invariant_catches_a_lost_row():
    """Счётчик обязан ЗАМЕТИТЬ пропажу, а не сойтись по построению.

    Тест на сам инструмент измерения: если Tally умеет только показывать
    красивые цифры, все остальные проверки этого файла ничего не стоят.
    """
    t = Tally("проверка", offered=10, parsed=9, kept=9)
    eq(t.mismatch(), 1, "строка, которую никуда не записали, не замечена")
    true("РАСХОЖДЕНИЕ" in t.row().title, "расхождение не названо в сводке словами")

    ok = Tally("проверка", offered=10, dropped=1, dupes=2, skipped_kind=1, parsed=6,
               kept=3, skipped_profile=2, skipped_old=1)
    eq(ok.mismatch(), 0, "сошедшийся баланс объявлен расхождением")
    true("РАСХОЖДЕНИЕ" not in ok.row().title, "лишняя тревога в сводке")

    row = ok.row()
    eq(row.external_id, "_summary", "сводка обязана быть служебной строкой")
    eq(row.url, "", "сводка с непустым url попадёт в выдачу как вакансия")


# Сводки бывают двух форм, и обе честные:
#   web  — offered/parsed/kept (sources.Tally): «сколько строк прошло через парсер»;
#   auth — claimed/got/dropped_dup (sources_auth.Tally): «сколько НАЗВАЛ сервер».
# Вторая отвечает на вопрос, которого нет у первой: сервер обрезал limit — мы
# об этом узнаем, только сравнив с его же total.
def balance_of(summary: Vacancy) -> tuple[int, str]:
    raw = summary.raw or {}
    if "mismatch" in raw:
        return int(raw["mismatch"]), (
            f"отдано {raw['offered']} = не разобралось {raw['dropped']} + дублей "
            f"{raw['dupes']} + не вакансий {raw['skipped_kind']} + разобрано "
            f"{raw['parsed']}; разобрано = записано {raw['kept']} + профиль "
            f"{raw['skipped_profile']} + старьё {raw['skipped_old']}")
    if "lost" in raw:
        return int(raw["lost"]), (
            f"сервер назвал {raw['claimed']} = унесли {raw['got']} + дублей "
            f"{raw['dropped_dup']} + НЕ ДОСЧИТАЛИСЬ {raw['lost']}")
    return -1, "форма сводки неизвестна"


def test_every_source_balances_what_it_offered():
    """[СЕТЬ] По каждому источнику: отдано = разобрано + отсеяно. И ни строки мимо.

    Падение здесь означает ровно одно: парсер выбрасывает записи, не считая их,
    — то есть «найдено 39» в отчёте не равно «площадка отдала 39»."""
    res = LIVE.run()
    summaries = LIVE.summaries()
    by_status: dict[str, list[str]] = {}
    for r in res["report"]:
        by_status.setdefault(r["status"], []).append(r["source"])

    print("\n   баланс по источникам:")
    for r in sorted(res["report"], key=lambda x: x["source"]):
        name = r["source"]
        if r["status"] != "ok":
            # Недоступная площадка — это «недоступна», а не «ок» и не «ноль».
            note(f"{name:<12} {r['status'].upper():<9} — {(r['error'] or '')[:60]}")
            continue
        s = summaries.get(name)
        if not s:
            FAILS.append(f"{name}: источник отработал, но не сказал, сколько отдал — "
                         f"потерю строк в нём доказать нечем")
            continue
        if name in WEB_REFERENCE:
            # Справочник (levels.fyi) вакансий не отдаёт вовсе, и баланса
            # «отдано → записано» у него нет. Требование к нему другое:
            # цифры должны быть настоящие, а «найдено 0» — объяснено.
            raw = s.raw or {}
            true(bool(raw.get("sample_size")), f"{name}: в сводке нет размера выборки — "
                                               f"медиана без него читается как цифра "
                                               f"неизвестно чего")
            true(bool(raw.get("median_total")), f"{name}: в сводке нет медианы")
            eq(r["found"], 0, f"{name}: справочник рынка посчитан вакансиями")
            note(f"{name:<12} справочник — {s.title[:90]}")
            continue
        miss, human = balance_of(s)
        if miss != 0:
            FAILS.append(f"{name}: баланс не сошёлся ({miss}) — {human}")
        else:
            print(f"   · {name:<12} ok       {r['found']:>5}  {human}")

    live_ok = by_status.get("ok", [])
    true(len(live_ok) >= 10,
         f"отработало всего {len(live_ok)} площадок — это не «данные собраны», "
         f"а «сеть/окружение недоступны»: {by_status}")


def test_no_source_is_silently_missing():
    """[СЕТЬ] В покрытии есть КАЖДАЯ площадка реестра — со статусом и цифрой.

    Молча выпавшая площадка страшнее упавшей: упавшая видна строкой «УПАЛ»,
    выпавшая не видна ничем, и отчёт выглядит полным."""
    res = LIVE.run()
    seen = {r["source"] for r in res["report"]}
    missing = sorted(set(SOURCES) - seen)
    eq(missing, [], "площадки нет в покрытии прогона вовсе")
    for r in res["report"]:
        true(r["status"] in ("ok", "blocked", "error", "skipped", "no_login"),
             f"{r['source']}: непонятный статус {r['status']!r}")
        if r["status"] != "ok":
            true(bool(r.get("error")), f"{r['source']}: статус {r['status']} без причины — "
                                       f"в отчёте это будет пустая клетка")


# ──────────────────────────────────────────────────────────────────────────────
# 2. Сохранность в базе
# ──────────────────────────────────────────────────────────────────────────────

def test_everything_collected_is_in_the_db():
    """[СЕТЬ] Собрано → записано. Ни одна вакансия не растворилась при вставке.

    Ключ хранения — (source, external_id). Дубли по dup_key (одна вакансия
    на пяти агрегаторах) НЕ схлопываются: это разные объявления с разными
    ссылками, и склейка автоматикой потеряла бы то, где вилка указана честнее.
    """
    res = LIVE.run()
    jobs = LIVE.jobs()
    uniq = {(j["source"], j["external_id"]) for j in jobs}

    with store.connect(LIVE.db) as conn:
        in_db = conn.execute(
            "SELECT COUNT(*) FROM vacancy WHERE external_id <> '_summary'").fetchone()[0]
        visible = store.count(conn, exclude_decided=False)
        empty_url = conn.execute(
            "SELECT COUNT(*) FROM vacancy WHERE external_id <> '_summary' "
            "AND (url IS NULL OR url = '')").fetchone()[0]

    eq(in_db, len(uniq), "в базе не столько записей, сколько уникальных вакансий собрано")
    eq(res["new"] + res["updated"] - len(LIVE.summaries()), len(uniq),
       "новых+обновлённых не сходится с числом уникальных вакансий "
       "(сводки считаются отдельно)")
    # Пустой url — это тихая смерть вакансии: `store.query` режет такие строки,
    # и в дельту, отчёт и enrich она не попадёт НИКОГДА, оставшись в базе.
    eq(empty_url, 0, "вакансии с пустым url записаны, но невидимы для дельты и отчёта")
    eq(visible, in_db, "часть записей не видна выборке — потеря после вставки")

    dups: dict[str, set] = {}
    for j in jobs:
        dups.setdefault(j["dup_key"], set()).add(j["source"])
    cross = {k: v for k, v in dups.items() if len(v) > 1}
    note(f"кросс-площадочных дублей по dup_key: {len(cross)} "
         f"(строк в базе они не отнимают — склеивает их модель, не сборщик)")


def test_summaries_never_enter_the_delta():
    """[СЕТЬ] Служебные сводки лежат в базе, но не считаются вакансиями."""
    with store.connect(LIVE.db) as conn:
        rows = store.query(conn, limit=None, exclude_decided=False)
    bad = [r for r in rows if r["external_id"] == "_summary"]
    eq(len(bad), 0, "строка-сводка попала в выдачу как вакансия")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Фильтр профессии: не режет своё, режет чужое
# ──────────────────────────────────────────────────────────────────────────────

# Заголовки настоящие: сняты из базы прогона 30.07.2026 (3147 вакансий),
# по одному-двум на каждую форму записи. Выдуманные примеры здесь бесполезны —
# фильтр ломается ровно на том, чего не придумаешь: «Разработчик бэкенда»
# (\bбэкенд\b не бьётся об «бэкенда») и «Platforms» во множественном числе.
OURS = [
    "Senior Go Developer",
    "Golang Developer (Remote)",
    "Разработчик Go",
    "Go-разработчик в команду платежей",
    "Ведущий разработчик Go, Группа задач бухгалтерского учёта",
    "Backend Engineer",
    "Backend Developer (Go/Kubernetes)",
    "Бэкенд-разработчик",
    "Разработчик бэкенда в Яндекс Образование",
    "Бекенд разработчик (Go)",
    "Platform Engineer",
    "Systems Development Engineer, Enterprise Collaboration Platforms",
    "Платформенный инженер",
    "Инженер платформы данных",
    "Site Reliability Engineer (SRE)",
    "SRE / DevOps Engineer with Go",
    "Инженер по надёжности (SRE)",
    "Тимлид Go",
    "Тимлид команды бэкенда",
    "Team Lead Go",
    "Технический лидер команды (Go)",
    "Tech Lead Backend",
    "Software Engineer, Distributed Systems",
    "Senior Software Engineer (Microservices)",
    "Cloud Infrastructure Engineer",
    "Инфраструктурный инженер",
    "Системный инженер",
    "Архитектор решений (backend)",
    "Full Stack Engineer",
]

# Чужие профессии — тоже настоящие заголовки. Специально без слов «backend»
# и «cloud» внутри: «QA Engineer — Web, Backend, API» фильтр пропустит, и это
# осознанный перекос в сторону лишнего. Лишняя строка стоит одного взгляда
# модели, потерянная вакансия — не стоит ничего, потому что её никто не увидит.
THEIRS = [
    "Frontend Developer (React)",
    "Фронтенд-разработчик",
    "QA-инженер",
    "QA Automation Engineer (Python)",
    "Дизайнер интерфейсов",
    "UX/UI Designer",
    "Продавец-консультант",
    "Менеджер по продажам",
    "Бухгалтер на первичную документацию",
    "Water Sports Instructor",
    "Assistant Ménager",
    "Магазинный работник",
]


def test_profile_filter_keeps_ours():
    """Ни один заведомо свой заголовок не отсеян. Это главный тест файла.

    Фильтр профессии — единственное место, где сборщик выбрасывает вакансию
    сам, по своему решению. Ошибка здесь не видна нигде: в отчёте будет ровная
    строка «отсеяно по профессии 6340», и в этих 6340 будет лежать нужное.
    """
    lost = [t for t in OURS if not ATS_ROLE_RE.search(t)]
    eq(lost, [], "фильтр профессии отсеял СВОИ вакансии")


def test_profile_filter_drops_theirs():
    """Заведомо чужие профессии не проходят — иначе фильтра нет вовсе."""
    passed = [t for t in THEIRS if ATS_ROLE_RE.search(t)]
    eq(passed, [], "фильтр профессии пропустил чужие профессии")


def test_profile_filter_on_real_database():
    """[БАЗА] Проверка фильтра на живых заголовках пользователя, если база есть.

    Тест на фикстурах доказывает, что не сломаны разобранные случаи. Этот —
    что не появилось НОВЫХ: площадки меняют формулировки чаще, чем мы правим
    регулярку.
    """
    db = store.DEFAULT_DB
    if not os.path.exists(db):
        note("базы пользователя нет — проверка на живых заголовках пропущена")
        return
    import re

    # Грубый признак «это точно про нас»: слово Go/Golang/бэкенд/SRE в названии.
    # Если такой заголовок фильтр выбрасывает — вакансия теряется молча.
    rough = re.compile(r"\bgo\b|golang|голанг|бэкенд|бекенд|backend|\bsre\b|"
                       r"kubernetes|микросервис|microservic", re.I)
    with store.connect(db) as conn:
        rows = conn.execute("SELECT title FROM vacancy "
                            "WHERE external_id <> '_summary'").fetchall()
    titles = [r["title"] or "" for r in rows]
    lost = sorted({t for t in titles if rough.search(t) and not ATS_ROLE_RE.search(t)})
    note(f"живых заголовков в базе: {len(titles)}; профильных по грубому "
         f"признаку и отсеянных фильтром: {len(lost)}")
    if lost:
        FAILS.append("фильтр профессии режет свои живые заголовки: "
                     + "; ".join(lost[:8]))


# ──────────────────────────────────────────────────────────────────────────────
# 4. Дельта не режет молча
# ──────────────────────────────────────────────────────────────────────────────

class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _seed(db: str, n: int) -> None:
    vs = [Vacancy(source="hh", external_id=str(1000 + i),
                  url=f"https://hh.ru/vacancy/{1000 + i}",
                  title=f"Go Developer {i}", company="Acme",
                  published_at="2026-07-29T10:00:00Z") for i in range(n)]
    with store.connect(db) as conn:
        store.upsert(conn, vs)


def test_delta_without_limit_returns_everything():
    """`new` без limit отдаёт ВСЮ дельту, с limit — честно говорит об усечении.

    Живьём это стоило 1305 потерянных вакансий: заголовок «200 вакансий» при
    1505 в окне, и ни слова о том, что показано не всё.
    """
    tmp = tempfile.mkdtemp(prefix="scout-delta-")
    db = os.path.join(tmp, "d.db")
    try:
        _seed(db, 25)
        with store.connect(db) as conn:
            total = store.count(conn)
            all_rows = store.query(conn, limit=None)
            cut = store.query(conn, limit=10)
        eq(total, 25, "count посчитал не все строки дельты")
        eq(len(all_rows), 25, "query без limit отдал не всё")
        eq(len(cut), 10, "limit не применился")

        out = _capture(lambda: cmd_new(_Args(
            since="30d", by="published", sources=None, include_decided=False,
            limit=10, db=db, format="text", strict=False)))
        true("всего 25" in out, "в заголовке нет полного размера дельты")
        true("за кадром 15" in out, "не сказано, сколько вакансий осталось за кадром")
        true("ПОКАЗАНА НЕ ВСЯ ДЕЛЬТА" in out, "усечение не объявлено предупреждением")

        full = _capture(lambda: cmd_new(_Args(
            since="30d", by="published", sources=None, include_decided=False,
            limit=0, db=db, format="text", strict=False)))
        true("ПОКАЗАНА НЕ ВСЯ ДЕЛЬТА" not in full,
             "--limit 0 предупреждает об усечении, которого нет")
        eq(full.count("| https://hh.ru/vacancy/"), 25,
           "--limit 0 показал не все строки")

        raw = _capture(lambda: cmd_new(_Args(
            since="30d", by="published", sources=None, include_decided=False,
            limit=10, db=db, format="json", strict=False)))
        payload = json.loads(raw)
        eq(payload["total"], 25, "в JSON нет полного размера дельты")
        eq(payload["truncated"], 15, "в JSON не посчитано усечение")
        eq(len(payload["items"]), 10, "в JSON items не совпадает с limit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delta_strict_flag_fails_on_truncation():
    """`--strict` даёт ненулевой код: скрипт, который вызывает `new`, обязан
    иметь способ УЗНАТЬ об усечении, а не вычитывать текст глазами."""
    tmp = tempfile.mkdtemp(prefix="scout-delta2-")
    db = os.path.join(tmp, "d.db")
    try:
        _seed(db, 12)
        code = _silent(lambda: cmd_new(_Args(
            since="30d", by="published", sources=None, include_decided=False,
            limit=5, db=db, format="text", strict=True)))
        eq(code, 1, "усечение при --strict не дало ненулевого кода")
        code = _silent(lambda: cmd_new(_Args(
            since="30d", by="published", sources=None, include_decided=False,
            limit=0, db=db, format="text", strict=True)))
        eq(code, 0, "полная выдача при --strict объявлена усечённой")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Детали доезжают
# ──────────────────────────────────────────────────────────────────────────────

def _is_skeleton(d) -> bool:
    """Каркас — это когда страница скачалась, а выжимки из неё нет.

    Проверяем не статус, а содержимое: `status='generic'` с полным описанием —
    рабочий случай, а `ok` без названия и текста — нет.
    """
    body = (d.requirements or d.description or "")
    return not (d.title and len(body) >= 200)


def test_details_arrive_for_every_source():
    """[СЕТЬ] Выжимка по выборке из каждого источника: доля рабочих и каркасы.

    Тест не требует ста процентов: часть площадок закрывает описание за входом,
    часть отдаёт его только браузером. Требует другого — чтобы каркас был НАЗВАН
    каркасом. Молчаливая деталка-пустышка попадает в карточку как «описание
    не найдено» и выглядит фактом о вакансии.
    """
    jobs = LIVE.jobs()
    if not jobs:
        note("вакансий не собрано — проверка деталей пропущена")
        return
    # Группируем по КОРНЮ источника: у ATS-досок source выглядит как
    # «ats:greenhouse:canonical», и без этого выборка раздувается с 2 штук
    # до сорока — двадцать досок вместо одного источника.
    per: dict[str, list[dict]] = {}
    for j in jobs:
        per.setdefault(j["source"].split(":", 1)[0], []).append(j)

    sample = {}
    for src, rows in sorted(per.items()):
        for r in rows[:2]:
            sample[f"{src}|{r['source']}:{r['external_id']}"] = r

    print(f"\n   [СЕТЬ] выжимки по выборке {len(sample)} вакансий "
          f"из {len(per)} источников …", flush=True)
    results = parallel({k: (lambda u=r["url"]: get_detail(u))
                        for k, r in sample.items()}, workers=6)

    stat: dict[str, dict] = {}
    for key, (ok, payload) in results.items():
        src = key.split("|", 1)[0]
        st = stat.setdefault(src, {"ok": 0, "каркас": 0, "антибот": 0, "упал": 0,
                                   "почему": []})
        if ok:
            if _is_skeleton(payload):
                st["каркас"] += 1
                st["почему"].append(f"{key} → status={payload.status}, "
                                    f"описание {len(payload.description or '')} симв.")
            else:
                st["ok"] += 1
        elif isinstance(payload, BlockedError):
            st["антибот"] += 1
            st["почему"].append(f"{key} → антибот")
        else:
            st["упал"] += 1
            st["почему"].append(f"{key} → {type(payload).__name__}: {str(payload)[:70]}")

    total_ok = sum(s["ok"] for s in stat.values())
    skeleton_sources, dead_sources = [], []
    print("\n   детали по источникам:")
    for src, s in sorted(stat.items()):
        n = s["ok"] + s["каркас"] + s["антибот"] + s["упал"]
        print(f"   · {src:<12} выжимка {s['ok']}/{n}"
              + (f", каркас {s['каркас']}" if s["каркас"] else "")
              + (f", антибот {s['антибот']}" if s["антибот"] else "")
              + (f", упало {s['упал']}" if s["упал"] else ""))
        if s["ok"] == 0 and s["каркас"]:
            skeleton_sources.append(src)
        if s["ok"] == 0 and not s["каркас"]:
            dead_sources.append(src)
        for why in s["почему"][:2]:
            print(f"       {why}")

    if skeleton_sources:
        note(f"КАРКАС вместо выжимки (парсера деталки нет): "
             f"{', '.join(skeleton_sources)}")
    if dead_sources:
        note(f"деталка недоступна (антибот/сеть): {', '.join(dead_sources)}")
    true(total_ok > 0,
         "ни одна выжимка не получилась — либо сеть недоступна, либо деталка "
         "сломана целиком")
    share = total_ok / max(len(results), 1)
    note(f"доля рабочих выжимок: {total_ok}/{len(results)} ({share:.0%})")


# ──────────────────────────────────────────────────────────────────────────────
# 6. Сквозной отчёт scan
# ──────────────────────────────────────────────────────────────────────────────

def test_scan_report_lists_every_source():
    """В отчёте `scan` есть строка КАЖДОГО источника — с любым исходом этапов.

    Отчёт — это то, что читает модель ВМЕСТО ручного обхода. Площадка, которой
    в нём нет, для модели не существует: она не «упала», она не спрашивалась,
    и никто этого не заметит.
    """
    report = [{"source": n, "status": "ok", "found": 7, "elapsed_ms": 100,
               "error": None, "note": None} for n in SOURCES]
    report[0].update(status="blocked", found=0, error="антибот-проверка")
    report[1].update(status="no_login", found=0, error="нет сессии")
    report[2].update(status="error", found=0, error="HTTP 500")
    stages = {
        "collect": {"status": "ok", "report": report,
                    "found": 7 * (len(report) - 3), "new": 5, "updated": 2},
        "telegram": {"status": "skipped", "note": "выключен флагом"},
        "enrich": {"status": "ok", "ok": 3, "delta": 9, "digests": ["── Go Dev — Acme"]},
        "hh": {"status": "no_creds", "text": ""},
        "mail": {"status": "ok", "text": "писем найма 4", "found": 4},
    }
    text = build_scan_report(stages, generated_at="2026-07-30T12:00:00+00:00",
                             days=7, matches=[], delta_rows=[])
    for name in SOURCES:
        true(f"площадка: {name} " in text, f"в отчёте scan нет строки источника {name}")
    for mark in ("АНТИБОТ", "НУЖЕН ВХОД", "УПАЛ", "ПРОПУЩЕН"):
        true(mark in text, f"в отчёте нет пометки {mark} — исход этапа не читается")
    # Цифра «найдено» в отчёте должна быть цифрой, а не прочерком: прочерк
    # у отработавшего этапа неотличим от нуля.
    coverage = text.split("## Покрытие")[1].split("## Кандидаты")[0]
    true("| 7 |" in coverage, "числа найденного не доехали до таблицы покрытия")


def test_scan_report_survives_a_dead_stage():
    """Упавший этап — строка в отчёте, а не отсутствие отчёта."""
    stages = {"collect": {"status": "error", "error": "RuntimeError: сеть"},
              "telegram": {"status": "error", "error": "нет сессии"},
              "enrich": {"status": "error", "error": "упал"},
              "hh": {"status": "error", "error": "упал"},
              "mail": {"status": "error", "error": "упал"}}
    text = build_scan_report(stages, generated_at="2026-07-30T12:00:00+00:00",
                             days=3, matches=[], delta_rows=[])
    true("collect (весь этап) | УПАЛ" in text, "падение collect не объявлено в покрытии")
    true("## Покрытие" in text and "## Дельта площадок" in text,
         "отчёт развалился из-за упавших этапов")


def test_delta_table_shows_every_row_it_was_given():
    """Таблица дельты в отчёте не режет строки сама по себе."""
    rows = [{"title": f"Go Dev {i}", "company": "Acme", "source": "hh",
             "url": f"https://hh.ru/vacancy/{i}", "salary_from": None,
             "salary_to": None, "currency": None, "salary_gross": None,
             "salary_period": None, "location": "Москва",
             "published_at": "2026-07-29T00:00:00Z", "updated_at": None,
             "dup_key": f"acme|dev {i}"} for i in range(40)]
    text = "\n".join(_delta_table(rows, days=7))
    got = text.count("https://hh.ru/vacancy/")
    eq(got, 40, "таблица дельты в отчёте потеряла строки")


def test_every_platform_the_user_named_is_in_the_registry():
    """Реестр покрывает все площадки, которые назвал пользователь.

    Список зашит в тест намеренно: реестр можно сократить случайно (переименовали
    ключ, потеряли импорт), и ни один другой тест этого не заметит.
    """
    missing = sorted(n for n in WANTED if n not in SOURCES)
    eq(missing, [], "площадка выпала из реестра SOURCES")
    from .sources import RAW_SOURCES
    orphan = [n for n, c in RAW_SOURCES.items() if not c.get("parser")]
    eq(orphan, [], "у сырьевого источника нет полноценного парсера — "
                   "`raw` снова стал способом собирать площадку, а не отлаживать её")


# ──────────────────────────────────────────────────────────────────────────────
# 7. Регрессии слоя сети: обе ломали прогон молча
# ──────────────────────────────────────────────────────────────────────────────

def test_retry_after_5xx_keeps_the_request_body():
    """Повтор после 502 уходит с ТЕЛОМ ЗАПРОСА, а не с телом ошибки.

    Живой симптом был обманчивый: `TypeError: POST data should be bytes` вместо
    «площадка ответила 502». Ловилось на первом же 502 у POST-источника, и в
    покрытии площадка выглядела сломанным парсером, а не недоступным сервером.
    """
    import urllib.error
    import urllib.request

    from . import net

    seen: list = []
    calls = {"n": 0}

    class _Resp:
        status = 200
        headers = _H()

        def read(self):
            return b'{"ok": true}'

        def geturl(self):
            return "https://example.test/api"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, **kw):
        seen.append(req.data)
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError("https://example.test/api", 502, "Bad Gateway",
                                         _H(), None)
        return _Resp()

    real_open, real_sleep = urllib.request.urlopen, net.time.sleep
    urllib.request.urlopen = fake_urlopen
    net.time.sleep = lambda *_a, **_kw: None
    try:
        text, _ = net.fetch("https://example.test/api", method="POST",
                            data={"q": "golang"}, retries=1)
    finally:
        urllib.request.urlopen = real_open
        net.time.sleep = real_sleep

    eq(text, '{"ok": true}', "повтор после 502 не довёз ответ")
    eq(len(seen), 2, "повтора не было вовсе")
    true(all(isinstance(b, bytes) for b in seen),
         f"тело запроса при повторе перестало быть байтами: {[type(b) for b in seen]}")
    eq(seen[0], seen[1], "при повторе ушло ДРУГОЕ тело запроса")


class _H(dict):
    """Заголовки ответа: urllib ждёт get_content_charset()."""

    def get_content_charset(self):
        return "utf-8"

    def get(self, key, default=None):
        return dict.get(self, key, default)


def test_wall_in_russian_is_recognized_by_net():
    """Русскоязычный челлендж — стена, а не «страница без вакансий».

    Через рендер это выглядело хуже всего: 344 КБ «страницы», ноль вакансий
    и честное «ничего не нашлось» в отчёте. `render_page` зовёт именно
    `net.looks_blocked`, поэтому проверка обязана жить в нём.
    """
    ru = ("<html><head><title>Один момент…</title></head><body>"
          + "<div>" + "x" * 9000 + "</div></body></html>")
    true(bool(looks_blocked(ru)), "русский челлендж Cloudflare не признан стеной")
    waf = '<html><head><title>levels.fyi</title><script src="/challenge.js"></script>'
    true(bool(looks_blocked(waf)), "AWS WAF (challenge.js) не признан стеной")
    ok = ("<html><head><title>Вакансия Go-разработчик</title></head><body>"
          "Требуется опыт. Один момент, и вы с нами.</body></html>")
    eq(looks_blocked(ok), None, "живая страница объявлена стеной по слову в тексте")


# ──────────────────────────────────────────────────────────────────────────────
# 8. Умолчания забирают всё, а обрезание видно
#
# Общая беда всех четырёх проверок ниже одна: потолок, который молчит. Лимит
# выжимок в 20 штук, лимит обхода в 100 строк и таблица отчёта в 400 строк
# выглядели ровно так же, как «столько и было». Ни один тест этого не ловил,
# потому что все они спрашивали «правильно ли разобрано», а не «сколько
# осталось за кадром».
# ──────────────────────────────────────────────────────────────────────────────

# Заголовки, на которых своя регулярка выжимок давала False, а вакансия при этом
# ровно та, ради которой прогон запускается. Взяты из живой базы пользователя.
ENRICH_OURS = [
    "Разработчик бэкенда",
    "Платформенный инженер",
    "DevOps Engineer",
    "DevOps-инженер",
    "Архитектор решений",
    "Software Engineer",
    "Cloud Engineer",
    "Системный инженер в Yandex Cloud",
    "Инженер по высоконагруженным системам",
    "Разработчик распределённых систем",
    "Backend-разработчик микросервисов",
    "Бэкенд-разработчик (Go)",
]


def test_enrich_profile_filter_understands_russian():
    """Очередь выжимок узнаёт русские заголовки. Это тест на ЦЕНУ ошибки.

    Своя регулярка в cli дописывала русские основы без хвоста `\\w*`, но
    с замыкающим `\\b`: «платформ\\b» не совпадает с «платформенный», «бэкенд\\b» —
    с «бэкенда», «высоконагруж\\b» — с «высоконагруженных». Вся русская половина
    списка не срабатывала ни разу, а devops/architect/cloud/software engineer
    в ней отсутствовали вовсе.

    Видно это не было НИЧЕМ: вакансия оставалась в базе и в таблице, просто
    навсегда без описания — 1106 профильных строк из 4113 падали во вторую
    категорию очереди и при любом потолке не получали выжимку никогда.
    """
    from .cli import _PROFILE_RE, _enrich_rank

    lost = [t for t in ENRICH_OURS if not _PROFILE_RE.search(t)]
    eq(lost, [], "очередь выжимок не признала профильными русские заголовки")
    # Ранг — то, что реально решает, кому достанется слот: 0 — первая категория.
    bad = [t for t in ENRICH_OURS if _enrich_rank({"title": t})[0] != 0]
    eq(bad, [], "профильный заголовок попал во вторую категорию очереди выжимок")


def test_one_profile_regex_not_two():
    """Фильтр профессии на входе и отбор выжимок — ОДИН объект, а не две копии.

    Две регулярки на один вопрос расходятся всегда: они и разошлись — 3447
    заголовков против 2341 на одной и той же базе. Сравнение по `is`, а не по
    множеству совпадений: одинаковое поведение сегодня ничего не обещает завтра.
    """
    from .cli import _PROFILE_RE

    true(_PROFILE_RE is ATS_ROLE_RE,
         "в cli снова живёт вторая копия фильтра профессии — она разойдётся "
         "с ATS_ROLE_RE, вопрос только в том, когда")


def test_enrich_limit_covers_the_profile_part():
    """Умолчание потолка выжимок покрывает профильную часть типовой дельты.

    Было `--max-enrich 20` при дельте 3288: 97% строк не получали описания
    никогда, а карточку без описания написать нельзя. Проверяется не «20 это
    мало», а свойство: умолчание — сотни, и оно берёт профильные роли раньше
    непрофильных, то есть потолок режет хвост, а не голову.
    """
    from .cli import DEFAULT_MAX_ENRICH, _by_relevance, enrich_max

    true(DEFAULT_MAX_ENRICH >= 200,
         f"умолчание выжимок {DEFAULT_MAX_ENRICH} — это снова десятки, "
         f"а не покрытие профильной части дельты")
    eq(enrich_max(None), DEFAULT_MAX_ENRICH, "умолчание потолка выжимок потеряно")
    eq(enrich_max(0), None, "`0` обязан означать «без потолка», а не «ничего»")
    eq(enrich_max(7), 7, "явный потолок не доехал")

    # Профильные обязаны стоять раньше непрофильных, иначе потолок срежет их.
    rows = ([{"title": "Менеджер по продажам", "salary_from": 500000}] * 30
            + [{"title": "Разработчик бэкенда", "salary_from": None}] * 10)
    order = _by_relevance(rows)
    eq([r["title"] for r in order[:10]], ["Разработчик бэкенда"] * 10,
       "непрофильные с вилкой вытеснили профильные из головы очереди")


def test_enrich_truncation_is_spoken_out_loud():
    """Отрезанное потолком названо числом — и отдельно сказано, сколько среди
    отрезанного профильных ролей. «Обогащено 20» без второй половины фразы
    читается как «в дельте было 20»."""
    from .cli import enrich_summary

    cut = enrich_summary({"ok": 20, "blocked": 0, "failed": 0, "done": 118,
                          "delta": 3288, "todo": 20, "max_n": 20,
                          "skipped_by_max": 2692, "skipped_profile": 1106,
                          "profile_total": 1422})
    true("2692" in cut, "число отрезанного по потолку не названо")
    true("1106" in cut, "не сказано, сколько профильных ролей осталось без выжимки")
    true("кэшируются" in cut,
         "не сказано, что отрезанное достанется следующему прогону — "
         "иначе цифра читается как безвозвратная потеря")

    whole = enrich_summary({"ok": 40, "blocked": 0, "failed": 0, "done": 3,
                            "delta": 43, "todo": 40, "max_n": 400,
                            "skipped_by_max": 0, "skipped_profile": 0,
                            "profile_total": 31})
    true("ничего не отрезал" in whole,
         "потолок ничего не резал, а сводка об этом молчит — «обрезано» и "
         "«не обрезано» обязаны читаться по-разному")

    # Огрызок этапа из упавшего скана не должен ронять сборку отчёта.
    enrich_summary({})


def test_collect_truncation_is_visible_in_the_summary():
    """Обрезание обхода видно строкой и в терминале, и в markdown-отчёте.

    Признак берётся из сводки САМОГО источника, а не считается снаружи по
    «найдено >= лимита»: глубину задаёт `max(лимит, пол площадки)`, поэтому
    внешняя арифметика объявила бы обрезанием любую длинную ленту. Соврать про
    обрезание ровно так же плохо, как промолчать о нём.
    """
    from .cli import _limit_hit, _limit_lines, build_scan_report

    cut = Vacancy(source="linkedin", external_id="_summary", url="", title="сводка",
                  raw={"notes": ["ОБРЕЗАНО по потолку страниц (20): Germany (200) — "
                                 "за остальным нужен --limit больше"]})
    quiet = Vacancy(source="hh", external_id="_summary", url="", title="сводка",
                    raw={"notes": ["окно --days применяет сама площадка"]})
    true(bool(_limit_hit([cut], 200, 400)), "заявленное обрезание не распознано")
    eq(_limit_hit([quiet], 182, 400), "",
       "обрезание объявлено там, где источник о нём не заявлял")

    report = [{"source": "linkedin", "status": "ok", "found": 200,
               "limit_hit": "ОБРЕЗАНО по потолку страниц (20): Germany (200)"},
              {"source": "hh", "status": "ok", "found": 182, "limit_hit": ""}]
    lines = "\n".join(_limit_lines(report, 400))
    true("linkedin" in lines, "обрезанный источник не назван по имени")
    true("hh" not in lines, "необрезанный источник попал в предупреждение")
    eq(_limit_lines([r for r in report if r["source"] == "hh"], 400), [],
       "предупреждение об обрезании печатается там, где обрезания не было")

    text = build_scan_report({"collect": {"status": "ok", "report": report,
                                          "limit": 400, "found": 382}},
                             generated_at="2026-07-30T12:00:00+00:00", days=3)
    true("ОБРЕЗАН" in text, "в markdown-отчёте обрезание обхода не упомянуто")


def test_report_table_shows_the_whole_delta_by_default():
    """Таблица отчёта по умолчанию — ВСЯ дельта, а при потолке честна дважды.

    Было «первые 400 из 3288»: формально предупреждение стояло, практически
    2888 вакансий не смотрел никто. Отдельно проверяется, что при явном потолке
    названо не только «сколько за кадром», но и «сколько среди них профильных» —
    без второго числа предупреждение не говорит, дорого ли обрезание.
    """
    rows = [{"title": "Разработчик бэкенда" if i % 2 else "Менеджер по продажам",
             "company": "Acme", "source": "hh", "url": f"https://hh.ru/vacancy/{i}",
             "salary_from": None, "salary_to": None, "currency": None,
             "salary_gross": None, "salary_period": None, "location": "Москва",
             "published_at": "2026-07-29T00:00:00Z", "updated_at": None,
             "dup_key": f"acme|row {i}"} for i in range(900)]

    full = "\n".join(_delta_table(rows, days=3))
    eq(full.count("https://hh.ru/vacancy/"), 900,
       "умолчание таблицы отчёта снова режет дельту")
    true("ЗА КАДРОМ" not in full.upper(), "таблица не резала, а предупреждает")

    cut = "\n".join(_delta_table(rows, limit=100, days=3))
    eq(cut.count("https://hh.ru/vacancy/"), 100, "явный потолок таблицы не применился")
    true("100 ИЗ 900" in cut.upper(), "не сказано, сколько строк из скольких показано")
    true("800" in cut, "не названо число строк за кадром")
    # 450 профильных, 100 показанных — все профильные (они идут первыми),
    # значит за кадром остаётся 350 профильных.
    true("350" in cut, "не сказано, сколько ПРОФИЛЬНЫХ ролей осталось за кадром")


def test_source_notes_do_not_lie():
    """Пометка про `--days` не обещает того, чего в коде нет.

    Живой повод: geekjob обещал «--days применяется приблизительно», а замер
    давал 62 вакансии и при `--days 1`, и при `--days 120`. Разбор подтвердил:
    `ctx.days` в адаптере не читается ни разу.

    Проверка статическая, по AST адаптера: «пометка обещает окно» ⇒ «адаптер
    читает ctx.days». Живой замер стоит минут и требует сети, а разойтись
    пометка с кодом может в любом коммите.
    """
    import ast
    import inspect
    import textwrap

    from .cli import source_note
    from .sources import SOURCE_NOTES

    def reads_days(name: str) -> bool | None:
        fn = SOURCES.get(name)
        fn = getattr(fn, "func", fn)
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, TypeError, SyntaxError):
            return None
        return any(isinstance(n, ast.Attribute) and n.attr == "days"
                   and isinstance(n.value, ast.Name) and n.value.id == "ctx"
                   for n in ast.walk(tree))

    liars = []
    for name in SOURCES:
        text = source_note(name) or ""
        low = text.lower()
        # «--days НЕ применяется» — честное признание, его проверять не надо.
        promises = "--days" in low and "не применяется" not in low
        if promises and reads_days(name) is False:
            liars.append(f"{name}: «{text[:60]}», а ctx.days в адаптере не читается")
    eq(liars, [], "пометка источника обещает окно свежести, которого в коде нет")

    # Пометку чинят ПО МЕСТУ, рядом с адаптером. Слой поправок в cli.py — это
    # вторая правда, которая расходится с кодом, её породившим: так уже было с
    # geekjob (поправка в cli.NOTE_FIXUPS пережила починку самой константы).
    # Поэтому `source_note` обязана быть тонкой обёрткой над реестром источника.
    shadowed = [n for n in SOURCES if source_note(n) != SOURCE_NOTES.get(n)]
    eq(shadowed, [], "пометка подменяется слоем поправок в cli.py — чинить надо "
                     "по месту, в sources.py / sources_web.py / sources_auth.py")


def test_days_window_notes_match_live_behaviour():
    """[СЕТЬ] Пометка про окно сверяется с площадкой: `--days 1` против `--days 120`.

    Статическая проверка выше ловит «обещали окно, а кода нет». Эта — обратное
    и более коварное: код окно вроде бы применяет, а площадка на него не
    реагирует. Разница в цифрах — единственное доказательство.

    Расхождение здесь НЕ падение: у площадки может честно не быть ничего старше
    суток. Падение — только противоположное: пометка обещает окно, а числа
    совпадают до единицы при стократной разнице окон.
    """
    from .sources import SOURCE_NOTES

    checked = 0
    for name, text in sorted(SOURCE_NOTES.items()):
        low = (text or "").lower()
        if "--days" not in low or "не применяется" in low:
            continue
        if name in NEEDS_BROWSER_SET:
            continue
        try:
            narrow = _count_live(name, days=1)
            wide = _count_live(name, days=120)
        except (FetchError, OSError) as e:
            note(f"{name}: замер не сделан ({type(e).__name__}: {e})")
            continue
        checked += 1
        note(f"{name}: --days 1 → {narrow}, --days 120 → {wide}")
        if narrow and narrow == wide:
            FAILS.append(f"{name}: пометка обещает окно «{(text or '')[:50]}», "
                         f"но --days 1 и --days 120 дают одно и то же ({narrow}) — "
                         f"окно не применяется, пометка врёт")
    note(f"площадок с обещанным окном проверено живьём: {checked}")


def _count_live(name: str, *, days: int) -> int:
    """Сколько вакансий отдаёт источник в окне. Только для сверки пометок."""
    ctx = Ctx(query="Golang", days=days, limit=400)
    return sum(1 for v in SOURCES[name](ctx) if v.external_id != "_summary")


# ──────────────────────────────────────────────────────────────────────────────
# Служебное
# ──────────────────────────────────────────────────────────────────────────────

def _capture(fn) -> str:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()


def _silent(fn) -> int:
    import io
    from contextlib import redirect_stdout

    with redirect_stdout(io.StringIO()):
        return fn()


OFFLINE = (test_tally_invariant_catches_a_lost_row,
           test_profile_filter_keeps_ours,
           test_profile_filter_drops_theirs,
           test_profile_filter_on_real_database,
           test_delta_without_limit_returns_everything,
           test_delta_strict_flag_fails_on_truncation,
           test_scan_report_lists_every_source,
           test_scan_report_survives_a_dead_stage,
           test_delta_table_shows_every_row_it_was_given,
           test_every_platform_the_user_named_is_in_the_registry,
           test_retry_after_5xx_keeps_the_request_body,
           test_wall_in_russian_is_recognized_by_net,
           test_enrich_profile_filter_understands_russian,
           test_one_profile_regex_not_two,
           test_enrich_limit_covers_the_profile_part,
           test_enrich_truncation_is_spoken_out_loud,
           test_collect_truncation_is_visible_in_the_summary,
           test_report_table_shows_the_whole_delta_by_default,
           test_source_notes_do_not_lie)

NETWORK = (test_every_source_balances_what_it_offered,
           test_no_source_is_silently_missing,
           test_everything_collected_is_in_the_db,
           test_summaries_never_enter_the_delta,
           test_details_arrive_for_every_source,
           test_days_window_notes_match_live_behaviour)


def main(argv=None) -> int:
    global LIVE
    argv = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline" in argv
    fast = "--fast" in argv

    tests = list(OFFLINE)
    if offline:
        print("режим --offline: сетевые проверки пропущены, полнота обхода "
              "НЕ доказана этим прогоном")
    else:
        LIVE = Live(fast=fast)
        if fast and NEEDS_BROWSER_SET:
            print(f"режим --fast: браузерные площадки "
                  f"({', '.join(NEEDS_BROWSER_SET)}) идут как «ПРОПУЩЕН»")
        tests += list(NETWORK)

    try:
        for fn in tests:
            print(f"\n▸ {fn.__name__}", flush=True)
            try:
                fn()
            except (FetchError, OSError) as e:
                # Недоступная сеть — это «не проверено», и так оно и печатается.
                FAILS.append(f"{fn.__name__}: не отработал — {type(e).__name__}: {e}")
    finally:
        if LIVE:
            LIVE.cleanup()

    print()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print(f"все проверки прошли ({len(tests)} тестов"
          + (", без сети" if offline else ", включая живой обход площадок") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
