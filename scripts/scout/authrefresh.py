"""Продление сессий площадок без участия человека — и честный отказ там, где без
него нельзя.

Три площадки, за которые просили автопродление, больны РАЗНЫМ (проверено живым
чтением кук 06.08.2026), и одного лечения на всех не существует:

* **hirehi** — refresh-токен лежит в собственной сессии scout (`.auth/hirehi.json`)
  и РОТИРУЕТСЯ каждым обновлением: у того, кто обновил не последним, сессия
  протухает мгновенно. Поэтому продлеваем ровно так, как это делает сам клиент
  площадки: открываем страницу его же кодом, он сам зовёт `POST /api/auth/refresh`,
  а обновлённый storage_state оседает обратно в `.auth/hirehi.json`. Приватных
  ручек руками не дёргаем, и куки браузера владельца НЕ читаем — заход ими
  проротировал бы токен и разлогинил его живую вкладку (тот же довод, что в
  шапке `reveal.py`).

* **wantapply** — в куке url-encoded JSON `{token, refreshToken, tokenExpires}`.
  Продлевать нечего: сам wantapply.com стоит за управляемым Cloudflare, вход туда
  проходит человек. Автоматизировать здесь можно ровно одно — сказать про истечение
  ДО прогона, а не после, когда 401 на ручке контактов уже прочитан как «у вакансии
  нет прямой ссылки». Срок виден офлайн, без единого запроса.

* **careered** — сессия не в куках вовсе, а в localStorage. Разовый
  `auth login careered` снимает её слепком в `.auth/careered.json`, и слепок
  стареет. Снимаем заново с ПОСТОЯННОГО профиля scout, где сессия живёт и
  продлевается сама, — тем же настоящим браузером, что ходит по площадкам.

* **shadowhint** — вход через «Войти с Google» (в куках домена лежит `g_state`),
  то есть пароля у площадки нет вовсе, а живёт всё на сессии Google. Сам JWT
  площадки короткий — 7,7 дня (замер 08.08.2026), а сессия Google в постоянном
  профиле держится месяцами и выдаёт новый JWT при каждом заходе. Поэтому
  лечение то же, что у careered: один вход В ПРОФИЛЬ, дальше снимаем свежую
  куку без человека. Вход в другом окне сюда не попадает — это и была причина
  еженедельных походов за новым токеном.

Отсюда и деление: `preflight()` — офлайн-картина «что отвалится в этом прогоне»
(её зовут `wave` и `budget` до сбора), `renew()` — то, что можно поднять без
человека. Всё, что человек обязан сделать сам, названо командой, а не намёком.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone

from . import auth

# Порядок площадок в отчёте — по цене разлогина, а не по алфавиту: сначала те,
# без кого волна станет неполной, потом те, кто стоит только контактов.
ORDER = ("shadowhint", "hh", "hirehi", "wantapply", "careered", "habr", "geekjob")

# Что именно теряется, если сессия мертва. Берётся из реестра площадок
# (`login_gains` / `login_optional`), а не пишется здесь второй раз.
FULL_LOSS = "сбор по площадке"


def cost_of_death(platform: str) -> tuple[str, bool]:
    """(что теряется без входа, критично ли это для сбора).

    Различать обязательно. У shadowhint без входа нет НИЧЕГО, у wantapply без
    входа пропадают только прямые ссылки в ATS — а каталог собирается целиком.
    Свалить их в одно «залогинься» значит добиться, чтобы список перестали
    читать: он станет одинаково тревожным всегда."""
    cfg = auth.PLATFORMS.get(platform) or {}
    gains = cfg.get("login_gains")
    if not gains:
        return "ничего — вход этой площадке не нужен", False
    if cfg.get("login_optional"):
        return gains, False
    return f"{FULL_LOSS}: {gains}", True


def can_renew(platform: str) -> bool:
    """Поднимается ли сессия ПРЯМО СЕЙЧАС без человека.

    Три площадки, и у каждой своё условие:

    * hirehi — продлевать можно лишь то, что есть: клиенту нужен живой
      refresh-токен из `.auth/hirehi.json`. Нет файла — нет и продления, и
      обещать «поднимется само» здесь значит отправить человека выполнять
      команду, которая заведомо вернёт отказ;
    * careered — источник не файл, а постоянный профиль, где сессия живёт сама.
      Слепка `.auth/careered.json` может не быть вовсе: он результат продления,
      а не его условие;
    * shadowhint — то же, что careered, но сессия в куке. Условие то же:
      постоянный профиль должен быть залогинен в Google, иначе площадка отдаст
      анонимную страницу и продлевать будет нечего.

    Проверяем НАЛИЧИЕ куки Google-состояния в профиле, а не сам вход: срок
    сессии Google снаружи не виден, и обещать «поднимется само» без единого
    признака значит слать человека за заведомым отказом.

    Всё остальное — пароль, код или Cloudflare, то есть человек."""
    if platform == "hirehi":
        return auth.have("hirehi")
    if platform == "shadowhint":
        return _profile_knows_shadowhint()
    return platform == "careered"


def _profile_knows_shadowhint() -> bool:
    """Есть ли в постоянном профиле следы входа на shadowhint (кука `g_state`).

    Дешёвая офлайн-проверка: читаем базу кук профиля, браузер не поднимаем.
    `preflight` зовут `wave` и `budget` до сбора, и открывать там окно ради
    ответа «продлевается ли» было бы дороже самого продления.
    """
    try:
        from . import cookiesrc  # noqa: PLC0415

        for browser in ("chrome", "yandex"):
            got = cookiesrc.read_scout_profile(browser, ("shadowhint.com",))
            if any(c.get("name") in ("g_state", "auth_token") for c in got):
                return True
    except Exception:  # noqa: BLE001 — нет профиля, нет браузера, залочена база
        return False
    return False


_CACHE: dict[str | None, list[dict]] = {}


def preflight(cookies_from: str | None = None) -> list[dict]:
    """Состояние сессий БЕЗ сети и браузера — чтение кук и файлов `.auth/`.

    Отсюда растёт предупреждение до прогона. Наружу не уходит ни один запрос,
    окон не открывается.

    Результат кэшируется на процесс. Не микрооптимизация: чтение кук Chrome на
    macOS расшифровывается ключом из Keychain, и звать это дважды за прогон
    (картина волны и «следующий шаг» спрашивают одно и то же) значит дважды
    трогать Keychain ради заведомо одинакового ответа. Кэш на процесс безопасен
    потому, что внутри одного прогона пользователь не логинится: волна идёт
    минуты, а он в это время читает её вывод.
    """
    if cookies_from in _CACHE:
        return _CACHE[cookies_from]
    rows: list[dict] = []
    for platform in ORDER:
        if platform not in auth.PLATFORMS:
            continue
        state, why = auth.session_probe(platform, cookies_from=cookies_from)
        if state == "not_needed":
            continue
        loss, critical = cost_of_death(platform)
        if not critical and loss.startswith("ничего"):
            # habr: вход даёт свою историю откликов, но её тянет отдельная
            # синхронизация, а не сбор. Строка «залогинься ради ничего» — чистый
            # шум, и именно из-за такого шума списки перестают читать.
            continue
        rows.append({
            "platform": platform,
            "state": state,
            "why": why,
            "loss": loss,
            "critical": critical,
            "renewable": can_renew(platform),
        })
    _CACHE[cookies_from] = rows
    return rows


def forget() -> None:
    """Сбросить кэш проб — после `auth login`/`auth refresh` состояние другое."""
    _CACHE.clear()


def _trim_howto(why: str) -> str:
    """Убирает из пояснения хвост «а теперь войди командой …».

    Пояснения в `auth.py` пишутся для `auth status`, где команда в конце строки
    уместна. Здесь она идёт отдельной строкой ниже, и без обрезки получается
    «Разовый вход: scout auth login hirehi — нужен твой вход: scout auth login
    hirehi» — совет, повторённый дважды, читается как два разных совета."""
    for marker in ("Разовый вход:", "одноразовый вход:", "повтори:", "обнови её"):
        head = why.split(marker, 1)[0]
        if head != why:
            why = head
    return why.rstrip(" —,.")


def preflight_lines(cookies_from: str | None = None) -> list[str]:
    """Строки для `wave` и `budget` — ТОЛЬКО про то, что сломано.

    Живые сессии не перечисляем намеренно: отчёт волны и так длинный, а строка
    «shadowhint жив» не меняет ни одного решения. Полная картина — `auth status`.
    """
    lines: list[str] = []
    for r in preflight(cookies_from):
        if r["state"] != "anonymous":
            # Сюда не попадают ни `logged_in`, ни `unknown`, и второе важнее.
            # «По кукам не понять» — это отсутствие знания, а не поломка; выдать
            # его за поломку значит показывать тревогу в каждой волне и приучить
            # к тому, что блок можно пролистывать. Неизвестность разбирает
            # `auth check`, который для того и открывает страницу.
            continue
        mark = "❌" if r["critical"] else "⚠️"
        fix = (f"поднимется само: `scout auth refresh {r['platform']}`"
               if r["renewable"] else f"нужен твой вход: `scout auth login {r['platform']}`")
        lines.append(f"{mark} {r['platform']}: {_trim_howto(r['why'])}\n"
                     f"   теряем {r['loss']} — {fix}")
    return lines


def preflight_block(cookies_from: str | None = None) -> str:
    """Тот же список абзацем для «картины волны». Пусто — значит всё живо."""
    lines = preflight_lines(cookies_from)
    if not lines:
        return ""
    head = ("## Авторизация\n\n"
            "Сессии проверены ДО сбора (по кукам и `.auth/`, без запросов наружу).\n")
    return head + "\n".join(f"- {ln}" for ln in lines)


# ──────────────────────────────────────────────────────────────────────────────
# Продление: hirehi
# ──────────────────────────────────────────────────────────────────────────────

def _playwright():
    """Ленивый импорт playwright — отдельной функцией, чтобы её подменял тест.

    Проверять надо ровно то, что руками не проверишь: обрыв страницы ПОСЛЕ
    ротации токена. Живьём такой прогон стоит сожжённой сессии, и повторить его
    по требованию нельзя."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    return sync_playwright


def adopt_from_browser(platform: str, spec: str | None = None) -> tuple[bool, str]:
    """Забрать сессию площадки из ПОВСЕДНЕВНОГО браузера в собственный файл scout.

    Владелец 07.08.2026: «авторизацию лучше проводить в основном браузере
    (у меня Яндекс), а потом просто считывать её». Это дешевле любого окна:
    вход человек делает там, где и так залогинен, а scout только читает
    копию базы кук.

    Размен назван вслух и разрешён владельцем ещё 04.08.2026: у ротационных
    площадок (hirehi) забранный токен дальше ротируем МЫ, и живая вкладка в
    Яндексе разлогинится. Поэтому команда не делается «на всякий случай» —
    только явным `--from-browser`.
    """
    cfg = auth.PLATFORMS.get(platform) or {}
    pair = cfg.get("state_cookie")
    if not pair:
        return False, f"{platform}: собственной сессии scout у площадки нет"
    domain, name = pair
    from . import cookiesrc  # noqa: PLC0415

    try:
        # use_cache=False: кэш здесь означает «вижу вчерашний разлогин» — ровно
        # то, от чего мы и уходим, забирая свежий вход человека.
        src = cookiesrc.resolve(spec, (domain,), use_cache=False)
    except Exception as e:  # noqa: BLE001
        return False, f"куки {domain} прочитать не вышло: {type(e).__name__}: {e}"
    state = src.storage_for_playwright()
    got = [c for c in (state.get("cookies") or []) if c.get("name") == name]
    if not got:
        return False, (f"в браузере ({src.line()}) нет куки {name} на {domain} — "
                       f"войди на площадку в своём браузере и повтори")
    try:
        auth.save_filtered(state, auth.state_path(platform),
                           domains=tuple(cfg.get("domains") or ()))
    except OSError as e:
        return False, f"не смог записать {auth.state_path(platform)}: {e}"
    forget()
    return True, (f"сессия забрана из браузера ({src.line()}) в "
                  f"{auth.state_path(platform)}; дальше ротацию держим мы, "
                  f"и живая вкладка {domain} разлогинится — так разрешено")


def renew_hirehi(*, wait_ms: int = 6000) -> tuple[bool, str]:
    """Даём клиенту hirehi обновить свой токен самому и сохраняем ротацию у себя.

    Почему не POST /api/auth/refresh из stdlib: контракт ручки не документирован,
    а ошибиться в нём здесь дороже обычного — неудачный обмен СЖИГАЕТ единственный
    refresh-токен, и лечится это только новым входом человека. Клиент площадки
    знает свой контракт точно; наше дело — открыть ему страницу и не потерять то,
    что он проротировал.

    Источник сессии — только `.auth/hirehi.json`. Куки браузера владельца здесь
    не читаются ни при каких флагах: это и есть та ошибка, от которой у него
    «протухало за три часа».
    """
    import os  # noqa: PLC0415

    from .net import UA  # noqa: PLC0415

    state_file = auth.state_path("hirehi")
    if not os.path.exists(state_file):
        return False, ("нет .auth/hirehi.json — продлевать нечего. "
                       "Разовый вход: `scout auth login hirehi`")
    try:
        sync_playwright = _playwright()
    except ImportError:
        return False, ("нужен playwright: pip install playwright && "
                       "playwright install chromium")

    cfg = auth.PLATFORMS["hirehi"]
    # Заводим до запуска браузера: если контекст не создастся вовсе, вердикт
    # ниже обязан читаться, а не падать NameError поверх настоящей причины.
    st, why, failed, saved = "unknown", "", None, None
    with sync_playwright() as pw:
        # headless с подставным UA — ровно как в reveal: на UA со словом
        # HeadlessChrome hirehi отдаёт 403 в 48 байт, и это ложная стена.
        br = pw.chromium.launch(headless=True)
        try:
            try:
                ctx = br.new_context(storage_state=state_file, locale="ru-RU",
                                     user_agent=UA)
                page = ctx.new_page()
            except Exception as e:  # noqa: BLE001 — битый storage_state бывает
                # Без этого перехвата обещание строкой ниже («вердикт обязан
                # читаться») не выполнялось: у внешнего try есть только finally,
                # и исключение улетало трейсбеком через `renew()` — то есть
                # `scout auth refresh` падал вместо честного «не продлено,
                # потому что …». Битый .auth/hirehi.json это ровно тот случай,
                # когда человеку нужен совет, а не стек вызовов.
                return False, (f"сессию не открыть ({type(e).__name__}: {e}) — "
                               f"похоже, {state_file} испорчен. "
                               f"Разовый вход: `scout auth login hirehi`")
            try:
                page.goto(cfg["check_url"], wait_until="domcontentloaded",
                          timeout=60000)
                # Пауза не косметическая: обновление токена клиент делает уже
                # после первой отрисовки, и снимок storage_state до него сохранил
                # бы СТАРУЮ куку — то есть ровно ту, которую сервер только что
                # обесценил.
                page.wait_for_timeout(wait_ms)
                # Судим по приватной ручке, а не по разметке: у hirehi слово
                # «Войти» есть и у залогиненного, и проверка по тексту трижды
                # подряд объявила живую сессию мёртвой (07.08.2026).
                st, why = auth._page_state(page, "hirehi")
            except Exception as e:  # noqa: BLE001 — площадка могла не открыться
                st, why = "unknown", f"{type(e).__name__}: {e}"
                failed = f"страница не открылась: {why}"

            # Сохраняем ВСЕГДА, что бы ни случилось со страницей. Обрыв на
            # середине — не «ничего не произошло»: клиент мог успеть обменять
            # токен до него, и выход без записи оставил бы у нас куку, которую
            # сервер уже обесценил. Это ровно тот способ сжечь сессию, от
            # которого вся эта конструкция и защищает.
            try:
                auth.save_filtered(ctx.storage_state(), state_file,
                                   domains=tuple(cfg.get("domains") or ()))
            except Exception as e:  # noqa: BLE001 — иначе потеряем и причину
                saved = f"{type(e).__name__}: {e}"
        finally:
            br.close()

    if saved:
        return False, (f"⚠️  ротацию НЕ УДАЛОСЬ записать в {state_file} ({saved}) — "
                       f"сессия могла сгореть, проверь `scout auth status`")
    if failed:
        return False, f"{failed} (снимок сессии всё равно сохранён)"
    if st == "logged_in":
        return True, f"сессия продлена, ротация осела в {state_file} ({why})"
    return False, (f"страница отдалась анонимной ({why}) — refresh-токен уже "
                   f"не принимают. Разовый вход: `scout auth login hirehi`")


# ──────────────────────────────────────────────────────────────────────────────
# Продление: careered
# ──────────────────────────────────────────────────────────────────────────────

def renew_careered(*, browser: str | None = None) -> tuple[bool, str]:
    """Снимает Bearer из localStorage постоянного профиля scout в `.auth/careered.json`.

    Постоянный профиль и есть место, где сессия careered живёт и продлевается
    сама: приложение держит токен в localStorage, туда же кладёт обновлённый.
    `.auth/careered.json` — только слепок для stdlib-слоя, и его задача не
    «хранить сессию», а «не отстать от профиля».
    """
    from . import render  # noqa: PLC0415

    origin, name = auth.PLATFORMS["careered"]["localstorage_token"]
    script = "() => window.localStorage.getItem(%r)" % name
    if not browser:
        # Умолчание `render` — встроенный шелл, а careered его не проходит
        # («нужен настоящий браузер»). Требовать флаг там, где выбор ровно один,
        # значит превратить `scout auth refresh` без аргументов в заведомый отказ.
        real = [b for b in render.installed_browsers() if b != render.BUNDLED]
        browser = real[0] if real else None
    try:
        token = render.evaluate_on(origin + "/", script, browser=browser)
    except Exception as e:  # noqa: BLE001 — площадка/браузер не повод падать
        return False, f"профиль не отдал токен: {type(e).__name__}: {e}"
    if not token:
        return False, (f"в постоянном профиле нет localStorage {name!r} — "
                       f"войди один раз: `scout auth login careered`")
    try:
        auth.save_localstorage_token("careered", origin, name, str(token))
    except OSError as e:
        return False, f"не смог записать .auth/careered.json: {e}"
    return True, f"токен снят с постоянного профиля в {auth.state_path('careered')}"


# ──────────────────────────────────────────────────────────────────────────────
# Команда
# ──────────────────────────────────────────────────────────────────────────────

def renew_shadowhint(*, browser: str | None = None) -> tuple[bool, str]:
    """Снимает свежий `auth_token` shadowhint с постоянного профиля scout.

    Зачем это вообще возможно. Вход на shadowhint идёт через «Войти с Google»
    (в куках домена лежит `g_state` — cookie Google Identity Services), поэтому
    пароля у площадки не существует, а живёт всё на сессии Google. Сессия
    Google в постоянном профиле держится месяцами, и при каждом заходе площадка
    выдаёт НОВЫЙ восьмидневный JWT.

    Отсюда весь смысл: JWT shadowhint живёт 7,7 дня (замер 08.08.2026), и без
    этой ручки владельцу пришлось бы входить руками каждую неделю. С ней —
    один вход в постоянный профиль, дальше продление без человека.

    Кука НЕ httpOnly намеренно со стороны площадки: приложение зеркалит в неё
    Bearer из localStorage. Поэтому её видно из `document.cookie`, и браузер
    отдаёт её той же дорогой, что и careered свой localStorage.
    """
    import urllib.parse  # noqa: PLC0415

    from . import render  # noqa: PLC0415

    domain, name = auth.PLATFORMS["shadowhint"]["session_cookie"]
    if not browser:
        # Тот же выбор, что у careered: встроенный шелл сюда не годится —
        # постоянного профиля у него нет, а именно в нём и живёт сессия.
        real = [b for b in render.installed_browsers() if b != render.BUNDLED]
        browser = real[0] if real else None
    try:
        raw = render.evaluate_on(f"https://{domain}/", "() => document.cookie",
                                 browser=browser)
    except Exception as e:  # noqa: BLE001 — площадка/браузер не повод падать
        return False, f"профиль не отдал куки: {type(e).__name__}: {e}"

    token = None
    for part in (raw or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            token = urllib.parse.unquote(value)
    if not token:
        return False, (f"в постоянном профиле нет куки {name!r} — войди ОДИН раз "
                       f"именно в него: `scout auth login shadowhint --force "
                       f"--browser chrome`. Вход в другом окне сюда не попадёт")

    # Свежесть проверяем ДО записи: перезаписать живой слепок протухшим значит
    # своими руками сломать то, что работало. Ровно это уже случалось при
    # экспорте — просроченный токен из браузера накрывал свежий из файла.
    exp = auth._jwt_exp(token)
    if exp is not None and exp < time.time():
        return False, ("профиль отдал ПРОСРОЧЕННЫЙ токен — сессия Google в нём "
                       "кончилась, нужен вход руками")
    try:
        auth.save_session_cookie("shadowhint", domain, name, token)
    except OSError as e:
        return False, f"не смог записать {auth.state_path('shadowhint')}: {e}"
    when = ("до " + datetime.fromtimestamp(exp, timezone.utc).astimezone()
            .strftime("%d.%m.%Y %H:%M")) if exp else "срок не объявлен"
    return True, f"токен снят с постоянного профиля, жив {when}"


RENEWERS = {"hirehi": renew_hirehi, "careered": renew_careered,
            "shadowhint": renew_shadowhint}


# ──────────────────────────────────────────────────────────────────────────────
# Вход ПАРАЛЛЕЛЬНО обходу
# ──────────────────────────────────────────────────────────────────────────────

class Pending:
    """Входы, которые человек проходит, ПОКА идёт обход остальных площадок.

    Требование владельца 08.08.2026: скрипт сам просит вход, ждёт его и при
    этом не останавливает анализ; пропущенную площадку называет вслух.

    Раньше выбор был из двух плохих: либо войти ДО прогона (человек сидит и
    ждёт, пока ничего не происходит), либо не входить вовсе (площадка молча
    выпадает). Здесь третье — окно открывается сразу, а пул в это время
    обходит всё, что входа не требует.

    🔴 Поток ровно один, и площадки в нём идут ПО ОЧЕРЕДИ. Два окна разом
    делят один постоянный профиль браузера, второе получает ProfileBusy, и в
    покрытии появляется «УПАЛ» у площадки, которая не падала, — тот самый
    ложный статус, из-за которого чинят работающее.
    """

    def __init__(self, platforms: list[str], *, wait: int, browser: str | None):
        self.platforms = list(platforms)
        self.wait, self.browser = wait, browser
        self.results: dict[str, bool] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> "Pending":
        if not self.platforms:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="scout-auth")
        self._thread.start()
        return self

    def _run(self) -> None:
        for name in self.platforms:
            try:
                code = auth.login(name, wait=self.wait, browser=self.browser,
                                  force=True)
            except Exception as e:  # noqa: BLE001 — вход не повод рвать обход
                print(f"вход {name}: не вышло — {type(e).__name__}: {e}",
                      file=sys.stderr)
                code = 1
            self.results[name] = code == 0

    def join(self, timeout: float | None = None) -> None:
        """Дождаться входов. Обход к этому моменту уже отработал своё."""
        if self._thread is not None:
            self._thread.join(timeout)
            forget()          # состояние сессий изменилось — пробы устарели

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def needs_human(names: list[str], *, cookies_from: str | None = None) -> list[str]:
    """Из запрошенных источников — те, кому нужен вход человека ПРЯМО СЕЙЧАС.

    Отсекаются трое, и каждый по своей причине:

    * кто и так жив — окно ему открывать незачем;
    * кто продлевается сам (`can_renew`) — это делается без человека и быстрее;
    * кому вход не нужен вовсе.

    Порядок сохраняется из `ORDER`: сначала те, без кого волна станет неполной.
    """
    want = set(names)
    out = []
    for row in preflight(cookies_from):
        if row["platform"] not in want:
            continue
        if row["state"] != "anonymous" or row["renewable"]:
            continue
        out.append(row["platform"])
    return out


def renew(platforms: list[str] | None = None, *, browser: str | None = None,
          cookies_from: str | None = None, from_browser: str | None = None) -> int:
    """`scout auth refresh [площадка…]` — поднять что поднимается, назвать остальное.

    `from_browser` сперва забирает свежий вход человека из его повседневного
    браузера (см. `adopt_from_browser`), и только потом продлевает. Порядок
    важен: продлевать заведомо мёртвый токен бессмысленно, а забрать живой —
    ровно то, что просил владелец.

    Коды: 0 — после прогона живо всё, что вообще может быть живо; 1 — осталось
    то, что без человека не поднять; 2 — названа неизвестная площадка.
    """
    forget()  # состояние сейчас и станет другим — старые пробы врали бы
    wanted = platforms or list(RENEWERS)
    unknown = [p for p in wanted if p not in auth.PLATFORMS]
    if unknown:
        print(f"не знаю площадку: {', '.join(unknown)}; есть: "
              f"{', '.join(auth.PLATFORMS)}", file=sys.stderr)
        return 2

    if from_browser:
        for platform in wanted:
            if not (auth.PLATFORMS.get(platform) or {}).get("state_cookie"):
                continue
            ok, why = adopt_from_browser(platform, from_browser)
            print(f"{platform}: {'забрано из браузера' if ok else 'НЕ забрано'} — {why}")

    left: list[str] = []
    for platform in wanted:
        renewer = RENEWERS.get(platform)
        if not renewer:
            state, why = auth.session_probe(platform, cookies_from=cookies_from)
            if state == "logged_in":
                print(f"{platform}: жив — {why}")
            else:
                print(f"{platform}: продлевать нечем — {why}")
                left.append(platform)
            continue
        kwargs = {"browser": browser} if platform == "careered" else {}
        ok, why = renewer(**kwargs)
        print(f"{platform}: {'продлено' if ok else 'НЕ продлено'} — {why}")
        if not ok:
            left.append(platform)

    if left:
        print(f"\nбез твоего входа не поднять: {', '.join(left)}", file=sys.stderr)
        return 1
    return 0
