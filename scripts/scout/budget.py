"""budget — смета волны ДО её начала.

Зачем команда существует. Прогон 04.08.2026 стоил 5,6 млн токенов и всё равно
терял вакансии; потолок волны с тех пор — 500 000. Потолок, о превышении которого
узнают постфактум, не потолок, а пожелание: к моменту, когда расход виден,
он уже случился.

Смета делает потолок исполнимым. Она считает не «сколько примерно бывает»,
а РЕАЛЬНЫЙ размер того, что команды напечатают на этой конкретной базе:
`wave` и `brief` вызываются здесь же, вхолостую, и меряется длина их вывода.
Единственное, что оценивается коэффициентом, — карточки: их пишет модель,
и до написания их длину не измерить.

Что смета НЕ считает: рассуждения модели и её собственный текст. Она меряет то,
что ВХОДИТ в контекст со стороны сборщика, — а это и была статья перерасхода:
2,8 МБ отчёта и 3,3 МБ дампов, разобранных подагентами.

Формула токенов. ТЗ говорит «символы/4», и для латиницы это близко к правде.
Для кириллицы — нет: русский текст токенизируется примерно вдвое плотнее, и
оценка «символы/4» занизила бы расход почти вдвое на выдаче, которая наполовину
русская. Поэтому считаем взвешенно и печатаем ОБЕ цифры: заниженная оценка
потолка хуже отсутствующей.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

from . import brief, shortlist, store

# Потолок волны. Не константа удобства, а приёмка из задания.
WAVE_CAP = 500_000

# Символов на токен: латиница ~4, кириллица ~2. Второе — не пессимизм, а замер:
# у большинства BPE-словарей русское слово это 2–4 токена при 5–8 буквах.
CHARS_PER_TOKEN_LAT = 4.0
CHARS_PER_TOKEN_CYR = 2.0

# Сколько стоит карточка, которую пишет модель. Диапазон из задания (600–900),
# берём верх: смета обязана ошибаться в сторону перестраховки.
CARD_TOKENS = 900
# Постоянные расходы волны, которых нет в выводе команд: системный промпт скилла,
# SKILL.md и переписка вокруг. Замер по прошлым волнам.
OVERHEAD_TOKENS = 40_000
# Части картины волны, которых на пустой базе нет и быть не может: таблица этапов
# с их примечаниями, список стен и блок «следующий шаг». Их размер зависит от
# того, что случится в прогоне, поэтому берётся наблюдённый ВЕРХ, а не среднее:
# смета обязана ошибаться в сторону перестраховки. Без этой добавки смета
# показывала бы 1.1K там, где живой `wave` печатает 3–6K.
WAVE_EXTRA_TOKENS = 5_000


def tokens(text: str) -> int:
    """Оценка в токенах с поправкой на кириллицу."""
    if not text:
        return 0
    cyr = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    lat = len(text) - cyr
    return int(lat / CHARS_PER_TOKEN_LAT + cyr / CHARS_PER_TOKEN_CYR)


def naive_tokens(text: str) -> int:
    """Формула из задания, символы/4. Печатается рядом — для сверки."""
    return int(len(text or "") / 4)


def measure(db: str, *, days: int, top: int, brief_n: int,
            cards: int) -> dict:
    """Считает смету, ВЫЗЫВАЯ команды вхолостую. Сеть не трогается.

    `wave` целиком здесь звать нельзя — он ходит на площадки. Меряется его
    печатная часть (`render_picture`) на текущей базе: именно она и уезжает
    в контекст, а вывод самого скана лежит в файле и в контекст не идёт.
    """
    since = store.since_arg(f"{days}d")
    sl = shortlist.build(db, since=since, by="seen", sources=None, limit=0)
    rows = sl["rows"]

    # 1. Картина волны. Подсовываем пустые этапы: их размер фиксирован и мал,
    #    а мерить надо таблицу шорт-листа, которая от базы зависит целиком.
    from . import wave  # noqa: PLC0415 — ленивый импорт, wave тянет cli

    stub = {"stages": {k: {"status": "ok", "found": 0} for k, _ in wave.STAGES},
            "report_path": "—"}
    picture = wave.render_picture(stub, sl, top=top)

    # 2. Досье. Меряем на РЕАЛЬНЫХ строках топа, а не на средней по больнице:
    #    вакансия с длинным описанием стоит вчетверо дороже короткой.
    urls = [r.get("url") for r in rows[:brief_n] if r.get("url")]
    buf = io.StringIO()
    with store.connect(db) as conn, redirect_stdout(buf):
        chunks = [brief.one(conn, u) for u in urls]
    brief_text = "\n\n".join(chunks)

    no_text = [r for r in rows[:top] if not r.get("_enriched")]
    no_channel = [r for r in rows[:top] if r.get("company") and not r.get("_channel")]

    wave_t = tokens(picture) + WAVE_EXTRA_TOKENS
    brief_t = tokens(brief_text)
    cards_t = cards * CARD_TOKENS
    total = wave_t + brief_t + cards_t + OVERHEAD_TOKENS
    return {
        "since": since, "days": days, "top": top, "brief_n": len(urls),
        "cards": cards,
        "delta": sl["stats"]["delta"], "groups": sl["stats"]["groups"],
        "off_profile": sl["stats"]["off_profile"],
        "no_text": len(no_text), "no_channel": len(no_channel),
        "wave_tokens": wave_t, "wave_chars": len(picture),
        "wave_naive": naive_tokens(picture),
        "brief_tokens": brief_t, "brief_chars": len(brief_text),
        "brief_naive": naive_tokens(brief_text),
        "brief_per_row": int(brief_t / len(urls)) if urls else 0,
        "cards_tokens": cards_t, "overhead": OVERHEAD_TOKENS,
        "total": total, "cap": WAVE_CAP, "fits": total <= WAVE_CAP,
        "rows": rows,
    }


def max_top(db: str, *, days: int, cap: int = WAVE_CAP) -> int:
    """Наибольший `--top`, который ещё влезает в потолок.

    Существует ради одной строки в выводе: «не влезает» без ответа «а сколько
    можно» заставляет подбирать число вручную, прогон за прогоном.
    """
    lo, hi, best = 0, 120, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if mid == 0:
            lo = 1
            continue
        m = measure(db, days=days, top=mid, brief_n=mid, cards=mid)
        if m["total"] <= cap:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def render(m: dict, *, suggest: int | None = None,
           auth_lines: list[str] | None = None) -> str:
    """Смета человеку и модели. Вердикт — первой строкой после заголовка.

    `auth_lines` — предупреждение о разлогине (см. `authrefresh.preflight_lines`).
    Оно здесь не ради полноты: смета отвечает на вопрос «стоит ли пускать волну»,
    а волна с мёртвой сессией стоит столько же, но приносит меньше. Узнать об
    этом ПОСЛЕ прогона — значит заплатить за неполный сбор и заплатить второй раз
    за повторный.
    """
    k = lambda n: f"{n / 1000:.1f}K"
    out = [f"# Смета волны: окно {m['days']}d, топ {m['top']}", ""]

    verdict = ("✅ ВЛЕЗАЕТ" if m["fits"] else "🔴 НЕ ВЛЕЗАЕТ")
    out.append(f"{verdict}: прогноз {k(m['total'])} из потолка {k(m['cap'])} токенов.")
    if not m["fits"] and suggest is not None:
        out.append(f"   Уменьшай --top, а не надежду: влезает --top {suggest}.")
    out.append("")

    if auth_lines:
        out.append("## Авторизация — починить ДО прогона")
        out.extend(f"  {ln}" for ln in auth_lines)
        out.append("")

    out.append("## Что уже в базе (сеть не трогалась)")
    out.append(f"  дельта за окно            {m['delta']}")
    out.append(f"  из них профильных         {m['groups']}  ← столько отдаст shortlist")
    out.append(f"  чужая профессия           {m['off_profile']}  (отсеяно со счётчиком)")
    out.append(f"  в топ-{m['top']} без текста для суждения  {m['no_text']}"
               f"  ← им нужен `scout brief`/`detail`")
    out.append(f"  в топ-{m['top']} компаний без канала найма {m['no_channel']}"
               f"  ← `scout channel`, НЕ агент")
    out.append("")

    brief_label = f"brief на {m['brief_n']} вакансий"
    cards_label = f"карточки, {m['cards']} × {CARD_TOKENS}"
    out.append("## Прогноз расхода")
    out.append(f"  {'этап':<34} {'токенов':>9} {'символов':>10}  {'символы/4':>10}")
    out.append(f"  {'-' * 68}")
    out.append(f"  {'wave (картина + топ)':<34} {m['wave_tokens']:>9} "
               f"{m['wave_chars']:>10}  {m['wave_naive']:>10}"
               f"   (+{WAVE_EXTRA_TOKENS} на этапы и стены)")
    out.append(f"  {brief_label:<34} {m['brief_tokens']:>9} "
               f"{m['brief_chars']:>10}  {m['brief_naive']:>10}")
    out.append(f"  {cards_label:<34} {m['cards_tokens']:>9} {'—':>10}  {'—':>10}")
    out.append(f"  {'постоянные (скилл, переписка)':<34} {m['overhead']:>9} "
               f"{'—':>10}  {'—':>10}")
    out.append(f"  {'-' * 68}")
    out.append(f"  {'ИТОГО':<34} {m['total']:>9}")
    out.append("")
    out.append(f"  одно досье в среднем: {m['brief_per_row']} токенов")
    out.append("")
    out.append("Считается ВЫВОД СБОРЩИКА — то, что входит в контекст. Рассуждения "
               "модели сюда не входят.")
    out.append("Колонка «символы/4» — формула из задания; основная оценка выше "
               "учитывает, что кириллица токенизируется вдвое плотнее.")
    out.append("")
    out.append("🔴 Запрещено и в смету не заложено: читать `.scout/reports/*.md`, "
               "дампы `.scout/tg/**/*.txt` и разбирать вывод сборщика подагентами. "
               "Всё, что нужно для отбора, отдают `shortlist`, `brief` и `card`.")
    return "\n".join(out)


def cli(args) -> int:
    top = args.top
    m = measure(args.db, days=args.days, top=top,
                brief_n=args.brief if args.brief is not None else top,
                cards=args.cards if args.cards is not None else top)
    suggest = None
    if not m["fits"]:
        suggest = max_top(args.db, days=args.days, cap=args.cap)
    from . import authrefresh  # noqa: PLC0415 — ленивый импорт, как везде в ядре
    print(render(m, suggest=suggest, auth_lines=authrefresh.preflight_lines()))
    # Ненулевой код — чтобы «не влезает» было видно вызывающему, а не утонуло
    # в выводе: смета существует ровно ради этого решения.
    return 0 if m["fits"] else 1
