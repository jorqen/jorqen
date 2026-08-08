"""hhapi — hh.ru через ОФИЦИАЛЬНЫЙ API.

Зачем. hh — самая плотная площадка выдачи, и до сих пор она читалась разбором
встроенного стейта страницы (`HH-Lux-InitialState`). Это работает ровно до
следующей смены вёрстки и упирается в антибот-проверки: разбор HTML — гость
на чужой странице, API — договор.

Каким токеном. Замерено 05.08.2026 на живом hh, три факта:

  * `grant_type=client_credentials` этими ключами → 502 (заглушка hh), при том
    что заведомо мусорный client_id даёт честный `400 invalid_client`. Значит
    отказ адресный, а не «эндпоинт лежит»: токена ПРИЛОЖЕНИЯ по ним не будет.
  * `GET api.hh.ru/vacancies` без токена → `403 forbidden`. Анонимного API нет.
  * `GET hh.ru/oauth/authorize?client_id=…` → 302 на логин с сохранением
    client_id и redirect_uri. Живая дверь ровно одна.

Отсюда режим работы: пользовательский токен (`authorization_code`), вход —
существующей сессией hh из `.auth/`. Такой токен покрывает и поиск, и отклики,
поэтому разбор вёрстки кабинета (`hhsync`) становится запасным путём, а не
основным.

Цена. Токен пользователя — это доступ к резюме, откликам и переписке, то есть
права шире, чем нужно для поиска; и ключи приложения не наши (см. `hh.env`),
значит hh видит этот трафик как трафик своего мобильного клиента. Решение
осознанное и записано в README: своё приложение на dev.hh.ru не выдают.
Практическое следствие для кода: ничего, кроме чтения. Ни откликов, ни
сообщений, ни правок резюме через API этот модуль не делает и делать не должен.

Нет ключей или нет токена — модуль молча уступает место разбору HTML и говорит
об этом одной строкой. Ничего не ломается, просто площадка читается по-старому.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse

from .auth import AUTH_DIR
from .net import FetchError, fetch_json, qs

ENV_PATH = os.path.join(AUTH_DIR, "hh.env")
TOKEN_PATH = os.path.join(AUTH_DIR, "hh-token.json")

API = "https://api.hh.ru"
TOKEN_URL = "https://hh.ru/oauth/token"
AUTHORIZE_URL = "https://hh.ru/oauth/authorize"

# Кастомная схема мобильного клиента: страница согласия уводит на неё вместе с
# ?code=…. Браузер по этому адресу никуда не пойдёт — код снимается с попытки
# перехода, сам переход и не нужен.
REDIRECT_URI = "hhandroid://oauthresponse"

# hh требует User-Agent, называющий приложение и контакт. Это не формальность:
# без внятного UA запросы режут, и это правильно — по нему нас и опознают.
# Здесь именно своё имя, а не подделка под мобильный клиент: на выдачу токена
# UA не влияет (проверено — 502 приходит с любым), а притворяться устройством,
# которого нет, значит врать без выгоды.
# 🔴 Контакта здесь нет намеренно: репозиторий публичный, и почта владельца
# в коде — это адрес для сборщиков спама. Своё имя с контактом кладётся
# в HH_USER_AGENT в .auth/hh.env (файл под gitignore) и перекрывает это
# значение в обоих местах ниже.
DEFAULT_UA = "jorqen-scout/1.0"

HOWTO = f"""hh читается разбором HTML: нет пользовательского токена API.

Через API надёжнее — не зависит от вёрстки, не упирается в антибот и заодно
отдаёт статусы откликов без разбора кабинета. Порядок:

  1. Ключи приложения в {ENV_PATH} (HH_CLIENT_ID/HH_CLIENT_SECRET).
  2. `scout hh-auth` — один раз: откроется вход hh, дальше токен ляжет
     в {TOKEN_PATH} и будет обновляться сам.

Файлы остаются на этой машине (.auth/ в .gitignore). Токена нет — разбор HTML
продолжает работать, ничего не ломается."""


def read_env(path: str = ENV_PATH) -> dict[str, str] | None:
    """KEY=VALUE построчно. Нет файла — None (штатный случай, не ошибка)."""
    if not os.path.exists(path):
        return None
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        # В файле лежит client_secret — предъявительский секрет.
        os.chmod(path, 0o600)
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out or None


def configured(env: dict | None = None) -> bool:
    """Есть ключи приложения. Это ещё НЕ значит, что API доступен."""
    e = read_env() if env is None else env
    return bool(e and e.get("HH_CLIENT_ID") and e.get("HH_CLIENT_SECRET"))


def usable(env: dict | None = None) -> bool:
    """Ключи + пользовательский токен (живой или обновляемый).

    Разведено с configured() намеренно: ключи без токена — ровно та ситуация,
    в которой раньше источник уходил в API и получал 403 на каждой странице.
    Решение «API или HTML» принимается по ЭТОЙ функции."""
    if not configured(env):
        return False
    t = read_token()
    return bool(_fresh(t) or t.get("refresh_token"))


def read_token() -> dict:
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _fresh(data: dict) -> str | None:
    """Живой access_token из пары, если он ещё жив. Запас в час: токен,
    протухший на середине обхода, стоит половины выдачи."""
    if not data.get("access_token"):
        return None
    if float(data.get("expires_at") or 0) - 3600 < time.time():
        return None
    return str(data["access_token"])


def save_token(data: dict) -> dict:
    """Пара токенов на диск, 600. Пишется атомарно (rename): refresh_token
    одноразовый, и файл, порванный на середине записи, — это ещё один поход
    через браузер."""
    out = {"access_token": data.get("access_token"),
           "refresh_token": data.get("refresh_token"),
           "expires_at": time.time() + float(data.get("expires_in") or 1209600)}
    os.makedirs(AUTH_DIR, exist_ok=True)
    tmp = TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_PATH)
    return out


def _post_token(params: dict, env: dict) -> dict:
    body = urllib.parse.urlencode(params)
    data = fetch_json(
        TOKEN_URL, method="POST", data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": env.get("HH_USER_AGENT") or DEFAULT_UA})
    if not data.get("access_token"):
        raise FetchError(TOKEN_URL, f"hh не выдал токен: {str(data)[:200]}")
    return save_token(data)


def authorize_url(env: dict | None = None) -> str:
    e = env if env is not None else (read_env() or {})
    if not e.get("HH_CLIENT_ID"):
        raise FetchError(AUTHORIZE_URL, f"нет ключей приложения в {ENV_PATH}")
    return qs(AUTHORIZE_URL, {"client_id": e["HH_CLIENT_ID"],
                              "response_type": "code",
                              "redirect_uri": REDIRECT_URI})


def exchange_code(code: str, env: dict | None = None) -> dict:
    """?code=… → пара токенов. Код одноразовый и живёт минуты.

    redirect_uri здесь ОБЯЗАТЕЛЕН: без него hh отвечает
    `400 invalid_request: bad redirect url` (проверено 05.08.2026). Тот же обмен
    в hh-applicant-tool его не шлёт — значит либо у них он давно не выполнялся,
    либо hh ужесточил проверку; в любом случае повторять чужую ошибку незачем."""
    e = env if env is not None else (read_env() or {})
    return _post_token({"grant_type": "authorization_code", "code": code,
                        "redirect_uri": REDIRECT_URI,
                        "client_id": e.get("HH_CLIENT_ID", ""),
                        "client_secret": e.get("HH_CLIENT_SECRET", "")}, e)


def refresh(env: dict | None = None) -> str:
    """Обновление по refresh_token.

    hh принимает refresh_token ОДИН раз и только после истечения access_token —
    поэтому дёргается лениво, из token(), а не по расписанию. Отказ означает,
    что пара мертва: файл сносим, иначе следующий запуск будет вечно долбиться
    протухшим токеном вместо честного «сходи авторизуйся заново»."""
    e = env if env is not None else (read_env() or {})
    rt = read_token().get("refresh_token")
    if not rt:
        raise FetchError(TOKEN_URL, f"нет refresh_token.\n\n{HOWTO}")
    try:
        return str(_post_token({"grant_type": "refresh_token",
                                "refresh_token": rt}, e)["access_token"])
    except FetchError:
        try:
            os.unlink(TOKEN_PATH)
        except OSError:
            pass
        raise


def token(env: dict | None = None) -> str:
    """Пользовательский токен из .auth/hh-token.json, с ленивым обновлением.

    Только чтение выдачи и своих откликов. Ни одного пишущего вызова API в
    модуле нет — см. заголовок файла."""
    data = read_token()
    cached = _fresh(data)
    if cached:
        return cached
    e = env if env is not None else (read_env() or {})
    if not (e.get("HH_CLIENT_ID") and e.get("HH_CLIENT_SECRET")):
        raise FetchError(TOKEN_URL, f"нет ключей приложения.\n\n{HOWTO}")
    if data.get("refresh_token"):
        return refresh(e)
    raise FetchError(TOKEN_URL, f"нет токена.\n\n{HOWTO}")


def headers(env: dict | None = None) -> dict[str, str]:
    e = env if env is not None else (read_env() or {})
    return {"Authorization": f"Bearer {token(e)}",
            "User-Agent": e.get("HH_USER_AGENT") or DEFAULT_UA,
            "HH-User-Agent": e.get("HH_USER_AGENT") or DEFAULT_UA}


# ──────────────────────────────────────────────────────────────────────────────
# Вход: страница согласия hh → ?code= → пара токенов
# ──────────────────────────────────────────────────────────────────────────────

LOGIN_TIMEOUT = 180.0   # человеку хватит и на ввод кода из письма, и на капчу

# Экран согласия: «Вход в приложение <имя> (<почта>) … Proceed».
CONSENT_BUTTON = 'button[data-qa="oauth-grant-allow"]'
CONSENT_WHO = '[data-qa="oauth-authorize-logout-form"], form'

_SCHEME = REDIRECT_URI.split("://", 1)[0] + "://"


def _code_from(url: str) -> str | None:
    if not url.startswith(_SCHEME):
        return None
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get(
        "code", [None])[0]


def login(*, visible: bool = False, confirm: bool = True,
          cookies_from: str | None = None, use_cache: bool = False,
          timeout: float = LOGIN_TIMEOUT, env: dict | None = None) -> dict:
    """Один поход за пользовательским токеном. Возвращает сохранённую пару.

    Что делает скрипт: открывает страницу согласия сессией, которая уже есть в
    `.auth/` (или в браузере — `--cookies-from`), и жмёт «Proceed», после чего
    снимает `?code=` с редиректа на `hhandroid://`.

    Чего скрипт НЕ делает: не вводит логин, пароль и код из письма и не трогает
    капчу. Нет живой сессии — значит `--visible` и вход руками; подставлять
    учётные данные за человека здесь нечему и незачем.

    Согласие жмётся по `--confirm` (по умолчанию да — так просил владелец), и
    перед нажатием в stderr печатается, КАКОМУ аккаунту выдаются права: пара
    токенов привязывается к конкретному аккаунту, а их у владельца несколько,
    и «прочитал отклики не того аккаунта» — ошибка, которую по данным потом не
    отличить от «откликов нет».

    Код ловится двумя способами сразу: событием запроса на неизвестную схему и
    заголовком Location у 302. Один способ — это ставка на то, что Chromium и
    дальше будет сообщать о переходе, которого он не умеет совершать."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from .hhsync import _hh_storage  # noqa: PLC0415
    from .net import UA  # noqa: PLC0415

    e = env if env is not None else (read_env() or {})
    url = authorize_url(e)
    storage, where = _hh_storage(cookies_from, use_cache)
    print(f"# hh-auth: {where or 'без сессии hh — потребуется вход руками'}",
          file=sys.stderr)

    code: dict[str, str] = {}

    def catch(candidate: str) -> None:
        got = _code_from(candidate or "")
        if got and "code" not in code:
            code["code"] = got

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not visible)
        try:
            ctx = browser.new_context(storage_state=storage, locale="ru-RU",
                                      user_agent=UA)
            page = ctx.new_page()
            page.on("request", lambda r: catch(r.url))
            page.on("response", lambda r: catch(r.headers.get("location", "")))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                # Переход на hhandroid:// падает по определению — схема
                # браузеру неизвестна. Код к этому моменту уже перехвачен.
                pass

            deadline = time.time() + timeout
            clicked = False
            while "code" not in code and time.time() < deadline:
                if not clicked and confirm:
                    btn = page.query_selector(CONSENT_BUTTON)
                    if btn:
                        who = _consent_account(page)
                        print(f"# hh-auth: выдаю права приложению от имени "
                              f"{who or 'аккаунта из этой сессии'}", file=sys.stderr)
                        try:
                            btn.click(timeout=10000)
                            clicked = True
                        except Exception as exc:  # noqa: BLE001
                            print(f"# hh-auth: согласие не нажалось ({exc});"
                                  f" нажми сам", file=sys.stderr)
                page.wait_for_timeout(500)
        finally:
            browser.close()

    if "code" not in code:
        raise FetchError(AUTHORIZE_URL, "hh не отдал code: " + (
            "окно закрылось без редиректа" if visible else
            "в фоне не вышло — вероятно, нужен вход или капча. Запусти "
            "`scout hh-auth login --visible` и пройди шаг руками"))
    return exchange_code(code["code"], e)


def _consent_account(page) -> str | None:
    """Кому именно выдаём права — из текста экрана согласия. Чисто для отчёта,
    поэтому любая осечка молчит: сорванный разбор строки не повод не выдать
    токен, но и молча выдать «непонятно кому» нельзя."""
    try:
        text = " ".join((page.inner_text("body") or "").split())
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"[Вв]ход в приложение\s+([^,(]{2,60}\([^)]{3,80}\))", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"[\w .-]{2,40}\([\w.+-]+@[\w.-]+\)", text)
    return m.group(0).strip() if m else None


# ──────────────────────────────────────────────────────────────────────────────
# Поиск
# ──────────────────────────────────────────────────────────────────────────────

PER_PAGE = 100          # серверный потолок
MAX_PAGES = 20          # предохранитель: 2000 вакансий на формулировку
# hh отдаёт максимум 2000 вакансий на запрос. Больше — только сузив запрос,
# и об этом надо СКАЗАТЬ, а не молча отдать первые две тысячи.
API_RESULT_CAP = 2000


def negotiations_page(page: int = 0, env: dict | None = None) -> dict:
    """Страница своих откликов. То же, что читал разбор кабинета, но контрактом.

    Требует пользовательского токена — приложением этот эндпоинт не открыть."""
    url = qs(f"{API}/negotiations", {"page": page, "per_page": PER_PAGE})
    return fetch_json(url, headers=headers(env))


def search_page(query: str, *, area=None, period: int = 3, page: int = 0,
                env: dict | None = None) -> dict:
    url = qs(f"{API}/vacancies", {
        "text": query, "area": area, "period": period,
        "per_page": PER_PAGE, "page": page, "order_by": "publication_time",
    })
    return fetch_json(url, headers=headers(env))
