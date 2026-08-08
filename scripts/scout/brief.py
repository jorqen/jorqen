"""brief — всё, что нужно знать о вакансии перед карточкой, одним вызовом.

Раньше подагент на КАЖДУЮ вакансию делал четыре обращения: `detail` за текстом,
`resolve` за путём отклика, `status` за историей компании и веб-поиск за каналом
найма. Четыре вызова × десятки вакансий — это и есть та цена, которую платил
прошлый конвейер (замер 04.08.2026).

`brief` собирает то же самое за один проход и печатает компактно: описание
урезано до сути, требуемый стаж и маркеры права на работу уже распознаны,
история компании подтянута из `negotiation`, а прямой канал найма — из кэша
`employer_channel`, если он там есть.

Агенту остаётся ровно то, чего в базе нет: раскрыть скрытого работодателя и
найти канал найма, когда кэш пуст.
"""

from __future__ import annotations

import json
import sys

from . import store
from .shortlist import (company_aliases, norm, own_text_payload, required_years,
                        rtw_flags)


def _trim(text: str | None, limit: int) -> str:
    t = " ".join((text or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")


def one(conn, url: str, *, desc_chars: int = 900) -> str:
    """Сводка по одной вакансии. Сеть трогаем только если выжимки ещё нет."""
    row = conn.execute(
        "SELECT source, external_id, title, company, salary_from, salary_to, "
        "currency, salary_period, location, remote, published_at, employer_url, "
        "description, raw "
        "FROM vacancy WHERE url = ?", (url,)).fetchone()
    if row is None:
        return f"## {url}\n  нет в базе — сначала `scout collect` или дай ссылку из shortlist"
    (source, ext_id, title, company, sal_from, sal_to, cur, period,
     location, remote, published, employer_url, description, raw) = row

    payload = None
    text_src = ""
    d = conn.execute("SELECT payload, status FROM detail WHERE source = ? AND "
                     "external_id = ?", (source, ext_id)).fetchone()
    if d and d[0]:
        try:
            payload = json.loads(d[0])
            text_src = "выжимка страницы"
        except (TypeError, ValueError):
            payload = None
    if payload is None:
        # Своего текста у записи может быть достаточно: у телеграм-поста
        # страницы нет вовсе — пост САМ и есть описание, и оно уже в базе.
        # Без этой ветки brief советовал «возьми scout detail» для ссылки
        # t.me — то есть отправлял качать то, что качать нечего и незачем.
        payload = own_text_payload({"title": title, "description": description,
                                    "raw": raw})
        text_src = "текст самой вакансии (страницу не качали)" if payload else ""

    out = [f"## {title} — {company or 'работодатель не раскрыт'}", f"  url: {url}"]
    money = "—"
    if sal_from or sal_to:
        per = {"hour": "/час", "month": "/мес", "year": "/год"}.get(period, "")
        money = (f"{sal_from}–{sal_to}" if sal_from and sal_to
                 else f"от {sal_from or sal_to}") + f" {cur or ''}{per}"
    out.append(f"  деньги: {money} · локация: {location or '—'}"
               f"{' · remote' if remote else ''} · опубл.: {(published or '—')[:10]}")
    out.append(f"  источник: {source}:{ext_id}"
               + (f" · сайт работодателя: {employer_url}" if employer_url else ""))

    years = required_years(payload)
    out.append(f"  требуемый стаж: {years if years is not None else 'не назван'}"
               f" · право на работу: {rtw_flags(payload) or 'маркеров нет'}")

    # Прямой канал найма из кэша — то, что дороже всего искать заново.
    # 🔴 Ключей у компании два (`company_aliases`): письма приходят от «<Компания>
    # Careers» и «<Компания> HR», и по одному ключу история с каналом не
    # находились. В карточке это дороже, чем где-либо: SKILL.md требует читать
    # историю ДО того, как предложить отклик, а `brief` — единственное место,
    # где она печатается. Живой случай 08.08.2026: отказ от «Infomediji Careers»
    # не находился по вакансии «Infomediji».
    keys = set(company_aliases(company))
    key = norm(company)
    # Сверяем МНОЖЕСТВА алиасов, а не ключ с ключом. Хвост живёт на стороне
    # ЗАПИСИ («Infomediji Careers» в письме против «Infomediji» в вакансии), и
    # подставлять алиасы в WHERE бесполезно: искать надо среди того, что
    # записано. LIKE здесь нельзя — на живой базе «ALTEN» так получал историю
    # «Altenar», 26 коллизий; строки берём целиком и сводим одной функцией.
    ch = next((row for row in conn.execute(
        "SELECT channel, kind, evidence, company_key FROM employer_channel")
        if keys & set(company_aliases(row[3]))), None) if keys else None
    if ch:
        out.append(f"  🎯 канал найма (из кэша): {ch[1] or '—'} {ch[0]}")
        if ch[2]:
            out.append(f"     подтверждение: {_trim(ch[2], 160)}")
    else:
        out.append("  канал найма: НЕ НАЙДЕН — это и есть задача агента")

    # История: что компания уже отвечала. Ради этого блока таблица и заведена.
    if key:
        # Точное совпадение, а не LIKE-подстрока: на живой базе «ALTEN»
        # (инженерный консалтинг) получал историю «Altenar» (беттинг-софт)
        # просто потому, что одно имя — подстрока другого. 26 таких коллизий.
        hist = [row for row in conn.execute(
            "SELECT status, title, event_at, source, company_key FROM negotiation "
            "WHERE status != 'other' ORDER BY event_at DESC")
            if keys & set(company_aliases(row[4]))][:5]
        if hist:
            out.append("  история с компанией:")
            for st, t, at, src, _ck in hist:
                out.append(f"    · [{st}] {_trim(t, 54)} ({(at or '')[:10]}, {src})")
        else:
            out.append("  история с компанией: пусто — контакт холодный")

    dec = conn.execute("SELECT state, note FROM decision WHERE source = ? AND "
                       "external_id = ?", (source, ext_id)).fetchone()
    if dec:
        out.append(f"  ⛔ наше решение по ЭТОЙ вакансии: {dec[0]}"
                   + (f" — {_trim(dec[1], 80)}" if dec[1] else ""))

    # ДРУГИЕ роли той же компании и решения по ним. Раньше это добиралось
    # отдельным `scout status --query <компания>` перед каждой карточкой — до
    # тридцати вызовов на волну, — и заодно вручную собирался блок «другие роли
    # этой компании в волне», который SKILL.md требует. И то и другое один
    # запрос по нормализованному имени: точное совпадение, а не LIKE, ровно по
    # тому же доводу, что у истории выше (ALTEN ↔ Altenar, 26 коллизий).
    if company:
        others = conn.execute(
            "SELECT v.title, v.url, d.state, d.note FROM vacancy v "
            "LEFT JOIN decision d ON d.source = v.source "
            "AND d.external_id = v.external_id "
            "WHERE v.company IS NOT NULL AND v.url != ? "
            "AND lower(trim(v.company)) = lower(trim(?)) "
            "ORDER BY v.last_seen DESC LIMIT 6", (url, company)).fetchall()
        if others:
            out.append(f"  другие роли {company} в базе: {len(others)}")
            for t, u, st, note in others:
                mark = f" [{st}]" if st else ""
                tail = f" — {_trim(note, 50)}" if note else ""
                out.append(f"    · {_trim(t, 52)}{mark}{tail}  {u}")

    # Маршруты отклика: считаем заново из того, что знаем, и ДОПОЛНЯЕМ кэш.
    # Пересчёт дешёвый (это разбор доменов), а кэш хранит то, что нашли волны
    # раньше, — вместе они дают полный список, а не последний известный.
    from . import applyopt  # noqa: PLC0415

    raw_dict = None
    if raw:
        try:
            raw_dict = json.loads(raw)
        except (TypeError, ValueError):
            raw_dict = None
    fresh = applyopt.gather(
        {"employer_url": employer_url, "url": url, "raw": raw_dict}, payload)
    if fresh:
        store.save_apply_options(conn, source, ext_id, fresh)
    known = store.apply_options(conn, source, ext_id) or fresh
    out += applyopt.render(known)

    # Вердикты прошлого ресёрча: раскрытый работодатель, живость, право на работу.
    # Ради этого блока таблица и заведена — повторять эти проверки в каждой волне
    # значит платить за них снова.
    res = store.research(conn, source, ext_id)
    if res:
        bits = [f"{k}: {res[k]}" for k in
                ("employer_revealed", "liveness", "rtw", "verdict") if res.get(k)]
        if bits:
            out.append(f"  ✔ ресёрч от {(res.get('checked_at') or '')[:10]}: "
                       + "; ".join(bits))
            if res.get("evidence"):
                out.append(f"     подтверждение: {_trim(res['evidence'], 160)}")

    if payload:
        out.append(f"  текст: {text_src}")
        if payload.get("apply_note"):
            out.append(f"  путь отклика: {_trim(payload['apply_note'], 200)}")
        if payload.get("apply_url"):
            out.append(f"  контакт из выжимки: {payload['apply_url']}")
        for field, label in (("requirements", "требования"),
                             ("description", "описание")):
            if payload.get(field):
                out.append(f"  {label}: {_trim(payload[field], desc_chars)}")
        for n in payload.get("notes") or ():
            out.append(f"  ⚠️  {_trim(n, 160)}")
    else:
        out.append("  ВЫЖИМКИ НЕТ — возьми `scout detail <url>` "
                   "(и `--render`, если страница SPA)")
    return "\n".join(out)


def cli(args) -> int:
    with store.connect(args.db) as conn:
        chunks = [one(conn, u, desc_chars=args.chars) for u in args.urls]
    print("\n\n".join(chunks))
    missing = sum(1 for c in chunks if "нет в базе" in c)
    if missing:
        print(f"\nне нашлось в базе: {missing} из {len(args.urls)}", file=sys.stderr)
        return 1
    return 0
