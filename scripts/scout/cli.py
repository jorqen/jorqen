"""CLI сборщика.

    python3 -m scripts.scout collect            # обойти все площадки, сложить в базу
    python3 -m scripts.scout new --since 3d     # что появилось с прошлого раза
    python3 -m scripts.scout coverage           # кто отработал, кто упал — за последний прогон
    python3 -m scripts.scout resolve <url>      # куда на самом деле ведёт «Откликнуться»
    python3 -m scripts.scout raw <источник>     # страница для источников без парсера
    python3 -m scripts.scout mark <src> <id> --state applied

Главная идея: скрипт делает механику (сходить на пятнадцать площадок, распаковать,
сложить, посчитать дельту), модель — суждение (что подходит, кому писать, каким текстом).
Так обход перестаёт упираться в контекст: в модель приезжает таблица на сотню строк,
а не полтора мегабайта HTML.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from . import store
from .model import Vacancy
from .net import parallel
from .sources import NEEDS_LOGIN, RAW_SOURCES, SOURCES, Ctx, raw_dump


# ──────────────────────────────────────────────────────────────────────────────
# collect
# ──────────────────────────────────────────────────────────────────────────────

def cmd_collect(args) -> int:
    ctx = Ctx(query=args.query, extra_queries=tuple(args.also or ()), days=args.days,
              area=args.area, limit=args.limit, include_foreign=not args.ru_only,
              ats_all=args.ats_all)
    names = args.sources.split(",") if args.sources else list(SOURCES)
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        print(f"неизвестные источники: {', '.join(unknown)}", file=sys.stderr)
        print(f"доступны: {', '.join(SOURCES)}", file=sys.stderr)
        return 2

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

    results = parallel({n: wrap(n) for n in names}, workers=args.workers)

    all_vacancies: list[Vacancy] = []
    report: list[dict] = []
    for name in names:
        ok, payload = results.get(name, (False, RuntimeError("не запускался")))
        if ok:
            all_vacancies.extend(payload)
            report.append({"source": name, "status": "ok", "found": len(payload),
                           "elapsed_ms": timings.get(name, 0), "error": None})
        else:
            report.append({"source": name, "status": "error", "found": 0,
                           "elapsed_ms": timings.get(name, 0), "error": str(payload)})

    new = updated = 0
    if not args.no_store:
        with store.connect(args.db) as conn:
            run_id = store.start_run(conn, ctx.query, vars(args))
            new, updated = store.upsert(conn, all_vacancies)
            by_source: dict[str, int] = {}
            for v in all_vacancies:
                by_source[v.source.split(":")[0]] = by_source.get(v.source.split(":")[0], 0) + 1
            for r in report:
                store.record_source(conn, run_id, r["source"], r["status"],
                                    found=r["found"], error=r["error"],
                                    elapsed_ms=r["elapsed_ms"])
            store.finish_run(conn, run_id)

    failed = [r for r in report if r["status"] != "ok"]
    if args.format == "json":
        print(json.dumps({
            "query": ctx.query, "days": ctx.days, "coverage": report,
            "found": len(all_vacancies), "new": new, "updated": updated,
            "needs_login": list(NEEDS_LOGIN), "raw_sources": list(RAW_SOURCES),
            "vacancies": [v.to_dict() for v in all_vacancies] if args.with_items else [],
        }, ensure_ascii=False, indent=2))
    else:
        _print_coverage(report, len(all_vacancies), new, updated, time.time() - started)

    # Ненулевой код — чтобы упавшая площадка была видна вызывающему, а не утонула в выводе.
    return 1 if failed else 0


def _print_coverage(report, total, new, updated, elapsed) -> None:
    print(f"\n## Покрытие прогона ({elapsed:.1f}s)\n")
    print(f"{'источник':<14} {'статус':<8} {'найдено':>8}  примечание")
    print("-" * 78)
    for r in sorted(report, key=lambda x: (x["status"] != "error", x["source"])):
        mark = "ok" if r["status"] == "ok" else "УПАЛ"
        note = (r["error"] or "")[:44]
        print(f"{r['source']:<14} {mark:<8} {r['found']:>8}  {note}")
    print("-" * 78)
    print(f"{'ИТОГО':<14} {'':<8} {total:>8}  новых: {new}, обновлено: {updated}")

    if RAW_SOURCES:
        print(f"\nБез парсера (забирать `raw`, разбирать глазами): {', '.join(RAW_SOURCES)}")
    print(f"Требуют входа пользователя (сборщик не трогает): {', '.join(NEEDS_LOGIN)}")
    failed = [r["source"] for r in report if r["status"] != "ok"]
    if failed:
        print(f"\n⚠️  НЕ ОТРАБОТАЛИ: {', '.join(failed)} — обход неполный, "
              f"это надо сказать в отчёте, а не замолчать.")


# ──────────────────────────────────────────────────────────────────────────────
# new / coverage
# ──────────────────────────────────────────────────────────────────────────────

def cmd_new(args) -> int:
    since = store.since_arg(args.since)
    with store.connect(args.db) as conn:
        rows = store.query(conn, since=since if args.by == "published" else None,
                           first_seen_since=since if args.by == "seen" else None,
                           sources=args.sources.split(",") if args.sources else None,
                           exclude_decided=not args.include_decided,
                           limit=args.limit)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    if not rows:
        print("Ничего нового в окне.")
        return 0

    print(f"# Новое с {since} — {len(rows)} вакансий\n")
    print("| # | роль | компания | деньги | локация | опубликовано | источник | ссылка |")
    print("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        v = Vacancy(source=r["source"], external_id=r["external_id"], url=r["url"],
                    title=r["title"], company=r["company"], salary_from=r["salary_from"],
                    salary_to=r["salary_to"], currency=r["currency"],
                    salary_gross=bool(r["salary_gross"]) if r["salary_gross"] is not None else None)
        pub = (r["published_at"] or "")[:10]
        loc = (r["location"] or "")[:24]
        money = v.salary_str() or "—"
        print(f"| {i} | {r['title'][:60]} | {(r['company'] or '—')[:28]} | {money} | "
              f"{loc or '—'} | {pub or '—'} | {r['source']} | {r['url']} |")

    no_salary = sum(1 for r in rows if r["salary_from"] is None and r["salary_to"] is None)
    print(f"\nБез вилки: {no_salary} из {len(rows)}. "
          f"Отсутствие вилки и маленькая вилка — не причина пропускать вакансию: "
          f"вилка выносится в карточку фактом, решает пользователь.")
    return 0


def cmd_coverage(args) -> int:
    with store.connect(args.db) as conn:
        run = store.last_run(conn)
        st = store.stats(conn)
    if not run:
        print("Прогонов ещё не было.")
        return 0
    print(f"Последний прогон #{run['id']}: {run['started_at']} → {run['finished_at']}")
    print(f"Запрос: {run['query']}\n")
    print(f"{'источник':<14} {'статус':<8} {'найдено':>8}  ошибка")
    print("-" * 78)
    for s in run["sources"]:
        print(f"{s['source']:<14} {s['status']:<8} {s['found']:>8}  {(s['error'] or '')[:44]}")
    print("-" * 78)
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
    text, url = raw_dump(args.source, ctx)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{url} → {args.out} ({len(text)} символов)")
    else:
        sys.stdout.write(text)
    return 0


def cmd_auth(args) -> int:
    from . import auth
    if args.action == "login":
        if not args.platform:
            print(f"укажи площадку: {', '.join(auth.PLATFORMS)}", file=sys.stderr)
            return 2
        return auth.login(args.platform)
    if args.action == "check":
        return auth.check([args.platform] if args.platform else None)
    return auth.status()


def cmd_mark(args) -> int:
    with store.connect(args.db) as conn:
        store.decide(conn, args.source, args.id, args.state, args.note)
    print(f"{args.source}:{args.id} → {args.state}")
    return 0


# ──────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    # --db принимается и до, и после подкоманды: писать `collect --db ...` естественнее,
    # чем `--db ... collect`, и спотыкаться об это на каждом запуске незачем.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=store.DEFAULT_DB,
                        help="путь к SQLite (по умолчанию .scout/scout.db)")

    p = argparse.ArgumentParser(prog="scout", description="Сборщик вакансий", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="обойти площадки", parents=[common])
    c.add_argument("--query", default="Golang")
    c.add_argument("--also", nargs="*", default=["Go разработчик", "Backend Go"],
                   help="дополнительные формулировки: одна не покрывает всё")
    c.add_argument("--days", type=int, default=3, help="окно по публикации-или-обновлению")
    c.add_argument("--area", default="113", help="113 — вся РФ, 1 — Москва, 2 — СПб")
    c.add_argument("--limit", type=int, default=100)
    c.add_argument("--sources", help="через запятую; по умолчанию все")
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--ru-only", action="store_true", help="без зарубежных источников")
    c.add_argument("--ats-all", action="store_true",
                   help="нести все роли с ATS-досок, включая заведомо чужие профессии")
    c.add_argument("--no-store", action="store_true", help="не писать в базу (для облака)")
    c.add_argument("--with-items", action="store_true", help="выгрузить вакансии в JSON")
    c.add_argument("--format", choices=["text", "json"], default="text")
    c.set_defaults(func=cmd_collect)

    n = sub.add_parser("new", help="дельта: что появилось с указанного момента", parents=[common])
    n.add_argument("--since", default="3d", help="3d, 12h, 2026-07-20 или ISO")
    n.add_argument("--by", choices=["seen", "published"], default="seen",
                   help="seen — чего не было в базе; published — по дате площадки")
    n.add_argument("--sources")
    n.add_argument("--limit", type=int, default=200)
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
    w.set_defaults(func=cmd_raw)

    a = sub.add_parser("auth", help="сессии площадок в .auth/ (вход делает пользователь)",
                       parents=[common])
    a.add_argument("action", choices=["status", "login", "check"], nargs="?", default="status")
    a.add_argument("platform", nargs="?")
    a.set_defaults(func=cmd_auth)

    m = sub.add_parser("mark", help="зафиксировать решение по вакансии", parents=[common])
    m.add_argument("source")
    m.add_argument("id")
    m.add_argument("--state", required=True,
                   choices=["applied", "rejected", "skipped", "shortlist", "interview"])
    m.add_argument("--note")
    m.set_defaults(func=cmd_mark)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
