"""Черновик фита — сводка по фактам, которые карточка уже посчитала.

Что такое фит и почему у него есть машинная часть. Фит отвечает на четыре
вопроса: чем кандидат силён под ЭТУ вакансию, чего не хватает, каковы условия и
стоит ли писать. Первые три — пересказ того, что алгоритм уже разметил в таблице
требований, в барьерах и во флагах. Четвёртый — суждение, и его машина не считает.

Раньше все четыре писал я, вручную, на каждой карточке волны. Требование
владельца 09.08.2026: «всё, что можно переложить на алгоритм, нужно переложить,
чтобы агент работал минимально». Здесь переложены ровно первые три.

🔴 Черновик НЕ ЗАМЕНЯЕТ суждение и не притворяется им: строка «Вывод» остаётся
пустой с прямым указанием, что решение за моделью. Карточка со сгенерированным
фитом и незаполненным выводом — незакрытая карточка, и `lint-cards` это видит.
"""

from __future__ import annotations

import re

# Понятия, которые в разговоре о фите весят больше остальных: по ним человек
# решает, а не по перечислению технологий.
_HEAVY = ("highload", "нагруж", "rps", "latency", "p99", "распределён", "distributed",
          "платеж", "платёж", "payment", "транзакц", "финтех", "fintech",
          "наблюдаем", "observability", "kafka", "очеред", "queue", "kubernetes")


def _row_terms(row: str) -> str:
    return (row or "").lower()


def parse_table(text: str) -> list[tuple[str, str, str, bool]]:
    """Строки таблицы требований: (требование, уровень, что у тебя, совпало)."""
    out = []
    for m in re.finditer(r"^\| (.{4,}?) *\| (🔴 обяз\.|плюсом|—) *\| (.*?) *\| (.) *\|$",
                         text, re.M):
        req, level, mine, mark = m.groups()
        if req.strip().lower() == "требование":
            continue
        out.append((req.strip(), level.strip(), mine.strip(), mark.strip() == "✓"))
    return out


def _section(text: str, title: str) -> list[str]:
    """Пункты раздела карточки, без заголовка."""
    m = re.search(rf"### {re.escape(title)}[^\n]*\n(.*?)(?:\n### |\Z)", text, re.S)
    if not m:
        return []
    return [ln.strip("- ").strip() for ln in m.group(1).splitlines()
            if ln.strip().startswith("-")]


def draft(text: str) -> str:
    """Черновик раздела «Фит» по готовой карточке. Пусто — если считать нечего.

    На вход идёт САМА карточка, а не сырые данные: всё нужное в ней уже
    посчитано и размечено, и второй разбор тех же фактов разошёлся бы с первым.
    """
    rows = parse_table(text)
    if not rows:
        return ""

    hit = [r for r in rows if r[3]]
    miss_must = [r for r in rows if not r[3] and r[1].startswith("🔴")]
    # Сильные совпадения вперёд: по ним и решают.
    hit.sort(key=lambda r: (0 if any(w in _row_terms(r[0]) for w in _HEAVY) else 1))

    def field(name: str) -> str:
        m = re.search(rf"\*\*{name}:\*\* (.+)", text)
        return m.group(1).strip() if m else ""

    money, fmt, years = field("Деньги"), field("Формат"), field("Требуемый стаж")
    barriers = [b for b in _section(text, "Формальные барьеры")
                if "не найдено" not in b]
    flags = [f for f in _section(text, "Флаги") if "не найдено" not in f]

    out: list[str] = []

    if hit:
        skills = []
        for _req, _lvl, mine, _ok in hit[:6]:
            for part in mine.split(","):
                part = part.strip()
                if part and part not in skills and not part.startswith("http"):
                    skills.append(part)
        covered = ", ".join(skills[:10]) or "по стеку"
        out.append(f"**Чем силён.** Совпало {len(hit)} требований из {len(rows)}: "
                   f"{covered}. Разбор эпизодов — в письме ниже: черновик собран "
                   f"под эти же требования.")

    if miss_must:
        items = "; ".join(r[0][:90] for r in miss_must[:4])
        out.append(f"**Чего не хватает.** Обязательных пунктов без совпадения: "
                   f"{len(miss_must)} — {items}. Проверь глазами: совпадение "
                   f"считается по словам резюме и может не увидеть синоним.")
    elif rows:
        out.append("**Чего не хватает.** Обязательных требований без совпадения "
                   "нет — всё, что площадка пометила обязательным, закрыто.")

    conditions = []
    if money and money not in ("не указана", "—"):
        conditions.append(f"деньги {money}")
    if fmt and fmt not in ("—", "unknown"):
        conditions.append(f"формат {fmt}")
    if years and years != "не назван":
        conditions.append(f"требуют стаж {years}")
    if conditions:
        out.append("**Условия.** " + ", ".join(conditions) + ".")

    if barriers:
        out.append("🔴 **Барьеры.** " + " ".join(barriers[:3]))
    risky = [f for f in flags if f.startswith(("🔴", "🟡"))]
    if risky:
        out.append("**На что смотреть.** " + " ".join(risky[:3]))

    out.append("**Вывод: <решает модель>.** Черновик выше собран по фактам "
               "карточки; писать или пропустить — суждение, и его машина не "
               "считает.")
    return "\n\n".join(out)
