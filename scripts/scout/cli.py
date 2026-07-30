"""CLI сборщика.

    python3 -m scripts.scout collect            # обойти все площадки, сложить в базу
    python3 -m scripts.scout new --since 3d     # что появилось с прошлого раза
    python3 -m scripts.scout coverage           # кто отработал, кто упал — за последний прогон
    python3 -m scripts.scout resolve <url>      # куда на самом деле ведёт «Откликнуться»
    python3 -m scripts.scout detail <url>       # выжимка страницы вакансии чистым текстом
    python3 -m scripts.scout enrich --since 3d  # выжимки по всей дельте, с хранением
    python3 -m scripts.scout ats check <token>  # живость токена на всех ATS сразу
    python3 -m scripts.scout ats jobs gh:tok    # вакансии доски со структурным матчем локаций
    python3 -m scripts.scout ats sniff <url>    # на каком ATS сидит компания
    python3 -m scripts.scout check-links <url>  # предфлайт живости ATS-ссылок
    python3 -m scripts.scout tg <файл>          # разбор телеграм-дампа
    python3 -m scripts.scout tg-auth login      # сессия Telegram (вход делает пользователь)
    python3 -m scripts.scout tg-fetch           # архив Telegram → дампы → парсер tg
    python3 -m scripts.scout render <url>       # SPA-страница через браузер
    python3 -m scripts.scout browse <url> --keep # видимое окно с куками пользователя
    python3 -m scripts.scout auth status         # чем СЕЙЧАС закрыт вход по площадкам
    python3 -m scripts.scout hh-sync            # отказы/приглашения из кабинета hh
    python3 -m scripts.scout mail-sync          # статусы из почты (IMAP, только чтение)
    python3 -m scripts.scout mail-ingest f.json # статусы из JSON-выгрузки писем (MCP)
    python3 -m scripts.scout scan               # ВЕСЬ конвейер → .scout/reports/<дата>.md
    python3 -m scripts.scout raw <источник>     # страница для источников без парсера
    python3 -m scripts.scout mark <src> <id> --state applied
    python3 -m scripts.scout status --query t   # что решено по вакансии (title/company)

Главная идея: скрипт делает механику (сходить на пятнадцать площадок, распаковать,
сложить, посчитать дельту), модель — суждение (что подходит, кому писать, каким текстом).
Так обход перестаёт упираться в контекст: в модель приезжает таблица на сотню строк,
а не полтора мегабайта HTML.

Второе правило, из которого следует половина кода ниже: **тихая потеря хуже падения**.
Усечение выдачи, пустой дамп SPA, анонимный вид залогиненной площадки, письмо без
статуса — всё это выглядит успехом и не оставляет следа. Поэтому счётчики печатают
«показано N из M», статусы называются вслух, а команды возвращают ненулевой код там,
где раньше молча возвращали ноль.

Браузерные команды (render, browse, hh-sync, detail --render, scan) берут куки из
БРАУЗЕРА пользователя (`--cookies-from`, см. cookiesrc.py), а не из файла-кэша.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone

from . import store
from .model import Vacancy
from .net import BlockedError, FetchError, parallel
from .sources import (ATS_ROLE_RE, LOGIN_VALUE, NEEDS_BROWSER_SET, NEEDS_LOGIN,
                      RAW_SOURCES, SOURCE_NOTES, SOURCES, Ctx, EmptyDumpError,
                      raw_dump)


# ──────────────────────────────────────────────────────────────────────────────
# Потолки прогона
# ──────────────────────────────────────────────────────────────────────────────
#
# Лимит — ПРЕДОХРАНИТЕЛЬ ОТ БЕСКОНЕЧНОСТИ, а не рабочий режим. Умолчание обязано
# забирать всё, что есть в окне свежести; упереться в круглое число и замолчать —
# это тихая потеря, то есть худшее, что этот скрипт умеет делать.
#
# Чем это было: `--limit 100` резал careered на 100 из 1797 (взято 6% ленты),
# himalayas на 13 из 50, arbeitnow на 32 из 78, eures на 25 из 53, jobsdb на 5
# из 14, hackoffer на 100 из 117. Итого 3536 вакансий вместо 5365 — и ни одной
# строки о том, что выдача обрезана.
#
# Почему 400, а не «побольше». Лимит здесь НЕ режет: адаптеры считают глубину
# как `max(лимит, свой проверенный пол)` — `sources._page_budget`,
# `sources_web.row_budget`. То есть `--limit` умеет только ПОДНЯТЬ потолок выше
# того, что площадка проверенно отдаёт в окне, и никогда не опустить. Умолчание
# обязано не мешать этим полам, а не соревноваться с ними.
#
# 400 — минимальное значение, которое ничего не режет у источников со СТАРОЙ
# арифметикой (`min(ctx.limit, 400) // страница` у himalayas и arbeitnow,
# `min(max(ctx.limit, 20), 100)` у hnhiring): им нужно ровно 400, чтобы дойти
# до собственного потолка. Всё, что больше, у остальных площадок только
# поднимает потолок глубины сверх проверенного — а у LinkedIn, который троттлит
# охотнее всех и держит паузу 1.2s на страницу, это прямая дорога в бан.
DEFAULT_LIMIT = 400

# `--limit 0` = «сколько отдаёт площадка». Не None и не бесконечность: значение
# уезжает в арифметику адаптеров (`min(ctx.limit, 400) // 20`, `ceil(limit/стр.)`),
# где None взорвётся TypeError, а миллион превратится в сто тысяч страниц.
# 20 000 строк с одного источника — заведомо больше любой известной ленты
# (самая длинная, careered, — 1797) и всё ещё конечное число.
NO_LIMIT = 20_000


def limit_value(raw: int | None) -> int:
    """`--limit` из аргументов → число для Ctx. 0 и отрицательное = без потолка."""
    if raw is None:
        return DEFAULT_LIMIT
    return NO_LIMIT if raw <= 0 else raw


# ──────────────────────────────────────────────────────────────────────────────
# Пометки источников
# ──────────────────────────────────────────────────────────────────────────────
#
# Пометка в строке покрытия — это обещание пользователю. Соврать в ней хуже,
# чем промолчать: «--days применяется приблизительно» читается как «окно всё-таки
# работает», человек ставит --days 1 и уверен, что видит вчерашнее.
#
# Здесь пометка только берётся из реестра источника. Чинить враньё нужно ПО МЕСТУ,
# рядом с адаптером (`sources*.py`), иначе поправка становится второй правдой и
# расходится с кодом, который её породил. Контракт «пометка обещает окно ⇒ адаптер
# читает `ctx.days`» держит `test_source_notes_do_not_lie` в test_e2e.py.
#
# Так и вышло с geekjob: пометка обещала окно, хотя `src_geekjob` не читает
# `ctx.days` ни разу (живой замер: одинаковая выдача при `--days 1` и `--days 120`).
# Временная поправка жила здесь и самоликвидировалась, когда
# `sources_auth.GEEKJOB_DAYS_NOTE` починили по месту.


def source_note(name: str) -> str | None:
    """Пометка источника для строки покрытия."""
    return SOURCE_NOTES.get(name)


# ──────────────────────────────────────────────────────────────────────────────
# Вежливость к площадкам
# ──────────────────────────────────────────────────────────────────────────────


class HostPacer:
    """Минимальный зазор между запросами К ОДНОМУ хосту.

    Нужен там, где мы ходим не по разу за прогон, а сотнями — то есть в enrich.
    Пул на восемь потоков без зазора выдаёт восемь одновременных запросов на один
    домен, и это ровно тот шаблон, за который rabota.ru закрыла нам TLS после
    ~25 запросов за 20 минут. Это была наша вина, а не её.

    Считает по ХОСТУ, а не глобально: 300 вакансий jobgether и 50 вакансий hh
    не должны стоять в одной очереди — они мешают разным серверам.
    """

    def __init__(self, gap: float = 0.7):
        self.gap = gap
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str) -> float:
        """Спит столько, сколько нужно этому хосту. Возвращает длительность сна."""
        if self.gap <= 0:
            return 0.0
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        with self._lock:
            now = time.monotonic()
            due = self._next.get(host, 0.0)
            start = max(now, due)
            # Слот занимается ДО сна и под тем же локом: иначе два потока увидят
            # один и тот же «свободно сейчас» и уйдут на хост одновременно.
            self._next[host] = start + self.gap
        delay = start - now
        if delay > 0:
            time.sleep(delay)
        return max(0.0, delay)


# ──────────────────────────────────────────────────────────────────────────────
# collect
# ──────────────────────────────────────────────────────────────────────────────


class _Skipped(RuntimeError):
    """Площадку не запускали (флаг прогона). Это НЕ ноль вакансий и НЕ ошибка.

    Отдельный тип, потому что «не проверяли» обязано выглядеть в отчёте иначе,
    чем «проверили и не нашли»: иначе прогон с выключенным браузером читается
    как полный обход, а две площадки из него молча выпали.
    """


# Слова, которыми адаптер сам заявляет «я остановился раньше, чем кончилась
# выдача»: `sources._truncated_note`, `sources_web._cut_note`, потолок страниц
# LinkedIn, потолок страниц geekjob.
#
# Признак берётся из сводки САМОГО источника, а не вычисляется здесь по
# «найдено >= лимита». Считать снаружи больше нельзя и не нужно: глубину теперь
# задаёт `max(лимит, пол площадки)`, поэтому careered при `--limit 400` отдаёт
# 1797 строк, и любая внешняя арифметика объявила бы это обрезанием. Соврать
# про обрезание ровно так же плохо, как промолчать о нём.
_CUT_MARKERS = ("обрезан", "остановились на потолке")


def _limit_hit(vacancies: list, found: int, limit: int) -> str:
    """Заявление источника об обрезании обхода, если оно было; иначе пустая строка."""
    for v in vacancies:
        if v.external_id != "_summary":
            continue
        for note in (v.raw or {}).get("notes") or []:
            low = str(note).lower()
            if any(m in low for m in _CUT_MARKERS):
                return str(note)
    return ""


def run_collect(ctx: Ctx, names: list[str], *, workers: int = 8,
                db: str = store.DEFAULT_DB, no_store: bool = False,
                no_browser: bool = False, args_dict: dict | None = None) -> dict:
    """Ядро collect, отделённое от печати: им пользуется и `collect`, и `scan`.
    Возвращает {report, vacancies, new, updated, elapsed}."""
    started = time.time()
    timings: dict[str, int] = {}

    def wrap(name):
        def run():
            t0 = time.time()
            try:
                return SOURCES[name](ctx)
            finally:
                timings[name] = int((time.time() - t0) * 1000)
        return run

    # Прогон открывается ДО обхода, а не после: раньше start_run стоял внутри
    # блока записи, и журнал фиксировал не обход, а вставку в базу — в `coverage`
    # 81-секундный прогон выглядел 28-миллисекундным.
    run_id = None
    if not no_store:
        with store.connect(db) as conn:
            run_id = store.start_run(conn, ctx.query, args_dict or {})

    # Браузерные источники идут ОТДЕЛЬНО и по одному. Профиль браузера один и он
    # под локом: два параллельных захода дают ProfileBusy у второго, и в покрытии
    # появляется «УПАЛ» у площадки, которая не падала. Это ровно тот ложный статус,
    # из-за которого потом чинят работающее и не чинят сломанное.
    browser = [n for n in names if n in NEEDS_BROWSER_SET]
    plain = [n for n in names if n not in NEEDS_BROWSER_SET]

    results = parallel({n: wrap(n) for n in plain}, workers=workers)
    if no_browser:
        for name in browser:
            results[name] = (False, _Skipped(
                f"пропущен флагом --no-browser (нужен браузер: "
                f"{NEEDS_BROWSER_SET.get(name)})"))
    else:
        for name in browser:
            results.update(parallel({name: wrap(name)}, workers=1))

    all_vacancies: list[Vacancy] = []
    report: list[dict] = []
    for name in names:
        ok, payload = results.get(name, (False, RuntimeError("не запускался")))
        if ok:
            all_vacancies.extend(payload)
            found = sum(1 for v in payload if v.external_id != "_summary")
            report.append({"source": name, "status": "ok", "found": found,
                           "elapsed_ms": timings.get(name, 0), "error": None,
                           "limit_hit": _limit_hit(payload, found, ctx.limit),
                           "note": source_note(name)})
        else:
            # Четыре исхода, и путать их нельзя:
            #   blocked  — антибот-стена, чинится заходом человека, а не кодом;
            #   no_login — нет сессии пользователя, чинится одним входом;
            #   skipped  — не запускали сами (флаг), площадка НЕ проверена;
            #   error    — сломалось, обход неполный.
            # Свести их в «упал» значит гонять человека чинить код там, где надо
            # залогиниться, и наоборот — молчать о настоящей поломке.
            from .sources_auth import NeedsLogin  # noqa: PLC0415 — цикл импорта

            if isinstance(payload, BlockedError):
                status = "blocked"
            elif isinstance(payload, NeedsLogin):
                status = "no_login"
            elif isinstance(payload, _Skipped):
                status = "skipped"
            else:
                status = "error"
            report.append({"source": name, "status": status, "found": 0,
                           "elapsed_ms": timings.get(name, 0), "error": str(payload),
                           "limit_hit": "", "note": source_note(name)})

    new = updated = 0
    if not no_store and run_id is not None:
        with store.connect(db) as conn:
            new, updated = store.upsert(conn, all_vacancies)
            for r in report:
                store.record_source(conn, run_id, r["source"], r["status"],
                                    found=r["found"], error=r["error"],
                                    elapsed_ms=r["elapsed_ms"])
            store.finish_run(conn, run_id)

    # Служебная сводка ATS — не вакансия: в «найдено» она попадать не должна.
    total = sum(1 for v in all_vacancies if v.external_id != "_summary")
    return {"report": report, "vacancies": all_vacancies, "new": new,
            "updated": updated, "elapsed": time.time() - started, "total": total,
            "limit": ctx.limit}


def cmd_collect(args) -> int:
    ctx = Ctx(query=args.query, extra_queries=tuple(args.also or ()), days=args.days,
              area=args.area, limit=limit_value(args.limit),
              include_foreign=not args.ru_only, ats_all=args.ats_all)
    names = args.sources.split(",") if args.sources else list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        print(f"неизвестные источники: {', '.join(unknown)}", file=sys.stderr)
        print(f"доступны: {', '.join(SOURCES)}", file=sys.stderr)
        return 2

    res = run_collect(ctx, names, workers=args.workers, db=args.db,
                      no_store=args.no_store,
                      no_browser=getattr(args, "no_browser", False),
                      args_dict=vars(args))
    report, all_vacancies = res["report"], res["vacancies"]
    total = res["total"]

    # Заблокированное антиботом в счёт провалов не идёт: прогон отработал как задумано,
    # а стену снимает человек. Иначе вызывающий видит «упало» на каждом запуске
    # и перестаёт различать реальную поломку и обычную капчу.
    failed = [r for r in report if r["status"] == "error"]
    if args.format == "json":
        print(json.dumps({
            "query": ctx.query, "days": ctx.days, "coverage": report,
            "limit": ctx.limit, "limit_unbounded": ctx.limit >= NO_LIMIT,
            "truncated_sources": {r["source"]: r["limit_hit"] for r in report
                                  if r.get("limit_hit")},
            "found": total, "new": res["new"], "updated": res["updated"],
            "needs_login": {n: LOGIN_VALUE.get(n) for n in NEEDS_LOGIN},
            "raw_sources": {n: c.get("parser") for n, c in RAW_SOURCES.items()},
            "needs_browser": dict(NEEDS_BROWSER_SET),
            "vacancies": [v.to_dict() for v in all_vacancies if v.external_id != "_summary"]
                         if args.with_items else [],
        }, ensure_ascii=False, indent=2))
    else:
        _print_coverage(report, total, res["new"], res["updated"], res["elapsed"],
                        subset=bool(args.sources), limit=ctx.limit)

    # Ненулевой код — чтобы упавшая площадка была видна вызывающему, а не утонула в выводе.
    return 1 if failed else 0


_MARK = {"ok": "ok", "blocked": "АНТИБОТ", "error": "УПАЛ", "skipped": "ПРОПУЩЕН",
         "no_login": "НУЖЕН ВХОД"}


def _limit_lines(report, limit: int | None) -> list[str]:
    """Строки об обрезании по лимиту. Пусто — значит ничего не обрезано.

    Отдельной функцией, потому что одно и то же надо сказать дважды: человеку
    в терминал и модели в markdown-отчёт. Разойдись эти два места — и отчёт
    начнёт выглядеть полнее, чем прогон был на самом деле.
    """
    hits = [r for r in report if r.get("limit_hit")]
    if not hits:
        return []
    out = [f"⚠️  ОБХОД ОБРЕЗАН ПОТОЛКОМ (--limit {limit}). Это НЕ «столько и было»: "
           f"площадка отдавала дальше, мы остановились сами."]
    out += [f"    · {r['source']}: {r['limit_hit']}" for r in hits]
    out.append("    Поднять потолок: `--limit <больше>` или `--limit 0`. "
               "Лимит — предохранитель от бесконечности, а не рабочий режим.")
    return out


def _print_coverage(report, total, new, updated, elapsed, *, subset: bool = False,
                    limit: int | None = None) -> None:
    print(f"\n## Покрытие прогона ({elapsed:.1f}s)\n")
    print(f"{'источник':<14} {'статус':<9} {'найдено':>8} {'время':>7}  примечание")
    print("-" * 88)
    for r in sorted(report, key=lambda x: (x["status"] == "ok", x["source"])):
        ms = r.get("elapsed_ms") or 0
        tail = (r["error"] or r.get("note") or "")[:42]
        if r.get("limit_hit"):
            tail = "ОБХОД ОБРЕЗАН ПОТОЛКОМ; " + tail
        print(f"{r['source']:<14} {_MARK.get(r['status'], r['status']):<9} {r['found']:>8} "
              f"{ms / 1000:>6.1f}s  {tail[:60]}")
    print("-" * 88)
    print(f"{'ИТОГО':<14} {'':<9} {total:>8}  новых: {new}, обновлено: {updated}")
    for line in _limit_lines(report, limit):
        print(line)

    # `raw` больше не способ собирать площадку — у всех семи есть парсер, и все семь
    # стоят в таблице выше. Строка осталась потому, что отладочный дамп («что там
    # на самом деле отдали») нужен ровно в тот момент, когда парсер принёс не то.
    debug = [f"{n} → парсер {c['parser']}" for n, c in RAW_SOURCES.items() if c.get("parser")]
    if debug:
        print(f"\nОтладочный дамп страницы (`scout raw <площадка>`): {'; '.join(debug)}")
    orphan = [n for n, c in RAW_SOURCES.items() if not c.get("parser")]
    if orphan:
        print(f"Без парсера (забирать `raw`, разбирать глазами): {', '.join(orphan)}")

    # Вход просим только там, где он что-то меняет, и говорим ЧТО именно:
    # «требует входа» без пользы читается как «без входа ноль» — и человека
    # гоняют логиниться на площадку, которая и так отдаёт всё.
    for name in NEEDS_LOGIN:
        print(f"Вход пользователя ({name}): {LOGIN_VALUE.get(name, 'не описано')}")

    blocked = [r["source"] for r in report if r["status"] == "blocked"]
    failed = [r["source"] for r in report if r["status"] == "error"]
    skipped = [r["source"] for r in report if r["status"] == "skipped"]
    no_login = [r["source"] for r in report if r["status"] == "no_login"]
    missing = sorted(set(SOURCES) - {r["source"] for r in report})
    if no_login:
        print(f"\n🔑 НУЖЕН ТВОЙ ВХОД: {', '.join(no_login)} — "
              f"`python3 -m scripts.scout auth login {no_login[0]}`. "
              f"Пароль и код вводишь только ты; сборщик за тебя не логинится.")
    if blocked:
        print(f"\n🔒 АНТИБОТ-ПРОВЕРКА: {', '.join(blocked)}. Это не поломка и не чинится "
              f"кодом — проверку проходит человек.\n   Зайди на площадку в браузере "
              f"сам, потом `scout auth login <площадка>`, и сессия переживёт перезапуск.")
    if skipped:
        print(f"\n⏭  НЕ ПРОВЕРЯЛИСЬ: {', '.join(skipped)} — это не «ноль вакансий», "
              f"а «не смотрели». Для них нужен браузер.")
    if failed:
        print(f"\n⚠️  НЕ ОТРАБОТАЛИ: {', '.join(failed)} — обход неполный, "
              f"это надо сказать в отчёте, а не замолчать.")
    if missing and subset:
        print(f"\nВне выборки --sources: {', '.join(missing)}.")
    elif missing:
        # Площадка, которой нет в таблице, — самая дорогая потеря: её отсутствие
        # ничем не видно, и отчёт выглядит полным. Поэтому пишется отдельно
        # от «упал» и от «пропущен».
        print(f"\n⚠️  НЕ ОБХОДИЛИСЬ ВОВСЕ: {', '.join(missing)} — площадки есть "
              f"в реестре, но в этом прогоне их не спрашивали.")


# ──────────────────────────────────────────────────────────────────────────────
# new / coverage
# ──────────────────────────────────────────────────────────────────────────────

def cmd_new(args) -> int:
    since = store.since_arg(args.since)
    kw = dict(since=since if args.by == "published" else None,
              first_seen_since=since if args.by == "seen" else None,
              sources=args.sources.split(",") if args.sources else None,
              exclude_decided=not args.include_decided)
    with store.connect(args.db) as conn:
        # COUNT без limit — иначе «показано» неотличимо от «всего», и вызывающий
        # уверен, что видит всю дельту. Живьём: заголовок «200 вакансий» при 1505
        # в окне, то есть 1305 потерянных вакансий без единого предупреждения.
        total = store.count(conn, **kw)
        rows = store.query(conn, limit=args.limit, **kw)
    truncated = max(0, total - len(rows))

    if args.format == "json":
        # JSON — объект с метаданными, а не голый массив: в массиве усечение
        # выразить нечем, и потребитель его не заметит.
        print(json.dumps({"since": since, "total": total, "shown": len(rows),
                          "truncated": truncated, "limit": args.limit,
                          "items": rows}, ensure_ascii=False, indent=2, default=str))
        return 0
    if not rows:
        print("Ничего нового в окне.")
        return 0

    head = f"# Новое с {since} — всего {total}"
    if truncated:
        head += f", показано {len(rows)} (обрезано по --limit {args.limit}, " \
                f"за кадром {truncated})"
    print(head + "\n")
    print("| # | роль | компания | деньги | локация | дата | источник | ссылка |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        v = Vacancy(source=r["source"], external_id=r["external_id"], url=r["url"],
                    title=r["title"], company=r["company"], salary_from=r["salary_from"],
                    salary_to=r["salary_to"], currency=r["currency"],
                    salary_gross=bool(r["salary_gross"]) if r["salary_gross"] is not None else None,
                    salary_period=r.get("salary_period"))
        # У habr на карточке дата поднятия, а не публикации — она лежит в updated_at.
        pub = ((r["published_at"] or r["updated_at"]) or "")[:10]
        loc = (r["location"] or "")[:24]
        money = v.salary_str() or "—"
        print(f"| {i} | {r['title'][:60]} | {(r['company'] or '—')[:28]} | {money} | "
              f"{loc or '—'} | {pub or '—'} | {r['source']} | {r['url']} |")

    no_salary = sum(1 for r in rows if r["salary_from"] is None and r["salary_to"] is None)
    no_period = sum(1 for r in rows
                    if (r["salary_from"] or r["salary_to"]) and not r.get("salary_period"))
    print(f"\nБез вилки: {no_salary} из {len(rows)}. "
          f"Отсутствие вилки и маленькая вилка — не причина пропускать вакансию: "
          f"вилка выносится в карточку фактом, решает пользователь.")
    print(f"Деньги читаются только с периодом: /час, /мес, /год. Без суффикса "
          f"({no_period} из {len(rows) - no_salary} вилок) — период площадка не назвала, "
          f"и месяц по умолчанию тут НЕ подразумевается.")
    if truncated:
        print(f"\n⚠️  ПОКАЗАНА НЕ ВСЯ ДЕЛЬТА: {len(rows)} из {total}. "
              f"Остальное — `--limit {total}` или `--limit 0` (без ограничения).")
    return 1 if truncated and args.strict else 0


def cmd_coverage(args) -> int:
    with store.connect(args.db) as conn:
        run = store.last_run(conn)
        st = store.stats(conn)
    if not run:
        print("Прогонов ещё не было.")
        return 0
    dur = ""
    try:
        if run["finished_at"]:
            delta = (datetime.fromisoformat(run["finished_at"])
                     - datetime.fromisoformat(run["started_at"])).total_seconds()
            dur = f" ({delta:.1f}s)"
    except (TypeError, ValueError):
        pass
    print(f"Последний прогон #{run['id']}: {run['started_at']} → "
          f"{run['finished_at'] or 'НЕ ЗАВЕРШЁН'}{dur}")
    print(f"Запрос: {run['query']}\n")
    # elapsed_ms по источникам в базе лежал всегда, но не печатался — а именно он
    # отвечает на вопрос «кто тормозит обход».
    print(f"{'источник':<14} {'статус':<8} {'найдено':>8} {'время':>7}  ошибка")
    print("-" * 88)
    for s in run["sources"]:
        print(f"{s['source']:<14} {s['status']:<8} {s['found']:>8} "
              f"{(s['elapsed_ms'] or 0) / 1000:>6.1f}s  {(s['error'] or '')[:44]}")
    print("-" * 88)
    print(f"\nВ базе: {st['vacancies']} вакансий из {st['sources']} источников, "
          f"решений принято: {st['decided']}, прогонов: {st['runs']}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# resolve / raw / mark
# ──────────────────────────────────────────────────────────────────────────────

def cmd_resolve(args) -> int:
    from .resolve import resolve
    try:
        res = resolve(args.url, follow_redirects=not args.no_follow)
    except Exception as e:  # noqa: BLE001
        print(f"не удалось разобрать страницу: {e}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print(f"Страница: {res['page']}\n")
    print(f"ВЕРДИКТ: {res['verdict']}\n")
    if res["best"]:
        b = res["best"]
        print(f"Лучший путь [{b['kind']}{'/' + b['label'] if b.get('label') else ''}]: {b['url']}")
        if b.get("note"):
            print(f"  {b['note']}")
        print()
    print("Все кандидаты:")
    for t in res["targets"]:
        flag = "" if t["safe_to_open"] else "  ⛔ НЕ НАЖИМАТЬ"
        print(f"  [{t['kind']:<12}] {t['url'] or '—'}{flag}")
        if t.get("label"):
            print(f"      текст: {t['label'][:70]}")
        if t.get("note"):
            print(f"      {t['note'][:100]}")
    return 0


def cmd_raw(args) -> int:
    ctx = Ctx(query=args.query, days=args.days, area=args.area)
    if args.source not in RAW_SOURCES:
        print(f"нет такого сырьевого источника; есть: {', '.join(RAW_SOURCES)}", file=sys.stderr)
        return 2
    # Раньше здесь не было обработки вовсе: антибот-стена вылетала тридцатью
    # строками Python-трейсбека вместо статуса, который есть у всех остальных команд.
    try:
        text, url = raw_dump(args.source, ctx, use_render=args.render)
    except EmptyDumpError as e:
        print(f"ПУСТО: {e}", file=sys.stderr)
        return 1
    except BlockedError as e:
        print(f"АНТИБОТ: {e} — стену снимает человек, зайди браузером сам, "
              f"потом `scout auth login {args.source}`.", file=sys.stderr)
        return 1
    except FetchError as e:
        print(f"не отдалась: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — один источник не роняет вызывающего трейсбеком
        print(f"не отдалась: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    note = RAW_SOURCES[args.source].get("note")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{url} → {args.out} ({len(text)} символов)")
    else:
        sys.stdout.write(text)
    if note:
        print(f"примечание: {note}", file=sys.stderr)
    return 0


def cmd_auth(args) -> int:
    from . import auth
    if args.action == "import":
        from . import cookieimport
        return cookieimport.cli(from_=args.from_, domains=args.domains,
                                list_only=args.list)
    if args.action == "login":
        if args.all:
            return auth.login_all()
        if not args.platform:
            print(f"укажи площадку: {', '.join(auth.PLATFORMS)}  "
                  f"(или `--all` — вход разом на все)", file=sys.stderr)
            return 2
        return auth.login(args.platform)
    if args.action == "check":
        return auth.check([args.platform] if args.platform else None)
    if args.action == "secure":
        fixed = auth.secure_auth_dir()
        print("\n".join(f"  починено: {f}" for f in fixed)
              if fixed else "  права уже 0600 у всего содержимого .auth/")
        return 0
    return auth.status()


def cmd_mark(args) -> int:
    # `skip` — короткая форма `skipped`: в базе одно каноническое значение,
    # иначе через месяц в decision живут оба и фильтры видят половину.
    state = {"skip": "skipped"}.get(args.state, args.state)
    with store.connect(args.db) as conn:
        known = store.vacancy_exists(conn, args.source, args.id)
        store.decide(conn, args.source, args.id, state, args.note)
    print(f"{args.source}:{args.id} → {state}" + (f" ({args.note})" if args.note else ""))
    if not known:
        # Решение всё равно записано (вакансия могла выпасть из базы), но молчать
        # нельзя: store.search джойнит ОТ vacancy, и по опечатке в id решение
        # не всплывёт больше нигде и никогда.
        print(f"⚠️  вакансии {args.source}:{args.id} в базе НЕТ — решение записано, "
              f"но в `status` оно не появится. Опечатка в id?", file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    """Что известно по подстроке: и вакансии с нашими решениями, и ОТВЕТЫ работодателей.

    Второй блок появился потому, что команда, созданная ради вопроса «сюда уже
    отказали?», в таблицу с отказами не смотрела вовсе: 79 отказов из кабинета hh
    и все почтовые статусы были через неё недостижимы."""
    with store.connect(args.db) as conn:
        rows = store.search(conn, args.query, limit=args.limit)
        negs = store.search_negotiations(conn, args.query, limit=args.limit)
    if not rows and not negs:
        print(f"По подстроке {args.query!r} в базе ничего нет "
              f"(искал в вакансиях и в статусах откликов).")
        return 0

    if rows:
        print(f"# Вакансии: {len(rows)} по {args.query!r}\n")
        for r in rows:
            state = r["decision"] or "—"
            note = f" · {r['decision_note']}" if r["decision_note"] else ""
            # Деньги здесь печатаются с периодом: строка «19–23 USD» без «/час»
            # в команде, которая отвечает на вопрос «что мы знаем про эту вакансию»,
            # — это ответ, вводящий в заблуждение.
            print(f"[{state:<9}] {r['source']}:{r['external_id']}  {r['title'][:60]} — "
                  f"{(r['company'] or '?')[:30]} · {_money(r)}{note}")
            print(f"            {r['url']}")
        decided = [r for r in rows if r["decision"]]
        if decided:
            print(f"\nС нашим решением: {len(decided)} из {len(rows)}.")

    print(f"\n# Ответы работодателей (hh-кабинет + почта): {len(negs)}\n")
    if not negs:
        print("  статусов по этой подстроке нет — значит, сюда ещё не откликались "
              "(или ответ не пришёл)")
    for n in negs:
        mark = "🔴" if n["status"] == "rejection" else (
            "🟢" if n["status"] in ("invitation", "interview") else "·")
        print(f"{mark} [{n['status']:<10}] {(n['title'] or '')[:60]} — "
              f"{(n['company'] or '?')[:30]}  ({(n['event_at'] or '—')[:10]}, "
              f"{n['source']})")
        if n.get("url"):
            print(f"            {n['url']}")
    hard = [n for n in negs if n["status"] == "rejection"]
    if hard:
        print(f"\n⚠️  ОТКАЗЫ: {len(hard)} — перед новым откликом проверь, "
              f"не туда ли уже писали.")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# ats: check / jobs / sniff (порт бывших scripts/ats/*.sh)
# ──────────────────────────────────────────────────────────────────────────────

def _split_board_spec(spec: str) -> tuple[str, str]:
    from .atsapi import ATS_ALIASES, BOARD_IMPL
    if ":" not in spec:
        raise ValueError(f"жду <ats>:<token>, получил {spec!r}")
    ats, token = spec.split(":", 1)
    ats = ATS_ALIASES.get(ats, ats)
    if ats not in BOARD_IMPL:
        raise ValueError(f"неизвестный ATS {ats!r}; знаю: {', '.join(BOARD_IMPL)}")
    return ats, token


def cmd_ats_check(args) -> int:
    """Живость токена на всех ATS сразу. Название компании печатается обязательно:
    совпавший токен — ещё не та компания (greenhouse `insider` = Business Insider)."""
    from .atsapi import ATS_KINDS, board
    from .net import parallel

    exit_code = 0
    for token in args.tokens:
        results = parallel(
            {k: (lambda kk=k, t=token: board(kk, t)) for k in ATS_KINDS}, workers=7)
        print(f"\n## токен «{token}»")
        print(f"{'ATS':<16} {'статус':<8} {'вакансий':>8}  компания / примечание")
        print("-" * 78)
        found_alive = False
        for kind in ATS_KINDS:
            ok, payload = results[kind]
            if ok:
                b = payload
                status = "ЖИВОЙ" if b.total else "ПУСТО"
                # SmartRecruiters отвечает 200 с totalFound=0 на ЛЮБОЙ токен —
                # такой ответ живость не подтверждает. Раньше он выставлял
                # found_alive, и `ats check` возвращал 0 на заведомо несуществующей
                # компании, то есть был непригоден как проверка в скрипте.
                if b.total:
                    found_alive = True
                note = " · ".join(x for x in (b.company, b.note) if x) or "—"
                print(f"{kind:<16} {status:<8} {b.total:>8}  {note[:60]}")
            else:
                status = "АНТИБОТ" if isinstance(payload, BlockedError) else "НЕТ"
                print(f"{kind:<16} {status:<8} {'—':>8}  {str(payload)[:60]}")
        if not found_alive:
            exit_code = 1
            print(f"\nНи одна доска не подтвердила токен «{token}» вакансиями. "
                  f"«ПУСТО 0» у smartrecruiters от «доски нет» неотличимо.")
    print("\nЛовушки токенов и реестр проверенных досок — "
          ".claude/skills/jobs/references/sources-setup.md")
    return exit_code


def cmd_ats_jobs(args) -> int:
    from .atsapi import board, country_matcher, job_matches_country
    try:
        ats, token = _split_board_spec(args.board)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    try:
        # workable отдаёт большие доски только по запросу — прокидываем grep как текст.
        query = re.sub(r"[^\w\s-]", " ", args.grep).strip() if (
            ats == "workable" and args.grep) else None
        b = board(ats, token, query=query)
    except BlockedError as e:
        print(f"АНТИБОТ: {e}. Это не поломка — зайди на площадку браузером сам.")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"доска не отдалась: {e}", file=sys.stderr)
        return 1

    jobs = b.jobs
    by_country, pat = None, None
    if args.country:
        pat = country_matcher(args.country)
        jobs = [j for j in jobs if job_matches_country(j, pat)]
        by_country = len(jobs)
    if args.grep:
        try:
            rx = re.compile(args.grep, re.I)
        except re.error as e:
            print(f"кривая регулярка --grep: {e}", file=sys.stderr)
            return 2
        jobs = [j for j in jobs if rx.search(j.title)]

    print(f"# {ats}:{token} — {b.company or 'название компании API не отдал'}")
    # У workable с ?query= счётчик «всего» — уже по запросу, а не по всей доске.
    # Сказать это обязательно: иначе «всего 30» читается как размер доски.
    line = (f"всего по запросу «{query}»: {b.total} (вся доска может быть больше)"
            if query else f"всего {b.total}")
    if by_country is not None:
        line += f" / по стране {args.country}: {by_country}"
    line += f" / показано {len(jobs)}"
    print(line)
    if b.note:
        print(f"⚠️  {b.note}")
    print()
    for j in jobs:
        if pat:
            # Сначала — поля, из-за которых вакансия попала в фильтр: страна часто
            # лежит в пятой-шестой secondaryLocation, и без этого матч выглядит ошибкой.
            hits = [x for x in (*j.locations, j.title) if pat.search(x)]
            rest = [x for x in j.locations if x not in hits]
            locs = "; ".join(hits[:3] + rest[:2]) or "—"
        else:
            locs = "; ".join(j.locations[:4]) or "—"
        print(f"- {j.title}")
        print(f"  {locs}  ·  {j.url}")
    if args.locations:
        from collections import Counter
        cnt = Counter(loc for j in b.jobs for loc in j.locations[:1])
        print("\n## Распределение локаций (первое поле)")
        for loc, n in cnt.most_common(30):
            print(f"  {n:>4}  {loc}")
    return 0


def cmd_ats_sniff(args) -> int:
    from .atsapi import sniff
    code = 0
    for url in args.urls:
        try:
            res = sniff(url)
        except BlockedError as e:
            print(f"### {url}\n  АНТИБОТ: {e} — стену снимает человек, зайди браузером")
            code = 1
            continue
        except Exception as e:  # noqa: BLE001
            print(f"### {url}\n  не отдалась: {e}")
            code = 1
            continue
        print(f"### {url}" + (f" → {res['final']}" if res["final"] != url else ""))
        if res["hits"]:
            for h in res["hits"]:
                print(f"  {h}")
        else:
            print("  (маркеров ATS не найдено — возможно, самописный сайт или SPA; "
                  "смотри глазами)")
    return code


def cmd_ats(args) -> int:
    return {"check": cmd_ats_check, "jobs": cmd_ats_jobs, "sniff": cmd_ats_sniff}[args.ats_cmd](args)


# ──────────────────────────────────────────────────────────────────────────────
# detail / enrich
# ──────────────────────────────────────────────────────────────────────────────

def cmd_detail(args) -> int:
    from .detail import format_detail, get_detail
    try:
        d = get_detail(args.url, use_render=args.render,
                       cookies_from=getattr(args, "cookies_from", None),
                       use_cache=getattr(args, "cache", False))
    except BlockedError as e:
        print(f"АНТИБОТ: {e}. Проверку проходит человек — открой страницу браузером.",
              file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"не разобрал страницу: {e}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(d.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_detail(d))
    return 0


# Роли, ради которых прогон вообще запускается. Лимит выжимок должен тратиться
# на них: раньше отбор шёл чистым ORDER BY published_at DESC, и верх занимали
# ежедневные перепубликации агрегатора jobgether (606 строк в базе) — Power
# Platform, Angular, PHP-Ruby, Data Architect. Лимит уходил в никуда.
#
# ЭТО ТОТ ЖЕ ОБЪЕКТ, что фильтр профессии на входе, а не вторая копия — и вот
# почему копии больше нет. Своя регулярка здесь дописывала русские основы БЕЗ
# хвоста `\w*`, но С замыкающим `\b`: «платформ\b» не совпадает с «платформенный»,
# «бэкенд\b» — с «бэкенда», «высоконагруж\b» — с «высоконагруженных». То есть вся
# русская половина списка не срабатывала НИ РАЗУ, а devops, architect, cloud,
# software engineer, микросервис и распределённ в ней просто отсутствовали.
#
# Замер на 4113 живых заголовках базы: своя регулярка признавала профильными 2341,
# ATS_ROLE_RE — 3447 при НУЛЕ расхождений в обратную сторону. 1106 профильных строк
# («Разработчик бэкенда», «Платформенный инженер», «DevOps-инженер», «Архитектор
# решений», «Системный инженер в Yandex Cloud») падали во вторую категорию очереди
# и при любом лимите не получали описания никогда.
#
# Две регулярки на один вопрос расходятся всегда — расходились и эти. Имя оставлено
# алиасом: читателю `_enrich_rank` полезно видеть «профиль», а не «роль для ATS».
_PROFILE_RE = ATS_ROLE_RE


def _enrich_date(row: dict) -> str:
    """Дата, по которой считается свежесть. Пустая строка — дата неизвестна."""
    return row.get("published_at") or row.get("first_seen") or ""


def _enrich_rank(row: dict) -> tuple:
    """Категория вакансии: сначала профильные, потом с вилкой. Меньше — раньше.

    Свежести здесь НЕТ намеренно — она добавляется отдельным проходом в
    `_by_relevance`. Внутри возрастающего кортежа ISO-дату не развернуть, а
    попытка это сделать уже стоила дорого (см. там же)."""
    title = row.get("title") or ""
    return (0 if _PROFILE_RE.search(title) else 1,
            0 if (row.get("salary_from") or row.get("salary_to")) else 1)


def _by_relevance(rows: list[dict]) -> list[dict]:
    """Порядок отбора: профильные роли → с вилкой → свежие.

    Два прохода, а не один кортеж: `sorted` идёт по возрастанию, а дату надо
    развернуть. Раньше дата лежала в кортеже как есть — и «свежие» на деле
    означало «самые старые»: весь лимит выжимок уходил на объявления 2022–2024
    годов (Java и QA четырёхлетней давности), пока свежие Go-вакансии с вилкой
    оставались без единой строки описания.

    Сортировка в Python стабильная, поэтому порядок по дате внутри категорий
    сохраняется. Строки без даты уезжают в конец."""
    rows = sorted(rows, key=_enrich_date, reverse=True)
    return sorted(rows, key=_enrich_rank)


# Потолок выжимок за прогон. Сотни, а не десятки — и вот арифметика: при `20`
# в базе стояли 4091 вакансия и 118 описаний, то есть 97% строк не имели описания
# НИКОГДА, а карточку без описания написать нельзя. За прогон отрезалось 2692.
#
# Почему это не «сколько угодно»: каждая выжимка — живой GET к площадке. 400 штук
# с паузой по хосту — это минуты и никакого троттлинга; 2700 — это полчаса стука
# в одни и те же домены, то есть ровно то, за что нас уже забанила rabota.ru.
#
# Почему потолок вообще терпим: выжимки КЭШИРУЮТСЯ в базе (`store.have_details`),
# второй раз ничего не качается. Поэтому отрезанное не теряется — оно достаётся
# следующему прогону, и это единственная причина, по которой лимит здесь честен.
# Всё, что он отрезал, названо вслух отдельной строкой — вместе с тем, сколько
# среди отрезанного профильных.
DEFAULT_MAX_ENRICH = 400

# Пауза между двумя запросами к ОДНОМУ хосту в enrich. 0.7s подобрано по факту:
# при восьми потоках без паузы весь пул уходит на самый плотный домен разом.
ENRICH_PACE = 0.7


def enrich_max(raw: int | None) -> int | None:
    """`--max`/`--max-enrich` → потолок выжимок. 0 и отрицательное = без потолка."""
    if raw is None:
        return DEFAULT_MAX_ENRICH
    return None if raw <= 0 else raw


def run_enrich(db: str, since_iso: str | None, *, sources: list[str] | None = None,
               max_n: int | None = None, workers: int = 8, refresh: bool = False,
               include_decided: bool = False, pace: float = ENRICH_PACE) -> dict:
    """Ядро enrich без печати — им пользуется и `enrich`, и `scan`.
    Возвращает {digests, ok, blocked, failed, fails, delta, done, todo, skipped_by_max}."""
    from .detail import digest, get_detail
    from .net import parallel

    with store.connect(db) as conn:
        rows = store.query(conn, first_seen_since=since_iso, sources=sources,
                           exclude_decided=not include_decided, limit=None)
    rows = [r for r in rows if r["url"]]
    keys = [(r["source"], r["external_id"]) for r in rows]

    with store.connect(db) as conn:
        done = store.have_details(conn, keys) if not refresh else set()
    todo = [r for r in rows if (r["source"], r["external_id"]) not in done]
    # Схлопываем агрегаторские дубли: одна вакансия, размноженная по площадкам,
    # не должна съедать лимит несколько раз.
    seen_dup: set[str] = set()
    deduped = []
    for r in todo:
        dk = r.get("dup_key")
        if dk and dk in seen_dup:
            continue
        if dk:
            seen_dup.add(dk)
        deduped.append(r)
    dropped_dups = len(todo) - len(deduped)
    todo = _by_relevance(deduped)

    # Что именно отрезал потолок. Просто «отрезано 2692» не отвечает на главный
    # вопрос — попали ли под нож те роли, ради которых прогон запускается. Считаем
    # профильные ОТДЕЛЬНО: их ноль в отрезке — это «лимит не мешает», а не
    # «отрезано мало».
    profile_total = sum(1 for r in todo if _PROFILE_RE.search(r.get("title") or ""))
    cut = todo[max_n:] if max_n else []
    skipped_by_max = len(cut)
    skipped_profile = sum(1 for r in cut if _PROFILE_RE.search(r.get("title") or ""))
    if max_n:
        todo = todo[:max_n]

    # Вежливость к площадкам: пул в восемь потоков без паузы бьёт в один домен
    # восемью запросами сразу. Пауза считается ПО ХОСТУ, поэтому пятьсот вакансий
    # с разных площадок всё равно качаются параллельно.
    pacer = HostPacer(pace)

    def fetch_one(url: str):
        pacer.wait(url)
        return get_detail(url)

    results = parallel(
        {f"{r['source']}:{r['external_id']}": (lambda u=r["url"]: fetch_one(u))
         for r in todo},
        workers=workers)

    ok = blocked = failed = 0
    digests: list[str] = []
    fails: list[str] = []
    with store.connect(db) as conn:
        for r in todo:
            key = f"{r['source']}:{r['external_id']}"
            success, payload = results[key]
            if success:
                ok += 1
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  payload.status, payload=payload.to_dict())
                digests.append(digest(payload))
            elif isinstance(payload, BlockedError):
                blocked += 1
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  "blocked", error=str(payload))
                fails.append(f"{key} АНТИБОТ: {payload}")
            else:
                failed += 1
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  "error", error=str(payload))
                fails.append(f"{key} УПАЛ: {payload}")

    return {"digests": digests, "ok": ok, "blocked": blocked, "failed": failed,
            "fails": fails, "delta": len(rows), "done": len(done), "todo": len(todo),
            "skipped_by_max": skipped_by_max, "skipped_profile": skipped_profile,
            "profile_total": profile_total, "dropped_dups": dropped_dups,
            "max_n": max_n, "rows": rows}


def enrich_summary(res: dict) -> str:
    """Одна строка про то, сколько обогащено и сколько отрезано. Без неё лимит
    работает молча, а молчащий лимит — это и есть тихая потеря.

    Только `.get`: сюда приезжает и полный результат `run_enrich`, и огрызок
    этапа из упавшего скана. Отчёт обязан собираться при любом подмножестве
    ключей — падение сборки стоит всего файла, а не одной строки.
    """
    line = (f"обогащено {res.get('ok', 0)} "
            f"(антибот {res.get('blocked', 0)}, упало {res.get('failed', 0)}), "
            f"уже было в базе {res.get('done', '?')}, "
            f"в дельте {res.get('delta', '?')}")
    if res.get("dropped_dups"):
        line += f", схлопнуто агрегаторских дублей {res['dropped_dups']}"
    if res.get("skipped_by_max"):
        line += (f"\n⚠️  ОТРЕЗАНО ПО ЛИМИТУ {res['skipped_by_max']}"
                 f" (--max/--max-enrich {res.get('max_n')}), из них профильных "
                 f"{res.get('skipped_profile', 0)} из {res.get('profile_total', 0)}."
                 f"\n    Отрезанное не потеряно: выжимки кэшируются, следующий "
                 f"прогон возьмёт их без повторной закачки. Забрать всё сейчас — "
                 f"лимит 0 (без потолка).")
    else:
        # Именно «в очередь попало всё», а не «у всех есть описание»: часть
        # очереди могла упереться в антибот или упасть, и это разные новости.
        line += "\n✓ Потолок ничего не отрезал: вся дельта попала в очередь."
    return line


def cmd_enrich(args) -> int:
    since = store.since_arg(args.since)
    res = run_enrich(args.db, since,
                     sources=args.source.split(",") if args.source else None,
                     max_n=enrich_max(args.max), workers=args.workers,
                     refresh=args.refresh, include_decided=args.include_decided)

    print(f"# enrich: в дельте {res['delta']}, уже обогащено {res['done']}, "
          f"качаю {res['todo']}"
          + (f", отрезано по --max {res['skipped_by_max']}" if res["skipped_by_max"] else "")
          + (f", схлопнуто дублей {res['dropped_dups']}" if res["dropped_dups"] else "")
          + "\n  порядок: профильные роли → с вилкой → свежие")
    for d in res["digests"]:
        print()
        print(d)

    print(f"\n## Итог: ok {res['ok']} / антибот {res['blocked']} / "
          f"упало {res['failed']} из {res['todo']}")
    print(enrich_summary(res))
    for f in res["fails"]:
        print(f"  - {f}")
    if res["blocked"]:
        print("АНТИБОТ снимается заходом человека в браузере, не повтором скрипта.")
    return 1 if res["failed"] else 0


# ──────────────────────────────────────────────────────────────────────────────
# tg / check-links
# ──────────────────────────────────────────────────────────────────────────────

def cmd_tg(args) -> int:
    from .tg import render
    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"файл не читается: {e}", file=sys.stderr)
        return 1
    try:
        report, counters = render(text, since=args.since, full=args.full)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    print(report)
    # Сверка полноты: заголовков в файле должно быть ровно столько, сколько сообщений
    # (плюс, возможно, одно синтетическое «до первого заголовка» — оно с id «?»).
    headers = len(re.findall(r"^\[#\d+\] \[\d{4}-\d{2}-\d{2}T", text, re.M))
    synthetic = 1 if "(до первого заголовка)" in report else 0
    if headers != counters["total"] - synthetic:
        print(f"\n⚠️  РАСХОЖДЕНИЕ: заголовков в файле {headers}, разобрано "
              f"{counters['total']} — парсер потерял сообщения, это баг, чини tg.py")
        return 1
    return 0


def cmd_check_links(args) -> int:
    """Предфлайт живости ATS-ссылок перед вставкой в карточки.

    Ashby ротирует UUID вакансии при переопубликации: ссылка вчерашнего скана может
    быть мёртвой при живой вакансии. Для мёртвой ссылки печатаются живые вакансии
    той же доски с похожим названием — обычно среди них и есть переехавшая."""
    from .atsapi import board, parse_job_url

    parsed: list[tuple[str, tuple | None]] = [(u, parse_job_url(u)) for u in args.urls]
    boards: dict[tuple[str, str], object] = {}
    for _, p in parsed:
        if p and (p[0], p[1]) not in boards:
            try:
                boards[(p[0], p[1])] = board(p[0], p[1])
            except Exception as e:  # noqa: BLE001
                boards[(p[0], p[1])] = e

    def title_words(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-zа-яё0-9+#]{3,}", s.lower())
                if w not in {"the", "and", "for", "или", "разработчик", "developer",
                             "engineer", "инженер", "senior", "middle", "junior"}}

    exit_code = 0
    with store.connect(args.db) as conn:
        for url, p in parsed:
            if not p:
                print(f"?  {url}\n   не ATS-ссылка — живость по API не проверить, "
                      f"открой глазами или `scout resolve`")
                exit_code = 1
                continue
            ats, token, jid = p
            b = boards[(ats, token)]
            if isinstance(b, Exception):
                kind = "АНТИБОТ" if isinstance(b, BlockedError) else "НЕДОСТУПНА"
                print(f"?  {url}\n   доска {ats}:{token} {kind}: {b}")
                exit_code = 1
                continue
            hit = next((j for j in b.jobs if j.id.lower() == jid.lower()), None)
            if hit:
                print(f"✓  ЖИВА  {url}\n   {hit.title} · {'; '.join(hit.locations[:3]) or '—'}")
                continue
            exit_code = 1
            print(f"✗  МЕРТВА  {url}")
            # Название пропавшей вакансии: из базы по URL или из slug самой ссылки.
            row = conn.execute("SELECT title FROM vacancy WHERE url LIKE ? LIMIT 1",
                               (f"%{jid}%",)).fetchone()
            words = title_words(row["title"] if row else jid.replace("-", " "))
            similar = [j for j in b.jobs if words & title_words(j.title)] if words else []
            if similar:
                print(f"   похожие живые на {ats}:{token}:")
                for j in similar[:10]:
                    print(f"     - {j.title} · {j.url}")
            elif b.jobs:
                print(f"   похожих нет; на доске {b.total} вакансий "
                      f"(`scout ats jobs {ats}:{token}` покажет все)")
            else:
                print(f"   доска жива, но пуста ({b.note or 'вакансий ноль'})")
    return exit_code


# ──────────────────────────────────────────────────────────────────────────────
# tg-auth / tg-fetch: Telegram-архив без MCP
# ──────────────────────────────────────────────────────────────────────────────

def cmd_tg_auth(args) -> int:
    from . import tgclient
    if args.action == "login":
        return tgclient.cmd_login()
    return tgclient.cmd_status()


def tg_fetch_flow(out_dir: str, *, archive_only: bool = True, mark: bool = True) -> tuple[int, list[dict]]:
    """Обход архива + автоматический прогон парсера `tg` по каждому дампу.

    Печатает в stdout (scan перехватывает это в отчёт), возвращает
    (код, кандидаты) — кандидаты нужны сверке с таблицей статусов."""
    from . import tgclient
    from .tg import classify as tg_classify, parse_dump, render as tg_render

    summary = tgclient.fetch(out_dir, archive_only=archive_only, mark=mark)
    print(f"# tg-fetch: чатов с непрочитанным {summary.visited}, дампов {summary.dumped}, "
          f"отмечено прочитанным {summary.marked}, упало {summary.failed}")
    for cr in summary.chats:
        if cr.error:
            print(f"  УПАЛ  {cr.title[:40]:<40} {cr.error[:70]}")
        else:
            t = f" ({cr.topics} топиков)" if cr.topics else ""
            m = "✓" if cr.marked else "·"
            print(f"  {m}     {cr.title[:40]:<40} {cr.messages:>4} сообщ.{t}"
                  + (f"  → {cr.dump_path}" if cr.dump_path else ""))
    if not summary.visited:
        print("  непрочитанного в архиве нет")

    candidates: list[dict] = []
    for cr in summary.chats:
        if not cr.dump_path:
            continue
        with open(cr.dump_path, encoding="utf-8") as f:
            text = f.read()
        print(f"\n### {cr.title}")
        try:
            report, _counters = tg_render(text)
            print(report)
        except ValueError as e:
            print(f"дамп не разобрался: {e}")
            continue
        for msg in parse_dump(text):
            tg_classify(msg)
            if msg.category == "candidate":
                candidates.append({"chat": cr.title, "id": msg.id, "date": msg.date,
                                   "body": msg.body})
    return (1 if summary.failed else 0), candidates


def cmd_tg_dm(args) -> int:
    """Личная переписка с рекрутёром — прочитать перед тем, как советовать контакт.

    Отдельная команда, а не флаг tg-fetch: у tg-fetch есть mark-as-read, а здесь
    его быть не должно ни при каком флаге."""
    from . import tgclient

    try:
        res = tgclient.read_dm(args.peer, limit=args.limit)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 1
    print(tgclient.render_dm(res, args.peer, args.limit), end="")
    return 0


def cmd_tg_fetch(args) -> int:
    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.db)), "tg",
        datetime.now(timezone.utc).date().isoformat())
    code, candidates = tg_fetch_flow(out_dir, archive_only=args.archive_only,
                                     mark=not args.no_mark)
    print(f"\nИтого кандидатов из Telegram: {len(candidates)}")
    return code


# ──────────────────────────────────────────────────────────────────────────────
# render: SPA через браузер
# ──────────────────────────────────────────────────────────────────────────────

def cmd_render(args) -> int:
    from . import cookiesrc
    from .detail import html_to_text
    from .render import RenderUnavailable, render_page

    # Одна строка про источник кук — уровень 1 из трёх: есть куки, взяли молча.
    if not (args.session or args.session_file):
        src = cookiesrc.resolve(args.cookies_from, cookiesrc.domains_for_url(args.url),
                                use_cache=args.cache, write_cache=args.save_cache)
        cookiesrc.report(src)
    try:
        html, final = render_page(args.url, session=args.session,
                                  session_file=args.session_file, wait=args.wait,
                                  cookies_from=args.cookies_from, use_cache=args.cache)
    except RenderUnavailable as e:
        print(e, file=sys.stderr)
        return 3
    except BlockedError as e:
        print(f"АНТИБОТ: {e}", file=sys.stderr)
        return 1
    except FetchError as e:
        print(f"не отрендерилось: {e}", file=sys.stderr)
        return 1
    # Залогинены ли мы — говорится ВСЕГДА, когда площадка известна: анонимный вид
    # с exit 0 неотличим от нужного, и именно так теряются прямые контакты.
    from . import auth as _auth
    platform = _auth.platform_for_url(final)
    if platform:
        state, why = _auth.login_state(platform, html)
        mark = {"logged_in": "ВХОД ЕСТЬ", "anonymous": "⚠️  АНОНИМНО",
                "unknown": "вход: неясно"}[state]
        print(f"{mark} ({platform}): {why}", file=sys.stderr)

    if args.mode == "html":
        sys.stdout.write(html)
        return 0
    m = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S | re.I)
    body = m.group(1) if m else (re.search(r"<body\b[^>]*>(.*)</body>", html, re.S | re.I)
                                 or [None, html])[1]
    text = html_to_text(body)
    print(f"# {final} ({len(text)} симв. текста)", file=sys.stderr)
    print(text)
    if not text.strip():
        print("после рендера текста не осталось — страница пуста или требует входа "
              "(--session <площадка> после `scout auth login`)", file=sys.stderr)
        return 1
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# hh-sync / mail-sync
# ──────────────────────────────────────────────────────────────────────────────

def cmd_hh_sync(args) -> int:
    from . import hhsync
    return hhsync.sync(args.db, max_pages=args.max_pages,
                       cookies_from=getattr(args, "cookies_from", None),
                       use_cache=getattr(args, "cache", False))


def cmd_mail_sync(args) -> int:
    from . import mailsync
    return mailsync.sync(args.db, days=args.days)


def cmd_mail_ingest(args) -> int:
    from . import mailsync
    return mailsync.ingest(args.db, args.file)


def cmd_browse(args) -> int:
    from . import cookiesrc
    from .detail import html_to_text
    from .render import RenderUnavailable, browse

    cookiesrc.report(cookiesrc.resolve(args.cookies_from,
                                       cookiesrc.domains_for_url(args.url),
                                       use_cache=args.cache))
    try:
        html, final = browse(args.url, keep=args.keep, wait=args.wait,
                             cookies_from=args.cookies_from, use_cache=args.cache)
    except RenderUnavailable as e:
        print(e, file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"не открылось: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    m = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S | re.I)
    body = m.group(1) if m else (re.search(r"<body\b[^>]*>(.*)</body>", html, re.S | re.I)
                                 or [None, html])[1]
    text = html_to_text(body)
    print(f"# {final} ({len(text)} симв. текста)", file=sys.stderr)
    print(text[:8000])
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# scan: весь конвейер одной командой
# ──────────────────────────────────────────────────────────────────────────────

_MATCH_WORD = re.compile(r"[a-zа-яё0-9+#]{4,}", re.I)
_MATCH_NOISE = {"разработчик", "developer", "engineer", "инженер", "программист",
                "senior", "middle", "junior", "lead", "ведущий", "старший",
                "вакансия", "вакансию", "ищем", "ищет", "команду", "компания",
                "remote", "удаленно", "удалённо", "backend", "бэкенд"}


def _match_words(s: str | None) -> set[str]:
    return {w.lower() for w in _MATCH_WORD.findall(s or "")} - _MATCH_NOISE


def match_processed(candidates: list[dict], negotiations: list[dict]) -> list[dict]:
    """Сверка свежих кандидатов с таблицей статусов откликов.

    Консервативно и только как пометка «возможный дубль» — ничего не выбрасывается:
    авто-склейку по похожести уже пробовали, и она врала (см. README про Дайса).
    Вакансия с площадки матчится по компании (взаимное вхождение подстроки, короче
    5 символов не считается) ПЛЮС пересечению значимых слов названия. Телеграм-
    кандидат без структурной компании — по вхождению компании из статуса в текст поста.

    Результат — ОДНА строка на кандидата со списком совпавших статусов, а не
    декартово произведение: раньше одна вакансия «Golang-разработчик» матчилась
    с четырьмя строками Т-Банка подряд и блок раздувался до 69 строк на 40
    кандидатов. Статусы `other` в сверке не участвуют вовсе — среди них лежат
    рекламные рассылки, и отчёт сообщал «возможный дубль» про письмо о грантах.
    """
    negs = [n for n in negotiations if n.get("status") != "other"]
    out = []
    for c in candidates:
        c_comp = (c.get("company") or "").strip().lower()
        c_words = _match_words(c.get("title"))
        body = (c.get("body") or "").lower()
        hits = []
        for n in negs:
            n_comp = (n.get("company") or "").strip().lower()
            why = None
            # Порог 5 символов: на четырёх «Mira» ↔ «Miratech» давало ложный матч.
            if (c_comp and n_comp and min(len(c_comp), len(n_comp)) >= 5
                    and (c_comp in n_comp or n_comp in c_comp)):
                if c_words & _match_words(n.get("title")):
                    why = "компания + пересечение слов названия"
                elif n.get("source") == "mail":
                    # В title почтовой строки лежит тема письма — пересечения слов
                    # с названием вакансии может не быть при том же работодателе.
                    why = "компания совпала (тема письма без пересечения слов)"
            elif body and n_comp and len(n_comp) >= 5 and n_comp in body:
                why = "компания из статуса встречается в telegram-посте"
            if why:
                hits.append({"negotiation": n, "why": why})
        if hits:
            # Отказ важнее просмотра: он и есть повод не откликаться второй раз.
            rank = {"rejection": 0, "invitation": 1, "interview": 1, "applied": 2}
            hits.sort(key=lambda h: rank.get(h["negotiation"].get("status"), 3))
            out.append({"candidate": c, "hits": hits,
                        # Плоские поля — обратная совместимость вывода и тестов.
                        "negotiation": hits[0]["negotiation"], "why": hits[0]["why"]})
    return out


_STAGE_MARK = {"ok": "ok", "blocked": "АНТИБОТ", "error": "УПАЛ", "skipped": "ПРОПУЩЕН",
               "no_creds": "НЕТ КРЕДОВ", "no_dep": "НЕТ ЗАВИСИМОСТИ",
               "no_login": "НУЖЕН ВХОД"}


def _count_in(text: str, pattern: str) -> int | str:
    """Число из вывода этапа для колонки «найдено». Не нашлось — «—», не ноль:
    ноль означал бы «этап отработал и ничего не нашёл»."""
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else "—"


def _money(r: dict) -> str:
    return Vacancy(source=r.get("source") or "", external_id="", url="",
                   title=r.get("title") or "", salary_from=r.get("salary_from"),
                   salary_to=r.get("salary_to"), currency=r.get("currency"),
                   salary_gross=(bool(r["salary_gross"])
                                 if r.get("salary_gross") is not None else None),
                   salary_period=r.get("salary_period"),
                   ).salary_str() or "—"


def _delta_table(rows: list[dict], limit: int = 0, days: int = 3) -> list[str]:
    """Компактная таблица всей дельты — то, ради чего отчёт вообще существует.

    Без неё в отчёт попадали ТОЛЬКО дайджесты enrich (лимит 20), а 1441 вакансия
    из 1517 не упоминалась даже строкой: карточку по ним написать было нельзя,
    и об этом никто не узнавал.

    `limit=0` — ВСЯ дельта, и это умолчание. Потолок 400 при дельте 3288 означал
    2888 вакансий, о которых в отчёте была одна строчка «остальное через
    scout new»: формально честно, практически — их никто не смотрел. Таблица
    строится строками по ~140 байт, поэтому «вся дельта» стоит сотни килобайт
    текста, а не мегабайты HTML, ради которых всё это и затевалось.
    """
    if not rows:
        return ["Дельта пуста."]
    # Тот же порядок, что у выжимок: профильные роли → с вилкой → свежие.
    # Иначе потолок таблицы срезает именно Go-вакансии, ради которых всё и есть:
    # верх занимают Business Analyst и перепубликации агрегатора.
    rows = _by_relevance(rows)
    cut = limit if limit and limit > 0 else len(rows)
    shown = rows[:cut]
    profile = sum(1 for r in rows if _PROFILE_RE.search(r.get("title") or ""))
    head = (f"Всего в дельте: {len(rows)} (профильных ролей {profile}); "
            f"порядок: профильные роли → с вилкой → свежие")
    if len(rows) > cut:
        head += f". В ТАБЛИЦЕ ПЕРВЫЕ {cut}"
    out = [head, "",
           "| # | роль | компания | деньги | локация | формат | источник | ссылка |",
           "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(shown, 1):
        loc = (r.get("location") or "—")[:28].replace("|", "/")
        fmt = "remote" if r.get("remote") else ""
        out.append(f"| {i} | {(r.get('title') or '')[:58].replace('|', '/')} "
                   f"| {(r.get('company') or '—')[:26].replace('|', '/')} "
                   f"| {_money(r)} | {loc} | {fmt} | {r.get('source')} "
                   f"| {r.get('url')} |")
    if len(rows) > cut:
        lost_profile = sum(1 for r in rows[cut:]
                           if _PROFILE_RE.search(r.get("title") or ""))
        out.append(f"\n⚠️  В ТАБЛИЦЕ {cut} ИЗ {len(rows)}: за кадром "
                   f"{len(rows) - cut}, из них профильных ролей {lost_profile}. "
                   f"Остальное — `scout new --since {days}d --limit 0` или "
                   f"`scan --report-rows 0`.")
    return out


def build_scan_report(stages: dict, *, generated_at: str, days: int,
                      matches: list[dict] | None = None,
                      delta_rows: list[dict] | None = None,
                      report_rows: int = 0) -> str:
    """Единый отчёт скана — файл, который модель читает ВМЕСТО ручного обхода.

    Чистая функция: сюда приходят готовые результаты этапов, поэтому сборка
    тестируется без сети. Отчёт обязан собраться при любом подмножестве этапов
    и любых ошибках: упавший этап — строка в покрытии и в блоке ошибок, не отмена
    всего файла."""
    out = [f"# scout scan — {generated_at[:10]}", "",
           f"Сгенерирован: {generated_at} · окно: {days} дн.", ""]

    # ── Покрытие всех источников ─────────────────────────────────────────
    out += ["## Покрытие", "", "| этап | статус | найдено | примечание |",
            "|---|---|---|---|"]
    collect = stages.get("collect") or {}
    for r in collect.get("report", []):
        note = (r.get("error") or r.get("note") or "")[:70]
        if r.get("limit_hit"):
            note = f"ОБХОД ОБРЕЗАН ПОТОЛКОМ — {r['limit_hit']}; " + note
        out.append(f"| площадка: {r['source']} | {_STAGE_MARK.get(r['status'], r['status'])} "
                   f"| {r['found']} | {note[:140].replace('|', '/')} |")
    if collect.get("status") == "error":
        out.append(f"| collect (весь этап) | УПАЛ | — | {(collect.get('error') or '')[:70]} |")
    stage_rows = (("telegram", "telegram-архив", "candidates"),
                  ("enrich", "enrich дельты", "ok"),
                  ("hh", "hh-sync (кабинет hh)", "found"),
                  ("mail", "mail-sync (почта)", "found"))
    for key, label, found_key in stage_rows:
        st = stages.get(key)
        if not st:
            out.append(f"| {label} | НЕ ЗАПУСКАЛСЯ | — |  |")
            continue
        note = st.get("note") or st.get("error") or ""
        out.append(f"| {label} | {_STAGE_MARK.get(st.get('status'), st.get('status'))} "
                   f"| {st.get(found_key, '—')} | {note[:70]} |")
    if collect.get("status") == "ok":
        out.append(f"\nПлощадки: найдено {collect.get('found', 0)}, "
                   f"новых {collect.get('new', 0)}, обновлено {collect.get('updated', 0)}.")
    for line in _limit_lines(collect.get("report") or [], collect.get("limit")):
        out.append(line)

    # ── Кандидаты из Telegram ────────────────────────────────────────────
    out += ["", "## Кандидаты из Telegram", ""]
    tg = stages.get("telegram") or {}
    if tg.get("text"):
        out.append(tg["text"].rstrip())
    else:
        out.append(f"Этап не дал текста: {_STAGE_MARK.get(tg.get('status'), '—')}"
                   + (f" — {tg.get('note') or tg.get('error')}" if (tg.get('note') or tg.get('error')) else ""))

    # ── Дельта площадок: СНАЧАЛА таблица всего, потом выжимки верхних ─────
    en = stages.get("enrich") or {}
    out += ["", "## Дельта площадок — все вакансии", ""]
    out += _delta_table(delta_rows if delta_rows is not None else (en.get("rows") or []),
                        limit=report_rows, days=days)

    out += ["", "## Выжимки (enrich): верхние по релевантности", ""]
    if en.get("status") == "ok":
        out.append(enrich_summary(en))
        out.append("Порядок отбора: профильные роли → с вилкой → свежие; "
                   "остальное — в таблице выше.")
    if en.get("digests"):
        for d in en["digests"]:
            out += ["", d]
    else:
        out.append("Дайджестов нет: "
                   + (en.get("note") or en.get("error") or "дельта пуста или этап не отработал"))

    # ── Статусы откликов ─────────────────────────────────────────────────
    out += ["", "## Статусы откликов (hh + почта)", ""]
    for key, label in (("hh", "hh-sync"), ("mail", "mail-sync")):
        st = stages.get(key) or {}
        out.append(f"### {label}")
        out.append(st.get("text", "").rstrip()
                   or f"({_STAGE_MARK.get(st.get('status'), 'НЕ ЗАПУСКАЛСЯ')}"
                      + (f": {st.get('note') or st.get('error')})" if (st.get('note') or st.get('error')) else ")"))
        out.append("")

    # ── Уже отработано ───────────────────────────────────────────────────
    out += ["## Уже отработано (возможные дубли)", ""]
    if matches:
        out.append("Совпадения свежих кандидатов с таблицей статусов. Это ПОМЕТКИ, "
                   "а не фильтр — кандидаты остаются в списках выше, решает человек. "
                   "Одна строка на кандидата; статусы `other` в сверке не участвуют.")
        out.append("")
        for m in matches:
            c = m["candidate"]
            out.append(f"- [возможный дубль] {(c.get('title') or '?')[:60]} "
                       f"({c.get('source') or c.get('chat') or '?'})"
                       + (f" — {c['url']}" if c.get("url") else ""))
            hits = m.get("hits") or [{"negotiation": m["negotiation"], "why": m["why"]}]
            for h in hits[:3]:
                n = h["negotiation"]
                out.append(f"    · {n.get('status')} от {n.get('source')}: "
                           f"{(n.get('title') or '')[:60]} — {n.get('company') or '?'} "
                           f"[{h['why']}]")
            if len(hits) > 3:
                out.append(f"    · … ещё {len(hits) - 3} совпадений по той же компании "
                           f"(`scout status --query «{(c.get('company') or '')[:30]}»`)")
    else:
        out.append("Совпадений с таблицей статусов не найдено (или сверка не отработала — "
                   "см. блок ошибок).")

    # ── Стены и ошибки ───────────────────────────────────────────────────
    out += ["", "## Стены и ошибки", ""]
    problems: list[str] = []
    for r in collect.get("report", []):
        if r["status"] == "blocked":
            problems.append(f"АНТИБОТ {r['source']}: {(r.get('error') or '')[:80]} — "
                            f"снимает человек заходом в браузер, не скрипт")
        elif r["status"] == "error":
            problems.append(f"УПАЛ {r['source']}: {(r.get('error') or '')[:80]}")
    for key, label in (("collect", "collect"), ("telegram", "telegram"),
                       ("enrich", "enrich"), ("hh", "hh-sync"), ("mail", "mail-sync")):
        st = stages.get(key) or {}
        if st.get("status") in ("error", "no_creds", "no_dep"):
            problems.append(f"{label}: {_STAGE_MARK[st['status']]} — "
                            f"{(st.get('error') or st.get('note') or '')[:100]}")
        for f in st.get("fails", []):
            problems.append(f"{label}: {f[:110]}")
    if (stages.get("enrich") or {}).get("blocked"):
        problems.append(
            f"enrich: стен {stages['enrich']['blocked']} — повтор по ним отложен "
            f"на {store.RETRY_BLOCKED_DAYS} дн., чтобы капчи не съедали лимит "
            f"выжимок каждый прогон")
    if problems:
        out += [f"- {p}" for p in problems]
    else:
        out.append("Чисто: все этапы отработали без стен и ошибок.")
    out.append("")
    return "\n".join(out)


def cmd_scan(args) -> int:
    """Оркестратор: каждый этап падает независимо, отчёт собирается всегда."""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    since_iso = store.since_arg(f"{args.days}d")
    stages: dict[str, dict] = {}
    tg_candidates: list[dict] = []

    def banner(s):
        print(f"\n{'═' * 8} {s} {'═' * 8}", flush=True)

    # 1. collect — площадки
    banner("collect: площадки")
    try:
        # Лимит передаётся ЯВНО. Раньше здесь его не было вовсе, и scan молча
        # брал умолчание dataclass-а Ctx (100) — то есть самый автономный режим
        # работал с самым узким потолком из всех.
        ctx = Ctx(query="Golang", extra_queries=("Go разработчик", "Backend Go"),
                  days=args.days, limit=limit_value(args.limit))
        res = run_collect(ctx, list(SOURCES), db=args.db,
                          no_browser=getattr(args, "no_browser", False),
                          args_dict={"cmd": "scan", "days": args.days,
                                     "limit": ctx.limit})
        _print_coverage(res["report"], res["total"], res["new"],
                        res["updated"], res["elapsed"], limit=ctx.limit)
        # `total` — вакансии без служебных строк-сводок. `len(vacancies)` завышал
        # цифру ровно на число источников: сводка каждого источника считалась
        # вакансией, и «найдено» в отчёте не сходилось с числом строк в таблице.
        stages["collect"] = {"status": "ok", "report": res["report"],
                             "found": res["total"], "new": res["new"],
                             "updated": res["updated"], "limit": ctx.limit}
    except Exception as e:  # noqa: BLE001
        stages["collect"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        print(f"collect УПАЛ: {e}", file=sys.stderr)

    # 2. Telegram-архив
    if args.no_telegram:
        stages["telegram"] = {"status": "skipped", "note": "выключен флагом --no-telegram"}
    else:
        banner("tg-fetch: архив Telegram")
        buf = io.StringIO()
        try:
            out_dir = os.path.join(os.path.dirname(os.path.abspath(args.db)), "tg",
                                   generated_at[:10])
            with redirect_stdout(buf), redirect_stderr(buf):
                code, tg_candidates = tg_fetch_flow(out_dir)
            stages["telegram"] = {
                "status": "ok" if code == 0 else "error", "text": buf.getvalue(),
                "candidates": len(tg_candidates),
                "note": None if code == 0 else "часть чатов упала — детали в тексте этапа"}
        except SystemExit:
            stages["telegram"] = {"status": "no_creds", "text": buf.getvalue(),
                                  "note": "нет telethon/кредов/сессии — инструкция в тексте этапа"}
        except Exception as e:  # noqa: BLE001
            stages["telegram"] = {"status": "error", "text": buf.getvalue(),
                                  "error": f"{type(e).__name__}: {e}"}
        print(stages["telegram"].get("text") or stages["telegram"].get("note") or "")

    # 3. enrich дельты
    banner("enrich: выжимки дельты")
    try:
        res = run_enrich(args.db, since_iso, max_n=enrich_max(args.max_enrich),
                         workers=args.enrich_workers)
        stages["enrich"] = {"status": "ok", **res}
        print(f"ok {res['ok']} / антибот {res['blocked']} / упало {res['failed']} "
              f"из {res['todo']} (в дельте {res['delta']})")
        print(enrich_summary(res))
    except Exception as e:  # noqa: BLE001
        stages["enrich"] = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        print(f"enrich УПАЛ: {e}", file=sys.stderr)

    # 4. hh-sync
    if args.no_hh:
        stages["hh"] = {"status": "skipped", "note": "выключен флагом --no-hh"}
    else:
        banner("hh-sync: кабинет hh")
        from . import hhsync
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                code = hhsync.sync(args.db, cookies_from=args.cookies_from,
                                   use_cache=args.cache)
            stages["hh"] = {
                "status": {0: "ok", 2: "no_creds", 3: "no_dep"}.get(code, "error"),
                "text": buf.getvalue(),
                # Колонка «найдено» у этих этапов всегда была «—», хотя число
                # стоит в первой же строке их вывода.
                "found": _count_in(buf.getvalue(), r"откликов (\d+)")}
        except Exception as e:  # noqa: BLE001
            stages["hh"] = {"status": "error", "text": buf.getvalue(),
                            "error": f"{type(e).__name__}: {e}"}
        print(stages["hh"].get("text", ""))

    # 5. mail-sync
    if args.no_mail:
        stages["mail"] = {"status": "skipped", "note": "выключен флагом --no-mail"}
    else:
        banner("mail-sync: почта")
        from . import mailsync
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                code = mailsync.sync(args.db, days=args.mail_days)
            stages["mail"] = {"status": {0: "ok", 2: "no_creds"}.get(code, "error"),
                              "text": buf.getvalue(),
                              "found": _count_in(buf.getvalue(), r"писем найма (\d+)")}
        except Exception as e:  # noqa: BLE001
            stages["mail"] = {"status": "error", "text": buf.getvalue(),
                              "error": f"{type(e).__name__}: {e}"}
        print(stages["mail"].get("text", ""))

    # 6. Сверка «уже отработано» — по свежей дельте и телеграм-кандидатам
    matches: list[dict] = []
    delta_rows: list[dict] = []
    try:
        with store.connect(args.db) as conn:
            negs = store.negotiations(conn)
            delta_rows = store.query(conn, first_seen_since=since_iso, limit=None,
                                     order="salary")
        cands = [{"title": r["title"], "company": r["company"], "source": r["source"],
                  "url": r["url"]} for r in delta_rows]
        cands += [{"title": c["body"].split("\n")[0][:80], "company": None,
                   "chat": c["chat"], "body": c["body"]} for c in tg_candidates]
        matches = match_processed(cands, negs)
    except Exception as e:  # noqa: BLE001
        print(f"сверка со статусами не отработала: {e}", file=sys.stderr)

    # 7. Отчёт — одним файлом, при любом исходе этапов
    report_text = build_scan_report(stages, generated_at=generated_at, days=args.days,
                                    matches=matches, delta_rows=delta_rows,
                                    report_rows=args.report_rows)
    rep_dir = os.path.join(os.path.dirname(os.path.abspath(args.db)), "reports")
    os.makedirs(rep_dir, exist_ok=True)
    path = os.path.join(rep_dir, f"{generated_at[:10]}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text)

    banner("итог")
    for key in ("collect", "telegram", "enrich", "hh", "mail"):
        st = stages.get(key) or {}
        print(f"  {key:<10} {_STAGE_MARK.get(st.get('status'), '—')}"
              + (f"  {st.get('note') or st.get('error') or ''}"[:80]
                 if st.get("note") or st.get("error") else ""))
    print(f"\nОтчёт: {path}")
    return 1 if any((stages.get(k) or {}).get("status") == "error"
                    for k in ("collect", "telegram", "enrich", "hh", "mail")) else 0


# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    from . import cookiesrc

    # --db принимается и до, и после подкоманды: писать `collect --db ...` естественнее,
    # чем `--db ... collect`, и спотыкаться об это на каждом запуске незачем.
    #
    # default=SUPPRESS здесь обязателен. Подпарсер разбирает свои аргументы в ОТДЕЛЬНОЕ
    # пространство имён и потом копирует его поверх основного — со ЗНАЧЕНИЯМИ ПО
    # УМОЛЧАНИЮ включительно. С обычным default `scout --db свой.db status` молча
    # затирался на `.scout/scout.db`, то есть команда работала не с той базой, что
    # просили (и `--db` до подкоманды не работал вовсе). SUPPRESS кладёт атрибут
    # только когда флаг реально передан, поэтому копировать поверх нечего.
    # Значение по умолчанию живёт ТОЛЬКО на верхнем парсере, и объявлять его надо
    # отдельным вызовом: `parents=[...]` копирует не описание аргумента, а сам объект
    # Action, поэтому `p.set_defaults(db=...)` поменял бы default и у подкоманд —
    # ровно то, что мы здесь чиним.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help="путь к SQLite (по умолчанию .scout/scout.db)")

    p = argparse.ArgumentParser(prog="scout", description="Сборщик вакансий")
    p.add_argument("--db", default=store.DEFAULT_DB,
                   help="путь к SQLite (по умолчанию .scout/scout.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="обойти площадки", parents=[common])
    c.add_argument("--query", default="Golang")
    c.add_argument("--also", nargs="*", default=["Go разработчик", "Backend Go"],
                   help="дополнительные формулировки: одна не покрывает всё")
    c.add_argument("--days", type=int, default=3, help="окно по публикации-или-обновлению")
    c.add_argument("--area", default="113", help="113 — вся РФ, 1 — Москва, 2 — СПб")
    c.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"нижняя граница глубины обхода одной площадки (по "
                        f"умолчанию {DEFAULT_LIMIT}; 0 — сколько отдаёт площадка). "
                        f"Только ПОДНИМАЕТ проверенные потолки источников, "
                        f"обрезание всегда названо строкой в сводке")
    c.add_argument("--sources", help="через запятую; по умолчанию все")
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--ru-only", action="store_true", help="без зарубежных источников")
    c.add_argument("--ats-all", action="store_true",
                   help="нести все роли с ATS-досок, включая заведомо чужие профессии")
    c.add_argument("--no-browser", action="store_true",
                   help="не запускать площадки, которым нужен настоящий браузер "
                        "(glassdoor, levels); в покрытии они будут «ПРОПУЩЕН», "
                        "а не пропадут")
    c.add_argument("--no-store", action="store_true", help="не писать в базу (для облака)")
    c.add_argument("--with-items", action="store_true", help="выгрузить вакансии в JSON")
    c.add_argument("--format", choices=["text", "json"], default="text")
    c.set_defaults(func=cmd_collect)

    n = sub.add_parser("new", help="дельта: что появилось с указанного момента", parents=[common])
    n.add_argument("--since", default="3d", help="3d, 12h, 2026-07-20 или ISO")
    n.add_argument("--by", choices=["seen", "published"], default="seen",
                   help="seen — чего не было в базе; published — по дате площадки")
    n.add_argument("--sources")
    n.add_argument("--limit", type=int, default=200,
                   help="0 — без ограничения; при усечении это ВСЕГДА сказано в шапке")
    n.add_argument("--strict", action="store_true",
                   help="ненулевой код возврата, если выдача обрезана по --limit")
    n.add_argument("--include-decided", action="store_true",
                   help="показать и то, по чему уже принято решение")
    n.add_argument("--format", choices=["text", "json"], default="text")
    n.set_defaults(func=cmd_new)

    v = sub.add_parser("coverage", help="кто отработал в последнем прогоне", parents=[common])
    v.set_defaults(func=cmd_coverage)

    r = sub.add_parser("resolve", help="куда ведёт кнопка «Откликнуться»", parents=[common])
    r.add_argument("url")
    r.add_argument("--no-follow", action="store_true", help="не проходить редиректы")
    r.add_argument("--format", choices=["text", "json"], default="text")
    r.set_defaults(func=cmd_resolve)

    w = sub.add_parser("raw", help="страница источника без парсера", parents=[common])
    w.add_argument("source", choices=list(RAW_SOURCES))
    w.add_argument("--query", default="Golang")
    w.add_argument("--days", type=int, default=3)
    w.add_argument("--area", default="113")
    w.add_argument("--out")
    w.add_argument("--render", action="store_true",
                   help="забрать страницу браузером (SPA: geekjob, hirehi, shadowhint)")
    w.set_defaults(func=cmd_raw)

    a = sub.add_parser("auth", help="сессии площадок в .auth/ (вход делает пользователь); "
                                    "import — забрать куки из браузеров в единый профиль",
                       parents=[common])
    a.add_argument("action", choices=["status", "login", "check", "import", "secure"],
                   nargs="?", default="status")
    a.add_argument("platform", nargs="?")
    a.add_argument("--all", action="store_true",
                   help="login: одно окно с вкладкой на каждую площадку")
    a.add_argument("--from", dest="from_", choices=["yandex", "chrome", "claude", "all"],
                   default="all", help="import: из какого браузера брать куки")
    a.add_argument("--domains", nargs="*",
                   help="import: домены площадок поимённо (по умолчанию встроенный "
                        "allowlist; `*` не поддерживается)")
    a.add_argument("--list", action="store_true",
                   help="import: показать домены и число кук, не записывая")
    a.set_defaults(func=cmd_auth)

    m = sub.add_parser("mark", help="зафиксировать решение по вакансии", parents=[common])
    m.add_argument("source")
    m.add_argument("id")
    m.add_argument("--state", required=True,
                   choices=["applied", "rejected", "skipped", "skip", "shortlist", "interview"],
                   help="skip — синоним skipped")
    m.add_argument("--note")
    m.set_defaults(func=cmd_mark)

    st = sub.add_parser("status", help="поиск по базе (title+company) с показом решений",
                        parents=[common])
    st.add_argument("--query", required=True, help="подстрока названия или компании")
    st.add_argument("--limit", type=int, default=50)
    st.set_defaults(func=cmd_status)

    # ── ats: порт бывших scripts/ats/*.sh ────────────────────────────────────
    at = sub.add_parser("ats", help="доски работодателей: check / jobs / sniff",
                        parents=[common])
    ats_sub = at.add_subparsers(dest="ats_cmd", required=True)

    ac = ats_sub.add_parser("check", help="живость токена на всех ATS сразу, с названием компании")
    ac.add_argument("tokens", nargs="+")
    ac.set_defaults(func=cmd_ats)

    aj = ats_sub.add_parser("jobs", help="вакансии доски со структурным матчем локаций "
                                         "(secondaryLocations, offices, заголовок)")
    aj.add_argument("board", help="<ats>:<token>, например greenhouse:gitlab или ashby:ruby-labs")
    aj.add_argument("--country", help="код страны (TR, RU, CY, …) или свободный текст")
    aj.add_argument("--grep", help="регулярка по заголовку роли")
    aj.add_argument("--locations", action="store_true",
                    help="показать распределение локаций доски")
    aj.set_defaults(func=cmd_ats)

    an = ats_sub.add_parser("sniff", help="вычислить ATS по careers-странице компании")
    an.add_argument("urls", nargs="+")
    an.set_defaults(func=cmd_ats)

    # ── detail / enrich ──────────────────────────────────────────────────────
    d = sub.add_parser("detail", help="нормализованная выжимка страницы вакансии",
                       parents=[common])
    d.add_argument("url")
    d.add_argument("--json", dest="format", action="store_const", const="json",
                   default="text")
    d.add_argument("--render", action="store_true",
                   help="брать HTML из браузера (SPA); только для generic-случаев — "
                        "для hh/habr/ATS это будет сказано в notes")
    cookiesrc.add_cookie_args(d)
    d.set_defaults(func=cmd_detail)

    e = sub.add_parser("enrich", help="выжимки по дельте из базы, с хранением "
                                      "(второй раз не качает)", parents=[common])
    e.add_argument("--since", default="3d", help="окно дельты: 3d, 12h, ISO")
    e.add_argument("--source", help="через запятую: hh,habr,…")
    e.add_argument("--max", type=int, default=DEFAULT_MAX_ENRICH,
                   help=f"потолок выжимок за прогон (по умолчанию "
                        f"{DEFAULT_MAX_ENRICH}; 0 — без потолка). Отрезанное "
                        f"не теряется: выжимки кэшируются в базе")
    e.add_argument("--workers", type=int, default=8)
    e.add_argument("--refresh", action="store_true", help="перекачать уже обогащённые")
    e.add_argument("--include-decided", action="store_true")
    e.set_defaults(func=cmd_enrich)

    # ── tg / check-links ─────────────────────────────────────────────────────
    t = sub.add_parser("tg", help="разбор телеграм-дампа: счётчики, теги, кандидаты",
                       parents=[common])
    t.add_argument("file")
    t.add_argument("--since", help="ISO-дата: сообщения старше — только в счётчик")
    t.add_argument("--full", action="store_true", help="тела целиком, а не первые ~15 строк")
    t.set_defaults(func=cmd_tg)

    cl = sub.add_parser("check-links", help="предфлайт живости ATS-ссылок "
                                            "(Ashby ротирует UUID!)", parents=[common])
    cl.add_argument("urls", nargs="+")
    cl.set_defaults(func=cmd_check_links)

    # ── tg-auth / tg-fetch: Telegram-архив без MCP ───────────────────────────
    ta = sub.add_parser("tg-auth", help="сессия Telegram (Telethon; вход делает "
                                        "пользователь в терминале)", parents=[common])
    ta.add_argument("action", choices=["login", "status"], nargs="?", default="status")
    ta.set_defaults(func=cmd_tg_auth)

    tf = sub.add_parser("tg-fetch", help="выкачать непрочитанное из архива Telegram "
                                         "и прогнать парсер tg", parents=[common])
    tf.add_argument("--archive-only", action="store_true", default=True,
                    help="только архивные диалоги (так по умолчанию)")
    tf.add_argument("--all-folders", dest="archive_only", action="store_false",
                    help="обойти и основную папку тоже")
    tf.add_argument("--no-mark", action="store_true", help="не отмечать прочитанным")
    tf.add_argument("--out", help="куда класть дампы (по умолчанию .scout/tg/<дата>)")
    tf.set_defaults(func=cmd_tg_fetch)

    td = sub.add_parser("tg-dm", help="личная переписка с человеком: последние N "
                                      "сообщений (только чтение, БЕЗ отметки "
                                      "прочитанным)", parents=[common])
    td.add_argument("peer", help="@ник, ник или числовой id собеседника")
    td.add_argument("--limit", type=int, default=50,
                    help="сколько последних сообщений показать (по умолчанию 50)")
    td.set_defaults(func=cmd_tg_dm)

    # ── render / hh-sync / mail-sync / scan ──────────────────────────────────
    rn = sub.add_parser("render", help="страница через браузер: SPA и авторизованные "
                                       "площадки (exness, wantapply, ecom.tech)",
                        parents=[common])
    rn.add_argument("url")
    rn.add_argument("--session", help="оверрайд: отдельная сессия площадки из .auth/ "
                                      "(hh, shadowhint, …); по умолчанию единый профиль")
    rn.add_argument("--session-file", help="оверрайд: конкретный storage_state-файл "
                                           "(например .auth/browser.json)")
    rn.add_argument("--wait", type=float, default=3.0,
                    help="секунды ожидания после networkidle (SPA дорисовываются)")
    rn.add_argument("--html", dest="mode", action="store_const", const="html",
                    default="text", help="сырой HTML вместо чистого текста")
    rn.add_argument("--text", dest="mode", action="store_const", const="text",
                    help="чистый текст (по умолчанию)")
    cookiesrc.add_cookie_args(rn)
    rn.set_defaults(func=cmd_render)

    br = sub.add_parser("browse", help="видимое окно с куками пользователя для "
                                       "ручного дебага (только чтение)", parents=[common])
    br.add_argument("url")
    br.add_argument("--keep", action="store_true",
                    help="держать окно открытым до Enter")
    br.add_argument("--wait", type=float, default=3.0)
    cookiesrc.add_cookie_args(br)
    br.set_defaults(func=cmd_browse)

    hs = sub.add_parser("hh-sync", help="статусы откликов из кабинета hh "
                                        "(отказы/приглашения) → таблица negotiation",
                        parents=[common])
    hs.add_argument("--max-pages", type=int, default=25)
    cookiesrc.add_cookie_args(hs)
    hs.set_defaults(func=cmd_hh_sync)

    ms = sub.add_parser("mail-sync", help="статусы откликов из почты "
                                          "(IMAP, только чтение)", parents=[common])
    ms.add_argument("--days", type=int, default=30, help="окно поиска писем")
    ms.set_defaults(func=cmd_mail_sync)

    mi = sub.add_parser("mail-ingest", help="принять JSON-выгрузку писем (Gmail MCP) → "
                                            "классификатор → таблица статусов",
                        parents=[common])
    mi.add_argument("file", help="путь к JSON-файлу или '-' для stdin")
    mi.set_defaults(func=cmd_mail_ingest)

    sc = sub.add_parser("scan", help="весь конвейер одной командой: collect → tg → "
                                     "enrich → hh-sync → mail-sync → сводный отчёт",
                        parents=[common])
    sc.add_argument("--days", type=int, default=3, help="окно свежести площадок и дельты")
    sc.add_argument("--no-telegram", action="store_true")
    sc.add_argument("--no-mail", action="store_true")
    sc.add_argument("--no-hh", action="store_true")
    sc.add_argument("--no-browser", action="store_true",
                    help="без площадок, которым нужен браузер; в покрытии они "
                         "останутся строкой «ПРОПУЩЕН»")
    sc.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"нижняя граница глубины обхода площадки (по умолчанию "
                         f"{DEFAULT_LIMIT}; 0 — сколько отдаёт площадка)")
    sc.add_argument("--max-enrich", type=int, default=DEFAULT_MAX_ENRICH,
                    help=f"потолок выжимок за прогон (по умолчанию "
                         f"{DEFAULT_MAX_ENRICH}; 0 — без потолка). Кэшируются: "
                         f"отрезанное достанется следующему прогону")
    sc.add_argument("--enrich-workers", type=int, default=8,
                    help="потоки выжимок; пауза между запросами к ОДНОМУ хосту "
                         "держится независимо от их числа")
    sc.add_argument("--report-rows", type=int, default=0,
                    help="строк в таблице дельты отчёта (0 — вся дельта, так "
                         "по умолчанию); при обрезании это сказано в отчёте")
    sc.add_argument("--mail-days", type=int, default=30,
                    help="окно почты (шире окна площадок: ответы приходят с лагом)")
    cookiesrc.add_cookie_args(sc)
    sc.set_defaults(func=cmd_scan)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
