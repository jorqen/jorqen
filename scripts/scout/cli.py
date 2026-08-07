"""CLI сборщика.

    python3 -m scripts.scout collect            # обойти все площадки, сложить в базу
    python3 -m scripts.scout new --since 3d     # что появилось с прошлого раза
    python3 -m scripts.scout coverage           # кто отработал, кто упал — за последний прогон
    python3 -m scripts.scout resolve <url>      # куда на самом деле ведёт «Откликнуться»
    python3 -m scripts.scout reveal <url>       # прямой контакт hirehi (СПИСЫВАЕТ лимит)
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
# HostPacer переехал в net.py 07.08.2026: он про сетевую вежливость, а не про
# командную строку, и держать его здесь значило замыкать цикл импортов, как
# только команды разъезжаются по модулям. Реэкспорт — чтобы прежние импорты жили.
from .net import BlockedError, FetchError, HostPacer, parallel  # noqa: F401
from . import cookiesrc
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
                no_browser: bool = False, args_dict: dict | None = None,
                raw_cache: str | None = None) -> dict:
    """Ядро collect, отделённое от печати: им пользуется и `collect`, и `scan`.
    Возвращает {report, vacancies, new, updated, elapsed}.

    `raw_cache`: None или 'off' — не кэшировать; 'write' — ходить в сеть и складывать
    ответы; 'read' — брать из кэша всё, что там есть за сегодня. Режим 'read'
    существует для отладки парсеров: прогон по уже скачанному не стоит ни
    одного запроса к площадке.
    """
    started = time.time()
    timings: dict[str, int] = {}
    cache = None
    if raw_cache and raw_cache != "off":
        from . import net as _net, rawcache  # noqa: PLC0415

        cache = rawcache.Cache(db, read=(raw_cache == "read"), write=True)
        _net.set_cache(cache)

    def wrap(name):
        def run():
            t0 = time.time()
            # Имя источника для кэша сырых ответов — по потоку: обход идёт
            # в пуле, и общий на процесс «текущий источник» перепутал бы
            # страницы площадок между собой.
            from . import rawcache  # noqa: PLC0415
            rawcache.set_source(name)
            # Тот же довод, что у кэша сырья: воркер переиспользуется, и без
            # сброса следующий источник унаследовал бы вежливость предыдущего —
            # заплатил бы паузу до своего ПЕРВОГО запроса к чужой площадке.
            from .sources import reset_pace  # noqa: PLC0415
            reset_pace()
            try:
                return SOURCES[name](ctx)
            finally:
                timings[name] = int((time.time() - t0) * 1000)
                rawcache.set_source(None)
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
            # Сколько площадка ОТДАЛА до нашего фильтра профессии. Нужно
            # здоровью источников: без этого «ноль» одинаково читается у мёртвой
            # площадки и у живой, чья выдача целиком чужой профессии, — и вторая
            # получала ярлык «АВАРИЯ … это не „вакансий нет“, а поломка».
            offered = next((int((v.raw or {}).get("offered") or 0)
                            for v in payload if v.external_id == "_summary"), 0)
            report.append({"source": name, "status": "ok", "found": found,
                           "offered": offered,
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
    degraded: list[dict] = []
    checked = 0
    if not no_store and run_id is not None:
        from . import health  # noqa: PLC0415

        with store.connect(db) as conn:
            new, updated = store.upsert(conn, all_vacancies)
            # Сверка с историей — ДО записи свежих счётчиков: иначе текущий
            # прогон попадёт в собственную базу сравнения и разбавит медиану
            # ровно тем числом, которое мы проверяем.
            degraded = health.assess(conn, run_id, report)
            checked = len(health.history(conn, run_id))
            for r in report:
                store.record_source(conn, run_id, r["source"], r["status"],
                                    found=r["found"], error=r["error"],
                                    elapsed_ms=r["elapsed_ms"])
            store.finish_run(conn, run_id)

    if cache is not None:
        from . import net as _net  # noqa: PLC0415

        _net.set_cache(None)      # шов процессный — снимаем за собой

    # Служебная сводка ATS — не вакансия: в «найдено» она попадать не должна.
    total = sum(1 for v in all_vacancies if v.external_id != "_summary")
    return {"report": report, "vacancies": all_vacancies, "new": new,
            "updated": updated, "elapsed": time.time() - started, "total": total,
            "limit": ctx.limit, "health": degraded, "health_checked": checked,
            "cache": cache.line() if cache is not None else None}


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
                      args_dict=vars(args),
                      raw_cache=getattr(args, "raw_cache", "write"))
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
                        subset=bool(args.sources), limit=ctx.limit,
                        health_rows=res.get("health"),
                        health_checked=res.get("health_checked", 0),
                        cache_line=res.get("cache"))

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
                    limit: int | None = None, health_rows: list[dict] | None = None,
                    health_checked: int = 0, cache_line: str | None = None) -> None:
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

    # Сверка с прошлыми прогонами. Статус `ok` говорит только «площадка ответила»;
    # упавшая втрое выдача при живом парсере выглядит точно так же, и заметить её
    # можно ровно здесь.
    if health_rows is not None:
        from . import health  # noqa: PLC0415

        print(health.render(health_rows, checked=health_checked))
    if cache_line:
        print(f"\n💾 {cache_line}")

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
    since = store.since_arg(args.since, db=args.db)
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
        # Окно по датам не видит строк, у которых дат нет вовсе, — их надо
        # объявить, иначе источник целиком выпадает из выборки молча.
        undated = store.count_undated(conn, sources=kw["sources"],
                                      exclude_decided=kw["exclude_decided"]) \
            if args.by == "published" and since else {}
    truncated = max(0, total - len(rows))
    undated_note = ""
    if undated:
        per_src = ", ".join(f"{s}: {n}" for s, n in sorted(undated.items()))
        undated_note = (f"⚠️  ВНЕ ОКНА, ПОТОМУ ЧТО ДАТ НЕТ ВООБЩЕ: "
                        f"{sum(undated.values())} строк ({per_src}) — у площадки нет "
                        f"ни даты публикации, ни обновления, окно --since к ним "
                        f"неприменимо. Смотреть их: `new --by seen --sources <источник>`.")

    if args.format == "json":
        # JSON — объект с метаданными, а не голый массив: в массиве усечение
        # выразить нечем, и потребитель его не заметит.
        print(json.dumps({"since": since, "total": total, "shown": len(rows),
                          "truncated": truncated, "limit": args.limit,
                          "undated": undated,
                          "items": rows}, ensure_ascii=False, indent=2, default=str))
        return 0
    if not rows:
        print("Ничего нового в окне.")
        if undated_note:
            print("\n" + undated_note)
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
    if undated_note:
        print("\n" + undated_note)
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


def cmd_reveal(args) -> int:
    """Раскрытие прямого контакта hirehi. СПИСЫВАЕТ лимит раскрытий пользователя —
    разрешение на это дано им 30.07.2026 (релевантные вакансии, идемпотентно,
    лишний раз не нажимать). Механика и все предохранители — в reveal.py."""
    from .reveal import reveal
    return reveal(args.urls, limit=args.limit, db=args.db,
                  from_browser=(getattr(args, "cookies_from", None) or "auto")
                  if args.from_browser else None)


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
        # browser обязан доехать: без него `--browser chrome` молча уходил в
        # встроенный chromium, то есть человек входил в одно окно, а сессия
        # ложилась разовым слепком вместо постоянного профиля.
        return auth.login(args.platform, wait=args.wait,
                          browser=getattr(args, "browser", None))
    if args.action == "check":
        return auth.check([args.platform] if args.platform else None)
    if args.action == "refresh":
        from . import authrefresh
        return authrefresh.renew([args.platform] if args.platform else None,
                                 browser=getattr(args, "browser", None),
                                 from_browser=args.from_browser)
    if args.action == "push-browser":
        from . import cookiepush
        if not args.platform:
            print("укажи площадку: scout auth push-browser hirehi [--from yandex]",
                  file=sys.stderr)
            return 2
        src = args.from_ if args.from_ in ("yandex", "chrome", "claude") else "yandex"
        return cookiepush.push(args.platform, src)
    if args.action == "secure":
        fixed = auth.secure_auth_dir()
        print("\n".join(f"  починено: {f}" for f in fixed)
              if fixed else "  права уже 0600 у всего содержимого .auth/")
        return 0
    return auth.status()


def cmd_tg_rollback(args) -> int:
    """Откат прочитанности Telegram: переиграть прогон с той же точки."""
    from . import tgclient
    return tgclient.cmd_rollback(args.file, apply=args.apply, db=args.db,
                                 force=args.force)


def cmd_wave(args) -> int:
    """Весь конвейер + картина волны + блок «следующий шаг»."""
    from . import wave
    return wave.cli(args)


def cmd_tg_reparse(args) -> int:
    """Пересчитать телеграм-вакансии по сохранённому тексту поста, без сети."""
    from . import store as _store, tgvacancy

    with _store.connect(args.db) as conn:
        seen, changed, examples = tgvacancy.reparse_stored(conn, apply=args.apply)
    print(f"# tg-reparse: просмотрено {seen}, изменилось бы {changed}"
          if not args.apply else
          f"# tg-reparse: просмотрено {seen}, обновлено {changed}")
    for e in examples:
        print(f"  {e}")
    if not args.apply and changed:
        print("\nЭто предпросмотр. Применить: `scout tg-reparse --apply`")
    return 0


def cmd_tg_mirror(args) -> int:
    """Пересылка постов вакансий в свой приватный канал (только forward)."""
    from . import tgmirror
    return tgmirror.cli(args)


def cmd_card(args) -> int:
    """Скелет карточки: всё, кроме фита и письма — их пишет модель."""
    if getattr(args, "write", False):
        from . import cardfiles
        return cardfiles.cli_write(args)
    from . import card
    return card.cli(args)


def cmd_lint_cards(args) -> int:
    """Формальная проверка готовых карточек волны."""
    from . import cardfiles
    return cardfiles.cli_lint(args)


def cmd_research(args) -> int:
    """Кэш вердиктов ресёрча: записать выясненное, чтобы не выяснять снова."""
    from . import store as _store

    with _store.connect(args.db) as conn:
        row = conn.execute("SELECT source, external_id FROM vacancy WHERE url=?",
                           (args.url,)).fetchone()
        if row is None:
            print(f"нет в базе: {args.url}", file=sys.stderr)
            return 1
        if args.action == "get":
            got = _store.research(conn, row["source"], row["external_id"])
            if not got:
                print("ресёрча по этой вакансии ещё не было")
                return 1
            for k, v in got.items():
                if v:
                    print(f"  {k}: {v}")
            return 0
        _store.save_research(conn, row["source"], row["external_id"],
                             employer_revealed=args.employer, liveness=args.liveness,
                             rtw=args.rtw, verdict=args.verdict,
                             evidence=args.evidence)
    print(f"записано: {row['source']}:{row['external_id']}")
    return 0


def cmd_lint_letter(args) -> int:
    from . import lintletter
    return lintletter.cli(args)


def cmd_wavedoc(args) -> int:
    from . import wavedoc
    return wavedoc.cli(args)


def cmd_budget(args) -> int:
    """Смета волны до её начала — механизм, которым держится потолок 500K."""
    from . import budget
    return budget.cli(args)


def cmd_channel(args) -> int:
    """Прямой канал найма зондированием доменов — вместо подагентов-искателей."""
    from . import channel
    return channel.cli(args)


def cmd_brief(args) -> int:
    """Всё про вакансию одним вызовом: выжимка + история компании + канал найма."""
    from . import brief
    return brief.cli(args)


def cmd_shortlist(args) -> int:
    """Дельта, свёрнутая до строки на вакансию. Заменяет вычитку отчёта агентами:
    дедуп, сверка с историей и разбор требуемого стажа делаются кодом."""
    from . import shortlist
    return shortlist.cli(args)


def cmd_profile(args) -> int:
    """Спрос рынка против того, что подтверждает резюме. Считается по своей же
    базе: чинит и точность отбора (что спросить у пользователя), и конверсию
    (что в резюме не подтверждено ничем)."""
    from . import profile, store
    with store.connect(args.db) as conn:
        days = None if args.all else args.days
        print(profile.build(conn, days=days, top=args.top,
                            min_companies=args.min_companies))
    return 0


def cmd_employer(args) -> int:
    """Кэш прямых каналов найма: найденное однажды не ищется каждым прогоном."""
    from . import shortlist, store
    from datetime import datetime, timezone
    with store.connect(args.db) as conn:
        if args.action == "list":
            cur = conn.execute("SELECT company, channel, kind, checked_at "
                               "FROM employer_channel ORDER BY company")
            rows = cur.fetchall()
            if not rows:
                print("кэш пуст — заполняется командой `employer set`")
                return 1
            for company, channel, kind, checked in rows:
                print(f"{company}\t{kind or '—'}\t{channel}\t{checked[:10]}")
            return 0
        if args.action == "get":
            key = shortlist.norm(args.company)
            cur = conn.execute("SELECT company, channel, kind, evidence, checked_at "
                               "FROM employer_channel WHERE company_key = ?", (key,))
            row = cur.fetchone()
            if not row:
                print(f"нет в кэше: {args.company}", file=sys.stderr)
                return 1
            print(f"{row[0]}\n  канал: {row[2] or '—'} {row[1]}\n  "
                  f"подтверждение: {row[3] or '—'}\n  проверено: {row[4][:10]}")
            return 0
        if not args.company or not args.channel:
            print("нужны компания и канал: employer set <компания> <url|почта|@ник> "
                  "[--kind careers|ats|email|telegram|none] [--evidence …]",
                  file=sys.stderr)
            return 2
        conn.execute(
            "INSERT INTO employer_channel (company_key, company, channel, kind, "
            "evidence, checked_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(company_key) DO UPDATE SET channel=excluded.channel, "
            "kind=excluded.kind, evidence=excluded.evidence, "
            "checked_at=excluded.checked_at",
            (shortlist.norm(args.company), args.company, args.channel, args.kind,
             args.evidence, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        conn.commit()
        print(f"запомнено: {args.company} → {args.channel}")
    return 0


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
        # Движки с НАСТОЯЩИМ серверным поиском — им grep прокидывается текстом,
        # и он сужает саму выдачу, а не только печать. У workable иначе большую
        # доску не получить вовсе; у workday это единственный способ добраться до
        # своих вакансий, когда их 2000, а обход упирается в потолок в 500.
        query = re.sub(r"[^\w\s-]", " ", args.grep).strip() if (
            ats in ("workable", "workday") and args.grep) else None
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
# detail / enrich — переехали в clidetail.py
# ──────────────────────────────────────────────────────────────────────────────
# enrich_summary тоже реэкспортируется: им пользуются и `scan`, и тесты —
# сводка обогащения одна на весь сборщик, второй такой быть не должно.
from .clidetail import (  # noqa: F401 — реэкспорт
    DEFAULT_MAX_ENRICH, ENRICH_PACE, _PROFILE_RE, _by_relevance, _enrich_rank,
    cmd_detail, cmd_enrich, enrich_max, enrich_summary, run_enrich,
)


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
    if getattr(args, "save", False):
        print(_tg_save_dump(args.file, text, args.db))
    # Сверка полноты: заголовков в файле должно быть ровно столько, сколько сообщений
    # (плюс, возможно, одно синтетическое «до первого заголовка» — оно с id «?»).
    headers = len(re.findall(r"^\[#\d+\] \[\d{4}-\d{2}-\d{2}T", text, re.M))
    synthetic = 1 if "(до первого заголовка)" in report else 0
    if headers != counters["total"] - synthetic:
        print(f"\n⚠️  РАСХОЖДЕНИЕ: заголовков в файле {headers}, разобрано "
              f"{counters['total']} — парсер потерял сообщения, это баг, чини tg.py")
        return 1
    return 0


def _tg_save_dump(path: str, text: str, db: str) -> str:
    """Разобрать УЖЕ ЛЕЖАЩИЙ дамп в вакансии. Возвращает строку отчёта.

    Нужно для переразбора старых дампов после правки парсера — без похода
    в Telegram и без сдвига водяного знака (знак ведёт только `tg-fetch`:
    сдвинуть его отсюда значило бы объявить разобранным то, чего мы в этом
    прогоне не выкачивали).

    Идентичность чата берётся из ИМЕНИ файла (`<название>-<dialog.id>.txt`) —
    другого источника у отдельного дампа нет. Публичного ника в имени нет,
    поэтому ссылки выйдут в форме `t.me/c/<id>/<N>`: они открываются только
    у участника канала. Это честное ограничение офлайн-разбора, а `tg-fetch`
    ник знает и ставит нормальные ссылки.
    """
    from . import store, tgvacancy
    from .tg import classify as tg_classify, parse_dump

    name = os.path.basename(path)
    m = re.match(r"(.+?)-{1,2}(\d+)\.txt$", name)
    title, chat_id = (m.group(1), m.group(2)) if m else (name, "")
    if not chat_id:
        return ("\n⚠️  --save пропущен: из имени файла не вычленяется id чата "
                "(ожидаю «<название>-<id>.txt»), а без него ссылка на пост "
                "не собирается — записать вакансию было бы враньём про её адрес.")
    msgs = parse_dump(text)
    for msg in msgs:
        tg_classify(msg)
    vacancies, st = tgvacancy.from_dump(
        msgs, tgvacancy.ChatRef(chat_id=chat_id, title=title))
    with store.connect(db) as conn:
        new, upd = store.upsert(conn, vacancies)
    out = [f"\n--save: {st.line()}; в базу новых {new}, обновлено {upd} "
           f"(источник tg:{chat_id}; водяной знак НЕ сдвинут)"]
    for ex in st.examples[:5]:
        out.append(f"    отсев: {ex[:110]}")
    return "\n".join(out)


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
            host = urllib.parse.urlsplit(url).netloc.lower()
            if host == "wantapply.com" or host.endswith(".wantapply.com"):
                # У wantapply даты — время их краулера; живость отвечает только
                # их API (status/statusChangedAt). Карточка SteelMount была
                # написана по вакансии, снятой десятью месяцами раньше.
                from .sources_auth import wantapply_check
                row = conn.execute(
                    "SELECT external_id FROM vacancy "
                    "WHERE source='wantapply' AND url=? LIMIT 1", (url,)).fetchone()
                verdict, why = wantapply_check(
                    url, external_id=row["external_id"] if row else None)
                if verdict == "ЖИВА":
                    print(f"✓  ЖИВА  {url}\n   {why}")
                elif verdict == "МЕРТВА":
                    print(f"✗  МЕРТВА  {url}\n   {why}")
                    exit_code = 1
                else:
                    print(f"?  {url}\n   {why}")
                    exit_code = 1
                continue
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


def tg_fetch_flow(out_dir: str, *, archive_only: bool = True, mark: bool = True,
                  db: str | None = None) -> tuple[int, list[dict]]:
    """Обход архива → дампы → ВАКАНСИИ В БАЗЕ → водяной знак.

    Ключевое отличие от прежней версии: посты не остаются дампами, а становятся
    строками `vacancy` (`tg:<канал>`). Пока их там не было, `shortlist` телеграм
    не видел, и единственным способом отобрать из него что-либо было чтение
    дампов моделью — 3,3 МБ текста и главная статья расходов прогона 04.08.2026.

    Разбор и запись идут ПО ЧАТУ, в колбэке `on_chat`, а не общим проходом
    в конце: прогон, упавший на пятнадцатом чате, обязан сохранить четырнадцать
    разобранных вместе с их водяными знаками. Возвращает (код, кандидаты).
    """
    from . import store, tgclient, tgvacancy
    from .tg import classify as tg_classify, parse_dump

    db = db or store.DEFAULT_DB
    candidates: list[dict] = []
    totals = tgvacancy.ParseStats()
    saved = {"new": 0, "updated": 0}
    per_chat: list[str] = []

    with store.connect(db) as conn:
        marks = store.tg_watermarks(conn)

    def on_chat(cr) -> None:
        """Разобрать дамп чата, сложить вакансии и сдвинуть знак — атомарно."""
        msgs = []
        if cr.dump_path:
            with open(cr.dump_path, encoding="utf-8") as f:
                text = f.read()
            msgs = parse_dump(text)
            for m in msgs:
                tg_classify(m)
        chat = tgvacancy.ChatRef(chat_id=cr.chat_id, title=cr.title,
                                 username=cr.username)
        vacancies, st = tgvacancy.from_dump(msgs, chat)
        with store.connect(db) as conn:
            new, upd = store.upsert(conn, vacancies)
            # Знак двигаем в ТОЙ ЖЕ транзакции, что и вакансии: иначе падение
            # между двумя коммитами оставило бы сдвинутый знак без вакансий —
            # то есть тихую потерю ровно того сорта, от которого этот код и есть.
            if cr.last_id > cr.watermark:
                store.set_tg_watermark(conn, cr.chat_id, cr.last_id,
                                       chat_title=cr.title, username=cr.username)
        saved["new"] += new
        saved["updated"] += upd
        totals.merge(st)
        per_chat.append(
            f"  {cr.title[:34]:<34} {st.messages:>4} сообщ. → {st.vacancies:>4} вакансий, "
            f"новых {new}, знак {cr.watermark}→{cr.last_id}"
            + ("  [ЗАГРУЗКА С НУЛЯ: знака не было]" if cr.bootstrapped else "")
            + ("  [ОБРЕЗАН — знак не сдвинут]" if cr.truncated else ""))
        for m in msgs:
            if m.category == "candidate":
                candidates.append({"chat": cr.title, "id": m.id, "date": m.date,
                                   "body": m.body})

    summary = tgclient.fetch(out_dir, archive_only=archive_only, mark=mark,
                             watermarks=marks, on_chat=on_chat)

    print(f"# tg-fetch: чатов с новым {summary.visited}, уже на знаке "
          f"{summary.up_to_date}, дампов {summary.dumped}, отмечено прочитанным "
          f"{summary.marked}, упало {summary.failed}"
          + (f", первая загрузка {summary.bootstrapped}" if summary.bootstrapped else ""))
    print(f"# в базу: вакансий новых {saved['new']}, обновлено {saved['updated']}; "
          f"{totals.line()}")
    for line in per_chat:
        print(line)
    for cr in summary.chats:
        if cr.error:
            print(f"  УПАЛ  {cr.title[:40]:<40} {cr.error[:70]}")
    if not summary.visited:
        print("  нового после водяного знака нет")
    # Отсев печатается ВСЕГДА и с примерами: «не вакансий 641» без единого
    # примера — это цифра, которую нечем проверить.
    if totals.examples:
        print("  примеры отсеянного (в базу не пошли, в дампе остались):")
        for ex in totals.examples[:8]:
            print(f"    {ex[:110]}")
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
                                     mark=not args.no_mark, db=args.db)
    print(f"\nИтого кандидатов из Telegram: {len(candidates)}")
    print("Вакансии уже в базе — смотреть их `scout shortlist`, "
          "дампы для отбора читать не нужно.")
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

def cmd_hh_auth(args) -> int:
    """Один поход за пользовательским токеном API hh. Токен обновляется сам,
    сюда возвращаются только когда пара окончательно умерла."""
    from . import hhapi

    if args.action == "status":
        t = hhapi.read_token()
        if not hhapi.configured():
            print(f"ключей приложения нет: {hhapi.ENV_PATH}")
            return 1
        if not t:
            print(f"токена нет ({hhapi.TOKEN_PATH}). `scout hh-auth login`")
            return 1
        left = float(t.get("expires_at") or 0) - time.time()
        print(f"токен есть: живёт ещё {left / 86400:.1f} сут"
              if left > 0 else "токен истёк, обновится сам при первом запросе")
        print(f"refresh_token: {'есть' if t.get('refresh_token') else 'НЕТ'}")
        return 0

    data = hhapi.login(visible=args.visible, confirm=not args.no_confirm,
                       cookies_from=getattr(args, "cookies_from", None),
                       use_cache=getattr(args, "cache", False))
    left = float(data.get("expires_at") or 0) - time.time()
    print(f"токен получен, {hhapi.TOKEN_PATH}, живёт {left / 86400:.1f} сут")
    return 0


def cmd_hh_sync(args) -> int:
    from . import hhsync
    return hhsync.sync(args.db, max_pages=args.max_pages,
                       cookies_from=getattr(args, "cookies_from", None),
                       use_cache=getattr(args, "cache", False))


def cmd_habr_sync(args) -> int:
    from . import habrsync
    return habrsync.sync(args.db, max_pages=args.max_pages,
                         cookies_from=getattr(args, "cookies_from", None),
                         use_cache=getattr(args, "cache", False))


def cmd_mail_sync(args) -> int:
    from . import mailsync
    return mailsync.sync(args.db, days=args.days)


def cmd_mail_read(args) -> int:
    from . import mailsync
    return mailsync.read_mail(args.query, days=args.days, limit=args.limit)


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


def _tg_summary(text: str, head_lines: int = 60) -> str:
    """Шапка телеграм-этапа + путь к дампам вместо их полного содержимого.

    Дампы уже сохранены файлами (`.scout/tg/<дата>/<чат>.txt`) — дублировать их
    в отчёте значит удваивать хранение и заставлять читателя платить за разбор
    того же текста второй раз. Полный разбор чата: `scout tg <файл> --full`."""
    lines = text.splitlines()
    head = [ln for ln in lines[:head_lines]]
    paths = [ln.split("→", 1)[1].strip() for ln in lines if "→" in ln and ".txt" in ln]
    tail = [
        "",
        f"_Полные дампы не встроены в отчёт: {len(paths)} файлов в `.scout/tg/`._",
        "_Разбор конкретного чата: `scout tg <путь к дампу> --full`;_",
        "_кандидаты в машинном виде: `scout shortlist --since <окно>`._",
    ]
    if len(lines) > head_lines:
        tail.insert(1, f"_(показана шапка: {head_lines} строк из {len(lines)})_")
    return "\n".join(head + tail)


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
    # Шапка-предупреждение — ПЕРВОЙ строкой файла, до заголовка. Отчёт нужен для
    # покрытия и стен, а не для отбора: кандидаты лежат в базе, и `shortlist`
    # отдаёт их строкой на вакансию. Прогон 04.08.2026 стоил 5,6 млн токенов
    # ровно потому, что этот файл (2,8 МБ) читали целиком и подагентами.
    out = ["> 🔴 НЕ ЧИТАЙ ЭТОТ ФАЙЛ ЦЕЛИКОМ — он для покрытия и стен.",
           "> Кандидаты: `scout shortlist`. Досье по вакансии: `scout brief <url>`.",
           "> Скелет карточки: `scout card <url>`. Смета волны: `scout budget`.",
           "",
           f"# scout scan — {generated_at[:10]}", "",
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
    # Здоровье источников — прямо в покрытии, а не только в stdout: `wave`
    # прячет вывод скана в буфер, и строка про деградацию до человека не доезжала.
    # Статус у деградировавшей площадки остаётся `ok`, поэтому в таблице выше
    # её ничем не отличить.
    bad = collect.get("health") or []
    if bad:
        out += ["", "🔴 **ЗДОРОВЬЕ ИСТОЧНИКОВ** — сверка с медианой прошлых прогонов:"]
        out += [f"- **{b['label']}** `{b['source']}` — {b['why']}" for b in bad]
        out.append("Статус у них «ok»: площадка ответила, но отдала не то, что обычно. "
                   "Пока это не починено, выдача волны неполная.")
    stage_rows = (("telegram", "telegram-архив", "candidates"),
                  ("enrich", "enrich дельты", "ok"),
                  ("hh", "hh-sync (кабинет hh)", "found"),
                  ("habr_sync", "habr-sync (Хабр Карьера)", "found"),
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
        # ВЕСЬ текст дампов сюда больше не кладём. Живой прогон 04.08.2026: эта
        # секция занимала 1,47 МБ из 2,8 МБ отчёта, то есть отчёт превращался
        # в копию того, что уже лежит файлами в .scout/tg/<дата>/. Модель потом
        # платила за вычитку этой копии подагентами. Здесь остаётся шапка со
        # счётчиками и путями, а полный текст берётся из файла по надобности.
        out.append(_tg_summary(tg["text"]))
    else:
        out.append(f"Этап не дал текста: {_STAGE_MARK.get(tg.get('status'), '—')}"
                   + (f" — {tg.get('note') or tg.get('error')}" if (tg.get('note') or tg.get('error')) else ""))

    # ── Дельта площадок: СНАЧАЛА таблица всего, потом выжимки верхних ─────
    en = stages.get("enrich") or {}
    out += ["", "## Дельта площадок — все вакансии", ""]
    out += _delta_table(delta_rows if delta_rows is not None else (en.get("rows") or []),
                        limit=report_rows, days=days)

    # Дайджесты enrich из отчёта УБРАНЫ намеренно. Они занимали большую часть
    # файла (2,8 МБ на прогоне 04.08.2026), дублировали то, что и так лежит
    # в таблице `detail`, и провоцировали читать отчёт целиком — а это и была
    # главная статья расходов. Тот же текст по требованию отдаёт `scout brief
    # <url>`, по одной вакансии и ровно тогда, когда он нужен.
    out += ["", "## Выжимки (enrich)", ""]
    if en.get("status") == "ok":
        out.append(enrich_summary(en))
    out.append("Тексты выжимок в отчёт не пишутся: они есть в базе, и `scout brief "
               "<url>` отдаёт их по одной вакансии. Раньше они занимали бо́льшую "
               "часть этого файла и провоцировали читать его целиком.")

    # ── Статусы откликов ─────────────────────────────────────────────────
    out += ["", "## Статусы откликов (hh + Хабр + почта)", ""]
    for key, label in (("hh", "hh-sync"), ("habr_sync", "habr-sync"),
                       ("mail", "mail-sync")):
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
                       ("enrich", "enrich"), ("hh", "hh-sync"),
                       ("habr_sync", "habr-sync"), ("mail", "mail-sync")):
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
    res = run_scan(args)
    return 0 if res.get("ok") else 1


def run_scan(args) -> dict:
    """Тот же конвейер, но возвращает структуру, а не код возврата.

    Разделение нужно команде `wave`: ей нужны статусы этапов и путь к отчёту,
    чтобы собрать «картину волны», а не разбирать собственный печатный вывод —
    разбор своего же вывода и был главной статьёй расходов прошлого конвейера."""
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
                                     "limit": ctx.limit},
                          raw_cache=getattr(args, "raw_cache", "write"))
        _print_coverage(res["report"], res["total"], res["new"],
                        res["updated"], res["elapsed"], limit=ctx.limit,
                        health_rows=res.get("health"),
                        health_checked=res.get("health_checked", 0),
                        cache_line=res.get("cache"))
        # `total` — вакансии без служебных строк-сводок. `len(vacancies)` завышал
        # цифру ровно на число источников: сводка каждого источника считалась
        # вакансией, и «найдено» в отчёте не сходилось с числом строк в таблице.
        stages["collect"] = {"status": "ok", "report": res["report"],
                             "found": res["total"], "new": res["new"],
                             "updated": res["updated"], "limit": ctx.limit,
                             "health": res.get("health") or []}
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
                code, tg_candidates = tg_fetch_flow(out_dir, db=args.db)
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

    # 4b. habr-sync — четвёртый канал статусов; без него «уже отработано» слепо
    # к откликам на Хабр Карьере (так в отчёт попала вакансия с живым откликом).
    if getattr(args, "no_habr", False):
        stages["habr_sync"] = {"status": "skipped", "note": "выключен флагом --no-habr"}
    else:
        banner("habr-sync: кабинет Хабр Карьеры")
        from . import habrsync
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                code = habrsync.sync(args.db, cookies_from=args.cookies_from,
                                     use_cache=args.cache)
            stages["habr_sync"] = {
                "status": {0: "ok", 2: "no_creds"}.get(code, "error"),
                "text": buf.getvalue(),
                "found": _count_in(buf.getvalue(), r"откликов (\d+)")}
        except Exception as e:  # noqa: BLE001
            stages["habr_sync"] = {"status": "error", "text": buf.getvalue(),
                                   "error": f"{type(e).__name__}: {e}"}
        print(stages["habr_sync"].get("text", ""))

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
    for key in ("collect", "telegram", "enrich", "hh", "habr_sync", "mail"):
        st = stages.get(key) or {}
        print(f"  {key:<10} {_STAGE_MARK.get(st.get('status'), '—')}"
              + (f"  {st.get('note') or st.get('error') or ''}"[:80]
                 if st.get("note") or st.get("error") else ""))
    print(f"\nОтчёт: {path}")
    failed = [k for k in ("collect", "telegram", "enrich", "hh", "habr_sync", "mail")
              if (stages.get(k) or {}).get("status") == "error"]
    return {"stages": stages, "report_path": path, "ok": not failed,
            "failed": failed, "generated_at": generated_at}


# ──────────────────────────────────────────────────────────────────────────────
# Командная строка — переехала в cliargs.py
# ──────────────────────────────────────────────────────────────────────────────
#
# Реэкспорт: `from .cli import build_parser` работает как работал. Переезд
# сделан отдельно от функциональности и поведения не менял.
from .cliargs import build_parser  # noqa: F401,E402 — реэкспорт




def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
