"""Импорт уже существующих браузерных сессий пользователя в единый профиль scout.

Зачем. Пользователь залогинен на площадках в своих браузерах — Яндекс.Браузере,
Chrome и в браузерной панели Claude. Логиниться там ещё раз руками ради scout незачем:
модуль берёт УЖЕ существующие куки этих сессий и складывает в единый Playwright
storage_state `.auth/browser.json`, которым дальше пользуются render, hh-sync и browse.

Границы, которые здесь не двигаются:

* **Только домены площадок.** Жёсткий allowlist ALLOWED_DOMAINS: hh, habr, hirehi,
  shadowhint, wantapply, geekjob, getmatch, careered и т.п. Ни google, ни банков,
  ни соцсетей, ни паспортных кук Яндекса. `--domains *` не поддерживается сознательно —
  домены перечисляются поимённо, звёздочка отклоняется.
* **Значения кук не печатаются.** `--list` показывает домен и число кук, и всё. Сами
  значения идут только из БД браузера в `.auth/browser.json` и никуда больше.
* **Ключ расшифровки достаёт macOS, а не мы.** Ключ лежит в системном Keychain; его
  отдаёт утилита `security` ПОСЛЕ того, как пользователь подтвердит доступ в диалоге
  macOS. Пароль/код мы не вводим, сам ключ в вывод не печатаем.
* **`.auth/browser.json` не покидает машину** — `.auth/` в .gitignore.

`cryptography` — опциональная зависимость (ставится в .venv): ядро scout остаётся
stdlib-only, поэтому её импорт ленивый и с понятной ошибкой, если библиотеки нет.
Не поддалась расшифровка (у Яндекса бывает своя схема, Keychain не подтвердили) —
честный отказ и совет `scout auth login --all`, без обхода защиты.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .auth import AUTH_DIR, BROWSER_STATE

# Домены площадок, с которыми работает scout. Только они и импортируются — список
# намеренно узкий и правится руками. Здесь НЕ должно быть: google, паспортных кук
# Яндекса (yandex.ru), банков, почты, соцсетей. Импорт чужого домена — это утечка
# предъявительского доступа туда, где он scout не нужен.
ALLOWED_DOMAINS: tuple[str, ...] = (
    "hh.ru", "career.habr.com", "habr.com", "hirehi.ru", "shadowhint.com",
    "wantapply.com", "geekjob.ru", "getmatch.ru", "careered.io", "rabota.ru",
    "hack-offer.tech", "find.dreamoffer.app",
    # ── площадки, которые мы обходим и которые закрыты антиботом ──────────────
    #
    # 🔴 Владелец 10.08.2026: «мы по факту не являемся ботом — я человек, который
    # ищет вакансии; скрипт должен работать ровно так же и искать от моего имени».
    # Именно это здесь и включается: у него в браузере лежит `cf_clearance` —
    # проверку Cloudflare он прошёл руками, — и без этих доменов в списке кука в
    # браузерный контекст не подставлялась. Итог: 60 из 60 страниц jooble
    # упирались в «Just a moment» при живой, пройденной человеком проверке.
    #
    # Это НЕ обход антибота: проверку по-прежнему проходит человек, мы лишь
    # пользуемся её результатом — ровно как его собственный браузер во второй
    # вкладке. Капча не решается и не автоматизируется.
    "jooble.org", "linkedin.com", "careerjet.ru", "careerjet.com",
    "adzuna.co.uk", "adzuna.de", "adzuna.pl", "adzuna.nl", "adzuna.com",
    "jobviewtrack.com", "vseti.app", "jobicy.com", "himalayas.app",
    "relocate.me", "europa.eu", "glassdoor.com",
)

# Откуда берём куки. Пути и имена Keychain-записей проверены на этой машине 30.07.2026.
BROWSER_COOKIES: dict[str, dict] = {
    "yandex": {
        "db": "~/Library/Application Support/Yandex/YandexBrowser/Default/Cookies",
        "service": "Yandex Safe Storage", "account": "Yandex"},
    "chrome": {
        "db": "~/Library/Application Support/Google/Chrome/Default/Cookies",
        "service": "Chrome Safe Storage", "account": "Chrome"},
    "claude": {
        "db": "~/Library/Application Support/Claude/Cookies",
        "service": "Claude Safe Storage", "account": "Claude Key"},
}


class ImportError_(RuntimeError):
    """Импорт из конкретного браузера не удался. Причина обязана дойти до вывода."""


def parse_domains(domains: list[str] | None) -> tuple[str, ...]:
    """Разбирает `--domains`. Пустой список → дефолтный allowlist. Звёздочка
    отклоняется: массовый импорт кук — ровно то, чего этот модуль не делает."""
    if not domains:
        return ALLOWED_DOMAINS
    if any(d.strip() in ("*", "all", "") for d in domains):
        raise ValueError("`--domains *` не поддерживается: перечисли домены площадок "
                         "поимённо. Массовый импорт кук — это утечка, а не удобство.")
    return tuple(d.strip().lower().lstrip(".") for d in domains if d.strip())


def domain_allowed(host: str, domains: tuple[str, ...]) -> bool:
    """host_key из БД (может быть с ведущей точкой) попадает под домен площадки?
    Совпадение самого домена или его поддомена — `.hh.ru` и `spb.hh.ru` считаются hh."""
    h = (host or "").lstrip(".").lower()
    return any(h == d or h.endswith("." + d) for d in domains)


# ──────────────────────────────────────────────────────────────────────────────
# Чтение БД (без расшифровки) — этого хватает для --list
# ──────────────────────────────────────────────────────────────────────────────

def _db_path(source: str) -> str:
    return os.path.expanduser(BROWSER_COOKIES[source]["db"])


def _copy_db(path: str) -> str:
    """Браузер держит файл под локом — работаем с копией во временном каталоге."""
    if not os.path.exists(path):
        raise ImportError_(f"файла кук нет: {path}")
    fd, tmp = tempfile.mkstemp(prefix="scout-cookies-", suffix=".db")
    os.close(fd)
    shutil.copy2(path, tmp)
    # Рядом с основной БД лежат WAL/SHM — без них свежие записи не видны.
    for ext in ("-wal", "-shm"):
        if os.path.exists(path + ext):
            shutil.copy2(path + ext, tmp + ext)
    return tmp


def _read_rows(db_path: str) -> list[tuple]:
    """Возвращает строки cookies. Значения — в encrypted_value (BLOB), их не трогаем,
    пока не дойдёт до расшифровки.

    last_access_utc/creation_utc нужны мержу: при импорте из двух браузеров
    побеждать должна СВЕЖАЯ кука, а не та, чей браузер стоит позже в словаре.
    На этом уже обожглись — шестидневная сессия из Chrome затирала сегодняшнюю
    из Яндекс.Браузера просто по порядку ключей."""
    tmp = _copy_db(db_path)
    try:
        conn = sqlite3.connect(tmp)
        try:
            return conn.execute(
                "SELECT host_key, name, encrypted_value, path, expires_utc, "
                "is_secure, is_httponly, samesite, last_access_utc, creation_utc "
                "FROM cookies").fetchall()
        finally:
            conn.close()
    finally:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                os.remove(p)


def list_counts(source: str, domains: tuple[str, ...]) -> dict[str, int]:
    """{домен: число кук} для --list. Расшифровка НЕ нужна — читаем только host_key,
    поэтому Keychain не тревожим и значения не раскрываем."""
    counts: dict[str, int] = {}
    for host_key, *_ in _read_rows(_db_path(source)):
        if domain_allowed(host_key, domains):
            key = (host_key or "").lstrip(".")
            counts[key] = counts.get(key, 0) + 1
    return counts


# ──────────────────────────────────────────────────────────────────────────────
# Расшифровка Chromium v10 (нужна только для реального импорта)
# ──────────────────────────────────────────────────────────────────────────────

def _keychain_key(service: str, account: str, timeout: int = 30) -> bytes:
    """AES-ключ из macOS Keychain → PBKDF2. Значение самого ключа никуда не печатается.

    `security` покажет пользователю диалог «разрешить доступ» — подтверждает ЕГО
    пользователь, один клик. В неинтерактивной среде диалог висит: ограничиваем
    временем и честно падаем, а не зависаем навсегда."""
    try:
        res = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service, "-a", account],
            capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ImportError_("нет утилиты `security` — это точно macOS?") from e
    except subprocess.TimeoutExpired as e:
        raise ImportError_(
            f"Keychain не ответил за {timeout}с — вероятно, ждёт подтверждения в диалоге "
            f"macOS. Подтверди доступ к «{service}» и повтори, либо `scout auth login --all`."
        ) from e
    if res.returncode != 0:
        raise ImportError_(
            f"Keychain не отдал ключ «{service}» (код {res.returncode}). "
            f"Если был отказ в диалоге — используй `scout auth login --all`.")
    password = res.stdout.rstrip(b"\n")
    # Соль/итерации/длина — константы Chromium для macOS, не наши.
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)


def _decrypt_value(encrypted: bytes, key: bytes) -> str:
    """Chromium v10: AES-128-CBC, IV = 16 пробелов, префикс версии + PKCS7-padding.

    В новых Chromium первые 32 байта расшифрованного — SHA256 домена (защита от
    подмены host); определяем по неюникодному началу и срезаем."""
    if not encrypted:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers import (  # noqa: PLC0415
            Cipher, algorithms, modes)
    except ImportError as e:
        raise ImportError_(
            "нужна библиотека cryptography для расшифровки кук:\n"
            "  .venv/bin/pip install cryptography\n"
            "Ядро scout остаётся stdlib-only — это зависимость только команды import."
        ) from e
    if encrypted[:3] in (b"v10", b"v11"):
        encrypted = encrypted[3:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    plain = decryptor.update(encrypted) + decryptor.finalize()
    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16:
            plain = plain[:-pad]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        # 32-байтовый SHA256-префикс новых Chromium.
        return plain[32:].decode("utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# Строка БД → cookie формата Playwright storage_state
# ──────────────────────────────────────────────────────────────────────────────

_SAMESITE = {-1: "Lax", 0: "None", 1: "Lax", 2: "Strict"}

# Ключ свежести куки. Живёт только внутри scout, в storage_state не уезжает.
META_TS = "_scout_ts"


def strip_meta(cookies: list[dict]) -> list[dict]:
    """Снимает служебные поля перед отдачей в Playwright: лишний ключ в cookie —
    это TypeError в add_cookies, а не молчаливое игнорирование."""
    return [{k: v for k, v in c.items() if not k.startswith("_scout")} for c in cookies]


def _chromium_expires(expires_utc: int) -> float:
    """Микросекунды с 1601-01-01 → unix-секунды. 0 (сессионная кука) → -1 у Playwright."""
    if not expires_utc:
        return -1
    unix = expires_utc / 1_000_000 - 11_644_473_600
    return unix if unix > 0 else -1


def _to_cookie(row: tuple, key: bytes) -> dict:
    host_key, name, enc, path, expires_utc, is_secure, is_httponly, samesite = row[:8]
    last_access, creation = (row[8] if len(row) > 8 else 0), (row[9] if len(row) > 9 else 0)
    same = _SAMESITE.get(samesite, "Lax")
    # sameSite=None без Secure Playwright не принимает — понижаем до Lax.
    if same == "None" and not is_secure:
        same = "Lax"
    return {
        "name": name,
        "value": _decrypt_value(enc, key),
        "domain": host_key,
        "path": path or "/",
        "expires": _chromium_expires(expires_utc),
        "httpOnly": bool(is_httponly),
        "secure": bool(is_secure),
        "sameSite": same,
        # Служебное поле мержа. Playwright таких ключей не принимает — его
        # обязательно снимает strip_meta() перед подстановкой в контекст.
        META_TS: max(last_access or 0, creation or 0),
    }


def collect(source: str, domains: tuple[str, ...]) -> list[dict]:
    """Куки площадок из браузера, расшифрованные, в формате Playwright.
    Достаёт ключ из Keychain — вызывать только для реального импорта, не для --list."""
    cfg = BROWSER_COOKIES[source]
    key = _keychain_key(cfg["service"], cfg["account"])
    out = []
    for row in _read_rows(_db_path(source)):
        if not domain_allowed(row[0], domains):
            continue
        try:
            cookie = _to_cookie(row, key)
        except ImportError_:
            raise
        except Exception:  # noqa: BLE001 — одна битая кука не рушит импорт
            continue
        if cookie["value"]:
            out.append(cookie)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Мерж в единый storage_state
# ──────────────────────────────────────────────────────────────────────────────

def _cookie_id(c: dict) -> tuple[str, str, str]:
    return ((c.get("domain") or "").lstrip("."), c.get("path") or "/", c.get("name") or "")


def merge_cookies(base: list[dict], incoming: list[dict]) -> list[dict]:
    """Мерж по (домен, путь, имя): свежая кука вытесняет старую, остальные остаются.
    Перетирания всего профиля нет — импорт из второго браузера дополняет первый.

    «Свежая» — по метке `META_TS` (last_access_utc/creation_utc из БД браузера),
    если она есть у ОБОИХ; иначе побеждает входящая. Раньше входящая побеждала
    безусловно, и порядок словаря BROWSER_COOKIES решал, чья сессия выживет:
    шестидневный Chrome затирал сегодняшний Яндекс.Браузер молча."""
    merged = {_cookie_id(c): c for c in base}
    for c in incoming:
        cid = _cookie_id(c)
        old = merged.get(cid)
        if old is not None:
            ts_new, ts_old = c.get(META_TS), old.get(META_TS)
            if ts_new is not None and ts_old is not None and ts_new < ts_old:
                continue  # у нас уже лежит более свежая — не трогаем
        merged[cid] = c
    return list(merged.values())


def filter_state(state: Mapping[str, Any],
                 domains: tuple[str, ...] = ALLOWED_DOMAINS) -> dict:
    """Оставляет в storage_state только домены площадок — и в куках, и в origins.

    Нужен везде, где состояние приходит из живого браузерного контекста
    (`browse`, `auth login`): Playwright отдаёт ВСЁ, что накопил контекст, включая
    трекерные куки Яндекса и mc.yandex.ru. Обещание модуля «ни google, ни
    паспортных кук Яндекса» должно держаться и на этом пути тоже."""
    cookies = [c for c in state.get("cookies", [])
               if domain_allowed(c.get("domain", ""), domains)]
    origins = [o for o in state.get("origins", [])
               if domain_allowed(urlsplit(o.get("origin", "")).hostname or "", domains)]
    return {"cookies": cookies, "origins": origins}


def merge_origins(base: list[dict], incoming: list[dict]) -> list[dict]:
    """Мерж localStorage по origin. Playwright возвращает origins ТОЛЬКО тех
    сайтов, которые в этом контексте реально открывались, — класть его результат
    поверх файла значит стирать localStorage всех невизитированных площадок."""
    merged = {o.get("origin"): o for o in base if o.get("origin")}
    for o in incoming:
        if o.get("origin"):
            merged[o["origin"]] = o
    return list(merged.values())


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"cookies": [], "origins": []}
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("cookies", [])
    state.setdefault("origins", [])
    return state


def _seed_from_existing(domains: tuple[str, ...]) -> list[dict]:
    """Куки площадок из уже лежащих `.auth/<площадка>.json` (например hh.json) —
    их подхватываем и вливаем, чтобы прежний вход не потерялся при переезде на профиль."""
    seed: list[dict] = []
    for p in glob.glob(os.path.join(AUTH_DIR, "*.json")):
        if os.path.abspath(p) == os.path.abspath(BROWSER_STATE):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                cookies = json.load(f).get("cookies", [])
        except (json.JSONDecodeError, OSError):
            continue
        seed += [c for c in cookies if domain_allowed(c.get("domain", ""), domains)]
    return seed


def write_state(cookies: list[dict], path: str = BROWSER_STATE,
                origins: list[dict] | None = None,
                domains: tuple[str, ...] = ALLOWED_DOMAINS) -> None:
    """Пишет профиль, ДОПОЛНЯЯ прежние origins, а не заменяя их.

    Allowlist применяется к итогу, а не только ко входящему: в профиле уже
    накопились куки yandex.ru/mc.yandex.ru/vk.com, натащенные путями, на которые
    фильтр раньше не распространялся (`auth login`, `browse`). Держать их —
    значит хранить предъявительский доступ туда, где scout не работает."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = load_state(path)
    state["cookies"] = strip_meta(cookies)
    if origins is not None:
        state["origins"] = merge_origins(state.get("origins", []), origins)
    state = filter_state(state, domains)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _sources_for(from_: str) -> list[str]:
    if from_ == "all":
        return [s for s in BROWSER_COOKIES if os.path.exists(_db_path(s))]
    return [from_]


def cli(from_: str = "all", domains: list[str] | None = None,
        list_only: bool = False) -> int:
    try:
        doms = parse_domains(domains)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2

    sources = _sources_for(from_)
    if not sources:
        print("Не нашёл ни одной БД кук из известных браузеров "
              f"({', '.join(BROWSER_COOKIES)}).", file=sys.stderr)
        return 1

    print(f"Домены площадок ({len(doms)}): {', '.join(doms)}")
    print("Импортируются ТОЛЬКО они. Значения кук не печатаются и с машины не уходят.\n")

    # ── --list: только счётчики, без расшифровки и без Keychain ──────────────
    if list_only:
        grand = 0
        for src in sources:
            print(f"## {src}  ({_db_path(src)})")
            try:
                counts = list_counts(src, doms)
            except ImportError_ as e:
                print(f"  не прочитал: {e}\n")
                continue
            if not counts:
                print("  кук площадок не найдено\n")
                continue
            for dom, n in sorted(counts.items(), key=lambda x: -x[1]):
                print(f"  {n:>4}  {dom}")
            grand += sum(counts.values())
            print()
        print(f"Итого кук площадок: {grand}. Запись не делалась (--list).")
        print("Импортировать: python3 -m scripts.scout auth import --from "
              f"{from_}")
        return 0

    # ── реальный импорт: расшифровка + мерж ──────────────────────────────────
    print("Ключ расшифровки берётся из macOS Keychain — если появится диалог "
          "«разрешить доступ», подтверди его (один клик).\n")
    cookies = merge_cookies(load_state(BROWSER_STATE).get("cookies", []),
                            _seed_from_existing(doms))
    ok_sources, failed = [], []
    for src in sources:
        try:
            got = collect(src, doms)
        except ImportError_ as e:
            print(f"## {src}: НЕ ПОДДАЛСЯ — {e}\n")
            failed.append(src)
            continue
        cookies = merge_cookies(cookies, got)
        by_dom: dict[str, int] = {}
        for c in got:
            d = (c["domain"] or "").lstrip(".")
            by_dom[d] = by_dom.get(d, 0) + 1
        print(f"## {src}: импортировано {len(got)} кук"
              + (" по доменам " + ", ".join(f"{d}×{n}" for d, n in sorted(by_dom.items()))
                 if by_dom else " — площадочных кук нет"))
        ok_sources.append(src)

    write_state(cookies)
    print(f"\nЕдиный профиль: {BROWSER_STATE} — всего кук {len(cookies)} "
          f"(в .gitignore, с машины не уезжает).")
    if failed:
        print(f"\nНе поддались: {', '.join(failed)}. Это не поломка — у браузера могла "
              f"быть своя схема шифрования или не подтверждён Keychain.\n"
              f"Fallback: `scout auth login --all` — одно окно, логинишься по табам сам.")
    return 0 if ok_sources else 1
