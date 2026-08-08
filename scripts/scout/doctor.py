"""doctor — одна команда на вопрос «что у меня сломано».

Зачем отдельная команда. Начало каждой сессии агента раньше выглядело так:
`auth status`, потом `coverage`, потом догадки — есть ли playwright, не занят ли
профиль браузера, заведены ли ключи, когда вообще был последний прогон. Четыре
вызова и чтение трёх выдач ради ответа, который умещается в экран.

Правила, по которым здесь всё написано:

* **Ни одной сетевой операции.** Всё, что проверяется, лежит на диске. Живость
  сессий — это `auth status` (там браузер и запросы к площадкам), и дублировать
  его тут нельзя: команда, которая иногда идёт в сеть на минуту, перестаёт быть
  той, которую запускают «на всякий случай».
* **Ничего не чинит.** Диагноз и одна строка «что сделать» — дальше решает
  человек. Единственное исключение — права `.auth/`: репозиторий публичный,
  и открытые куки чинятся молча (`auth.secure_auth_dir` это уже делает).
* **Отсутствие — не поломка.** Нет ключа jooble — площадка просто выключена,
  это ⚠️, а не 🔴. 🔴 остаётся за тем, из-за чего волна упадёт или соврёт.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys

from . import auth, sources_keyed, store

OK, WARN, BAD = "✅", "⚠️", "🔴"

# Пакеты сверх stdlib. Ядро обязано работать без них (инвариант 3), поэтому
# каждый описан тем, ЧТО без него отвалится, — иначе «playwright не найден»
# читается как «всё сломано», хотя без него живы 24 площадки из 28.
OPTIONAL = (
    ("playwright", "браузерные площадки (glassdoor, вход на площадки, render)"),
    ("price_parser", "разбор редких форматов вилок; без него работает свой парсер"),
    ("telethon", "telegram-каналы (tg-fetch, tg-dm, tg-mirror)"),
    # 🔴 Без него `mail-sync` падает этапом «УПАЛ», а почта — один из четырёх
    # каналов статусов откликов и единственный для компаний, которые не пишут
    # в hh. 08.08.2026 doctor отрапортовал «всё на месте», волна отработала
    # целиком, и только в покрытии выяснилось, что почта не читалась вовсе —
    # а в ней лежал свежий отказ по вакансии из топа шорт-листа.
    ("imap_tools", "статусы откликов из почты (mail-sync, mail-read)"),
    # 🔴 Без него куки браузера НЕ РАСШИФРОВЫВАЮТСЯ, и весь механизм «вход,
    # сделанный руками, доезжает до сборщика» молча выключается: scout
    # откатывается на устаревший слепок `.auth/browser.json` и объявляет живые
    # сессии истёкшими. 08.08.2026 это стоило ложной тревоги «wantapply: нужен
    # новый вход» при живом входе (токен был действителен ещё сутки), а заодно
    # прямых ссылок в ATS работодателя по всем вакансиям площадки.
    ("cryptography", "чтение кук браузера — без него входы площадок не видны"),
)


def _size(path: str) -> str:
    try:
        n = os.path.getsize(path)
    except OSError:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} ГБ"


def _python() -> list[tuple[str, str]]:
    v = sys.version_info
    rows = [(OK, f"python {v.major}.{v.minor}.{v.micro} — {sys.executable}")]
    if not os.path.exists(".venv/bin/python"):
        rows.append((WARN, "нет .venv — ядро поднимется и так (инвариант 3), "
                           "но браузерные площадки и telegram отвалятся"))
    for mod, gain in OPTIONAL:
        try:
            __import__(mod)
        except ImportError:
            rows.append((WARN, f"нет {mod} — не будет: {gain}"))
        else:
            rows.append((OK, f"{mod} на месте"))
    rows += _browsers()
    return rows


def _browsers() -> list[tuple[str, str]]:
    """Установлен ли САМ браузер, а не только пакет playwright.

    🔴 Пакет и браузер ставятся отдельно, и проверка импорта про второй не
    говорит ничего. Живой случай 08.08.2026: `playwright на месте`, а каталог
    сборок пуст — остался один ffmpeg. Всё браузерное молча не работало:
    `render` падал, `channel --render` возвращал «рендер тоже не прошёл», а
    живость вакансий hh проверить было нечем, потому что площадка уводит робота
    на страницу VPN-проверки и отдаёт её с кодом 200.
    """
    try:
        __import__("playwright")
    except ImportError:
        return []
    root = os.path.expanduser(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        or "~/Library/Caches/ms-playwright")
    if not os.path.isdir(root):
        return [(WARN, "браузеров playwright нет вовсе — `.venv/bin/python -m "
                       "playwright install chromium`; без них render, "
                       "channel --render и проверка живости за стеной не работают")]
    builds = [d for d in os.listdir(root)
              if d.startswith(("chromium", "firefox", "webkit"))]
    # 🔴 `chromium` и `chromium_headless_shell` — РАЗНЫЕ сборки, и ставятся они
    # раздельно. `render` зовёт `chromium.launch(headless=True)`, то есть именно
    # headless-shell. Проверка «есть хоть какой-то chromium» это пропускала:
    # 08.08.2026 doctor рапортовал «браузеры на месте: chromium-1234», а render
    # падал «Executable doesn't exist at …/chromium_headless_shell-1234/…».
    if builds and not any(d.startswith("chromium_headless_shell") for d in builds):
        return [(WARN, f"есть {', '.join(sorted(builds)[:2])}, но НЕТ "
                       f"chromium_headless_shell — именно его запускает render "
                       f"(`chromium.launch(headless=True)`). Поставь: "
                       f"`.venv/bin/python -m playwright install "
                       f"chromium-headless-shell`")]
    if not builds:
        have = ", ".join(sorted(os.listdir(root))[:4]) or "пусто"
        return [(WARN, f"пакет playwright есть, а браузеров НЕТ (в {root}: {have}) "
                       f"— `.venv/bin/python -m playwright install chromium`. "
                       f"Молча отваливаются render, channel --render и живость "
                       f"страниц за антибот-стеной")]
    return [(OK, f"браузеры playwright: {', '.join(sorted(builds)[:3])}")]


def _db(path: str) -> list[tuple[str, str]]:
    if not os.path.exists(path):
        return [(WARN, f"базы {path} нет — первый `wave` её заведёт")]
    rows = [(OK, f"база {path}, {_size(path)}")]
    try:
        # Отдельное подключение с таймаутом в ноль: смысл проверки как раз в том,
        # чтобы УПАСТЬ, если базу держит другой процесс, а не ждать его минуту.
        conn = sqlite3.connect(path, timeout=0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE").fetchall()
            conn.rollback()
        except sqlite3.OperationalError:
            rows.append((BAD, "база ЗАБЛОКИРОВАНА другим процессом — идёт вторая "
                              "волна или зависла прошлая; `ps aux | grep scout`"))
        n = conn.execute("SELECT COUNT(*) FROM vacancy").fetchone()[0]
        groups = conn.execute("SELECT COUNT(DISTINCT dup_key) FROM vacancy").fetchone()[0]
        rows.append((OK, f"вакансий {n}, групп после дедупа {groups} "
                         f"(схлопнуто {n - groups})"))
        cache = store.raw_cache_stats(conn)
        if cache.get("pages"):
            # Класс называется Cache. Имя RawCache не существовало никогда, а
            # ветка живёт только при НЕПУСТОМ кэше — то есть doctor падал
            # ImportError ровно после первой волны, когда его и запускают
            # первым делом. Пустая база это скрывала: и на чистой машине, и в
            # тесте ветка просто не выполнялась.
            from .rawcache import Cache  # noqa: PLC0415 — только ради срока
            rows.append((OK, f"кэш ответов площадок за сегодня: {cache['pages']} "
                             f"страниц, {cache['bytes'] / 1024 ** 2:.1f} МБ "
                             f"(старше {Cache.KEEP_DAYS} дн. чистится сам)"))
        last = conn.execute(
            "SELECT started_at, finished_at, query FROM run "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if last is None:
            rows.append((WARN, "прогонов ещё не было — начни с "
                               "`budget --days 3`, потом `wave --days 3`"))
        elif not last["finished_at"]:
            rows.append((WARN, f"последний прогон от {last['started_at'][:16]} "
                               f"НЕ ЗАВЕРШЁН — упал или его прервали"))
        else:
            rows.append((OK, f"последний прогон {last['finished_at'][:16]}, "
                             f"запрос «{last['query']}»"))
        conn.close()
    except sqlite3.DatabaseError as e:
        rows.append((BAD, f"база не читается: {e}"))
    return rows


def _browser_profile() -> list[tuple[str, str]]:
    """Постоянный профиль. Его занятость — самая частая причина «оно зависло»."""
    path = auth.profile_dir("chrome-profile")
    if not os.path.isdir(path):
        return [(WARN, f"постоянного профиля {path} нет — glassdoor и вход "
                       f"на площадки поднимут его сами при первом запуске")]
    rows = [(OK, f"профиль браузера {path}, {_size(os.path.join(path, 'Default', 'Cookies'))} кук")]
    # Chromium держит SingletonLock, пока окно живо. Запуск второго процесса на
    # том же профиле не падает, а МОЛЧА ждёт, и это выглядит как зависший обход.
    if os.path.lexists(os.path.join(path, "SingletonLock")):
        rows.append((WARN, "профиль ЗАНЯТ — окно браузера открыто. Второй запуск "
                           "на нём будет ждать молча; закрой окно перед волной"))
    return rows


def _keys() -> list[tuple[str, str]]:
    rows = []
    for name, p in sources_keyed.PLATFORMS.items():
        if sources_keyed.keys(name) is None:
            rows.append((WARN, f"{name}: ключа нет ({p.env_file}) — площадка "
                               f"выключена. Где брать: {p.where}"))
        else:
            rows.append((OK, f"{name}: ключ на месте"))
    return rows


def _sessions() -> list[tuple[str, str]]:
    """Состояние входов — тем же способом, что предупреждение перед волной.

    Своей проверки здесь нет намеренно. `authrefresh.preflight` уже отвечает на
    этот вопрос без сети (читает куки браузера и файлы `.auth/`) и, главное, уже
    знает, кому вход НЕ нужен: geekjob отдаёт всё анониму, habr-вход даёт только
    историю откликов. Написать вторую проверку значит завести второй ответ,
    который разойдётся с первым, — в проекте это уже проходили на фильтре ролей.

    Файл сессии на диске живой сессией не является: он мог протухнуть неделю
    назад. Живость разбирает `auth check`, который для этого открывает страницу.
    """
    from . import authrefresh  # noqa: PLC0415 — тянет auth и куки, не всем нужно

    rows: list[tuple[str, str]] = []
    for r in authrefresh.preflight():
        if r["state"] == "logged_in":
            rows.append((OK, f"{r['platform']}: вход есть"))
        elif r["state"] == "anonymous":
            fix = (f"поднимется само: `scout auth refresh {r['platform']}`"
                   if r["renewable"] else f"нужен вход: `scout auth login {r['platform']}`")
            rows.append((BAD if r["critical"] else WARN,
                         f"{r['platform']}: входа нет — теряем {r['loss']}. {fix}"))
        else:
            rows.append((WARN, f"{r['platform']}: {r['why'][:80]}"))
    rows.append((OK, "живость здесь НЕ проверяется — это `auth check`, "
                     "он поднимает браузер и открывает страницу площадки"))
    return rows


def _secrets() -> list[tuple[str, str]]:
    """Репозиторий публичный. Куки с правами 0644 — это утечка, а не замечание.

    🔴 `prune_foreign=False` здесь обязателен. По умолчанию `secure_auth_dir`
    делает ВТОРОЕ дело — вырезает чужие домены из сохранённых сессий, то есть
    переписывает `.auth/*.json`. Диагностическая команда, молча правящая файлы
    сессий, — не то, что обещано в шапке этого модуля («ничего не чинит, кроме
    прав»). Чистка доменов осталась там, где её просят явно: `auth secure`.
    """
    fixed = auth.secure_auth_dir(prune_foreign=False)
    if fixed:
        return [(WARN, f"права поправлены на {len(fixed)} файлах в .auth/ "
                       f"(было доступно чужим): {', '.join(os.path.basename(f) for f in fixed[:6])}")]
    return [(OK, ".auth/ закрыт правами 0600 — куки чужому пользователю недоступны")]


def _disk() -> list[tuple[str, str]]:
    try:
        free = shutil.disk_usage(".").free
    except OSError as e:
        # Пустой список здесь означал бы, что раздел «Диск» просто ИСЧЕЗ из
        # отчёта, и «не смогли посмотреть» стало бы неотличимо от «всё хорошо».
        # У команды, которая отвечает на вопрос «что сломано», это худший
        # возможный ответ.
        return [(WARN, f"свободное место не посмотреть: {e}")]
    gb = free / 1024 ** 3
    if gb < 1:
        return [(BAD, f"на диске {gb:.1f} ГБ — браузерному профилю и кэшу мало")]
    if gb < 5:
        return [(WARN, f"на диске {gb:.1f} ГБ")]
    return [(OK, f"на диске {gb:.0f} ГБ")]


SECTIONS = (
    ("Окружение", _python),
    ("База", None),            # особый: ему нужен путь
    ("Браузер", _browser_profile),
    ("Ключи площадок", _keys),
    ("Сессии", _sessions),
    ("Секреты", _secrets),
    ("Диск", _disk),
)


def report(db: str = store.DEFAULT_DB) -> tuple[list[str], int]:
    """(строки отчёта, число 🔴). Печать — в cli, чтобы отчёт можно было тестировать."""
    out: list[str] = ["# scout doctor — что на этой машине сломано", ""]
    bad = 0
    for title, fn in SECTIONS:
        rows = _db(db) if fn is None else fn()
        if not rows:
            continue
        out.append(f"## {title}")
        for mark, text in rows:
            out.append(f"{mark} {text}")
            bad += mark == BAD
        out.append("")
    out.append(f"Итог: {'всё на месте' if not bad else f'ПОЛОМОК {bad}'}. "
               f"⚠️ — это выключенное, а не сломанное. Чего ждать от каждой "
               f"площадки — `coverage`, живость входов — `auth check`.")
    return out, bad


def cli(args) -> int:
    lines, bad = report(getattr(args, "db", store.DEFAULT_DB))
    print("\n".join(lines))
    return 1 if bad else 0
