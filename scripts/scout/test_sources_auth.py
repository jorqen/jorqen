"""Тесты сессионных площадок и работы через настоящий браузер.

Проверяется не «работает ли сеть», а ровно те места, где ошибка НЕ ПАДАЕТ,
а молча уезжает в карточку или в отчёт:

* «от 350K ₽» разобранное как 350 рублей — вилка, которая врёт в тысячу раз
  и при этом выглядит фактом о зарплате;
* молча проигнорированный фильтр — выдача выглядит богатой (9165 вместо 94),
  а в ней бухгалтеры вместо Go;
* молча обрезанный сервером limit — 100 записей из 683 и бодрый отчёт;
* «нет сессии», превратившееся в «вакансий нет»;
* профиль браузера, из которого молча испарились куки.

    .venv/bin/python -m scripts.scout.test_sources_auth
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.parse

from datetime import datetime, timedelta, timezone

from . import auth, render
from . import sources_auth as sa
from .model import PLACEHOLDER_COMPANY, Vacancy, dup_key, no_dup_evidence
from .sources import Ctx

FAILS: list[str] = []

# Паузу между страницами тесты не ждут: она про вежливость к живым площадкам,
# а не про логику. Обнуляется один раз на модуль, чтобы каждый тест не помнил.
sa.PAGE_PAUSE = 0


def _ago(days: float) -> str:
    """ISO-дата «N дней назад» — для проверок окна свежести."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def eq(got, want, label):
    if got != want:
        FAILS.append(f"{label}: получено {got!r}, ожидалось {want!r}")


def ok(cond, label):
    if not cond:
        FAILS.append(label)


class patched:
    """Точечная подмена атрибута модуля на время теста."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Деньги через K
# ──────────────────────────────────────────────────────────────────────────────

def test_k_suffix_is_thousands():
    """«от 350K ₽» — это 350 000, а не 350.

    Общий parse_salary про суффикс не знает и отдавал 350 рублей: не падение,
    а уверенно напечатанная ложь в колонке «деньги». Строки настоящие, с geekjob.
    """
    cases = [
        ("от 350K ₽",        (350000, None, "RUB")),
        ("150K — 200K ₽",    (150000, 200000, "RUB")),
        ("350K — 500K ₽",    (350000, 500000, "RUB")),
        ("до 500K ₽",        (None, 500000, "RUB")),
        ("от 250 000 ₽",     (250000, None, "RUB")),   # без суффикса — как было
        ("$3000 - $5000",    (3000, 5000, "USD")),
        ("",                 (None, None, None)),
        (None,               (None, None, None)),
    ]
    for text, (wf, wt, wc) in cases:
        f, t, c, _ = sa.parse_money(text)
        eq((f, t, c), (wf, wt, wc), f"parse_money({text!r})")


def test_k_suffix_does_not_eat_words():
    """Множитель разворачивается только у отдельно стоящей K.

    Иначе «Kubernetes», «OK» и «K8s» внутри названия вакансии превращались бы
    в числа, а «100Kb» — в зарплату."""
    for text in ("Kubernetes, K8s, OK", "опыт 5 лет", "Senior Go (K8s)"):
        eq(sa.expand_k(text), text, f"expand_k не трогает {text!r}")
    eq(sa.expand_k("1,5K ₽"), "1500 ₽", "expand_k понимает дробный множитель")


def test_zero_salary_is_not_a_salary():
    """Ноль в поле вилки означает «не указано». «0–0 ₽» читается как предложение."""
    eq(sa._int(0), None, "_int(0) → None, а не 0")
    eq(sa._int("0"), None, "_int('0') → None")
    eq(sa._int(350000), 350000, "_int обычного числа")
    eq(sa._int(None), None, "_int(None)")


# ──────────────────────────────────────────────────────────────────────────────
# Молча проигнорированный фильтр
# ──────────────────────────────────────────────────────────────────────────────

def test_ignored_filter_is_a_failure_not_a_jackpot():
    """total, равный полному каталогу, — это провал фильтра, а не удача.

    Живой случай: у wantapply `{"query":"golang"}` возвращает 9165 вместо 94,
    у hirehi `q=golang` — 17455. Ошибки сервер не отдаёт, выдача выглядит богатой.
    """
    try:
        sa._guard_filter("wantapply", 9165, 9165, 'filters={"query":"golang"}')
        FAILS.append("_guard_filter пропустил проигнорированный фильтр")
    except sa.FilterIgnored:
        pass
    # Сработавший фильтр — молча дальше.
    sa._guard_filter("wantapply", 9165, 94, 'filters={"search":"golang"}')
    # Нет эталона (базовый запрос не удался) — не выдумываем провал.
    sa._guard_filter("wantapply", 0, 9165, "нет эталона")


# ──────────────────────────────────────────────────────────────────────────────
# Счётчики без потерь
# ──────────────────────────────────────────────────────────────────────────────

def test_tally_accounts_for_every_record():
    """Заявленное = унесённое + дубли. Иначе расхождение обязано быть видно."""
    t = sa.Tally("hirehi", claimed=1049, got=694, dropped_dup=355, pages=11)
    eq(t.lost, 0, "всё сошлось → потерь нет")
    v = t.summary()
    ok("НЕ ДОСЧИТАЛИСЬ" not in v.title, "при сошедшемся балансе про потери не пишем")
    eq(v.url, "", "сводка с пустым url — её режет store.query")
    eq(v.external_id, "_summary", "сводка помечена _summary")

    t2 = sa.Tally("hirehi", claimed=683, got=100, dropped_dup=0, pages=1)
    eq(t2.lost, 583, "сервер обрезал limit — потеря посчитана")
    ok("НЕ ДОСЧИТАЛИСЬ 583" in t2.summary().title,
       "потеря обязана быть в тексте сводки, а не только в raw")


def test_deliberate_cuts_are_not_losses():
    """Осознанно отрезанное — не «НЕ ДОСЧИТАЛИСЬ», но и не молчание.

    Живой случай: shadowhint показывал «НЕ ДОСЧИТАЛИСЬ 6» на шести записях без id.
    Это мусор площадки, а не пропавшие вакансии, и капсом об этом кричать нельзя —
    иначе капс перестают читать там, где он про настоящую потерю."""
    t = sa.Tally("shadowhint", claimed=946, got=700, dropped_dup=151,
                 skipped_old=80, unparsed=6, beyond_window=9, pages=10)
    eq(t.lost, 0, "каждое число объяснено → потерь нет")
    title = t.summary().title
    ok("НЕ ДОСЧИТАЛИСЬ" not in title, "объяснённое не выдаётся за потерю")
    for part in ("старше окна 80", "за окном не забирали 9", "не вакансий 6"):
        ok(part in title, f"в сводке не видно «{part}»: {title}")


def test_summary_carries_numbers_for_later():
    t = sa.Tally("geekjob", claimed=25, got=20, dropped_dup=5, pages=2)
    raw = t.summary().raw
    eq((raw["claimed"], raw["got"], raw["dropped_dup"], raw["lost"]), (25, 20, 5, 0),
       "цифры сводки лежат в raw машинно, а не только строкой")
    for key in ("skipped_old", "unparsed", "beyond_window", "requests"):
        ok(key in raw, f"в raw сводки нет {key} — считать баланс снаружи будет нечем")


# ──────────────────────────────────────────────────────────────────────────────
# Формулировки: одна не покрывает выдачу
# ──────────────────────────────────────────────────────────────────────────────

def test_cyrillic_query_becomes_latin_words():
    """hirehi и wantapply на кириллицу отвечают нулём, а слова складывают через И.

    «Go разработчик» у wantapply — 0 записей, «Backend Go» у hirehi — 2. Те же
    слова по отдельности: 330 и 277+180. Разбор на латинские слова — не мелочь,
    а разница между 94 и 625 у одного источника."""
    eq(sa.latin_terms(["Golang", "Go разработчик", "Backend Go"]),
       ["Golang", "Go", "Backend"], "остались латинские слова, без повторов")
    eq(sa.latin_terms(["бэкенд", "разработчик"]), [],
       "чистая кириллица не превращается в запрос-пустышку")
    eq(sa.latin_terms(["back-end", "C++"]), ["back-end", "C++"],
       "дефис и плюсы — часть слова, а не разделитель")


def test_platform_query_set_extends_but_never_replaces():
    """Проверенный набор площадки ДОПОЛНЯЕТ --query, а не заменяет его."""
    got = sa.merge_queries(["Rust"], sa.WANTAPPLY_QUERIES)
    eq(got[0], "Rust", "запрос пользователя идёт первым и не теряется")
    ok("backend" in got, "набор площадки на месте")
    eq(sa.merge_queries(["golang"], ("Golang", "Go")), ["golang", "Go"],
       "повтор в другом регистре не удваивает запрос к площадке")


def test_query_sets_are_measured_not_guessed():
    """Каждый набор — это замер, а не фантазия. Сторож против «допишу ещё пяток»:
    формулировка, которую площадка не находит, стоит запроса и не даёт ничего."""
    for name, qs_ in (("geekjob", sa.GEEKJOB_QUERIES),
                      ("hirehi", sa.HIREHI_QUERIES),
                      ("wantapply", sa.WANTAPPLY_QUERIES),
                      ("shadowhint", sa.SHADOWHINT_QUERIES)):
        ok(len(qs_) >= 3, f"{name}: одна-две формулировки — это снова 6% выдачи")
        eq(len(qs_), len({q.lower() for q in qs_}), f"{name}: повтор в наборе")
    # У hirehi и wantapply кириллице в наборе делать нечего — она даёт ноль.
    for name, qs_ in (("hirehi", sa.HIREHI_QUERIES), ("wantapply", sa.WANTAPPLY_QUERIES)):
        bad = [q for q in qs_ if any("а" <= c.lower() <= "я" for c in q)]
        eq(bad, [], f"{name}: кириллица в наборе — запрос заведомо в ноль")


# ──────────────────────────────────────────────────────────────────────────────
# hirehi
# ──────────────────────────────────────────────────────────────────────────────

def _hirehi_fake(total: int, per_page: int = sa.HIREHI_PAGE, site: int = 17563,
                 category: int = 4379, age_days=lambda i: 0.0):
    """Подделка API hirehi.

    Моделирует ровно то, чем площадка опасна: два уровня эталона (весь сайт и вся
    категория), нарезку по per_page и сортировку по дате — свежие первыми.
    """
    calls: list[str] = []

    def fake(url, **kw):
        calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "category" not in q:
            return {"total_count": site, "jobs": [], "has_more": False}
        if "subcategory" not in q and "search" not in q:
            return {"total_count": category, "jobs": [], "has_more": False}
        page = int(q.get("page", ["1"])[0])
        start = (page - 1) * per_page
        jobs = [{"id": 1000 + i, "title": f"Go Dev {i}", "company": f"Компания {i}",
                 "category": "development", "format": "удалённо",
                 "salary": "зп не указана",
                 "created_at": (datetime.now(timezone.utc)
                                - timedelta(days=age_days(i))).isoformat(),
                 "level": "senior"}
                for i in range(start, min(start + per_page, total))]
        return {"total_count": total, "jobs": jobs,
                "has_more": start + len(jobs) < total,
                "filter_counts": {"level": {"senior": total}}}

    return fake, calls


def test_hirehi_walks_every_page():
    """692 вакансии не помещаются в один ответ: сервер режет limit до 100 МОЛЧА.

    Один запрос вместо семи — это тихая потеря 85% выдачи, и она ничем себя
    не проявляет: ни ошибки, ни предупреждения, ровно 100 бодрых записей.
    """
    fake, calls = _hirehi_fake(683)
    with patched(sa, "fetch_json", fake):
        got = sa.src_hirehi(Ctx(query="Go", extra_queries=(), days=30))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(body), 683, "унесены все 683, а не первая страница")
    eq(summary.raw["lost"], 0, "потерь нет")
    ok(summary.raw["pages"] >= 7, f"страниц обойдено {summary.raw['pages']}, ждали ≥7")
    ok(all("limit=100" in c for c in calls if "page=" in c),
       "limit просим ровно 100 — больший сервер молча обрежет")


def test_hirehi_dies_on_ignored_filter():
    """Если фильтр вдруг перестанет работать, парсер обязан упасть, а не принести
    17 тысяч чужих вакансий как «выдачу по Go»."""
    fake, _ = _hirehi_fake(17563, site=17563)
    with patched(sa, "fetch_json", fake):
        try:
            sa.src_hirehi(Ctx(query="Go", extra_queries=()))
            FAILS.append("src_hirehi проглотил проигнорированный фильтр")
        except sa.FilterIgnored:
            pass


def test_hirehi_unknown_subcategory_is_caught_by_the_category_total():
    """Несуществующий слаг подкатегории отдаёт ВСЮ категорию — 4379 вместо 692.

    Это худшая из ловушек площадки: с эталоном «весь сайт» (17 563) проверка
    проходит на ура, потому что 4379 ≠ 17 563, — и в выдачу приезжает вся
    категория: дизайнеры интерфейсов, аналитики, 1С. Замерено живьём на
    devops, sre, qa, ruby, scala, csharp, javascript, 1c — каждый отдал 4379."""
    fake, _ = _hirehi_fake(4379, site=17563, category=4379)
    with patched(sa, "fetch_json", fake):
        try:
            sa.src_hirehi(Ctx(query="Go", extra_queries=()))
            FAILS.append("проигнорированная подкатегория проехала как выдача по Go")
        except sa.FilterIgnored as e:
            ok("4379" in str(e), f"в ошибке названа цифра-улика: {e}")


def test_hirehi_looks_wider_than_go_and_backend():
    """go+backend — это 692 из 4379 категории. Остальные 3687 не были видны вовсе."""
    ok(len(sa.HIREHI_SUBCATEGORIES) >= 5,
       f"подкатегорий всего {len(sa.HIREHI_SUBCATEGORIES)} — зона поиска не расширена")
    for must in ("go", "backend"):
        ok(must in sa.HIREHI_SUBCATEGORIES, f"{must} обязан остаться в наборе")
    # Слаги, которые площадка НЕ знает и в ответ отдаёт весь `development`.
    # Попадание любого из них в набор = 4379 чужих вакансий в выдаче.
    for ghost in ("devops", "sre", "qa", "ruby", "scala", "csharp", "javascript", "1c"):
        ok(ghost not in sa.HIREHI_SUBCATEGORIES,
           f"«{ghost}» — несуществующий слаг, площадка молча отдаст всю категорию")


def test_hirehi_applies_the_freshness_window_itself():
    """У площадки нет фильтра по дате, а старьё составляет большинство выдачи.

    Живой замер: 692 записи по go+backend, внутри трёх дней 97, старше месяца 242.
    Без окна в отчёте стоит «695» и читается как «695 свежих» — это ровно та
    цифра, которая вводит в заблуждение сильнее, чем ноль.

    Выдача отсортирована по дате, поэтому обход обязан ещё и ОСТАНОВИТЬСЯ на
    первой странице без единой свежей записи: качать месячный сток ради нуля невежливо."""
    # 300 записей: первые 40 свежие, остальные — месячной давности.
    fake, calls = _hirehi_fake(300, age_days=lambda i: 0.5 if i < 40 else 40.0)
    with patched(sa, "fetch_json", fake):
        got = sa.src_hirehi(Ctx(query="Go", extra_queries=(), days=3))
    body = [v for v in got if v.external_id != "_summary"]
    raw = [v for v in got if v.external_id == "_summary"][0].raw
    eq(len(body), 40, "в выдачу попали только свежие")
    ok(raw["skipped_old"] > 0, "отсеянное по окну посчитано, а не выброшено молча")
    eq(raw["lost"], 0, "осознанно отрезанное не выдаётся за потерю")
    ok(raw["beyond_window"] > 0,
       "незабранный хвост обязан быть отдельным числом в сводке")
    pages = [c for c in calls if "page=" in c and "subcategory" in c]
    ok(len(pages) <= 3, f"обход не остановился на границе окна: {len(pages)} страниц")


def test_hirehi_never_asks_in_cyrillic():
    """«бэкенд» у hirehi — 0 записей при 277 по «backend». Кириллический запрос
    здесь не «поиск на русском», а гарантированный ноль и лишний запрос."""
    fake, calls = _hirehi_fake(300)
    with patched(sa, "fetch_json", fake):
        sa.src_hirehi(Ctx(query="Golang", extra_queries=("Go разработчик",), days=30))
    searches = [urllib.parse.parse_qs(urllib.parse.urlsplit(c).query).get("search", [""])[0]
                for c in calls]
    bad = [s for s in searches if any("а" <= ch.lower() <= "я" for ch in s)]
    eq(bad, [], f"в поиск ушла кириллица: {bad}")
    ok("Golang" in searches and "Go" in searches,
       f"слова запроса разобраны и ушли по отдельности: {sorted(set(searches))}")


def test_hirehi_hybrid_is_not_a_yes_or_no():
    """«гибрид Москва» — это не удалёнка и не офис. Булево здесь было бы выдумкой."""
    eq(sa._hirehi_format("удалённо"), (True, None), "удалённо → remote")
    eq(sa._hirehi_format("удалённо по РФ"), (True, None), "удалённо по РФ → remote")
    eq(sa._hirehi_format("гибрид Москва"), (None, "Москва"),
       "гибрид → remote неизвестен, город вынут")
    eq(sa._hirehi_format("офис Санкт-Петербург"), (False, "Санкт-Петербург"),
       "офис → не удалёнка, город вынут")
    eq(sa._hirehi_format(None), (None, None), "пусто → ничего не выдумываем")


def test_hirehi_url_needs_only_id():
    """Slug угадывать не надо: /development/x-69754 редиректит на канонический."""
    eq(sa.hirehi_url({"id": 69754, "category": "development"}),
       "https://hirehi.ru/development/x-69754", "ссылка строится из id")
    eq(sa.hirehi_url({"id": 1}), "https://hirehi.ru/development/x-1",
       "категории нет → дефолтная, ссылка всё равно рабочая")


def test_hirehi_salary_period_is_not_invented():
    """Площадка периода не называет — значит период None, а не «в месяц»."""
    v = sa._hirehi_vacancy({"id": 1, "title": "Go", "category": "development",
                            "salary_display": "от 300 000 ₽", "format": "удалённо"})
    eq((v.salary_from, v.currency, v.salary_period), (300000, "RUB", None),
       "вилка есть, период не назван → None")
    eq(v.salary_str(), "от 300 000 RUB", "в строке денег нет выдуманного «/мес»")


def test_hirehi_no_salary_means_no_period():
    """ld+json отдаёт unitText=MONTH даже при value='зпнеуказана'.

    «/мес» без суммы читается как обещание ежемесячной выплаты, которого
    площадка не давала."""
    lo, hi, cur, period = sa._ld_salary(
        {"currency": "RUB",
         "value": {"value": "зпнеуказана", "unitText": "MONTH"}})
    eq((lo, hi, period), (None, None, None), "нет суммы → нет и периода")
    eq(cur, "RUB", "валюта сохраняется")


def test_ld_json_survives_nonce_attribute():
    """У hirehi у тега ld+json есть nonce, и он стоит ПЕРЕД type — поиск по
    точной строке тега такой скрипт не находил бы вовсе."""
    html = ('<script nonce="abc" type="application/ld+json">'
            '{"@type":"JobPosting","title":"Go"}</script>')
    eq((sa._ld_json(html) or {}).get("title"), "Go", "ld+json найден при nonce")
    eq(sa._ld_json('<script type="application/ld+json">{"@type":"WebSite"}</script>'),
       None, "чужой @type не подсовывается вместо вакансии")


# ──────────────────────────────────────────────────────────────────────────────
# geekjob
# ──────────────────────────────────────────────────────────────────────────────

_GEEKJOB_ROW = {
    "position": "Senior Golang Engineer", "salary": "от 350K ₽",
    "country": "Россия", "city": None,
    "jobFormat": {"remote": True, "relocate": False, "parttime": False,
                  "inhouse": False},
    "log": {"modify": "8 июля", "archived": None},
    "company": {"type": "1", "name": "Компания", "logo": None, "id": "abc"},
    "id": "6a4e0ea09fef8988d10b3707",
}


def test_geekjob_does_not_invent_the_year():
    """В списке дата — «8 июля» БЕЗ ГОДА. Достроить год догадкой значит соврать
    на границе года; точная дата берётся с карточки."""
    v = sa._geekjob_vacancy(_GEEKJOB_ROW)
    eq(v.published_at, None, "года нет → published_at пустой, а не выдуманный")
    eq(v.raw["modified_human"], "8 июля", "человекочитаемая дата не потеряна")


def test_geekjob_empty_salary_is_not_zero():
    """salary приезжает ПУСТОЙ СТРОКОЙ, а не null. Спутать её с нулём нельзя."""
    v = sa._geekjob_vacancy({**_GEEKJOB_ROW, "salary": ""})
    eq((v.salary_from, v.salary_to), (None, None), "пустая строка → вилки нет")
    eq(v.salary_str(), "", "и в карточке пусто, а не «0 ₽»")


def test_geekjob_parses_k_salary():
    v = sa._geekjob_vacancy(_GEEKJOB_ROW)
    eq((v.salary_from, v.currency), (350000, "RUB"), "«от 350K ₽» → 350 000 RUB")
    eq(v.remote, True, "jobFormat.remote → remote")
    eq(v.url, "https://geekjob.ru/vacancy/6a4e0ea09fef8988d10b3707", "ссылка из id")


def test_geekjob_merges_queries_by_id():
    """qs — полнотекст, а не тег: go (18) шире golang (7). Берём объединение,
    иначе половина выдачи теряется молча."""
    def fake(url, **kw):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = q.get("qs", [""])[0]
        ids = {"golang": ["a", "b"], "go": ["b", "c", "d"]}.get(query, [])
        return {"documentsCount": len(ids), "pagecount": 1, "page": 1,
                "data": [{**_GEEKJOB_ROW, "id": i} for i in ids]}

    with patched(sa, "fetch_json", fake), patched(sa, "GEEKJOB_QUERIES", ()):
        got = sa.src_geekjob(Ctx(query="golang", extra_queries=("go",)))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(sorted(v.external_id for v in body), ["a", "b", "c", "d"],
       "объединение по id, без потерь и без дублей")
    eq(summary.raw["dropped_dup"], 1, "пересечение запросов посчитано, а не забыто")
    eq(summary.raw["lost"], 0, "заявленное = унесённое + дубли")


def test_geekjob_asks_more_than_one_wording():
    """Одна «Golang» — это 7 записей из 93 доступных, то есть 6% выдачи.

    Проверяем не текст константы, а поведение: к площадке ушёл ВЕСЬ набор,
    а не только --query."""
    asked: list[str] = []

    def fake(url, **kw):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        asked.append(q.get("qs", [""])[0])
        return {"documentsCount": 1, "pagecount": 1, "page": 1,
                "data": [{**_GEEKJOB_ROW, "id": f"id-{len(asked)}"}]}

    with patched(sa, "fetch_json", fake):
        sa.src_geekjob(Ctx(query="Golang", extra_queries=()))
    ok(len(asked) >= len(sa.GEEKJOB_QUERIES),
       f"к площадке ушло {len(asked)} формулировок при наборе "
       f"из {len(sa.GEEKJOB_QUERIES)}")
    for must in ("backend", "Go"):
        ok(must in asked, f"формулировка «{must}» до площадки не дошла")


def test_geekjob_dedupes_inside_one_answer():
    """Площадка дублирует записи В ОДНОМ ответе: по «backend» documentsCount 93,
    а разных id в них 69. Без дедупа «унесли 93» — цифра о строках, а не о
    вакансиях, и в базу поедут повторы одной и той же ссылки."""
    def fake(url, **kw):
        rows = [{**_GEEKJOB_ROW, "id": i} for i in ["a", "b", "a", "c", "b", "a"]]
        return {"documentsCount": len(rows), "pagecount": 1, "page": 1, "data": rows}

    with patched(sa, "fetch_json", fake), patched(sa, "GEEKJOB_QUERIES", ()):
        got = sa.src_geekjob(Ctx(query="backend", extra_queries=()))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(sorted(v.external_id for v in body), ["a", "b", "c"],
       "шесть строк площадки — три вакансии")
    eq(summary.raw["lost"], 0, "повторы посчитаны дублями, а не потеряны")
    ok("дублирует записи внутри одного ответа" in summary.title,
       f"про дубли площадки в сводке не сказано: {summary.title}")


def test_geekjob_empty_answer_is_a_failure():
    """Ноль вакансий при ответившей площадке — сломанный парсер, а не «нет работы»."""
    with patched(sa, "fetch_json", lambda url, **kw: {"documentsCount": 0,
                                                      "pagecount": 1, "data": []}):
        try:
            sa.src_geekjob(Ctx(query="Go", extra_queries=()))
            FAILS.append("src_geekjob вернул пустоту вместо падения")
        except sa.FetchError:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# wantapply
# ──────────────────────────────────────────────────────────────────────────────

_WANTAPPLY_ROW = {
    "id": "ffd7881b", "title": "Lead Backend Developer (Golang)",
    "companyName": "Joom", "url": "lead-backend-developer-golang-at-joom",
    "levels": ["lead"], "workplaceTypes": ["hybrid"], "remote": False,
    "jobLocations": [{"iso3": "PRT", "name_ru": "Португалия"}],
    "publishedAt": "2026-07-29T10:00:00.000Z",
    "description": "<p><strong>О нас</strong></p><p>Пишем на Go</p>",
    "decoyApplyUrl": "https://wantapply.com/decoy/apply",
    "decoyContactEmail": "decoy@wantapply.com",
    "salary": "", "salaryMin": None, "salaryMax": None,
}


def test_wantapply_decoys_never_become_a_contact():
    """decoyApplyUrl и decoyContactEmail — ПРИМАНКИ площадки, а не работодатель.

    Положить их в employer_url значит отправить человека по подложной ссылке
    и выдать это за «контакт как можно ближе к работодателю»."""
    v = sa._wantapply_vacancy(_WANTAPPLY_ROW)
    eq(v.employer_url, None, "приманка не подставляется как ссылка работодателя")
    eq(v.raw["decoy"]["applyUrl"], "https://wantapply.com/decoy/apply",
       "приманка сохранена в raw — чтобы было видно, что она была")


def test_wantapply_html_description_becomes_text():
    v = sa._wantapply_vacancy(_WANTAPPLY_ROW)
    ok("<p>" not in (v.description or ""), "HTML описания вычищен")
    ok("Пишем на Go" in (v.description or ""), "текст описания на месте")


def test_wantapply_period_comes_from_its_own_field():
    """salaryUnit — единственный честный источник периода у этой площадки."""
    v = sa._wantapply_vacancy({**_WANTAPPLY_ROW, "salaryMin": 90000,
                               "salaryMax": 120000, "salaryCurrency": "USD",
                               "salaryUnit": "year"})
    eq(v.salary_period, "year", "период взят из salaryUnit")
    eq(v.salary_str(), "90 000–120 000 USD/год", "период виден в строке денег")
    v2 = sa._wantapply_vacancy({**_WANTAPPLY_ROW, "salaryMin": 90000,
                                "salaryCurrency": "USD", "salaryUnit": None})
    eq(v2.salary_period, None, "поля нет → период не выдумывается")


def test_wantapply_missing_session_does_not_lose_the_catalog():
    """Протухший токен стоит прямых ссылок в ATS — и НИЧЕГО больше.

    Уронить из-за него весь сбор значило бы потерять 9165 вакансий, собранных
    анонимно, ради ссылки, которой всё равно не будет."""
    with patched(auth, "session_token", lambda p, **kw: (None, "токен истёк 26.07")):
        note = sa.enrich_apply_urls([sa._wantapply_vacancy(_WANTAPPLY_ROW)], limit=5)
    ok("истёк" in note, f"причина названа: {note!r}")
    ok("auth login wantapply" in note, "сказано, что именно делать")


def test_wantapply_takes_the_union_of_wordings():
    """search=Golang → 94, search=Go → 330, search=backend → 344, объединение 625.

    Транспортных потерь у площадки нет вовсе (94 из 94 заявленных доезжали), —
    и именно поэтому потеря была невидимой: сводка сходилась идеально, просто
    спрашивали одно слово из трёх."""
    catalog = {"Golang": [f"g{i}" for i in range(4)],
               "Go": [f"g{i}" for i in range(4)] + [f"o{i}" for i in range(6)],
               "backend": [f"b{i}" for i in range(7)]}
    asked: list[str] = []

    def fake(url, **kw):
        spec = json.loads(urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["filters"][0]))
        query = spec.get("search")
        if query is None:
            return {"total": 9168, "data": [], "hasNextPage": False}
        asked.append(query)
        ids = catalog.get(query, [])
        return {"total": len(ids), "hasNextPage": False,
                "data": [{**_WANTAPPLY_ROW, "id": i, "url": f"slug-{i}"} for i in ids]}

    with patched(sa, "fetch_json", fake), \
            patched(sa, "WANTAPPLY_QUERIES", ("Golang", "Go", "backend")):
        got = sa.src_wantapply(Ctx(query="Golang", extra_queries=("Go разработчик",)))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(body), 17, "унесено объединение трёх формулировок, а не одна из них")
    eq(summary.raw["dropped_dup"], 4, "пересечение Golang ⊂ Go посчитано дублями")
    eq(summary.raw["lost"], 0, "баланс сошёлся")
    ok("Go разработчик" not in asked,
       f"кириллическая фраза ушла на площадку, где она даёт 0: {asked}")


def test_wantapply_says_that_days_is_not_applied():
    """625 записей без окна — это не «625 свежих». Молчать об этом нельзя."""
    def fake(url, **kw):
        spec = json.loads(urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["filters"][0]))
        if spec.get("search") is None:
            return {"total": 9168, "data": [], "hasNextPage": False}
        return {"total": 1, "hasNextPage": False, "data": [_WANTAPPLY_ROW]}

    with patched(sa, "fetch_json", fake):
        got = sa.src_wantapply(Ctx(query="Golang", extra_queries=()))
    summary = [v for v in got if v.external_id == "_summary"][0]
    ok("--days не применяется" in summary.title,
       f"в сводке не сказано про окно: {summary.title}")
    eq(sa.SOURCE_NOTES_AUTH["wantapply"], sa.WANTAPPLY_DAYS_NOTE,
       "то же самое обязано быть в реестре примечаний, а не только в сводке")


def test_wantapply_apply_url_reads_the_real_link():
    """Из ответа ручки берётся именно ссылка, а не первое попавшееся поле."""
    with patched(sa, "fetch_json",
                 lambda url, **kw: {"applyUrl": "https://boards.greenhouse.io/x/jobs/1",
                                    "email": "hr@example.com"}):
        eq(sa.wantapply_apply_url("slug", "tok"),
           "https://boards.greenhouse.io/x/jobs/1", "прямая ссылка в ATS")
    with patched(sa, "fetch_json", lambda url, **kw: {"nothing": "here"}):
        eq(sa.wantapply_apply_url("slug", "tok"), None,
           "ссылки нет → None, а не мусор из чужого поля")


def test_wantapply_dead_statuses_are_filtered_and_named():
    """Каталог отдаёт снятые вакансии обычными строками: deleted находится
    поиском наравне с живыми. В карточки им нельзя, но и исчезнуть молча
    они не должны — иначе баланс сводки перестаёт сходиться."""
    rows = [
        {**_WANTAPPLY_ROW, "id": "live", "url": "slug-live", "status": "published",
         "statusChangedAt": None, "expirationDate": "2026-10-27T18:21:31.217Z"},
        {**_WANTAPPLY_ROW, "id": "arch", "url": "slug-arch", "status": "archived",
         "statusChangedAt": "2026-05-01T00:00:00.000Z"},
        {**_WANTAPPLY_ROW, "id": "del", "url": "slug-del", "status": "deleted",
         "statusChangedAt": "2025-09-09T16:33:09.864Z"},
    ]

    def fake(url, **kw):
        spec = json.loads(urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["filters"][0]))
        if spec.get("search") is None:
            return {"total": 9168, "data": [], "hasNextPage": False}
        return {"total": len(rows), "hasNextPage": False, "data": rows}

    with patched(sa, "fetch_json", fake), \
            patched(sa, "WANTAPPLY_QUERIES", ("Golang",)):
        got = sa.src_wantapply(Ctx(query="Golang", extra_queries=()))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq([v.external_id for v in body], ["live"], "в выдаче только published")
    eq(summary.raw["skipped_dead"], 2, "снятые посчитаны, а не потеряны")
    eq(summary.raw["lost"], 0, "баланс сводки сошёлся")
    ok("archived 1" in summary.title and "deleted 1" in summary.title,
       f"в сводке названо, что именно отсеяно: {summary.title}")
    v = body[0]
    eq(v.published_at, "2026-07-29T10:00:00+00:00",
       "published_at взят из publishedAt площадки, а не из времени краулинга")
    eq(v.raw["status"], "published", "status сохранён в raw")
    eq(v.raw["statusChangedAt"], None, "statusChangedAt сохранён в raw")
    eq(v.raw["expirationDate"], "2026-10-27T18:21:31.217Z",
       "expirationDate сохранён в raw")


def test_wantapply_check_reads_status_not_presence():
    """Сам факт «нашлось по слагу» — ещё не жизнь: живой пример
    senior-golang-backend-at-steelmount находится как ни в чём не бывало,
    но со status=deleted и датой снятия 2025-09-09."""
    def catalog(row, total):
        return lambda url, **kw: {"total": total, "hasNextPage": False,
                                  "data": [row] if row else []}

    alive = {**_WANTAPPLY_ROW, "status": "published", "expirationDate": _ago(-90)}
    with patched(sa, "fetch_json", catalog(alive, 1)):
        verdict, why = sa.wantapply_check(
            "https://wantapply.com/jobs/lead-backend-developer-golang-at-joom")
    eq(verdict, "ЖИВА", f"published → ЖИВА ({why})")
    ok("подозрительна" not in why, "живой срок годности не пугает")

    stale = {**alive, "expirationDate": _ago(3)}
    with patched(sa, "fetch_json", catalog(stale, 1)):
        verdict, why = sa.wantapply_check("https://wantapply.com/jobs/x")
    eq(verdict, "ЖИВА", "истёкший expirationDate — ещё не смерть")
    ok("подозрительна" in why, f"но пометка обязана быть: {why!r}")

    dead = {**_WANTAPPLY_ROW, "url": "senior-golang-backend-at-steelmount",
            "status": "deleted", "statusChangedAt": "2025-09-09T16:33:09.864Z"}
    with patched(sa, "fetch_json", catalog(dead, 1)):
        verdict, why = sa.wantapply_check(
            "https://wantapply.com/jobs/senior-golang-backend-at-steelmount")
    eq(verdict, "МЕРТВА", "deleted → МЕРТВА")
    ok("2025-09-09" in why, f"дата смерти в пояснении: {why!r}")


def test_wantapply_check_does_not_trust_an_ignored_filter():
    """total=9200 по фильтру-слагу — ловушка №1 (весь каталог), а не «нашлось
    9200». Уверенный вердикт из такого ответа — ложь."""
    with patched(sa, "fetch_json",
                 lambda url, **kw: {"total": 9200, "hasNextPage": True,
                                    "data": [_WANTAPPLY_ROW, _WANTAPPLY_ROW]}):
        verdict, why = sa.wantapply_check("https://wantapply.com/jobs/whatever")
    eq(verdict, "НЕИЗВЕСТНО", "проигнорированный фильтр не даёт вердикта")
    ok("проигнорирован" in why, f"причина названа: {why!r}")


def test_wantapply_check_rechecks_by_id_before_declaring_death():
    """Слага нет в каталоге — ещё не смерть: при переименовании вакансии слаг
    меняется, а id остаётся. Без UUID честный ответ — НЕИЗВЕСТНО; с UUID —
    перепроверка по id, и только 404 там означает МЕРТВА."""
    empty = {"total": 0, "hasNextPage": False, "data": []}
    uuid = "3f36d31e-e5f7-4e5c-a2be-03f9aeef7f04"

    with patched(sa, "fetch_json", lambda url, **kw: empty):
        verdict, _ = sa.wantapply_check("https://wantapply.com/jobs/gone")
    eq(verdict, "НЕИЗВЕСТНО", "без id смерть не выдумывается")

    def gone(url, **kw):
        if "filters=" in url:
            return empty
        raise sa.FetchError(url, "HTTP 404", 404)

    with patched(sa, "fetch_json", gone):
        verdict, why = sa.wantapply_check("https://wantapply.com/jobs/gone", uuid)
    eq(verdict, "МЕРТВА", f"404 по id → МЕРТВА ({why})")

    def renamed(url, **kw):
        if "filters=" in url:
            return empty
        return {**_WANTAPPLY_ROW, "status": "published"}

    with patched(sa, "fetch_json", renamed):
        verdict, _ = sa.wantapply_check("https://wantapply.com/jobs/gone", uuid)
    eq(verdict, "ЖИВА", "по id запись жива → слаг просто сменился")


# ──────────────────────────────────────────────────────────────────────────────
# shadowhint
# ──────────────────────────────────────────────────────────────────────────────

def test_shadowhint_without_session_says_so():
    """Разлогин обязан отличаться от «вакансий нет»: одно чинится заходом
    пользователя, другое — нет."""
    with patched(auth, "session_token", lambda p, **kw: (None, "нет куки auth_token")):
        try:
            sa.src_shadowhint(Ctx(query="Go", extra_queries=()))
            FAILS.append("src_shadowhint промолчал вместо NeedsLogin")
        except sa.NeedsLogin as e:
            ok("auth login shadowhint" in e.reason, "сказано, что делать")
            ok("auth_token" in e.reason, "назван точный признак разлогина")


def test_shadowhint_401_is_login_not_breakage():
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", _raiser(sa.FetchError("u", "HTTP 401", 401))):
        try:
            sa.src_shadowhint(Ctx(query="Go", extra_queries=()))
            FAILS.append("401 не превратился в NeedsLogin")
        except sa.NeedsLogin:
            pass


def test_shadowhint_unknown_shape_fails_loudly():
    """Форму ответа зафиксировать заранее не вышло (анонимно везде 401).

    Поэтому единственный безопасный режим: либо разобрали, либо упали с перечнем
    приехавших ключей. Молчаливый ноль на площадке с 37 тысячами вакансий —
    худший исход."""
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json",
                    lambda url, **kw: {"total": 37000, "unexpected": {"a": 1}}):
        try:
            sa.src_shadowhint(Ctx(query="Go", extra_queries=()))
            FAILS.append("неизвестная форма ответа не вызвала падения")
        except sa.FetchError as e:
            ok("37000" in e.reason, "в тексте названо, сколько обещал сервер")


def test_shadowhint_maps_flexible_field_names():
    """Имена полей неизвестны, поэтому берём по списку кандидатов — но id и
    название обязательны, без них запись не вакансия."""
    v = sa._shadowhint_vacancy({"id": 7, "name": "Go Developer",
                                "company_name": "ООО", "salary": "от 250K ₽",
                                "message_url": "https://t.me/c/1/2",
                                "published_at": "2026-07-30T10:00:00Z"})
    eq((v.external_id, v.title, v.company), ("7", "Go Developer", "ООО"),
       "разные имена полей разобраны")
    eq((v.salary_from, v.currency), (250000, "RUB"), "вилка с K разобрана")
    eq(v.url, "https://t.me/c/1/2", "ссылка на исходный пост важнее ссылки на витрину")
    eq(sa._shadowhint_vacancy({"title": "без id"}), None, "без id — не вакансия")


def test_shadowhint_nested_values_do_not_crash():
    """location вполне может приехать словарём — форма ответа не зафиксирована."""
    v = sa._shadowhint_vacancy({"id": 1, "title": "Go",
                                "company": {"name": "ООО"},
                                "location": {"name": "Москва"}})
    eq((v.company, v.location), ("ООО", "Москва"), "вложенные значения развёрнуты")


# Настоящая форма ответа площадки — camelCase. Именно на ней парсер и промахнулся.
_SHADOWHINT_ROW = {
    "id": "8efa8e45", "title": "Golang Developer", "companyName": "Rocket Tech",
    "channelUsername": "godevjob", "telegramMessageId": 2617,
    "messageLink": "https://t.me/godevjob/2617", "externalLink": "",
    "messageDate": "2026-07-30T13:31:58Z", "createdAt": "2026-07-30T14:00:00Z",
    "salaryText": "от 250K ₽", "salaryCurrency": "RUB", "remoteType": "remote",
    "rawText": "Ищем Go-разработчика", "categoryNames": ["Backend"],
}


def test_shadowhint_link_points_at_the_telegram_post():
    """Форма ответа площадки — camelCase (`messageLink`), а список кандидатов был
    на snake_case. Совпадений ноль — и ВСЕ 789 вакансий получали одну и ту же
    ссылку-заглушку на витрину.

    Это не косметика: с общим url вакансия неотличима от соседней ни для человека,
    ни для ключа дубля, а «контакт как можно ближе к работодателю» превращается
    в ссылку на список."""
    v = sa._shadowhint_vacancy(_SHADOWHINT_ROW)
    eq(v.url, "https://t.me/godevjob/2617", "ссылка на исходный пост в канале")
    eq(v.raw["url_guessed"], False, "ссылка настоящая, а не додуманная")
    eq(v.published_at[:10], "2026-07-30", "дата взята из messageDate")
    eq((v.salary_from, v.currency), (250000, "RUB"), "вилка и валюта из camelCase-полей")
    eq(v.remote, True, "remoteType разобран")
    # externalLink пустой — и подставлять вместо него ничего нельзя.
    eq(v.employer_url, None, "пустая внешняя ссылка не превращается в контакт")
    v2 = sa._shadowhint_vacancy({**_SHADOWHINT_ROW,
                                 "externalLink": "https://acme.com/jobs/1"})
    eq(v2.employer_url, "https://acme.com/jobs/1",
       "ссылка работодателя из поста идёт в employer_url, а не подменяет пост")


def _shadowhint_fake(catalog: dict, per_page: int = sa.SHADOWHINT_PAGE):
    """Подделка API shadowhint: totalCount/totalPages/vacancies, свежие первыми."""
    calls: list[str] = []

    def fake(url, **kw):
        calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        query = q.get("search_query", [""])[0]
        page = int(q.get("page", ["1"])[0])
        rows = catalog.get(query, [])
        start = (page - 1) * per_page
        return {"totalCount": len(rows), "perPage": per_page,
                "totalPages": max(1, -(-len(rows) // per_page)), "page": page,
                "vacancies": rows[start:start + per_page]}

    return fake, calls


def test_shadowhint_walks_to_the_last_page():
    """Пагинация до конца по totalPages, а не «пока страница не опустела».

    Страница ровно 100: per_page=50 отдаёт 50, per_page=100 отдаёт 100,
    а per_page=200 отдаёт ДВАДЦАТЬ — просить больше сотни здесь хуже, чем сто."""
    rows = [{**_SHADOWHINT_ROW, "id": f"v{i}", "messageLink": f"https://t.me/c/{i}",
             "messageDate": _ago(0.5)} for i in range(250)]
    fake, calls = _shadowhint_fake({"Go": rows}, per_page=100)
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", fake), patched(sa, "SHADOWHINT_QUERIES", ("Go",)):
        got = sa.src_shadowhint(Ctx(query="Go", extra_queries=(), days=3))
    body = [v for v in got if v.external_id != "_summary"]
    raw = [v for v in got if v.external_id == "_summary"][0].raw
    eq(len(body), 250, "унесены все 250, а не первая страница")
    eq(raw["lost"], 0, "баланс сошёлся")
    ok(all("per_page=100" in c for c in calls),
       "per_page просим ровно 100 — на 200 сервер отдаёт 20")


def test_shadowhint_merges_wordings_by_id():
    """`Go` 756 и `Golang` 581, но у второго 318 записей, которых нет в первом,
    а у «backend» — 1464. Одна формулировка теряет большинство архива."""
    mk = lambda name, n: [{**_SHADOWHINT_ROW, "id": f"{name}{i}",
                           "messageLink": f"https://t.me/c/{name}{i}",
                           "messageDate": _ago(0.5)} for i in range(n)]
    go, golang = mk("x", 3), mk("x", 3) + mk("y", 2)
    fake, _ = _shadowhint_fake({"Go": go, "Golang": golang})
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", fake), \
            patched(sa, "SHADOWHINT_QUERIES", ("Go", "Golang")):
        got = sa.src_shadowhint(Ctx(query="Go", extra_queries=(), days=3))
    body = [v for v in got if v.external_id != "_summary"]
    raw = [v for v in got if v.external_id == "_summary"][0].raw
    eq(len(body), 5, "объединение, а не одна формулировка")
    eq(raw["dropped_dup"], 3, "пересечение посчитано дублями")
    eq(raw["lost"], 0, "баланс сошёлся")


def test_shadowhint_stops_at_the_edge_of_the_window():
    """Архив площадки — 39 190 записей, и по профилю 88% из них старше месяца.

    Без окна «shadowhint 789» читается как «789 свежих». Выдача отсортирована
    по дате, поэтому обход прекращается на первой странице без единой свежей записи —
    и незабранный хвост печатается отдельным числом, а не тонет в потерях."""
    rows = ([{**_SHADOWHINT_ROW, "id": f"n{i}", "messageLink": f"https://t.me/c/n{i}",
              "messageDate": _ago(0.5)} for i in range(30)]
            + [{**_SHADOWHINT_ROW, "id": f"o{i}", "messageLink": f"https://t.me/c/o{i}",
                "messageDate": _ago(60)} for i in range(470)])
    fake, calls = _shadowhint_fake({"Go": rows}, per_page=100)
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", fake), patched(sa, "SHADOWHINT_QUERIES", ("Go",)):
        got = sa.src_shadowhint(Ctx(query="Go", extra_queries=(), days=3))
    body = [v for v in got if v.external_id != "_summary"]
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(len(body), 30, "унесены только свежие")
    eq(summary.raw["lost"], 0, "отрезанное окном — не потеря")
    # Страница, на которой окно кончилось, дочитывается ЦЕЛИКОМ (30 свежих + 70
    # старых), и только следующая — вся старая — останавливает обход.
    eq(summary.raw["skipped_old"], 170, "старьё с прочитанных страниц посчитано")
    eq(summary.raw["beyond_window"], 300, "хвост, за которым не ходили, назван числом")
    eq(len(calls), 2, f"обход не остановился на границе окна: {len(calls)} запросов")
    ok("за окном не забирали 300" in summary.title,
       f"хвост не виден в тексте сводки: {summary.title}")


def test_a_seen_record_does_not_block_the_early_stop():
    """Свежесть считается ДО дедупликации — иначе окно перестаёт работать.

    Замерено живьём: по «backend» площадка отдаёт 1717 записей, свежих 20. Вторая
    и дальнейшие формулировки видят те же старые записи как ДУБЛИ, страница
    перестаёт считаться «целиком старой», и обход честно докачивает восемнадцать
    страниц архива ради нуля новых вакансий. Для площадки это тридцать лишних
    запросов подряд — ровно то, за что нас уже закрывала rabota.ru."""
    rows = ([{**_SHADOWHINT_ROW, "id": f"n{i}", "messageLink": f"https://t.me/c/n{i}",
              "messageDate": _ago(0.5)} for i in range(30)]
            + [{**_SHADOWHINT_ROW, "id": f"o{i}", "messageLink": f"https://t.me/c/o{i}",
                "messageDate": _ago(60)} for i in range(470)])
    fake, calls = _shadowhint_fake({"Go": rows, "Golang": rows}, per_page=100)
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", fake), \
            patched(sa, "SHADOWHINT_QUERIES", ("Go", "Golang")):
        got = sa.src_shadowhint(Ctx(query="Go", extra_queries=(), days=3))
    summary = [v for v in got if v.external_id == "_summary"][0]
    golang = [c for c in calls if "search_query=Golang" in c]
    eq(len(golang), 2, f"вторая формулировка не остановилась на окне: {len(golang)} стр.")
    eq(summary.raw["lost"], 0, "баланс сошёлся и на второй формулировке")


def test_shadowhint_junk_rows_are_not_a_loss():
    """Шесть записей без id — это мусор площадки, а не пропавшие вакансии.

    Раньше они падали в `lost`, и сводка кричала «НЕ ДОСЧИТАЛИСЬ 6» ровно там,
    где считать было нечего. Капс обязан оставаться редким."""
    rows = [{**_SHADOWHINT_ROW, "id": f"v{i}", "messageLink": f"https://t.me/c/{i}",
             "messageDate": _ago(0.5)} for i in range(4)]
    rows += [{"title": "без id", "messageDate": _ago(0.5)} for _ in range(2)]
    fake, _ = _shadowhint_fake({"Go": rows})
    with patched(auth, "session_token", lambda p, **kw: ("t", "жив")), \
            patched(sa, "fetch_json", fake), patched(sa, "SHADOWHINT_QUERIES", ("Go",)):
        got = sa.src_shadowhint(Ctx(query="Go", extra_queries=(), days=3))
    summary = [v for v in got if v.external_id == "_summary"][0]
    eq(summary.raw["unparsed"], 2, "мусор посчитан отдельно")
    eq(summary.raw["lost"], 0, "и потерей не назван")
    ok("НЕ ДОСЧИТАЛИСЬ" not in summary.title, f"ложная тревога в сводке: {summary.title}")


def _raiser(exc):
    def fn(*a, **kw):
        raise exc
    return fn


# ──────────────────────────────────────────────────────────────────────────────
# Сессии: что именно даёт вход
# ──────────────────────────────────────────────────────────────────────────────

def test_expired_token_is_reported_with_a_date():
    """У wantapply срок лежит прямо в куке — протухание видно БЕЗ запроса.

    Иначе 401 на ручке контактов читается как «у вакансии нет прямой ссылки»."""
    dead = urllib.parse.quote(json.dumps({"token": "t", "tokenExpires": 1785086334005}))
    token, why = auth.token_from_cookie("wantapply", dead)
    eq(token, None, "истёкший токен не выдаётся за рабочий")
    ok("истёк" in why and "26.07.2026" in why, f"названа дата протухания: {why!r}")

    alive = urllib.parse.quote(json.dumps({"token": "t", "tokenExpires": 4102444800000}))
    token2, why2 = auth.token_from_cookie("wantapply", alive)
    eq(token2, "t", "живой токен отдаётся")
    ok("жив до" in why2, "срок жизни назван")


def test_plain_token_cookie_is_taken_as_is():
    """shadowhint кладёт в куку сам токен, а не JSON."""
    token, _why = auth.token_from_cookie("shadowhint", "abc.def.ghi")
    eq(token, "abc.def.ghi", "строковый токен взят как есть")
    eq(auth.token_from_cookie("shadowhint", "")[0], None, "пустая кука → нет токена")


def test_geekjob_login_is_never_requested():
    """Вход на geekjob не даёт НИЧЕГО (сверено: documentsCount совпал один в один).

    Просить его — врать про пользу и обесценивать список «залогинься»."""
    state, why = auth.session_probe("geekjob")
    eq(state, "not_needed", f"geekjob: вход не нужен ({why})")
    eq(sa.LOGIN_VALUE["geekjob"], None, "в реестре пользы входа тоже честный None")


def test_anonymous_platforms_are_not_in_the_todo_list():
    """hirehi и geekjob не попадают в «что от тебя требуется» никогда: их выдача
    анонимна целиком."""
    with patched(auth, "session_probe", lambda p, **kw: ("anonymous", "нет кук")):
        got = sa.needs_login()
    ok("geekjob" not in got, "geekjob не просит входа")
    ok("hirehi" not in got, "hirehi не просит входа — вся выдача анонимна")
    eq(sorted(got), ["shadowhint", "wantapply"],
       "просим вход только там, где он что-то даёт")


def test_hirehi_anonymous_view_is_not_advertised_as_broken():
    """Раньше анонимный hirehi получал совет «обнови сессию заходом browse».

    Это дезинформация: обновлять нечего, анонимно отдаётся вся выдача."""
    state, why = auth.login_state("hirehi", "<a>Войти</a>")
    eq(state, "anonymous", "аноним распознан")
    ok("хватает" in why, f"сказано, что для сбора хватает: {why!r}")
    ok("browse" not in why, "устаревший совет про browse убран")


def test_login_value_matches_the_registry():
    """Один и тот же факт в двух местах обязан совпадать, иначе разъедется."""
    for name, value in sa.LOGIN_VALUE.items():
        cfg = auth.PLATFORMS.get(name, {})
        eq(cfg.get("login_gains") is None, value is None,
           f"{name}: польза входа согласована между auth и sources_auth")


# ──────────────────────────────────────────────────────────────────────────────
# Настоящий браузер
# ──────────────────────────────────────────────────────────────────────────────

def test_mock_keychain_is_always_disabled():
    """Дефолт Playwright `--use-mock-keychain` МОЛЧА уничтожает куки профиля.

    Замерено на копии профиля, запуск на about:blank, ни одного визита: 3376 кук
    → 41, из них hh 54 → 0. Браузер при этом открывается и страницы грузятся —
    просто везде аноним. Это самая тихая из возможных поломок, поэтому флаг
    проверяется тестом, а не комментарием."""
    ok("--use-mock-keychain" in render.IGNORE_DEFAULTS,
       "снятие --use-mock-keychain не должно исчезнуть при рефакторинге")


def test_real_browser_never_spoofs_the_user_agent():
    """У настоящего браузера UA настоящий — подставлять нечего.

    Прежний headless представлялся HeadlessChrome, из-за чего hirehi отдавал 403;
    лечили это подстановкой Chrome/142, которого на машине не существует."""
    src = open(os.path.join(os.path.dirname(__file__), "render.py"),
               encoding="utf-8").read()
    real = src[src.index("def _render_real"):src.index("def _render_bundled")]
    ok("user_agent" not in real, "в пути настоящего браузера UA не подменяется")
    ok("headless=False" in src, "настоящий браузер запускается не в headless")


def test_unknown_browser_is_rejected_by_name():
    try:
        render.pick_browser("firefox")
        FAILS.append("pick_browser проглотил неизвестный браузер")
    except ValueError as e:
        ok("firefox" in str(e), "в ошибке названо, что именно не понято")
    eq(render.pick_browser("chromium"), "chromium", "встроенный шелл выбирается явно")


def test_lock_holder_ignores_a_dead_pid():
    """Протухший лок Chromium снимает сам — считать его занятостью нельзя,
    иначе профиль после падения прогона больше не откроется никогда."""
    with tempfile.TemporaryDirectory() as d:
        eq(render.lock_holder(d), None, "нет лока → никто не держит")
        os.symlink("хост-999999", os.path.join(d, "SingletonLock"))
        eq(render.lock_holder(d), None, "мёртвый pid → профиль свободен")
        os.remove(os.path.join(d, "SingletonLock"))
        os.symlink(f"хост-{os.getpid()}", os.path.join(d, "SingletonLock"))
        eq(render.lock_holder(d), os.getpid(), "живой pid → профиль занят")


def test_busy_profile_explains_itself():
    """Занятый профиль — это понятная строка, а не TargetClosedError со стектрейсом."""
    with tempfile.TemporaryDirectory() as d:
        os.symlink(f"хост-{os.getpid()}", os.path.join(d, "SingletonLock"))
        with patched(render, "profile_path", lambda b: d):
            try:
                render._check_free("yandex", d)
                FAILS.append("_check_free не заметил живой лок")
            except render.ProfileBusy as e:
                ok("scout" in str(e).lower(), "сказано, что профиль наш, а не браузер юзера")
                ok(str(os.getpid()) in str(e), "назван pid держателя")


def _make_cookie_db(path: str, rows: list[tuple[str, str]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, encrypted_value BLOB, "
                "path TEXT, expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER, "
                "samesite INTEGER, last_access_utc INTEGER, creation_utc INTEGER)")
    con.executemany("INSERT INTO cookies VALUES (?,?,?,'/',0,1,0,1,0,0)",
                    [(h, n, b"v10x") for h, n in rows])
    con.commit()
    con.close()


def test_profile_keeps_only_platform_domains():
    """Профиль накапливает чужие куки сам: метрики и виджеты на самих площадках
    ставят yandex.ru, vk.com, mail.ru — после нескольких прогонов было 230 кук
    по 38 доменам вместо 114 засеянных. Это тот же allowlist, что и для `.auth/`."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "Default", "Cookies")
        _make_cookie_db(db, [("hh.ru", "hhtoken"), (".spb.hh.ru", "geo"),
                             ("hirehi.ru", "hirehi-refresh-token"),
                             ("yandex.ru", "yandexuid"), ("vk.com", "remixsid"),
                             (".mail.ru", "t")])
        with patched(render, "profile_path", lambda b: d):
            dropped = render.prune_profile("yandex")
        eq(dropped, 3, "выкинуты ровно чужие домены")
        con = sqlite3.connect(db)
        left = sorted(h for (h,) in con.execute("SELECT host_key FROM cookies"))
        con.close()
        eq(left, [".spb.hh.ru", "hh.ru", "hirehi.ru"],
           "куки площадок (включая гео-поддомены hh) остались")


def test_top_up_never_overwrites_a_rotated_cookie():
    """Досыпаем только НЕДОСТАЮЩИЕ куки.

    Иначе старая копия токена из живого браузера затрёт ту, что площадка уже
    проротировала в профиле, — и мы своими руками сожжём свежую сессию."""
    from . import cookiesrc

    added: list[dict] = []

    class FakeCtx:
        def cookies(self):
            return [{"domain": ".hirehi.ru", "path": "/",
                     "name": "hirehi-refresh-token", "value": "СВЕЖИЙ"}]

        def add_cookies(self, cookies):
            added.extend(cookies)

    class FakeSrc:
        cookies = [{"domain": "hirehi.ru", "path": "/",
                    "name": "hirehi-refresh-token", "value": "СТАРЫЙ"},
                   {"domain": "hirehi.ru", "path": "/", "name": "current_category",
                    "value": "dev"}]

    with patched(cookiesrc, "resolve", lambda *a, **kw: FakeSrc()):
        n = render.top_up_cookies(FakeCtx(), ("hirehi.ru",))
    eq(n, 1, "досыпана ровно одна недостающая кука")
    eq([c["name"] for c in added], ["current_category"],
       "проротированный токен не перезаписан старой копией")


def test_rotating_hosts_are_known():
    """hirehi и wantapply ротируют refresh-токен; забыть их в списке значит
    вернуть «протухание за три часа» на запасном пути."""
    for host in ("hirehi.ru", "wantapply.com"):
        ok(host in render.ROTATING_SESSION_HOSTS, f"{host} помечен как ротационный")


# ──────────────────────────────────────────────────────────────────────────────
# Ключ дубля: ложная склейка дороже лишнего раскола
# ──────────────────────────────────────────────────────────────────────────────

def _key(source, external_id, company, title, url=None):
    return Vacancy(source=source, external_id=external_id, company=company,
                   title=title, url=url or f"https://{source}/x/{external_id}").dup_key


def test_hidden_employer_never_merges_strangers():
    """«NDA» — это не компания, а отсутствие компании.

    Замер по базе: ключ «nda|backend» объединил 62 РАЗНЫХ работодателя, «nda|go»
    и «nda|golang» — по 34. Дальше `run_enrich` выбрасывает записи с уже виденным
    ключом, и до карточки доезжает одна из шестидесяти двух."""
    keys = {_key("hirehi", str(i), "NDA", "Backend-разработчик") for i in range(62)}
    eq(len(keys), 62, "62 работодателя под NDA схлопнулись в один ключ")
    for hidden in ("NDA", "nda (iGaming)", "Название скрыто", "не указана",
                   "Название скрыто (финтех)", ""):
        a = _key("hirehi", "1", hidden, "Разработчик системы биллинга")
        b = _key("hirehi", "2", hidden, "Разработчик системы биллинга")
        ok(a != b, f"заглушка «{hidden}» всё ещё склеивает разных работодателей")


def test_aggregator_token_is_not_an_employer():
    """У доски-агрегатора `company` — имя ДОСКИ. «jobgether|full software stack» —
    это 43 разных объявления разных компаний, а не одна вакансия."""
    keys = {_key("ats:lever:jobgether", str(i), "Jobgether", "Full Stack Software")
            for i in range(43)}
    eq(len(keys), 43, "объявления агрегатора склеились в один ключ")
    # А вот обычная ATS-доска — это настоящий работодатель, и склейка по нему честная:
    # под «canonical» лежат вакансии Canonical, а не полутора сотен разных компаний.
    for real in ("canonical", "datadog", "okx", "gitlab", "binance"):
        ok(real not in PLACEHOLDER_COMPANY,
           f"«{real}» — настоящий работодатель, а не доска; так теряется честная склейка")


def test_one_employer_many_openings_do_not_merge():
    """У «Ozon» в одной только hirehi 21 разная вакансия с костяком «go», у
    «Wildberries» — 16, у «ВКонтакте» — 14. Это НЕ одна вакансия в двадцати одном
    экземпляре: id разные, url разные, объявления разные.

    Костяк из одних стековых слов — совпадение, а не доказательство."""
    keys = {_key("hirehi", str(i), "Ozon", "Go-разработчик") for i in range(21)}
    eq(len(keys), 21, "21 вакансия Ozon схлопнулась в «ozon|go»")
    eq(len({_key("hirehi", "1", "Фарпост", "SQL-разработчик"),
            _key("hirehi", "2", "Фарпост", "Разработчик SQL")}), 2,
       "«sql» — тоже стек, а не название конкретной вакансии")


def test_a_real_cross_platform_duplicate_still_merges():
    """Ради раскола нельзя ломать то, ради чего ключ существует.

    Настоящий случай из базы: одна вакансия Okko лежит на hh, Хабре и shadowhint —
    и заслуживает одного захода за деталями, а не трёх.

    Заголовки взяты из базы ДОСЛОВНО (30.07.2026). Это важно: раньше здесь стоял
    придуманный «Senior Golang разработчик …» для shadowhint, и тест доказывал не
    то, что бывает, а то, что автор вообразил. В живых данных грейд у одной и той
    же вакансии на разных площадках совпадает — расходятся регистр, дефис и
    скобки."""
    a = _key("hh", "1", "Okko", "Golang-разработчик (команда платформы персонализации)")
    b = _key("habr", "2", "Okko", "Golang-разработчик (Команда платформы персонализации)")
    c = _key("shadowhint", "3", "Okko",
             "Golang разработчик — команда платформы персонализации")
    eq(len({a, b, c}), 1, "одна вакансия на трёх площадках дала три разных ключа")
    # А другая вакансия того же работодателя обязана остаться отдельной.
    d = _key("hh", "4", "Okko", "Инженер по нагрузочному тестированию")
    ok(d != a, "разные вакансии одного работодателя склеились")


def test_the_reason_for_not_merging_is_named():
    """Ключ, который «вдруг стал другим», не диагностируется. Причина — строкой."""
    eq(no_dup_evidence("", "backend go"), "работодатель не назван", "пустая компания")
    ok("заглушка" in (no_dup_evidence("nda", "backend go") or ""),
       "про заглушку сказано словами")
    ok("только стек" in (no_dup_evidence("ozon", "go") or ""),
       "про стековый костяк сказано словами")
    eq(no_dup_evidence("ozon", "биллинга система"), None,
       "нормальная пара причин для отказа не даёт")


def test_bare_key_call_stays_conservative():
    """Голый вызов «сравни две строки» развести записи нечем — и выдумывать
    идентичность там, где о записи ничего не известно, нельзя."""
    eq(dup_key("Т-Банк", "Golang-разработчик"),
       dup_key("Т-Банк", "разработчик Golang"),
       "без источника и id ключ остаётся прежним «компания|название»")
    ok(dup_key("Ozon", "Golang разработчик") != dup_key("Т-Банк", "Golang разработчик"),
       "разные компании не склеиваются никогда")


def test_grade_is_not_noise():
    """Грейд различает вакансии, а не украшает их.

    Замер по базе 30.07.2026: пока «junior/senior/старший» лежали в `_NOISE`,
    склеивались 42 записи, которые вакансиями-дублями не являются — у Sezzle
    Junior и Senior «Software Engineer with Accounting Experience» в четырёх
    странах, у Canonical Junior/Senior Ubuntu, у OKX Junior/Senior Product
    Manager. До карточки доезжала одна из пары, вторая пропадала молча.
    """
    ok(dup_key("Sezzle", "Junior Software Engineer with Accounting Experience (Brazil)")
       != dup_key("Sezzle", "Senior Software Engineer with Accounting Experience (Brazil)"),
       "Junior и Senior одной роли — разные вакансии, а не дубль")
    ok(dup_key("Авито", "Python-разработчик в команду модерации")
       != dup_key("Авито", "Старший Python-разработчик в команду Модерации"),
       "русский грейд тоже различает вакансии")
    # Но сам по себе грейд ничего не доказывает: «senior go» — это стек плюс
    # уточнение, и склеивать по такому костяку по-прежнему нельзя.
    ok("только стек" in (no_dup_evidence("ozon", "go senior") or ""),
       "костяк «грейд + стек» не может быть доказательством дубля")


def test_registry_is_complete():
    eq(sorted(sa.SOURCES_AUTH), ["geekjob", "hirehi", "shadowhint", "wantapply"],
       "все четыре площадки в реестре")
    for name in sa.SOURCES_AUTH:
        ok(name in auth.PLATFORMS, f"{name} описан в реестре площадок auth")
        ok(name in sa.LOGIN_VALUE, f"{name} назвал, что даёт вход")


# ──────────────────────────────────────────────────────────────────────────────
# Продление сессий: предупреждение обязано быть точным, иначе его перестанут читать
# ──────────────────────────────────────────────────────────────────────────────

def _auth_dir(d: str):
    """Подменяет `.auth/` на временный каталог. Настоящий каталог тесты не трогают
    ни на чтение, ни на запись: там предъявительский доступ к живым аккаунтам."""
    return patched(auth, "AUTH_DIR", d)


def _write_state(d: str, platform: str, state: dict) -> None:
    with open(os.path.join(d, f"{platform}.json"), "w", encoding="utf-8") as f:
        json.dump(state, f)


def test_hirehi_session_is_read_only_from_our_own_file():
    """Сессия hirehi берётся ТОЛЬКО из .auth/hirehi.json.

    Не стилистика, а защита от прожига: refresh-токен hirehi один на всех, и
    заход куками живого браузера ротирует его — у пользователя вкладка
    разлогинивается мгновенно. Поэтому проба обязана не ходить в cookiesrc
    вовсе, даже если куки там есть."""
    from . import cookiesrc

    called: list[str] = []

    def boom(*a, **kw):
        called.append("resolve")
        raise AssertionError("cookiesrc для hirehi звать нельзя")

    with tempfile.TemporaryDirectory() as d, _auth_dir(d), \
            patched(cookiesrc, "resolve", boom):
        state, why = auth.session_probe("hirehi")
        eq(state, "anonymous", "нет своего файла — сессии нет")
        _write_state(d, "hirehi", {"cookies": [
            {"domain": ".hirehi.ru", "name": "hirehi-refresh-token", "value": "T"}]})
        state, why = auth.session_probe("hirehi")
        eq(state, "logged_in", "свой файл с refresh-кукой — сессия есть")
    eq(called, [], "куки браузера для hirehi не читались ни разу")


def test_unreadable_browser_does_not_hide_a_live_session_in_another():
    """Залоченная база одного браузера не отменяет поиск в остальных.

    Раньше здесь стоял ранний выход, и волна при открытом Chrome собирала
    shadowhint анонимом — 401, ноль вакансий, и НИ СЛОВА в предупреждении:
    состояние `unknown` в него не попадает по построению. Живая сессия в
    Яндексе лежала рядом и не спрашивалась. Молчаливый ноль дороже любой
    ложной тревоги."""
    from . import cookiesrc

    class Src:
        cookies = [{"name": "auth_token", "value": "ЖИВОЙ"}]

        def line(self):
            return "тест"

    def resolve(spec=None, domains=(), **kw):
        if spec in (None, "", "auto", "yandex"):
            raise sqlite3.OperationalError("database is locked")
        return Src()

    with patched(cookiesrc, "resolve", resolve), \
            patched(cookiesrc, "BROWSER_NAMES", ("yandex", "chrome")):
        token, why = auth.session_token("shadowhint")
    eq(token, "ЖИВОЙ", f"живую сессию в другом браузере не нашли ({why})")
    ok("chrome" in why, "источник не назван — непонятно, откуда взялась сессия")


def test_unreadable_cookie_db_is_not_a_logout():
    """Не смог прочитать куки ≠ пользователь вышел.

    Chrome держит свою базу залоченной, и раньше это давало «❌ разлогин» в
    каждой волне, запущенной при открытом браузере. Ложная тревога в отчёте
    дороже пропущенной: после третьей такой строки блок перестают читать."""
    from . import cookiesrc

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    with patched(cookiesrc, "resolve", locked):
        state, why = auth.session_probe("shadowhint")
    eq(state, "unknown", "залоченная база — неизвестность, а не разлогин")
    ok(auth.UNREADABLE in why, "пояснение названо тем же признаком, по которому решали")


def test_preflight_reports_only_what_it_knows():
    """В предупреждение попадает `anonymous` и только он.

    `unknown` — отсутствие знания; выдать его за поломку значит тревожить в
    каждом прогоне. Живые сессии тоже не печатаем: строка «shadowhint жив» не
    меняет ни одного решения, а место в отчёте занимает."""
    from . import authrefresh

    rows = [{"platform": "shadowhint", "state": "unknown", "why": "не понять",
             "loss": "сбор по площадке: всё", "critical": True, "renewable": False},
            {"platform": "wantapply", "state": "logged_in", "why": "жив",
             "loss": "ссылки в ATS", "critical": False, "renewable": False}]
    with patched(authrefresh, "preflight", lambda *a, **kw: rows):
        eq(authrefresh.preflight_lines(), [], "ни неизвестность, ни живое не печатаются")
        eq(authrefresh.preflight_block(), "", "пустой список не даёт заголовка")


def test_preflight_is_silent_about_platforms_that_lose_nothing():
    """habr не должен появляться в предупреждении.

    Вход туда даёт свою историю откликов, но её тянет отдельная синхронизация,
    а не сбор. Строка «залогинься ради ничего» — тот самый шум, из-за которого
    перестают читать весь список."""
    from . import authrefresh

    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        authrefresh.forget()  # кэш на процесс — иначе тест прочитает чужую пробу
        seen = [r["platform"] for r in authrefresh.preflight(cookies_from="none")]
        authrefresh.forget()
    ok("habr" not in seen, "площадка, от разлогина которой ничего не теряется, молчит")
    ok("shadowhint" in seen, "площадка, без которой нет сбора, — на месте")


def test_hirehi_is_not_promised_to_renew_itself_without_a_session():
    """«Поднимется само» обещается, только если есть что поднимать.

    Клиенту hirehi нужен живой refresh-токен из .auth/hirehi.json. Без файла
    `auth refresh hirehi` заведомо вернёт отказ, и советовать его — значит
    послать человека выполнить команду вместо той, что действительно чинит."""
    from . import authrefresh

    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        ok(not authrefresh.can_renew("hirehi"), "нет файла — продлевать нечего")
        _write_state(d, "hirehi", {"cookies": [
            {"domain": ".hirehi.ru", "name": "hirehi-refresh-token", "value": "T"}]})
        ok(authrefresh.can_renew("hirehi"), "есть файл — продление возможно")
        # careered наоборот: источник — постоянный профиль, а слепок только итог.
        ok(authrefresh.can_renew("careered"), "careered продлевается без слепка")


def test_localstorage_token_is_merged_not_overwritten():
    """Свежий токен careered кладётся в слепок, не выбрасывая остального.

    Переписать файл целиком ради одной строки — значит потерять куки, которых
    в постоянном профиле уже нет, а в слепке они ещё живы."""
    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        _write_state(d, "careered", {
            "cookies": [{"domain": "careered.io", "name": "cf_clearance", "value": "C"}],
            "origins": [{"origin": "https://careered.io",
                         "localStorage": [{"name": "access_token", "value": "СТАРЫЙ"},
                                          {"name": "theme", "value": "dark"}]}]})
        auth.save_localstorage_token("careered", "https://careered.io",
                                     "access_token", "СВЕЖИЙ")
        token, _ = auth.bearer_from_state("careered")
        eq(token, "СВЕЖИЙ", "токен обновлён")
        with open(os.path.join(d, "careered.json"), encoding="utf-8") as f:
            state = json.load(f)
        eq([c["name"] for c in state["cookies"]], ["cf_clearance"], "куки на месте")
        eq(sorted(i["name"] for i in state["origins"][0]["localStorage"]),
           ["access_token", "theme"], "соседний ключ localStorage не затёрт")
        eq(os.stat(os.path.join(d, "careered.json")).st_mode & 0o777, 0o600,
           "права слепка 0600 — там предъявительский доступ")


class _FakePW:
    """Playwright ровно в том объёме, который трогает renew_hirehi."""

    def __init__(self, state: dict, *, boom: str | None = None):
        self.state, self.boom, self.saved = state, boom, []

    # sync_playwright() → контекстный менеджер → объект с .chromium
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, **kw):
        return self

    def new_context(self, **kw):
        return self

    def new_page(self):
        return self

    def close(self):
        pass

    def goto(self, *a, **kw):
        # Ротация происходит В МОМЕНТ захода — как у настоящего клиента,
        # который меняет токен сам, ещё до того, как страница дорисуется.
        self.state["cookies"] = [{"domain": "hirehi.ru", "path": "/",
                                  "name": "hirehi-refresh-token",
                                  "value": "ПРОРОТИРОВАННЫЙ"}]
        if self.boom == "goto":
            raise RuntimeError("net::ERR_CONNECTION_RESET")

    def wait_for_timeout(self, ms):
        if self.boom == "wait":
            raise RuntimeError("Target page closed")

    def content(self):
        return "<html>Личный кабинет</html>"

    def storage_state(self):
        return dict(self.state)


def test_hirehi_saves_rotation_even_when_the_page_dies():
    """Обрыв страницы НЕ отменяет запись сессии.

    Самое дорогое место всей конструкции. Клиент hirehi меняет refresh-токен
    в момент захода; если после этого страница отвалилась, а мы вышли не
    записав, у нас в файле останется кука, которую сервер уже обесценил, —
    и сессия сгорит именно там, где мы обещали её сберечь. Проверяется на обоих
    обрывах: до отрисовки и после неё."""
    from . import authrefresh

    for boom in ("goto", "wait"):
        with tempfile.TemporaryDirectory() as d, _auth_dir(d):
            _write_state(d, "hirehi", {"cookies": [
                {"domain": "hirehi.ru", "path": "/",
                 "name": "hirehi-refresh-token", "value": "СТАРЫЙ"}]})
            fake = _FakePW({"cookies": [], "origins": []}, boom=boom)
            with patched(authrefresh, "_playwright", lambda: fake):
                ok_, why = authrefresh.renew_hirehi(wait_ms=0)
            ok(not ok_, f"обрыв на {boom} — не успех")
            ok("сохранён" in why, f"обрыв на {boom}: сказано, что снимок сохранён")
            got, _ = auth.state_cookie("hirehi")
            eq(got, "ПРОРОТИРОВАННЫЙ",
               f"обрыв на {boom}: в файле новый токен, а не сожжённый старый")


def test_hirehi_renewal_confirms_by_the_page_not_by_the_absence_of_errors():
    """Успех — это признаки входа на странице, а не «ничего не упало».

    Анонимный вид у hirehi отдаётся с кодом 200: без проверки вёрстки продление
    рапортовало бы об успехе ровно тогда, когда сессия умерла."""
    from . import authrefresh

    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        _write_state(d, "hirehi", {"cookies": [
            {"domain": "hirehi.ru", "path": "/",
             "name": "hirehi-refresh-token", "value": "СТАРЫЙ"}]})
        fake = _FakePW({"cookies": [], "origins": []})
        with patched(authrefresh, "_playwright", lambda: fake):
            ok_, why = authrefresh.renew_hirehi(wait_ms=0)
        ok(ok_, f"признаки входа на странице — продление удалось ({why})")

        fake = _FakePW({"cookies": [], "origins": []})
        with patched(fake, "content", lambda: "<html>Войти</html>"), \
                patched(authrefresh, "_playwright", lambda: fake):
            ok_, why = authrefresh.renew_hirehi(wait_ms=0)
        ok(not ok_, "анонимный вид с кодом 200 успехом не считается")
        ok("auth login hirehi" in why, "названо, чем это чинится")


def test_careered_snapshot_comes_from_the_persistent_profile():
    """Токен careered снимается с постоянного профиля и ложится в слепок.

    Сессия careered живёт в localStorage, а не в куках, — поэтому `cookiesrc`
    её не видит в принципе, и разовый слепок стареет. Источник обязан быть тот,
    где сессия продлевается сама: постоянный профиль scout."""
    from . import authrefresh, render

    asked: list[tuple] = []

    def fake_eval(url, script, **kw):
        asked.append((url, script))
        return "СВЕЖИЙ-BEARER"

    with tempfile.TemporaryDirectory() as d, _auth_dir(d), \
            patched(render, "evaluate_on", fake_eval):
        ok_, why = authrefresh.renew_careered()
    ok(ok_, f"токен снят и записан ({why})")
    ok(asked and asked[0][0].startswith("https://careered.io"),
       "спрашивали именно careered")
    ok("localStorage" in asked[0][1], "спрашивали localStorage, а не куки")

    # Пустой профиль — это отказ с названной командой, а не молчаливый успех:
    # записать в слепок None значило бы объявить сессию живой.
    with tempfile.TemporaryDirectory() as d, _auth_dir(d), \
            patched(render, "evaluate_on", lambda *a, **kw: None):
        ok_, why = authrefresh.renew_careered()
        # Проверка ВНУТРИ подмены: снаружи `.auth/` уже настоящий, и на машине
        # с живым входом careered она читала бы чужой файл и зеленела вхолостую.
        ok(not auth.have("careered"), "пустой слепок не создаётся")
    ok(not ok_, "нет токена в профиле — не успех")
    ok("auth login careered" in why, "названо, чем это чинится")


def test_snapshot_is_taken_even_when_already_logged_in():
    """Слепок снимается и на ветке «уже залогинен».

    Именно этот случай и есть обычный для careered: сессия живёт в постоянном
    профиле и продлевается там сама, а `.auth/careered.json` может отстать или
    не появиться вовсе. Выйти по «уже залогинен», не сняв слепок, значит
    оставить сборщик анонимом при живом входе."""
    class FakePage:
        def __init__(self, value):
            self.value = value

        def evaluate(self, script):
            return self.value

    cfg = auth.PLATFORMS["careered"]
    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        ok(auth._snapshot_localstorage(FakePage("BEARER"), "careered", cfg),
           "живой токен снят")
        eq(auth.bearer_from_state("careered")[0], "BEARER", "и лёг в слепок")
    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        ok(not auth._snapshot_localstorage(FakePage(None), "careered", cfg),
           "пустой localStorage — честный отказ")
        ok(not auth.have("careered"), "пустого слепка не создаётся")
    # Площадке без клиентской сессии здесь делать нечего, и это не провал.
    with tempfile.TemporaryDirectory() as d, _auth_dir(d):
        ok(auth._snapshot_localstorage(FakePage(None), "hh", auth.PLATFORMS["hh"]),
           "площадка без localStorage-сессии проходит молча")


def test_private_endpoint_outranks_the_markup():
    """Вход определяется приватной ручкой, а не словом «Войти» в разметке.

    Стоило трёх ложных «войди ещё раз» подряд 07.08.2026. У hirehi форма входа
    рендерится ВСЕГДА, в том числе залогиненному, а «Мои отклики» не попадают
    в серверный HTML вовсе — их дорисовывает клиент. Замер того же дня:
    `/api/favorites` отдаёт 200 нашей сессии и 401 анониму при одинаковой
    вёрстке. Значит вёрстка для этой площадки не свидетель."""
    class Page:
        def __init__(self, status):
            self.status = status

        def evaluate(self, script, *a):
            return self.status

        def content(self):
            # Ровно тот HTML, что сбивал проверку: «Войти» есть, признаков входа нет.
            return "<html>Войти</html>"

    st, why = auth._page_state(Page(200), "hirehi")
    eq(st, "logged_in", f"200 на приватной ручке — вход есть, что бы ни было в HTML ({why})")
    st, why = auth._page_state(Page(401), "hirehi")
    eq(st, "anonymous", "401 — площадка нас не узнаёт")
    # Неожиданный код — не приговор: по нему о входе судить нельзя, решает вёрстка.
    st, _ = auth._page_state(Page(503), "hirehi")
    eq(st, "anonymous", "503 на ручке — падаем на разметку, а не выдумываем вход")

    # Площадка без описанной ручки судится по вёрстке, как и раньше.
    ok(auth.api_state(Page(200), "shadowhint") is None,
       "у площадки без alive_api ручки нет — проба молчит")


def test_live_session_is_found_in_another_browser():
    """`auto` выбрал браузер с мёртвым токеном — ищем живой в остальных.

    Живой случай 07.08.2026: `auto` берёт браузер по покрытию доменов и свежести
    базы кук и про СРОК внутри куки не знает ничего. Он выбрал Яндекс с токеном
    wantapply, истёкшим 31.07, при живом до 08.08 в Chrome — то есть прямые
    ссылки в ATS терялись при работающем входе. Явно названный источник при этом
    подменять нельзя: `--cookies-from yandex` — вопрос про Яндекс."""
    from . import cookiesrc

    fresh = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp() * 1000)
    stale = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)

    def cookie(exp):
        return {"name": "auth-token-data",
                "value": urllib.parse.quote(json.dumps({"token": "T", "tokenExpires": exp}))}

    class Src:
        def __init__(self, exp):
            self.cookies = [cookie(exp)]

        def line(self):
            return "тест"

    by_name = {"yandex": Src(stale), "chrome": Src(fresh)}

    def fake_resolve(spec=None, domains=(), **kw):
        return by_name.get(spec) or by_name["yandex"]   # auto → Яндекс, как живьём

    with patched(cookiesrc, "resolve", fake_resolve), \
            patched(cookiesrc, "BROWSER_NAMES", ("yandex", "chrome")):
        token, why = auth.session_token("wantapply")
        eq(bool(token), True, f"живой токен найден в другом браузере ({why})")
        ok("chrome" in why, "источник назван — иначе непонятно, откуда взялась сессия")

        token, why = auth.session_token("wantapply", cookies_from="yandex")
    eq(token, None, "явно названный источник другим не подменяется")
    ok("истёк" in why, "и про него сказана правда")


def test_dead_critical_session_is_the_first_next_step():
    """Разлогин на площадке, без которой сбора нет, идёт ПЕРВЫМ шагом волны.

    Выше деградации выдачи: деградация — «нашлось меньше обычного», разлогин —
    «не нашлось ничего, и не найдётся, пока не починишь»."""
    from . import authrefresh, wave

    rows = [{"platform": "shadowhint", "state": "anonymous", "why": "нет куки",
             "loss": "сбор по площадке: всю выдачу", "critical": True,
             "renewable": False}]
    res = {"stages": {"collect": {"health": [{"label": "мало", "source": "hh",
                                             "found": 3}], "report": []}}}
    with patched(authrefresh, "preflight", lambda *a, **kw: rows):
        steps = wave.next_steps(res, {"rows": []})
    ok(steps and "ВОССТАНОВИ ВХОД" in steps[0],
       "вход чинится раньше, чем разбирается неполная выдача")
    ok("scout auth login shadowhint" in steps[0], "названа команда, а не намёк")


def main() -> int:
    for fn in (test_k_suffix_is_thousands, test_k_suffix_does_not_eat_words,
               test_zero_salary_is_not_a_salary,
               test_ignored_filter_is_a_failure_not_a_jackpot,
               test_tally_accounts_for_every_record,
               test_deliberate_cuts_are_not_losses,
               test_summary_carries_numbers_for_later,
               # ── формулировки: одна не покрывает выдачу ─────────────────
               test_cyrillic_query_becomes_latin_words,
               test_platform_query_set_extends_but_never_replaces,
               test_query_sets_are_measured_not_guessed,
               # ── hirehi: 403 был ложной стеной, выдача анонимна ─────────
               test_hirehi_walks_every_page, test_hirehi_dies_on_ignored_filter,
               test_hirehi_unknown_subcategory_is_caught_by_the_category_total,
               test_hirehi_looks_wider_than_go_and_backend,
               test_hirehi_applies_the_freshness_window_itself,
               test_hirehi_never_asks_in_cyrillic,
               test_hirehi_hybrid_is_not_a_yes_or_no, test_hirehi_url_needs_only_id,
               test_hirehi_salary_period_is_not_invented,
               test_hirehi_no_salary_means_no_period,
               test_ld_json_survives_nonce_attribute,
               # ── geekjob: маленький объём — свойство, а не поломка ──────
               test_geekjob_does_not_invent_the_year,
               test_geekjob_empty_salary_is_not_zero, test_geekjob_parses_k_salary,
               test_geekjob_merges_queries_by_id, test_geekjob_asks_more_than_one_wording,
               test_geekjob_dedupes_inside_one_answer,
               test_geekjob_empty_answer_is_a_failure,
               # ── wantapply: приманки и прямые ссылки в ATS ──────────────
               test_wantapply_decoys_never_become_a_contact,
               test_wantapply_html_description_becomes_text,
               test_wantapply_period_comes_from_its_own_field,
               test_wantapply_takes_the_union_of_wordings,
               test_wantapply_says_that_days_is_not_applied,
               test_wantapply_missing_session_does_not_lose_the_catalog,
               test_wantapply_apply_url_reads_the_real_link,
               # ── wantapply: живость — это status, а не наличие записи ───
               test_wantapply_dead_statuses_are_filtered_and_named,
               test_wantapply_check_reads_status_not_presence,
               test_wantapply_check_does_not_trust_an_ignored_filter,
               test_wantapply_check_rechecks_by_id_before_declaring_death,
               # ── shadowhint: без входа нет ничего ───────────────────────
               test_shadowhint_without_session_says_so,
               test_shadowhint_401_is_login_not_breakage,
               test_shadowhint_unknown_shape_fails_loudly,
               test_shadowhint_maps_flexible_field_names,
               test_shadowhint_nested_values_do_not_crash,
               test_shadowhint_link_points_at_the_telegram_post,
               test_shadowhint_walks_to_the_last_page,
               test_shadowhint_merges_wordings_by_id,
               test_shadowhint_stops_at_the_edge_of_the_window,
               test_a_seen_record_does_not_block_the_early_stop,
               test_shadowhint_junk_rows_are_not_a_loss,
               # ── ключ дубля: ложная склейка дороже лишнего раскола ──────
               test_hidden_employer_never_merges_strangers,
               test_aggregator_token_is_not_an_employer,
               test_one_employer_many_openings_do_not_merge,
               test_a_real_cross_platform_duplicate_still_merges,
               test_the_reason_for_not_merging_is_named,
               test_bare_key_call_stays_conservative,
               test_grade_is_not_noise,
               # ── сессии: что именно даёт вход ───────────────────────────
               test_expired_token_is_reported_with_a_date,
               test_plain_token_cookie_is_taken_as_is,
               test_geekjob_login_is_never_requested,
               test_anonymous_platforms_are_not_in_the_todo_list,
               test_hirehi_anonymous_view_is_not_advertised_as_broken,
               test_login_value_matches_the_registry,
               # ── настоящий браузер вместо подделанного headless ─────────
               test_mock_keychain_is_always_disabled,
               test_real_browser_never_spoofs_the_user_agent,
               test_unknown_browser_is_rejected_by_name,
               test_lock_holder_ignores_a_dead_pid, test_busy_profile_explains_itself,
               test_profile_keeps_only_platform_domains,
               test_top_up_never_overwrites_a_rotated_cookie,
               test_rotating_hosts_are_known, test_registry_is_complete,
               # ── продление сессий: точность предупреждения ───────────────
               test_hirehi_session_is_read_only_from_our_own_file,
               test_unreadable_cookie_db_is_not_a_logout,
               test_unreadable_browser_does_not_hide_a_live_session_in_another,
               test_preflight_reports_only_what_it_knows,
               test_preflight_is_silent_about_platforms_that_lose_nothing,
               test_hirehi_is_not_promised_to_renew_itself_without_a_session,
               test_localstorage_token_is_merged_not_overwritten,
               test_hirehi_saves_rotation_even_when_the_page_dies,
               test_hirehi_renewal_confirms_by_the_page_not_by_the_absence_of_errors,
               test_careered_snapshot_comes_from_the_persistent_profile,
               test_snapshot_is_taken_even_when_already_logged_in,
               test_private_endpoint_outranks_the_markup,
               test_live_session_is_found_in_another_browser,
               test_dead_critical_session_is_the_first_next_step):
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
