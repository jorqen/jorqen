"""dups — показать СОСТАВ схлопнутых групп, чтобы склейку можно было оспорить.

Зачем команда. Инвариант 7 требует, чтобы дедуп ошибался в сторону РАЗДЕЛЕНИЯ:
лишняя строка стоит секунды внимания, лишняя склейка — потерянной вакансии.
Проверить это по числу «схлопнуто N» нельзя — под ним одинаково выглядят два
разных события:

* одна вакансия одной компании, открытая в тридцати городах (это 82% всего
  схлопнутого на живой базе — adesso SE, Bending Spoons), склеивать её ПРАВИЛЬНО;
* две разные позиции одной команды, слипшиеся в одну (инцидент SumUp: «Backend
  Engineer - Cards» и «Senior Backend Engineer - Cards», младшая исчезла из
  выдачи совсем).

Отличает их одно: НАЗВАНИЕ. Одинаковые названия на разных площадках — скучный
и верный случай. Разные названия внутри одной группы — то, что надо смотреть
глазами. Поэтому команда не пересказывает счётчики, а сортирует группы по
подозрительности и печатает состав.

Своей склейки здесь НЕТ намеренно: группы берутся у `shortlist.build`, то есть
ровно те, что уходят в выдачу. Второй алгоритм дедупа для аудита разошёлся бы
с настоящим, и аудит стал бы проверять сам себя.
"""

from __future__ import annotations

from . import shortlist, store
from .shortlist import norm

# Роль и грейд считаются ТЕМИ ЖЕ функциями, что и в самом дедупе. Своя формула
# здесь была бы третьей копией и разошлась бы молча: аудит начал бы одобрять
# склейки, которых дедуп не делает, и наоборот — то есть проверял бы не то,
# что происходит.
#
# Грейд при сравнении роли НЕ снимается (`drop_grade` по умолчанию выключен):
# «Backend Engineer - Cards» и «Senior Backend Engineer - Cards» у SumUp —
# две разные открытые позиции, и аудит обязан показать их расхождение, а не
# сгладить его.
_role = shortlist.role_key
_grade = shortlist.grade_of


def audit(db: str, *, since: str | None, by: str = "seen",
          simhash_bits: int = shortlist.SIMHASH_MAX_DIST) -> dict:
    """{'groups': [...], 'stats': {...}} — схлопнутые группы, худшие первыми.

    `risk` каждой группы — это НЕ вероятность и не оценка модели, а перечень
    проверяемых расхождений внутри группы. Их три, по убыванию цены ошибки:

      * `компания` — участники числятся за разными работодателями. Слой 3
        межкомпанейские склейки запрещает вовсе, значит такое приехало по
        ключу «компания + роль» с пустой компанией либо по совпадению адреса;
      * `грейд` — разные грейды. Прямой случай SumUp;
      * `роль` — разные роли после снятия грейдового шума.
    """
    res = shortlist.build(db, since=since, by=by, limit=0,
                          simhash_bits=simhash_bits)
    out = []
    for g in res["rows"]:
        members = g.get("_members") or []
        if len(members) < 2:
            continue
        companies = {norm(m.get("company")) for m in members if m.get("company")}
        roles = {_role(m.get("title")) for m in members}
        grades = {_grade(m.get("title")) for m in members if _grade(m.get("title"))}
        risk = []
        if len(companies) > 1:
            risk.append("компания")
        if len(grades) > 1:
            risk.append("грейд")
        if len(roles) > 1:
            risk.append("роль")
        out.append({"members": members, "risk": risk,
                    "near_dup": bool(g.get("_near_dup")),
                    "cities": len({m.get("location") for m in members
                                   if m.get("location")}),
                    "title": g.get("title"), "company": g.get("company")})
    # Порядок — по цене ошибки: сначала разные компании, потом разные грейды,
    # потом просто разные роли. Внутри — большие группы раньше: в них и цена
    # потери выше, и разбирать их выгоднее.
    order = {"компания": 0, "грейд": 1, "роль": 2}
    out.sort(key=lambda x: (min((order[r] for r in x["risk"]), default=9),
                            -len(x["members"])))
    stats = dict(res["stats"])
    stats["multi"] = len(out)
    stats["risky"] = sum(1 for x in out if x["risk"])
    stats["by_risk"] = {name: sum(1 for x in out if name in x["risk"])
                        for name in ("компания", "грейд", "роль")}
    # Группы, где расхождений нет вовсе, — те самые «одна работа в N городах».
    stats["cities_only"] = sum(1 for x in out if not x["risk"])
    return {"groups": out, "stats": stats}


def render(res: dict, *, sample: int = 12) -> str:
    st, groups = res["stats"], res["groups"]
    lines = [
        "# dups — что именно схлопнул дедуп",
        "",
        f"дельта {st['delta']}, групп {st['groups']}, "
        f"схлопнуто строк {st['collapsed']} (из них слоем описаний {st['near_dup']})",
        f"групп больше одной строки: {st['multi']}, "
        f"из них с расхождением внутри: {st['risky']}",
        f"  · разные компании: {st['by_risk']['компания']}   "
        f"разные грейды: {st['by_risk']['грейд']}   "
        f"разные роли: {st['by_risk']['роль']}",
        f"  · без расхождений (одна работа в нескольких городах): "
        f"{st['cities_only']} — это штатное схлопывание, инвариант не задет",
        "",
    ]
    risky = [g for g in groups if g["risk"]]
    if not risky:
        lines.append("Расхождений внутри групп нет: всё схлопнутое — одна и та "
                     "же роль одной компании. Порог не съехал.")
        return "\n".join(lines) + "\n"

    lines.append(f"## Смотреть глазами — {min(sample, len(risky))} худших "
                 f"из {len(risky)}")
    lines.append("")
    for g in risky[:sample]:
        mark = " [слой описаний]" if g["near_dup"] else ""
        lines.append(f"### {', '.join(g['risk'])}{mark} — {len(g['members'])} строк")
        for m in g["members"]:
            where = f" · {m['location']}" if m.get("location") else ""
            lines.append(f"  · [{m['source']}] {m.get('title') or '—'} "
                         f"@ {m.get('company') or 'работодатель не раскрыт'}{where}")
            lines.append(f"      {m.get('url') or ''}")
        lines.append("")
    lines.append("Склейка неверна — раздели порогом: `--simhash-bits` выше "
                 "разводит слой описаний, `-1` выключает его целиком.")
    return "\n".join(lines) + "\n"


def cli(args) -> int:
    since = store.since_arg(getattr(args, "since", None), db=args.db)
    res = audit(args.db, since=since, by=getattr(args, "by", "seen"),
                simhash_bits=getattr(args, "simhash_bits",
                                     shortlist.SIMHASH_MAX_DIST))
    print(render(res, sample=getattr(args, "sample", 12)))
    # Код возврата — признак «есть что разобрать», как у `tails`: команда
    # годится в рутину. Расхождение внутри группы это не авария, а повод
    # посмотреть, поэтому 1, а не 2.
    return 1 if res["stats"]["risky"] else 0
