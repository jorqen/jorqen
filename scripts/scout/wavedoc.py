"""Главный документ волны — скелет `.jobs/<дата>.md`, собранный скриптом.

Зачем. По SKILL.md документ волны обязан содержать таблицу отобранного, покрытие
источников, раздел отсева и хвосты. Всё это уже лежит в базе: строки — в
`shortlist`, покрытие — в отчёте прогона, решения — в таблице `decision`. Модель
переписывала это руками по тридцать строк на волну, то есть выполняла работу
алгоритма и тратила на неё контекст.

Граница проведена там же, где инвариант репозитория «суждение — зона модели»:
скрипт собирает ФАКТЫ и оставляет размеченные места под то, чего в базе нет.
Ни фит, ни рекомендация, ни текст письма здесь не сочиняются и сочиняться не
должны — иначе документ станет складным и неверным.

Файл НЕ перезаписывается молча: волну переигрывают, и потерять дописанное
суждение дороже, чем показать команду с `--force`.
"""

from __future__ import annotations

import os
import re

from . import shortlist, store

# Транслит для слагов каталогов. Свой, а не через стороннюю библиотеку:
# ядро обязано подниматься без установки (инвариант 3).
_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Организационно-правовые формы в слаг не идут: «АО «Каргономика»» и
# «Каргономика» — одна компания и обязаны дать один каталог.
_LEGAL = re.compile(r"^\s*(ООО|ОАО|ЗАО|АО|ПАО|ИП|НКО|ГК|LLC|Ltd|Inc|GmbH|OOO)\b[\s.«\"']*",
                    re.I)


def slug(name: str | None) -> str:
    """Название компании → слаг каталога. Пусто → `_hidden`.

    Пустое имя — это НЕ ошибка, а частый штатный случай: работодатель за
    заглушкой агрегатора. Такие карточки лежат отдельно (`_hidden`), потому что
    складывать их в каталог с именем «» значит смешать разные компании в одну.
    """
    s = _LEGAL.sub("", (name or "").strip().lower())
    out = []
    for ch in s:
        if ch in _TR:
            out.append(_TR[ch])
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    res = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    return res or "_hidden"


def _money(row: dict) -> str:
    return shortlist._money(row)


def build(db: str, *, days: int, top: int) -> dict:
    """Факты волны для документа. Суждение сюда не попадает по построению."""
    since = store.since_arg(f"{days}d")
    sl = shortlist.build(db, since=since, by="seen", sources=None, limit=0)
    rows = (sl.get("rows") or [])[:top]
    with store.connect(db) as conn:
        decided = [dict(r) for r in conn.execute(
            "SELECT d.source, d.external_id, d.state, d.note, v.title, v.company "
            "FROM decision d LEFT JOIN vacancy v "
            "ON v.source=d.source AND v.external_id=d.external_id "
            "WHERE d.state IN ('rejected','skipped') "
            "ORDER BY d.updated_at DESC LIMIT 200").fetchall()]
        run = store.last_run(conn)
    return {"rows": rows, "stats": sl.get("stats") or {},
            "total": len(sl.get("rows") or []), "decided": decided, "run": run,
            "days": days, "top": top}


def render(data: dict, date: str) -> str:
    """Скелет документа. Места под суждение помечены и оставлены ПУСТЫМИ."""
    out = [f"# Волна {date}", ""]
    st = data["stats"]
    out.append(f"Окно {data['days']} дней. Дельта {st.get('delta', 0)}, "
               f"профильных {st.get('groups', 0)}, чужая профессия "
               f"{st.get('off_profile', 0)}, схлопнуто дублей "
               f"{st.get('collapsed', 0)}.")
    out.append("")

    run = data.get("run") or {}
    sources = run.get("sources") or []
    if sources:
        out.append("## Покрытие источников")
        out.append("")
        out.append("| источник | статус | найдено | с |")
        out.append("|---|---|---|---|")
        for s in sources:
            out.append(f"| {s['source']} | {s['status']} | {s.get('found', 0)} "
                       f"| {(s.get('elapsed_ms') or 0) / 1000:.0f} |")
        out.append("")

    out.append(f"## Отобрано: {len(data['rows'])} из {data['total']}")
    out.append("")
    out.append("| # | Роль | Компания | Деньги | Стаж | RTW | Канал | Ссылка |")
    out.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(data["rows"], 1):
        out.append(
            f"| {i} | {(r.get('title') or '')[:56].replace('|', '/')} "
            f"| {(r.get('company') or '—')[:26].replace('|', '/')} "
            f"| {_money(r)} "
            f"| {r.get('_years') if r.get('_years') is not None else ''} "
            f"| {(r.get('_rtw') or '')[:24]} "
            f"| {'✓' if r.get('_channel') else ''} | {r.get('url')} |")
    out.append("")

    if data["decided"]:
        out.append("## Отсеяно ранее")
        out.append("")
        for d in data["decided"][:60]:
            what = d.get("title") or f"{d['source']}/{d['external_id']}"
            who = f" @ {d['company']}" if d.get("company") else ""
            out.append(f"- **{what}**{who} — {d['state']}"
                       + (f": {d['note']}" if d.get("note") else ""))
        out.append("")

    # Ниже — ровно те разделы, которые скрипт заполнять НЕ ИМЕЕТ ПРАВА.
    # Пустые заголовки здесь не формальность: они показывают, что решение
    # не принято, тогда как отсутствие раздела читается как «вопрос не вставал».
    out.append("## Рекомендации")
    out.append("")
    out.append("<!-- ЗАПОЛНЯЕТ МОДЕЛЬ: кому писать в первую очередь и почему. "
               "Скрипт сюда не пишет намеренно — это суждение, см. инвариант "
               "«отбор и тексты писем — зона модели». -->")
    out.append("")
    out.append("## Хвосты")
    out.append("")
    out.append("<!-- ЗАПОЛНЯЕТ МОДЕЛЬ: что осталось невыясненным, кому написать "
               "уточнение, какие вакансии ждут ответа. -->")
    return "\n".join(out) + "\n"


def write(db: str, *, days: int, top: int, date: str, root: str = ".jobs",
          force: bool = False) -> tuple[str, str]:
    """(путь, что сделано). Существующий файл не трогает без `force`."""
    path = os.path.join(root, f"{date}.md")
    if os.path.exists(path) and not force:
        return path, ("файл уже есть — не перезаписан. Волну переигрывают, и "
                      "потерять дописанное суждение дороже: `--force`, если "
                      "скелет действительно надо пересобрать")
    text = render(build(db, days=days, top=top), date)
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, f"записан скелет ({len(text.splitlines())} строк)"


def cli(args) -> int:
    date = getattr(args, "date", None) or store.now()[:10]
    if getattr(args, "write", False):
        path, what = write(args.db, days=args.days, top=args.top, date=date,
                           force=getattr(args, "force", False))
        print(f"{path}: {what}")
        return 0
    print(render(build(args.db, days=args.days, top=args.top), date))
    return 0
