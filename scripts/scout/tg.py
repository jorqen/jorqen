"""Разбор телеграм-дампов формата MCP-коннектора.

Формат: `[#id] [2026-07-28T07:47:02.000Z] Автор: текст`, тело многострочное, сообщения
разделены пустой строкой — но пустые строки бывают и ВНУТРИ тела, поэтому границей
сообщения считается только строка, матчащая `^\\[#\\d+\\] \\[дата\\]`. Резать по пустой
строке нельзя: половина вакансий развалилась бы на куски и посчиталась дважды.

Здесь ничего не выбрасывается молча — только группируется. Резюме и реклама уходят
в перечни id (их всё ещё можно посмотреть в дампе), всё остальное — кандидаты
в вакансии, и решение по ним принимает модель, а не регулярка.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Начало сообщения: только id + ISO-дата в квадратных скобках. Автор может быть
# каким угодно (с двоеточиями, эмодзи), поэтому в якорь он не входит.
MSG_HEADER = re.compile(r"^\[#(\d+)\] \[(\d{4}-\d{2}-\d{2}T[^\]]+)\] ?(.*)$")

RE_GO = re.compile(r"\bgo\b|golang", re.I)
RE_RESUME = re.compile(r"#резюме|#cv\b|#resume\b|#opentowork\b|ищу работу", re.I)
# Анкетная форма резюме: «Должность: …» и «Опыт: …» отдельными строками.
RE_FORM_POSITION = re.compile(r"^\s*Должность\s*:", re.I | re.M)
RE_FORM_EXP = re.compile(r"^\s*Опыт\s*:", re.I | re.M)
RE_AD = re.compile(r"#реклама\b|\berid\s*[:=]", re.I)


@dataclass
class Message:
    id: str
    date: str
    author: str
    body: str                 # текст после «Автор: », включая многострочное тело
    tags: list[str] = field(default_factory=list)
    category: str = "candidate"   # candidate | resume | ad


def parse_dump(text: str) -> list[Message]:
    """Режет дамп на сообщения по заголовочной строке. Хвост до первого заголовка
    игнорируется только если он пуст — иначе честно попадает в отчёт отдельной строкой."""
    messages: list[Message] = []
    cur: Message | None = None
    preamble: list[str] = []
    for line in text.split("\n"):
        m = MSG_HEADER.match(line)
        if m:
            if cur:
                cur.body = cur.body.rstrip()
                messages.append(cur)
            rest = m.group(3)
            # «Автор: текст» — двоеточие-разделитель ищем первым вхождением «: ».
            # Автор с двоеточием внутри имени размечется неточно; это заголовок,
            # а не тело — на классификацию не влияет.
            author, sep, body0 = rest.partition(": ")
            if not sep:
                author, body0 = rest.rstrip(":"), ""
            cur = Message(id=m.group(1), date=m.group(2), author=author.strip(), body=body0)
        elif cur:
            cur.body += "\n" + line
        elif line.strip():
            preamble.append(line)
    if cur:
        cur.body = cur.body.rstrip()
        messages.append(cur)
    if preamble:
        # Текст до первого заголовка — не сообщение, но и молча терять его нельзя.
        messages.insert(0, Message(id="?", date="", author="(до первого заголовка)",
                                   body="\n".join(preamble), category="candidate",
                                   tags=["БЕЗ-ЗАГОЛОВКА"]))
    return messages


def classify(msg: Message) -> None:
    """Проставляет категорию и теги. Порядок важен: реклама раньше резюме —
    рекламный пост про курс «обнови резюме» не должен уехать в резюме."""
    body = msg.body
    if RE_AD.search(body):
        msg.category = "ad"
    elif RE_RESUME.search(body) or (RE_FORM_POSITION.search(body) and RE_FORM_EXP.search(body)):
        msg.category = "resume"
    if RE_GO.search(body):
        msg.tags.append("GO")
    if msg.category == "resume":
        msg.tags.append("РЕЗЮМЕ")
    if msg.category == "ad":
        msg.tags.append("РЕКЛАМА")


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def render(text: str, *, since: str | None = None, full: bool = False,
           preview_lines: int = 15) -> tuple[str, dict]:
    """Разбирает дамп и отдаёт (отчёт, счётчики). Счётчики — и для сверки с `grep -c '^\\[#'`."""
    messages = parse_dump(text)
    for m in messages:
        classify(m)

    since_dt = _parse_iso(since) if since else None
    if since and since_dt is None:
        raise ValueError(f"не понимаю дату {since!r}; жду ISO вида 2026-07-28T00:00:00Z")

    def in_window(m: Message) -> bool:
        if not since_dt:
            return True
        dt = _parse_iso(m.date)
        return dt is not None and dt >= since_dt

    window = [m for m in messages if in_window(m)]
    resumes = [m for m in window if m.category == "resume"]
    ads = [m for m in window if m.category == "ad"]
    candidates = [m for m in window if m.category == "candidate"]

    counters = {"total": len(messages), "window": len(window), "resume": len(resumes),
                "ad": len(ads), "candidates": len(candidates)}

    out = []
    out.append(f"Всего сообщений: {counters['total']}"
               + (f" · в окне с {since}: {counters['window']}" if since_dt else ""))
    out.append(f"Вакансии-кандидаты: {counters['candidates']} · "
               f"резюме: {counters['resume']} · реклама: {counters['ad']}")
    if since_dt and counters["total"] > counters["window"]:
        out.append(f"Вне окна (не показаны, но посчитаны): "
                   f"{counters['total'] - counters['window']}")
    out.append("")

    for m in candidates:
        tag_s = "".join(f"[{t}]" for t in m.tags)
        out.append(f"━━ #{m.id} {m.date[:16]} {tag_s} {m.author}")
        lines = [ln for ln in m.body.split("\n")]
        shown = lines if full else lines[:preview_lines]
        out += ["  " + ln for ln in shown]
        if not full and len(lines) > preview_lines:
            out.append(f"  … ещё {len(lines) - preview_lines} строк (--full покажет целиком)")
        out.append("")

    # Резюме и реклама — перечнем id: не выброшены, а сгруппированы.
    out.append("РЕЗЮМЕ (id): " + (", ".join(f"#{m.id}" for m in resumes) or "—"))
    out.append("РЕКЛАМА (id): " + (", ".join(f"#{m.id}" for m in ads) or "—"))
    return "\n".join(out), counters
