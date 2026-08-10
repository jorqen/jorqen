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


def next_steps(res: dict, sl: dict, cookies_from: str | None = None) -> list[str]:
    """Блок «что делать дальше» — то, ради чего команда и существует.

    Не советы вообще, а конкретные команды с подставленными аргументами: модели
    остаётся выбрать, а не сочинять."""
    steps: list[str] = []
    stages = res.get("stages") or {}

    # Разлогин на площадке, без которой сбора нет, стоит выше деградации выдачи:
    # деградация — это «нашлось меньше обычного», а разлогин — «не нашлось ничего,
    # и в следующей волне тоже не найдётся, пока не починишь».
    from . import authrefresh  # noqa: PLC0415
    dead = [r for r in authrefresh.preflight(cookies_from)
            if r["state"] == "anonymous" and r["critical"]]
    if dead:
        steps.append(
            "СНАЧАЛА ВОССТАНОВИ ВХОД — без него площадка не собирается вовсе: "
            + "; ".join(f"{r['platform']} (`scout auth login {r['platform']}`)"
                        for r in dead)
            + ". Пароль и код вводишь только ты.")

    # Деградация источника идёт ПЕРВЫМ пунктом: она означает, что вакансий
    # в этой волне физически меньше, чем могло быть, и любой отбор ниже сделан
    # по неполной выдаче. Читать это надо до, а не после карточек.
    bad = (stages.get("collect", {}) or {}).get("health") or []
    if bad:
        steps.append(
            "СНАЧАЛА ПОЧИНИ ИСТОЧНИКИ — выдача этой волны неполная: "
            + "; ".join(f"{b['label']} {b['source']} ({b['found']})" for b in bad[:6])
            + ". Статус у них «ok»: площадка ответила, но отдала не то, что обычно. "
              "Смотреть `scout raw <площадка>` и `scout auth status`.")

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

    # 🔴 Долги по раскрытию контактов. Лимит hirehi восстанавливается, поэтому
    # «не смогли в прошлый раз» означает «вернуться сейчас», а не «забыть».
    # Напоминает алгоритм, а не память агента (требование владельца 09.08.2026).
    try:
        from . import store  # noqa: PLC0415
        from .reveal import pending_reveals  # noqa: PLC0415
        with store.connect(store.DEFAULT_DB) as conn:
            debts = pending_reveals(conn)
    except Exception:  # noqa: BLE001 — напоминание не должно ронять волну
        debts = []
    if debts:
        steps.append(
            f"ДОЛГИ ПО КОНТАКТАМ: {len(debts)} вакансий ждут раскрытия с прошлых "
            f"волн (напр. {', '.join((d.get('company') or '—')[:18] for d in debts[:3])}). "
            f"Список — `scout pending-reveals`. Сначала проверь живость "
            f"(`scout check-links <url>`), потом трать лимит.")

    # Телеграм-посты: ссылка на пост контактом НЕ является, настоящая спрятана
    # внутри. Раскопка автоматизирована (applyopt зовёт tgpost), но напомнить
    # надо — по такой ссылке не видно, что вакансия давно закрыта.
    tg_rows = [r for r in rows[:40] if "t.me/" in str(r.get("url") or "")]
    if tg_rows:
        steps.append(
            f"Телеграм-постов в топе: {len(tg_rows)}. Ссылка на пост — НЕ контакт: "
            f"настоящая спрятана под словом «Откликнуться» и достаётся сама "
            f"(`applyopt` → `tgpost`). По посту живость не читается вовсе — "
            f"проверь ту ссылку, что внутри: `scout check-links <url из поста>`.")

    steps.append(
        "Отобрал — зафиксируй решение: "
        "`scout mark <источник> <id> --state shortlist|skip --note \"почему\"`, "
        "и найденный канал в кэш: `scout employer set \"<компания>\" <канал>`.")
    steps.append(
        "Карточки и главный документ волны — по формату SKILL.md "
        "(.jobs/<дата>.md + .jobs/<дата>/companies/<компания>/…). "
        "Ссылки в таблице считаются ОТ каталога документа, поэтому начинаются "
        "с даты волны — иначе они не откроются. Проверяет `lint-cards`.")
    steps.append(
        "🔴 ПЕРЕД СДАЧЕЙ: `scout lint-cards .jobs/<дата>` — он проверяет письма "
        "(канон + гейт на чужие ссылки), битые ссылки документа и незакрытые "
        "вопросы. Ноль замечаний — обязательное условие, а не пожелание.")
    return steps


def render_picture(res: dict, sl: dict, *, top: int,
                   cookies_from: str | None = None) -> str:
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

    # Авторизация — сразу под этапами и ДО шорт-листа. Разлогин означает, что
    # часть выдачи не собралась вовсе, то есть любой отбор ниже сделан по
    # неполной картине; прочитать это надо раньше, чем сами вакансии.
    from . import authrefresh  # noqa: PLC0415 — ленивый импорт, как и остальное
    block = authrefresh.preflight_block(cookies_from)
    if block:
        out.append("")
        out.append(block)

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
    for i, step in enumerate(next_steps(res, sl, cookies_from), 1):
        out.append(f"{i}. {step}")
    return "\n".join(out)


def cli(args) -> int:
    started = time.monotonic()
    # 🔴 ДО сбора забираем свежие куки у площадок, чьи токены мы не ротируем.
    # Живой счёт 09.08.2026: у wantapply токен «истёк 14:02» посреди волны, и
    # часть вакансий пришла без прямых ссылок в ATS работодателя — при том, что
    # владелец из аккаунта не выходил, сессия в его браузере была жива, а
    # прогон читал устаревший слепок. Забор безопасен именно у этих площадок:
    # мы куку читаем, а не обмениваем, поэтому живая вкладка не разлогинится.
    from . import authrefresh  # noqa: PLC0415 — ленивый импорт, как и везде
    for platform, ok, why in authrefresh.adopt_safe_sessions(
            getattr(args, "cookies_from", None)):
        if ok:
            print(f"сессия {platform} обновлена из браузера: {why}")
        else:
            print(f"сессия {platform}: свежую куку взять не вышло — {why}",
                  file=sys.stderr)

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

    # `wave` объявляет --cookies-from (cliargs: add_cookie_args), и раньше он
    # до пробы сессий не доезжал: `--cookies-from none` всё равно лез читать
    # настоящие куки, а на macOS — Keychain, тогда как флагом просили
    # обратного.
    print(render_picture(res, sl, top=args.top,
                         cookies_from=getattr(args, 'cookies_from', None)))
    print()
    print(f"_Отчёт скана: {res.get('report_path') or '—'} · "
          f"прогон занял {int(time.monotonic() - started)} с._")
    if args.verbose:
        print("\n<details><summary>вывод scan</summary>\n")
        print(scan_out)
        print("</details>")
    return 0 if res.get("ok", True) else 1
