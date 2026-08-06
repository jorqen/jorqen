"""wave — весь конвейер одной командой и явная передача хода модели.

Задача модуля: сделать пайплайн прозрачным. Скрипт сам знает, что делать по
порядку, и сам это делает; модель не оркестрирует этапы, а получает готовую
картину и решает ровно два вопроса — какую команду звать следующей и когда
остановиться.

Полная картина за два-три вызова:

1. `scout wave` — собрать всё и показать: покрытие, шорт-лист, стены, что уже
   отработано, и блок «СЛЕДУЮЩИЙ ШАГ» с конкретными командами.
2. `scout brief <url…>` — досье по выбранным вакансиям (выжимка, стаж, история,
   канал найма).
3. `scout channel <компания> --site <домен> --save` — прямой канал, если его
   ещё нет в кэше.

Дальше модель пишет карточки. Никаких «агентов-читателей отчёта» в этом
конвейере нет и быть не должно — см. «Экономия» в SKILL.md.
"""

from __future__ import annotations

import io
import sys
import time
from contextlib import redirect_stdout

from . import shortlist, store

# Этапы конвейера в порядке исполнения. Каждый — (ключ, заголовок, обязателен ли).
# Необязательный этап падает молча в статус, а не роняет прогон: половина
# доступов живёт только на машине пользователя, и в облаке их нет.
STAGES = (
    ("collect", "площадки"),
    ("telegram", "Telegram-архив"),
    ("enrich", "выжимки дельты"),
    ("hh", "статусы hh"),
    ("habr_sync", "статусы Хабр Карьеры"),
    ("mail", "статусы почты"),
)


def _run_scan(args) -> dict:
    """Запускает штатный scan и возвращает его результат по этапам."""
    from .cli import run_scan  # noqa: PLC0415 — ленивый импорт: cli тяжёлый
    return run_scan(args)


def next_steps(res: dict, sl: dict) -> list[str]:
    """Блок «что делать дальше» — то, ради чего команда и существует.

    Не советы вообще, а конкретные команды с подставленными аргументами: модели
    остаётся выбрать, а не сочинять."""
    steps: list[str] = []
    stages = res.get("stages") or {}

    # Стены лежат в отчёте collect построчно по источникам — читаем оттуда,
    # а не из выдуманного поля: несуществующий ключ молча дал бы «стен нет».
    walls = [r.get("source") for r in (stages.get("collect", {}).get("report") or [])
             if str(r.get("status", "")).lower() in ("blocked", "антибот")
             or "антибот" in str(r.get("error") or "").lower()]
    if walls:
        steps.append(
            f"Стены на площадках: {', '.join(dict.fromkeys(walls))}. "
            f"Пройти настоящим браузером: `scout render <url площадки>` — "
            f"он ждёт фоновую проверку сам, а интерактивную покажет окном.")

    rows = sl.get("rows") or []
    no_channel = [r for r in rows[:40] if not r.get("_channel") and r.get("company")]
    if no_channel:
        names = ", ".join(dict.fromkeys((r.get("company") or "")[:22]
                                        for r in no_channel[:5]))
        steps.append(
            f"Нет прямого канала найма у {len(no_channel)} компаний "
            f"(напр. {names}). Искать НЕ агентом, а командой: "
            f"`scout channel \"<компания>\" --site <домен> --render --save`.")

    not_enriched = [r for r in rows[:40] if not r.get("_enriched")]
    if not_enriched:
        urls = " ".join(f'"{r["url"]}"' for r in not_enriched[:6])
        steps.append(
            f"Нет выжимки у {len(not_enriched)} строк топа — полный текст и "
            f"требования: `scout brief {urls}`")

    steps.append(
        "Отобрал — зафиксируй решение: "
        "`scout mark <источник> <id> --state shortlist|skip --note \"почему\"`, "
        "и найденный канал в кэш: `scout employer set \"<компания>\" <канал>`.")
    steps.append(
        "Карточки и главный документ волны — по формату SKILL.md "
        "(.jobs/<дата>.md + .jobs/companies/<компания>/…).")
    return steps


def render_picture(res: dict, sl: dict, *, top: int) -> str:
    """Единый экран: покрытие → шорт-лист → следующий шаг."""
    out: list[str] = []
    stages = res.get("stages") or {}

    out.append("# Картина волны")
    out.append("")
    out.append("## Этапы")
    out.append("| этап | статус | найдено | примечание |")
    out.append("|---|---|---|---|")
    for key, label in STAGES:
        st = stages.get(key) or {}
        status = st.get("status", "НЕ ЗАПУСКАЛСЯ")
        found = st.get("found", st.get("candidates", st.get("ok", "—")))
        note = (st.get("note") or st.get("error") or "")[:90]
        out.append(f"| {label} | {status} | {found} | {note} |")

    st = sl.get("stats") or {}
    out.append("")
    out.append(f"## Шорт-лист: {st.get('groups', 0)} вакансий "
               f"(дельта {st.get('delta', 0)}, чужая профессия "
               f"{st.get('off_profile', 0)}, схлопнуто дублей "
               f"{st.get('collapsed', 0)})")
    out.append("")
    rows = (sl.get("rows") or [])[:top]
    out.append("| # | Роль | Компания | Деньги | Стаж | RTW | История | Канал | Ссылка |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for i, g in enumerate(rows, 1):
        out.append(
            f"| {i} | {(g.get('title') or '')[:52].replace('|', '/')} "
            f"| {(g.get('company') or '—')[:24].replace('|', '/')} "
            f"| {shortlist._money(g)} "
            f"| {g.get('_years') if g.get('_years') is not None else ''} "
            f"| {(g.get('_rtw') or '')[:26]} "
            f"| {'; '.join(g.get('_worked') or [])[:26]} "
            f"| {'✓' if g.get('_channel') else ''} | {g.get('url')} |")
    if len(sl.get("rows") or []) > top:
        out.append(f"\n_Показано {top} из {len(sl['rows'])}; "
                   f"вся выдача — `scout shortlist --since <окно>`._")

    out.append("")
    out.append("## СЛЕДУЮЩИЙ ШАГ")
    # Нумеруем при печати: раньше номера были вшиты в текст, и пропуск любого
    # шага давал дыру в нумерации («1, 2, 4, 5») — читается как потерянный пункт.
    for i, step in enumerate(next_steps(res, sl), 1):
        out.append(f"{i}. {step}")
    return "\n".join(out)


def cli(args) -> int:
    started = time.monotonic()
    # scan печатает много и по делу, но в «картину» его вывод не идёт: он уже
    # лежит в файле отчёта. Здесь нас интересует только структура результата.
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            res = _run_scan(args)
    except BaseException:
        # Буфер обязан долететь до пользователя, даже если конвейер упал:
        # иначе он видит трейсбек без единой строки о том, где именно оборвалось,
        # — а этапы печатают ровно эту информацию.
        sys.stdout.write(buf.getvalue())
        sys.stdout.flush()
        raise
    scan_out = buf.getvalue()

    since = store.since_arg(f"{args.days}d")
    sl = shortlist.build(args.db, since=since, by="seen", sources=None,
                         limit=0)

    print(render_picture(res, sl, top=args.top))
    print()
    print(f"_Отчёт скана: {res.get('report_path') or '—'} · "
          f"прогон занял {int(time.monotonic() - started)} с._")
    if args.verbose:
        print("\n<details><summary>вывод scan</summary>\n")
        print(scan_out)
        print("</details>")
    return 0 if res.get("ok", True) else 1
