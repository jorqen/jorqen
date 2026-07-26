"""Авторизация, которая переживает перезапуск: сессии лежат в `.auth/` под gitignore.

Проблема, ради которой это написано: логины на площадках отваливались, и каждый скан
начинался с «зайди заново». Браузерная панель — не то место, где состояние живёт
предсказуемо, а ходить в неё за куками для скрипта нельзя вовсе.

Как устроено:

1. `login <площадка>` открывает ВИДИМЫЙ браузер. Входит **человек** — руками, своим
   паролем, своим кодом из почты. Скрипт в этот момент ничего не вводит и не читает.
2. После входа сохраняется `storage_state` — куки и localStorage — в `.auth/<площадка>.json`.
3. Дальше обычный сборщик на stdlib берёт оттуда куки заголовком `Cookie` и ходит
   на площадку уже залогиненным, без всякого браузера.

Границы, которые здесь не двигаются:

* **Логинится только пользователь.** Ни пароля, ни кода из письма, ни magic-link —
  вход по коду из почты это ровно механика захвата аккаунта, и делать её чужими руками
  нельзя независимо от намерений.
* **Куки не уезжают с машины.** `.auth/` в `.gitignore`, в облачную рутину не попадает,
  на сервер не копируется. Сессионная кука — это предъявительский доступ к аккаунту.
  Именно поэтому облачный сборщик ходит только по анонимным источникам.

Playwright нужен ТОЛЬКО для шага 1 и для проверки живости. Нет его — сборщик работает,
просто без авторизованных площадок.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

AUTH_DIR = os.environ.get("SCOUT_AUTH_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".auth"
)

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
    },
    "shadowhint": {
        "login_url": "https://shadowhint.com/auth",
        "check_url": "https://shadowhint.com/profile/tg-vacancies",
        "alive_if": ["Личный кабинет", "tg-vacancies", "Выход"],
        "dead_if": ["/auth", "Войти"],
        "domains": ["shadowhint.com"],
        "note": "~38 000 вакансий из Telegram с полнотекстовым поиском",
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
        "note": "внизу карточки — «Apply on corporate website», прямая ссылка в ATS. "
                "За Cloudflare: проверку проходит пользователь, скрипт её не трогает",
    },
    "geekjob": {
        "login_url": "https://geekjob.ru/login",
        "check_url": "https://geekjob.ru/",
        "alive_if": ["Аккаунт", "Резюме", "Выход"],
        "dead_if": ["Войти", "/login"],
        "domains": ["geekjob.ru"],
        "note": "по Go немного, но контакт бывает прямой",
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


def login(platform: str) -> int:
    """Открывает видимый браузер. Входит пользователь; скрипт только сохраняет результат."""
    if platform not in PLATFORMS:
        print(f"не знаю площадку {platform!r}; есть: {', '.join(PLATFORMS)}", file=sys.stderr)
        return 2
    cfg = PLATFORMS[platform]
    sync_playwright = _require_playwright()
    os.makedirs(AUTH_DIR, exist_ok=True)

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

        ctx.storage_state(path=state_path(platform))
        browser.close()

    os.chmod(state_path(platform), 0o600)
    print(f"\nСессия сохранена: {state_path(platform)} (в .gitignore, с машины не уезжает)")
    return 0


def check(platforms: list[str] | None = None) -> int:
    """Проверяет живость сессий, открывая страницу и глядя на признаки входа.

    Не по метке в localStorage — она привязана к origin, а площадки редиректят
    на поддомены. Только по содержимому страницы.
    """
    names = platforms or [p for p in PLATFORMS if have(p)]
    if not names:
        print("Сохранённых сессий нет. Заведи: python3 -m scripts.scout auth login hh")
        return 0
    sync_playwright = _require_playwright()

    dead = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name in names:
            cfg = PLATFORMS[name]
            if not have(name):
                print(f"  {name:<12} нет сохранённой сессии")
                dead.append(name)
                continue
            ctx = browser.new_context(storage_state=state_path(name), locale="ru-RU")
            page = ctx.new_page()
            try:
                page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
                body = page.content()
                alive = any(m in body for m in cfg["alive_if"])
                status = "жива" if alive else "ИСТЕКЛА"
                if not alive:
                    dead.append(name)
                print(f"  {name:<12} {status:<9} {page.url}")
            except Exception as e:  # noqa: BLE001
                print(f"  {name:<12} ОШИБКА    {type(e).__name__}: {e}")
                dead.append(name)
            finally:
                ctx.close()
        browser.close()

    if dead:
        print(f"\nПерелогинить: {', '.join(dead)}")
        print("  python3 -m scripts.scout auth login <площадка>")
        print("  Вход делает пользователь — скрипт пароль не вводит.")
    return 1 if dead else 0


def cookie_header(platform: str) -> str | None:
    """Собирает заголовок `Cookie` из сохранённого состояния.

    Смысл: Playwright нужен один раз для входа, а дальше на площадку ходит обычный
    сборщик на stdlib. Браузер на каждый прогон поднимать незачем.
    """
    if not have(platform):
        return None
    with open(state_path(platform), encoding="utf-8") as f:
        state = json.load(f)
    domains = PLATFORMS.get(platform, {}).get("domains", [])
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


def status() -> int:
    """Что вообще сохранено — без запуска браузера."""
    print(f"Каталог сессий: {AUTH_DIR}\n")
    for name, cfg in PLATFORMS.items():
        if have(name):
            p = state_path(name)
            age_days = (datetime.now().timestamp() - os.path.getmtime(p)) / 86400
            with open(p, encoding="utf-8") as f:
                n = len(json.load(f).get("cookies", []))
            print(f"  {name:<12} есть   {n:>3} кук, обновлена {age_days:.1f} дн. назад")
        else:
            print(f"  {name:<12} нет    — {cfg['note'][:52]}")
    print("\nЖивость проверяется браузером: python3 -m scripts.scout auth check")
    return 0
