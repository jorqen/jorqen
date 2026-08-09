"""Раскрытие прямого контакта hirehi — единственная команда, которая ТРАТИТ лимит.

Механика площадки (реверс 30.07.2026): настоящий контакт работодателя отдаёт только
POST /api/limits/consume {type: 'direct_contact', job_id, contact_ticket} под Bearer,
и каждый вызов списывает лимит раскрытий (он восстанавливается). Клик по кнопке
<a data-apply-link="true"> на странице вакансии делает всё это сам: клиент достаёт
токен, при истечении сам зовёт POST /api/auth/refresh, делает consume и открывает
open_url из ответа. Мы ровно это и используем: один клик по кнопке залогиненной
страницы, ответ consume подслушивается, сами приватных ручек не дёргаем.

Списание разрешено пользователем 30.07.2026: «списывай раскрытие контакта, если
вакансия релевантна; идемпотентно, лимиты восстанавливаются; лишний раз не нажимай».
Решение о релевантности принимает вызывающий (модель/человек) ДО запуска команды.

Про refresh-токен hirehi: он ОДИН на все заходы и РОТИРУЕТСЯ каждым
POST /api/auth/refresh — у того, кто обновил его не последним, сессия протухает
мгновенно и выглядит не как «войдите», а как 403/аноним. Поэтому по умолчанию
работает СОБСТВЕННАЯ сессия scout из .auth/hirehi.json (одноразовый
`scout auth login hirehi`), и обновлённый storage_state после прогона
перезаписывается туда же — ротация оседает у нас.

`--from-browser` берёт куки из браузера пользователя и, значит, СОЗНАТЕЛЬНО
роняет его живую вкладку hirehi: ротация уедет к нам. Это прямое разрешение
пользователя в чате от 04.08.2026 («пусть убьёт мою живую вкладку, ничего в этом
такого нет»), и включается только этим флагом — умолчание прежнее.

Предохранители (все обязательны, ни один не выключается флагом):
1. нет сессии и нет --from-browser → код 2 и инструкция;
2. кликается ТОЛЬКО кнопка data-apply-link — раскрытие контакта, то самое, что
   разрешено; формы не заполняются и не отправляются, других кликов нет;
3. уже раскрытое повторно не кликается: контакт берётся из базы (идемпотентность);
4. --limit N (по умолчанию 5) раскрытий за прогон — упёрся: честная строка и стоп;
5. consume ответил rate_limited/allowed=false → печать message и остановка;
6. страница анонимна (VACANCY_DATA.is_authenticated=false / contact_ticket пуст) →
   код 2 «сессия протухла», БЕЗ клика: у анонима кнопка ведёт не туда.

Коды возврата: 0 — все контакты раскрыты; 1 — часть не раскрылась (сеть/лимит);
2 — нет или протухла сессия; 3 — нет playwright.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
from dataclasses import dataclass

from . import auth, store
from .net import UA

CONSUME_PATH = "/api/limits/consume"

NO_SESSION = """нет .auth/hirehi.json — собственной сессии scout для hirehi не существует.
  scout auth login hirehi — одноразовый вход, дальше сессия живёт у scout.
  Куки твоего браузера эта команда НЕ читает намеренно: refresh-токен hirehi
  ротируется на каждом обновлении, и заход с ними сжёг бы сессию в твоей живой вкладке."""

PLAYWRIGHT_HOWTO = """Нужен Playwright — раскрытие делает клик на настоящей странице.
  pip install playwright && playwright install chromium"""


# ──────────────────────────────────────────────────────────────────────────────
# Чистые функции — их и тестируем (сеть/браузер сюда не заходят)
# ──────────────────────────────────────────────────────────────────────────────

def job_id_from_url(url: str) -> str | None:
    """id вакансии из URL hirehi. Чужой хост и страницы поиска → None:
    команда со списанием обязана отказаться от всего, что не вакансия hirehi."""
    host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
    if host != "hirehi.ru":
        return None
    from .detail import _hirehi_job_id  # noqa: PLC0415 — тот же разбор, что у деталки
    return _hirehi_job_id(url)


def contact_kind(open_url: str | None) -> str | None:
    """Тип контакта по open_url: t.me/tg → telegram, mailto → email, http(s) → ссылка."""
    if not open_url:
        return None
    u = open_url.strip().lower()
    if u.startswith("mailto:"):
        return "email"
    if u.startswith(("tg:", "tg://")) or re.search(r"(?:^|//)(?:www\.)?t\.me/", u):
        return "telegram"
    if u.startswith(("http://", "https://")):
        return "ссылка"
    return "контакт"


@dataclass
class Consume:
    """Плоский разбор ответа /api/limits/consume."""
    allowed: bool
    open_url: str | None
    kind: str | None          # telegram | email | ссылка | контакт | None
    remaining: int | None     # None = площадка остатка не назвала, а не ноль
    rate_limited: bool
    message: str | None


def parse_consume(data) -> Consume:
    """Ответ consume → Consume. Терпим к форме: поля allowed может не быть вовсе,
    тогда живой open_url и есть согласие; rate_limited гасит allowed всегда."""
    if not isinstance(data, dict):
        return Consume(allowed=False, open_url=None, kind=None, remaining=None,
                       rate_limited=False,
                       message="ответ consume не разобрался (не объект)")
    open_url = (data.get("open_url") or "").strip() or None
    remaining = data.get("remaining")
    if remaining is None:
        for k, v in data.items():
            if isinstance(v, int) and not isinstance(v, bool) \
                    and ("remaining" in k or "left" in k):
                remaining = v
                break
    rate_limited = bool(data.get("rate_limited"))
    if rate_limited:
        allowed = False
    elif "allowed" in data:
        allowed = bool(data["allowed"])
    else:
        allowed = open_url is not None
    return Consume(allowed=allowed, open_url=open_url, kind=contact_kind(open_url),
                   remaining=remaining, rate_limited=rate_limited,
                   message=data.get("message") or data.get("error") or None)


def page_state(vacancy_data) -> tuple[str, str]:
    """('ok' | 'anonymous' | 'unknown', пояснение) по window.VACANCY_DATA страницы.

    'anonymous' — кликать НЕЛЬЗЯ: у залогиненного кнопка делает consume, у анонима
    ведёт на форму логина; оба исхода без сессии — не то, за чем пришли."""
    if not isinstance(vacancy_data, dict):
        return "unknown", ("на странице нет VACANCY_DATA — это не вакансия hirehi "
                           "или вёрстка сменилась")
    if not vacancy_data.get("is_authenticated"):
        return "anonymous", "is_authenticated=false"
    # contact_ticket в состоянии страницы ПУСТ у живой сессии: билет выдаётся
    # сервером на сам клик, а не кладётся в стейт заранее. Проверка «пусто =
    # сессия протухла» блокировала раскрытие при полностью рабочем входе
    # (живой прогон 04.08.2026: is_authenticated=true, direct_left 3 из 5,
    # кнопка data-apply-link на месте — и всё равно «протухла»).
    # Решает ровно один признак — is_authenticated.
    left = (vacancy_data.get("free_limits") or {}).get("direct_left")
    if left == 0 and not vacancy_data.get("has_pro"):
        return "no_limits", "лимит раскрытий исчерпан (direct_left=0) — клик не поможет"
    tail = f", раскрытий осталось {left}" if left is not None else ""
    return "ok", f"сессия жива{tail}"


# ──────────────────────────────────────────────────────────────────────────────
# База: идемпотентность и запись контакта
# ──────────────────────────────────────────────────────────────────────────────

def _already_revealed(db: str, jid: str) -> str | None:
    """Контакт из прошлого раскрытия, если он уже в базе. Повторный клик списал бы
    лимит второй раз за то, что уже куплено, — ровно это пользователь и запретил."""
    try:
        with store.connect(db) as conn:
            payload = store.get_detail_payload(conn, "hirehi", jid) or {}
    except Exception:  # noqa: BLE001 — нечитаемая база не повод падать до браузера
        return None
    return payload.get("contact") or None


def _save_contact(db: str, jid: str, url: str, c: Consume) -> None:
    """Дописывает контакт в payload таблицы detail, не затирая выжимку enrich."""
    try:
        with store.connect(db) as conn:
            payload = store.get_detail_payload(conn, "hirehi", jid) or {
                "source": "hirehi", "url": url}
            payload["contact"] = c.open_url
            payload["contact_kind"] = c.kind
            payload["contact_revealed_at"] = store.now()
            row = conn.execute(
                "SELECT status, page_state FROM detail "
                "WHERE source='hirehi' AND external_id=?", (jid,)).fetchone()
            # page_state переносим вместе со статусом: запись идёт через
            # INSERT OR REPLACE, и без этого дописанный контакт стирал бы
            # состояние страницы — снятая вакансия возвращалась бы в очередь
            # закачки, причём ровно та, до которой мы дошли руками.
            store.save_detail(conn, "hirehi", jid, url,
                              row["status"] if row else "ok", payload=payload,
                              page_state=row["page_state"] if row else None)
    except Exception as e:  # noqa: BLE001 — контакт уже напечатан, база его не отменит
        print(f"  ⚠️  контакт в базу не записался: {type(e).__name__}: {e}",
              file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────────
# Сам прогон
# ──────────────────────────────────────────────────────────────────────────────

def _capture_consume(resp, got: dict) -> None:
    if CONSUME_PATH not in resp.url or "payload" in got:
        return
    try:
        got["payload"] = resp.json()
    except Exception:  # noqa: BLE001 — не-JSON от consume называется, а не глотается
        got["error"] = f"consume ответил не-JSON (HTTP {resp.status})"


def _close_popups(popups: list) -> None:
    """Клик открывает open_url новой вкладкой (window.open) — нам нужен только
    ответ consume; чужую страницу (t.me, почта, сайт) не грузим, закрываем сразу."""
    for p in popups:
        try:
            p.close()
        except Exception:  # noqa: BLE001
            pass
    popups.clear()


# Метка долга в `research.verdict`. Отдельного поля не заводим намеренно:
# долг — это вердикт ресёрча («контакт не добыт, вот почему»), а не новая
# сущность, и `brief`/`card` печатают вердикт и так.
# Площадки, где контакт работодателя виден БЕЗ траты лимита: у hh и ATS есть
# форма отклика, у careered контакт открывается сессией. Телеграм-пост сюда не
# входит намеренно — он витрина, а не контакт (см. tgpost).
_FREE_HOSTS = ("hh.ru", "career.habr.com", "careered.io", "getmatch.ru",
               "geekjob.ru", "rabota.ru", "trudvsem.ru",
               "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
               "recruitee.com", "smartrecruiters.com", "teamtailor.com",
               "huntflow.ru")


def careered_contact_open(job: dict) -> bool:
    """У careered контакт РЕАЛЬНО открыт? (по ответу их API)

    🔴 careered делит вакансии на бесплатные и платные. У бесплатных живая
    сессия открывает контакт всегда; у платных он зарезан даже с Bearer:
    `mode: "preview"` и `links.telegram == "#"`. Считать такую вакансию
    «контакт есть бесплатно» — значит отговорить от раскрытия там, где оно и
    было единственным путём (живой случай с Remoby 09.08.2026).

    Ссылки обратно на сам careered контактом работодателя не являются: это
    витрина, а не наниматель.
    """
    if not isinstance(job, dict) or job.get("mode") != "full":
        return False
    for link in (job.get("links") or []):
        val = str((link or {}).get("value") or "")
        if not val or val == "#" or "careered.io" in val:
            continue
        if val.startswith(("http://", "https://", "mailto:", "tg:")):
            return True
    return False


def free_contact_for(conn, url: str) -> str | None:
    """Та же вакансия на площадке, где контакт бесплатен. None — не нашлось.

    🔴 Раскрытие у hirehi тратит невосполнимый в моменте лимит, поэтому сначала
    ищем то же самое даром (требование владельца 09.08.2026). Первое место —
    СВОЯ БАЗА: одна компания обычно висит на нескольких площадках сразу, и на
    hh или ATS её контакт открыт. Живой счёт: у Remoby с hirehi нашлись записи
    на careered и на hh.

    Ищем по имени компании, а не по тексту: тексты площадки переписывают, а имя
    работодателя совпадает. Сама исходная вакансия из результата исключается.
    """
    row = conn.execute("SELECT company, source, external_id FROM vacancy "
                       "WHERE url = ? LIMIT 1", (url,)).fetchone()
    if not row or not row["company"]:
        return None
    from .shortlist import company_aliases  # noqa: PLC0415 — общий нормализатор
    keys = set(company_aliases(row["company"]))
    if not keys:
        return None
    cur = conn.execute(
        "SELECT url, company FROM vacancy WHERE url != ? AND company IS NOT NULL "
        "ORDER BY last_seen DESC", (url,))
    for cand in cur:
        if not (keys & set(company_aliases(cand["company"]))):
            continue
        host = (cand["url"].split("/")[2] if "://" in cand["url"] else "").lower()
        host = host.removeprefix("www.")
        if not any(host == h or host.endswith("." + h) for h in _FREE_HOSTS):
            continue
        # careered обещает контакт не всегда: у платных вакансий он зарезан
        # даже с живой сессией. Проверяем, а не верим домену.
        if host.endswith("careered.io"):
            try:
                from . import auth as _auth  # noqa: PLC0415
                from .net import fetch_json  # noqa: PLC0415
                jid = cand["url"].rstrip("/").rsplit("/", 1)[-1]
                tok, _ = _auth.bearer_from_state("careered")
                job = fetch_json(f"https://careered.io/api/jobs/{jid}",
                                 headers={"Authorization": f"Bearer {tok}"} if tok else None)
            except Exception:  # noqa: BLE001 — сеть не должна ронять предполёт
                continue
            if not careered_contact_open(job if isinstance(job, dict) else {}):
                continue
        return cand["url"]
    return None


DEBT_MARK = "КОНТАКТ НЕ РАСКРЫТ"


def note_debt(conn, url: str, *, why: str) -> bool:
    """Запоминает, что контакт добыть не вышло. True — записали.

    🔴 Лимит раскрытий у hirehi восстанавливается, поэтому «не смогли сегодня»
    означает «вернуться завтра», а не «забыть». Раньше `reveal` печатал «лимит
    исчерпан» в консоль, и вакансия жила дальше только в памяти агента — то
    есть до конца сессии (требование владельца 09.08.2026 закрыть эту дыру).
    """
    row = conn.execute("SELECT source, external_id FROM vacancy WHERE url = ? LIMIT 1",
                       (url,)).fetchone()
    if not row:
        return False
    store.save_research(conn, row["source"], row["external_id"],
                       verdict=f"{DEBT_MARK}: {why}. Вернуться, когда лимит восстановится")
    return True


# Чем долг закрывается, а чем нет. Долг заведён из-за ОТСУТСТВИЯ контакта,
# поэтому «вот ссылка на витрину» его не закрывает: с витрины мы и пришли.
# 🔴 Живой прогон 09.08.2026 показал цену пропуска этой проверки сразу: обход
# дублей вернул `kind="витрина"` с честным пояснением «прямого канала обход не
# нашёл», а долг всё равно снялся — у Remoby платной careered-записью, у
# Teleport вакансией ОДНОИМЁННОЙ американской компании на LinkedIn. Оба долга
# выглядели закрытыми, писать было некуда.
REAL_CONTACT = frozenset({"почта найма", "telegram", "ATS работодателя",
                          "вакансия на сайте работодателя"})


def clear_debt(conn, url: str, *, contact: str, why: str) -> bool:
    """Снимает долг: контакт добыт даром. True — сняли."""
    row = conn.execute("SELECT source, external_id FROM vacancy WHERE url = ? LIMIT 1",
                       (url,)).fetchone()
    if not row:
        return False
    store.save_research(conn, row["source"], row["external_id"],
                        verdict=f"КОНТАКТ НАЙДЕН БЕСПЛАТНО: {contact} — {why}")
    return True


def twin_anywhere(conn, url: str) -> list[str]:
    """Та же компания на ЛЮБОЙ другой площадке, свежие первыми.

    Отличие от `free_contact_for`: там список площадок, где контакт открыт
    сразу, здесь — любая запись, потому что контакт может лежать не в ней
    самой, а за её ссылками (телеграм-пост → страница вакансии → телеграм
    рекрутёра). Достаёт его обход, а не сама запись.
    """
    row = conn.execute("SELECT company FROM vacancy WHERE url = ? LIMIT 1",
                       (url,)).fetchone()
    if not row or not row["company"]:
        return []
    from .shortlist import company_aliases  # noqa: PLC0415
    keys = set(company_aliases(row["company"]))
    if not keys:
        return []
    out = []
    for cand in conn.execute("SELECT url, company FROM vacancy WHERE url != ? "
                             "AND company IS NOT NULL ORDER BY last_seen DESC", (url,)):
        if keys & set(company_aliases(cand["company"])):
            out.append(cand["url"])
    return out[:6]


def resolve_debt(conn, url: str, *, db: str) -> dict | None:
    """Пробует добыть контакт по долгу ДАРОМ. None — не вышло.

    Три пути, по убыванию дешевизны, и все три уже есть в проекте — здесь они
    только связаны в один порядок:

      1. та же вакансия на площадке с открытым контактом (`free_contact_for`);
      2. та же компания на любой другой площадке + ОБХОД её ссылок: контакт
         часто лежит не в записи, а за ней (живой счёт 09.08.2026: Teleport с
         hirehi нашёлся телеграм-постом на shadowhint, пост вёл на vseti.app,
         и уже там стоял телеграм рекрутёра);
      3. зонд собственного сайта компании (`channel`): у Remoby весь контакт —
         info@remoby.com на главной.

    Ради этого механизм и написан: три долга прошлой волны я закрывал руками,
    и следующая волна принесла бы ту же ручную работу.
    """
    free = free_contact_for(conn, url)
    if free:
        return {"contact": free, "why": f"та же компания на площадке с открытым контактом"}

    from . import crawl as _crawl  # noqa: PLC0415 — обход тяжёлый, грузим по нужде
    for twin in twin_anywhere(conn, url):
        try:
            # Именно `walk`, а не голый `crawl`: стартовые ссылки собирает
            # `applyopt.gather`, и только он умеет вскрыть телеграм-пост —
            # достать ссылки ИЗ ЕГО ТЕЛА. Голый обход отбрасывал сам пост
            # фильтром «соцсеть работодателем не бывает» и возвращал ноль
            # узлов: дубль Teleport, у которого контакт как раз и лежал за
            # постом, «не находился» (09.08.2026).
            # `force`: кэш обхода хранит МАРШРУТЫ, а контакт-ник в нём не живёт.
            # Без переобхода `contact_from_routes` отвечает «прямой канал из
            # базы» — то есть ссылкой, а долг заведён именно из-за отсутствия
            # контакта. Долги разбираются редко и по прямой просьбе, так что
            # свежий обход тут дешевле промаха.
            res, found = _crawl.walk(conn, twin, force=True)
        except Exception:  # noqa: BLE001 — сеть не должна ронять разбор долгов
            continue
        best = (_crawl.best_contact(res) if res is not None
                else _crawl.contact_from_routes(found))
        if best and best.get("kind") in REAL_CONTACT:
            return {"contact": best["value"],
                    "why": f"обход дубля {twin}: {best.get('why') or best.get('kind')}"}

    row = conn.execute("SELECT company FROM vacancy WHERE url = ? LIMIT 1",
                       (url,)).fetchone()
    company = (row["company"] or "").strip() if row else ""
    if company:
        from . import channel as _channel  # noqa: PLC0415
        try:
            res = _channel.find(company, render=True)
        except Exception:  # noqa: BLE001
            res = {}
        pick = _channel.best(res.get("hits") or [])
        if pick:
            value = (pick.get("mails") or [None])[0] or pick.get("url")
            if value:
                return {"contact": value, "why": f"зонд сайта компании: {pick.get('why')}"}
    return None


def pending_reveals(conn) -> list[dict]:
    """Вакансии, по которым контакт остался нераскрытым. Список для следующей волны."""
    cur = conn.execute(
        "SELECT r.source, r.external_id, r.verdict, v.url, v.title, v.company "
        "FROM research r JOIN vacancy v "
        "  ON v.source = r.source AND v.external_id = r.external_id "
        "WHERE r.verdict LIKE ? ORDER BY r.checked_at DESC", (f"{DEBT_MARK}%",))
    return [{"url": r["url"], "title": r["title"], "company": r["company"],
             "why": r["verdict"]} for r in cur.fetchall()]


def preflight(urls: list[str], *, liveness: dict[str, str] | None = None,
              free_contact: dict[str, str] | None = None) -> list[dict]:
    """План раскрытия: [{url, spend, why}] — на что лимит тратить, а на что нет.

    🔴 Лимит раскрытий у hirehi очень маленький, и каждый клик необратим.
    Требование владельца 09.08.2026: до траты убедиться, что вакансия жива и
    что того же контакта нет бесплатно — та же вакансия часто лежит на другой
    площадке, где контакт открыт, или у работодателя на careers-странице.

    Функция ничего не решает за человека и никуда не ходит: она сводит уже
    известные факты (живость от `check-links`, найденный бесплатный канал от
    `channel`/`employer_channel`) в явный план. Раньше это решение принимал
    агент по памяти — то есть иногда не принимал вовсе.
    """
    live = liveness or {}
    free = free_contact or {}
    plan: list[dict] = []
    for url in urls:
        verdict = (live.get(url) or "").upper()
        if verdict == "МЕРТВА":
            plan.append({"url": url, "spend": False,
                         "why": "вакансия мертва — раскрывать нечего"})
            continue
        if free.get(url):
            plan.append({"url": url, "spend": False,
                         "why": f"контакт есть бесплатно: {free[url]}"})
            continue
        plan.append({"url": url, "spend": True,
                     "why": ("живая, бесплатного контакта не нашлось — "
                             + ("живость подтверждена" if verdict == "ЖИВА"
                                else "живость не проверена, но и не опровергнута"))})
    return plan


def plan(urls: list[str], *, db: str = store.DEFAULT_DB, walk: bool = True,
         depth: int = 1) -> list[dict]:
    """План раскрытия ПО ФАКТАМ: где контакт есть даром, там лимит не тратим.

    🔴 Требование владельца 09.08.2026 и причина, по которой обход ссылок
    подключён именно сюда: раскрытие необратимо тратит маленький лимит, а
    контакт часто лежит рядом бесплатно. Два источника, оба до единого клика:

      1. своя база — та же компания на площадке, где контакт открыт
         (`free_contact_for`); сети не требует вовсе;
      2. обход ссылок вакансии — careers-страница, доска ATS или почта найма
         на сайте работодателя (`crawl.walk`). Раньше это делалось руками и
         потому делалось не всегда.

    Обход кэшируется в базе, поэтому повторный прогон по тем же вакансиям
    ничего не стоит. `walk=False` (флаг `--no-crawl`) оставлен для случая
    «сеть недоступна или её жалко» — тогда решение принимается по базе.

    Глубина здесь 1, а не 2: нужен факт «бесплатный контакт существует», а не
    полная карта сайта компании. Полную даёт `scout crawl`.
    """
    from . import crawl as C  # noqa: PLC0415 — ленивый: reveal живёт и без обхода

    live: dict[str, str] = {}
    free: dict[str, str] = {}
    with store.connect(db) as conn:
        for url in urls:
            same = free_contact_for(conn, url)
            if same:
                free[url] = f"та же вакансия там, где контакт открыт: {same}"
                continue
            if not walk:
                continue
            try:
                res, found = C.walk(conn, url, depth=depth)
            except Exception as e:  # noqa: BLE001 — обход не имеет права сорвать раскрытие
                print(f"{url}\n  обход ссылок не вышел ({type(e).__name__}: {e}) — "
                      f"решаю по базе", file=sys.stderr)
                continue
            best = C.best_contact(res) if res is not None else C.contact_from_routes(found)
            verdict = (C.liveness(res)[0] if res is not None
                       else C.liveness_from_routes(found))
            if verdict:
                live[url] = verdict
            # Витрина бесплатным контактом НЕ считается: ради обхода витрины
            # раскрытие и затевается. Годится только прямой канал работодателя.
            if best and best["kind"] != "витрина":
                free[url] = f"{best['kind']}: {best['value']} ({best['why']})"
    return preflight(urls, liveness=live, free_contact=free)


def plan_lines(steps: list[dict]) -> list[str]:
    """План раскрытия строками. Отдельно от печати — чтобы его можно было
    проверить тестом, не поднимая браузер."""
    out = [f"План раскрытия: {sum(1 for s in steps if s['spend'])} из "
           f"{len(steps)} — на остальные лимит не тратится"]
    for s in steps:
        out.append(f"  {'СПИСАТЬ' if s['spend'] else 'не тратить'}  {s['url']}")
        out.append(f"      {s['why']}")
    return out


def reveal(urls: list[str], *, limit: int = 5, db: str = store.DEFAULT_DB,
           from_browser: str | None = None, walk: bool = True,
           dry_run: bool = False) -> int:
    """Раскрывает прямой контакт по каждому URL. Коды — в шапке модуля.

    Перед первым кликом считается план (`plan`): вакансии, где контакт есть
    даром или где раскрывать уже нечего, из раскрытия выпадают — лимит на них
    не тратится. Это единственный необратимый расход во всём сборщике, и
    решение «тратить или нет» принимается по фактам, а не по памяти агента.

    `dry_run=True` печатает этот план и выходит, не открывая браузер и не
    списывая ничего: посмотреть, во что обойдётся прогон, до того как он
    случится. Обход при этом всё равно выполняется — он бесплатный и его
    результат остаётся в базе.
    """
    skipped: dict[str, str] = {}
    if urls:
        steps = plan(urls, db=db, walk=walk)
        if dry_run:
            # Выход ДО импорта playwright и до любой сессии: сухой прогон обязан
            # работать там, где раскрытие не работает вовсе.
            print("\n".join(plan_lines(steps)))
            print("\nсухой прогон (--dry-run): ни одного клика, лимит не тронут")
            return 0
        for step in steps:
            if not step["spend"]:
                skipped[step["url"]] = step["why"]
        for url, why in skipped.items():
            print(f"{url}\n  лимит НЕ трачу: {why}")
        urls = [u for u in urls if u not in skipped]
        if not urls:
            print("\nраскрывать нечего: по всем вакансиям контакт есть даром "
                  "или раскрывать уже нечего")
            return 0

    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        print(PLAYWRIGHT_HOWTO, file=sys.stderr)
        return 3

    state_file = auth.state_path("hirehi")
    browser_state = None
    if from_browser:
        # Осознанный размен: заход этими куками ротирует refresh-токен и разлогинит
        # живую вкладку пользователя. Разрешено им прямо и только под этим флагом.
        from . import cookiesrc  # noqa: PLC0415 — ленивый импорт, как в деталке
        src = cookiesrc.resolve(None if from_browser == "auto" else from_browser,
                                ("hirehi.ru",), use_cache=False)
        browser_state = src.storage_for_playwright()
        n = len(browser_state.get("cookies") or ())
        if not n:
            print("куки hirehi.ru в браузере не нашлись — войди на площадку в браузере "
                  "или сделай `scout auth login hirehi`", file=sys.stderr)
            return 2
        print(f"источник сессии: браузер ({src.line()}); "
              f"живая вкладка hirehi будет разлогинена — так разрешено")
    elif not os.path.exists(state_file):
        print(NO_SESSION, file=sys.stderr)
        return 2

    revealed = failed = clicks = 0
    # URL, по которым контакт получен: остальное при обрыве уйдёт в долг.
    done: set[str] = set()
    stale = False
    stopped: str | None = None

    with sync_playwright() as pw:
        # headless, но UA без слова HeadlessChrome: hirehi отдаёт на него 403
        # в 48 байт (ложная стена) — тот же подставной UA, что у stdlib-слоя
        # и bundled-рендера (render._render_bundled).
        br = pw.chromium.launch(headless=True)
        try:
            ctx = br.new_context(
                storage_state=browser_state if browser_state else state_file,
                locale="ru-RU", user_agent=UA)
            for url in urls:
                jid = job_id_from_url(url)
                if not jid:
                    failed += 1
                    print(f"{url}\n  не похоже на вакансию hirehi — пропуск",
                          file=sys.stderr)
                    continue

                cached = _already_revealed(db, jid)
                if cached:
                    print(f"{url}\n  контакт уже раскрыт ранее: {cached} — "
                          f"повторно не списываю")
                    revealed += 1
                    done.add(url)
                    continue

                if clicks >= limit:
                    stopped = (f"упёрся в --limit {limit}: раскрытий за прогон "
                               f"больше не делаю. Лимит площадки восстанавливается — "
                               f"добери следующим прогоном")
                    break

                page = ctx.new_page()
                got: dict = {}
                popups: list = []
                page.on("response", lambda r, g=got: _capture_consume(r, g))
                # Именно lambda, а не popups.append: Playwright вешает служебный
                # атрибут на обработчик, а у встроенного метода списка нет __dict__ —
                # `page.on("popup", popups.append)` падал AttributeError и ронял
                # раскрытие целиком (поймано на живом прогоне 04.08.2026).
                page.on("popup", lambda p, acc=popups: acc.append(p))
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:  # noqa: BLE001 — одна вакансия не рвёт прогон
                    failed += 1
                    print(f"{url}\n  страница не открылась: {type(e).__name__}: {e}",
                          file=sys.stderr)
                    page.close()
                    continue
                if resp and resp.status >= 400:
                    failed += 1
                    print(f"{url}\n  HTTP {resp.status} — страница не отдалась",
                          file=sys.stderr)
                    page.close()
                    continue
                page.wait_for_timeout(1500)

                try:
                    vd = page.evaluate("() => window.VACANCY_DATA || null")
                except Exception:  # noqa: BLE001
                    vd = None
                st, why = page_state(vd)
                if st == "anonymous":
                    print(f"сессия протухла: scout auth login hirehi ({why}) — "
                          f"НЕ кликаю", file=sys.stderr)
                    stale = True
                    page.close()
                    break
                if st == "no_limits":
                    stopped = why
                    page.close()
                    break
                if st == "unknown":
                    failed += 1
                    print(f"{url}\n  {why} — не кликаю", file=sys.stderr)
                    page.close()
                    continue

                btn = page.query_selector('a[data-apply-link="true"]')
                if btn is None:
                    failed += 1
                    print(f"{url}\n  кнопки data-apply-link на странице нет — "
                          f"не кликаю", file=sys.stderr)
                    page.close()
                    continue

                clicks += 1
                btn.click()  # РОВНО ОДИН клик — раскрытие контакта, оно и разрешено
                for _ in range(80):  # до ~20 с на consume (клиент может сперва рефрешить)
                    _close_popups(popups)
                    if got:
                        break
                    page.wait_for_timeout(250)
                _close_popups(popups)
                page.close()

                if "payload" not in got:
                    failed += 1
                    print(f"{url}\n  ответ {CONSUME_PATH} не пойман "
                          f"({got.get('error') or 'кнопка не дошла до сети'}) — "
                          f"контакт не подтверждён", file=sys.stderr)
                    continue
                c = parse_consume(got["payload"])
                if c.rate_limited or not c.allowed:
                    failed += 1
                    reason = c.message or ("rate_limited" if c.rate_limited
                                           else "allowed=false")
                    print(f"{url}\n  площадка отказала: {reason} — стоп")
                    stopped = "consume отказал — дальше жать бессмысленно"
                    break

                revealed += 1
                done.add(url)
                line = f"{url}\n  контакт ({c.kind or '?'}): {c.open_url}"
                if c.remaining is not None:
                    line += f"\n  остаток лимита раскрытий: {c.remaining}"
                print(line)
                _save_contact(db, jid, url, c)

            # Ротация refresh-токена обязана осесть у нас: не сохранить обновлённый
            # storage_state — значит сжечь собственную сессию к следующему прогону.
            try:
                auth.save_filtered(ctx.storage_state(), state_file,
                                   domains=("hirehi.ru",))
                print(f"сессия scout обновлена: {state_file} "
                      f"(ротация refresh осела у нас)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 — сохранение не отменяет раскрытое
                print(f"⚠️  не смог перезаписать {state_file}: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        finally:
            br.close()

    untouched = len(urls) - revealed - failed
    tail = f"раскрыто {revealed} из {len(urls)}"
    if skipped:
        tail += f", лимит сэкономлен на {len(skipped)} (контакт нашёлся даром)"
    if failed:
        tail += f", не раскрылось {failed}"
    if untouched:
        tail += f", не тронуто {untouched}"
    print(f"\n{tail}")
    if stopped:
        print(stopped, file=sys.stderr)
    # 🔴 Нераскрытое записывается ДОЛГОМ, а не теряется. Лимит у hirehi
    # восстанавливается, поэтому «не смогли сегодня» значит «вернуться завтра».
    # Раньше строка «лимит исчерпан» уходила в консоль, и вакансия жила дальше
    # только в памяти агента — до конца сессии (требование владельца 09.08.2026).
    if stopped and (untouched or failed):
        left = [u for u in urls if u not in done]
        with store.connect(db) as conn:
            noted = sum(1 for u in left if note_debt(conn, u, why=stopped))
        if noted:
            print(f"записано долгом: {noted} — вернуться, когда лимит "
                  f"восстановится (`scout pending-reveals`)", file=sys.stderr)
    if stale:
        return 2
    return 0 if revealed == len(urls) else 1
