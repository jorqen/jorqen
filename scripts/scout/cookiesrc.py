"""Откуда берутся куки: живое чтение браузера, файл экспорта или (необязательный) кэш.

Схема до этого модуля была «файл-кэш `.auth/browser.json` — единственный источник
правды»: его наполняли разовые команды, дальше все ходили в него. Это ломалось тихо
двумя способами. Кэш устаревал — и залогиненная площадка отдавала анонимную выдачу.
Кэш стирали — и `render` молча уходил анонимом, а `hh-sync` падал «нет сессии» при
живом входе в браузере. Теперь наоборот: **источник по умолчанию — сам браузер**,
кэш опционален и его удаление не ломает ничего.

Три уровня запроса доступа (и ни одним больше):

1. **Куки уже есть** — в выбранном браузере, в переданном json или в кэше — берём
   молча, ничего не спрашиваем и ничего не печатаем сверх одной строки источника.
2. **`auto` выбирает ОДИН браузер** — тот, что покрывает больше всего нужных доменов,
   и берёт всё из него. В выводе одна строка: «источник: yandex, покрыто 3/4 доменов».
3. **Доменов не хватает** — НЕ лезем автоматически в остальные браузеры. Печатаем,
   чего именно нет, и точную команду для добора: `--cookies-from yandex,chrome`.
   Комбинирование нескольких браузеров — только по явному указанию пользователя.

Никаких «залогинься везде» при старте: нужные домены считаются от ЗАДАЧИ (URL
команды, площадка этапа), а не от всего реестра площадок.

Границы те же, что у cookieimport: только домены площадок (жёсткий allowlist),
значения кук не печатаются, ключ расшифровки отдаёт macOS после подтверждения
пользователя, ничего не уезжает с машины.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import cookieimport as ci
from .auth import BROWSER_STATE, PLATFORMS

# Значение --cookies-from по умолчанию. Именно «сам браузер», а не файл.
DEFAULT_SPEC = "auto"

BROWSER_NAMES = tuple(ci.BROWSER_COOKIES)


# ──────────────────────────────────────────────────────────────────────────────
# Какие домены нужны задаче
# ──────────────────────────────────────────────────────────────────────────────

def domains_for_url(url: str) -> tuple[str, ...]:
    """Домены, которые нужны ДЛЯ ЭТОГО URL, и только они.

    Смысл — не спрашивать доступ ко всему сразу: команде `render hh.ru/...` нужен
    hh, и требовать при этом вход на geekjob незачем."""
    host = (urlsplit(url if "//" in url else "//" + url).hostname or "").lower()
    if not host:
        return ()
    for dom in ci.ALLOWED_DOMAINS:
        if host == dom or host.endswith("." + dom):
            return (dom,)
    return (host,)


def domains_for_platform(platform: str) -> tuple[str, ...]:
    return tuple(PLATFORMS.get(platform, {}).get("domains", ()) or (platform,))


# ──────────────────────────────────────────────────────────────────────────────
# Разбор файлов экспорта
# ──────────────────────────────────────────────────────────────────────────────

_EXT_SAMESITE = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax",
                 "strict": "Strict", "none": "None", "": "Lax"}


def _ext_cookie(c: dict) -> dict | None:
    """Кука из экспорта расширения (EditThisCookie / Cookie-Editor / Cookie Quick
    Manager) → формат Playwright. Отличия, из-за которых нельзя просто скопировать:
    срок лежит в `expirationDate` (float, секунды), сессионная кука помечена
    `session: true`, а sameSite пишется словами вроде `no_restriction`."""
    name, value = c.get("name"), c.get("value")
    if not name or value is None:
        return None
    same = _EXT_SAMESITE.get(str(c.get("sameSite") or "").lower().replace("-", "_"))
    if same is None:
        same = str(c.get("sameSite") or "Lax").capitalize()
        if same not in ("Lax", "Strict", "None"):
            same = "Lax"
    secure = bool(c.get("secure"))
    if same == "None" and not secure:
        same = "Lax"  # Playwright такую пару отклоняет
    exp = c.get("expirationDate", c.get("expires", c.get("expiry")))
    if c.get("session") or exp in (None, "", 0):
        expires: float = -1
    else:
        try:
            expires = float(exp)
        except (TypeError, ValueError):
            expires = -1
    domain = str(c.get("domain") or c.get("host") or c.get("hostKey") or "")
    if not domain:
        return None
    return {"name": str(name), "value": str(value), "domain": domain,
            "path": str(c.get("path") or "/"), "expires": expires,
            "httpOnly": bool(c.get("httpOnly") or c.get("http_only")),
            "secure": secure, "sameSite": same}


def parse_cookie_file(path: str) -> dict:
    """Файл с куками → storage_state. Распознаёт ОБА формата:

    * Playwright storage_state — `{"cookies": [...], "origins": [...]}`;
    * выгрузка расширения — просто массив объектов с `expirationDate`/`session`
      (EditThisCookie, Cookie-Editor и совместимые), в том числе завёрнутый
      в `{"cookies": [...]}` без `origins`.

    Кидает ValueError с внятным текстом: молча отдать пустой профиль здесь —
    это ровно «залогинен, но ходим анонимом»."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    origins: list[dict] = []
    if isinstance(raw, dict):
        origins = raw.get("origins") or []
        raw = raw.get("cookies", raw.get("Request Cookies", []))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: жду массив кук или storage_state с ключом cookies")
    cookies: list[dict] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        # storage_state отличается от экспорта расширения полем expires (а не
        # expirationDate) и отсутствием session/hostOnly. Приводим оба к одному виду.
        if "expires" in c and "expirationDate" not in c and "session" not in c:
            if c.get("name") and c.get("domain") is not None:
                cookies.append({k: c[k] for k in
                                ("name", "value", "domain", "path", "expires",
                                 "httpOnly", "secure", "sameSite") if k in c})
                continue
        got = _ext_cookie(c)
        if got:
            cookies.append(got)
    if not cookies:
        raise ValueError(f"{path}: не разобрал ни одной куки — это точно экспорт "
                         f"кук (storage_state или выгрузка расширения)?")
    return {"cookies": cookies, "origins": origins if isinstance(origins, list) else []}


# ──────────────────────────────────────────────────────────────────────────────
# Результат разрешения источника
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CookieSource:
    """Куки под задачу плюс честный отчёт о покрытии.

    `state` годится для `browser.new_context(storage_state=...)` как есть."""
    state: dict
    origin: str                       # человекочитаемый источник
    needed: tuple[str, ...] = ()
    covered: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[str]:
        return [d for d in self.needed if not self.covered.get(d)]

    @property
    def cookies(self) -> list[dict]:
        return self.state.get("cookies", [])

    def line(self) -> str:
        """Ровно одна строка про источник — то, что видит пользователь при успехе."""
        if not self.needed:
            return f"источник кук: {self.origin} ({len(self.cookies)} кук)"
        n = len(self.needed) - len(self.missing)
        return (f"источник кук: {self.origin}, покрыто {n}/{len(self.needed)} доменов"
                + (f" ({', '.join(f'{d}×{self.covered[d]}' for d in self.needed if self.covered.get(d))})"
                   if n else ""))

    def hint(self) -> str | None:
        """Что предложить, если доменов не хватило. Автоматически в остальные
        браузеры НЕ идём — только называем точную команду."""
        if not self.missing:
            return None
        others = [b for b in BROWSER_NAMES if b not in self.tried and _db_exists(b)]
        cmd = (f"--cookies-from {','.join(self.tried + others[:1])}"
               if others else "--cookies-from <путь-к-экспорту.json>")
        return (f"нет кук для: {', '.join(self.missing)} — источник {self.origin} их "
                f"не покрывает.\n  Добрать явно: {cmd}"
                + ("\n  Либо залогинься на площадке в браузере сам "
                   "(scout auth login <площадка>) — скрипт пароль не вводит."))

    def storage_for_playwright(self) -> dict:
        return {"cookies": ci.strip_meta(self.cookies),
                "origins": self.state.get("origins", [])}

    def cookie_header(self, domains: tuple[str, ...] | None = None) -> str | None:
        """Заголовок Cookie для stdlib-слоя: те же куки, тот же профиль."""
        doms = domains or self.needed
        now = datetime.now(timezone.utc).timestamp()
        pairs, seen = [], set()
        for c in self.cookies:
            dom = (c.get("domain") or "").lstrip(".")
            if doms and not any(dom == d or dom.endswith("." + d) for d in doms):
                continue
            exp = c.get("expires", -1)
            if exp and exp > 0 and exp < now:
                continue
            name = c.get("name")
            if name and name not in seen:
                seen.add(name)
                pairs.append(f"{name}={c.get('value', '')}")
        return "; ".join(pairs) or None


# ──────────────────────────────────────────────────────────────────────────────
# Живое чтение браузеров
# ──────────────────────────────────────────────────────────────────────────────

def _db_exists(browser: str) -> bool:
    try:
        return os.path.exists(ci._db_path(browser))
    except KeyError:
        return False


def coverage_without_keychain(browser: str,
                              domains: tuple[str, ...]) -> dict[str, int]:
    """{домен: сколько кук} по БД браузера БЕЗ расшифровки.

    Именно этим выбирается браузер в режиме `auto`: посчитать покрытие можно по
    одному host_key, и Keychain при выборе не тревожится вовсе — диалог доступа
    появится ровно один раз и ровно для выбранного браузера."""
    out: dict[str, int] = {}
    try:
        rows = ci._read_rows(ci._db_path(browser))
    except (ci.ImportError_, OSError, Exception):  # noqa: BLE001 — браузер мог быть не установлен
        return out
    for host_key, *_ in rows:
        h = (host_key or "").lstrip(".").lower()
        for d in domains:
            if h == d or h.endswith("." + d):
                out[d] = out.get(d, 0) + 1
    return out


_LIVE_CACHE: dict[tuple[str, tuple[str, ...]], list[dict]] = {}


def read_browser(browser: str, domains: tuple[str, ...]) -> list[dict]:
    """Расшифрованные куки площадок из живого браузера. Мемоизировано на процесс:
    один прогон scan трогает Keychain один раз, а не на каждом этапе."""
    key = (browser, domains)
    if key not in _LIVE_CACHE:
        _LIVE_CACHE[key] = ci.collect(browser, domains)
    return _LIVE_CACHE[key]


# ──────────────────────────────────────────────────────────────────────────────
# Постоянный профиль scout
# ──────────────────────────────────────────────────────────────────────────────
#
# После `auth login` сессия оседает НЕ в живом браузере пользователя, а в
# отдельном профиле scout — и площадки ротируют токены именно там. Если читать
# только живой браузер, stdlib-слой будет ходить со старой копией токена: у
# ротационных площадок это не «чуть устарело», а мгновенное протухание.
#
# Расшифровка та же: ключ в macOS Keychain один на БРАУЗЕР, а не на профиль,
# поэтому куки профиля scout читаются тем же ключом, что и живые.

def profile_db_path(browser: str) -> str | None:
    from . import render  # noqa: PLC0415 — цикл: render импортирует auth, а не нас
    try:
        return render.profile_db(browser)
    except KeyError:
        return None


def read_scout_profile(browser: str, domains: tuple[str, ...]) -> list[dict]:
    """Куки площадок из постоянного профиля scout. Нет профиля — пустой список,
    и это штатный случай: профиль заводится первым запуском браузерной команды."""
    db = profile_db_path(browser)
    if not db or not os.path.exists(db):
        return []
    cfg = ci.BROWSER_COOKIES[browser]
    key = ci._keychain_key(cfg["service"], cfg["account"])
    out: list[dict] = []
    for row in ci._read_rows(db):
        if not ci.domain_allowed(row[0], domains):
            continue
        try:
            cookie = ci._to_cookie(row, key)
        except ci.ImportError_:
            raise
        except Exception:  # noqa: BLE001 — одна битая кука не рушит чтение
            continue
        if cookie["value"]:
            out.append(cookie)
    return out


def with_scout_profile(browser: str, cookies: list[dict],
                       domains: tuple[str, ...]) -> tuple[list[dict], str | None]:
    """Домешивает куки профиля scout к живым. Свежая побеждает — merge_cookies
    сравнивает last_access/creation из БД, а не порядок аргументов.

    Возвращает (куки, что дописать в строку источника)."""
    try:
        extra = read_scout_profile(browser, domains)
    except ci.ImportError_:
        return cookies, None
    if not extra:
        return cookies, None
    return ci.merge_cookies(cookies, extra), f"+профиль scout ({len(extra)})"


def choose_browser(domains: tuple[str, ...]) -> tuple[str | None, dict[str, dict[str, int]]]:
    """Какой ОДИН браузер брать в режиме auto: максимум покрытых доменов,
    при равенстве — больше кук, дальше — свежее БД. Возвращает (имя, все покрытия)."""
    per: dict[str, dict[str, int]] = {}
    for b in BROWSER_NAMES:
        if _db_exists(b):
            per[b] = coverage_without_keychain(b, domains)

    def rank(name: str):
        cov = per[name]
        try:
            mtime = os.path.getmtime(ci._db_path(name))
        except OSError:
            mtime = 0
        return (len(cov), sum(cov.values()), mtime)

    alive = [b for b in per if per[b]]
    if not alive:
        return None, per
    return max(alive, key=rank), per


# ──────────────────────────────────────────────────────────────────────────────
# Главная точка входа
# ──────────────────────────────────────────────────────────────────────────────

def cache_state() -> dict | None:
    """Кэш `.auth/browser.json` — НЕОБЯЗАТЕЛЬНЫЙ ускоритель. Нет файла — None,
    и это штатный случай: всё берётся из браузера."""
    if not os.path.exists(BROWSER_STATE):
        return None
    try:
        return ci.load_state(BROWSER_STATE)
    except (json.JSONDecodeError, OSError):
        return None


def _origins_for(domains: tuple[str, ...]) -> list[dict]:
    """localStorage нужных площадок из кэша.

    Куки читаются из браузера живьём, а localStorage — нет: он лежит в LevelDB
    профиля, а не в SQLite, и вычитывать его мы не беремся. Между тем у SPA
    с клиентской авторизацией (hirehi на Supabase) сессия живёт именно там:
    с одними куками страница рисуется анонимной. Поэтому origins подмешиваются
    из кэша, если он есть, — это ускоритель, а не источник правды, и его
    отсутствие означает лишь анонимный вид, а не поломку."""
    cached = cache_state()
    if not cached:
        return []
    out = []
    for o in cached.get("origins", []):
        host = (urlsplit(o.get("origin", "")).hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in domains):
            out.append(o)
    return out


def _covered(cookies: list[dict], domains: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    now = datetime.now(timezone.utc).timestamp()
    for c in cookies:
        exp = c.get("expires", -1)
        if exp and exp > 0 and exp < now:
            continue  # протухшая кука покрытием не считается
        h = (c.get("domain") or "").lstrip(".").lower()
        for d in domains:
            if h == d or h.endswith("." + d):
                out[d] = out.get(d, 0) + 1
    return out


def resolve(spec: str | None = None, domains: tuple[str, ...] = (), *,
            use_cache: bool = False, write_cache: bool = False) -> CookieSource:
    """`--cookies-from <spec>` + нужные домены → куки под задачу.

    spec: `auto` (по умолчанию — живое чтение браузера), имя браузера, несколько
    имён через запятую (явное комбинирование), путь к json-экспорту, `none`
    (осознанный аноним). `use_cache=True` разрешает взять `.auth/browser.json`,
    если он уже покрывает нужные домены — это ускорение, а не источник правды.
    """
    spec = (spec or DEFAULT_SPEC).strip()
    doms = tuple(domains)

    # ── явный аноним ─────────────────────────────────────────────────────────
    if spec in ("none", "off", "anon"):
        return CookieSource({"cookies": [], "origins": []}, "аноним (--cookies-from none)",
                            needed=doms)

    # ── файл экспорта ────────────────────────────────────────────────────────
    if spec.endswith(".json") or os.sep in spec or spec.startswith("~"):
        path = os.path.expanduser(spec)
        state = parse_cookie_file(path)
        return CookieSource(state, f"файл {os.path.basename(path)}", needed=doms,
                            covered=_covered(state["cookies"], doms), tried=[])

    # ── явно перечисленные браузеры (комбинирование только так) ──────────────
    if spec != "auto":
        names = [n.strip() for n in spec.split(",") if n.strip()]
        unknown = [n for n in names if n not in ci.BROWSER_COOKIES]
        if unknown:
            raise ValueError(f"не знаю браузер {', '.join(unknown)}; "
                             f"есть: {', '.join(BROWSER_NAMES)}, либо путь к .json")
        cookies: list[dict] = []
        warns: list[str] = []
        extras: list[str] = []
        for n in names:
            if not _db_exists(n):
                warns.append(f"{n}: файла кук нет ({ci._db_path(n)})")
                continue
            try:
                cookies = ci.merge_cookies(cookies, read_browser(n, doms or ci.ALLOWED_DOMAINS))
            except ci.ImportError_ as e:
                warns.append(f"{n}: не поддался — {e}")
                continue
            cookies, extra = with_scout_profile(n, cookies, doms or ci.ALLOWED_DOMAINS)
            if extra:
                extras.append(extra)
        src = CookieSource({"cookies": cookies, "origins": []},
                           "+".join(names) + (" " + extras[0] if extras else ""),
                           needed=doms, covered=_covered(cookies, doms),
                           warnings=warns, tried=names)
        if write_cache and cookies:
            _write_cache(src)
        return src

    # ── auto ─────────────────────────────────────────────────────────────────
    # Уровень 1: куки уже есть в кэше и покрывают задачу — берём молча.
    if use_cache:
        cached = cache_state()
        if cached is not None:
            cov = _covered(cached.get("cookies", []), doms)
            if doms and all(cov.get(d) for d in doms):
                age = (datetime.now().timestamp() - os.path.getmtime(BROWSER_STATE)) / 3600
                return CookieSource(cached, f"кэш .auth/browser.json ({age:.0f} ч)",
                                    needed=doms, covered=cov)

    # Уровень 2: один браузер — тот, что покрывает больше нужных доменов.
    pick, per = choose_browser(doms or ci.ALLOWED_DOMAINS)
    warns: list[str] = []
    cookies = []
    if pick:
        try:
            cookies = read_browser(pick, doms or ci.ALLOWED_DOMAINS)
        except ci.ImportError_ as e:
            warns.append(f"{pick}: не поддался — {e}")
            pick = None
    if not pick or not cookies:
        # Живьём не вышло — вот здесь кэш и оправдан, но об этом надо СКАЗАТЬ.
        cached = cache_state()
        if cached is not None and cached.get("cookies"):
            warns.append("живое чтение браузера не дало кук — взят кэш "
                         f"{BROWSER_STATE}; он может быть устаревшим")
            return CookieSource(cached, "кэш .auth/browser.json (живое чтение не дало кук)",
                                needed=doms, covered=_covered(cached["cookies"], doms),
                                warnings=warns, tried=list(per))
        return CookieSource({"cookies": [], "origins": []}, "аноним (кук не нашлось)",
                            needed=doms, warnings=warns, tried=list(per))

    # Профиль scout домешивается ПОСЛЕ живого браузера: там лежат сессии, которые
    # пользователь завёл через `auth login`, и токены, проротированные площадками.
    cookies, extra = with_scout_profile(pick, cookies, doms or ci.ALLOWED_DOMAINS)
    src = CookieSource({"cookies": cookies, "origins": _origins_for(doms)},
                       f"{pick}{' ' + extra if extra else ''}",
                       needed=doms, covered=_covered(cookies, doms), warnings=warns,
                       tried=[pick])
    # Уровень 3: чего-то не хватает — НЕ лезем в остальные браузеры сами.
    # Подсказку печатает вызывающий (src.hint()), решение за пользователем.
    if write_cache:
        _write_cache(src)
    return src


def _write_cache(src: CookieSource) -> None:
    """Обновить необязательный кэш. Пишем только домены площадок."""
    try:
        state = ci.filter_state(src.state)
        base = ci.load_state(BROWSER_STATE)
        merged = ci.merge_cookies(base.get("cookies", []), state["cookies"])
        ci.write_state(merged, BROWSER_STATE, origins=state["origins"])
    except OSError:
        pass  # кэш необязателен: не записался — работаем дальше


def add_cookie_args(parser) -> None:
    """Общие флаги источника кук. Одинаковые у render/browse/hh-sync/detail/scan —
    пользователь не должен помнить, где какой флаг называется иначе."""
    parser.add_argument(
        "--cookies-from", default=DEFAULT_SPEC, metavar="ИСТОЧНИК",
        help="auto (по умолчанию — живое чтение браузера), yandex|chrome|claude, "
             "несколько через запятую (явное комбинирование), путь к json-экспорту "
             "(storage_state или выгрузка расширения), none — аноним")
    parser.add_argument("--cache", dest="cache", action="store_true", default=False,
                        help="разрешить кэш .auth/browser.json как ускорение "
                             "(по умолчанию куки читаются из браузера)")
    parser.add_argument("--no-cache", dest="cache", action="store_false",
                        help="никогда не использовать кэш (умолчание)")
    parser.add_argument("--save-cache", action="store_true",
                        help="записать прочитанные куки в кэш .auth/browser.json")


def from_args(args, domains: tuple[str, ...] = ()) -> CookieSource:
    return resolve(getattr(args, "cookies_from", None), domains,
                   use_cache=getattr(args, "cache", False),
                   write_cache=getattr(args, "save_cache", False))


def report(src: CookieSource, *, stream=None) -> None:
    """Одна строка про источник + предупреждения + подсказка о доборе."""
    import sys
    out = stream or sys.stderr
    print(src.line(), file=out)
    for w in src.warnings:
        print(f"  ! {w}", file=out)
    hint = src.hint()
    if hint:
        print(f"  {hint}", file=out)
