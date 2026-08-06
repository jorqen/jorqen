"""wall — прохождение антибот-проверок настоящим браузером пользователя.

Идея, ради которой модуль существует: scout — это не бот, это персональный
браузер владельца профиля. Он ходит на площадки его сессией, его браузером, в его
темпе. Большинство «стен» на таких заходах снимаются сами: Cloudflare и подобные
показывают промежуточную страницу, крутят проверку несколько секунд и пропускают
настоящий браузер дальше, ставя `cf_clearance` на недели вперёд.

Что модуль делает:

1. **Открывает страницу настоящим браузером** на постоянном профиле scout
   (`.auth/<браузер>-profile`) — не headless-обёрткой: у headless другой UA,
   и площадки отвечают на него 403 даже там, где стены нет.
2. **Терпеливо ждёт**, пока промежуточная страница сменится содержимым: опрашивает
   маркеры челленджа, а не спит фиксированную паузу.
3. **Не прошло само — зовёт человека**: открывает видимое окно того же профиля,
   человек проходит проверку сам, и полученная кука остаётся в профиле. Следующие
   прогоны идут молча. САМЫЙ КРАЙНИЙ И НЕЖЕЛАТЕЛЬНЫЙ СЛУЧАЙ!!! - он не должен происходить почти никогда

Капчу модуль НЕ решает и решать не будет — ни распознаванием, ни внешним
сервисом. 05.08.2026 был написан и по требованию владельца снят промежуточный
вариант (картинка уезжала ему в Telegram, ответ возвращался в поле): канал
признан неподходящим. Осталось то, что было: капчу проходит человек в браузере,
а лучшая защита от неё — не встречать её вовсе, то есть ходить через API там,
где он есть (hh, Хабр, rabota.ru уже переведены).
"""

from __future__ import annotations

import sys
import time

from .net import looks_blocked

# Маркеры промежуточной страницы. Пока хоть один виден — проверка ещё идёт.
_CHALLENGE_HINTS = (
    "just a moment", "один момент", "подождите", "checking your browser",
    "проверяем ваш браузер", "cf-browser-verification", "cf_chl_opt",
    "challenge-platform", "ddos-guard", "attention required",
)

# Признаки того, что дальше нужен ЧЕЛОВЕК: интерактивная галочка или картинка.
# Их мы не трогаем — ни кликом, ни распознаванием.
_INTERACTIVE = (
    "turnstile", "recaptcha", "hcaptcha", "подтвердите, что вы не робот",
    "verify you are human", "i'm not a robot", "smart-captcha", "captcha",
)


def challenge_state(html: str, status: int | None = None) -> str:
    """'clear' | 'waiting' | 'human' — что сейчас на странице.

    'waiting' — фоновая проверка, её достаточно переждать.
    'human' — интерактивная проверка: дальше только пользователь, руками."""
    low = (html or "").lower()
    if any(h in low for h in _INTERACTIVE):
        return "human"
    if any(h in low for h in _CHALLENGE_HINTS):
        return "waiting"
    return "clear" if not looks_blocked(html, status) else "waiting"


def wait_out(page, *, patience: float = 45.0, poll: float = 2.0) -> str:
    """Ждёт, пока фоновая проверка сама пропустит браузер дальше.

    Возвращает итоговое состояние. Опрос, а не фиксированный сон: Cloudflare
    отпускает за 5–8 секунд, а DDoS-Guard иногда за 20, и спать всегда по
    максимуму — значит платить этим временем на каждой странице.
    """
    deadline = time.monotonic() + patience
    state = "waiting"
    while time.monotonic() < deadline:
        try:
            html = page.content()
        except Exception:  # noqa: BLE001 — страница может перезагружаться
            html = ""
        state = challenge_state(html)
        if state in ("clear", "human"):
            return state
        try:
            page.wait_for_timeout(poll * 1000)
        except Exception:  # noqa: BLE001 — окно закрыли
            break
    return state


def open_for_human(url: str, browser: str | None = None, *,
                   patience: float = 300.0, domains: tuple[str, ...] = ()) -> str:
    """Видимое окно того же профиля: проверку проходит пользователь.

    Кука проверки оседает в постоянном профиле scout, поэтому следующие прогоны
    идут без окна. Мы в этом окне ничего не нажимаем и не вводим."""
    from .render import BUNDLED, pick_browser, real_context  # noqa: PLC0415

    name = pick_browser(browser, domains)
    if name == BUNDLED:
        print("настоящего браузера на машине нет — проверку в отдельном окне "
              "не показать; пройди её в своём браузере сам", file=sys.stderr)
        return "human"
    print(f"🔒 {url}\n"
          f"   Проверку проходит человек — открываю окно {name}. "
          f"Пройди её, окно закроется само.", file=sys.stderr, flush=True)
    with real_context(name, offscreen=False, domains=domains) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        state = wait_out(page, patience=patience, poll=3.0)
    if state == "clear":
        print("   ✅ проверка пройдена, кука сохранена в профиле scout",
              file=sys.stderr)
    else:
        print("   ⚠️  проверка так и не снята — площадка останется со статусом "
              "АНТИБОТ в покрытии", file=sys.stderr)
    return state


def fetch_through(url: str, *, browser: str | None = None, wait: float = 3.0,
                  patience: float = 45.0, domains: tuple[str, ...] = (),
                  ask_human: bool = False) -> tuple[str, str, str]:
    """(html, финальный URL, состояние) — страница через настоящий браузер.

    Состояние 'human' означает, что дальше нужен пользователь: с `ask_human`
    откроется видимое окно, без него вернётся как есть — и вызывающий честно
    покажет статус АНТИБОТ вместо того, чтобы выдать челлендж за выдачу.
    """
    from .render import BUNDLED, pick_browser, real_context  # noqa: PLC0415

    name = pick_browser(browser, domains)
    if name == BUNDLED:
        # Настоящего браузера на машине нет — идём встроенным chromium через
        # штатный рендер. Он слабее против стен (другой UA), но это честный
        # запасной путь, а не падение: раньше здесь был KeyError 'chromium'.
        from .render import render_page  # noqa: PLC0415
        try:
            html, final = render_page(url, wait=wait)
        except Exception as e:  # noqa: BLE001 — стена или недоступность
            return "", url, "human" if "антибот" in str(e).lower() else "error"
        return html, final, challenge_state(html)
    with real_context(name, offscreen=True, domains=domains) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        state = wait_out(page, patience=patience)
        if state == "clear":
            page.wait_for_timeout(wait * 1000)
        html, final = page.content(), page.url

    if state == "human" and ask_human:
        if open_for_human(url, name, domains=domains) == "clear":
            return fetch_through(url, browser=name, wait=wait, patience=patience,
                                 domains=domains, ask_human=False)
    return html, final, state


def fetch_many_through(urls: list[str], *, browser: str | None = None,
                       wait: float = 3.0, patience: float = 45.0,
                       domains: tuple[str, ...] = ()) -> list[tuple[str, str, str]]:
    """Несколько адресов ОДНИМ браузерным контекстом. Порядок сохраняется.

    Зачем отдельная функция. `fetch_through` открывает контекст на каждый вызов,
    и зонд канала найма (три адреса на компанию) запускал браузер трижды подряд.
    На двадцати компаниях это шестьдесят запусков вместо двадцати: каждый —
    это профиль под локом, секунды ожидания и лишний повод для площадки
    посчитать нас ботом.

    Адрес, упавший или закрытый стеной, не рвёт обход остальных: у него будет
    своё состояние в своём элементе списка.
    """
    from .render import BUNDLED, pick_browser, real_context  # noqa: PLC0415

    if not urls:
        return []
    name = pick_browser(browser, domains)
    if name == BUNDLED:
        # Без настоящего браузера переиспользовать нечего — идём штатным путём.
        return [fetch_through(u, browser=name, wait=wait, patience=patience,
                              domains=domains, ask_human=False) for u in urls]
    out: list[tuple[str, str, str]] = []
    with real_context(name, offscreen=True, domains=domains) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                state = wait_out(page, patience=patience)
                if state == "clear":
                    page.wait_for_timeout(wait * 1000)
                out.append((page.content(), page.url, state))
            except Exception as e:  # noqa: BLE001 — один адрес не рвёт обход
                out.append(("", url,
                            "human" if "антибот" in str(e).lower() else "error"))
    return out
