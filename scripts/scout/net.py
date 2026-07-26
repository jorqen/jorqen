"""HTTP без внешних зависимостей.

Только stdlib — сборщик должен одинаково запускаться локально, в облачной рутине
и на чужой машине, где ничего не установлено. Любая зависимость здесь означает
«в облаке не поднялось», а молча не отработавший источник — это ровно та потеря
вакансий, ради которой всё и затевалось.
"""

from __future__ import annotations

import gzip
import json
import random
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
                return raw.decode(charset, errors="replace"), resp.geturl()
        except urllib.error.HTTPError as e:
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
