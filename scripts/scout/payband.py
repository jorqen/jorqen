"""payband — что называть рекрутёру, когда работодатель вилку не назвал.

ЗАЧЕМ. Про ожидания спрашивают почти всегда, и промах мимо коридора рекрутёра
стоит переписки целиком: после названной цифры просто перестают отвечать.
Когда вилка в вакансии есть — вопрос закрыт ею, и здесь считать нечего. Когда
её нет, цифру всё равно придётся назвать вслух, и подпирать её догадкой нельзя:
за суммой, которую произносят, должно стоять основание, которое не стыдно
повторить рекрутёру.

ЧЕМ ПОДПИРАЕМ — тремя РАЗНЫМИ вещами, и они не складываются в одну:

1. **Наша база** (`vacancy`) — вакансии с УКАЗАННОЙ вилкой, похожие по роли,
   грейду и региону. Для российского рынка это самое точное, что у нас есть:
   это не опрос и не справочник, а живые открытые позиции этого месяца.
2. **levels.fyi** (`sources_web.levels_benchmark`) — медиана полной компенсации
   за год по западному рынку. Другая валюта, другой рынок и другое понятие
   («total comp» с акциями и бонусом), поэтому идёт ТОЛЬКО на сверку и в цифру
   для российской вакансии не подмешивается.
3. **Справочник dreamoffer** — рублёвые медианы по профессиям за 30 дней.
   Период справочник не называет, а хвост выборки загрязнён годовыми суммами,
   поэтому он тоже только сверка и в наценку не берётся.

ПРАВИЛА ЧЕСТНОСТИ, которые здесь важнее самой функции:

* Оценка НИКОГДА не выдаётся за вилку работодателя — разные вещи, разные
  подписи, и в блоке про это сказано прямым текстом.
* У каждой цифры названо основание: источник, размер выборки, период, валюта,
  дата. Цифра без основания — выдумка, и называть её рекрутёру нельзя.
* Мало данных — так и печатаем. Медиана по двум строкам это не рынок,
  а совпадение, и подгонять её до «похоже на правду» мы не будем.
* Наценка на переговоры (`MARKUP`) видна в тексте вместе с базой: читатель
  обязан видеть и «медиана X», и «X+15% = Y», иначе он не сможет проверить.
* Валюты НЕ пересчитываются вовсе. Курс — это ещё одна цифра с датой, которая
  устаревает молча; сложить рублёвую вакансию с долларовой медианой значит
  получить сумму, которую нельзя назвать ни одному рекрутёру.

ПОЧЕМУ ПЕРИОД ОБЯЗАТЕЛЕН. В пул сравнения попадают только вакансии, где
площадка НАЗВАЛА период (`salary_period`). Разница между почасовой, месячной и
годовой вилкой — от 12 до 2000 раз (см. `model.Vacancy.salary_period`), и одна
подставленная по умолчанию «месячная» строка сдвигает медиану так, что ошибку
уже не видно. Отброшенные строки не прячем — их количество печатается.
"""

from __future__ import annotations

import collections
import json
import re
import statistics
from datetime import datetime, timedelta, timezone

from . import store
from .model import PERIOD_SUFFIX, norm_currency, norm_period
from .shortlist import _has, grade_of

# Наценка на переговоры. Ровно то, что просил владелец: называть на 10–20%
# выше рынка. Держим ДИАПАЗОНОМ, а не одним числом: рекрутёру называют вилку,
# и «от и до» — это и есть нормальный ответ на вопрос об ожиданиях.
MARKUP = (0.10, 0.20)

# Сколько похожих вакансий должно набраться, чтобы медиана вообще что-то значила.
# Пять — не круглое число из головы: столько же требует от своей выборки
# справочник dreamoffer (`min_required_samples`), и спорить с ним причин нет.
MIN_PEERS = 5

# Ниже этого выборку называем маленькой прямо в тексте. Медиана по шести
# строкам считается, но верить ей как двадцати нельзя, и читатель должен это
# видеть, а не выяснять из размера выборки самостоятельно.
THIN_PEERS = 10

# Окно по `first_seen`. Прошлогодняя вилка — это прошлогодний рынок; полгода —
# компромисс между свежестью и тем, что база собрана недавно и глубже не видит.
WINDOW_DAYS = 180

# Ставка НДФЛ. Нужна ровно для одной фразы: пул смешивает «на руки» и «до
# вычета», и читатель должен знать цену этой смеси, а не догадываться о ней.
NDFL = 0.13

# ── Роль ─────────────────────────────────────────────────────────────────────
#
# Порядок ВАЖЕН: проверка идёт сверху вниз и останавливается на первом
# совпадении. Специальные семейства стоят выше backend, иначе «QA-инженер
# backend (Python)» уедет в бэкенд и потянет за собой чужую вилку.
FAMILY: dict[str, tuple[str, ...]] = {
    "qa": ("qa", "тестировщик", "test engineer", "автотест", "sdet"),
    "sre": ("sre", "site reliability"),
    "devops": ("devops", "девопс"),
    "mobile": ("android", "ios", "мобильн", "mobile", "flutter"),
    "frontend": ("frontend", "фронтенд", "front-end", "фронтэнд"),
    "fullstack": ("fullstack", "full-stack", "full stack", "фулстек", "фуллстек"),
    "data": ("data engineer", "дата-инженер", "инженер данных"),
    "ml": ("machine learning", "mlops", "ml", "мл-инженер"),
    "analyst": ("аналитик", "analyst"),
    "manager": ("product manager", "project manager", "продакт", "проджект"),
    "backend": ("backend", "бэкенд", "бекенд", "back-end", "серверн", "server-side"),
}

# Языки. Слово ищется по границам (`shortlist._has`), а не подстрокой: «go»
# внутри «Django» уже один раз выдало Scala-вакансии за Go-вакансии.
LANGS = ("go", "golang", "python", "java", "php", "kotlin", "scala", "rust",
         "node", "typescript", "javascript", "ruby", "c#", "c++", ".net",
         "elixir", "erlang", "swift")

# Слова, по которым заголовок без семейства всё-таки читается как разработка.
# Нужны для запасного пути: «Golang-разработчик» семейства в себе не несёт.
_DEV_WORDS = ("разработчик", "developer", "программист", "engineer", "инженер",
              "software")

_CYR = re.compile(r"[а-яё]", re.I)


def family_of(title: str | None) -> str | None:
    """Семейство роли из заголовка. None — заголовок роль не назвал."""
    t = (title or "").lower()
    for fam, words in FAMILY.items():
        if any(_has(t, w) for w in words):
            return fam
    # Запасной путь: язык + слово про разработку. «Golang-разработчик» — это
    # бэкенд, и терять такие строки жалко: на hh их больше, чем «backend».
    # Мобильные и фронтовые заголовки сюда не доходят — они отсеяны выше.
    if any(_has(t, lang) for lang in ("go", "golang", "java", "python", "php",
                                      "scala", "kotlin", "rust", "elixir")) \
            and any(_has(t, w) for w in _DEV_WORDS):
        return "backend"
    return None


def langs_of(title: str | None) -> set[str]:
    """Языки из заголовка. golang и go — один язык, названный двумя способами."""
    t = (title or "").lower()
    out = {lang for lang in LANGS if _has(t, lang)}
    if "golang" in out:
        out.discard("golang")
        out.add("go")
    return out


def country_of(row: dict) -> str | None:
    """Страна вакансии — ТОЛЬКО из поля локации.

    По заголовку страну не ищем намеренно: «Backend Java Engineer —
    Kazakhstan/Russian speaker» это язык, а не место работы, и на этом уже
    один раз обожглись (см. `atsapi.country_matcher`).
    """
    loc = (row.get("location") or "").strip()
    if not loc or loc.lower() in ("n/a", "remote", "удалённо", "удаленно", "-"):
        return None
    from .atsapi import COUNTRY_ALIASES, country_matcher  # noqa: PLC0415

    for cc in COUNTRY_ALIASES:
        if country_matcher(cc).search(loc):
            return cc
    return None


def profile(row: dict) -> dict:
    """Что мы знаем о роли вакансии: семейство, языки, грейд, страна."""
    title = row.get("title") or ""
    return {
        "family": family_of(title),
        "langs": langs_of(title),
        "grade": {g for g in grade_of(title).split(",") if g},
        "country": country_of(row),
        # Русскоязычный заголовок — сигнал рынка не хуже локации: у телеграм-
        # вакансий локации нет вовсе, а рынок у них рублёвый.
        "ru_leaning": bool(_CYR.search(title)) or country_of(row) == "RU",
    }


# ── Пул похожих вакансий ─────────────────────────────────────────────────────

def midpoint(row: dict) -> int | None:
    """Одно число из вилки: середина, а при односторонней вилке — её граница.

    «от 300 000» кладём как 300 000, хотя реальная зарплата выше: занизить
    ориентир безопаснее, чем завысить. Завышенный ориентир превращается
    в названную рекрутёру цифру, после которой не отвечают.
    """
    lo, hi = row.get("salary_from"), row.get("salary_to")
    if lo and hi:
        return (int(lo) + int(hi)) // 2
    one = lo or hi
    return int(one) if one else None


def _pct(values: list[int], q: float) -> int:
    """Перцентиль по ближайшему рангу.

    Не `statistics.quantiles`: тот требует минимум двух точек и интерполирует,
    а мы работаем на выборках в 5–20 строк, где интерполяция рисует суммы,
    которых в базе нет. Ближайший ранг всегда возвращает НАСТОЯЩУЮ вилку
    из базы — её можно пойти и посмотреть глазами.
    """
    if not values:
        return 0
    k = max(0, min(len(values) - 1, round(q * (len(values) - 1))))
    return values[k]


# Лестница сужений: от самого точного отбора к самому широкому. Берётся первый
# уровень, на котором набралось `min_peers`. Уровень печатается в карточке —
# «медиана по 13 senior-Go в России» и «медиана по 46 любым бэкендам» это
# разные утверждения, и читатель обязан видеть, какое из них перед ним.
LADDER = (
    ("роль + язык + грейд + регион", ("family", "lang", "grade", "country")),
    ("роль + грейд + регион", ("family", "grade", "country")),
    ("роль + язык + регион", ("family", "lang", "country")),
    ("роль + регион", ("family", "country")),
    ("роль + язык, любой регион", ("family", "lang")),
    ("роль, любой регион", ("family",)),
)


def _matches(prof: dict, other: dict, keys: tuple[str, ...]) -> bool:
    if "family" in keys and (not prof["family"] or other["family"] != prof["family"]):
        return False
    if "lang" in keys and not (prof["langs"] & other["langs"]):
        return False
    if "grade" in keys:
        # Пустой грейд с обеих сторон — это совпадение: «Backend Developer»
        # похож на «Backend Developer», а не на «Senior Backend Developer».
        if prof["grade"] != other["grade"] and not (prof["grade"] & other["grade"]):
            return False
    if "country" in keys and (not prof["country"] or other["country"] != prof["country"]):
        return False
    return True


def peers(conn, row: dict, *, min_peers: int = MIN_PEERS,
          window_days: int = WINDOW_DAYS) -> dict:
    """Похожие вакансии С ВИЛКОЙ из нашей базы. Сеть не трогается.

    Возвращает разбор, а не одно число: уровень отбора, валюту, период, размер
    выборки, окно дат, источники и состав по gross/net. Всё это уезжает в текст
    карточки — цифра без этого хвоста непроверяема.
    """
    prof = profile(row)
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    rows = [dict(x) for x in conn.execute(
        "SELECT source, external_id, title, company, location, currency, "
        "salary_from, salary_to, salary_period, salary_gross, first_seen, dup_key "
        "FROM vacancy WHERE (salary_from IS NOT NULL OR salary_to IS NOT NULL) "
        "AND first_seen >= ?", (since,)).fetchall()]

    # Себя и свои же дубли с других площадок из пула вон: вилка того же самого
    # объявления — это не «рынок», это та же вакансия, и она разбирается
    # отдельной строкой в карточке (см. `sibling_band`).
    mine = (row.get("source"), row.get("external_id"))
    dup = row.get("dup_key")
    rows = [x for x in rows
            if (x["source"], x["external_id"]) != mine
            and not (dup and x.get("dup_key") == dup)]

    ready, no_period = [], 0
    for x in rows:
        period = norm_period(x["salary_period"])
        cur = norm_currency(x["currency"])
        value = midpoint(x)
        if value is None or not cur:
            continue
        if not period:
            # Вилка есть, а периода нет. Такую строку в медиану класть нельзя
            # ни при каких условиях, но и молчать о ней нельзя — считаем.
            no_period += 1
            continue
        x["_v"], x["_cur"], x["_per"] = value, cur, period
        x["_prof"] = profile(x)
        ready.append(x)

    out = {"level": None, "n": 0, "dropped_no_period": no_period,
           "profile": prof, "pool": len(ready), "buckets": [], "rows": []}
    if not prof["family"]:
        out["why"] = ("роль из заголовка не разобралась — сравнивать не с чем; "
                      "по любым вакансиям подряд медиана считается, но смысла "
                      "в ней нет")
        return out

    for label, keys in LADDER:
        sel = [x for x in ready if _matches(prof, x["_prof"], keys)]
        if not sel:
            continue
        # Внутри уровня валюта и период НЕ смешиваются: берём самую большую
        # однородную корзину, остальные показываем счётчиком, чтобы не выглядело,
        # будто их не было.
        by = collections.Counter((x["_cur"], x["_per"]) for x in sel)
        (cur, per), n = by.most_common(1)[0]
        if n < min_peers:
            continue
        top = sorted((x for x in sel if (x["_cur"], x["_per"]) == (cur, per)),
                     key=lambda x: x["_v"])
        vals = [x["_v"] for x in top]
        dates = sorted(x["first_seen"][:10] for x in top if x["first_seen"])
        out.update({
            "level": label, "n": n, "currency": cur, "period": per,
            "median": int(statistics.median(vals)),
            "p25": _pct(vals, 0.25), "p75": _pct(vals, 0.75),
            "low": vals[0], "high": vals[-1],
            "window": (dates[0], dates[-1]) if dates else None,
            "sources": collections.Counter(x["source"] for x in top),
            "gross": collections.Counter(x["salary_gross"] for x in top),
            "buckets": [(f"{c} {PERIOD_SUFFIX.get(p, p)}".strip(), k)
                        for (c, p), k in by.most_common() if (c, p) != (cur, per)],
            "rows": top,
        })
        return out

    # Ни один уровень не набрал минимума — скажем, сколько всё-таки нашлось
    # на самом широком, иначе «мало данных» не отличить от «не искали».
    widest = [x for x in ready if _matches(prof, x["_prof"], ("family",))]
    out["n"] = len(widest)
    out["why"] = (f"похожих вакансий с названной вилкой И названным периодом — "
                  f"{len(widest)}, нужно хотя бы {min_peers}")
    return out


def sibling_band(conn, row: dict) -> dict | None:
    """Вилка у ТОЙ ЖЕ вакансии на другой площадке.

    Самый частый случай «вилки нет»: hh её скрыл, а getmatch или телеграм-канал
    ту же позицию опубликовали с деньгами. Это вилка РАБОТОДАТЕЛЯ, а не оценка,
    и подменять её медианой рынка было бы прямой потерей факта.
    """
    dup = row.get("dup_key")
    if not dup:
        return None
    r = conn.execute(
        "SELECT url, source, salary_from, salary_to, currency, salary_gross, "
        "salary_period FROM vacancy WHERE dup_key=? AND (source<>? OR external_id<>?) "
        "AND (salary_from IS NOT NULL OR salary_to IS NOT NULL) LIMIT 1",
        (dup, row.get("source"), row.get("external_id"))).fetchone()
    return dict(r) if r else None


# ── levels.fyi ───────────────────────────────────────────────────────────────

LEVELS_SOURCE = "levels-benchmark"


def _levels_view(raw: dict, *, as_of: str, origin: str) -> dict:
    """Приведение выдачи levels.fyi к одному виду.

    Разборов у площадки было два: старый (`__NEXT_DATA__`, с перцентилями
    и размером выборки) и нынешний (`.md`, без выборки и без p10/p99). В базе
    лежат записи ОБОИХ, и читать их надо одинаково — иначе цифра с прошлого
    прогона просто пропадёт из карточки без единого слова.
    """
    tc = raw.get("total_compensation") or {}
    median = raw.get("median_total") or tc.get("p50")
    return {
        "median": median,
        "p25": raw.get("p25") if raw.get("p25") is not None else tc.get("p25"),
        "p75": raw.get("p75") if raw.get("p75") is not None else tc.get("p75"),
        "p90": raw.get("p90") if raw.get("p90") is not None else tc.get("p90"),
        "currency": raw.get("currency") or "USD",
        "period": raw.get("period") or "year",
        "job_title": raw.get("job_title") or raw.get("role"),
        "sample_size": raw.get("sample_size"),
        "updated": raw.get("updated"),
        "as_of": as_of,
        "origin": origin,
        "attribution": raw.get("attribution")
        or "Data source: Levels.fyi (https://www.levels.fyi)",
    }


def levels(conn, role: str = "backend", *, fetch: bool = False) -> dict | None:
    """Бенчмарк levels.fyi: сначала из базы, в сеть — только по прямой просьбе.

    Карточки собираются пачкой, и ходить в сеть на каждую — это и минуты, и
    свежая антибот-стена на ровном месте. Поэтому по умолчанию берём то, что
    уже лежит в базе (кэш за сутки или сводка с прошлого прогона), и честно
    печатаем ДАТУ этой цифры. Цифра позавчерашнего рынка — нормальная опора;
    цифра без даты — нет.
    """
    day = datetime.now(timezone.utc).date().isoformat()
    key = f"levels:{role}"
    try:
        body = store.raw_cache_get(conn, LEVELS_SOURCE, key)
    except Exception:  # noqa: BLE001 — кэш не имеет права ронять карточку
        body = None
    if body:
        try:
            return _levels_view(json.loads(body), as_of=day, origin="кэш за сегодня")
        except (TypeError, ValueError):
            pass

    if fetch:
        try:
            from .sources_web import levels_benchmark  # noqa: PLC0415 — ради сети

            raw = levels_benchmark(role)
            try:
                store.raw_cache_put(conn, LEVELS_SOURCE, key,
                                    json.dumps(raw, ensure_ascii=False))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
            return _levels_view(raw, as_of=day, origin="запрошено сейчас")
        except Exception as e:  # noqa: BLE001 — источник упал, карточка живёт
            return {"error": f"{type(e).__name__}: {e}"}

    # Сводка с прошлого прогона. `src_levels` кладёт её строкой в `vacancy`
    # с пустым url — в выдачу она не попадает, но данные в ней настоящие.
    r = conn.execute(
        "SELECT raw, last_seen FROM vacancy WHERE source='levels' AND url='' "
        "ORDER BY last_seen DESC LIMIT 1").fetchone()
    if not r or not r["raw"]:
        return None
    try:
        raw = json.loads(r["raw"])
    except (TypeError, ValueError):
        return None
    return _levels_view(raw, as_of=(r["last_seen"] or "")[:10],
                        origin="сводка с прошлого прогона")


# ── Справочник dreamoffer ────────────────────────────────────────────────────

REFBOOK_URL = "https://api.dreamoffer.app/db/pg/salary_dataset"
REFBOOK_SOURCE = "dreamoffer-salary"

# Наши семейства → профессии справочника. Сопоставлено только то, что совпадает
# без натяжки. «fullstack» намеренно не сопоставлен: ближайшее в справочнике —
# «General developer», а это другая профессия, и подставлять её значило бы
# выдать чужую медиану за медиану по роли.
REFBOOK_PROFESSION = {
    "backend": "Backend developer",
    "frontend": "Frontend developer",
    "devops": "DevOps",
    "mobile": "Mobile developer",
    "qa": "QA engineer",
    "data": "DataEngineer",
    "ml": "MLandAI",
    "manager": "Project manager",
}


def _distill(payload: dict) -> dict:
    """Из 1,7 МБ выдачи оставить сотню строк, которые реально нужны.

    В кэш кладём именно выжимку, а не исходный ответ: складывать полтора
    мегабайта в базу каждый день ради двух медиан — это плата без покупки.
    Ветку `intl` не берём вовсе: это те же самые вакансии, пересчитанные
    площадкой в евро (одинаковые `count`), то есть не второй рынок, а вторая
    валюта — а валюты мы не смешиваем.
    """
    ru = (payload.get("branches") or {}).get("ru") or {}
    profs = {}
    for name, p in (ru.get("professions") or {}).items():
        st = p.get("stats") or {}
        profs[name] = {"count": p.get("count"), "median": st.get("median"),
                       "p25": st.get("p25"), "p75": st.get("p75"),
                       "max": st.get("max"), "min": st.get("min")}
    return {"currency": ru.get("currency"), "professions": profs,
            "period_start": payload.get("period_start"),
            "period_end": payload.get("period_end"),
            "period_days": payload.get("period_days"),
            "min_required_samples": payload.get("min_required_samples"),
            "rate_source": payload.get("rate_source"),
            "rate_date": payload.get("rate_date")}


def refbook(conn, *, fetch: bool = False) -> dict | None:
    """Рублёвый справочник медиан. Как и levels — из кэша, в сеть по просьбе."""
    try:
        body = store.raw_cache_get(conn, REFBOOK_SOURCE, REFBOOK_URL)
    except Exception:  # noqa: BLE001
        body = None
    if body:
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            pass
    if not fetch:
        return None
    try:
        from .net import fetch_json  # noqa: PLC0415 — ради сети

        # Ответ большой (~1,7 МБ) и небыстрый (~20 с), поэтому таймаут щедрее
        # обычного: обрубить его на 40-й секунде значит потерять справочник
        # целиком и не узнать, почему.
        data = _distill(fetch_json(REFBOOK_URL, timeout=90, retries=1))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    try:
        store.raw_cache_put(conn, REFBOOK_SOURCE, REFBOOK_URL,
                            json.dumps(data, ensure_ascii=False))
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return data


# ── Сборка оценки ────────────────────────────────────────────────────────────

def has_own_band(row: dict) -> bool:
    """Есть ли у вакансии СВОЯ вилка. Единственное условие включения блока."""
    return bool(row.get("salary_from") or row.get("salary_to"))


def ask_from(base: int, *, markup: tuple[float, float] = MARKUP) -> dict:
    """Наценка на переговоры от НАЗВАННОЙ базы.

    Ничего не округляем намеренно: «медиана 350 000 +15% = 402 500» читатель
    проверяет в уме за секунду, а округлённые 400 000 уже не сходятся с базой,
    и проверить их нельзя — придётся верить на слово.

    Отсюда же `round`, а не `int`: (0.10+0.20)/2 в двоичной дроби даёт
    0.15000000000000002, и обрезание вниз печатало 402 499 — цифру, которая в
    уме как раз НЕ сходится. Расходилась 21 круглая база из 51 в диапазоне
    100–600 тысяч.
    """
    lo, hi = markup
    return {"base": base, "low": round(base * (1 + lo)), "high": round(base * (1 + hi)),
            "mid": round(base * (1 + (lo + hi) / 2)), "markup": markup}


# У levels.fyi свой бенчмарк есть не у каждого семейства ролей. Где его нет,
# берём общий «software engineer»: выдавать бэкендовую медиану за ориентир для
# QA, фронтенда или аналитика нельзя — цифра подаётся как «назвать рекрутёру»,
# и несовпадение видно только строкой ниже. Семейство не опознано — остаётся
# бэкенд: это профиль владельца, и для его собственных вакансий догадка верна.
#
# `manager` из FAMILY — это ПРОДАКТ и ПРОДЖЕКТ, а не инженерный менеджер, и
# маршрут engineering-manager ему не подходит; такие роли уходят в общий срез.
_LEVELS_ROLE = {"backend": "backend", "sre": "sre", "devops": "devops"}


def levels_role(family: str | None) -> str:
    """Роль для бенчмарка levels.fyi по семейству вакансии."""
    return "backend" if family is None else _LEVELS_ROLE.get(family, "software-engineer")


def estimate(conn, row: dict, *, markup: tuple[float, float] = MARKUP,
             min_peers: int = MIN_PEERS, window_days: int = WINDOW_DAYS,
             fetch: bool = False) -> dict:
    """Полный разбор «сколько просить» для вакансии без вилки."""
    prof = profile(row)
    est = {
        "own": has_own_band(row),
        "profile": prof,
        "sibling": sibling_band(conn, row),
        "peers": peers(conn, row, min_peers=min_peers, window_days=window_days),
        "levels": levels(conn, levels_role(prof["family"]), fetch=fetch),
        "refbook": refbook(conn, fetch=fetch),
        "markup": markup,
        "ask": None,
        "base_label": None,
    }
    p = est["peers"]
    if p.get("level"):
        est["ask"] = ask_from(p["median"], markup=markup)
        est["base_label"] = (f"медиана похожих вакансий из нашей базы "
                             f"({p['n']} шт., {p['currency']}"
                             f"{PERIOD_SUFFIX.get(p['period'], '')})")
        est["ask_currency"] = p["currency"]
        est["ask_period"] = p["period"]
        return est

    # Своей базы не хватило. levels.fyi годится как опора ТОЛЬКО для западной
    # вакансии: назвать российскому рекрутёру долларовый total comp — это не
    # завышенная цифра, это ответ не на тот вопрос.
    lv = est["levels"] or {}
    if lv.get("median") and not est["profile"]["ru_leaning"]:
        est["ask"] = ask_from(int(lv["median"]), markup=markup)
        est["base_label"] = (f"медиана полной компенсации levels.fyi "
                             f"({lv['currency']}/год, снято {lv.get('as_of') or '?'})")
        est["ask_currency"] = lv.get("currency") or "USD"
        est["ask_period"] = "year"
    return est


# ── Текст для карточки ───────────────────────────────────────────────────────

def _money(n) -> str:
    return f"{int(n):,}".replace(",", " ") if isinstance(n, (int, float)) else "?"


def _unit(cur: str | None, period: str | None) -> str:
    return f"{cur or '?'}{PERIOD_SUFFIX.get(period or '', '')}"


def _gross_line(gross: collections.Counter) -> str | None:
    """Состав выборки по «на руки / до вычета».

    Пул смешанный почти всегда, и это не мелочь: 350 000 gross — это 304 500
    на руки, то есть половина разницы, из-за которой переписка и обрывается.
    """
    net_n, gross_n, unknown = gross.get(0, 0), gross.get(1, 0), gross.get(None, 0)
    if not (net_n or gross_n):
        return None
    parts = []
    if net_n:
        parts.append(f"{net_n} «на руки»")
    if gross_n:
        parts.append(f"{gross_n} «до вычета»")
    if unknown:
        parts.append(f"{unknown} без пометки")
    tail = (f" — в одной медиане смешаны обе базы; «до вычета» выше «на руки» "
            f"примерно на {int(NDFL * 100)}% НДФЛ"
            if net_n and gross_n else "")
    return "**Состав выборки:** " + ", ".join(parts) + tail


def _levels_line(lv: dict | None) -> str:
    if not lv:
        return ("**Мировой рынок (levels.fyi):** в базе цифры нет и в сеть мы за ней "
                "не ходили — `scout card --fetch-market <url>` сходит и запишет")
    if lv.get("error"):
        return f"**Мировой рынок (levels.fyi):** спросить не вышло — {lv['error']}"
    sample = (f"выборка {_money(lv['sample_size'])}" if lv.get("sample_size")
              else "размер выборки площадка больше не отдаёт")
    q = " · ".join(f"{k} {_money(v)}" for k, v in
                   (("p25", lv.get("p25")), ("p75", lv.get("p75")),
                    ("p90", lv.get("p90"))) if v)
    return (f"**Мировой рынок (levels.fyi, для сверки):** медиана полной компенсации "
            f"{_money(lv['median'])} {_unit(lv.get('currency'), lv.get('period'))}"
            + (f" · {q}" if q else "")
            + f" · {sample} · роль {lv.get('job_title') or '?'} · "
            f"{lv.get('origin')}, дата {lv.get('as_of') or '?'}"
            + (f" · обновлено площадкой {lv['updated']}" if lv.get("updated") else "")
            + f". {lv.get('attribution')}. Другой рынок и другая валюта — "
            f"в рубли не пересчитываем и в цифру выше не подмешиваем")


def _refbook_line(rb: dict | None, family: str | None) -> str | None:
    if not rb:
        return None
    if rb.get("error"):
        return f"**Справочник dreamoffer:** спросить не вышло — {rb['error']}"
    name = REFBOOK_PROFESSION.get(family or "")
    if not name:
        return (f"**Справочник dreamoffer:** профессии под роль «{family or 'не разобрана'}» "
                f"в справочнике нет — сверять не с чем")
    p = (rb.get("professions") or {}).get(name)
    if not p or not p.get("median"):
        return f"**Справочник dreamoffer:** по профессии {name} медианы нет"
    cur = rb.get("currency") or "RUB"
    # Загрязнение хвоста считаем, а не утверждаем: если верх выборки на порядок
    # выше медианы — в неё попали годовые суммы, и верить можно только медиане.
    dirty = ""
    if p.get("max") and p["median"] and p["max"] > 10 * p["median"]:
        dirty = (f"; хвост выборки загрязнён — верх {_money(p['max'])} при медиане "
                 f"{_money(p['median'])}, это годовые суммы среди месячных, "
                 f"поэтому берём только медиану")
    return (f"**Справочник dreamoffer (для сверки):** медиана {_money(p['median'])} {cur} · "
            f"p25 {_money(p.get('p25'))} · p75 {_money(p.get('p75'))} · "
            f"{p.get('count')} вакансий · профессия {name} · "
            f"{rb.get('period_start')}…{rb.get('period_end')} "
            f"({rb.get('period_days')} дней) · валюты приведены к рублю курсом "
            f"{rb.get('rate_source')} на {(rb.get('rate_date') or '?')[:10]}. "
            f"Период справочник НЕ называет{dirty} — цифра идёт на сверку "
            f"и в наценку не берётся")


def lines(est: dict) -> list[str]:
    """Блок карточки. Пустой список — блок не нужен (у вакансии своя вилка)."""
    if est.get("own"):
        return []

    out = ["", "### Сколько просить — работодатель вилку не назвал"]

    sib = est.get("sibling")
    if sib:
        from .model import salary_str  # noqa: PLC0415 — один формат вилки на проект

        band = salary_str(sib["salary_from"], sib["salary_to"], sib["currency"],
                          sib["salary_gross"], sib["salary_period"])
        out.append(f"- **Вилка есть у той же вакансии на другой площадке:** {band} "
                   f"({sib['source']}, {sib['url']}) — это вилка РАБОТОДАТЕЛЯ, "
                   f"а не оценка; отталкивайся от неё, а не от медиан ниже. "
                   f"Убедись, что это действительно та же позиция")

    p = est.get("peers") or {}
    ask = est.get("ask")
    if p.get("level"):
        win = f"{p['window'][0]}…{p['window'][1]}" if p.get("window") else "окно не собралось"
        src = ", ".join(f"{k} {v}" for k, v in p["sources"].most_common(5))
        prof = est["profile"]
        who = " · ".join(x for x in (
            prof["family"], "/".join(sorted(prof["langs"])) or None,
            "/".join(sorted(prof["grade"])) or "грейд не назван",
            prof["country"] or "регион не назван") if x)
        out.append(f"- **Ориентир (похожие вакансии в нашей базе):** медиана "
                   f"**{_money(p['median'])} {_unit(p['currency'], p['period'])}** · "
                   f"p25 {_money(p['p25'])} · p75 {_money(p['p75'])} · "
                   f"весь разброс {_money(p['low'])}–{_money(p['high'])} · "
                   f"{p['n']} вакансий · отбор «{p['level']}» ({who}) · "
                   f"окно {win} · источники: {src}")
        gl = _gross_line(p.get("gross") or collections.Counter())
        if gl:
            out.append(f"- {gl}")
        if p["n"] < THIN_PEERS:
            out.append(f"- ⚠️ **Выборка маленькая** ({p['n']} вакансий): цифра "
                       f"ориентировочная, разброс p25–p75 честнее медианы")
        if not est["profile"]["country"]:
            out.append("- ⚠️ **Регион вакансии не назван** — отбор шёл без него; "
                       "сверь, что валюта выборки совпадает с рынком вакансии")
        if p.get("buckets"):
            other = ", ".join(f"{u}: {k}" for u, k in p["buckets"][:4])
            out.append(f"- **Не вошли в медиану (другая валюта или период):** {other} "
                       f"— валюты и периоды не смешиваем")
        if p.get("dropped_no_period"):
            out.append(f"- **Отброшено без периода:** {p['dropped_no_period']} вакансий "
                       f"с вилкой, но без указания «в месяц или в год» — разница "
                       f"между ними до 12 раз, домысливать её нельзя")
    else:
        why = p.get("why") or "похожих вакансий в базе не нашлось"
        out.append(f"- **Мало данных, цифру по нашей базе не называем.** {why}. "
                   f"Медиана по паре строк — это совпадение, а не рынок")
        if p.get("dropped_no_period"):
            out.append(f"- **Из них отброшено:** {p['dropped_no_period']} вакансий "
                       f"с вилкой, но без названного периода")

    if ask:
        lo, hi = est["markup"]
        unit = _unit(est.get("ask_currency"), est.get("ask_period"))
        out.append(f"- **Назвать рекрутёру:** **{_money(ask['low'])} – "
                   f"{_money(ask['high'])} {unit}**. Это {est['base_label']} "
                   f"= {_money(ask['base'])}, плюс наценка на переговоры "
                   f"{int(lo * 100)}–{int(hi * 100)}% (`payband.MARKUP`). "
                   f"Одна цифра, если просят одну: **{_money(ask['mid'])}** "
                   f"(+{int((lo + hi) / 2 * 100)}%)")
    else:
        out.append("- **Что называть — не считаем.** Основания под цифру нет, "
                   "а придуманная цифра хуже честного «назовите ваш коридор». "
                   "Спроси вилку первым: «подскажите коридор по позиции — "
                   "чтобы не тратить время друг друга»")

    out.append(f"- {_levels_line(est.get('levels'))}")
    rb = _refbook_line(est.get("refbook"), est["profile"]["family"])
    if rb:
        out.append(f"- {rb}")
    out.append("- ⚠️ **Это ОЦЕНКА, а не вилка работодателя.** Ни одной из этих сумм "
               "работодатель не называл. В письме и в разговоре так и подавай: "
               "«похожие вакансии на рынке идут около …, рассчитываю на …»")
    return out


def block(conn, row: dict, *, fetch: bool = False, **kw) -> list[str]:
    """Готовые строки блока для карточки. Пусто — если вилка у вакансии есть."""
    if has_own_band(row):
        return []
    return lines(estimate(conn, row, fetch=fetch, **kw))
