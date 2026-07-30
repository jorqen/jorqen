"""Браузерный слой: настоящий браузер пользователя вместо подделанного headless.

Раньше здесь был один путь — playwright-chromium в headless с подставленным
User-Agent. Он ломался тихо и дорого:

* headless представляется `HeadlessChrome/…`, и hirehi.ru отдаёт на такой UA
  403 в 48 байт. Лечили это подстановкой `Chrome/142.0.0.0` — то есть выдумкой:
  в реальном headless-шелле стоит 149, а Chrome 142 на этой машине не существует;
* сессия жила в разовом `storage_state`: контекст создавался, площадка ротировала
  refresh-токен, контекст выбрасывался — и у пользователя в живом браузере
  оставался сожжённый токен. Симптом обманчивый: не «войдите», а 403/аноним.

Теперь основной путь — НАСТОЯЩИЙ бинарь браузера пользователя (Яндекс.Браузер
или Chrome) на ОТДЕЛЬНОМ постоянном профиле `.auth/<браузер>-profile`:

* UA настоящий (`Chrome/148.0.0.0 YaBrowser/26.6.0.0`), подделывать нечего;
* профиль постоянный — ротация refresh-токенов оседает в нём сама, «протухания
  за три часа» больше нет;
* профиль ОТДЕЛЬНЫЙ — живой профиль пользователя не блокируется и не портится,
  браузер можно не закрывать;
* засевается он ТОЛЬКО куками площадок из allowlist: копируется файл БД, из копии
  удаляется всё, что не площадка. Значения кук не читаются и не расшифровываются.

Обязателен снятый дефолт Playwright `--use-mock-keychain`. Без него браузер
получает фальшивый Keychain, не может расшифровать v10-куки и МОЛЧА их удаляет
(замерено: 3376 кук → 41, из них hh 54 → 0). Браузер при этом открывается,
страницы грузятся, всё «работает» — просто везде аноним.

Антибот-проверки НЕ обходятся. Настоящий браузер с сессией, которую пользователь
сам завёл, — легитимный заход. Но если после ожидания страница осталась челленджем
(маркеры из net.py) — это статус АНТИБОТ и остановка: проверку проходит человек.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager

from .auth import BROWSER_STATE, PLATFORMS, profile_dir, resolve_storage
from .net import UA, BlockedError, FetchError, looks_blocked


class RenderUnavailable(RuntimeError):
    """Playwright не установлен. Отдельный класс, чтобы вызывающий мог напечатать
    инструкцию и жить дальше — рендер опционален, как и весь браузерный слой."""


class ProfileBusy(RuntimeError):
    """Профиль занят другим процессом. Это не поломка и не повод падать стектрейсом:
    у Chromium на профиле висит `SingletonLock`, и второй запуск честно отбивается.
    Пользователю нужно понятное «кто держит и что сделать»."""


PLAYWRIGHT_HOWTO = """Нужен Playwright — он используется для рендера SPA и авторизованных страниц.
  pip install playwright && playwright install chromium
Без него работает всё остальное: SPA-площадки забираются глазами через браузер."""

# ──────────────────────────────────────────────────────────────────────────────
# Реестр браузеров
# ──────────────────────────────────────────────────────────────────────────────
#
# 🔴 Яндекс.Браузер здесь НЕ запускается. Решение пользователя от 30.07.2026:
# из него берутся только куки (чтение копии БД в cookieimport), а сам браузер
# трогать нельзя — прототип, открывший его настоящий профиль, переписал
# Preferences и сбросил настройки интерфейса. Данные (пароли, куки, история,
# закладки) при этом уцелели, но повторять это нельзя ни при каких условиях.
#
# Что осталось:
# `chromium` — встроенный playwright-шелл, ПУТЬ ПО УМОЛЧАНИЮ. Изолирован, ничего
#              пользовательского не трогает, куки подставляются в контекст.
# `chrome`   — настоящий Chrome на ОТДЕЛЬНОМ профиле scout внутри `.auth/`.
#              Нужен там, где сайт отдаёт встроенному шеллу 403 (см. real_context).
#
# `bin`      — бинарь браузера; `cookies` — имя источника в cookieimport;
# `profile`  — каталог отдельного профиля scout, НИКОГДА не профиль пользователя.

BROWSERS: dict[str, dict] = {
    "chrome": {
        "bin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "cookies": "chrome",
        "profile": "chrome-profile",
        "title": "Google Chrome",
    },
}

BUNDLED = "chromium"
BROWSER_CHOICES = (*BROWSERS, BUNDLED)

LAUNCH_ARGS = [
    "--no-first-run", "--no-default-browser-check", "--disable-sync",
    "--no-service-autorun",
    # Окно уезжает за край экрана, поэтому его нельзя усыплять как «невидимое»:
    # иначе таймеры SPA встают и страница не дорисовывается.
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

# Ровно один снятый дефолт Playwright — см. шапку модуля. Список именно такой
# длины: снимать что-то ещё «за компанию» здесь нельзя, каждый флаг что-то держит.
IGNORE_DEFAULTS = ["--use-mock-keychain"]

# Площадки, где сессия держится на ротации короткоживущего токена: сервер выдаёт
# новый refresh-токен на каждый заход, и не сохранить его — значит сжечь сессию.
# На постоянном профиле это перестало быть проблемой (ротация оседает в профиле),
# но для запасного bundled-пути с разовым storage_state — по-прежнему актуально.
ROTATING_SESSION_HOSTS = ("hirehi.ru", "wantapply.com")


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        raise RenderUnavailable(PLAYWRIGHT_HOWTO)
    return sync_playwright


# ──────────────────────────────────────────────────────────────────────────────
# Какой браузер брать
# ──────────────────────────────────────────────────────────────────────────────

def browser_installed(name: str) -> bool:
    cfg = BROWSERS.get(name)
    return bool(cfg) and os.path.exists(cfg["bin"])


def installed_browsers() -> list[str]:
    return [n for n in BROWSERS if browser_installed(n)]


def default_browser(domains: tuple[str, ...] = ()) -> str:
    """По умолчанию — встроенный chromium: он изолирован и ничего пользовательского
    не трогает. Куки в него подставляются из браузера пользователя (чтение копии БД).

    Настоящий Chrome берётся, только если его попросили явно (`--browser chrome`
    или `SCOUT_BROWSER=chrome`) — он нужен там, где сайт отдаёт встроенному шеллу 403.
    Яндекс.Браузер не запускается никогда, см. комментарий к BROWSERS.
    """
    env = (os.environ.get("SCOUT_BROWSER") or "").strip().lower()
    if env in BROWSER_CHOICES:
        return env
    return BUNDLED


def add_browser_args(parser) -> None:
    """Общий флаг выбора браузера — рядом с `cookiesrc.add_cookie_args`, чтобы
    пользователь не вспоминал, где флаг называется иначе."""
    parser.add_argument(
        "--browser", default=None, metavar="БРАУЗЕР",
        choices=[*BROWSER_CHOICES, "auto"],
        help="chromium (встроенный playwright-шелл, по умолчанию — изолирован) | "
             "chrome (настоящий, на отдельном профиле scout; нужен там, где сайт "
             "отдаёт шеллу 403) | auto. Яндекс.Браузер не запускается: из него "
             "берутся только куки")


def pick_browser(spec: str | None, domains: tuple[str, ...] = ()) -> str:
    """`--browser yandex|chrome|chromium` → имя. None/auto → default_browser()."""
    s = (spec or "").strip().lower()
    if not s or s == "auto":
        return default_browser(domains)
    if s not in BROWSER_CHOICES:
        raise ValueError(f"не знаю браузер {spec!r}; есть: {', '.join(BROWSER_CHOICES)}")
    if s in BROWSERS and not browser_installed(s):
        raise ValueError(f"{BROWSERS[s]['title']} не установлен ({BROWSERS[s]['bin']}); "
                         f"есть: {', '.join(installed_browsers()) or 'только chromium'}")
    return s


# ──────────────────────────────────────────────────────────────────────────────
# Отдельный постоянный профиль scout
# ──────────────────────────────────────────────────────────────────────────────

def profile_path(browser: str) -> str:
    return profile_dir(BROWSERS[browser]["profile"])


def profile_db(browser: str) -> str:
    """БД кук профиля scout. Она зашифрована ТЕМ ЖЕ ключом Keychain, что и живой
    профиль (ключ у macOS один на браузер, а не на профиль), поэтому cookiesrc
    читает её тем же путём — и видит сессии, обновлённые после входа."""
    return os.path.join(profile_path(browser), "Default", "Cookies")


def profile_seeded(browser: str) -> bool:
    return os.path.exists(profile_db(browser))


def _live_cookie_db(browser: str) -> str:
    from . import cookieimport as ci  # noqa: PLC0415
    return ci._db_path(BROWSERS[browser]["cookies"])


def seed_profile(browser: str, domains: tuple[str, ...] = (),
                 *, force: bool = False) -> tuple[int, int]:
    """Засевает профиль scout куками площадок из живого профиля пользователя.

    Копируется ФАЙЛ БД, дальше из КОПИИ удаляется всё, что не площадка. Значения
    кук при этом не читаются и не расшифровываются — они так и остаются
    зашифрованными ключом из Keychain, который тот же самый бинарь браузера
    достанет сам. Чужие домены (yandex, vk, mail, банки) в профиль не попадают:
    из 3373 кук живого профиля переезжает ~114, профиль весит 0.1 МБ вместо 3.7 ГБ.

    Засев ОДНОРАЗОВЫЙ. Повторный затёр бы куки, которые площадки уже проротировали
    в профиле, старыми из живого браузера — то есть ровно сжёг бы сессию, ради
    сохранения которой всё и делается. Пересев только явным `force`.

    Возвращает (сколько кук осталось, сколько было).
    """
    from . import cookieimport as ci  # noqa: PLC0415

    doms = tuple(domains) or ci.ALLOWED_DOMAINS
    dst = profile_db(browser)
    if os.path.exists(dst) and not force:
        return _count_cookies(dst), _count_cookies(dst)
    src = _live_cookie_db(browser)
    if not os.path.exists(src):
        raise FileNotFoundError(f"нет БД кук {BROWSERS[browser]['title']}: {src}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    # WAL/SHM — там лежат самые свежие записи; без них сессия «вчерашняя».
    for ext in ("-wal", "-shm"):
        if os.path.exists(src + ext):
            shutil.copy2(src + ext, dst + ext)
    con = sqlite3.connect(dst)
    try:
        total = con.execute("SELECT count(*) FROM cookies").fetchone()[0]
        cond = " OR ".join(["host_key = ? OR host_key = ? OR host_key LIKE ?"] * len(doms))
        params: list[str] = []
        for d in doms:
            params += [d, "." + d, "%." + d]
        con.execute(f"DELETE FROM cookies WHERE NOT ({cond})", params)
        con.commit()
        kept = con.execute("SELECT count(*) FROM cookies").fetchone()[0]
        con.execute("VACUUM")  # заодно сливает WAL в основной файл
    finally:
        con.close()
    for ext in ("-wal", "-shm"):
        if os.path.exists(dst + ext):
            os.remove(dst + ext)
    os.chmod(dst, 0o600)
    return kept, total


def _count_cookies(db: str) -> int:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return con.execute("SELECT count(*) FROM cookies").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def prune_profile(browser: str, domains: tuple[str, ...] = ()) -> int:
    """Убирает из профиля куки чужих доменов, наросшие за время работы.

    Профиль их накапливает неизбежно: метрики и виджеты на самих площадках ставят
    yandex.ru, vk.com, mail.ru — после нескольких прогонов было 230 кук по 38
    доменам вместо 114 засеянных. Это тот же allowlist, что и в `.auth/`, просто
    применённый к профилю. Возвращает, сколько удалено."""
    from . import cookieimport as ci  # noqa: PLC0415

    doms = tuple(domains) or ci.ALLOWED_DOMAINS
    db = profile_db(browser)
    if not os.path.exists(db):
        return 0
    before = _count_cookies(db)
    con = sqlite3.connect(db)
    try:
        cond = " OR ".join(["host_key = ? OR host_key = ? OR host_key LIKE ?"] * len(doms))
        params: list[str] = []
        for d in doms:
            params += [d, "." + d, "%." + d]
        con.execute(f"DELETE FROM cookies WHERE NOT ({cond})", params)
        con.commit()
        after = con.execute("SELECT count(*) FROM cookies").fetchone()[0]
    finally:
        con.close()
    return before - after


# ──────────────────────────────────────────────────────────────────────────────
# Лок профиля
# ──────────────────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # чужой процесс, но живой
    return True


def lock_holder(profile: str) -> int | None:
    """PID процесса, который держит профиль, или None.

    Chromium кладёт симлинк `SingletonLock` → `<хост>-<pid>`. Протухший лок (pid
    мёртв) Chromium снимает сам, поэтому такой мы за занятость не считаем."""
    link = os.path.join(profile, "SingletonLock")
    if not os.path.islink(link):
        return None
    try:
        pid = int(os.readlink(link).rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def _check_free(browser: str, profile: str) -> None:
    pid = lock_holder(profile)
    if pid is None:
        return
    if profile != profile_path(browser):
        raise ProfileBusy(
            f"{BROWSERS[browser]['title']} сейчас работает на этом профиле (pid {pid}).\n"
            f"  Профиль {profile} — живой, scout его не трогает.\n"
            f"  Убери SCOUT_BROWSER_PROFILE, и scout возьмёт свой отдельный: "
            f"{profile_path(browser)}")
    raise ProfileBusy(
        f"профиль scout занят другим прогоном (pid {pid}): {profile}\n"
        f"  Это отдельный профиль scout, не твой браузер. Дождись конца прогона "
        f"или закрой то окно — и повтори.")


# ──────────────────────────────────────────────────────────────────────────────
# Запуск настоящего браузера
# ──────────────────────────────────────────────────────────────────────────────

@contextmanager
def real_context(browser: str, *, offscreen: bool = True, seed: bool = True,
                 domains: tuple[str, ...] = (), prune: bool = True):
    """Настоящий браузер пользователя на отдельном постоянном профиле scout.

    headless НЕ используется намеренно: в headless UA становится
    `HeadlessChrome/…`, hirehi отдаёт 403, и лечить это подстановкой чужого UA —
    значит выдавать себя за другой браузер. Вместо этого работает настоящее окно
    настоящего браузера, просто уведённое за край экрана (`offscreen`).
    """
    sync_playwright = _sync_playwright()
    profile = os.environ.get("SCOUT_BROWSER_PROFILE") or profile_path(browser)
    _check_free(browser, profile)
    if seed and not os.path.exists(os.path.join(profile, "Default", "Cookies")):
        try:
            kept, total = seed_profile(browser, domains)
            print(f"  профиль scout засеян: {kept} кук площадок (из {total}; "
                  f"чужие домены не копировались)", file=sys.stderr)
        except (FileNotFoundError, sqlite3.Error) as e:
            print(f"  профиль засеять не вышло ({e}) — браузер пойдёт анонимом",
                  file=sys.stderr)
    args = list(LAUNCH_ARGS)
    if offscreen:
        args += ["--window-position=-3000,-3000", "--window-size=1400,1000"]
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                profile, executable_path=BROWSERS[browser]["bin"], headless=False,
                locale="ru-RU", args=args, ignore_default_args=IGNORE_DEFAULTS,
                viewport=None)
        except Exception as e:  # noqa: BLE001
            # Синглтон отбивает почти мгновенно и выглядит как TargetClosedError —
            # без перевода это «браузер упал», хотя всё в порядке.
            raise ProfileBusy(
                f"{BROWSERS[browser]['title']} не запустился на профиле {profile}: "
                f"{type(e).__name__}: {e}\n"
                f"  Чаще всего это лок профиля. Проверь, не идёт ли другой прогон scout.")
        try:
            yield ctx
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 — закрытие не должно ронять результат
                pass
    # Чистка ПОСЛЕ выхода из sync_playwright: пока браузер жив, он держит БД кук,
    # и править её из-под него нельзя. Профиль зарастает чужими доменами сам —
    # метрики и виджеты на самих площадках ставят yandex.ru, vk.com, mail.ru
    # (после нескольких прогонов было 230 кук по 38 доменам вместо 114 засеянных).
    if prune and profile == profile_path(browser) and lock_holder(profile) is None:
        try:
            prune_profile(browser)
        except (sqlite3.Error, OSError):
            pass  # гигиена не обязана ронять прогон


def top_up_cookies(ctx, domains: tuple[str, ...], cookies_from: str | None = None) -> int:
    """Досыпает в профиль куки площадок, которых в нём ещё нет.

    Нужно ровно для одного случая: пользователь залогинился на площадке уже ПОСЛЕ
    засева профиля. Досыпаем только НЕДОСТАЮЩИЕ (по домену+пути+имени) — иначе
    старая кука из живого браузера затрёт ту, что площадка уже проротировала
    в профиле, и мы своими руками сожжём свежую сессию.

    Возвращает, сколько добавлено.
    """
    if not domains:
        return 0
    from . import cookieimport as ci  # noqa: PLC0415
    from . import cookiesrc  # noqa: PLC0415

    try:
        have = {((c.get("domain") or "").lstrip("."), c.get("path") or "/", c.get("name"))
                for c in ctx.cookies()}
    except Exception:  # noqa: BLE001
        return 0
    try:
        src = cookiesrc.resolve(cookies_from, domains, use_cache=True)
    except Exception:  # noqa: BLE001 — нет кук не повод ронять рендер
        return 0
    fresh = [c for c in src.cookies
             if ((c.get("domain") or "").lstrip("."), c.get("path") or "/",
                 c.get("name")) not in have]
    if not fresh:
        return 0
    try:
        ctx.add_cookies(ci.strip_meta(fresh))
    except Exception:  # noqa: BLE001
        return 0
    return len(fresh)


# ──────────────────────────────────────────────────────────────────────────────
# Нормализация URL
# ──────────────────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> tuple[str, str | None]:
    """(url, пояснение) — правки, без которых залогиненная площадка отдаёт НЕ ТО.

    Хабр Карьера залогиненному пользователю молча подменяет `/vacancies` на
    `?type=suitable` — «3 вакансии, которые подходят именно вам» вместо полного
    списка. Для сборщика это тихая потеря почти всей выдачи, то есть ровно тот
    класс ошибки, против которого написаны правила полноты обхода."""
    from urllib.parse import parse_qs, urlsplit  # noqa: PLC0415

    u = urlsplit(url)
    host = (u.hostname or "").lower()
    if host == "career.habr.com" and u.path.rstrip("/") == "/vacancies" \
            and "type" not in parse_qs(u.query):
        sep = "&" if u.query else "?"
        return (f"{url}{sep}type=all",
                "добавлен ?type=all: залогиненный Хабр иначе показывает "
                "персональную подборку из нескольких вакансий вместо всего списка")
    return url, None


def _storage(url: str, *, session: str | None, session_file: str | None,
             cookies_from: str | None, use_cache: bool):
    """(storage_state для контекста, описание источника, объект CookieSource|None).

    Явный `--session*` — оверрайд. По умолчанию куки читаются из браузера
    пользователя ровно под домены ЭТОГО url: команде про hh.ru незачем трогать
    сессии остальных площадок."""
    from . import cookiesrc  # noqa: PLC0415

    override = resolve_storage(session=session, session_file=session_file)
    if override:
        if not os.path.exists(override):
            raise FetchError(url, f"нет файла сессии {override}")
        return override, override, None
    src = cookiesrc.resolve(cookies_from, cookiesrc.domains_for_url(url),
                            use_cache=use_cache)
    return src.storage_for_playwright(), src.origin, src


def _goto(page, url: str, timeout: float) -> int | None:
    """Навигация, переживающая медленную площадку.

    geekjob.ru отвечает десятками секунд: при 45 с прогон падал «навигация не
    удалась» на живой площадке, которая stdlib-фетчем отдаёт 731 КБ. Таймаут —
    это «мы устали ждать», а не «страницы нет»: если DOM к этому моменту уже
    построен, работаем с ним и говорим, что ждали не до конца. Пустой DOM —
    по-прежнему честное падение.
    """
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
    except Exception as e:  # noqa: BLE001
        try:
            partial = page.content()
        except Exception:  # noqa: BLE001
            partial = ""
        _not_an_error_page(page, url)
        if len(partial) < 2000:
            raise FetchError(url, f"навигация не удалась: {type(e).__name__}: {e}")
        print(f"  страница не догрузилась за {timeout:.0f} с, но DOM уже есть "
              f"({len(partial)} символов) — работаем с ним", file=sys.stderr)
        return None
    _not_an_error_page(page, url)
    return resp.status if resp else None


def _not_an_error_page(page, url: str) -> None:
    """Внутренняя страница ошибки браузера — это НЕ содержимое сайта.

    Ловушка ровно того же класса, что и пустой каркас SPA: `chrome-error://…`
    отдаёт вполне себе DOM на 44 КБ, и без этой проверки «соединение прервано»
    уезжало бы дальше как текст вакансий."""
    try:
        where = page.url or ""
    except Exception:  # noqa: BLE001
        return
    if where.startswith(("chrome-error://", "chrome-extension://")):
        raise FetchError(url, "браузер не смог открыть страницу (соединение "
                              "прервано) — площадка недоступна или отвечает "
                              "слишком долго; это не антибот и не пустая выдача")


def _settle(page, wait: float, timeout: float) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout * 1000)
    except Exception:  # noqa: BLE001 — вечный полинг не наступившего idle
        pass
    page.wait_for_timeout(wait * 1000)


def _blocked_or_html(html: str, status: int | None, final: str, origin: str) -> str:
    marker = looks_blocked(html, status)
    if marker:
        raise BlockedError(final, f"антибот-проверка ({marker}) — и после рендера "
                                  f"(куки: {origin}); проверку проходит человек, "
                                  f"зайди браузером сам")
    return html


# ──────────────────────────────────────────────────────────────────────────────
# Рендер
# ──────────────────────────────────────────────────────────────────────────────

def render_page(url: str, *, session: str | None = None,
                session_file: str | None = None, wait: float = 3.0,
                timeout: float = 60.0, cookies_from: str | None = None,
                use_cache: bool = False, save_session: bool = True,
                browser: str | None = None) -> tuple[str, str]:
    """Возвращает (HTML после исполнения скриптов, финальный URL).

    Кидает BlockedError, если страница и после ожидания осталась антибот-челленджем,
    RenderUnavailable без Playwright, FetchError на прочих ошибках навигации,
    ProfileBusy — если профиль занят.

    Таймаут по умолчанию 60 с, а не 45: geekjob.ru отвечает медленно, и 45 с ему
    честно не хватало — прогон падал «навигация не удалась» на живой площадке,
    которая stdlib-фетчем отдаёт 731 КБ.
    """
    if session and session not in PLATFORMS:
        raise FetchError(url, f"не знаю площадку {session!r}; есть: {', '.join(PLATFORMS)}")
    url, fix = normalize_url(url)
    if fix:
        print(f"  {fix}", file=sys.stderr)

    from . import cookiesrc  # noqa: PLC0415
    domains = cookiesrc.domains_for_url(url)
    name = pick_browser(browser, domains)

    # Настоящий браузер и постоянный профиль — только когда сессию не подменяют
    # явным файлом: с `--session-file` пользователь просил ИМЕННО тот профиль.
    if name != BUNDLED and not session and not session_file:
        return _render_real(url, name, wait=wait, timeout=timeout,
                            cookies_from=cookies_from, domains=domains)
    return _render_bundled(url, session=session, session_file=session_file, wait=wait,
                           timeout=timeout, cookies_from=cookies_from,
                           use_cache=use_cache, save_session=save_session)


def _render_real(url: str, name: str, *, wait: float, timeout: float,
                 cookies_from: str | None, domains: tuple[str, ...]) -> tuple[str, str]:
    with real_context(name, offscreen=True, domains=domains) as ctx:
        added = top_up_cookies(ctx, domains, cookies_from)
        if added:
            print(f"  досыпано {added} кук из живого браузера (в профиле их не было)",
                  file=sys.stderr)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        status = _goto(page, url, timeout)
        _settle(page, wait, timeout)
        html, final = page.content(), page.url
    return _blocked_or_html(html, status, final,
                            f"{BROWSERS[name]['title']}, профиль scout"), final


def _render_bundled(url: str, *, session: str | None, session_file: str | None,
                    wait: float, timeout: float, cookies_from: str | None,
                    use_cache: bool, save_session: bool) -> tuple[str, str]:
    """Запасной путь: встроенный playwright-chromium в headless.

    Здесь UA приходится подставлять, потому что headless-шелл представляется
    `HeadlessChrome/…` и площадки отвечают 403, неотличимым от настоящей стены.
    Это компромисс ради машин без установленного браузера; на машине пользователя
    работает путь выше, где ничего подставлять не нужно.
    """
    sync_playwright = _sync_playwright()
    storage, origin, _src = _storage(url, session=session, session_file=session_file,
                                     cookies_from=cookies_from, use_cache=use_cache)
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        try:
            ctx = br.new_context(storage_state=storage, locale="ru-RU", user_agent=UA)
            page = ctx.new_page()
            status = _goto(page, url, timeout)
            _settle(page, wait, timeout)
            html, final = page.content(), page.url
            if save_session and any(h in url for h in ROTATING_SESSION_HOSTS):
                _persist(ctx)
        finally:
            br.close()
    return _blocked_or_html(html, status, final, origin), final


def _persist(ctx) -> None:
    """Сохраняет продлённую сессию в кэш, ДОПОЛНЯЯ его. Только для bundled-пути:
    у постоянного профиля хранилищем служит он сам, и переливать его в json незачем.

    Три вещи, которые здесь легко сделать неправильно и которые уже ломались:
    (1) писать весь storage_state контекста — тогда в профиль въезжают трекерные
    куки Яндекса, которых allowlist не пускал; (2) писать поверх — тогда пропадают
    куки невизитированных площадок (за один `browse` терялось 11 штук, включая
    антибот-токены hh); (3) писать origins as-is — Playwright отдаёт localStorage
    только визитированных сайтов, и остальные обнуляются."""
    from . import cookieimport as ci  # noqa: PLC0415

    try:
        state = ci.filter_state(ctx.storage_state())
    except Exception:  # noqa: BLE001 — сохранение сессии не должно ронять чтение
        return
    if not state["cookies"]:
        # Анонимный контекст не имеет права вытеснить рабочий профиль.
        return
    base = ci.load_state(BROWSER_STATE)
    ci.write_state(ci.merge_cookies(base.get("cookies", []), state["cookies"]),
                   BROWSER_STATE, origins=state["origins"])


def browse(url: str, *, keep: bool = False, wait: float = 3.0,
           timeout: float = 60.0, cookies_from: str | None = None,
           use_cache: bool = False, browser: str | None = None) -> tuple[str, str]:
    """Видимое окно с сессией пользователя — для ручного дебага модели и человека.

    Инструмент ЧТЕНИЯ: формы не отправляются, кнопки отклика не жмутся.

    Возвращает (HTML, финальный URL). Кидает RenderUnavailable без Playwright."""
    url, fix = normalize_url(url)
    if fix:
        print(f"  {fix}", file=sys.stderr)
    from . import cookiesrc  # noqa: PLC0415
    domains = cookiesrc.domains_for_url(url)
    name = pick_browser(browser, domains)

    if name != BUNDLED:
        with real_context(name, offscreen=False, domains=domains) as ctx:
            top_up_cookies(ctx, domains, cookies_from)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _goto(page, url, timeout)
            _settle(page, wait, timeout)
            html, final = page.content(), page.url
            if keep:
                _hold()
        return html, final

    sync_playwright = _sync_playwright()
    storage, _origin, _src = _storage(url, session=None, session_file=None,
                                      cookies_from=cookies_from, use_cache=use_cache)
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False)
        try:
            ctx = br.new_context(storage_state=storage, locale="ru-RU", user_agent=UA)
            page = ctx.new_page()
            _goto(page, url, timeout)
            _settle(page, wait, timeout)
            html, final = page.content(), page.url
            if keep:
                _hold()
            _persist(ctx)
        finally:
            br.close()
    return html, final


def _hold() -> None:
    print("  Окно открыто. Смотри/кликай сам (форму отклика не отправляй). "
          "Enter здесь — закрыть и сохранить сессию.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def watch_json(url: str, patterns: tuple[str, ...], *, browser: str | None = None,
               wait: float = 6.0, timeout: float = 60.0,
               domains: tuple[str, ...] = ()) -> dict[str, dict]:
    """Открывает страницу и ПОДСЛУШИВАЕТ ответы, чьи URL содержат `patterns`.

    Наблюдение, а не запрос: сами мы приватных ручек не дёргаем и токенов не
    обновляем. Приложение делает свои запросы само — мы лишь читаем то, что оно
    получило. Ровно так добывается счётчик оставшихся раскрытий контакта у hirehi:
    его отдаёт `/api/limits` под Bearer, а Bearer живёт только в памяти вкладки.

    Возвращает {шаблон: разобранный JSON}. Чего не увидели — того в словаре нет,
    и это честный «не знаю», а не ноль.
    """
    name = pick_browser(browser, domains)
    if name == BUNDLED:
        raise FetchError(url, "подслушивание приватных ручек требует настоящего "
                              "браузера: во встроенном chromium сессии нет")
    seen: dict[str, dict] = {}

    with real_context(name, offscreen=True, domains=domains) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            for pat in patterns:
                if pat in resp.url and pat not in seen:
                    try:
                        seen[pat] = resp.json()
                    except Exception:  # noqa: BLE001 — не JSON, значит не наш
                        pass

        page.on("response", on_response)
        _goto(page, url, timeout)
        _settle(page, wait, timeout)
    return seen
