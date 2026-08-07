"""Команды `detail` и `enrich` — выжимки вместо HTML-дампов.

Выделены из `cli.py` 07.08.2026 переездом БЕЗ изменения поведения: файл был
самым большим в сборщике, а эти две команды — самый крупный связный кусок в нём
и единственный, который тянет за собой параллельный обход, рендер и антибот-
разбор. `cli` реэкспортирует обе, поэтому прежние импорты работают.
"""

from __future__ import annotations

import json
import sys

from . import store
from .net import BlockedError, HostPacer, parallel
from .sources import ATS_ROLE_RE

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
    Возвращает {digests, ok, blocked, gone, failed, fails, delta, done, todo,
    skipped_by_max, skipped_gone}."""
    from .detail import digest, get_detail
    from .net import PAGE_GONE, PAGE_OK, error_state, parallel

    with store.connect(db) as conn:
        rows = store.query(conn, first_seen_since=since_iso, sources=sources,
                           exclude_decided=not include_decided, limit=None)
    rows = [r for r in rows if r["url"]]
    keys = [(r["source"], r["external_id"]) for r in rows]

    with store.connect(db) as conn:
        done = store.have_details(conn, keys) if not refresh else set()
        # Подмножество `done`: пропущенные не потому, что выжимка есть, а потому,
        # что вакансии больше нет. Считаем здесь же — отдельной строкой отчёта.
        skipped_gone = len(store.gone_details(conn, keys)) if not refresh else 0
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

    ok = blocked = gone = failed = 0
    digests: list[str] = []
    fails: list[str] = []
    with store.connect(db) as conn:
        for r in todo:
            key = f"{r['source']}:{r['external_id']}"
            success, payload = results[key]
            if success:
                ok += 1
                # Разобралось — но страница всё ещё могла сказать «снята»
                # (hh с флагом archived, Хабр с archived, заглушка ATS). Это
                # состояние знает разборщик, и записать его надо здесь: выжимка
                # у нас есть, а вакансии уже нет.
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  payload.status, payload=payload.to_dict(),
                                  page_state=payload.extra.get("page_state") or PAGE_OK)
                digests.append(digest(payload))
            elif isinstance(payload, BlockedError):
                blocked += 1
                state, _why = error_state(payload)
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  "blocked", error=str(payload), page_state=state)
                fails.append(f"{key} АНТИБОТ: {payload}")
            else:
                state, _why = error_state(payload)
                store.save_detail(conn, r["source"], r["external_id"], r["url"],
                                  "error", error=str(payload), page_state=state)
                if state == PAGE_GONE:
                    # Не поломка и не работа для человека: вакансию сняли.
                    # В `fails` такие строки не идут — иначе раздел «Стены
                    # и ошибки» заполняется тем, что чинить нечем, и настоящие
                    # ошибки в нём тонут. Видно их по счётчику.
                    gone += 1
                else:
                    failed += 1
                    fails.append(f"{key} УПАЛ: {payload}")

    return {"digests": digests, "ok": ok, "blocked": blocked, "gone": gone,
            "failed": failed,
            "fails": fails, "delta": len(rows), "done": len(done), "todo": len(todo),
            "skipped_by_max": skipped_by_max, "skipped_profile": skipped_profile,
            "skipped_gone": skipped_gone,
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
            f"(антибот {res.get('blocked', 0)}, снято {res.get('gone', 0)}, "
            f"упало {res.get('failed', 0)}), "
            f"уже было в базе {res.get('done', '?')}, "
            f"в дельте {res.get('delta', '?')}")
    if res.get("dropped_dups"):
        line += f", схлопнуто агрегаторских дублей {res['dropped_dups']}"
    if res.get("skipped_gone"):
        # Пропуск обязан быть назван вслух: без этой строки снятые вакансии
        # молча растворяются в «уже было в базе», и отличить отложенный повтор
        # от потерянной вакансии нечем.
        line += (f"\n· пропущено как снятые {res['skipped_gone']} — страница "
                 f"сказала «такой вакансии нет», повтор отложен на "
                 f"{store.RETRY_GONE_DAYS} дн. (не навсегда: «снята» — вывод по "
                 f"признакам, а не факт от площадки)")
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
          f"снято {res['gone']} / упало {res['failed']} из {res['todo']}")
    print(enrich_summary(res))
    for f in res["fails"]:
        print(f"  - {f}")
    if res["blocked"]:
        print("АНТИБОТ снимается заходом человека в браузере, не повтором скрипта.")
    return 1 if res["failed"] else 0

