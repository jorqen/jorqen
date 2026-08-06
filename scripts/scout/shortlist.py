"""shortlist — дельта, свёрнутая до списка, по которому модель сразу пишет карточки.

Зачем команда существует. Раньше конвейер выглядел так: `scan` писал отчёт
markdown на 3 МБ, а модель раскладывала подагентов ВЫЧИТЫВАТЬ этот markdown
обратно в структуру. Живой прогон 04.08.2026: 14 агентов и ~2,6 млн токенов
ушло на то, чтобы получить 711 строк, которые всё это время лежали в SQLite
в готовом виде. Это чистая потеря: разбор собственного вывода.

`shortlist` делает ту же работу детерминированно и бесплатно:

1. **дельта из базы** — те же фильтры, что у `new`;
2. **схлопывание дублей** — одна вакансия с пяти площадок становится ОДНОЙ
   строкой со списком источников (ключ: нормализованная пара компания+роль,
   плюс `dup_key` площадок);
3. **отсев отработанного** — по `decision` и по `negotiation` (отклик, отказ,
   приглашение): то, что модель раньше сверяла глазами по блоку «уже отработано»;
4. **требуемый стаж** вытаскивается регуляркой из выжимки `detail` — главный
   критерий отбора, который модель иначе выясняет чтением каждой вакансии;
5. **контакт работодателя** подставляется из кэша `employer_channel`, если он
   был найден в прошлые прогоны.

Что команда НЕ делает: не оценивает фит, не решает, кому писать, не сочиняет
карточки. Отбор — работа модели; здесь только механика.
"""

from __future__ import annotations

import json
import re
import sys

from . import store
from .sources import ATS_ROLE_RE

# ── Требуемый стаж ────────────────────────────────────────────────────────────
# Все формы, встреченные живьём на 22 площадках. Порядок важен: сначала более
# специфичные (с указанием языка), потом общие.
_YEARS_RE = (
    re.compile(r"опыт[^.]{0,70}?(?:от|более)\s+(\d+)[\s-]*(?:х|ти)?\s*лет", re.I),
    re.compile(r"(?:от|более)\s+(\d+)\s*(?:\+\s*)?лет[^.\n]{0,30}?опыт", re.I),
    # «N+ years» засчитывается ТОЛЬКО рядом со словом про опыт: на живой вакансии
    # «delivering technology at scale for 20+ years» — это история компании,
    # и max() по всем совпадениям делал из неё требование в 20 лет стажа.
    re.compile(r"(?:experience|опыт|стаж|worked|разработк\w*)[^.\n]{0,60}?"
               r"(\d+)\s*\+\s*(?:years?|лет|года?)", re.I),
    re.compile(r"(\d+)\s*\+\s*(?:years?|лет|года?)[^.\n]{0,40}?"
               r"(?:experience|опыт|стаж|разработк\w*)", re.I),
    re.compile(r"at least\s+(\d+)\s+years?", re.I),
    # У диапазона важна ВЕРХНЯЯ граница: «3–5 years of experience» — это 5,
    # а не 3. Раньше вторая цифра матчилась, но не захватывалась, и функция
    # занижала требование вопреки собственному правилу «берём максимум».
    re.compile(r"\d+\s*(?:-|–|—)\s*(\d+)\s*years? of experience", re.I),
    re.compile(r"минимум\s+(\d+)\s*(?:лет|года?)", re.I),
)

# hh отдаёт бакет опыта отдельным полем — он режет резюме автофильтром, поэтому
# важен не меньше текста. between3And6 → 3, moreThan6 → 6.
_HH_BUCKET = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}
# hh кладёт в выжимку человекочитаемую форму («3–6 лет», «более 6 лет»), а не ключ API.
_HH_HUMAN = re.compile(r"(?:более\s+)?(\d+)\s*(?:–|-|—)?\s*(?:\d+)?\s*лет", re.I)


def required_years(payload: dict | None) -> int | None:
    """Наибольший из названных порогов стажа. None — порог не назван вовсе.

    Берём МАКСИМУМ, а не первое совпадение: в одной вакансии часто стоят и
    «опыт от 3 лет», и «Go от 5 лет» — отсекает именно старшее требование."""
    if not payload:
        return None
    found: list[int] = []
    bucket = (payload.get("extra") or {}).get("experience")
    if isinstance(bucket, str):
        if bucket in _HH_BUCKET:
            found.append(_HH_BUCKET[bucket])
        else:
            m = _HH_HUMAN.search(bucket)
            if m:
                found.append(int(m.group(1)))
    # Переносы строк схлопываем ДО поиска: в живой вакансии Ozon требование
    # выглядит как «важен опыт:\n\n• коммерческой бэкенд-разработки от 3 лет»,
    # и шаблон с [^.\n] на нём не срабатывал — стаж молча оставался «не назван».
    text = " ".join(str(payload.get(k) or "")
                    for k in ("requirements", "description", "title")).replace("\n", " ")
    for rx in _YEARS_RE:
        for m in rx.finditer(text):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 < n <= 20:          # 30+ лет — это не стаж, а мусор разметки
                found.append(n)
    return max(found) if found else None


# ── Право на работу: маркеры, которые нельзя пропустить ──────────────────────
_RTW_RE = (
    (re.compile(r"nationals? of|citizenship|citizens? of|гражданств", re.I),
     "гражданство"),
    (re.compile(r"work (?:authorization|permit|eligibility)|right to work|"
                r"authorized to work|разрешени[ея] на работу|право на работу", re.I),
     "право на работу"),
    (re.compile(r"(?:without|no) (?:visa )?sponsorship|not sponsor", re.I),
     "без спонсорства визы"),
    (re.compile(r"sponsor(?:ship)? (?:is )?(?:available|provided)|"
                r"we can sponsor|visa support|релокацион", re.I),
     "релокация/виза есть"),
    (re.compile(r"excluding russia|вне (?:РФ|России)|кроме (?:РФ|России)", re.I),
     "вне РФ"),
)


def rtw_flags(payload: dict | None) -> str:
    """Маркеры права на работу — по ним модель решает, барьер это или гео-метка."""
    if not payload:
        return ""
    text = " ".join(str(payload.get(k) or "")
                    for k in ("requirements", "description", "apply_note"))
    hits = [label for rx, label in _RTW_RE if rx.search(text)]
    return ", ".join(dict.fromkeys(hits))


# ── Профиль: своя профессия против чужой ─────────────────────────────────────
# Берём ту же регулярку, что и ATS-доски (ATS_ROLE_RE): две регулярки на один
# вопрос расходятся всегда, это уже проверено замером на 4113 заголовках.
# Здесь она работает как ФИЛЬТР, а не как сортировка, поэтому рядом обязателен
# счётчик отсеянного: «тихо потерял» и «отфильтровал» — разные вещи.
_OFF_PROFILE = re.compile(
    r"\b(qa|тестировщ\w*|автотест\w*|sdet|аналитик\w*|analyst|data scientist|"
    r"дизайн\w*|design(er)?|продакт|product manager|project manager|менеджер|"
    r"маркет\w*|marketing|sales|прода(?:ж|вец)\w*|recruit\w*|рекрут\w*|hr\b|"
    r"бухгалт\w*|юрист|支持|поддержк\w*|support engineer|"
    r"android|ios\b|мобильн\w*|mobile|frontend|фронтенд\w*|front-end|"
    r"1c|1с\b|копирайт\w*|контент|smm)\b", re.I)


def on_profile(title: str) -> bool:
    """Профильная ли роль. Перекос сознательно в сторону лишнего: пропустить
    чужую вакансию дешевле, чем потерять свою."""
    t = title or ""
    if _OFF_PROFILE.search(t) and not re.search(r"\b(go|golang|бэкенд|backend)\b", t, re.I):
        return False
    return bool(ATS_ROLE_RE.search(t))


# ── Схлопывание дублей ────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^a-zа-я0-9]+")
# 🔴 Грейдовые слова (senior/middle/lead/старший/ведущий) здесь НАМЕРЕННО
# отсутствуют. Когда они входили в шум, «Backend Engineer - Cards» и «Senior
# Backend Engineer - Cards» у SumUp давали один ключ и схлопывались в одну
# строку — младшая позиция исчезала из выдачи совсем (190 таких групп на живой
# базе). Показать две строки по одной вакансии не страшно, потерять открытую
# позицию — самая дорогая ошибка этого проекта.
_ROLE_NOISE = re.compile(
    r"\b(разработчик|developer|engineer|инженер|программист|backend|бэкенд|"
    r"go|golang|специалист|remote|удал[её]нно|м/ж|m/f|f/m)\b", re.I)


def norm(s: str | None) -> str:
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def dup_group(row: dict) -> str:
    """Ключ склейки: компания + роль без грейдовых и языковых слов.

    Автоматическую склейку по похожести ТЕКСТА мы уже пробовали и отказались
    (коэффициент Дайса 0.29 на паре, которая была одной вакансией). Здесь склейка
    консервативная и объяснимая: тот же работодатель + та же роль после снятия
    шума. Компания пустая → строка не склеивается ни с чем, кроме своего dup_key:
    у нераскрытых работодателей одинаковых заголовков много, а вакансии разные."""
    company = norm(row.get("company"))
    title = row.get("title") or ""
    role = norm(_ROLE_NOISE.sub(" ", title))
    if not company:
        return f"~{row.get('dup_key') or row.get('source')}:{row.get('external_id')}"
    if not role:
        # Заголовок состоит ТОЛЬКО из шумовых слов («Senior Backend Engineer»),
        # и после чистки от него ничего не осталось. Склеивать по пустой роли
        # нельзя: живьём так слиплись «Backend Engineer - Cards» и «Senior
        # Backend Engineer - Cards» у SumUp — две разные открытые позиции, и
        # младшая исчезала из выдачи совсем (190 таких групп на реальной базе).
        role = norm(title)
    return f"{company}|{role}"


def merge(rows: list[dict]) -> list[dict]:
    """Схлопывает дубли, сохраняя ВСЕ источники и лучшую вилку.

    Базой группы становится САМАЯ РАННЯЯ запись, а не первая попавшаяся из
    выборки: порядок строк SQL не гарантирован, и без явной сортировки один и
    тот же прогон на той же базе мог бы дать разный «канон» — а значит, разные
    ссылки в карточках. Тот же принцип у freehire: канон обязан быть старше
    любой новой строки, иначе онлайн- и батч-склейка дерутся между собой."""
    groups: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: (x.get("first_seen") or "",
                                         x.get("source") or "",
                                         str(x.get("external_id") or ""))):
        key = dup_group(r)
        g = groups.get(key)
        if g is None:
            g = dict(r)
            g["_sources"] = []
            g["_urls"] = []
            groups[key] = g
        g["_sources"].append(r.get("source"))
        g["_urls"].append(r.get("url"))
        # Вилка: побеждает та, где есть цифры (площадки часто отдают пустую).
        if not g.get("salary_from") and r.get("salary_from"):
            for k in ("salary_from", "salary_to", "currency", "salary_period",
                      "salary_gross"):
                g[k] = r.get(k)
        if not g.get("company") and r.get("company"):
            g["company"] = r["company"]
        if not g.get("employer_url") and r.get("employer_url"):
            g["employer_url"] = r["employer_url"]
    return list(groups.values())


# ── Совпадение с профилем: детерминированно, без модели ──────────────────────
# Правило знаменателя подсмотрено у freehire (cvmatch, «No LLM, ever»): то, чего
# в тексте вакансии НЕТ, уходит из знаменателя, а не получает ноль — вакансия без
# описания это «неизвестно», а не «не подходит».
#
# 🔴 Совпадения ищутся ТОЛЬКО по границам слов. Подстрочный поиск здесь —
# готовый источник вранья: «go» лежит внутри «Django», «algorithm» и «Diego»,
# и на первом же прогоне Scala-вакансия Codacy получила 100 из 100.
_WORD_CACHE: dict[str, re.Pattern] = {}


def _has(text: str, term: str) -> bool:
    rx = _WORD_CACHE.get(term)
    if rx is None:
        # Термины со слэшем и пробелом (ci/cd, service mesh) — как есть,
        # остальные — по границам слова с учётом русской морфологии.
        body = re.escape(term).replace(r"\ ", r"\s+")
        rx = re.compile(rf"(?<![\w]){body}(?![\w])" if term.isascii()
                        else rf"(?<![а-яё]){body}", re.I)
        _WORD_CACHE[term] = rx
    return bool(rx.search(text))


PROFILE_CORE = ("kubernetes", "k8s", "postgresql", "postgres", "kafka", "grpc",
                "redis", "docker", "ci/cd", "микросервис", "highload",
                "высоконагруж", "распределённ", "распределенн")
PROFILE_PLUS = ("istio", "service mesh", "mtls", "tls", "mqtt", "helm",
                "prometheus", "grafana", "observability", "наблюдаемост",
                "clickhouse", "nats", "rabbitmq", "terraform", "linux",
                "llm", "платформ", "infra", "инфраструктур")
# Чужой основной язык в ЗАГОЛОВКЕ — сильный сигнал, что роль не Go-шная.
# \b после «#», «+» и точки не срабатывает никогда: `\bc#\b` не матчит «C#».
# Из-за этого штраф за чужой основной язык молча не применялся к 222 живым
# заголовкам с C#/.NET/C++ — а скоринг теперь главный ключ сортировки.
_OTHER_LANG = re.compile(
    r"(?<![\w#+.])(scala|java(?!script)|python|php|ruby|rails|c#|\.net|"
    r"node\.?js|kotlin|swift|c\+\+|rust|elixir|perl)(?![\w+#])", re.I)
_GO_RE = re.compile(r"\b(go|golang|голанг)\b", re.I)


def match_score(row: dict, payload: dict | None) -> tuple[int | None, str]:
    """(0..100, пояснение) или (None, 'нет данных') — если судить не по чему."""
    title = (row.get("title") or "") + " " + str((payload or {}).get("title") or "")
    body = " ".join(str((payload or {}).get(k) or "")
                    for k in ("requirements", "description"))
    if len(body.strip()) < 80:
        return None, "нет данных: выжимки нет, судить не по чему"
    full = f"{title} {body}".lower()

    # 1. Go — основной критерий поиска, поэтому он и весит больше всего.
    if _GO_RE.search(title.lower()):
        score, why = 45, "Go в названии"
    elif _GO_RE.search(body.lower()):
        score, why = 28, "Go в тексте"
    else:
        score, why = 0, "Go не упомянут"

    # 2. Стек. Ядро дороже, «плюсы» дешевле — но и то и другое только по словам.
    core = [w for w in PROFILE_CORE if _has(full, w)]
    plus = [w for w in PROFILE_PLUS if _has(full, w)]
    score += min(36, len(core) * 6) + min(14, len(plus) * 2)

    # 3. Чужой основной язык в заголовке — почти всегда не наша роль.
    other = _OTHER_LANG.search(title)
    if other and not _GO_RE.search(title.lower()):
        score = max(0, score - 30)
        why += f"; в названии {other.group(0)}"

    # 4. Требуемый стаж выше формальных пяти лет — понижаем, но не прячем:
    #    решение по такой вакансии всё равно за пользователем.
    years = required_years(payload)
    if years is not None and years > 5:
        score = max(0, score - (years - 5) * 12)
        why += f"; требуют {years} лет"

    if core:
        why += f"; стек: {', '.join(core[:4])}"
    return max(0, min(100, score)), why


# ── Сверка с историей ─────────────────────────────────────────────────────────

_WORKED = {"applied", "rejection", "invitation", "interview", "viewed", "not_viewed"}


def worked_index(negotiations: list[dict]) -> dict[str, list[str]]:
    """{нормализованная компания: [статусы]} — по чему сверяем «уже отработано».

    Статус `other` не участвует: среди них рекламные рассылки, и по ним вакансию
    объявляли отработанной ошибочно."""
    idx: dict[str, list[str]] = {}
    for n in negotiations:
        if n.get("status") not in _WORKED:
            continue
        key = norm(n.get("company") or n.get("company_key"))
        if not key:
            continue
        idx.setdefault(key, []).append(
            f"{n.get('status')} {(n.get('event_at') or '')[:10]}".strip())
    return idx


def build(db: str, *, since: str | None, by: str = "seen",
          sources: list[str] | None = None, limit: int = 0) -> dict:
    """Собирает шорт-лист. Возвращает {rows, stats} — печать отдельно."""
    kw = dict(since=since if by == "published" else None,
              first_seen_since=since if by == "seen" else None,
              sources=sources, exclude_decided=True)
    with store.connect(db) as conn:
        total = store.count(conn, **kw)
        rows = store.query(conn, limit=0, **kw)
        negs = store.negotiations(conn)
        details = {(d["source"], d["external_id"]): d
                   for d in _details(conn)}
        channels = _channels(conn)

    kept = [r for r in rows if on_profile(r.get("title") or "")]
    off = len(rows) - len(kept)
    off_examples = [r.get("title") for r in rows
                    if not on_profile(r.get("title") or "")][:5]
    merged = merge(kept)
    worked = worked_index(negs)

    for g in merged:
        key = (g.get("source"), g.get("external_id"))
        d = details.get(key)
        payload = None
        if d and d.get("payload"):
            try:
                payload = json.loads(d["payload"])
            except (TypeError, ValueError):
                payload = None
        g["_years"] = required_years(payload)
        g["_score"], g["_score_why"] = match_score(g, payload)
        g["_rtw"] = rtw_flags(payload)
        g["_enriched"] = bool(payload)
        g["_worked"] = worked.get(norm(g.get("company")), [])
        g["_channel"] = channels.get(norm(g.get("company")), "")

    # Порядок: свежее и подготовленное — выше. Алфавит по компании как ключ
    # сортировки был бы прямым вредом: наверх всплывали бы «13tm» и «1406
    # Consulting», а не то, что появилось сегодня.
    merged.sort(key=lambda g: (
        bool(g["_worked"]),                       # без истории — раньше
        -(g["_score"] if g["_score"] is not None else -1),  # совпадение с профилем
        not bool(g.get("salary_from")),           # с вилкой — раньше
        -(_freshness(g)),                         # свежее — раньше
    ))
    stats = {"delta": total, "groups": len(merged),
             "off_profile": off, "off_examples": off_examples,
             "collapsed": len(kept) - len(merged),
             "with_years": sum(1 for g in merged if g["_years"] is not None),
             "scored": sum(1 for g in merged if g["_score"] is not None),
             "worked": sum(1 for g in merged if g["_worked"])}
    if limit:
        merged = merged[:limit]
    return {"rows": merged, "stats": stats}


def _freshness(g: dict) -> int:
    """Дата в виде числа для сортировки: 20260804 > 20260731. Нет даты — 0."""
    raw = (g.get("published_at") or g.get("updated_at") or g.get("first_seen") or "")
    digits = re.sub(r"\D", "", raw)[:8]
    return int(digits) if len(digits) == 8 else 0


def _details(conn) -> list[dict]:
    cur = conn.execute("SELECT source, external_id, payload FROM detail "
                       "WHERE status IN ('ok', 'generic')")
    return [{"source": r[0], "external_id": r[1], "payload": r[2]} for r in cur]


def _channels(conn) -> dict[str, str]:
    """Кэш прямых каналов найма — заполняется командой `employer set`."""
    try:
        cur = conn.execute("SELECT company_key, channel FROM employer_channel")
    except Exception:  # noqa: BLE001 — таблицы может не быть на старой базе
        return {}
    return {r[0]: r[1] for r in cur}


def _money(g: dict) -> str:
    lo, hi = g.get("salary_from"), g.get("salary_to")
    if not lo and not hi:
        return "—"
    cur = g.get("currency") or ""
    per = {"hour": "/час", "month": "/мес", "year": "/год"}.get(g.get("salary_period"), "")
    if lo and hi:
        return f"{lo}–{hi} {cur}{per}".strip()
    return f"от {lo or hi} {cur}{per}".strip()


def render(res: dict, *, fmt: str = "table") -> str:
    """Компактная выдача: одна строка — одна вакансия, без повторов и воды."""
    rows, st = res["rows"], res["stats"]
    if fmt == "json":
        return json.dumps(res, ensure_ascii=False, default=str)
    out = [
        f"# shortlist: {st['groups']} вакансий "
        f"(дельта {st['delta']}, чужая профессия {st['off_profile']}, "
        f"схлопнуто дублей {st['collapsed']}, стаж распознан у {st['with_years']}, "
        f"с историей по компании {st['worked']})",
        "",
        "Отсев по профессии — не тихий: примеры отсеянного — "
        + "; ".join((t or "")[:40] for t in st.get("off_examples") or []) or "—",
        "",
        "Отбор по фиту — не здесь: это механическая свёртка. Колонки: "
        "стаж — МАКСИМАЛЬНЫЙ названный порог (пусто = не назван), "
        "история — что компания уже отвечала, RTW — маркеры права на работу.",
        "",
        "| # | Роль | Компания | Деньги | Формат | Фит | Стаж | RTW | История | Источники | Ссылка |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, g in enumerate(rows, 1):
        srcs = ",".join(dict.fromkeys(s for s in g["_sources"] if s))
        loc = (g.get("location") or "")[:22]
        if g.get("remote"):
            loc = (loc + " remote").strip()
        out.append(
            f"| {i} | {(g.get('title') or '')[:58].replace('|', '/')} "
            f"| {(g.get('company') or '—')[:26].replace('|', '/')} "
            f"| {_money(g)} | {loc.replace('|', '/')} "
            f"| {g['_score'] if g['_score'] is not None else '—'} "
            f"| {g['_years'] if g['_years'] is not None else ''} "
            f"| {g['_rtw'][:34]} "
            f"| {'; '.join(g['_worked'][:2])[:38]} "
            f"| {srcs[:28]} | {g.get('url')} |")
    if not rows:
        out.append("| — | дельта пуста | | | | | | | | | |")
    return "\n".join(out)


def cli(args) -> int:
    since = store.since_arg(args.since) if args.since else None
    res = build(args.db, since=since, by=args.by,
                sources=args.sources.split(",") if args.sources else None,
                limit=args.limit)
    print(render(res, fmt=args.format))
    if not res["rows"]:
        print("\nдельта пуста — окно слишком узкое или всё уже отработано",
              file=sys.stderr)
        return 1
    return 0
