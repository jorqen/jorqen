"""funnel — что происходит с откликами: сколько ушло, сколько ответили, за сколько.

Данные для этого лежали в базе с самого начала и не были видны ни одной командой.
`status` отвечает на вопрос «сюда уже писали?» по одной подстроке, `profile` —
про резюме. Вопрос «что со мной происходит вообще» не задавал никто, и ответ на
него держался в голове.

ОТКУДА ЦИФРЫ. Только `negotiation` — то, что ответили НАМ (кабинет hh, Хабр
Карьера, почта). `decision` сюда не входит намеренно: там лежит, что решили МЫ,
и «отметил shortlist» откликом не является.

🔴 ЧЕГО ЭТИ ЦИФРЫ НЕ ЗНАЮТ. Отклик, отправленный мимо hh, Хабра и почты — через
форму на сайте компании, в телеграм, лично, — сюда не попадает вовсе. Поэтому
знаменатель здесь «отклики, о которых знает база», а не «все отклики», и так и
написано в отчёте. Соврать процентом конверсии тут проще всего.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import store

# Ответ работодателя — это отказ, приглашение или интервью. `viewed` ответом НЕ
# считается: «резюме посмотрели» не решение, а факт открытия страницы, и мешать
# его с ответом значит завышать отклик рынка втрое.
ANSWERED = ("rejection", "invitation", "interview")
POSITIVE = ("invitation", "interview")
# Молчание. Отдельно от ответов, потому что это и есть хвосты.
SILENT = ("applied", "viewed", "not_viewed", "pending", "other")

# Через сколько дней молчания отклик становится хвостом: либо написать
# повторно, либо закрыть. Две недели — не догадка: медиана ответа по базе
# считается прямо здесь и печатается рядом, чтобы порог можно было оспорить
# числом, а не ощущением.
TAIL_DAYS = 14


def _days_between(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    try:
        d1 = datetime.fromisoformat(str(a)[:10])
        d2 = datetime.fromisoformat(str(b)[:10])
    except ValueError:
        return None
    n = (d2 - d1).days
    return n if n >= 0 else None


def build(conn, *, tail_days: int = TAIL_DAYS) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT title, company, status, source, url, event_at, first_seen, "
        "updated_at, note FROM negotiation")]
    total = len(rows)
    by_status: dict[str, int] = {}
    by_source: dict[str, dict[str, int]] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        s = by_source.setdefault(r["source"], {"всего": 0, "ответ": 0, "плюс": 0})
        s["всего"] += 1
        s["ответ"] += r["status"] in ANSWERED
        s["плюс"] += r["status"] in POSITIVE

    answered = [r for r in rows if r["status"] in ANSWERED]
    positive = [r for r in rows if r["status"] in POSITIVE]
    # Скорость ответа считается только там, где известны обе даты. Записей без
    # даты события хватает, и подставлять им сегодняшнюю значило бы удлинять
    # медиану ровно на возраст базы.
    lags = sorted(x for x in (_days_between(r["first_seen"], r["event_at"])
                              for r in answered) if x is not None)
    median = lags[len(lags) // 2] if lags else None

    today = datetime.now(timezone.utc).date().isoformat()
    edge = (datetime.now(timezone.utc) - timedelta(days=tail_days)).date().isoformat()
    tails = sorted(
        (r for r in rows
         if r["status"] in SILENT and (r["event_at"] or r["first_seen"] or today)[:10] < edge),
        key=lambda r: (r["event_at"] or r["first_seen"] or "")[:10])
    for r in tails:
        r["_ждём"] = _days_between(r["event_at"] or r["first_seen"], today)
    return {"total": total, "by_status": by_status, "by_source": by_source,
            "answered": len(answered), "positive": len(positive),
            "median_days": median, "lags": len(lags),
            "tails": tails, "tail_days": tail_days}


def _pct(part: int, whole: int) -> str:
    return f"{part * 100 // whole}%" if whole else "—"


def render(res: dict) -> str:
    t = res["total"]
    out = [f"# Воронка откликов: {t} записей в базе", ""]
    if not t:
        out.append("Пока пусто. Статусы приезжают из `hh-sync`, `habr-sync` "
                   "и `mail-sync` — запусти их, и воронка появится сама.")
        return "\n".join(out)

    out.append("Знаменатель — отклики, О КОТОРЫХ ЗНАЕТ БАЗА (кабинет hh, Хабр "
               "Карьера, почта). Отклик через форму на сайте компании или в "
               "телеграм сюда не попадает: считать по этим числам «конверсию "
               "поиска» нельзя.")
    out.append("")
    out.append(f"- откликов известно: **{t}**")
    out.append(f"- ответили (отказ, приглашение, интервью): **{res['answered']}** "
               f"({_pct(res['answered'], t)})")
    out.append(f"- из них приглашений и интервью: **{res['positive']}** "
               f"({_pct(res['positive'], t)} от всех откликов, "
               f"{_pct(res['positive'], res['answered'])} от ответивших)")
    if res["median_days"] is not None:
        weak = "" if res["lags"] >= 10 else " — этого мало, число ненадёжно"
        out.append(f"- медиана «отклик → ответ»: **{res['median_days']} дн.** "
                   f"(по {res['lags']} откликам, где известны обе даты{weak})")
    else:
        out.append("- медиана «отклик → ответ»: не посчитать, дат событий нет")
    out.append("")

    out.append("| статус | сколько |")
    out.append("|---|---|")
    for status, n in sorted(res["by_status"].items(), key=lambda p: -p[1]):
        out.append(f"| {status} | {n} |")
    out.append("")

    out.append("| откуда | откликов | ответили | приглашений |")
    out.append("|---|---|---|---|")
    for src, s in sorted(res["by_source"].items(), key=lambda p: -p[1]["всего"]):
        out.append(f"| {src} | {s['всего']} | {s['ответ']} "
                   f"({_pct(s['ответ'], s['всего'])}) | {s['плюс']} |")
    return "\n".join(out)


def render_tails(res: dict) -> str:
    tails, days = res["tails"], res["tail_days"]
    if not tails:
        return (f"# Хвосты: нет\n\nОткликов, молчащих дольше {days} дней, "
                f"не осталось — всё либо отвечено, либо свежее.")
    out = [f"# Хвосты: {len(tails)} откликов молчат дольше {days} дней", "",
           "Молчание — не отказ. Это либо повод написать повторно, либо повод "
           "закрыть вопрос и не держать его в голове.", ""]
    # Сравнение с медианой печатается только когда медиане есть на чём стоять.
    # На пяти замерах она не медиана, а случайное число, и «ждёшь втрое дольше
    # типичного» из неё — вывод, которого данные не выдерживают.
    if res["median_days"] is not None and res["lags"] >= 10:
        out.append(f"Для сравнения: медиана ответа по базе — {res['median_days']} дн. "
                   f"(по {res['lags']} откликам).")
        out.append("")
    elif res["median_days"] is not None:
        out.append(f"Медиану ответа считать не на чем: дат события хватило только "
                   f"на {res['lags']} откликов. Порог в {days} дней — "
                   f"договорённость, а не вывод из данных.")
        out.append("")
    out.append("| ждём, дн. | статус | вакансия | компания | откуда |")
    out.append("|---|---|---|---|---|")
    for r in tails[:60]:
        out.append(f"| {r.get('_ждём') if r.get('_ждём') is not None else '?'} "
                   f"| {r['status']} | {(r['title'] or '')[:52].replace('|', '/')} "
                   f"| {(r['company'] or '?')[:28].replace('|', '/')} | {r['source']} |")
    if len(tails) > 60:
        out.append(f"\n…и ещё {len(tails) - 60}.")
    out.append("")
    out.append("Что с этим делать — решаешь ты: скрипт не пишет писем и "
               "не закрывает вакансии.")
    return "\n".join(out)


def cli(args) -> int:
    with store.connect(args.db) as conn:
        res = build(conn, tail_days=getattr(args, "tail_days", TAIL_DAYS))
    if getattr(args, "tails_only", False):
        print(render_tails(res))
        # Код возврата — признак «есть что разобрать»: команда годится в рутину,
        # где важен не текст, а сам факт наличия хвостов.
        return 1 if res["tails"] else 0
    print(render(res))
    print()
    print(render_tails(res))
    return 0
