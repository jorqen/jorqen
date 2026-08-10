"""HTTP без внешних зависимостей.

Только stdlib — сборщик должен одинаково запускаться локально, в облачной рутине
и на чужой машине, где ничего не установлено. Любая зависимость здесь означает
«в облаке не поднялось», а молча не отработавший источник — это ровно та потеря
вакансий, ради которой всё и затевалось.
"""

from __future__ import annotations

import gzip
import html
import json
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    # Ровно то, что шлёт Chrome. Отсебятина здесь стоит дорого: hh.ru отвечает 406,
    # если в Accept появляется `application/json` — content negotiation решает, что
    # запросили не ту страницу, и молча выкидывает самый плотный источник из прогона.
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "close",
}


class FetchError(RuntimeError):
    """Источник не отработал. Причина обязана дойти до отчёта, а не потеряться.

    `state` — из словаря PAGE_STATE_RU: чем именно кончилась страница. Нужен,
    потому что «вакансию сняли» и «парсер отстал от вёрстки» до этого приезжали
    одной строкой, а чинятся по-разному: первое — не чинится вовсе, второе —
    срочно, иначе источник теряется целиком и молча.
    """

    def __init__(self, url: str, reason: str, status: int | None = None,
                 state: str | None = None):
        self.url, self.reason, self.status = url, reason, status
        self.state = state or state_from_status(status)
        super().__init__(f"{reason} ({url})")


class BlockedError(FetchError):
    """Площадка упёрлась в антибот-проверку или капчу.

    Отдельный класс, потому что это НЕ поломка и лечится не повтором, а человеком.
    Смысл в том, чтобы такая площадка выпадала из прогона аккуратно: остальные
    четырнадцать отрабатывают, в отчёте появляется строка «нужен твой заход»,
    и прогон не считается провалившимся целиком.

    Проверку мы не проходим и капчу не решаем — это обход защиты от ботов.
    Заходит пользователь сам, дальше сессия живёт в `.auth/`.
    """

    def __init__(self, url: str, reason: str, status: int | None = None,
                 state: str | None = None):
        # Стена по умолчанию — капча, но не всякая: «доступ ограничен» и голый
        # 403 это НЕ проверка «ты робот?», а закрытая дверь, и лечится она
        # входом/правами, а не тем, что человек однажды прокликает капчу.
        super().__init__(url, reason, status, state=state or wall_state(reason))


# Признаки антибот-стены в теле ответа. Проверены живьём на hh, Avito, Cloudflare.
_BLOCK_MARKERS = (
    "являетесь роботом", "вы не робот", "подтвердите, что вы",
    "доступ ограничен", "проблема с ip", "captcha", "recaptcha", "hcaptcha",
    "just a moment", "checking your browser", "cf-browser-verification",
    "cf_chl_opt", "attention required! | cloudflare",
    "access to this page has been denied", "enable javascript and cookies to continue",
    "unusual traffic from your computer",
    # Стены, которых здесь не хватало и которые проезжали как «обычная страница»:
    # AWS WAF (levels.fyi отдаёт 202 с challenge.js), DataDome, PerimeterX,
    # Incapsula. Через рендер это выглядело хуже всего — 344 КБ «страницы»,
    # в которой ноль вакансий, и парсер честно докладывал «ничего не нашлось».
    "awswaf.com", "challenge.js", "datadome", "px-captcha", "_incapsula_resource",
)

# Заголовки страниц-стен. Проверяются ОТДЕЛЬНО от тела и точным вхождением
# в <title>: «один момент» в свободном тексте вакансии встречается на раз,
# а в заголовке документа — только у Cloudflare. Русского челленджа Glassdoor
# («Один момент…», английского «Just a moment» в теле нет вовсе) не видел
# ни один маркер выше, и стена уезжала дальше как нормальная выдача.
_WALL_TITLES = (
    "один момент", "just a moment", "attention required", "security | glassdoor",
    "проверка браузера", "checking your browser", "access denied", "доступ ограничен",
)


def looks_blocked(text: str, status: int | None = None) -> str | None:
    """Возвращает найденный маркер антибот-стены или None.

    Смотрим только в начало документа: на полноценной странице вакансий слово
    «captcha» может встретиться в тексте вакансии, и путать это с блокировкой
    нельзя — иначе живой источник объявится заблокированным.
    """
    head = text[:6000].lower()
    for marker in _BLOCK_MARKERS:
        if marker in head:
            return marker
    title = _TITLE_RE.search(text[:4000])
    if title:
        low = html.unescape(title.group(1)).strip().lower()
        for marker in _WALL_TITLES:
            if marker in low:
                return f"заголовок страницы: {marker}"
    if status == 403 and len(text) < 3000:
        return f"HTTP 403, короткий ответ ({len(text)} б)"
    return None


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def wall_marker(text: str, status: int | None = None) -> str | None:
    """looks_blocked с поправкой на JSON: распарсившийся JSON — данные, не стена.

    Antibot-заслоны приходят HTML-страницами; API, ответивший валидным JSON,
    отвечает по существу, что бы ни лежало в самих данных. Живой случай:
    вакансия SteelMount ПРО антифрод — «CAPTCHA, антибот-системы» в первых
    600 байтах ответа wantapply — объявлялась антибот-проверкой, и живость
    вакансии было не узнать."""
    marker = looks_blocked(text, status)
    if marker:
        head = text.lstrip()[:1]
        if head in ("{", "["):
            try:
                json.loads(text)
                return None
            except ValueError:
                pass
    return marker


# ──────────────────────────────────────────────────────────────────────────────
# Состояние страницы одним значением
# ──────────────────────────────────────────────────────────────────────────────
#
# `looks_blocked` отвечает ровно на один вопрос — «стена или нет», — и все
# прочие исходы сваливаются в общую кучу «не получилось». Цена этого была видна
# живьём: деталка hh на любой странице без HH-Lux-InitialState писала «вёрстка
# сменилась или это не вакансия», и за одной строкой прятались два разных факта.
# Вакансию сняли — чинить нечего, запись пора закрывать. Парсер отстал от
# вёрстки — чинить немедленно, иначе источник теряется целиком и молча. Пока
# исход один, отличить их можно было только руками, открыв страницу глазами.

PAGE_OK = "ok"
PAGE_CAPTCHA = "captcha"
PAGE_DENIED = "denied"
PAGE_GONE = "gone"
PAGE_LAYOUT = "layout"
PAGE_NETWORK = "network"

# Как называть состояние в отчёте. Это не украшение: строка уезжает в текст
# ошибки, а её читает человек, который решает, что чинить.
PAGE_STATE_RU = {
    PAGE_OK: "страница на месте",
    PAGE_CAPTCHA: "антибот-проверка или капча",
    PAGE_DENIED: "доступ закрыт — нужен вход или права",
    PAGE_GONE: "вакансия снята",
    PAGE_LAYOUT: "вёрстка сменилась — парсер отстал",
    PAGE_NETWORK: "сетевая ошибка",
}

# Маркеры снятой вакансии. Русские варианты собраны по hh и Хабру, английские —
# по ATS-доскам, где страница отвечает 200 и текстом «this job is no longer
# available» вместо честного 410.
_GONE_MARKERS = (
    "вакансия не найдена", "вакансия удалена", "вакансия в архиве",
    "архивная вакансия", "вакансия закрыта", "вакансия снята",
    "вакансия больше не", "вакансия недоступна", "вакансия неактивна",
    "публикация вакансии прекращена", "эта вакансия была удалена",
    "no longer available", "no longer accepting", "no longer accepts",
    "position has been filled", "this job has expired", "job has been closed",
    "job not found", "vacancy not found", "this position is closed",
    "posting is no longer", "sorry, this job",
)

# Маркеры закрытой двери. Отличаются от стены тем, что за ними НЕ проверка
# «робот ли ты», а отсутствие входа или прав: капчу тут кликать некому.
_DENIED_MARKERS = (
    "войдите, чтобы", "войти, чтобы", "требуется авторизация", "нужно войти",
    "авторизуйтесь", "доступ запрещ", "недостаточно прав", "нет доступа",
    "sign in to continue", "sign in to view", "log in to view",
    "please log in", "please sign in", "authorization required",
    "authentication required", "access denied", "403 forbidden",
    "you must be logged in", "members only",
)

# Стены, которые на самом деле «доступ закрыт», а не «докажи, что не робот».
_DENIED_WALLS = ("доступ ограничен", "access to this page has been denied",
                 "http 403")


def wall_state(marker: str | None) -> str:
    """Стена → captcha или denied. Разные починки: одна снимается заходом
    человека, вторая — сессией и правами, и путать их значит гонять человека
    кликать капчу там, где её нет."""
    low = (marker or "").lower()
    return PAGE_DENIED if any(x in low for x in _DENIED_WALLS) else PAGE_CAPTCHA


def state_from_status(status: int | None) -> str:
    """HTTP-код → состояние. Без тела ответа: только то, что сказал сервер.

    404/410 — вакансии нет. 401/403 — дверь закрыта. 429 и 5xx — не наша
    поломка и лечится повтором позже, поэтому «сетевая». Ничего не известно
    (status=None) — тоже сетевая: сюда попадают таймауты и обрывы.
    """
    if status in (404, 410):
        return PAGE_GONE
    if status in (401, 403):
        return PAGE_DENIED
    if status is None or status == 429 or status >= 500:
        return PAGE_NETWORK
    return PAGE_LAYOUT


def classify_page(text: str, status: int | None = None, *,
                  parsed_ok: bool = False) -> tuple[str, str]:
    """(состояние, чем подтверждено) по телу ответа.

    `parsed_ok` — сказал ли ВЫЗЫВАЮЩИЙ, что нашёл в странице свою разметку.
    Эту часть знает только он: якорь у каждого источника свой (у hh —
    HH-Lux-InitialState, у Хабра — ssr-state). Поэтому распределение
    обязанностей такое: здесь — всё, что видно по телу и коду ответа; там —
    один флаг «моя разметка на месте». Без флага и без других признаков ответ
    один: вёрстка сменилась.

    Смотрим только в начало документа и в <title> — ровно по той же причине,
    что и `looks_blocked`: «вакансия закрыта» в тексте живого объявления
    («вакансия закрыта для откликов из других регионов») встречается, и путать
    это со снятой вакансией нельзя.
    """
    body = text or ""
    hay = body[:6000].lower()
    title = _TITLE_RE.search(body[:4000])
    if title:
        hay += "\n" + html.unescape(title.group(1)).strip().lower()

    marker = wall_marker(body, status)
    if marker:
        return wall_state(marker), f"стена: {marker}"
    for m in _GONE_MARKERS:
        if m in hay:
            return PAGE_GONE, f"на странице «{m}»"
    for m in _DENIED_MARKERS:
        if m in hay:
            return PAGE_DENIED, f"на странице «{m}»"
    if status and status >= 400:
        return state_from_status(status), f"HTTP {status}"
    if not body.strip():
        return PAGE_NETWORK, "пустой ответ"
    if parsed_ok:
        return PAGE_OK, "разметка источника на месте"
    return PAGE_LAYOUT, "страница отдалась целиком, но разметки вакансии в ней нет"


def error_state(exc: BaseException) -> tuple[str, str]:
    """(состояние, пояснение) по исключению — для мест, где страницы уже нет.

    Своё `state` у FetchError главнее любых догадок: его проставил тот, кто
    видел тело ответа.
    """
    state = getattr(exc, "state", None)
    if state:
        return state, getattr(exc, "reason", str(exc))
    if isinstance(exc, (TimeoutError, ConnectionError, ssl.SSLError,
                        urllib.error.URLError)):
        return PAGE_NETWORK, f"{type(exc).__name__}: {exc}"
    # Разбор упал на своих же данных (json, регулярка, индекс) — это не сеть
    # и не площадка, это мы не поняли страницу.
    return PAGE_LAYOUT, f"{type(exc).__name__}: {exc}"


# Потолок на тело ответа — и до, и ПОСЛЕ распаковки. Ядро ходит на 28 чужих
# площадок, и потолка не было ни там, ни там: 64 КБ gzip разворачивались в
# 64 МБ строки за 0.03 с (раздутие ×1028), а `parallel` держит восемь таких
# потоков разом. Ни одна живая страница вакансии и близко не подходит к этому
# размеру — самый крупный настоящий ответ в замерах был 1,4 МБ.
MAX_BODY = 32 * 1024 * 1024


class TooLargeError(FetchError):
    """Ответ больше потолка. Отдельный класс, чтобы не путать с обрывом связи."""


def _inflate(obj, data: bytes, url: str) -> bytes:
    """Распаковка порциями с потолком: целиком её звать нельзя.

    `gzip.decompress` разворачивает всё в память ДО того, как размер можно
    проверить, — то есть проверять после неё уже поздно.
    """
    out = bytearray()
    for chunk_start in range(0, len(data), 65536):
        out += obj.decompress(data[chunk_start:chunk_start + 65536], MAX_BODY - len(out) + 1)
        if len(out) > MAX_BODY:
            raise TooLargeError(url, f"распакованное тело больше {MAX_BODY // 1024 // 1024} МБ")
    out += obj.flush()
    if len(out) > MAX_BODY:
        raise TooLargeError(url, f"распакованное тело больше {MAX_BODY // 1024 // 1024} МБ")
    return bytes(out)


def _decode(resp) -> bytes:
    url = getattr(resp, "url", "") or ""
    data = resp.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise TooLargeError(url, f"тело ответа больше {MAX_BODY // 1024 // 1024} МБ")
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        return _inflate(zlib.decompressobj(16 + zlib.MAX_WBITS), data, url)
    if "deflate" in enc:
        try:
            return _inflate(zlib.decompressobj(), data, url)
        except zlib.error:
            return _inflate(zlib.decompressobj(-zlib.MAX_WBITS), data, url)
    return data


# Единственный шов для кэша сырых ответов. Объект (см. rawcache.Cache) обязан
# уметь `.get(url) -> (текст, финальный_url) | None` и `.put(url, текст, финальный)`.
#
# Почему здесь, а не обёрткой вокруг каждого источника: `fetch` зовут из двух
# десятков мест в трёх модулях, и обернуть их все — значит обязательно забыть
# одно. Один шов в общем месте либо работает для всех, либо не работает ни для
# кого — и это видно сразу.
_CACHE = None


def set_cache(cache) -> None:
    """Ставит (или снимает, если None) кэш сырых ответов на весь процесс."""
    global _CACHE  # noqa: PLW0603 — шов процессный по замыслу
    _CACHE = cache


def _retry_with_owner_cookies(url, headers, body, method, timeout, ctx,
                              had_cookies) -> tuple[str, str] | None:
    """Повтор запроса с куками ХОЗЯИНА. None — не помогло или их нет.

    Смысл ровно один: стена уже выдала человеку пропуск (`cf_clearance` живёт в
    его браузере), и предъявить этот пропуск — не обход проверки, а обычная
    работа от его имени. Капча при этом не решается и не автоматизируется: нет
    пропуска — стена так и называется.

    Куки читаются с диска, поэтому повтор стоит одного обращения к базе кук и
    только в тот момент, когда обычный путь уже упёрся.
    """
    if had_cookies:
        return None                       # куки уже передали, второй раз незачем
    try:
        from . import auth  # noqa: PLC0415 — цикл: auth знает про сеть
        # По АДРЕСУ, а не по хосту: `cookie_header` ждёт имя площадки из реестра,
        # и хост в этой роли отменял и `.auth/<площадка>.json`, и апексную
        # `cf_clearance` — то есть весь смысл повтора.
        jar = auth.cookie_header_for_url(url)
    except Exception:  # noqa: BLE001 — базы кук нет, браузер закрыт, нет прав
        return None
    if not jar:
        return None
    h = dict(headers or {})
    h["Cookie"] = jar
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = _decode(resp)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            if wall_marker(text, resp.status):
                return None               # пропуск не подошёл — это честная стена
            return text, resp.geturl()
    except Exception:  # noqa: BLE001 — не помогло: вызывающий объявит стену
        return None


def fetch(
    url: str,
    *,
    method: str = "GET",
    data: bytes | dict | None = None,
    headers: dict | None = None,
    timeout: int = 40,
    retries: int = 2,
    cookies: str | None = None,
) -> tuple[str, str]:
    """Возвращает (текст, финальный URL после редиректов).

    Финальный URL важен сам по себе: hh редиректит на гео-поддомен, а агрегаторы —
    на сайт работодателя, и это ровно то, что ищет резолвер отклика.
    """
    # Кэшируем только чистые GET без тела: POST — это действие, у него нет
    # свойства «тот же запрос даст тот же ответ», и подменять его сохранённым
    # ответом значит соврать про результат действия.
    cache = _CACHE if (method == "GET" and data is None) else None
    if cache is not None:
        hit = cache.get(url)
        if hit is not None:
            # Ответ пришёл из кэша — значит к площадке мы НЕ ходили, и пауза
            # перед следующей страницей не нужна: она защищает частоту
            # ОБРАЩЕНИЙ, а обращения не было. Замер 08.08.2026: переразбор трёх
            # источников из кэша занимал те же 10 секунд на каждый, целиком
            # состоявшие из сна. Флаг снимает ровно следующую паузу, а не
            # отключает вежливость: как только запрос уйдёт в сеть, она вернётся.
            from . import sources  # noqa: PLC0415 — цикл: sources импортирует net
            sources.skip_next_pause()
            return hit

    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    if cookies:
        h["Cookie"] = cookies

    body = data
    if isinstance(data, dict):
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")

    ctx = ssl.create_default_context()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = _decode(resp)
                charset = resp.headers.get_content_charset() or "utf-8"
                text = raw.decode(charset, errors="replace")
                marker = wall_marker(text, resp.status)
                if marker:
                    # 🔴 Прежде чем объявить стену, предъявляем ТО, ЧТО ХОЗЯИН
                    # УЖЕ ПРОШЁЛ. Требование владельца 09.08.2026: «мы по факту
                    # не бот, я человек, который ищет вакансии; скрипт должен
                    # работать ровно так же, как я, и от моего имени». В его
                    # браузере лежит `cf_clearance` — результат пройденной им
                    # проверки, и предъявить его законно: это не обход стены,
                    # а предъявление выданного стеной пропуска.
                    #
                    # Капчу мы по-прежнему не решаем и не автоматизируем: если
                    # пропуска нет или он не подошёл, стена так и называется.
                    second = _retry_with_owner_cookies(url, h, body, method,
                                                       timeout, ctx, cookies)
                    if second is not None:
                        text, final = second
                        if cache is not None:
                            cache.put(url, text, final)
                        return text, final
                    # Повторять тем же способом бесполезно: стена не рассосётся.
                    raise BlockedError(resp.geturl(), f"антибот-проверка ({marker})",
                                       resp.status)
                if cache is not None:
                    # В кэш идёт только успешный ответ. Класть туда стену или
                    # ошибку значит закрепить поломку на сутки: следующий прогон
                    # получил бы её из кэша, не сходив на площадку.
                    cache.put(url, text, resp.geturl())
                return text, resp.geturl()
        except BlockedError:
            raise
        except urllib.error.HTTPError as e:
            # ОТДЕЛЬНАЯ переменная, а не `body`. Раньше тело ошибки писалось прямо
            # в `body` — то самое, из которого собирается запрос, — и следующая
            # попытка уходила со СТРОКОЙ вместо байтов: «TypeError: POST data
            # should be bytes». То есть любой повтор после 502 у POST-источника
            # падал не там, где сломалось, и площадка пропадала из прогона
            # (ловилось на первом же 502 dreamoffer).
            err_body = ""
            try:
                # Тело ошибки приходит сжатым ровно так же, как успешный ответ.
                # Без распаковки маркеры искались бы в гzip-байтах, и стена Cloudflare
                # выглядела бы обычным «HTTP 403» вместо «нужен твой заход».
                err_body = _decode(e).decode(
                    e.headers.get_content_charset() or "utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            marker = wall_marker(err_body, e.code)
            if marker:
                # Стена бывает и кодом ответа (403 Cloudflare), а не только
                # текстом страницы. Пропуск хозяина предъявляем и здесь — иначе
                # лестница чинила бы ровно половину случаев.
                second = _retry_with_owner_cookies(url, h, body, method,
                                                   timeout, ctx, cookies)
                if second is not None:
                    text, final = second
                    if cache is not None:
                        cache.put(url, text, final)
                    return text, final
                raise BlockedError(url, f"антибот-проверка ({marker})", e.code) from e
            last = FetchError(url, f"HTTP {e.code}", e.code)
            # 4xx кроме 429 повторять бессмысленно — ответ не изменится.
            if e.code < 500 and e.code != 429:
                raise last
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, ConnectionError) as e:
            last = FetchError(url, f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001 — источник не должен ронять весь прогон
            last = FetchError(url, f"{type(e).__name__}: {e}")
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1) + random.random())
    raise last or FetchError(url, "unknown")


# Ответ оборвался на середине. Это НЕ поломка парсера и не стена: сервер закрыл
# соединение, не дослав тело. Повтор того же запроса тут почти не помогает —
# помогает попросить МЕНЬШЕ за раз. Живой случай (прогон #10, 05.08.2026):
# shadowhint при per_page=100 отдал IncompleteRead на 185 КБ и вернул ноль
# вакансий — а это единственная площадка, где без входа нет вообще ничего.
_TRUNCATED_MARKERS = ("incompleteread", "chunkedencoding", "content-length",
                      "connection broken")


def looks_truncated(err: BaseException) -> bool:
    """Ответ оборван на середине? Тогда лечение — уменьшить порцию, а не повторять.

    Смотрит на текст причины, потому что `FetchError` не носит исходный класс:
    `http.client.IncompleteRead` не наследует ни URLError, ни ConnectionError и
    приезжает через общий `except Exception`.
    """
    text = f"{type(err).__name__}: {getattr(err, 'reason', err)}".lower()
    return any(m in text for m in _TRUNCATED_MARKERS)


def fetch_json(url: str, **kw):
    # `headers=None`, переданный явно, — легальный вызов (так деталка careered
    # ходит анонимом без Bearer). Цепочка setdefault() на нём падала AttributeError
    # и роняла ВСЕ анонимные careered-выжимки прогона (2026-08-04, ~150 штук).
    headers = dict(kw.get("headers") or {})
    headers.setdefault("Accept", "application/json, text/plain, */*")
    kw["headers"] = headers
    text, _ = fetch(url, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError(url, f"ответ не JSON: {e}") from e


def qs(base: str, params: dict) -> str:
    """Собирает URL, выбрасывая None. Списки разворачиваются в повторяющиеся ключи."""
    flat: list[tuple[str, str]] = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            flat.extend((k, str(i)) for i in v)
        else:
            flat.append((k, str(v)))
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urllib.parse.urlencode(flat)}" if flat else base


def parallel(jobs: dict, workers: int = 8) -> dict:
    """Гоняет {имя: callable} параллельно. Возвращает {имя: (ok, результат|исключение)}.

    Исключение одного источника никогда не роняет прогон — иначе одна упавшая площадка
    отменяет четырнадцать отработавших.
    """
    out: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = (True, fut.result())
            except Exception as e:  # noqa: BLE001
                out[name] = (False, e)
    return out


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
