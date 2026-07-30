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
    """Источник не отработал. Причина обязана дойти до отчёта, а не потеряться."""

    def __init__(self, url: str, reason: str, status: int | None = None):
        self.url, self.reason, self.status = url, reason, status
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


def _decode(resp) -> bytes:
    data = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        return gzip.decompress(data)
    if "deflate" in enc:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


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
                marker = looks_blocked(text, resp.status)
                if marker:
                    # Повторять бесполезно: стена не рассосётся от второго запроса.
                    raise BlockedError(resp.geturl(), f"антибот-проверка ({marker})",
                                       resp.status)
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
            marker = looks_blocked(err_body, e.code)
            if marker:
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


def fetch_json(url: str, **kw):
    kw.setdefault("headers", {}).setdefault("Accept", "application/json, text/plain, */*")
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
