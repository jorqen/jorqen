"""crawl — обход ВСЕХ ссылок вакансии с построением полной картины.

Что было. `applyopt.gather` брал из телеграм-поста до четырёх внешних ссылок и
выбирал «лучшую» ПО ДОМЕНУ: employer выше ats, ats выше витрины. Домен — это
догадка о том, куда ссылка ведёт, а не факт. Короткая ссылка не говорит вообще
ничего; витрина-редирект (`jobviewtrack.com`, `jooble.org/away/`) своей страницы
не имеет и увозит куда угодно; а живой на вид пост держит ссылку на вакансию,
снятую месяц назад (Авито, 09.08.2026: `career.avito.com/...` → 404 при живом
посте наверху таблицы).

Что здесь. Каждая ссылка вакансии обходится по-настоящему: страница читается,
редиректы и `meta refresh` проходятся, со страницы берутся ССЫЛКИ ДАЛЬШЕ —
кнопка «Откликнуться» витрины, careers-раздел на сайте компании, доска ATS. На
выходе граф, а не список догадок, и по нему считается то, ради чего всё:

  1. **лучший контакт** для отклика и ПОЧЕМУ именно он (`best_contact`);
  2. **живость** — открыта ли вакансия ещё (`liveness`);
  3. **работодатель**, если площадка его не назвала (`employer_guess`);
  4. **что осталось непройденным и почему** (`Result.dropped`).

Четыре предохранителя, без которых обход чужих сайтов кончается плохо:

* **глубина** (`max_depth`, по умолчанию 2: страница вакансии → careers-раздел →
  страница роли). Глубже начинается сайт компании целиком, а это не наша работа;
* **дедуп** — ключ страницы без схемы, `www`, якоря и хвоста трекинга: один и
  тот же адрес в четырёх написаниях читается один раз;
* **защита от зацикливания** — ключ помечается ДО запроса, а после редиректа
  проверяется ещё раз. `a → b → a` останавливается на втором `a`;
* **бюджеты** — страниц всего, страниц на хост и общий дедлайн по времени.

Ни один предохранитель не молчит: всё, куда не пошли, лежит в `dropped` с
причиной и печатается. Обрезанный обход, выглядящий полным, хуже отсутствующего.

Вежливость к хосту обязательна и берётся общая (`net.HostPacer`): обход по
определению долбит один домен подряд, а rabota.ru закрыла нам TLS после ~25
запросов за 20 минут — это была наша вина.

Чужие вердикты не пересчитываются, а зовутся: живость — `card.liveness_from_page`
(она ищет маркеры ТОЛЬКО в видимом тексте, потому что в JS-коде hh лежит строка
Sentry «Method not found», а в словаре локализации — «Вакансия в архиве»; по ним
однажды объявили мёртвыми 12 живых вакансий), стена — `webcommon.wall_marker`,
кто публикует — `applyopt.classify`, контакты в тексте — `contacts.gather`.

Отправки нет: только GET, как и в `resolve`. Ни одной формы, ни одного POST, ни
одной капчи — стена честно записывается как «не смогли», а не обходится.
"""

from __future__ import annotations

import html as H
import re
import time
import urllib.parse
from collections import deque
from dataclasses import asdict, dataclass, field

from . import applyopt
from .net import (PAGE_GONE, PAGE_LAYOUT, PAGE_OK, PAGE_STATE_RU, HostPacer,
                  error_state, fetch, wall_state)
from .resolve import META_REFRESH, NOISE_HOSTS, find_targets
from .tgpost import NOT_CONTACT, fetch_apply_links

# Пределы по умолчанию. Подобраны под задачу «ссылки одной вакансии», а не под
# обход сайта: глубины 2 хватает на цепочку витрина → сайт компании → ATS, и
# именно она встречается живьём.
MAX_DEPTH = 2
MAX_PAGES = 25
PER_HOST = 6
PER_PAGE = 8          # сколько ссылок брать С ОДНОЙ страницы
MAX_HOPS = 4          # редиректов и meta-refresh внутри одного узла
TIMEOUT = 20
GAP = 0.7             # зазор между запросами К ОДНОМУ хосту, секунды
DEADLINE = 120.0      # потолок времени на весь обход, секунды

# Сокращатели. Их адрес не говорит ни о чём — и ровно поэтому их надо ПРОЙТИ:
# `tgpost` выбрасывает их из контактов («куда ведёт, из ссылки не видно»), а
# обход как раз умеет посмотреть. Контактом считается то, куда приехали, а не
# сам сокращатель: завтра он может указывать в другое место.
SHORTENERS = ("clck.ru", "bit.ly", "vk.cc", "cutt.ly", "goo.gl", "tinyurl.com",
              "t.co", "is.gd", "rb.gy", "surl.li", "u.to", "lnkd.in",
              "l.tbank.ru", "tinyurl.ru", "clc.to")

# Витрины-редиректы: своей страницы вакансии у них нет вовсе, они увозят на
# сайт работодателя или на другую площадку. Проверять надо КОНЕЧНЫЙ адрес —
# иначе вердикт всегда «не похоже на страницу вакансии» (тот же вывод уже
# сделан в `cli.cmd_check_links`, здесь он записан данными).
REDIRECTORS = ("jobviewtrack.com", "jooble.org", "away.vk.com", "trk.mail.ru")

# Соцсети и мессенджеры — список ОДИН на проект, живёт в `tgpost`. Отсюда
# вычтены сокращатели: там они не контакт, здесь они дорога.
SOCIAL = tuple(d for d in NOT_CONTACT if d not in SHORTENERS)

# Файл — не страница: ссылок из него не достать, а текстом он приезжает мусором.
BINARY = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|tar|gz|png|jpe?g|gif|svg|webp|ico|"
    r"mp4|mp3|avi|mov|css|js|json|xml|rss|woff2?|ttf|eot)$", re.I)

# Юридический подвал: на каждом сайте, к вакансии отношения не имеет.
LEGAL = re.compile(r"(privacy|policy|politika|confidential|terms|soglasie|"
                   r"personal-data|cookie|oferta|agreement|licen[sz]|sitemap)", re.I)

# Хвост трекинга и позиционные параметры выдачи. В ключе дедупа их быть не
# должно: `?position=3`, `?refId=…`, `?ckey=…` — это НЕ разные вакансии, а одна
# и та же карточка, открытая из разных мест списка.
TRACKING = re.compile(
    r"^(utm_\w+|yclid|ysclid|gclid|fbclid|_openstat|erid|mc_cid|mc_eid|igshid|"
    r"from|source|ref|refid|referer|referrer|rb_clickid|_ga|yhid|position|pos|"
    r"ckey|trackingid|track|origin|campaign)$", re.I)

# Поддомены и пути, за которыми у компании лежат вакансии.
CAREERS_HOST = ("career.", "careers.", "job.", "jobs.", "rabota.", "hr.",
                "vacancy.", "vacancies.", "work.", "team.")
CAREERS_PATH = re.compile(r"/(career|careers|jobs?|vacanc|vakans|rabota|hr)\b", re.I)

# Почта отдела найма против общей приёмной. `sales@` и `info@` — это не контакт
# по вакансии, и ставить их выше формы ATS значит советовать писать не туда.
HR_MAILBOX = re.compile(
    r"^(hr|job|jobs|career|careers|recruit|recruiting|recruiter|rekrut|vacan|"
    r"vakan|cv|resume|talent|people|team|work|hiring|join|ludi)", re.I)

# Ссылки берём ТОЛЬКО из <a>. Общий поиск по `href=` тащил ещё и <link> —
# живьём это дало восемь строк «не пошли: файл, а не страница» на одной
# странице Greenhouse (css, js, манифест), то есть отчёт о непройденном
# заполнялся мусором и переставал читаться.
_HREF = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\'](https?://[^"\'>\s]+)["\']', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def clean_url(url: str) -> str:
    """Адрес, как он есть на самом деле: без экранирования и без якоря.

    🔴 Телеграм отдаёт `href` ДВАЖДЫ экранированным: в веб-версии поста лежит
    `...?source=web3.career&amp;amp;gh_src=...`. Одна распаковка оставляет
    `&amp;`, и по такому адресу площадка получает параметр с именем `amp;gh_src`
    — живой случай 09.08.2026 на посте job_web3/3757. Поэтому распаковываем до
    неподвижной точки.

    Якорь выбрасывается: документ он не адресует. Исключение — hash-маршруты
    (`#!` и `#/`): у SPA это и есть адрес страницы, и срезать его нельзя.
    """
    u = (url or "").strip()
    for _ in range(3):
        nxt = H.unescape(u)
        if nxt == u:
            break
        u = nxt
    head, sep, frag = u.partition("#")
    if sep and not frag.startswith(("!", "/")):
        return head
    return u


# ──────────────────────────────────────────────────────────────────────────────
# Ключ дедупа
# ──────────────────────────────────────────────────────────────────────────────

def normalize(url: str) -> str:
    """Ключ страницы: одна страница — один ключ, как бы её ни написали.

    Схема выбрасывается целиком (`http` и `https` одной страницы — одна
    страница), `www` и порт по умолчанию тоже, якорь не адресует документ,
    хвост трекинга к содержимому отношения не имеет, а остаток query
    сортируется: `?a=1&b=2` и `?b=2&a=1` — один и тот же запрос.

    Ключ НЕ используется как адрес для запроса: ходим по исходному URL, а
    сравниваем по ключу. Иначе обход чинил бы чужие адреса на свой вкус.
    """
    raw = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
        host = (parts.hostname or "").lower().removeprefix("www.")
        port = parts.port
    except ValueError:
        return raw.lower()
    if not host:
        return raw.lower()
    tail = f":{port}" if port and port not in (80, 443) else ""
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    keep = [(k, v) for k, v in
            urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if not TRACKING.match(k)]
    query = urllib.parse.urlencode(sorted(keep))
    return f"{host}{tail}{path}" + (f"?{query}" if query else "")


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_shortener(url: str) -> bool:
    return _matches(host_of(url), SHORTENERS)


def is_redirector(url: str) -> bool:
    """Витрина без собственной страницы: ценен только конечный адрес."""
    return _matches(host_of(url), REDIRECTORS) or "/away/" in (url or "")


def skip_reason(url: str) -> str | None:
    """Почему по этому адресу ходить незачем. None — ходить можно."""
    if not url.startswith(("http://", "https://")):
        return "не веб-адрес"
    host = host_of(url)
    if not host:
        return "адрес без хоста"
    path = urllib.parse.urlsplit(url).path or ""
    if BINARY.search(path):
        return "файл, а не страница"
    if _matches(host, SOCIAL):
        return "соцсеть или мессенджер — работодателем не бывает"
    if NOISE_HOSTS.search(host):
        return "счётчик, CDN или магазин приложений"
    if LEGAL.search(path):
        return "юридический подвал, к вакансии отношения не имеет"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Узел и результат
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    """Одна пройденная страница со всем, что о ней удалось узнать."""

    url: str
    depth: int
    parent: str | None = None
    why: str = ""                 # почему сюда пошли
    final_url: str = ""           # после редиректов и meta refresh
    hops: list[str] = field(default_factory=list)
    publisher: str = applyopt.UNKNOWN
    is_direct: bool = False
    state: str = ""               # net.PAGE_*: как ответила страница
    evidence: str = ""            # чем это подтверждено
    liveness: str = ""            # ЖИВА | МЕРТВА | НЕИЗВЕСТНО
    liveness_why: str = ""
    title: str = ""
    shell: bool = False           # пустой каркас SPA: stdlib его не раскрывает
    contacts: dict = field(default_factory=dict)   # почта/телеграм со страницы
    kids: list[str] = field(default_factory=list)
    note: str = ""                # особое: цикл, оборванная цепочка, каркас

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    origin: str = ""              # с чего начали
    note: str = ""                # откуда взялись стартовые ссылки
    seeds: list[str] = field(default_factory=list)
    # Имя работодателя, если площадка его назвала. Живёт в результате, а не в
    # параметрах производных функций: `employer_guess` обязан знать его всегда,
    # а параметр, который можно забыть передать, — это тот же дефект через
    # прогон (проверка «домен связан с именем» без имени просто не работает).
    company: str = ""
    nodes: list[Node] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)   # {"url", "why"}
    deduped: int = 0              # сколько раз ссылка оказалась уже известной
    limits: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"origin": self.origin, "note": self.note, "seeds": self.seeds,
                "nodes": [n.to_dict() for n in self.nodes],
                "dropped": self.dropped, "deduped": self.deduped,
                "limits": self.limits, "seconds": round(self.seconds, 1),
                "summary": summary(self)}


# ──────────────────────────────────────────────────────────────────────────────
# Обход
# ──────────────────────────────────────────────────────────────────────────────

def crawl(seeds: list[str], *, max_depth: int = MAX_DEPTH,
          max_pages: int = MAX_PAGES, per_host: int = PER_HOST,
          timeout: int = TIMEOUT, gap: float = GAP, deadline: float = DEADLINE,
          render: bool = False, fetcher=None, clock=time.monotonic,
          origin: str = "", note: str = "") -> Result:
    """Обходит граф ссылок вширь и возвращает полную картину.

    Вширь, а не вглубь, намеренно: ссылки вакансии равноправны, и первая не
    должна съедать бюджет страниц у остальных.

    `fetcher` — шов для тестов: `(url, *, timeout) -> (текст, финальный url)`.
    Сети в тестах быть не должно, а обход без сети иначе не проверить.
    `render=True` — доставать SPA-каркасы браузером (дорого, лениво, опционально).
    """
    get = fetcher or fetch
    pacer = HostPacer(gap)
    started = clock()
    res = Result(origin=origin, note=note, seeds=list(seeds),
                 limits={"max_depth": max_depth, "max_pages": max_pages,
                         "per_host": per_host, "deadline": deadline})
    queue: deque[tuple[str, int, str | None, str]] = deque()
    seen: set[str] = set()
    per_host_seen: dict[str, int] = {}

    def enqueue(raw_url: str, depth: int, parent: str | None, why: str) -> None:
        url = clean_url(raw_url)
        why_not = skip_reason(url)
        if why_not:
            res.dropped.append({"url": url, "why": why_not})
            return
        key = normalize(url)
        if key in seen:
            # Это НЕ потеря: адрес уже в очереди или уже пройден. Считаем,
            # чтобы в отчёте было видно, сколько работы сняли дедупом.
            res.deduped += 1
            return
        if depth > max_depth:
            res.dropped.append(
                {"url": url, "why": f"глубже предела обхода ({max_depth})"})
            return
        seen.add(key)
        queue.append((url, depth, parent, why))

    for seed in seeds:
        enqueue(seed, 0, origin or None, "ссылка вакансии")

    while queue:
        url, depth, parent, why = queue.popleft()
        # Бюджеты проверяются ЗДЕСЬ, а не при постановке в очередь: очередь
        # должна дочерпаться до конца, иначе остаток ссылок исчезнет молча,
        # вместо того чтобы честно назваться пропущенным.
        spent = clock() - started
        if spent >= deadline:
            res.dropped.append(
                {"url": url, "why": f"истёк дедлайн обхода ({deadline:g} с)"})
            continue
        if len(res.nodes) >= max_pages:
            res.dropped.append(
                {"url": url, "why": f"бюджет страниц исчерпан ({max_pages})"})
            continue
        host = host_of(url)
        if per_host_seen.get(host, 0) >= per_host:
            res.dropped.append(
                {"url": url, "why": f"на хосте {host} уже прочитано {per_host} страниц"})
            continue
        per_host_seen[host] = per_host_seen.get(host, 0) + 1

        node = Node(url=url, depth=depth, parent=parent, why=why)
        res.nodes.append(node)
        text = _visit(node, get=get, pacer=pacer, timeout=timeout, render=render)

        final_key = normalize(node.final_url or node.url)
        if final_key != normalize(node.url) and final_key in seen:
            # Цикл: редирект привёл туда, где мы уже были. Ссылки дальше не
            # берём — они уже взяты у первой встречи, и второй проход по ним
            # был бы бесконечным по построению.
            node.note = "редирект привёл на уже пройденный адрес — дальше не идём"
            continue
        seen.add(final_key)
        if text is None:
            continue
        page_url = node.final_url or node.url
        kids = _kids(text, page_url, node.publisher)
        if not kids and is_redirector(page_url):
            # Витрина-редирект, которая не раскрылась: адрес подставляет скрипт.
            # Молчать об этом нельзя — снаружи это выглядит как «дошли до конца».
            node.note = ("витрина-редирект не раскрылась: конечный адрес "
                         "подставляет скрипт — `scout render <url>`")
        for kid, kid_why in kids:
            node.kids.append(kid)
            enqueue(kid, depth + 1, page_url, kid_why)

    res.seconds = clock() - started
    return res


def _visit(node: Node, *, get, pacer: HostPacer, timeout: int,
           render: bool = False) -> str | None:
    """Читает страницу узла, проходя редиректы. Возвращает текст или None.

    None — страница не прочитана (стена, 404, сеть). Узел при этом заполнен:
    «не открылась» — тоже факт о маршруте, и в картине он нужен наравне с живым.
    Исключение наружу не уходит НИКОГДА: одна недоступная страница не имеет
    права уронить обход остальных.
    """
    current, text = node.url, None
    hops_seen = {normalize(node.url)}
    for hop in range(MAX_HOPS):
        pacer.wait(current)
        try:
            text, final = get(current, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — чужая страница не роняет обход
            state, why = error_state(exc)
            node.final_url = node.final_url or current
            node.state, node.evidence = state, why
            # 404 — это смерть вакансии, а 403/429/таймаут — молчание площадки.
            # Путать их нельзя: по первому вакансию закрывают, по второму
            # возвращаются позже.
            node.liveness = "МЕРТВА" if state == PAGE_GONE else "НЕИЗВЕСТНО"
            node.liveness_why = why
            node.publisher, node.is_direct = applyopt.classify(node.final_url)
            return None
        node.final_url = final
        if normalize(final) != normalize(current):
            node.hops.append(final)
        meta = META_REFRESH.search(text or "")
        target = urllib.parse.urljoin(final, H.unescape(meta.group(1).strip())) if meta else ""
        if target and normalize(target) in hops_seen:
            # Кольцо из meta-refresh: страница A шлёт на B, B обратно на A.
            # Без этой ветки узел крутил бы MAX_HOPS запросов вхолостую.
            node.note = "meta-refresh закольцован — дальше по нему не идём"
            break
        if target:
            hops_seen.add(normalize(target))
            node.hops.append(target)
            current = target
            if hop == MAX_HOPS - 1:
                node.note = f"цепочка редиректов длиннее предела ({MAX_HOPS})"
            continue
        break

    _describe(node, text or "")
    if node.shell and render:
        text = _render_shell(node) or text
    return text


def _describe(node: Node, text: str) -> None:
    """Факты о прочитанной странице. Все вердикты — чужие, второй копии нет.

    🔴 Живость считает ТОЛЬКО `card.liveness_from_page`, потому что она ищет
    маркеры в видимом тексте. Своя проверка по сырой разметке уже стоила
    двенадцати живых вакансий, объявленных мёртвыми по строке Sentry «Method
    not found» из JS-кода hh, и вакансии GS Labs, помеченной архивной по
    значению `"applicant.negotiations.vacancyArchived"` из словаря локализации.

    `state` выводится ИЗ этого вердикта, а не считается отдельно: два
    независимых суждения об одной странице расходятся всегда.
    """
    from .card import liveness_from_page, visible_text  # noqa: PLC0415
    from .channel import looks_like_shell  # noqa: PLC0415
    from .webcommon import wall_marker  # noqa: PLC0415 — детектор стены один на проект

    url = node.final_url or node.url
    node.publisher, node.is_direct = applyopt.classify(url)
    m = _TITLE.search(text[:8000])
    node.title = re.sub(r"\s+", " ", H.unescape(m.group(1))).strip()[:120] if m else ""
    node.liveness, node.liveness_why = liveness_from_page(text, 200, final_url=url)

    wall = wall_marker(text, 200)
    node.shell = looks_like_shell(text)
    if wall:
        # Стена приезжает с кодом 200 (careerjet: «Требуется подтверждение…
        # необычный трафик»; hh при подозрении на VPN отдаёт 228 КБ разметки
        # с /vpncheeck). Это «не смогли посмотреть», а не «вакансии нет».
        node.state, node.evidence = wall_state(wall), f"стена: {wall}"
    elif node.liveness == "МЕРТВА":
        node.state, node.evidence = PAGE_GONE, node.liveness_why
    elif node.shell:
        node.state = PAGE_LAYOUT
        node.evidence = "пустой каркас SPA: разметку дорисовывает скрипт"
        # Дописываем, а не перетираем: до сюда в `note` могла лечь причина
        # оборванной цепочки редиректов, и она тоже нужна в отчёте.
        node.note = "; ".join(filter(None, [
            node.note, "каркас SPA — stdlib отдаёт пустую страницу; "
                       "`scout render <url>` или `crawl --render`"]))
    else:
        node.state, node.evidence = PAGE_OK, node.liveness_why

    # Контакты собираем со ВСЕХ страниц, включая витрины: «в агрегаторах
    # откликнуться нельзя, можно только получить контакт» (владелец, 09.08.2026),
    # и ник рекрутёра в теле объявления — ровно то, за чем туда идут. Опасность
    # витрины не в самом сборе, а в выдаче её собственной поддержки за контакт
    # работодателя, и отвечает за это `best_contact`: почта берётся только со
    # страниц работодателя, а ник с витрины — с явной оговоркой, чей он.
    from . import contacts as C  # noqa: PLC0415
    found = C.gather({"description": visible_text(text)})
    node.contacts = {k: v[:3] for k, v in found.items() if v}


def _render_shell(node: Node) -> str | None:
    """Второе чтение каркаса SPA настоящим браузером. Дорого — потому по флагу.

    Опционально и лениво: playwright в ядро не входит, и его отсутствие обязано
    оставаться просто отметкой в узле, а не падением обхода.
    """
    try:
        from .render import render_page  # noqa: PLC0415
        text, final = render_page(node.final_url or node.url)
        node.final_url = final or node.final_url
    except Exception as e:  # noqa: BLE001 — рендер необязателен по построению
        node.note = f"{node.note}; рендер не вышел: {type(e).__name__}"
        return None
    if not text:
        return None
    node.note = "каркас SPA раскрыт браузером (--render)"
    node.shell = False
    _describe(node, text)
    return text


def _kids(text: str, page_url: str, publisher: str) -> list[tuple[str, str]]:
    """Ссылки со страницы, по которым имеет смысл идти дальше, и почему.

    Не «все ссылки»: у любой страницы их сотни, и обход утонул бы в меню и
    подвале. Чужой домен посещается, только если он кандидат в контакт — ATS,
    careers, сайт компании, сокращатель, — а со своего домена ходим только по
    сайту работодателя, иначе витрина утащит нас в свои соседние вакансии.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    host = host_of(page_url)

    def add(raw_url: str, why: str) -> None:
        if len(out) >= PER_PAGE:
            return
        url = clean_url(raw_url)
        key = normalize(url)
        if not key or key in seen:
            return
        seen.add(key)
        # Отсев (соцсети, файлы, юридический подвал) делает `enqueue`, а не мы:
        # он умеет записать причину в `dropped`, а молча выброшенная ссылка —
        # это дыра в картине, которую снаружи не отличить от «ссылки не было».
        out.append((url, why))

    # 1. То, что резолвер уже умеет: кнопка и ссылка «Откликнуться», адрес из
    #    data-атрибута, встроенный JSON страницы. Второй копии этого разбора
    #    в проекте быть не должно.
    for t in find_targets(text, page_url):
        if t.url and t.safe_to_open and t.url.startswith("http"):
            label = (t.label or t.note or "").strip()
            add(t.url, f"кнопка отклика: {label[:48]}" if label else "ссылка отклика")

    # 2. Всё остальное — по адресу ссылки.
    for m in _HREF.finditer(text):
        url = urllib.parse.urljoin(page_url, H.unescape(m.group(1).strip()))
        h = host_of(url)
        if not h:
            continue
        if h == host:
            # Свой домен. С витрины — никогда: её «похожие вакансии» это
            # соседние работодатели, а не путь к нашему.
            if publisher == applyopt.EMPLOYER and (
                    applyopt.looks_like_job(url) or CAREERS_PATH.search(url)):
                add(url, "вакансии на сайте работодателя")
            continue
        kind, _direct = applyopt.classify(url)
        if kind == applyopt.ATS:
            add(url, "доска ATS работодателя")
        elif is_shortener(url) or is_redirector(url):
            add(url, "короткая ссылка или витрина-редирект: куда ведёт, "
                     "видно только переходом")
        elif kind == applyopt.EMPLOYER and (applyopt.looks_like_job(url)
                                            or h.startswith(CAREERS_HOST)
                                            or CAREERS_PATH.search(url)):
            add(url, "вакансия или careers-раздел работодателя")
    return out


def crawl_post(post_url: str, **kw) -> Result:
    """Обход по ВСЕМ внешним ссылкам телеграм-поста.

    🔴 Сам пост живость вакансии НЕ доказывает: он остаётся на месте, когда
    набор давно закрыт (Авито, 09.08.2026). Поэтому стартуем не с поста, а с
    того, что внутри него. Сокращатели оставлены намеренно: в контакты они не
    годятся, а как дорога — вполне.
    """
    links, why = fetch_apply_links(post_url, keep_shorteners=True)
    return crawl(links, origin=post_url, note=why, **kw)


def crawl_url(url: str, **kw) -> Result:
    """Обход от любой страницы. Телеграм-пост распознаётся сам."""
    if re.match(r"https?://t\.me/(?:s/)?[A-Za-z0-9_]+/\d+", url or ""):
        return crawl_post(url, **kw)
    return crawl([url], origin=url, note="стартовая страница", **kw)


# ──────────────────────────────────────────────────────────────────────────────
# Итог обхода: контакт, живость, работодатель, непройденное
# ──────────────────────────────────────────────────────────────────────────────

def routes(res: Result) -> list[dict]:
    """Пройденные страницы в формате `applyopt` — плюс факты, которых там нет.

    Маршрутом называется ФИНАЛЬНЫЙ адрес: если короткая ссылка привела на
    careers-страницу, вести надо туда, а не в сокращатель.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for n in res.nodes:
        url = n.final_url or n.url
        key = normalize(url)
        if not key or key in seen or is_redirector(url):
            continue
        seen.add(key)
        publisher, direct = applyopt.classify(url)
        note = f"обход: {n.why or 'ссылка вакансии'}; {n.liveness.lower()} — {n.liveness_why}"
        out.append({"url": url, "publisher": publisher, "is_direct": direct,
                    "note": note[:240], "rank": len(out),
                    "liveness": n.liveness, "state": n.state})
    return out


def _usable(n: Node) -> bool:
    """Страница, на которую можно вести человека: она хотя бы не мертва."""
    return n.liveness != "МЕРТВА"


def _page_kind(publisher: str, url: str) -> str:
    if publisher == applyopt.ATS:
        return "ATS работодателя"
    if publisher == applyopt.EMPLOYER:
        return ("вакансия на сайте работодателя" if applyopt.looks_like_job(url)
                else "careers-раздел работодателя")
    return "витрина"


def _org(host: str) -> str:
    """«Организационная» часть домена: careers.kaspersky.ru → kaspersky.ru.

    Грубо, по двум последним меткам, и этого достаточно: задача — отличить свой
    домен от чужого (rbc.ru против kaspersky.ru), а не разобрать зону идеально.
    Для доменов вида co.uk берём три метки, иначе «bbc.co.uk» и «itv.co.uk»
    выглядели бы одной организацией.
    """
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) < 2:
        return host or ""
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _same_org(host_or_domain: str, own: set[str]) -> bool:
    return _org((host_or_domain or "").lower().removeprefix("www.")) in own


def employer_domains(res: Result) -> set[str]:
    """Домены, которые и есть работодатель этой вакансии.

    Берутся из СТАРТОВЫХ страниц обхода (глубина 0–1), классифицированных как
    сайт компании или её ATS: именно туда вела вакансия. Всё, что нашлось
    глубже, — уже соседи по дороге (баннеры, рейтинги, партнёры), и их контакты
    работодателем не являются.
    """
    # Берём САМЫЙ МЕЛКИЙ уровень, на котором работодатель вообще встретился:
    # для ссылки на сайт компании это глубина 0, для телеграм-поста — 1 (сам
    # пост работодателем не является). Всё, что глубже, — уже соседи по дороге.
    # Без этого баннер рейтинга РБК со страницы Kaspersky попадал в «свои»
    # домены и приносил чужую почту как контакт (09.08.2026).
    own_by_depth: dict[int, set[str]] = {}
    for n in res.nodes:
        if n.publisher not in (applyopt.EMPLOYER, applyopt.ATS):
            continue
        host = host_of(n.final_url or n.url)
        if host:
            own_by_depth.setdefault(n.depth, set()).add(_org(host))
    return own_by_depth[min(own_by_depth)] if own_by_depth else set()


def best_contact(res: Result) -> dict | None:
    """Куда писать — и ПОЧЕМУ именно туда. None — обход контакта не нашёл.

    Страницы между собой НЕ ранжируются здесь: их порядок — это `applyopt.best`,
    и второго правила «какой маршрут лучше» в проекте быть не должно. Своего у
    этой функции ровно две вещи, которых у маршрутов нет:

      * **почта найма** (`hr@`, `jobs@`) со страницы работодателя бьёт любую
        страницу: письмо человеку доходит быстрее формы. Общая приёмная
        (`info@`, `sales@`) — НЕ бьёт: письмо туда уходит в никуда, поэтому
        почта делится на почту найма и всякую другую;
      * **@ник со страницы компании** — лучше страницы, с которой откликнуться
        всё равно нельзя (корень сайта, careers-витрина без конкретной роли).
    """
    alive = [n for n in res.nodes if _usable(n)]

    def mails(node: Node, hr_only: bool) -> list[str]:
        vals = [c["value"] for c in (node.contacts.get("email") or [])]
        return [v for v in vals if bool(HR_MAILBOX.match(v.split("@")[0])) == hr_only]

    own = employer_domains(res)
    for n in alive:                       # 1. почта найма на странице компании
        if n.publisher not in (applyopt.EMPLOYER, applyopt.ATS):
            continue
        page = n.final_url or n.url
        for v in mails(n, hr_only=True):
            # 🔴 Почта обязана принадлежать НАНИМАТЕЛЮ, а не просто попасться по
            # дороге. Живой случай 09.08.2026: на странице вакансии Kaspersky
            # висел баннер рейтинга работодателей РБК, обход дошёл до
            # hr-rating.rbc.ru и выдал тамошний hr-forum@rbc.ru «лучшим
            # контактом» — письмо ушло бы чужой компании, а в карточке стояло
            # бы уверенное «почта отдела найма».
            if own and not (_same_org(host_of(page), own)
                            or _same_org(v.split("@")[-1], own)):
                res.dropped.append(
                    {"url": v, "why": f"почта с чужого домена ({v.split('@')[-1]}) "
                                      f"— работодатель вакансии: {', '.join(sorted(own))}"})
                continue
            return {"kind": "почта найма", "value": v, "url": page,
                    "why": f"почта отдела найма прямо на странице работодателя "
                           f"({host_of(page)})"}

    found = routes(res)
    url = applyopt.best([o for o in found if o.get("liveness") != "МЕРТВА"] or found)
    if not url:
        return None
    node = next((n for n in res.nodes if (n.final_url or n.url) == url), None)
    publisher, _direct = applyopt.classify(url)

    if not applyopt.looks_like_job(url):  # 2. ник лучше страницы без отклика
        for n in alive:
            if n.publisher in (applyopt.EMPLOYER, applyopt.ATS):
                for c in n.contacts.get("telegram") or []:
                    return {"kind": "telegram", "value": c["value"],
                            "url": n.final_url or n.url,
                            "why": "ник указан на странице работодателя, а с лучшей "
                                   "найденной страницы откликнуться нельзя"}

    kind = _page_kind(publisher, url)

    # 3. Ник ИЗ ТЕЛА вакансии на витрине — тоже контакт, и часто единственный.
    # «В агрегаторах вакансий откликнуться нельзя, можно только получить
    # контакт» (слова владельца 09.08.2026): раз так, ник в тексте объявления
    # ценнее ссылки на саму витрину, с которой мы и пришли. Живой счёт: у
    # Teleport с hirehi контакт нашёлся только так — телеграм рекрутёра в теле
    # вакансии на vseti.app. Пометка честная: чей это ник, обход не знает.
    if kind == "витрина":
        for n in alive:
            page = n.final_url or n.url
            site = re.sub(r"[^a-z0-9]+", "", _org(host_of(page)))
            for c in n.contacts.get("telegram") or []:
                # Канал самой площадки ником нанимателя не является: у витрины
                # «Сети» это @vseti, и подсунуть его как контакт — то же самое,
                # что подсунуть её же support@.
                nick = re.sub(r"[^a-z0-9]+", "", str(c["value"]).lower())
                if nick and site and (nick in site or site.startswith(nick[:5])):
                    continue
                return {"kind": "telegram", "value": c["value"], "url": page,
                        "why": f"ник из тела вакансии на витрине ({host_of(page)}) "
                               f"— прямого канала обход не нашёл; проверь, что "
                               f"это наниматель"}

    why = {"ATS работодателя": "отклик попадает в собственную воронку компании, "
                               "а не в общую базу витрины",
           "вакансия на сайте работодателя": "страница вакансии на домене компании",
           "careers-раздел работодателя": "конкретной страницы роли не нашлось, "
                                          "но это домен компании",
           "витрина": "прямого канала обход не нашёл — отклик через площадку"}[kind]
    if node:
        why += f", {node.liveness.lower()}"
    return {"kind": kind, "value": url, "url": url, "why": why}


def contact_from_routes(found: list[dict]) -> dict | None:
    """Лучший контакт по СОХРАНЁННЫМ маршрутам — когда обход не повторяем.

    Нужна отдельно от `best_contact`: та работает по картине обхода, а из базы
    приезжают только маршруты с живостью. Кэш обязан отвечать на тот же вопрос,
    иначе вторая команда за прогон пошла бы в сеть заново.
    """
    live = [o for o in found if o.get("liveness") != "МЕРТВА"]
    direct = [o for o in live if o.get("is_direct")]
    if direct:
        return {"kind": "прямой канал (факты из базы)", "value": direct[0]["url"],
                "url": direct[0]["url"],
                "why": direct[0].get("note") or "маршрут проверен прошлым обходом"}
    return None


def liveness_from_routes(found: list[dict]) -> str:
    """Живость по сохранённым маршрутам. Пусто или все мертвы — так и говорим."""
    marks = [o.get("liveness") for o in found if o.get("liveness")]
    if not marks:
        return "НЕИЗВЕСТНО"
    if "ЖИВА" in marks:
        return "ЖИВА"
    return "МЕРТВА" if all(m == "МЕРТВА" for m in marks) else "НЕИЗВЕСТНО"


# Служебные метки в адресах ATS: компанией не бывают ни одна из них. Список
# нужен потому, что слаг компании лежит у разных досок в РАЗНОМ месте:
# `jobs.lever.co/<фирма>/…` и `job-boards.greenhouse.io/<фирма>/jobs/…` — в пути,
# а `<фирма>.bamboohr.com/careers/24` и `<фирма>.huntflow.ru` — в поддомене.
# Живой случай 09.08.2026: по одному лишь пути работодателем PayDepot был
# назван «careers».
ATS_GENERIC = {"jobs", "job", "job-boards", "boards", "boards-api", "apply",
               "career", "careers", "hire", "e", "www", "vacancies", "vacancy",
               "o", "en", "ru", "search", "list"}


def _ats_company(url: str) -> str | None:
    """Слаг компании из адреса доски ATS: сперва поддомен, потом путь."""
    host = host_of(url)
    labels = host.split(".")
    if len(labels) > 2 and labels[0] not in ATS_GENERIC:
        return labels[0]
    for part in urllib.parse.urlsplit(url).path.split("/"):
        if part and part not in ATS_GENERIC and not part.isdigit():
            return part
    return None


def _name_touches_domain(company: str, host: str) -> bool:
    """Связан ли домен с названием компании — хотя бы буквами.

    Сравниваются только буквы и цифры в нижнем регистре: «Kaspersky Lab» ↔
    `careers.kaspersky.ru` связаны, «Teleport» ↔ `vseti.app` — нет. Проверка
    заведомо грубая и признаёт связь охотно; её задача — отсечь заведомо чужое,
    а не доказать принадлежность.
    """
    name = re.sub(r"[^a-z0-9]+", "", (company or "").lower())
    dom = re.sub(r"[^a-z0-9]+", "", _org((host or "").lower().removeprefix("www.")))
    if len(name) < 3 or len(dom) < 3:
        return False
    return name in dom or dom.startswith(name[:6]) or name.startswith(dom[:6])


def employer_guess(res: Result, company: str | None = None) -> dict | None:
    """Кто наниматель, если площадка его не назвала. Догадка С УЛИКОЙ.

    Только то, что видно в адресе: слаг компании на доске ATS или её
    собственный домен. Ничего не выдумывается — без улики лучше вернуть None,
    чем назвать вероятного работодателя.

    🔴 Когда имя компании ИЗВЕСТНО, домен обязан быть с ним связан. Признак
    «не витрина» уликой не является: у вакансии Teleport обход прошёл по
    `vseti.app`, `ya.ru` и статье на `vc.ru`, и каждый из них объявлялся
    «собственным доменом работодателя» — просто потому, что их нет в списке
    агрегаторов (09.08.2026). Догадка без улики уводит письмо в чужую компанию
    так же, как чужая почта с баннера.
    """
    # Имя берётся из результата, если не передано явно: так о нём не может
    # забыть ни один вызывающий.
    company = company if company is not None else (res.company or "")
    for n in res.nodes:
        url = n.final_url or n.url
        if n.publisher == applyopt.ATS:
            slug = _ats_company(url)
            if slug:
                return {"value": slug, "why": f"слаг компании на доске ATS: {url}",
                        "title": n.title}
    for n in res.nodes:
        url = n.final_url or n.url
        host = host_of(url)
        if n.publisher == applyopt.EMPLOYER and host:
            # Страница обязана ОТДАВАТЬ вакансию. Иначе работодателем
            # объявляется что угодно, до чего дошёл обход: у поста Teleport
            # так в догадку попал `ya.ru` — обход зашёл на главную Яндекса
            # по ссылке из объявления (09.08.2026).
            if n.liveness != "ЖИВА":
                continue
            if company and not _name_touches_domain(company, host):
                continue
            for pref in CAREERS_HOST:
                host = host.removeprefix(pref)
            return {"value": host, "why": f"собственный домен работодателя: {url}",
                    "title": n.title}
    return None


def liveness(res: Result) -> tuple[str, str]:
    """Жива ли вакансия по совокупности пройденного, и чем это подтверждено."""
    if not res.nodes:
        return "НЕИЗВЕСТНО", "обходить было нечего"
    live = [n for n in res.nodes if n.liveness == "ЖИВА"]
    dead = [n for n in res.nodes if n.liveness == "МЕРТВА"]
    if live:
        n = live[0]
        return "ЖИВА", f"{n.final_url or n.url}: {n.liveness_why}"
    if dead and len(dead) == len(res.nodes):
        return "МЕРТВА", (f"все {len(dead)} пройденных страниц мертвы "
                          f"({dead[0].liveness_why})")
    if dead:
        return "НЕИЗВЕСТНО", (f"мертвы {len(dead)} из {len(res.nodes)}, "
                              f"по остальным ответа нет — смотри глазами")
    # «Открылась, но это не страница вакансии» и «не открылась вовсе» — разные
    # ответы, и путать их нельзя: в первом случае смотреть надо глазами, во
    # втором — чинить доступ. Живой случай: у PayDepot открылись и главная, и
    # доска BambooHR, а строка гласила «ни одна страница не открылась».
    opened = [n for n in res.nodes if n.state == PAGE_OK]
    if opened:
        return "НЕИЗВЕСТНО", (f"страницы открылись ({len(opened)} из "
                              f"{len(res.nodes)}), но признаков вакансии в них "
                              f"нет — смотри глазами")
    return "НЕИЗВЕСТНО", "ни одна страница не открылась (стены или сеть)"


def summary(res: Result) -> dict:
    """Итог обхода одной структурой: контакт, живость, наниматель, непройденное."""
    live, why = liveness(res)
    return {"best_contact": best_contact(res), "liveness": live, "liveness_why": why,
            "employer": employer_guess(res), "pages": len(res.nodes),
            "unvisited": len(res.dropped), "deduped": res.deduped}


def render(res: Result) -> list[str]:
    """Карта обхода и итог — для человека."""
    out: list[str] = []
    if res.origin:
        out.append(f"Начало: {res.origin}" + (f" — {res.note}" if res.note else ""))
    lim = res.limits
    out.append(f"Ссылок на старте: {len(res.seeds)} · страниц пройдено: "
               f"{len(res.nodes)} · глубина ≤ {lim.get('max_depth', MAX_DEPTH)} · "
               f"дедуп снял повторов: {res.deduped} · {res.seconds:.1f} с")
    out.append("")

    s = summary(res)
    live_mark = {"ЖИВА": "✓", "МЕРТВА": "✗"}.get(s["liveness"], "?")
    out.append(f"ЖИВОСТЬ: {live_mark} {s['liveness']} — {s['liveness_why']}")
    if s["best_contact"]:
        b = s["best_contact"]
        out.append(f"ЛУЧШИЙ КОНТАКТ [{b['kind']}]: {b['value']}")
        out.append(f"  почему: {b['why']}")
    else:
        out.append("ЛУЧШИЙ КОНТАКТ: не нашёлся — контакт ищи в тексте вакансии "
                   "(@ник, почта), это делает `contacts`")
    if s["employer"]:
        out.append(f"РАБОТОДАТЕЛЬ (догадка): {s['employer']['value']} "
                   f"— {s['employer']['why']}")
    out.append("")

    out.append("КАРТА ОБХОДА:")
    mark = {"ЖИВА": "✓", "МЕРТВА": "✗", "НЕИЗВЕСТНО": "?"}
    for n in res.nodes:
        url = n.final_url or n.url
        pad = "  " * n.depth
        out.append(f"{pad}{mark.get(n.liveness, '?')} [{n.publisher}"
                   f"{'/прямой' if n.is_direct else ''}] {url}")
        inner = pad + "    "
        if n.title:
            out.append(f"{inner}{n.title}")
        out.append(f"{inner}← {n.why}")
        state_ru = PAGE_STATE_RU.get(n.state, n.state)
        out.append(f"{inner}{n.liveness}: {n.liveness_why}"
                   + (f" (страница: {state_ru})" if n.state != PAGE_OK else ""))
        for kind, items in (n.contacts or {}).items():
            out.append(f"{inner}{kind}: " + ", ".join(c["value"] for c in items))
        if n.hops:
            out.append(f"{inner}редиректы: {' → '.join(h[:70] for h in n.hops)}")
        if n.note:
            out.append(f"{inner}⚠️ {n.note}")
    if res.dropped:
        out.append("")
        out.append(f"НЕ ПОШЛИ ({len(res.dropped)}) — обрезание всегда названо:")
        for d in res.dropped[:30]:
            out.append(f"  · {d['url'][:88]} — {d['why']}")
        if len(res.dropped) > 30:
            out.append(f"  … и ещё {len(res.dropped) - 30}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Единственный шов для остальных команд
# ──────────────────────────────────────────────────────────────────────────────

def walk(conn, url: str, *, depth: int = MAX_DEPTH, max_pages: int = MAX_PAGES,
         force: bool = False, save: bool = True, **kw) -> tuple[Result | None, list[dict]]:
    """(картина обхода, маршруты) по вакансии из базы. Обход — ОДИН раз.

    `Result is None` означает «обход уже был, факты взяты из базы»: кэшом
    служит проставленная живость в `apply_option`. Кэш здесь принципиален —
    именно он позволяет звать обход из любой команды, не считая запросы и не
    вспоминая, делали ли его сегодня.

    Стартовые ссылки — всё, что о вакансии известно: `employer_url`, адрес
    кнопки отклика из выжимки, ссылки из текста, тело телеграм-поста и сам
    адрес вакансии. Их собирает `applyopt.gather`, второго списка нет.
    """
    import json  # noqa: PLC0415
    from . import store  # noqa: PLC0415 — ленивый: crawl зовут и без базы
    from .model import is_placeholder_company  # noqa: PLC0415

    row = conn.execute(
        "SELECT source, external_id, employer_url, company, raw FROM vacancy "
        "WHERE url = ?", (url,)).fetchone()
    if row is None:
        return None, []
    source, ext_id = row["source"], row["external_id"]
    known = store.apply_options(conn, source, ext_id)
    if known and not force and any(o.get("liveness") for o in known):
        return None, known     # обход уже был — второй раз за него не платим

    def _json(value):
        try:
            return json.loads(value) if value else None
        except (TypeError, ValueError):
            return None

    d = conn.execute("SELECT payload FROM detail WHERE source=? AND external_id=?",
                     (source, ext_id)).fetchone()
    payload = _json(d["payload"]) if d else None
    # Стартуем с ОБЪЕДИНЕНИЯ известного: маршруты прошлых волн плюс всё, что
    # видно в записи сейчас. Только по сохранённым маршрутам переобход не нашёл
    # бы ссылку, которую прошлый раз срезало бюджетом; только по свежим —
    # потерял бы найденное раньше. Лишняя ссылка стоит одного запроса,
    # потерянная — вакансии.
    fresh = applyopt.gather({"employer_url": row["employer_url"], "url": url,
                             "raw": _json(row["raw"])}, payload)
    seeds = list(dict.fromkeys([o["url"] for o in fresh]
                               + [o["url"] for o in known]))
    res = crawl(seeds, max_depth=depth, max_pages=max_pages, origin=url,
                note="ссылки вакансии", **kw)
    # Имя работодателя едет вместе с обходом: по нему `employer_guess` отличает
    # свой домен от случайного попутчика (витрина, поисковик, статья на vc.ru).
    known_company = (row["company"] or "").strip()
    res.company = "" if is_placeholder_company(known_company) else known_company
    found = routes(res)
    if not save:
        return res, found
    store.save_apply_options(conn, source, ext_id, found)
    live, why = liveness(res)
    best = best_contact(res)
    # Работодателя записываем ТОЛЬКО когда площадка его не назвала: у скрытой
    # вакансии домен собственной доски ATS — это и есть ответ «кто это», и
    # раньше его добывала модель вручную. Где имя есть, чужую догадку поверх
    # него класть нельзя.
    guess = employer_guess(res)
    company = (row["company"] or "").strip()
    hidden = not company or is_placeholder_company(company)
    # Живость и контакт уезжают в кэш ресёрча: это самые дорогие проверки
    # волны, и повторять их в следующей незачем (таблица ради этого и заведена).
    #
    # 🔴 «НЕИЗВЕСТНО» в кэш НЕ пишется. Стена или страница без признаков вакансии
    # — это отсутствие знания, а в таблице уже может лежать ЖИВА, проверенная
    # человеком или прошлой волной. Затирать знание незнанием нельзя: `research`
    # ровно для того и заведена, чтобы дорогой вердикт не приходилось получать
    # заново. Маршруты при этом пишутся всегда — там «не подтверждена» стоит у
    # конкретной ссылки и ничего чужого не портит.
    definite = live in ("ЖИВА", "МЕРТВА")
    store.save_research(
        conn, source, ext_id,
        liveness=live if definite else None,
        employer_revealed=(f"{guess['value']} — {guess['why']}"
                           if guess and hidden else None),
        evidence=(f"обход ссылок: {why}"
                  + (f"; контакт: {best['value']} ({best['kind']})" if best else "")
                  if definite else None))
    return res, store.apply_options(conn, source, ext_id)


def ensure(conn, url: str, **kw) -> list[dict]:
    """Маршруты вакансии С ФАКТАМИ, посчитанные один раз. Шов для команд.

    Обход подключается вызовом отсюда, а не флагом «не забудь»: забытый флаг —
    это ровно та работа, которую агент делал руками и пропускал.
    """
    return walk(conn, url, **kw)[1]
