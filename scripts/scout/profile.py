"""profile — чем рынок отличается от резюме, посчитано по собственной базе.

Две задачи скилла упираются в один и тот же вопрос, и обе решались на глаз:

1. **Точность профиля.** Отбор идёт по `resume/resume.yaml`, но резюме — выжимка.
   Термин, стоящий в списке навыков и ни разу не подтверждённый в опыте, и термин,
   которого нет вовсе, для отбора одинаковы, а для найма — нет. Здесь они наконец
   различаются, и видно, ЧТО именно стоит спросить у пользователя: не «расскажи
   о себе», а «вот эти 6 технологий требуют 200+ вакансий, а резюме про них молчит».
2. **Конверсия.** Что править в резюме, было вопросом вкуса. Теперь это разность
   двух множеств: чего рынок просит часто, а резюме не подтверждает ничем.

Считается ПО РАЗНЫМ ВАКАНСИЯМ И РАЗНЫМ КОМПАНИЯМ, а не по вхождениям: одна
компания, разместившая вакансию на пяти площадках, иначе выглядит как спрос
впятеро больше настоящего. Ключ компании — тот же `shortlist.norm`, что и в
дедупликации, чтобы «Ozon» и «ОЗОН» не считались двумя нанимателями.

Сеть не трогается: всё уже лежит в `.scout/scout.db`.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import card
from .shortlist import _has, norm, on_profile

# ── Словарь спроса ───────────────────────────────────────────────────────────
# Названия технологий в русских вакансиях пишутся латиницей всегда («Kubernetes»,
# «Kafka», «PostgreSQL»), поэтому берём только латинские токены. Правило грубое,
# зато не требует поддерживать вручную список технологий, который устареет.
_TOKEN = re.compile(r"[a-z][a-z0-9+#._/-]{1,24}")

# Латинские слова, которые встречаются в требованиях часто и технологией не являются.
_STOP = frozenset("""
a about above after all also an and any are as at back be been before being both but by
can come could day do does doing done down each even every few first for from get give go
good great had has have he her here him his how i if in into is it its just know like look
make many may me more most much must my new no not now of off on once one only or other
our out over own people plus same say see she should since so some such take team than
that the their them then there these they thing think this those through time to too two
up us use used using very want was way we well were what when where which while who why
will with within work working would year years you your
also etc ie eg vs via per non pre post multi self full part high low new old big small
experience level senior junior middle lead strong deep solid proven hands
english russian remote office hybrid fulltime
""".split())

# Слова из вакансий, которые почти всегда шум требований, а не стек.
_STOP_JOB = frozenset("""
опыт работы знание умение навыки требования обязанности условия задачи проекты команда
разработка разработки разработке технологии стек будет плюсом желательно обязательно
понимание владение готовность способность коммуникация ответственность внимание
development developer engineer engineering software backend frontend fullstack code coding
skills knowledge understanding ability responsibilities requirements qualifications
bonus advantage nice preferred required minimum degree computer science bachelor master
company product project products projects business customer customers user users client
clients service services system systems platform solution solutions application applications
tools tool technologies technology stack quality performance security support
""".split())


def _is_term(tok: str) -> bool:
    if len(tok) < 2 or tok in _STOP or tok in _STOP_JOB:
        return False
    if tok.isdigit() or re.fullmatch(r"[a-z]\d*", tok):
        return False
    return True


# ── Что подтверждает резюме ──────────────────────────────────────────────────
# Уровни намеренно разные: рекрутёр и ATS видят список навыков, нанимающий
# менеджер ищет подтверждение в пунктах опыта. Термин «в списке, но нигде не
# делался» — это не навык, а заявка, и на интервью её будут ковырять первой.
LEVELS = {"работа": 3, "дело": 2, "список": 1, "—": 0}
LEVEL_NOTE = {
    "работа": "в стеке места работы",
    "дело": "названо в пункте опыта",
    "список": "только в списке навыков, ничем не подтверждено",
    "—": "в резюме нет",
}


def resume_evidence(path: str = card.RESUME_PATH) -> tuple[dict[str, str], str | None]:
    """{термин: уровень подтверждения}. Термины — те же, что видит `card`."""
    data, why = card._resume(path)
    if why:
        return {}, why

    exp = (data.get("experience") or {}).get("items") or []

    # Сплошной текст опыта: пункты highlights + summary. По нему проверяем,
    # подтверждён ли термин делом, а не только перечислен.
    prose: list[str] = []
    for item in exp:
        for key in ("summary", "highlights"):
            val = item.get(key)
            for chunk in (val if isinstance(val, list) else [val]):
                if isinstance(chunk, dict):
                    prose += [str(v) for v in chunk.values()]
                elif chunk:
                    prose.append(str(chunk))
    for card_ in ((data.get("strengths") or {}).get("cards") or []):
        body = card_.get("body")
        prose += [str(v) for v in body.values()] if isinstance(body, dict) else [str(body or "")]
    prose_text = " ".join(prose).lower()

    stacks: set[str] = set()
    for item in exp:
        for raw in (item.get("stack") or []):
            stacks.add(str(raw).lower())

    terms, _ = card.load_skills(path)
    out: dict[str, str] = {}
    for term in terms:
        if any(_has(s, term) for s in stacks):
            out[term] = "работа"
        elif _has(prose_text, term):
            out[term] = "дело"
        else:
            out[term] = "список"
    return out, None


# ── Спрос ────────────────────────────────────────────────────────────────────
def _rows(conn, days: int | None, *, profile_only: bool = True) -> list[dict]:
    sql = ("SELECT title, company, description, first_seen, published_at "
           "FROM vacancy")
    args: list = []
    if days is not None:
        sql += (" WHERE COALESCE(published_at, first_seen) >= "
                "date('now', ?)")
        args.append(f"-{int(days)} day")
    rows = [dict(r) for r in conn.execute(sql, args)]
    if profile_only:
        rows = [r for r in rows if on_profile(r.get("title") or "")]
    return rows


def demand(rows: list[dict]) -> tuple[dict[str, int], dict[str, int], int]:
    """(вакансий на термин, компаний на термин, всего вакансий).

    Компания считается один раз, даже если разместила вакансию на пяти
    площадках: иначе один активный наниматель выглядит как рыночный тренд.
    """
    per_vac: dict[str, int] = defaultdict(int)
    per_co: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("title", "description"))
        reqs = card.requirements(text, limit=60) or [text[:4000]]
        toks = set()
        for line in reqs:
            low = line.lower()
            words = _TOKEN.findall(low)
            toks.update(w for w in words if _is_term(w))
            # Биграммы: «service mesh», «clean architecture», «apache kafka».
            for a, b in zip(words, words[1:]):
                if _is_term(a) and _is_term(b):
                    toks.add(f"{a} {b}")
        company = norm(row.get("company")) or f"?{id(row)}"
        for t in toks:
            per_vac[t] += 1
            per_co[t].add(company)
    return dict(per_vac), {t: len(c) for t, c in per_co.items()}, len(rows)


# ── Воронка откликов ─────────────────────────────────────────────────────────
# Всё, что известно о конверсии, лежит в negotiation (ответы площадок и почты)
# и decision (наши собственные отметки). Без этой таблицы любая правка резюме —
# вкусовщина: не с чем сравнить «до» и «после».
_FUNNEL_ORDER = ("applied", "not_viewed", "viewed", "invitation", "interview",
                 "rejection", "pending", "other")
_FUNNEL_RU = {
    "applied": "отклик отправлен", "not_viewed": "не просмотрен",
    "viewed": "просмотрен", "invitation": "приглашение",
    "interview": "интервью", "rejection": "отказ",
    "pending": "в ожидании", "other": "прочее",
}


def funnel(conn) -> tuple[dict[str, int], dict[str, int]]:
    neg: dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT status, COUNT(*) n FROM negotiation GROUP BY status"):
        neg[r["status"]] += r["n"]
    dec: dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT state, COUNT(*) n FROM decision GROUP BY state"):
        dec[r["state"]] += r["n"]
    return dict(neg), dict(dec)


def titles(rows: list[dict], top: int = 12) -> list[tuple[str, int]]:
    """Самые частые формулировки роли — с чем должен совпадать заголовок резюме."""
    cnt: dict[str, int] = defaultdict(int)
    for row in rows:
        t = re.sub(r"\s+", " ", (row.get("title") or "")).strip().lower()
        t = re.sub(r"\s*[(\[].*?[)\]]", "", t)
        t = re.sub(r"[,/|].*$", "", t).strip()
        if t:
            cnt[t] += 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])[:top]


# ── Отчёт ────────────────────────────────────────────────────────────────────
def build(conn, *, days: int | None = None, top: int = 25,
          min_companies: int = 3) -> str:
    rows = _rows(conn, days)
    per_vac, per_co, total = demand(rows)
    ev, why = resume_evidence()
    out: list[str] = []
    window = f"за {days} дн." if days else "по всей базе"
    out.append(f"# Профиль против рынка ({window}, профильных вакансий: {total})")
    if why:
        out.append(f"\n⚠️ {why}")
    if not total:
        out.append("\nВ окне нет профильных вакансий — расширь `--days` или сними фильтр.")
        return "\n".join(out)

    neg, dec = funnel(conn)
    out.append("\n## Воронка откликов\n")
    out.append("| статус | сколько |")
    out.append("|---|---|")
    for k in _FUNNEL_ORDER:
        if neg.get(k):
            out.append(f"| {_FUNNEL_RU[k]} | {neg[k]} |")
    seen = sum(neg.get(k, 0) for k in ("viewed", "invitation", "interview", "rejection"))
    answered = sum(neg.get(k, 0) for k in ("invitation", "interview", "rejection"))
    good = sum(neg.get(k, 0) for k in ("invitation", "interview"))
    tot = sum(neg.values())
    if tot:
        out.append(f"\nВсего записей: {tot}. Дошло до просмотра: {seen} "
                   f"({seen * 100 // tot}%). Есть ответ: {answered}. "
                   f"Из ответов приглашений/интервью: {good}"
                   + (f" ({good * 100 // answered}%)" if answered else ""))
    if dec:
        out.append("Наши отметки: "
                   + ", ".join(f"{k} {v}" for k, v in sorted(dec.items())))

    # Спрос против резюме
    ranked = sorted(per_co.items(), key=lambda kv: (-kv[1], -per_vac.get(kv[0], 0)))
    ranked = [(t, c) for t, c in ranked if c >= min_companies]

    out.append(f"\n## Чего требует рынок — топ-{top}\n")
    out.append("| термин | вакансий | компаний | в резюме |")
    out.append("|---|---:|---:|---|")
    for term, co in ranked[:top]:
        lvl = _resume_level(term, ev)
        out.append(f"| {term} | {per_vac[term]} | {co} | {lvl} |")

    gaps = [(t, c) for t, c in ranked if _resume_level(t, ev) == "—"]
    out.append(f"\n## Пробелы: рынок просит, резюме молчит — топ-{top}\n")
    if not gaps:
        out.append("_Пусто: всё, что просят от трёх компаний и чаще, в резюме есть._")
    else:
        out.append("Это НЕ список «чего он не умеет» — это список вопросов к нему. "
                   "Термин мог просто не попасть в выжимку.\n")
        out.append("| термин | вакансий | компаний |")
        out.append("|---|---:|---:|")
        for term, co in gaps[:top]:
            out.append(f"| {term} | {per_vac[term]} | {co} |")

    weak = [(t, per_co.get(t, 0)) for t, lvl in ev.items()
            if lvl == "список" and per_co.get(t, 0) >= min_companies]
    weak.sort(key=lambda kv: -kv[1])
    out.append("\n## Заявлено, но ничем не подтверждено\n")
    if not weak:
        out.append("_Пусто: всё востребованное подтверждено опытом._")
    else:
        out.append("Стоит в списке навыков, не встречается ни в одном стеке и ни в одном "
                   "пункте опыта. Спрос есть — значит, спросят на интервью именно про это.\n")
        out.append("| термин | компаний просят |")
        out.append("|---|---:|")
        for term, co in weak[:top]:
            out.append(f"| {term} | {co} |")

    dead = sorted((t for t, lvl in ev.items()
                   if lvl != "—" and per_co.get(t, 0) == 0))
    if dead:
        out.append("\n## Балласт: есть в резюме, рынок не просит ни разу\n")
        out.append(", ".join(dead))

    out.append("\n## Как рынок называет роль\n")
    out.append("| заголовок | сколько |")
    out.append("|---|---:|")
    for t, n in titles(rows):
        out.append(f"| {t} | {n} |")

    return "\n".join(out)


def _resume_level(term: str, ev: dict[str, str]) -> str:
    """Уровень подтверждения термина спроса. Термин рынка и термин резюме
    редко совпадают буквой в букву («k8s» против «kubernetes»), поэтому
    сверяем по вхождению слова в обе стороны."""
    best = "—"
    for own, lvl in ev.items():
        if own == term or _has(term, own) or _has(own, term):
            if LEVELS[lvl] > LEVELS[best]:
                best = lvl
    return best
