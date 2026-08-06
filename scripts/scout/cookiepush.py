"""Возврат обновлённой сессии обратно в браузер пользователя.

Зачем это вообще есть. Площадки с ротацией refresh-токена (hirehi) выдают ОДИН
токен на все заходы: кто обновил его не последним — разлогинен. Когда scout ходит
на такую площадку куками браузера, ротация оседает у scout, а живая вкладка
пользователя умирает. Эта команда возвращает свежий токен туда, откуда его взяли,
чтобы пользователь не логинился заново.

🔴 Это ЕДИНСТВЕННОЕ место во всём scout, которое ПИШЕТ в профиль браузера
пользователя. Прямое разрешение в чате от 04.08.2026 («желательно чтобы после всех
махинаций авторизация была возвращена в мой браузер, в том числе новые токены»).
Всё остальное по-прежнему только читает.

Предохранители (ни один не отключается флагом):

1. **Браузер должен быть закрыт.** Работающий Chromium держит куки в памяти и
   переписывает БД при выходе — запись в живую базу либо не пройдёт по локу, либо
   молча пропадёт, а выглядеть будет как успех. Процесс жив → отказ и код 2.
2. **Бэкап БД до записи** в `.auth/backup/` — откатиться можно всегда.
3. **Только домены площадок** из общего allowlist: чужую куку эта команда
   не запишет, даже если её передать руками.
4. **Только UPDATE существующих строк** по (host_key, name, path). Новых кук
   не создаём: подсадить в браузер то, чего там не было, — не наша задача.
5. **Схема шифрования снимается с самой БД**, а не угадывается: у новых Chromium
   к открытому тексту приписан SHA256 домена, у старых — нет. Пишем ровно в том
   виде, в каком браузер эту куку читает.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

from . import cookieimport as ci


class PushError(RuntimeError):
    """Запись в браузер не выполнена. Причина обязана дойти до вывода."""


# Процессы, чьё присутствие означает «браузер открыт».
_PROC_PATTERNS = {
    "yandex": "Yandex",
    "chrome": "Google Chrome",
    "claude": "Claude",
}


def browser_running(source: str) -> bool:
    """Живой процесс браузера. Запись при нём бессмысленна и опасна — см. шапку."""
    pattern = _PROC_PATTERNS.get(source)
    if not pattern:
        # Неизвестный источник — считаем браузер живым и отказываемся писать.
        # Предохранитель, который «на всякий случай разрешает», предохранителем
        # не является: добавь запись в _PROC_PATTERNS, если браузер новый.
        return True
    try:
        res = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Не смогли проверить — считаем, что браузер жив: отказ дешевле порчи.
        return True
    return res.returncode == 0 and bool(res.stdout.strip())


def version_tag(encrypted: bytes | None) -> bytes:
    """Тег версии, которым помечена существующая кука (v10/v11).

    Пишем ровно тот, что стоял: браузер читает своим разбором, и подменённый
    тег — это молча отвергнутая кука, то есть тихий разлогин."""
    if encrypted and encrypted[:3] in (b"v10", b"v11"):
        return encrypted[:3]
    return b"v10"


def encrypt_value(plain: str, key: bytes, *, host_key: str = "",
                  with_host_prefix: bool = False, tag: bytes = b"v10") -> bytes:
    """Обратная операция к cookieimport._decrypt_value: Chromium v10.

    AES-128-CBC, IV — 16 пробелов, PKCS7-паддинг, префикс версии `v10`.
    `with_host_prefix` добавляет SHA256(host_key) перед открытым текстом — так
    делают новые Chromium, и без этого браузер куку молча отвергнет."""
    try:
        from cryptography.hazmat.primitives.ciphers import (  # noqa: PLC0415
            Cipher, algorithms, modes)
    except ImportError as e:
        raise PushError(
            "нужна библиотека cryptography:\n  .venv/bin/pip install cryptography"
        ) from e
    data = plain.encode("utf-8")
    if with_host_prefix:
        data = hashlib.sha256(host_key.encode("utf-8")).digest() + data
    pad = 16 - (len(data) % 16)
    data += bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return tag + encryptor.update(data) + encryptor.finalize()


def detect_host_prefix(encrypted: bytes, key: bytes, host_key: str) -> bool:
    """Пишет ли этот браузер SHA256 домена перед значением.

    Определяется по уже лежащей в БД куке того же хоста — расшифровываем и
    сравниваем первые 32 байта с хешем. Угадывать нельзя: ошибка в любую сторону
    даёт куку, которую браузер не примет, то есть тихий разлогин."""
    try:
        from cryptography.hazmat.primitives.ciphers import (  # noqa: PLC0415
            Cipher, algorithms, modes)
    except ImportError as e:
        raise PushError("нужна библиотека cryptography") from e
    if not encrypted:
        return False
    body = encrypted[3:] if encrypted[:3] in (b"v10", b"v11") else encrypted
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    if plain:
        pad = plain[-1]
        if 1 <= pad <= 16:
            plain = plain[:-pad]
    return plain[:32] == hashlib.sha256(host_key.encode("utf-8")).digest()


def _backup(db_path: str, source: str) -> str:
    backup_dir = os.path.join(ci.AUTH_DIR, "backup")
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, f"Cookies-{source}-{int(time.time())}.db")
    shutil.copy2(db_path, dst)
    os.chmod(dst, 0o600)
    return dst


def push(platform: str, source: str = "yandex", *,
         domains: tuple[str, ...] | None = None) -> int:
    """Переносит куки площадки из .auth/<платформа>.json в БД браузера.

    Коды: 0 — записано; 1 — нечего писать или записалось не всё;
    2 — браузер открыт / нет файлов; 3 — нет cryptography."""
    from . import auth  # noqa: PLC0415 — ленивый импорт, как в остальных командах

    state_file = auth.state_path(platform)
    if not os.path.exists(state_file):
        print(f"нет {state_file} — сначала прогон, который эту сессию обновил",
              file=sys.stderr)
        return 2
    if source not in ci.BROWSER_COOKIES:
        print(f"не знаю браузер {source!r}; есть: {', '.join(ci.BROWSER_COOKIES)}",
              file=sys.stderr)
        return 2

    if browser_running(source):
        print(f"⛔ {source} сейчас запущен. Работающий браузер держит куки в памяти "
              f"и перепишет базу при выходе — запись бы просто пропала.\n"
              f"   Закрой браузер полностью и повтори команду.", file=sys.stderr)
        return 2

    allow = domains or ci.ALLOWED_DOMAINS
    state = json.load(open(state_file, encoding="utf-8"))
    fresh = [c for c in state.get("cookies", [])
             if ci.domain_allowed(c.get("domain", ""), allow)]
    if not fresh:
        print(f"в {state_file} нет кук разрешённых доменов — писать нечего",
              file=sys.stderr)
        return 1

    db_path = os.path.expanduser(ci.BROWSER_COOKIES[source]["db"])
    if not os.path.exists(db_path):
        print(f"нет базы кук браузера: {db_path}", file=sys.stderr)
        return 2

    cfg = ci.BROWSER_COOKIES[source]
    try:
        key = ci._keychain_key(cfg["service"], cfg["account"])
    except ci.ImportError_ as e:
        print(str(e), file=sys.stderr)
        return 2

    backup = _backup(db_path, source)
    conn = sqlite3.connect(db_path)
    updated, missing = [], []
    try:
        for c in fresh:
            name, value = c.get("name"), c.get("value")
            host = c.get("domain") or ""
            row = conn.execute(
                "SELECT host_key, path, encrypted_value FROM cookies "
                "WHERE name = ? AND (host_key = ? OR host_key = ?)",
                (name, host, host.lstrip("."))).fetchone()
            if row is None:
                missing.append(f"{host}/{name}")
                continue
            host_key, path, old_blob = row
            prefixed = detect_host_prefix(old_blob, key, host_key)
            blob = encrypt_value(value, key, host_key=host_key,
                                 with_host_prefix=prefixed,
                                 tag=version_tag(old_blob))
            conn.execute(
                "UPDATE cookies SET encrypted_value = ?, value = '' "
                "WHERE name = ? AND host_key = ? AND path = ?",
                (sqlite3.Binary(blob), name, host_key, path))
            updated.append(f"{host_key}{path}:{name}")
        conn.commit()
    finally:
        conn.close()

    print(f"бэкап базы: {backup}")
    if updated:
        print(f"обновлено кук в {source}: {len(updated)}")
        for u in updated:
            print(f"  · {u}")
    if missing:
        print(f"не нашлось в браузере (не создаю новые): {', '.join(missing)}")
    print("Открой браузер — сессия должна подхватиться свежим токеном.")
    return 0 if updated else 1
