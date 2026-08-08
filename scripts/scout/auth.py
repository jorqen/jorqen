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

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

AUTH_DIR = os.environ.get("SCOUT_AUTH_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".auth"
)

# НЕОБЯЗАТЕЛЬНЫЙ кэш профиля. Не источник правды: источник — браузер пользователя
# (cookiesrc.resolve). Файла может не быть вовсе — ни одна команда от этого не ломается.
BROWSER_STATE = os.path.join(AUTH_DIR, "browser.json")

# Начало пояснения, которым session_token сообщает «до кук не добрался».
# Живёт константой, потому что по нему принимается решение (unknown вместо
# anonymous), а решение по подстроке-литералу ломается от любой правки текста.
UNREADABLE = "куки прочитать не вышло"


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
        # 🔴 Срок проверяется и у ГОЛОГО токена, а не только у JSON-обёртки
        # wantapply. Токен shadowhint — обычный JWT, и `exp` в нём читается без
        # единого запроса и без единого секрета.
        #
        # Пока проверки не было, «токен есть» означало «токен годится», и
        # session_token возвращал ПЕРВЫЙ НАЙДЕННЫЙ вместо первого живого:
        # 08.08.2026 он брал из Яндекса JWT, истёкший накануне, при свежем
        # входе, лежащем рядом в `.auth/shadowhint.json`. Площадка отвечала 401,
        # покрытие показывало «НУЖЕН ВХОД», а вход был — просто не тот.
        exp = _jwt_exp(raw)
        if exp is not None:
            when = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone()
            if exp < datetime.now(timezone.utc).timestamp():
                return None, f"токен истёк {when:%d.%m.%Y %H:%M} — нужен новый вход"
            return raw, f"токен жив до {when:%d.%m.%Y %H:%M}"
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


def _jwt_exp(token: str) -> float | None:
    """`exp` из JWT или None, если это не JWT. Ни запросов, ни секретов.

    Подпись НЕ проверяется намеренно: нам не надо доверять токену, надо лишь
    понять, стоит ли его отправлять. Решает всё равно сервер.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = json.loads(base64.urlsafe_b64decode(pad)).get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 — не JWT, не наш формат, битая база64
        return None


def bearer_from_state(platform: str) -> tuple[str | None, str]:
    """(Bearer из localStorage сохранённого storage_state, пояснение).

    Для площадок, где сессия живёт НЕ в куках (careered): куки браузера
    пользователя тут не помогают вовсе, единственный источник — `.auth/
    <площадка>.json`, который заводит `scout auth login <площадка>`. Playwright
    сохраняет localStorage в origins[] по умолчанию — отдельного флага при
    записи storage_state не нужно, а filter_state() origins не выбрасывает.

    Никаких сетевых запросов: только чтение файла. Протухший токен отсюда
    не виден (JWT не разбираем) — его честно назовёт 401 в месте использования.
    """
    cfg = PLATFORMS.get(platform) or {}
    pair = cfg.get("localstorage_token")
    if not pair:
        return None, f"{platform}: localStorage-токен для площадки не описан"
    origin_want, name = pair
    if not have(platform):
        return None, (f"нет .auth/{platform}.json — одноразовый вход: "
                      f"scout auth login {platform}")
    try:
        with open(state_path(platform), encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f".auth/{platform}.json не читается: {type(e).__name__}: {e}"
    for o in state.get("origins", []):
        if (o.get("origin") or "").rstrip("/") != origin_want:
            continue
        for item in o.get("localStorage", []):
            if item.get("name") == name and item.get("value"):
                return item["value"], f"токен из localStorage {origin_want}"
    return None, (f"в .auth/{platform}.json нет localStorage {name!r} — вход не "
                  f"сохранился, повтори: scout auth login {platform}")


def state_cookie(platform: str) -> tuple[str | None, str]:
    """(значение сессионной куки из СОБСТВЕННОЙ сессии scout, пояснение).

    Отдельно от `session_token` намеренно: тот читает браузер пользователя, а
    здесь источник строго `.auth/<площадка>.json`. Разница принципиальная для
    ротационных площадок — у hirehi refresh-токен один на всех, и заход куками
    из живого браузера обесценивает его там. Поэтому у таких площадок вообще нет
    другого честного источника, кроме собственного файла.

    Никакой сети: только чтение файла.
    """
    cfg = PLATFORMS.get(platform) or {}
    pair = cfg.get("state_cookie")
    if not pair:
        return None, f"{platform}: собственная сессия scout для площадки не описана"
    domain, name = pair
    if not have(platform):
        return None, (f"нет .auth/{platform}.json — собственной сессии scout нет. "
                      f"Разовый вход: scout auth login {platform}")
    try:
        with open(state_path(platform), encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, f".auth/{platform}.json не читается: {type(e).__name__}: {e}"
    for c in state.get("cookies", []):
        if c.get("name") == name and (c.get("domain") or "").lstrip(".").endswith(domain):
            if c.get("value"):
                # Формулировка ровно про то, что проверено. Файл говорит, что
                # токен ЕСТЬ, и молчит о том, принимают ли его: у hirehi срок
                # внутрь куки не положен, а анонимный вид отдаётся с кодом 200.
                # Сказать здесь «сессия жива» значит завести ложное спокойствие —
                # ровно так и было 06.08.2026: файл на месте, заход анонимный.
                return str(c["value"]), (f"токен {name} на месте (.auth/{platform}.json); "
                                         f"принимают ли его — покажет заход")
    return None, (f"в .auth/{platform}.json нет куки {name} — вход не сохранился, "
                  f"повтори: scout auth login {platform}")


def save_session_cookie(platform: str, domain: str, name: str, value: str) -> None:
    """Кладёт свежую СЕССИОННУЮ куку в `.auth/<площадка>.json`, не трогая остальное.

    Зеркало `save_localstorage_token` для площадок, чья сессия живёт в куке
    (shadowhint). Файл здесь — тоже слепок, а не дом сессии: жить она обязана
    в постоянном профиле, где продлевается сама, а слепок нужен stdlib-слою,
    который в профиль ходить не умеет.

    Остальные куки домена сохраняются: среди них `g_state`, по которому
    площадка узнаёт сессию Google, и выбрасывать их ради одной строки нельзя.
    """
    path = state_path(platform)
    state: dict = {"cookies": [], "origins": []}
    if have(platform):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            pass  # битый слепок — перезаписываем целиком, это не потеря сессии
    cookies = state.setdefault("cookies", [])
    state.setdefault("origins", [])
    for c in cookies:
        if c.get("name") == name and str(c.get("domain") or "").lstrip(".") == domain:
            c["value"] = value
            break
    else:
        cookies.append({"name": name, "value": value, "domain": domain,
                        "path": "/", "secure": False, "httpOnly": False,
                        "sameSite": "Lax"})
    os.makedirs(AUTH_DIR, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def save_localstorage_token(platform: str, origin: str, name: str, token: str) -> None:
    """Кладёт свежий localStorage-токен в `.auth/<площадка>.json`, не трогая куки.

    Нужно для площадок с сессией на клиенте (careered): жить она обязана в
    постоянном профиле, где продлевается сама, а файл — только слепок для
    stdlib-слоя. Слепок и обновляем, целиком storage_state ради одной строки
    не переписывая: в файле могут лежать куки, которых в профиле уже нет."""
    path = state_path(platform)
    state: dict = {"cookies": [], "origins": []}
    if have(platform):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        except (json.JSONDecodeError, OSError):
            pass  # битый слепок — перезаписываем целиком, это не потеря сессии
    state.setdefault("cookies", [])
    origins = state.setdefault("origins", [])
    want = origin.rstrip("/")
    for o in origins:
        if (o.get("origin") or "").rstrip("/") != want:
            continue
        items = o.setdefault("localStorage", [])
        for item in items:
            if item.get("name") == name:
                item["value"] = token
                break
        else:
            items.append({"name": name, "value": token})
        break
    else:
        origins.append({"origin": want,
                        "localStorage": [{"name": name, "value": token}]})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


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
        # Отдельная формулировка, а не просто «токена нет»: по ней session_probe
        # отличает «не смог прочитать» от «точно вышел». Свалить их в одно значит
        # объявлять разлогин каждый раз, когда браузер держит свою базу кук
        # залоченной, — и приучить не верить предупреждению вовсе.
        #
        # Но НЕ выходим сразу: нечитаемым может быть один браузер, а сессия
        # лежать в другом. Раньше здесь стоял return, и волна при открытом
        # Chrome собирала shadowhint анонимом (401, ноль вакансий), молча — в
        # предупреждение `unknown` не попадает по построению. Живая сессия в
        # Яндексе при этом лежала рядом и не спрашивалась.
        src, unreadable = None, f"{UNREADABLE}: {domain}: {type(e).__name__}: {e}"
    else:
        unreadable = None
    if src is not None:
        token, why = _token_in(src, platform, name, domain)
        if token or cookies_from not in (None, "", "auto"):
            # Явный источник спрашивали — его и отвечаем, не подменяя другим:
            # `--cookies-from yandex` это вопрос про Яндекс, а не «найди хоть где».
            return token, why
    else:
        token, why = None, unreadable
        if cookies_from not in (None, "", "auto"):
            return token, why

    # Живой сессии в выбранном браузере нет — спрашиваем остальные ПОИМЁННО.
    # `auto` выбирает один браузер по покрытию доменов и свежести БД, и про
    # СРОК токена внутри куки не знает ничего: 07.08.2026 он выбрал Яндекс с
    # токеном wantapply, истёкшим 31.07, при живом до 08.08 в Chrome. Цена
    # ошибки — прямые ссылки в ATS, потерянные при работающем входе.
    from . import cookiesrc  # noqa: PLC0415

    for other in getattr(cookiesrc, "BROWSER_NAMES", ()):
        try:
            alt = cookiesrc.resolve(other, (domain,), use_cache=False)
        except Exception:  # noqa: BLE001 — нет такого браузера или нет доступа
            continue
        alt_token, alt_why = _token_in(alt, platform, name, domain)
        if alt_token:
            return alt_token, f"{alt_why} — источник {other}, а не выбранный auto"

    # ПОСЛЕДНИМ — сохранённая сессия из `.auth/<площадка>.json`. Порядок именно
    # такой и переставлять его нельзя: живая кука в браузере всегда свежее
    # снимка, а снимок мог быть снят до разлогина.
    #
    # Зато в облаке браузеров нет вообще, и до этой ветки доходит каждый раз:
    # именно сюда `hydrate_from_env` кладёт то, что приехало секретом. Пока
    # ветки не было, облачный вход был невозможен по построению — файл ложился,
    # а читатель смотрел только в браузеры и честно отвечал «нет куки».
    saved = _token_in_state(platform, name)
    if saved:
        return token_from_cookie(platform, saved)[0], \
            f"кука {name} из сохранённой сессии {os.path.basename(state_path(platform))}"
    return token, why


def _token_in_state(platform: str, name: str) -> str | None:
    """Значение куки `name` в сохранённом storage_state площадки."""
    path = state_path(platform)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    for c in state.get("cookies") or []:
        if c.get("name") == name and c.get("value"):
            return str(c["value"])
    return None


def _token_in(src, platform: str, name: str, domain: str) -> tuple[str | None, str]:
    """Разбор сессионной куки в одном конкретном источнике."""
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
    if cfg.get("localstorage_token"):
        # Сессия в localStorage (careered): по кукам её не видно в принципе,
        # смотрим сохранённый storage_state — тоже без браузера и без сети.
        token, why = bearer_from_state(platform)
        return ("logged_in" if token else "anonymous"), why
    if cfg.get("state_cookie"):
        # Ротационные площадки (hirehi): единственный честный источник — своя
        # сессия scout. Куки живого браузера сюда не годятся не потому, что их
        # трудно прочитать, а потому, что чтение с последующим заходом сожгло бы
        # токен у пользователя.
        token, why = state_cookie(platform)
        return ("logged_in" if token else "anonymous"), why
    if not cfg.get("session_cookie"):
        return "unknown", "по кукам не понять — нужна страница (auth check)"
    token, why = session_token(platform, cookies_from=cookies_from)
    if token:
        return "logged_in", why
    return ("unknown" if why.startswith(UNREADABLE) else "anonymous"), why


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
    "careered": {
        "login_url": "https://careered.io/",
        "check_url": "https://careered.io/",
        # careered.io — SPA-шелл на 547 байт: анонимный HTML и залогиненный
        # неотличимы до исполнения скриптов, поэтому уверенно назвать маркеры
        # ВХОДА по вёрстке нельзя — alive_if пуст сознательно, чтобы login_state
        # не объявлял вход там, где мы его не видели. «Sign in» на отрендеренной
        # странице — честный признак анонима. Настоящая проверка живости — токен
        # в localStorage сохранённого storage_state, см. bearer_from_state().
        "alive_if": [],
        "dead_if": ["Sign in"],
        "domains": ["careered.io"],
        "note": "контакты вакансий раскрываются только залогиненному "
                "(бесплатная регистрация); лента и описания — анонимно",
        "client_side_session": True,
        # Сессия НЕ в куках: после POST /api/users/sign-in access_token ложится
        # в localStorage и шлётся заголовком Authorization: Bearer. Куки браузера
        # пользователя mode='full' поэтому не открывают — работает только
        # storage_state от `scout auth login careered` (Playwright кладёт
        # localStorage в origins[] сам, отдельного флага не нужно).
        "localstorage_token": ("https://careered.io", "access_token"),
        "anon_ok": "вся лента и полные описания (контакт в mode=preview зарезан до '#')",
        "login_gains": "живой контакт работодателя в деталке (mode=full, t.me/почта)",
        # Сбор работает анонимно целиком — вход стоит ровно контактов в деталке.
        "login_optional": True,
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
        # мгновенно протухает. Поэтому мы этот POST не делаем НИКОГДА сами —
        # продление идёт клиентом площадки на своей же странице, см.
        # authrefresh.renew_hirehi, а ротация оседает в собственной сессии scout.
        "rotating_refresh": True,
        # Живость видна офлайн по своему же файлу — и ТОЛЬКО по нему: куки браузера
        # владельца для hirehi не читаются нигде, это тот самый заход, что жжёт токен.
        "state_cookie": ("hirehi.ru", "hirehi-refresh-token"),
        # Единственный надёжный признак входа: вёрстка врёт в обе стороны —
        # «Войти» есть у залогиненного (модалка рендерится всегда), а «Мои
        # отклики» нет и у него (рисуется клиентом после гидрации). Замер
        # 07.08.2026: 200 нашей сессии против 401 анониму. См. api_state.
        "alive_api": "/api/favorites",
    },
}


def state_path(platform: str) -> str:
    return os.path.join(AUTH_DIR, f"{platform}.json")


def have(platform: str) -> bool:
    return os.path.exists(state_path(platform))


# ── Сессии из окружения: единственный способ дать вход облачной рутине ───────
#
# В облаке чекаут публичного репозитория и больше ничего. `.auth/` туда не
# уезжает и не уедет (инвариант 4 — про git), поэтому авторизованные площадки
# были там выключены по построению.
#
# 🔴 Это НЕ то же самое, что ключ площадки. Ключ даёт чтение публичного
# каталога; сохранённая сессия — ПРЕДЪЯВИТЕЛЬСКИЙ ДОСТУП К АККАУНТУ. Кто
# получил файл, тот вошёл как владелец. Поэтому:
#
#   * значение кладётся в секреты окружения, а НЕ в промпт рутины и не в git;
#   * `auth export` печатает его РОВНО ОДИН РАЗ в терминал владельца — команда
#     существует, чтобы человек скопировал вывод сам, не показывая его никому;
#   * сессия смертна. Продлить её в облаке нечем: `authrefresh` поднимает
#     браузер, а playwright там нет. Когда сессия умрёт, площадка честно
#     станет «НУЖЕН ВХОД» в строке обхода поста — за этим и нужна та строка.
_ENV_PREFIX = "SCOUT_AUTH_"


def env_var(platform: str) -> str:
    """Имя переменной окружения для сессии площадки."""
    return _ENV_PREFIX + platform.upper().replace("-", "_")


def _drop_expired(cookies: list[dict]) -> list[dict]:
    """Куки с заведомо мёртвым JWT — вон. Остальные как есть.

    Проверяем только то, что читается без сети: `exp` внутри JWT. Непрозрачная
    строка остаётся — «не смогли прочесть срок» это не «просрочено».
    """
    import time  # noqa: PLC0415

    now = time.time()
    out = []
    for c in cookies:
        exp = _jwt_exp(str(c.get("value") or ""))
        if exp is not None and exp < now:
            continue
        out.append(c)
    return out


def _token_origins(platform: str, origins: list[dict]) -> list[dict]:
    """localStorage, урезанный до ключа, который несёт вход. Остальное — вон.

    Замер на живой машине: у hh в localStorage лежит 660 КБ аналитики и
    настроек интерфейса, и секрет окружения из-за них раздувался до 880 тысяч
    символов при полезных семидесяти куках. Площадка сама объявляет свой ключ
    (`localstorage_token` в PLATFORMS) — всё, что не он, к входу отношения не
    имеет и в облако ехать не должно: лишний объём это ещё и лишние данные о
    владельце в чужом хранилище.

    Ключ не объявлен — значит вход у площадки в куках, и localStorage ей не
    нужен вовсе.
    """
    want = (PLATFORMS.get(platform) or {}).get("localstorage_token")
    if not want:
        return []
    origin_url, key = want
    out = []
    for o in origins:
        items = [i for i in (o.get("localStorage") or []) if i.get("name") == key]
        if items and (o.get("origin") or "").rstrip("/") == origin_url.rstrip("/"):
            out.append({"origin": o["origin"], "localStorage": items})
    return out


def export_state(platform: str) -> tuple[str, str] | None:
    """(значение для секрета, откуда взято) или None, если брать нечего.

    🔴 Собирает вход ИЗ ТЕХ ЖЕ ИСТОЧНИКОВ, что и сборщик, а не из одного файла.
    Первая версия читала только `.auth/<площадка>.json` — и не видела ничего,
    потому что живые сессии лежат в куках повседневного браузера: `auth status`
    показывал «ВХОД ЖИВ» для shadowhint, а экспорт возвращал пусто. Экспорт,
    который не видит того, чем работает сборщик, бесполезен по построению.

    Склеиваются оба слоя, и оба нужны: куки дают вход большинству площадок,
    а `origins` (localStorage) — тем, кто держит там Bearer, как careered.

    base64, а не сырой JSON: значение уезжает в поле веб-формы и в оболочку,
    а в куках встречаются кавычки, переводы строк и знаки доллара.
    """
    from . import cookieimport as ci, cookiesrc  # noqa: PLC0415 — тянут браузеры

    cookies: list[dict] = []
    origins: list[dict] = []
    where: list[str] = []

    path = state_path(platform)
    if os.path.exists(path):
        saved = ci.load_state(path)
        cookies = ci.merge_cookies(cookies, _drop_expired(saved.get("cookies") or []))
        origins = ci.merge_origins(origins, saved.get("origins") or [])
        where.append(os.path.basename(path))

    try:
        # use_cache=False намеренно: кэш `.auth/browser.json` покрывает домены
        # и потому ПОБЕЖДАЕТ живое чтение, а ему бывает трое суток. На живой
        # машине из-за этого в экспорт shadowhint не попал `auth_token` — тот
        # самый ключ, ради которого экспорт и делается. Секрет уезжает в облако
        # на недели, свежесть тут важнее скорости.
        src = cookiesrc.resolve("auto", cookiesrc.domains_for_platform(platform),
                                use_cache=False)
        live = _drop_expired(src.state.get("cookies") or [])
        if live:
            # Живое чтение браузера идёт ВТОРЫМ и побеждает: `merge_cookies`
            # накрывает совпадающие по (имя, домен, путь). Обычно это верно —
            # вкладка в браузере свежее слепка.
            #
            # 🔴 Но «живое» не значит «непросроченное», и оба слоя проходят
            # через `_drop_expired` именно поэтому. Живой случай 08.08.2026:
            # в Яндексе лежал auth_token shadowhint, истёкший накануне, а
            # свежий вход — в `.auth/shadowhint.json`. Merge накрывал свежий
            # мёртвым по совпадению (имя, домен, путь), и экспорт увозил в
            # облако заведомо нерабочую сессию. Локально при этом всё
            # работало: там `session_token` умеет перебирать источники.
            cookies = ci.merge_cookies(cookies, live)
            origins = ci.merge_origins(origins, src.state.get("origins") or [])
            where.append(src.origin)
    except Exception as e:  # noqa: BLE001 — браузера может не быть вовсе
        where.append(f"браузер не прочитан ({type(e).__name__})")

    # 🔴 Режем по доменам ЭТОЙ площадки, и это не оптимизация размера.
    # Кэш `.auth/browser.json` общий: в нём куки всех площадок сразу. Без
    # фильтра каждый секрет нёс бы вход во все семь — то есть один утёкший
    # секрет отдавал бы все аккаунты, а не один. Заодно значение падает с
    # 50 КБ до килобайта, а у hh — с 900 КБ.
    doms = cookiesrc.domains_for_platform(platform)
    state = ci.filter_state({"cookies": cookies, "origins": origins}, doms)
    state["origins"] = _token_origins(platform, state.get("origins") or [])
    if not state["cookies"] and not state["origins"]:
        return None
    blob = json.dumps(state, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(blob).decode("ascii"), ", ".join(where)


def hydrate_from_env() -> list[str]:
    """Разложить сессии из окружения в AUTH_DIR. Возвращает, какие площадки легли.

    Материализуем в файлы, а не учим каждого читателя смотреть в окружение:
    `state_path` открывают восемь мест, и восемь развилок «файл или переменная»
    разъехались бы. Здесь одна точка входа и один формат на диске.

    Существующий файл НЕ перезаписывается: локально живая сессия свежее любого
    слепка, который когда-то положили в секреты.
    """
    laid: list[str] = []
    for platform in PLATFORMS:
        raw = os.environ.get(env_var(platform))
        if not raw or have(platform):
            continue
        try:
            blob = base64.b64decode(raw, validate=True)
            json.loads(blob.decode("utf-8"))      # мусор в AUTH_DIR не кладём
        except Exception as e:  # noqa: BLE001
            # Молчать нельзя: битый секрет неотличим от «входа нет», и площадка
            # молча выпала бы из обхода с пометкой, которая уводит не туда.
            print(f"{env_var(platform)}: не разобрался ({type(e).__name__}) — "
                  f"ожидается base64 от {platform}.json, площадка останется без "
                  f"входа", file=sys.stderr)
            continue
        os.makedirs(AUTH_DIR, exist_ok=True)
        path = state_path(platform)
        # Права ставятся ДО записи: между open() и chmod() файл существовал бы
        # с правами по умолчанию, а в нём сессия аккаунта.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        laid.append(platform)
    return laid


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


def api_state(page, platform: str) -> tuple[str, str] | None:
    """Состояние входа по ПРИВАТНОЙ РУЧКЕ площадки, а не по вёрстке.

    Заведено после разбора 07.08.2026, который стоил трёх ложных «войди ещё раз»
    подряд: у hirehi слово «Войти» лежит в разметке ВСЕГДА (форма входа в модалке
    рендерится и залогиненному), а «Мои отклики»/«Личный кабинет» в серверный HTML
    не попадают вовсе — их рисует клиент после гидрации. Проверка по тексту
    объявляла живую сессию анонимной: замер того же дня — `/api/favorites`,
    `/api/hidden`, `/api/recruiter/chats/unread` отдали 200 нашей сессии и 401
    анониму, при том что вёрстка в обоих случаях одинаковая.

    Запрос идёт СО СТРАНИЦЫ и same-origin: это тот же вызов, что делает сам
    клиент площадки, ничего не подделывается.

    Возвращает None, если у площадки такой ручки не описано, — тогда решает вёрстка.
    """
    path = (PLATFORMS.get(platform) or {}).get("alive_api")
    if not path:
        return None
    try:
        status = page.evaluate(
            "async (p) => { const r = await fetch(p, {credentials: 'include'});"
            " return r.status; }", path)
    except Exception as e:  # noqa: BLE001 — не смогли спросить, а не «вышли»
        return "unknown", f"приватную ручку не спросить: {type(e).__name__}: {e}"
    if status == 200:
        return "logged_in", f"{path} отвечает 200 — сессия принята площадкой"
    if status in (401, 403):
        return "anonymous", f"{path} отвечает {status} — площадка нас не узнаёт"
    return "unknown", f"{path} отвечает {status} — по нему о входе не судить"


def storage_state_probe(page, platform: str) -> tuple[str, str] | None:
    """Состояние входа по localStorage открытой страницы.

    Для careered это ЕДИНСТВЕННЫЙ честный признак, и в реестре так и написано:
    «настоящая проверка живости — токен в localStorage». Вёрстка там не говорит
    ничего — ни `alive_if`, ни `dead_if` не срабатывают, и проверка по ней даёт
    «unknown», из-за чего вход человека 07.08.2026 не был засчитан, а слепок
    не снялся при живой сессии.

    Возвращает None, если у площадки сессия не клиентская.
    """
    pair = (PLATFORMS.get(platform) or {}).get("localstorage_token")
    if not pair:
        return None
    _, key = pair
    try:
        token = page.evaluate("() => window.localStorage.getItem(%r)" % key)
    except Exception as e:  # noqa: BLE001 — не смогли прочитать, а не «вышли»
        return "unknown", f"localStorage не прочитать: {type(e).__name__}: {e}"
    if token:
        return "logged_in", f"токен {key} лежит в localStorage страницы"
    return "anonymous", f"в localStorage страницы нет {key!r}"


def _page_state(page, platform: str) -> tuple[str, str]:
    """Состояние входа по УЖЕ открытой странице.

    Порядок проб — от свидетеля к пересказу: приватная ручка отвечает за
    площадку, localStorage хранит то, чем площадка нас узнаёт, и только потом
    вёрстка, которая всего лишь описывает нарисованное. Обе первые пробы
    заведены после того, как вёрстка соврала в обе стороны за один день.
    """
    for probe in (api_state(page, platform), storage_state_probe(page, platform)):
        if probe and probe[0] != "unknown":
            return probe
    try:
        return login_state(platform, page.content())
    except Exception as e:  # noqa: BLE001
        return "unknown", f"страницу не прочитать: {type(e).__name__}: {e}"


def wait_for_login(page, platform: str, seconds: int) -> tuple[str, str]:
    """Ждёт входа, опрашивая саму страницу, вместо `input()` на stdin.

    Нужно ровно там, где stdin недоступен: команду запускает агент фоновой
    задачей, окно у пользователя открывается, а нажимать Enter в терминале
    некому — раньше это давало EOFError и «отменено» при живом окне.
    Опрос — это чтение состояния страницы, ничего не вводится и не отправляется.
    """
    deadline = time.monotonic() + seconds
    st, why = "anon", "ещё не входил"
    shown = 0
    while time.monotonic() < deadline:
        try:
            st, why = _page_state(page, platform)
        except Exception as e:  # noqa: BLE001 — окно могло быть в переходе
            st, why = "unknown", f"{type(e).__name__}: {e}"
        if st == "logged_in":
            return st, why
        left = int(deadline - time.monotonic())
        if left // 15 != shown // 15:
            print(f"  жду входа… осталось {left} с", flush=True)
        shown = left
        try:
            page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001 — окно закрыли руками
            break
    return st, why


def _snapshot_cookies(ctx, platform: str, cfg: dict) -> bool:
    """Слепок кук постоянного профиля в `.auth/<площадка>.json`.

    Та же болезнь, что у careered, только сессия в куках: вход оседает в
    постоянном профиле, а `reveal` и `authrefresh.renew_hirehi` читают файл —
    и без слепка после входа файла бы просто не появилось. Пишем сразу, пока
    контекст жив.

    Площадкам без собственной сессии scout (`state_cookie`) здесь делать
    нечего — тихо возвращаем True.
    """
    if not cfg.get("state_cookie"):
        return True
    try:
        save_filtered(ctx.storage_state(), state_path(platform),
                      domains=tuple(cfg.get("domains") or ()))
    except Exception as e:  # noqa: BLE001 — вход состоялся, ронять его нечем
        print(f"⚠️  вход есть, но слепок не сохранился ({type(e).__name__}: {e}) — "
              f"`reveal` и `auth refresh` останутся без сессии", file=sys.stderr)
        return False
    token, _ = state_cookie(platform)
    if not token:
        name = cfg["state_cookie"][1]
        print(f"⚠️  вход есть, но куки {name} в профиле нет — `reveal` и "
              f"`auth refresh` останутся без сессии", file=sys.stderr)
        return False
    print(f"  слепок для reveal/refresh: {state_path(platform)}")
    return True


def _snapshot_localstorage(page, platform: str, cfg: dict) -> bool:
    """Снимает клиентскую сессию со страницы в `.auth/<площадка>.json`.

    Сессия careered живёт в localStorage постоянного профиля — там она и
    продлевается сама. Но stdlib-слой в профиль не ходит, ему нужен файл;
    снимаем его, пока страница открыта. Отложить до `auth refresh` значит
    оставить окно, в котором вход уже сделан, а сборщик всё ещё аноним.

    Площадкам без клиентской сессии делать здесь нечего — тихо возвращаем True.
    """
    pair = cfg.get("localstorage_token")
    if not pair:
        return True
    origin, key = pair
    try:
        token = page.evaluate("() => window.localStorage.getItem(%r)" % key)
    except Exception as e:  # noqa: BLE001 — вход уже состоялся, ронять нечего
        token, why = None, f"{type(e).__name__}: {e}"
    else:
        why = f"в localStorage нет {key!r}"
    if not token:
        print(f"⚠️  вход есть, но слепок не снялся ({why}) — сборщик останется "
              f"анонимом на этой площадке", file=sys.stderr)
        return False
    save_localstorage_token(platform, origin, key, str(token))
    print(f"  слепок для сборщика: {state_path(platform)}")
    return True


def login(platform: str, *, browser: str | None = None, wait: int = 0,
          force: bool = False) -> int:
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
    # Ранний выход только там, где проба ДОКАЗЫВАЕТ живость. У площадок с
    # `state_cookie` она видит лишь наличие токена: срок внутрь куки не положен,
    # а мёртвый вход hirehi отдаёт кодом 200 и анонимной вёрсткой. Выйти здесь
    # значило бы отказать человеку во входе ровно тогда, когда вход и нужен.
    if state == "logged_in" and not cfg.get("state_cookie") and not force:
        # `--force` нужен не для удобства. Проба видит НАЛИЧИЕ куки, а не то,
        # принимают ли её: 08.08.2026 shadowhint отдавал 401 на живую с виду
        # сессию, `auth status` показывал «ВХОД ЖИВ», а `auth login` отказывался
        # открыть окно — то есть войти было нельзя ровно тогда, когда вход и
        # требовался. Пока проба не умеет доказывать живость для этой площадки
        # (нет `alive_api`), последнее слово остаётся за человеком.
        print(f"{platform}: уже залогинен — делать ничего не надо ({why}).")
        print("  Площадка при этом может отвечать 401: проба видит наличие куки, "
              "а не то, принимают ли её. Тогда `--force` откроет окно.")
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
    # Раньше площадки с сессией в localStorage (careered) уходили сюда ВСЕГДА:
    # bundled-путь сохраняет storage_state с origins/localStorage в файл, а из
    # постоянного профиля localStorage было не достать — файла бы не появилось.
    # Теперь достаётся (authrefresh.renew_careered снимает его со страницы), и
    # принуждение снято: разовый слепок стареет, а постоянный профиль сессию
    # продлевает сам. Bundled остался тем, чем и был, — запасным путём для
    # машин без настоящего браузера.
    if name == BUNDLED:
        return _login_bundled(platform, cfg, wait=wait)

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
                # Кроме одного: слепка могло не быть. Сюда приходят как раз тогда,
                # когда session_probe сказал «аноним» (иначе команда закончилась бы
                # выше), а страница профиля показала вход, — то есть сессия в
                # профиле есть, а файла для stdlib-слоя нет.
                _snapshot_localstorage(page, platform, cfg)
                _snapshot_cookies(ctx, platform, cfg)
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
            if wait:
                print(f"  Жду до {wait} с и проверяю страницу сам — Enter не нужен.")
                st, why = wait_for_login(page, platform, wait)
            else:
                try:
                    input("  Enter, когда вошёл: ")
                except (EOFError, KeyboardInterrupt):
                    print("\nотменено (stdin недоступен — запусти с `--wait 180`)",
                          file=sys.stderr)
                    return 1
                page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                st, why = _page_state(page, platform)
            if st == "logged_in":
                _snapshot_localstorage(page, platform, cfg)
                _snapshot_cookies(ctx, platform, cfg)
    except ProfileBusy as e:
        print(str(e), file=sys.stderr)
        return 4
    except RenderUnavailable as e:
        print(str(e), file=sys.stderr)
        return 3

    print(f"\n{platform}: {st} — {why}")
    print("Профиль в .gitignore, с машины не уезжает.")
    return 0 if st == "logged_in" else 1


def _login_bundled(platform: str, cfg: dict, *, wait: int = 0) -> int:
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
        if wait:
            print(f"  Жду до {wait} с и проверяю страницу сам — Enter не нужен.")
            wait_for_login(page, platform, wait)
        else:
            try:
                input("  Enter, когда вошёл: ")
            except (EOFError, KeyboardInterrupt):
                browser.close()
                print("\nотменено (stdin недоступен — запусти с `--wait 180`)",
                      file=sys.stderr)
                return 1
        state = ctx.storage_state()
        browser.close()

    n_all = len(state.get("cookies", []))
    save_filtered(state, state_path(platform), domains=tuple(cfg.get("domains") or ()))
    n_kept = len(json.load(open(state_path(platform), encoding="utf-8"))["cookies"])
    print(f"\nСессия сохранена: {state_path(platform)} — кук {n_kept} "
          f"(из {n_all}; остальное — чужие домены, они отброшены)")
    print("Файл в .gitignore, права 0600, с машины не уезжает.")
    if cfg.get("localstorage_token"):
        # У этой площадки кук может быть ноль — сессия в localStorage, и «кук 0»
        # выше выглядел бы провалом. Вердикт по тому единственному, что решает.
        token, why = bearer_from_state(platform)
        if token:
            print(f"Вход подтверждён: {why}.")
        else:
            print(f"⚠️  Вход НЕ сохранился: {why}", file=sys.stderr)
            return 1
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
        # Пояснение печатается ВСЕГДА, а не только при анониме. Список `where`
        # собран по `choose_browser` — это выбор режима `auto` по покрытию
        # доменов, и он может назвать НЕ ТОТ браузер, в котором сессия на самом
        # деле жива: 07.08.2026 строка гласила «wantapply ВХОД ЖИВ · yandex
        # (7 кук)», тогда как живой токен лежал в chrome, а в яндексовском он
        # истёк неделю назад. Вердикт был верен, источник — нет.
        print(f"  {'':<12} {'':<14} {why[:110]}")

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
