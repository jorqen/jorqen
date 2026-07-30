"""Сессии площадок: реестр площадок, вход руками и заголовок Cookie для stdlib-слоя.

Куки scout у себя НЕ хранит. Источник по умолчанию — живое чтение браузера
пользователя (см. `cookiesrc.py`), `.auth/browser.json` остался необязательным
кэшем-ускорителем: его удаление не ломает ни одну команду.

Как устроено:

1. Пользователь уже залогинен в своём браузере — этого достаточно, отдельный вход
   ради scout не нужен. `cookie_header()` и браузерные команды читают куки прямо
   оттуда, каждый раз свежие.
2. `login <площадка>` — запасной путь, когда браузера с сессией нет. Открывает
   ВИДИМОЕ окно, входит **человек** своим паролем и своим кодом. Скрипт в этот
   момент ничего не вводит.
3. Сохранённый storage_state фильтруется тем же allowlist, что и импорт: в файл
   не попадают трекерные и паспортные куки, случайно осевшие в контексте.

Границы, которые здесь не двигаются:

* **Логинится только пользователь.** Ни пароля, ни кода из письма, ни magic-link —
  вход по коду из почты это ровно механика захвата аккаунта, и делать её чужими руками
  нельзя независимо от намерений.
* **Куки не уезжают с машины.** `.auth/` в `.gitignore`, в облачную рутину не попадает,
  на сервер не копируется. Сессионная кука — это ПРЕДЪЯВИТЕЛЬСКИЙ доступ: у кого файл,
  тот и вошёл, без пароля и без второго фактора. Именно поэтому облачный сборщик
  ходит только по анонимным источникам.

Playwright нужен ТОЛЬКО для входа, рендера и проверки живости. Нет его — сборщик
работает, просто без авторизованных площадок.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

AUTH_DIR = os.environ.get("SCOUT_AUTH_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".auth"
)

# НЕОБЯЗАТЕЛЬНЫЙ кэш профиля. Не источник правды: источник — браузер пользователя
# (cookiesrc.resolve). Файла может не быть вовсе — ни одна команда от этого не ломается.
BROWSER_STATE = os.path.join(AUTH_DIR, "browser.json")


def profile_dir(name: str) -> str:
    """Каталог ОТДЕЛЬНОГО браузерного профиля scout (`.auth/chrome-profile` и т.п.).

    Живёт здесь, а не в render.py, потому что нужен трём модулям сразу: render
    его запускает, cookiesrc читает из него куки, auth в него логинит. Импорт
    render'а ради одной строки пути завёл бы цикл."""
    return os.path.join(AUTH_DIR, name)


def resolve_storage(session: str | None = None,
                    session_file: str | None = None) -> str | None:
    """Явный оверрайд файлом сессии: `--session-file <путь>` или `--session <площадка>`.

    Возвращает None, когда оверрайда нет: тогда куки берутся из браузера через
    `cookiesrc.resolve`. Раньше здесь молча подставлялся `.auth/browser.json`, и
    анонимный кэш вытеснял живой вход — теперь такого пути нет."""
    if session_file:
        return session_file
    if session:
        return state_path(session)
    return None


def platform_for_url(url: str) -> str | None:
    """Какая площадка реестра отвечает за этот URL (для проверки входа по вёрстке)."""
    host = (url.split("//", 1)[-1].split("/", 1)[0] or "").lower()
    for name, cfg in PLATFORMS.items():
        for d in cfg.get("domains", ()):
            if host == d or host.endswith("." + d):
                return name
    return None


def login_state(platform: str, html: str) -> tuple[str, str]:
    """(состояние, пояснение) по вёрстке страницы: `logged_in` / `anonymous` /
    `unknown`.

    Нужно, чтобы анонимный вид не выглядел успехом. Живой случай: у hirehi
    access-токен живёт 3 часа, и после его истечения страница отдаётся анонимной —
    команда возвращала exit 0 и вакансии без прямых контактов, ничем не намекнув,
    что показывает не то, за чем шли."""
    cfg = PLATFORMS.get(platform) or {}
    alive = [m for m in cfg.get("alive_if", ()) if m in html]
    dead = [m for m in cfg.get("dead_if", ()) if m in html]
    if alive:
        return "logged_in", f"признаки входа: {', '.join(alive[:3])}"
    if dead:
        # Что именно значит анонимный вид — зависит от площадки, и раньше здесь
        # всем одинаково советовалось «обнови сессию заходом browse». Для hirehi
        # это прямая дезинформация: анонимно отдаётся ВСЯ выдача, обновлять нечего.
        if cfg.get("login_optional"):
            extra = (f" Для сбора хватает: анонимно доступно {cfg.get('anon_ok')}. "
                     f"Вход добавил бы {cfg.get('login_gains')}.")
        elif cfg.get("client_side_session"):
            extra = (" Сессия на клиенте (localStorage + короткий токен) — обнови её "
                     "заходом `scout browse`.")
        else:
            extra = ""
        return "anonymous", (f"признаков входа нет, есть {', '.join(dead[:2])} — "
                             f"это АНОНИМНЫЙ вид.{extra}")
    return "unknown", "по вёрстке не понять, залогинены ли мы"


def token_from_cookie(platform: str, value: str) -> tuple[str | None, str]:
    """Значение сессионной куки → (Bearer-токен, пояснение).

    Две площадки кладут в куку РАЗНОЕ, и разбирать это надо честно:

    * shadowhint — сам токен строкой (приложение зеркалит его из localStorage
      кукой на год: `auth_token=<t>; max-age=31536000`). Ротации и refresh-ручки
      нет вовсе, поэтому «сгорающей сессии» здесь не бывает;
    * wantapply — url-encoded JSON `{token, refreshToken, tokenExpires}`. Срок
      лежит в миллисекундах, и по нему видно протухание БЕЗ запроса к серверу.
      Это важно: протухший токен даёт 401 на ручке контактов, что легко принять
      за «у вакансии нет прямой ссылки».

    Обновлять токен мы не пытаемся: refresh — это POST, который ротирует
    единственный живой креденшл пользователя и рвёт его сессию в браузере.
    """
    from urllib.parse import unquote  # noqa: PLC0415

    raw = unquote(value or "").strip()
    if not raw:
        return None, "кука пустая"
    if not raw.startswith("{"):
        return raw, "токен из куки"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "кука не разобралась как JSON"
    token = data.get("token") or data.get("accessToken") or data.get("access_token")
    exp = data.get("tokenExpires") or data.get("expiresAt")
    if exp:
        # Миллисекунды у wantapply; секунды тоже встречаются — различаем по величине.
        ts = float(exp) / 1000.0 if float(exp) > 1e11 else float(exp)
        when = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        if ts < datetime.now(timezone.utc).timestamp():
            return None, f"токен истёк {when:%d.%m.%Y %H:%M} — нужен новый вход"
        return token, f"токен жив до {when:%d.%m.%Y %H:%M}"
    return token, "токен из куки (срок не указан)"


def session_token(platform: str, *, cookies_from: str | None = None) -> tuple[str | None, str]:
    """(Bearer-токен площадки, пояснение) — БЕЗ запуска браузера.

    Работает потому, что обе площадки с Bearer держат его в НЕ-httpOnly куке,
    а куки читаются живьём из браузера пользователя. Никакой POST при этом не
    делается: мы только читаем.
    """
    cfg = PLATFORMS.get(platform) or {}
    pair = cfg.get("session_cookie")
    if not pair:
        return None, f"{platform}: токен не нужен"
    domain, name = pair
    from . import cookiesrc  # noqa: PLC0415

    try:
        # use_cache=False намеренно: токен сессии — самое короткоживущее, что есть.
        # Он появляется в момент входа пользователя и протухает сам по себе, поэтому
        # кэш здесь означает «вижу вчерашний разлогин» — ровно так и случилось
        # 30.07.2026: пользователь вошёл на shadowhint, кука появилась, а сборщик
        # продолжал брать снимок, снятый до входа, и требовал войти ещё раз.
        src = cookiesrc.resolve(cookies_from, (domain,), use_cache=False)
    except Exception as e:  # noqa: BLE001 — нет доступа к кукам не повод падать
        return None, f"куки {domain} прочитать не вышло: {type(e).__name__}: {e}"
    for c in src.cookies:
        if c.get("name") == name:
            return token_from_cookie(platform, c.get("value") or "")
    return None, (f"нет куки {name} на {domain} — это и есть точный признак разлогина "
                  f"(остальные куки домена ставят метрики, входом они не являются)")


def session_probe(platform: str, *, cookies_from: str | None = None) -> tuple[str, str]:
    """(состояние, пояснение) по кукам, без браузера и без сетевых запросов.

    Состояния: `logged_in` / `anonymous` / `not_needed` / `unknown`.
    `not_needed` — отдельно от `logged_in` намеренно: у geekjob и hirehi вход
    ничего (или почти ничего) не даёт, и требовать его — врать про пользу.
    """
    cfg = PLATFORMS.get(platform) or {}
    if cfg.get("login_gains") is None and "login_gains" in cfg:
        return "not_needed", f"вход не нужен: анонимно доступно {cfg.get('anon_ok')}"
    if not cfg.get("session_cookie"):
        return "unknown", "по кукам не понять — нужна страница (auth check)"
    token, why = session_token(platform, cookies_from=cookies_from)
    return ("logged_in" if token else "anonymous"), why


def secure_auth_dir(prune_foreign: bool = True) -> list[str]:
    """Гигиена `.auth/`: права 0600 на всё и чистка чужих доменов из сохранённых
    сессий. Возвращает список сделанного — это показывается, а не делается молча.

    Права: файлы, которые создаёт код, и так 0600, но `gmail.env` (App Password)
    и `telegram.env` (api_hash) заводит руками пользователь, и они приезжают 0644.

    Чужие домены: `auth login` раньше сохранял ВЕСЬ storage_state контекста, и
    в `.auth/hh.json` осели куки yandex.ru, vk.com, mail.ru и рекламных сетей —
    allowlist на этот путь не распространялся. Доступа к Яндекс-аккаунту они не
    дают (паспортных Session_id среди них нет), но это чужой предъявительский
    доступ в файле, которому там не место."""
    from . import cookieimport as ci  # noqa: PLC0415

    fixed: list[str] = []
    if not os.path.isdir(AUTH_DIR):
        return fixed
    for name in sorted(os.listdir(AUTH_DIR)):
        p = os.path.join(AUTH_DIR, name)
        if not os.path.isfile(p):
            continue
        mode = os.stat(p).st_mode & 0o777
        if mode & 0o077:
            os.chmod(p, 0o600)
            fixed.append(f"{name}: права {mode:04o} → 0600")
        if not (prune_foreign and name.endswith(".json")):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(state, dict) or "cookies" not in state:
            continue
        cleaned = ci.filter_state(state)
        dropped = len(state.get("cookies", [])) - len(cleaned["cookies"])
        if dropped:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            os.chmod(p, 0o600)
            fixed.append(f"{name}: убрано {dropped} кук чужих доменов "
                         f"(осталось {len(cleaned['cookies'])} площадочных)")
    return fixed


# Площадки для `auth login --all`: один видимый браузер, вкладка на каждую —
# пользователь проходит по табам и логинится сам, сессии оседают в общий профиль.
LOGIN_TOUR: list[tuple[str, str]] = [
    ("hh", "https://hh.ru/account/login"),
    ("career.habr.com", "https://career.habr.com/users/sign_in"),
    ("hirehi.ru", "https://hirehi.ru/"),
    ("shadowhint.com", "https://shadowhint.com/auth"),
    ("wantapply.com", "https://wantapply.com/login"),
    ("geekjob.ru", "https://geekjob.ru/login"),
    ("getmatch.ru", "https://getmatch.ru/"),
    ("careered.io", "https://careered.io/"),
]

# Площадки, где вход что-то даёт, и признак того, что вход ещё жив.
# Признак — из вёрстки, а не из localStorage-метки: метка привязана к origin,
# а hh редиректит на гео-поддомен, и проба, поставленная на hh.ru, на lipetsk.hh.ru
# не читается. На этом уже один раз построили ложный вывод «сессия слетела».
PLATFORMS: dict[str, dict] = {
    "hh": {
        "login_url": "https://hh.ru/account/login",
        "check_url": "https://hh.ru/applicant/negotiations",
        "alive_if": ["negotiations-item", "applicant/resumes", "Мои резюме", "Отклики"],
        "dead_if": ["Войти в аккаунт", "account/login"],
        "domains": ["hh.ru"],
        "note": "отклики, отказы, приглашения и чаты с рекрутёрами — этого нет больше нигде",
        "anon_ok": "поиск вакансий через открытый API",
        "login_gains": "отклики и переписку с рекрутёрами",
        # hh отдаёт анониму 403 на /applicant/* — страницей на 976 КБ, которую
        # looks_blocked() стеной НЕ считает, и правильно делает: это «не авторизован»,
        # а не антибот. Путать их дорого: одно чинится входом, другое — ничем.
        "http_403_means_anon": True,
    },
    "shadowhint": {
        "login_url": "https://shadowhint.com/auth",
        "check_url": "https://shadowhint.com/profile/tg-vacancies",
        "alive_if": ["Личный кабинет", "tg-vacancies", "Выход"],
        "dead_if": ["/auth", "Войти"],
        "domains": ["shadowhint.com"],
        "note": "~37 000 вакансий из Telegram с полнотекстовым поиском",
        # Признак живой сессии ровно один — кука auth_token. Приложение зеркалит
        # в неё Bearer из localStorage (`max-age=31536000`, не httpOnly), поэтому
        # stdlib-слою браузер не нужен вовсе. Остальные куки домена — _ym_d, _ym_uid
        # (Метрика) и g_state (Google One Tap): по ним «shadowhint есть, 3 куки»
        # выглядело как вход, хотя это разлогин. Считаем домены — врём, считаем
        # эту куку — не врём.
        "session_cookie": ("shadowhint.com", "auth_token"),
        "anon_ok": "ничего: выдача целиком под Bearer, аноним получает 401",
        "login_gains": "всю выдачу — без входа площадка недоступна",
    },
    "habr": {
        "login_url": "https://career.habr.com/users/sign_in",
        "check_url": "https://career.habr.com/",
        "alive_if": ["Мои отклики", "sign_out", "Профиль"],
        "dead_if": ["Войти", "sign_in"],
        "domains": ["career.habr.com", "habr.com"],
        "note": "своя история откликов; поиск работает и анонимно",
    },
    "wantapply": {
        "login_url": "https://wantapply.com/login",
        "check_url": "https://wantapply.com/",
        "alive_if": ["Account", "Logout", "Выход"],
        "dead_if": ["Sign in", "Log in"],
        "domains": ["wantapply.com"],
        "note": "9165 вакансий анонимно с API-хоста; вход нужен ТОЛЬКО за «Apply on "
                "corporate website» — прямой ссылкой в ATS работодателя",
        # Кука не httpOnly и содержит url-encoded JSON {token, refreshToken,
        # tokenExpires}: живость видно по tokenExpires, браузер для проверки не нужен.
        "session_cookie": ("wantapply.com", "auth-token-data"),
        "anon_ok": "весь каталог с полными описаниями — через api.wantapply.com",
        "login_gains": "прямую ссылку в ATS работодателя (ручка contacts)",
        # Каталог собирается анонимно целиком, поэтому разлогин здесь НЕ блокирует
        # сбор — он стоит ровно прямых ссылок в ATS. Смешивать это с shadowhint,
        # где без входа нет ничего, нельзя: тогда «залогинься» перестают читать.
        "login_optional": True,
        # Сам wantapply.com под управляемым Cloudflare-челленджем; сборщик туда
        # не ходит вовсе — ни stdlib, ни рендером. Логинится пользователь руками.
        "cloudflare": True,
    },
    "geekjob": {
        "login_url": "https://geekjob.ru/login",
        "check_url": "https://geekjob.ru/",
        "alive_if": ["Аккаунт", "Резюме", "Выход"],
        "dead_if": ["Войти", "/login"],
        "domains": ["geekjob.ru"],
        "note": "271 свежая вакансия, по Go 7–18; вход не даёт НИЧЕГО",
        # Сверено живьём анонимно и с куками пользователя: documentsCount совпал
        # один в один по всем запросам (go 18/18, golang 7/7, backend 92/92,
        # без запроса 271/271). Поэтому просить вход здесь — врать про пользу.
        "anon_ok": "всё, что есть у площадки",
        "login_gains": None,
    },
    "hirehi": {
        "login_url": "https://hirehi.ru/",
        "check_url": "https://hirehi.ru/vacancies/go,backend",
        "alive_if": ["Мои отклики", "Личный кабинет", "Выход"],
        "dead_if": ["Войти"],
        "domains": ["hirehi.ru"],
        "note": "683 вакансии по go+backend анонимно; вход нужен только за счётчиком "
                "раскрытий прямого контакта",
        # Раньше здесь стояло «залогиненный вид только через render», и рендер
        # ходил headless — а hirehi отдаёт 403 на любой UA со словом HeadlessChrome.
        # Ложная стена: сборщик честно писал «АНТИБОТ» там, где стены нет вовсе.
        # Выдача НЕ требует авторизации: stdlib-GET на /api/search/jobs отдаёт всё.
        "anon_ok": "вся выдача и полные описания (JSON API + ld+json на карточке)",
        "login_gains": "счётчик оставшихся раскрытий прямого контакта и сам контакт",
        "client_side_session": True,
        # Вход НЕОБЯЗАТЕЛЕН: анонимного пути хватает на весь сбор. Поэтому hirehi
        # не попадает в список «залогинься» — гонять человека за тем, что и так
        # работает, значит обесценить весь этот список.
        "login_optional": True,
        # refresh-токен ОДИН на браузер пользователя и на любой наш заход: POST
        # /api/auth/refresh ротирует его, и у того, кто обновил не последним, кука
        # мгновенно протухает. Поэтому мы этот POST не делаем НИКОГДА, а сессию
        # держим на постоянном профиле scout, где ротация оседает сама.
        "rotating_refresh": True,
    },
}


def state_path(platform: str) -> str:
    return os.path.join(AUTH_DIR, f"{platform}.json")


def have(platform: str) -> bool:
    return os.path.exists(state_path(platform))


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        print(
            "Нужен Playwright — он используется только для входа и проверки живости.\n"
            "  pip install playwright && playwright install chromium\n\n"
            "Без него сборщик работает, просто без авторизованных площадок "
            "(hh-отклики, shadowhint, wantapply).",
            file=sys.stderr,
        )
        raise SystemExit(3)
    return sync_playwright


def _page_state(page, platform: str) -> tuple[str, str]:
    """Состояние входа по УЖЕ открытой странице."""
    try:
        return login_state(platform, page.content())
    except Exception as e:  # noqa: BLE001
        return "unknown", f"страницу не прочитать: {type(e).__name__}: {e}"


def login(platform: str, *, browser: str | None = None) -> int:
    """Открывает НАСТОЯЩИЙ браузер пользователя на странице площадки.

    Порядок именно такой и он важен:

    1. если по куке видно, что вход уже жив, — говорим об этом и НЕ открываем окно;
    2. иначе открываем рабочую страницу площадки и смотрим на неё; вошёл — тоже
       ничего не делаем;
    3. и только если аноним — открываем форму входа и ждём человека.

    Пароль, код и капчу вводит ТОЛЬКО пользователь. Скрипт в этот момент не
    печатает ни одного символа в поля и ничего не отправляет.

    Сессия оседает в постоянном профиле scout — не в разовом storage_state.
    Именно из-за разового профиля ротационные площадки и «протухали за три часа»:
    сервер выдавал новый refresh-токен, контекст выбрасывался, и жечь оказывалось
    нечего с обеих сторон.
    """
    if platform not in PLATFORMS:
        print(f"не знаю площадку {platform!r}; есть: {', '.join(PLATFORMS)}", file=sys.stderr)
        return 2
    cfg = PLATFORMS[platform]

    if cfg.get("login_gains") is None and "login_gains" in cfg:
        print(f"{platform}: вход не нужен — анонимно доступно {cfg.get('anon_ok')}.")
        print(f"  {cfg['note']}")
        return 0

    state, why = session_probe(platform)
    if state == "logged_in":
        print(f"{platform}: уже залогинен — делать ничего не надо ({why}).")
        return 0

    from .render import (BUNDLED, ProfileBusy, RenderUnavailable,  # noqa: PLC0415
                         pick_browser, real_context)

    os.makedirs(AUTH_DIR, exist_ok=True)
    domains = tuple(cfg.get("domains") or (platform,))
    try:
        name = pick_browser(browser, domains)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if name == BUNDLED:
        return _login_bundled(platform, cfg)

    try:
        with real_context(name, offscreen=False, domains=domains) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                st, why = _page_state(page, platform)
            except Exception as e:  # noqa: BLE001 — площадка могла не открыться
                st, why = "unknown", f"{type(e).__name__}: {e}"
            if st == "logged_in":
                print(f"{platform}: уже залогинен — делать ничего не надо ({why}).")
                return 0

            page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=60000)
            print("=" * 72)
            print(f"  Вход на {platform}: {cfg['login_url']}")
            print(f"  {cfg['note']}")
            if cfg.get("login_gains"):
                print(f"  Вход даёт: {cfg['login_gains']}")
            if cfg.get("cloudflare"):
                print("  У площадки проверка Cloudflare — проходишь её ты, скрипт "
                      "её не трогает.")
            print("=" * 72)
            print("  Войди в открывшемся окне САМ — пароль и код вводишь только ты.")
            print(f"  Это ОТДЕЛЬНЫЙ профиль scout, твой обычный браузер не затронут;")
            print("  сессия здесь дальше продлевается сама.")
            print("=" * 72)
            try:
                input("  Enter, когда вошёл: ")
            except (EOFError, KeyboardInterrupt):
                print("\nотменено", file=sys.stderr)
                return 1
            page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            st, why = _page_state(page, platform)
    except ProfileBusy as e:
        print(str(e), file=sys.stderr)
        return 4
    except RenderUnavailable as e:
        print(str(e), file=sys.stderr)
        return 3

    print(f"\n{platform}: {st} — {why}")
    print("Профиль в .gitignore, с машины не уезжает.")
    return 0 if st == "logged_in" else 1


def _login_bundled(platform: str, cfg: dict) -> int:
    """Запасной вход через встроенный chromium — для машин без своего браузера."""
    sync_playwright = _require_playwright()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=state_path(platform) if have(platform) else None,
            locale="ru-RU",
        )
        page = ctx.new_page()
        page.goto(cfg["login_url"], wait_until="domcontentloaded")
        print("=" * 72)
        print(f"  Вход на {platform}: {cfg['login_url']}")
        print(f"  {cfg['note']}")
        print("=" * 72)
        print("  Войди в открывшемся окне САМ — пароль и код вводишь только ты.")
        print("  Когда профиль загрузится, вернись сюда и нажми Enter.")
        print("=" * 72)
        try:
            input("  Enter, когда вошёл: ")
        except (EOFError, KeyboardInterrupt):
            browser.close()
            print("\nотменено", file=sys.stderr)
            return 1
        state = ctx.storage_state()
        browser.close()

    n_all = len(state.get("cookies", []))
    save_filtered(state, state_path(platform), domains=tuple(cfg.get("domains") or ()))
    n_kept = len(json.load(open(state_path(platform), encoding="utf-8"))["cookies"])
    print(f"\nСессия сохранена: {state_path(platform)} — кук {n_kept} "
          f"(из {n_all}; остальное — чужие домены, они отброшены)")
    print("Файл в .gitignore, права 0600, с машины не уезжает.")
    return 0


def save_filtered(state: dict, path: str, domains: tuple[str, ...] = ()) -> None:
    """Сохраняет storage_state, оставив ТОЛЬКО домены площадок.

    Живой браузерный контекст накапливает всё подряд: после `auth login hh`
    в файле оказывались куки yandex.ru, mc.yandex.ru, vk.com, mail.ru и рекламных
    сетей — allowlist на этот путь просто не распространялся. Теперь один и тот же
    фильтр стоит и на импорте, и на входе."""
    from . import cookieimport as ci  # noqa: PLC0415 — цикл импорта

    doms = domains or ci.ALLOWED_DOMAINS
    filtered = ci.filter_state(state, doms)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def login_all() -> int:
    """Один видимый браузер со вкладкой на каждую площадку из LOGIN_TOUR.

    Пользователь проходит по табам и логинится сам — паролей и кодов скрипт
    не вводит. На выходе сохраняется общий `.auth/browser.json`. Это же fallback,
    когда `auth import` не смог достать куки из браузера (Keychain не поддался)."""
    sync_playwright = _require_playwright()
    os.makedirs(AUTH_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=BROWSER_STATE if os.path.exists(BROWSER_STATE) else None,
            locale="ru-RU")
        print("=" * 72)
        print("  Вход разом на все площадки — по вкладке на каждую:")
        for name, url in LOGIN_TOUR:
            try:
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                print(f"    • {name:<18} {url}")
            except Exception as e:  # noqa: BLE001 — одна недоступная площадка не рвёт тур
                print(f"    • {name:<18} НЕ ОТКРЫЛАСЬ: {type(e).__name__}")
        print("=" * 72)
        print("  Залогинься в каждой вкладке САМ — пароль и код вводишь только ты.")
        print("  Когда прошёл по всем, вернись сюда и нажми Enter.")
        print("=" * 72)
        try:
            input("  Enter, когда вошёл везде: ")
        except (EOFError, KeyboardInterrupt):
            browser.close()
            print("\nотменено", file=sys.stderr)
            return 1
        state = ctx.storage_state()
        browser.close()
    from . import cookieimport as ci  # noqa: PLC0415

    filtered = ci.filter_state(state)
    base = ci.load_state(BROWSER_STATE)
    ci.write_state(ci.merge_cookies(base.get("cookies", []), filtered["cookies"]),
                   BROWSER_STATE, origins=filtered["origins"])
    print(f"\nПрофиль-кэш сохранён: {BROWSER_STATE} — кук {len(filtered['cookies'])} "
          f"(из {len(state.get('cookies', []))}; чужие домены отброшены).")
    print("Это кэш-ускоритель, не источник правды: обычно куки читаются из браузера.")
    return 0


def check(platforms: list[str] | None = None, cookies_from: str | None = None,
          *, browser: str | None = None) -> int:
    """Живость сессий: сначала дёшево по куке, браузер — только для остальных.

    Три класса ответа, и смешивать их нельзя:

    * `вход не нужен` — площадка отдаёт всё анонимно (geekjob сверен один в один:
      go 18/18, golang 7/7, backend 92/92). Это НЕ «сессия истекла» и в список
      «залогинься» такая площадка не попадает;
    * `жива` — есть сессионная кука (и, если срок в ней указан, он не вышел);
    * `ИСТЕКЛА` — куки нет или срок вышел; вот это и требует захода руками.

    Раньше здесь всё считалось третьим классом, и `auth check` звал логиниться
    туда, где вход бесполезен, и молчал про то, что у wantapply токен протух
    четыре дня назад.
    """
    names = platforms or list(PLATFORMS)
    need_page: list[str] = []
    anon: list[str] = []

    for name in names:
        st, why = session_probe(name, cookies_from=cookies_from)
        if st == "not_needed":
            print(f"  {name:<12} вход не нужен  {why}")
        elif st == "logged_in":
            print(f"  {name:<12} жива           {why}")
        elif st == "anonymous":
            label = "аноним" if PLATFORMS[name].get("login_optional") else "ИСТЕКЛА"
            print(f"  {name:<12} {label:<14} {why}")
            anon.append(name)
        else:
            need_page.append(name)

    if need_page:
        from .render import (BUNDLED, ProfileBusy, RenderUnavailable,  # noqa: PLC0415
                             pick_browser, real_context)
        try:
            pick = pick_browser(browser)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if pick == BUNDLED:
            anon += _check_bundled(need_page, cookies_from)
        else:
            try:
                anon += _check_real(need_page, pick, cookies_from)
            except ProfileBusy as e:
                print(str(e), file=sys.stderr)
                return 4
            except RenderUnavailable as e:
                print(str(e), file=sys.stderr)
                return 3

    # Два РАЗНЫХ списка. Смешивать их — значит обесценить оба: если «залогинься»
    # выводится и для площадки, которая и так отдаёт всё, человек перестаёт читать
    # этот список целиком и пропускает ту единственную, где вход действительно нужен.
    blocking = [n for n in anon if not PLATFORMS[n].get("login_optional")]
    optional = [n for n in anon if PLATFORMS[n].get("login_optional")]
    if blocking:
        print(f"\nБЕЗ ВХОДА НЕ РАБОТАЮТ: {', '.join(blocking)}")
        for n in blocking:
            print(f"  {n}: без входа — {PLATFORMS[n].get('anon_ok')}")
        print("  python3 -m scripts.scout auth login <площадка> — откроется твой "
              "браузер на нужной странице.")
        print("  Вход делает пользователь: пароль, код и капчу скрипт не вводит.")
    if optional:
        print(f"\nРаботают анонимно, вход только добавил бы: {', '.join(optional)}")
        for n in optional:
            print(f"  {n}: собирается и так; вход дал бы {PLATFORMS[n].get('login_gains')}")
    return 1 if blocking else 0


def _label(platform: str, state: str) -> str:
    """Подпись состояния. «Аноним» и «ИСТЕКЛА» — разные новости: первое значит
    «собираем и так», второе «площадка потеряна до захода руками»."""
    if state == "logged_in":
        return "жива"
    if state != "anonymous":
        return "не понять"
    return "аноним" if PLATFORMS.get(platform, {}).get("login_optional") else "ИСТЕКЛА"


def _check_real(names: list[str], browser: str, cookies_from: str | None) -> list[str]:
    """Проверка страницей в настоящем браузере на профиле scout."""
    from .render import BROWSERS, real_context, top_up_cookies  # noqa: PLC0415

    dead: list[str] = []
    doms = tuple({d for n in names for d in (PLATFORMS[n].get("domains") or (n,))})
    with real_context(browser, offscreen=True, domains=doms) as ctx:
        top_up_cookies(ctx, doms, cookies_from)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for name in names:
            cfg = PLATFORMS[name]
            try:
                page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                st, why = _page_state(page, name)
            except Exception as e:  # noqa: BLE001
                st, why = "unknown", f"{type(e).__name__}: {e}"
            label = _label(name, st)
            print(f"  {name:<12} {label:<14} {why} [{BROWSERS[browser]['title']}]")
            if st != "logged_in":
                dead.append(name)
    return dead


def _check_bundled(names: list[str], cookies_from: str | None) -> list[str]:
    """Запасная проверка встроенным chromium — для машин без своего браузера."""
    from . import cookiesrc  # noqa: PLC0415
    from .net import UA  # noqa: PLC0415

    sync_playwright = _require_playwright()
    dead: list[str] = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        for name in names:
            cfg = PLATFORMS[name]
            doms = tuple(cfg.get("domains") or (name,))
            if have(name):
                storage: dict | str = state_path(name)
                where = f".auth/{name}.json"
            else:
                src = cookiesrc.resolve(cookies_from, doms, use_cache=True)
                if not src.cookies:
                    print(f"  {name:<12} КУК НЕТ        ни в браузере, ни в кэше")
                    dead.append(name)
                    continue
                storage, where = src.storage_for_playwright(), src.origin
            ctx = br.new_context(storage_state=storage, locale="ru-RU", user_agent=UA)
            page = ctx.new_page()
            try:
                page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                st, why = login_state(name, page.content())
            except Exception as e:  # noqa: BLE001
                st, why = "unknown", f"{type(e).__name__}: {e}"
            finally:
                ctx.close()
            label = _label(name, st)
            print(f"  {name:<12} {label:<14} {why} (куки: {where})")
            if st != "logged_in":
                dead.append(name)
        br.close()
    return dead


def _header_from_state(state: dict, domains: list[str] | tuple[str, ...]) -> str | None:
    pairs, seen = [], set()
    now = datetime.now(timezone.utc).timestamp()
    for c in state.get("cookies", []):
        dom = (c.get("domain") or "").lstrip(".")
        if domains and not any(dom == d or dom.endswith("." + d) for d in domains):
            continue
        exp = c.get("expires", -1)
        if exp and exp > 0 and exp < now:
            continue  # протухшая кука только мешает
        name = c.get("name")
        if name and name not in seen:
            seen.add(name)
            pairs.append(f"{name}={c.get('value', '')}")
    return "; ".join(pairs) or None


def cookie_header(platform: str, *, cookies_from: str | None = None) -> str | None:
    """Заголовок `Cookie` для площадки — для stdlib-сборщика, без всякого браузера.

    Порядок источников тот же, что у браузерного слоя, и это принципиально: раньше
    здесь читался ТОЛЬКО `.auth/<площадка>.json`, поэтому `collect` и `raw` ходили
    на shadowhint/wantapply/geekjob/habr анонимом, хотя пользователь был там залогинен
    и куки лежали в общем профиле. Симптом был самый дорогой: не ошибка, а пустая
    выдача, неотличимая от «вакансий нет».

    1. Явный `.auth/<площадка>.json` (его заводит `auth login <площадка>`).
    2. Живое чтение браузера по доменам этой площадки — и только этой.
    3. Кэш `.auth/browser.json`, если живьём не вышло.
    """
    domains = PLATFORMS.get(platform, {}).get("domains", []) or [platform]
    if have(platform):
        try:
            with open(state_path(platform), encoding="utf-8") as f:
                got = _header_from_state(json.load(f), domains)
            if got:
                return got
        except (json.JSONDecodeError, OSError):
            pass
    try:
        from . import cookiesrc  # noqa: PLC0415 — ленивый импорт, тут цикл
        src = cookiesrc.resolve(cookies_from, tuple(domains), use_cache=True)
        return src.cookie_header()
    except Exception:  # noqa: BLE001 — нет кук не повод ронять сбор, просто аноним
        return None


def status(cookies_from: str | None = None) -> int:
    """Откуда СЕЙЧАС возьмутся куки по каждой площадке — без запуска браузера.

    Раньше здесь смотрелись только `.auth/<площадка>.json`, и команда писала
    «habr нет» при живом входе в браузере. Теперь считается ровно то, что и будет
    использовано: живое чтение браузера, потом кэш."""
    from . import cookieimport as ci  # noqa: PLC0415
    from . import cookiesrc  # noqa: PLC0415

    print(f"Каталог `.auth/`: {AUTH_DIR}  (только telegram.session/telegram.env "
          f"и gmail.env; браузерные куки — из браузера)\n")

    print("## Браузеры (живой источник кук, БЕЗ Keychain — считаются только домены)")
    any_browser = False
    for b in cookiesrc.BROWSER_NAMES:
        path = ci._db_path(b)
        if not os.path.exists(path):
            print(f"  {b:<8} нет БД кук ({path})")
            continue
        any_browser = True
        cov = cookiesrc.coverage_without_keychain(b, ci.ALLOWED_DOMAINS)
        age = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
        print(f"  {b:<8} {sum(cov.values()):>4} кук площадок по {len(cov)} доменам, "
              f"БД обновлена {age:.0f} ч назад")
    if not any_browser:
        print("  ни одной БД кук не найдено — куки можно передать файлом: "
              "--cookies-from <экспорт.json>")

    print("\n## Площадки: чем будет закрыт вход")
    for name, cfg in PLATFORMS.items():
        doms = tuple(cfg.get("domains") or (name,))
        own = have(name)
        pick, per = cookiesrc.choose_browser(doms)
        got = per.get(pick, {}) if pick else {}
        where = []
        if own:
            where.append(f".auth/{name}.json")
        if got:
            where.append(f"{pick} ({sum(got.values())} кук)")
        cached = cookiesrc.cache_state()
        if cached:
            n = sum(1 for c in cached.get("cookies", [])
                    if any((c.get("domain") or "").lstrip(".").endswith(d) or
                           (c.get("domain") or "").lstrip(".") == d for d in doms))
            if n:
                where.append(f"кэш ({n})")
        # Число кук домена НЕ означает вход и раньше прямо вводило в заблуждение:
        # «shadowhint есть, yandex (3 кук)» — это _ym_d, _ym_uid и g_state, то есть
        # Метрика и Google One Tap при полном разлогине. Где вход определяется по
        # конкретной куке, показываем вердикт, а не арифметику доменов.
        state, why = session_probe(name, cookies_from=cookies_from)
        verdict = {"logged_in": "ВХОД ЖИВ", "anonymous": "аноним",
                   "not_needed": "вход не нужен"}.get(state, "")
        print(f"  {name:<12} {verdict or ('есть' if where else 'НЕТ')!s:<14} "
              + (", ".join(where) if where else f"— {cfg['note'][:48]}"))
        if state in ("anonymous", "not_needed"):
            print(f"  {'':<12} {'':<14} {why[:96]}")

    cache = cookiesrc.cache_state()
    print(f"\n## Кэш {BROWSER_STATE}")
    if cache is None:
        print("  нет — и это нормально: он необязателен, куки берутся из браузера")
    else:
        age = (datetime.now().timestamp() - os.path.getmtime(BROWSER_STATE)) / 3600
        print(f"  {len(cache.get('cookies', []))} кук, обновлён {age:.0f} ч назад "
              f"(ускорение; используется только с --cache)")

    bad = [n for n in (os.listdir(AUTH_DIR) if os.path.isdir(AUTH_DIR) else [])
           if os.path.isfile(os.path.join(AUTH_DIR, n))
           and os.stat(os.path.join(AUTH_DIR, n)).st_mode & 0o077]
    if bad:
        print(f"\n⚠️  Права шире 0600 у: {', '.join(bad)} — там предъявительские секреты."
              f"\n   Починить: python3 -m scripts.scout auth secure")
    print("\nЖивость проверяется браузером: python3 -m scripts.scout auth check")
    return 0
