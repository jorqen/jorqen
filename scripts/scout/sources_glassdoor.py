"""glassdoor.com.au — площадка за управляемой стеной Cloudflare.

Вынесена из `sources_web.py` 07.08.2026 переездом БЕЗ изменения поведения:
файл дорос до 2500 строк. Здесь она и по смыслу отдельно — единственный
источник, которому нужен настоящий браузер, и почти всегда всё равно стена
(см. WEB_NEEDS_BROWSER_MAP). Проверку проходит человек, мы её не обходим.
"""

from __future__ import annotations

import html as H
import json
import re
from datetime import datetime, timedelta, timezone

from .model import SUMMARY_ID, Vacancy, norm_period
from .net import BlockedError, FetchError
from .sources import ATS_ROLE_RE, Ctx, Tally, parse_salary, period_from_text
from .webcommon import _job_postings, _strip_tags, check_wall, cutoff, older_than

# ──────────────────────────────────────────────────────────────────────────────
# glassdoor.com.au — стена
# ──────────────────────────────────────────────────────────────────────────────

GLASSDOOR_URL = ("https://www.glassdoor.com.au/Job/"
                 "germany-golang-jobs-SRCH_IL.0,7_IN96_KO8,14.htm")


# Карточка выдачи. Якоря — только data-атрибуты (`data-jobid`, `data-test`):
# имена CSS-классов у Glassdoor собираются с хешем (`JobCard_jobTitle__GLyJ1`)
# и меняются каждым релизом, а data-test держится их же тестами.
_GD_SPLIT = re.compile(r'(?=<li[^>]*\bdata-jobid="\d+")')
_GD_ID = re.compile(r'data-jobid="(\d+)"')
_GD_HREF = re.compile(r'data-test="job-title"[^>]*href="([^"]+)"')
_GD_HREF2 = re.compile(r'href="([^"]+)"[^>]*data-test="job-title"')
_GD_TITLE = re.compile(r'data-test="job-title"[^>]*>(.*?)</a>', re.S)
_GD_EMP = re.compile(r'compactEmployerName[^>]*>(.*?)</span>', re.S)
_GD_LOC = re.compile(r'data-test="emp-location"[^>]*>(.*?)</div>', re.S)
_GD_AGE = re.compile(r'data-test="job-age"[^>]*>(.*?)</div>', re.S)
_GD_AGE_NUM = re.compile(r"(\d+)\s*([dhmн]|дн|ч)", re.I)


def _gd_posted(age: str | None, now: datetime | None = None) -> tuple[str | None, str | None]:
    """«15d» / «24h» → дата и честная оговорка.

    Точной даты публикации в выдаче нет вовсе — есть возраст объявления. Считаем
    из него дату и ГОВОРИМ, что она вычислена: иначе «опубликовано 15.07» выглядит
    фактом с площадки, которого площадка не сообщала.
    """
    m = _GD_AGE_NUM.search(age or "")
    if not m:
        return None, None
    n, unit = int(m.group(1)), m.group(2).lower()
    delta = timedelta(hours=n) if unit in ("h", "ч") else timedelta(days=n)
    when = (now or datetime.now(timezone.utc)) - delta
    return when.isoformat(timespec="seconds"), f"дата вычислена из метки «{age.strip()}»"


def _glassdoor_cards(html: str) -> list[dict]:
    """Карточки выдачи в структуру. Пустой список — значит разметка сменилась."""
    out = []
    for chunk in _GD_SPLIT.split(html):
        mid = _GD_ID.search(chunk[:400])
        if not mid:
            continue
        href = _GD_HREF.search(chunk) or _GD_HREF2.search(chunk)
        title = _GD_TITLE.search(chunk)
        sal = re.search(rf'id="job-salary-{mid.group(1)}"[^>]*>(.*?)</div>', chunk, re.S)
        emp = _GD_EMP.search(chunk)
        loc = _GD_LOC.search(chunk)
        age = _GD_AGE.search(chunk)
        out.append({
            "id": mid.group(1),
            "url": H.unescape(href.group(1)) if href else None,
            "title": _strip_tags(title.group(1)).strip() if title else None,
            "company": _strip_tags(emp.group(1)).strip() if emp else None,
            "location": _strip_tags(loc.group(1)).strip() if loc else None,
            "salary": _strip_tags(sal.group(1)).strip() if sal else None,
            "age": _strip_tags(age.group(1)).strip() if age else None,
        })
    return out


GLASSDOOR_GRAPH = "/graph"
GLASSDOOR_PAGE = 30      # серверный размер страницы; листается суффиксом _IP<N>
# Потолок страниц. Стоял 5 с формулировкой «жадничать незачем» — при том, что
# источник вообще не листался и отдавал одну страницу. Владелец 07.08.2026:
# «glassdoor — огромное количество вакансий, пропускать их нельзя, нужен полный
# список рынка». Тридцать страниц это 900 карточек на формулировку; дальше
# останавливает не потолок, а сама выдача (пустая страница или сплошные дубли).
GLASSDOOR_MAX_PAGES = 30
GLASSDOOR_PAUSE = 2.0    # площадка за управляемым Cloudflare — ходим медленно
# Сколько страниц подряд без НОВЫХ карточек считать концом выдачи. Не одна:
# Glassdoor повторяет часть карточек между соседними страницами.
GLASSDOOR_DRY = 2

# Токен CSRF ищется в теле страницы регуляркой, а не разбором JSON: он раскидан
# по нескольким инлайновым скриптам.
#
# ЗАМЕР 05.08.2026, чтобы это не исследовали в третий раз: на НАШЕЙ странице
# (`glassdoor.com.au`, поиск) токен присутствует, но ПУСТОЙ — `"token":""`.
# Непустым его отдаёт домен `.com` (`/Job/computer-science-jobs.htm`), но токен
# привязан к домену, и подставлять его в запрос к `.com.au` бессмысленно.
# Поэтому ветка GraphQL здесь пока НЕ срабатывает и честно уступает разбору
# вёрстки — это видно в сводке источника строкой «GraphQL не сработал».
#
# Ветка оставлена намеренно: она заработает сама, если площадка вернёт токен на
# эту страницу, и стоит один поиск по уже загруженному HTML. Следующий шаг, если
# понадобится, — не подделка токена, а поиск выдачи прямо в состоянии страницы:
# в тех же 900 КБ лежит JSON с вакансиями (`userProfileJobTitle`, `locationType`),
# и разобрать его надёжнее, чем карточки по data-атрибутам.
_GD_TOKEN = re.compile(r'"token":\s*"([^"]{8,})"')

# Запрос выполняется ВНУТРИ открытой страницы: из stdlib тот же POST получает
# 403 — Cloudflare у Glassdoor режет по TLS-отпечатку клиента, а не по
# заголовкам, поэтому подделать его нечем (проверено 05.08.2026).
_GD_QUERY = """
query JobSearchResultsQuery($keyword: String, $numJobsToShow: Int!,
                            $filterParams: [FilterParams]) {
  jobListings(contextHolder: {searchParams: {keyword: $keyword,
                                             numJobsToShow: $numJobsToShow,
                                             filterParams: $filterParams}}) {
    totalJobsCount
    jobListings {
      jobview {
        header { jobTitleText employerNameFromSearch ageInDays locationName
                 payPeriod payPeriodAdjustedPay { p10 p50 p90 } payCurrencyCode }
        job { listingId jobTitleText }
      }
    }
  }
}
"""


def _gd_script(keyword: str, days: int, token: str, count: int) -> str:
    """JS, который уедет в страницу. Тело собирается здесь, чтобы в JS не было
    ни одной интерполяции руками — только JSON.stringify от готовых значений."""
    payload = json.dumps([{
        "operationName": "JobSearchResultsQuery",
        "variables": {"keyword": keyword, "numJobsToShow": count,
                      "filterParams": [{"filterKey": "fromAge", "values": str(days)}]},
        "query": _GD_QUERY,
    }], ensure_ascii=False)
    return (
        "async () => {"
        f"  const r = await fetch({json.dumps(GLASSDOOR_GRAPH)}, {{"
        "     method: 'POST',"
        "     headers: {'content-type': 'application/json',"
        f"               'gd-csrf-token': {json.dumps(token)},"
        "               'apollographql-client-name': 'job-search-next'},"
        f"     body: {json.dumps(payload)} }});"
        "  return {status: r.status, text: (await r.text()).slice(0, 400000)};"
        "}"
    )


def src_glassdoor(ctx: Ctx) -> list[Vacancy]:
    """Glassdoor: сначала его собственный GraphQL, при отказе — разбор вёрстки.

    Почему GraphQL и почему именно ИЗ СТРАНИЦЫ. Тот же POST на `/graph` из
    stdlib получает 403: Cloudflare у них смотрит на TLS-отпечаток клиента, а
    не на заголовки, — подделать нечем, и правильно. Зато со страницы, которую
    настоящий браузер пользователя уже открыл и проверку прошёл сам, запрос
    уходит нормально: он same-origin.

    Что это даёт против разбора карточек: настоящее окно по дате на СТОРОНЕ
    площадки (`fromAge`) вместо нашего постфильтра по метке «15d», серверный
    поиск по тексту и стабильные поля вместо `data-jobid`-атрибутов, которые
    держатся только на их же тестах.

    Капчу и Cloudflare это не обходит: увидели челлендж — BlockedError и статус
    АНТИБОТ, как было.
    """
    try:
        return _src_glassdoor_api(ctx)
    except (FetchError, BlockedError, ValueError, KeyError) as e:
        if isinstance(e, BlockedError):
            raise  # стена — это стена, разбором вёрстки её не обойти
        rows = _src_glassdoor_html(ctx)
        for v in rows:
            if v.external_id == SUMMARY_ID:
                v.raw.setdefault("notes", []).insert(
                    0, f"GraphQL не сработал ({str(e)[:110]}) — разбор вёрстки")
        return rows


def _src_glassdoor_api(ctx: Ctx) -> list[Vacancy]:
    """GraphQL Glassdoor изнутри открытой страницы."""
    from .render import evaluate_on, render_page  # noqa: PLC0415

    tally = Tally("glassdoor")
    url = getattr(ctx, "glassdoor_url", GLASSDOOR_URL)
    html, _final = render_page(url, wait=5.0, browser=_real_browser())
    tally.requests += 1
    m = _GD_TOKEN.search(html)
    if not m:
        raise FetchError(url, "на странице нет csrf-токена — разметка сменилась")
    count = min(GLASSDOOR_PAGE * GLASSDOOR_MAX_PAGES, 150)
    query = (ctx.queries() or ["golang"])[0]
    got = evaluate_on(url, _gd_script(query, ctx.days, m.group(1), count), wait=3.0)
    tally.requests += 1
    if not isinstance(got, dict) or got.get("status") != 200:
        raise FetchError(url, f"GraphQL ответил {(got or {}).get('status')}")
    data = json.loads(got.get("text") or "[]")
    block = (data[0] if isinstance(data, list) and data else data) or {}
    listings = (((block.get("data") or {}).get("jobListings") or {})
                .get("jobListings"))
    if listings is None:
        raise FetchError(url, "в ответе GraphQL нет jobListings — схема сменилась")
    tally.pages += 1
    _gd_api_rows(listings, out := [], set(), tally)
    total = ((block.get("data") or {}).get("jobListings") or {}).get("totalJobsCount")
    tally.note(f"GraphQL: в выдаче {total if total is not None else '?'}, "
               f"взято {tally.kept}; окно --days применяет площадка (fromAge)")
    if not out and listings:
        # Строки приехали, а не разобралась ни одна — это поломка разбора,
        # и молчать про неё нельзя: снаружи она неотличима от «вакансий нет».
        raise FetchError(url, f"GraphQL отдал {len(listings)} строк, "
                              f"разобрать не удалось ни одной")
    out.append(tally.row())
    return out


def _gd_api_rows(listings: list, out: list[Vacancy], seen: set[str],
                 tally: Tally) -> None:
    for item in listings:
        tally.offered += 1
        view = (item or {}).get("jobview") or {}
        head = view.get("header") or {}
        job = view.get("job") or {}
        vid = str(job.get("listingId") or "")
        title = (head.get("jobTitleText") or job.get("jobTitleText") or "").strip()
        if not vid or not title:
            tally.dropped += 1
            continue
        if vid in seen:
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        tally.kept += 1
        pay = head.get("payPeriodAdjustedPay") or {}
        out.append(Vacancy(
            source="glassdoor",
            external_id=vid,
            url=f"https://www.glassdoor.com/job-listing/j?jl={vid}",
            title=title,
            company=(head.get("employerNameFromSearch") or "").strip() or None,
            # p50 — медиана оценки площадки, а не вилка работодателя. В поля
            # денег идут p10/p90 как границы, а сам факт «это оценка Glassdoor»
            # едет в raw: выдать её за предложение нанимателя значит соврать.
            salary_from=pay.get("p10"), salary_to=pay.get("p90"),
            currency=head.get("payCurrencyCode"),
            salary_period=norm_period(head.get("payPeriod")),
            location=head.get("locationName"),
            raw={"shape": "graphql", "path": "api",
                 "age_days": head.get("ageInDays"),
                 "pay_is_estimate": bool(pay),
                 "pay_median": pay.get("p50")},
        ))


def _glassdoor_page_url(url: str, page: int) -> str:
    """Страница N выдачи. Glassdoor листается суффиксом `_IP<N>` перед `.htm`."""
    if page <= 1:
        return url
    base, _, tail = url.rpartition(".htm")
    return f"{base}_IP{page}.htm{tail}" if base else url


def _real_browser() -> str:
    """Настоящий браузер для Glassdoor. Встроенный шелл сюда не годится.

    Это не предпочтение, а устройство площадки: у неё управляемая проверка
    Cloudflare, и встроенный chromium её НЕ ПРОХОДИТ — замер 07.08.2026 на одной
    и той же странице дал «Один момент…» у chromium и 963 КБ живой выдачи
    у настоящего Chrome на постоянном профиле scout. Проверку проходит человек
    один раз, её результат оседает в профиле, и дальше выдача читается сама.
    Ничего не подделывается: запрос делает тот самый браузер, которому площадка
    уже ответила.

    Настоящего браузера нет — возвращаем None и честно упираемся в стену: это
    видно строкой «АНТИБОТ» в покрытии, а не притворяется нулём вакансий.
    """
    from .render import BUNDLED, installed_browsers  # noqa: PLC0415

    real = [b for b in installed_browsers() if b != BUNDLED]
    return real[0] if real else None


def _src_glassdoor_html(ctx: Ctx) -> list[Vacancy]:
    """glassdoor.com.au — только через браузер пользователя, и только если стена снята.

    История площадки в двух замерах. 30.07.2026 утром: stdlib-GET → HTTP 403
    «Security | Glassdoor», рендер настоящим Chromium → 344 КБ страницы
    «Один момент…» с `captcha-container`. Тем же вечером, в том же профиле,
    стена не показалась и страница отдалась целиком — 82 вакансии по golang.

    Отсюда всё устройство функции: **проверку мы не проходим и капчу не решаем**,
    но если браузер пользователя её не увидел, выдачу надо разобрать, а не
    выбросить. Стена → BlockedError («АНТИБОТ» в покрытии, чинится заходом
    человека). Нет стены и нет карточек → FetchError: это сменившаяся разметка,
    а не «ноль вакансий».

    Разбор двухслойный: сначала ld+json (JobPosting) — его Glassdoor отдаёт
    на страницах отдельных вакансий, — потом карточки выдачи по data-атрибутам.
    """
    tally = Tally("glassdoor")
    url = getattr(ctx, "glassdoor_url", GLASSDOOR_URL)
    from .render import render_pages  # noqa: PLC0415 — Playwright опционален

    browser = _real_browser()
    out: list[Vacancy] = []
    edge = cutoff(ctx.days)
    postings: list[dict] = []
    cards: list[dict] = []
    seen_ids: set[str] = set()
    dry = 0
    final = url
    # Все страницы в ОДНОМ окне: перезапуск браузера на каждую стоил семидесяти
    # секунд из семидесяти пяти (замер 07.08.2026 — 277 с на четыре страницы).
    # Генератор ленивый, поэтому лишние страницы не грузятся: выходим по дублям,
    # пустой выдаче или потолку, и на этом обход прекращается.
    pages = render_pages(
        (_glassdoor_page_url(url, n) for n in range(1, GLASSDOOR_MAX_PAGES + 1)),
        browser=browser, wait=5.0, pause=GLASSDOOR_PAUSE)
    wall: str | None = None
    for _asked, html, final in pages:
        tally.requests += 1
        # Стена на СЕРЕДИНЕ пагинации не отменяет уже собранного. Cloudflare
        # выставляет проверку не всегда, а по нагрузке: замер 07.08.2026 —
        # первая страница прошла, вторая упёрлась. Прежний код бросал
        # BlockedError прямо отсюда, и весь источник возвращал НОЛЬ, выбросив
        # разобранные страницы. Это та же потеря, что «пустой ответ = конец
        # выдачи», только дороже: собранное уже лежало в руках.
        try:
            check_wall(html, final)
        except BlockedError as e:
            wall = str(e)
            break
        page_postings = _job_postings(html)
        page_cards = _glassdoor_cards(html) if not page_postings else []
        if not page_postings and not page_cards:
            break                    # выдача кончилась
        fresh = [c for c in page_cards if (c.get("id") or "") not in seen_ids]
        seen_ids.update(c.get("id") or "" for c in page_cards)
        postings.extend(page_postings)
        cards.extend(fresh)
        tally.pages += 1
        # Соседние страницы у Glassdoor пересекаются, поэтому концом выдачи
        # считается не «мало нового», а НИСКОЛЬКО нового подряд.
        dry = 0 if (fresh or page_postings) else dry + 1
        if dry >= GLASSDOOR_DRY:
            break
    if wall and not postings and not cards:
        # Ни одной страницы не прошло — это честная стена, статус АНТИБОТ.
        raise BlockedError(final, wall)
    if wall:
        tally.note(f"СТЕНА на стр. {tally.pages + 1}: собрано {tally.pages} страниц "
                   f"до неё, дальше не пошли. Это НЕ полная выдача — "
                   f"проверку проходит человек, зайди браузером сам")
    if not postings and not cards:
        raise FetchError(final, "стена не сработала, но и вакансий в разметке нет — "
                                "разбирать нечего, проверь страницу глазами")
    if tally.pages >= GLASSDOOR_MAX_PAGES:
        tally.note(f"ОБРЕЗАНО по потолку страниц ({GLASSDOOR_MAX_PAGES} × "
                   f"{GLASSDOOR_PAGE}) — за остальным нужен потолок выше")

    for j in postings:
        tally.offered += 1
        jurl = j.get("url") or ""
        if not jurl:
            tally.dropped += 1
            continue
        tally.parsed += 1
        org = j.get("hiringOrganization") or {}
        addr = ((j.get("jobLocation") or {}).get("address") or {})
        base = j.get("baseSalary") or {}
        val = base.get("value") if isinstance(base.get("value"), dict) else {}
        v = Vacancy(
            source="glassdoor",
            external_id=str(j.get("identifier", {}).get("value")
                            or re.sub(r"\D", "", jurl)[-12:] or jurl),
            url=jurl,
            title=j.get("title") or "",
            company=org.get("name"),
            salary_from=val.get("minValue"), salary_to=val.get("maxValue"),
            currency=base.get("currency"),
            salary_period=norm_period(val.get("unitText")),
            location=", ".join(str(x) for x in (addr.get("addressLocality"),
                                                addr.get("addressCountry")) if x) or None,
            published_at=j.get("datePosted"),
            description=_strip_tags(j.get("description")),
            raw={"note": "снято рендером; площадка обычно закрыта антибот-стеной",
                 "shape": "ld+json"},
        )
        if older_than(v.published_at, edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        out.append(v)

    for c in cards:
        tally.offered += 1
        if not (c["url"] and c["title"]):
            tally.dropped += 1
            continue
        tally.parsed += 1
        # «EUR 90K - EUR 130K (Employer provided)»: суффикс тысяч разворачивает
        # общий parse_salary, а вот период Glassdoor в выдаче не называет вовсе —
        # и подставлять «в месяц» тут значит выдумать условия за работодателя.
        sf, st, cur, gross = parse_salary(c["salary"])
        posted, note = _gd_posted(c["age"])
        v = Vacancy(
            source="glassdoor",
            external_id=c["id"],
            url=c["url"],
            title=c["title"],
            company=c["company"],
            salary_from=sf, salary_to=st, currency=cur, salary_gross=gross,
            salary_period=period_from_text(c["salary"]),
            location=c["location"],
            published_at=posted,
            raw={"shape": "карточка выдачи", "age_label": c["age"],
                 "salary_label": c["salary"], "date_note": note,
                 "note": "снято браузером пользователя; ld+json на странице поиска нет"},
        )
        if not (ctx.ats_all or ATS_ROLE_RE.search(v.title or "")):
            tally.skipped_profile += 1
            continue
        if older_than(v.published_at, edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        out.append(v)

    if cards:
        tally.note("ld+json на странице поиска нет — разобраны карточки выдачи "
                   "по data-атрибутам; дата вычислена из метки возраста («15d»)")
    # Своё число площадки уходит в `tally.claimed`, а не в примечание: там оно
    # СХОДИТСЯ с остальными счётчиками одной строкой «РАЗРЫВ 75 → 28: …».
    # Примечанием оно лежало рядом с ними и ни с чем не сходилось, и «взято 28
    # при заявленных 75» каждый раз приходилось раскладывать руками.
    total = re.search(r'data-test="search-title"[^>]*>(\d+)', html)
    if total:
        tally.claimed = int(total.group(1))
    tally.note("снято браузером (render.py); анонимный GET площадка не отдаёт")
    out.append(tally.row())
    return out
