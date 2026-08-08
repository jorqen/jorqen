"""Площадки с официальным API и БЕСПЛАТНЫМ ключом.

Зачем отдельный модуль. У всех остальных источников ключа нет вовсе: они либо
анонимны (`sources.py`), либо ходят сессией пользователя (`sources_auth.py`).
Здесь другой договор — площадка сама выдаёт ключ и разрешает читать выдачу, и из
этого следует поведение, которого нет ни у кого: **источник без ключа не падает
и не притворяется отработавшим, а объявляет себя выключенным**.

Почему это важнее, чем кажется. «Упал» гонит человека чинить код, которого не
сломано; «ok, найдено 0» — худшее из трёх, потому что выглядит как проверенная
площадка, на которой ничего не нашлось. Правда же ровно одна: площадку НЕ
СПРАШИВАЛИ, и включается она одним файлом в `.auth/`. Поэтому каждый адаптер
здесь возвращает служебную сводку с одной строкой «ВЫКЛЮЧЕН: нет ключа в … »
и тем же текстом помечается в строке покрытия.

Ключи заводит владелец сам — код их не создаёт, не регистрирует и никуда не
отправляет. Формат — тот же `KEY=VALUE`, что у `hh.env` и `gmail.env`, разбор
буквально тот же (`hhapi.read_env`, включая починку прав до 0600: в файле лежит
предъявительский секрет). Нет файла — это ШТАТНЫЙ случай, а не ошибка.

Что замерено живьём 05.08.2026 (без единого своего ключа):

  * **SuperJob** — без заголовка `X-Api-App-Id` отвечает HTML-заглушкой WAF
    (403, «IP … country code: GB»), то есть выглядит как упавший сайт. С любым
    заголовком, даже мусорным, — честный JSON `{"error":{"code":403,
    "message":"Приложение с переданным ключом не найдено"}}`. Вывод: сам API из-за
    границы доступен, и «403» в отчёте будет означать ровно то, что написано,
    а не географию.
  * **Adzuna** — без ключей 400 и HTML-страница «Uh oh, something isn't right»,
    то есть ответ вообще не JSON.
  * **Jooble** — без валидного ключа отдаёт Cloudflare-челлендж («Just a
    moment…»), который наш слой сети честно объявит антибот-стеной. Это ещё
    один довод не ходить туда без ключа: стена в отчёте — ложная тревога.
  * **Careerjet** — единственный, кого удалось проверить целиком: `affid` из
    примеров документации + обязательный заголовок `Referer` дают 200 и живую
    выдачу (по «golang» — 153 вакансии в `en_GB`, 318 в `ru_RU`). Без `Referer`
    площадка отвечает `{"error":"Undeclared referrer…"}`, без `user_ip` —
    `{"type":"ERROR"}`; оба поля обязательны, и об этом нет ни слова в тех
    примерах, которые чаще всего копируют.

Чего здесь принципиально НЕТ, как и у соседей: отбора по релевантности, стоп-слов
и склейки дублей. Сборщик приносит то, что отдала площадка.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from .auth import AUTH_DIR
from .hhapi import read_env  # тот же разбор KEY=VALUE и та же починка прав 0600
from .model import Vacancy, _amount, salary_str
from .net import UA, FetchError, fetch_json, qs
from .sources import (
    ATS_ROLE_RE,
    Ctx,
    Tally,
    _cutoff,
    _older_than,
    _page_budget,
    _pause,
    _salary_with_period,
    _strip_tags,
    _truncated_note,
    parse_salary,
    period_from_text,
)

# ──────────────────────────────────────────────────────────────────────────────
# Ключи: где лежат, чего не хватает, как включить
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Keyed:
    """Паспорт площадки с ключом.

    Одна структура на все четыре, потому что различаются они только именами
    переменных и адресом, где ключ дают. Всё остальное — путь в `.auth/`, разбор,
    поведение без ключа, текст «как включить» — обязано совпадать до буквы:
    четыре разных формулировки одного и того же означают, что три из них рано
    или поздно разойдутся с кодом.
    """

    name: str
    env_file: str
    required: tuple[str, ...]
    where: str                 # откуда берётся ключ — одной фразой, без воды
    free: str = "бесплатный"

    @property
    def path(self) -> str:
        return os.path.join(AUTH_DIR, self.env_file)

    def howto(self) -> str:
        """Одна строка: выключен, почему и что сделать. Печатается в сводке.

        Первые сорок символов несут главное намеренно: в таблице покрытия
        примечание режется по ширине колонки, и «ВЫКЛЮЧЕН: нет ключа …» обязано
        дожить до экрана целиком.
        """
        return (f"ВЫКЛЮЧЕН: нет ключа в {self.path} "
                f"({'/'.join(self.required)}). Ключ {self.free}: {self.where}. "
                f"Файл заводишь ты, права 0600; .auth/ из репозитория не уезжает.")


PLATFORMS: dict[str, Keyed] = {
    # Российский рынок и прямой конкурент hh — самый ценный из четырёх.
    "superjob": Keyed(
        name="superjob", env_file="superjob.env", required=("SUPERJOB_APP_ID",),
        where="api.superjob.ru → регистрация приложения → «Secret key»"),
    "adzuna": Keyed(
        name="adzuna", env_file="adzuna.env",
        required=("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
        where="developer.adzuna.com → регистрация → App ID и App Key"),
    "jooble": Keyed(
        name="jooble", env_file="jooble.env", required=("JOOBLE_API_KEY",),
        where="jooble.org/api/about → ключ приходит на почту по заявке"),
    "careerjet": Keyed(
        name="careerjet", env_file="careerjet.env", required=("CAREERJET_AFFID",),
        where="careerjet.com/partners → affiliate id"),
}


def keys(platform: str, env: dict | None = None) -> dict[str, str] | None:
    """Ключи площадки или None, если их нет.

    `env` передают тесты и вызывающий, который уже прочитал файл сам; None
    означает «сходи на диск». Пустой словарь — это ЯВНОЕ «ключей нет», а не
    «прочитай за меня»: ровно на этом различии у hh ломался выбор между API и
    разбором HTML.

    🔴 Когда идём на диск, ОКРУЖЕНИЕ ПРОЦЕССА бьёт файл. Это не удобство, а
    единственный способ отдать ключи облачной рутине: в облаке чекаут
    публичного репозитория и больше ничего — `.auth/` туда не уезжает и не
    уедет (инвариант 4). Ключ площадки при этом не равен сессионной куке: он
    даёт чтение публичного каталога вакансий, а не доступ к аккаунту, и
    отзывается в кабинете площадки.

    Берутся все переменные с префиксом площадки, а не только обязательные:
    у adzuna есть необязательный `ADZUNA_COUNTRIES`, и «ключи отдали, а
    настройку нет» было бы тихим сужением обхода.
    """
    p = PLATFORMS[platform]
    if env is None:
        prefix = platform.upper() + "_"
        e = dict(read_env(p.path) or {})
        e.update({k: v for k, v in os.environ.items()
                  if k.startswith(prefix) and v})
    else:
        e = env
    if not e or not all(e.get(k) for k in p.required):
        return None
    return e


def disabled_row(platform: str) -> Vacancy:
    """Служебная сводка выключенного источника — та самая одна честная строка."""
    tally = Tally(platform)
    tally.note(PLATFORMS[platform].howto())
    return tally.row()


def configured() -> dict[str, bool]:
    """{площадка: есть ли ключ}. Нужна отчётам и README-подсказкам, не адаптерам."""
    return {name: keys(name) is not None for name in PLATFORMS}


# ──────────────────────────────────────────────────────────────────────────────
# Общее для всех четырёх
# ──────────────────────────────────────────────────────────────────────────────


def _parsed_or_die(tally: Tally, where: str) -> None:
    """Площадка отдала строки, а разобралось ноль — это сломанный парсер.

    Порог тоньше, чем у анонимных досок (`src_himalayas` падает на пустой
    выдаче): здесь у всех четырёх есть НАСТОЯЩИЙ серверный поиск по словам, и
    «по такой формулировке ничего нет» — законный ответ, а не поломка. Ложью
    является только другое: строки приехали, а в выдачу не попала ни одна.
    """
    if tally.offered and not tally.parsed:
        raise FetchError(where, f"площадка отдала {tally.offered} строк, а разобралось "
                                f"ноль — формат ответа поехал, парсер отстал")


def _keep(title: str, ctx: Ctx, tally: Tally) -> bool:
    """Фильтр профессии для агрегаторов. True — строку берём.

    Применяется там, где серверный поиск ищет по ВСЕМУ тексту объявления
    (Adzuna, Jooble, Careerjet): «golang» в теле вакансии рекрутёра — это не
    вакансия для нас. У SuperJob поиск по названию точный, и там фильтра нет —
    иначе мы бы фильтровали уже отфильтрованное и теряли живые названия.
    """
    if ctx.ats_all or ATS_ROLE_RE.search(title or ""):
        return True
    tally.skipped_profile += 1
    return False


def _int(value) -> int | None:
    """Число из строки или числа; 0 — это «не указано», а не «платят ноль».

    Ровно та же функция, что разбирает деньги у самой `Vacancy`: Careerjet
    отдаёт вилку СТРОКАМИ («127500»), SuperJob — числами с нулём вместо пропуска,
    и второй разбор чисел в проекте означал бы, что однажды они разойдутся.
    """
    return _amount(value)


# ──────────────────────────────────────────────────────────────────────────────
# SuperJob — api.superjob.ru, ключ приложения
# ──────────────────────────────────────────────────────────────────────────────

SUPERJOB_API = "https://api.superjob.ru/2.0/vacancies/"
SUPERJOB_PAGE = 100       # серверный потолок count
SUPERJOB_MAX_PAGES = 20   # 2000 вакансий на формулировку — предохранитель

# `period` площадка принимает НЕ произвольным числом дней, а из своего списка:
# 0 (за всё время), 1, 3, 7. Поэтому окно сначала округляется ВВЕРХ до ближайшего
# разрешённого (взять больше и дорезать у себя — потеря нулевая), а `--days 30`
# уходит нулём: лучше принести лишнее и отсечь по дате, чем молча сузить окно
# до недели и отдать это за месяц.
SUPERJOB_PERIODS = (1, 3, 7)

SUPERJOB_NOTE = ("--days округляется вверх до 1/3/7 суток (других значений API "
                 "не принимает) и дорезается по дате публикации у нас")


def _superjob_period(days: int) -> int:
    for p in SUPERJOB_PERIODS:
        if days <= p:
            return p
    return 0


def src_superjob(ctx: Ctx, env: dict | None = None) -> list[Vacancy]:
    """SuperJob — второй по плотности российский сайт после hh.

    Ключ приложения ставится ЗАГОЛОВКОМ `X-Api-App-Id`, а не параметром запроса,
    и это не мелочь: без заголовка запрос до API вообще не доходит — отвечает
    WAF HTML-страницей с 403, которую легко принять за падение площадки
    (проверено живьём: с мусорным заголовком приходит опрятный JSON про
    ненайденное приложение, без заголовка — заглушка).

    Города НЕ передаём. `ctx.area` — это идентификатор региона hh (113 = Россия),
    а у SuperJob своя нумерация (4 — Москва, 14 — Петербург), и подставить одно
    вместо другого значит тихо искать не в том городе. Без `town` поиск идёт по
    всей стране, что нам и нужно.

    Фильтра профессии у нас нет: `keyword` — настоящий серверный поиск по
    названию, тот же случай, что у hh и jobicy.
    """
    k = keys("superjob", env)
    if not k:
        return [disabled_row("superjob")]

    headers = {"X-Api-App-Id": k["SUPERJOB_APP_ID"], "User-Agent": UA}
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("superjob")
    edge = _cutoff(ctx.days)
    budget = _page_budget(ctx, SUPERJOB_PAGE, SUPERJOB_MAX_PAGES)
    for q in ctx.queries():
        total: int | None = None
        for page in range(budget):
            if tally.requests:
                _pause()
            data = fetch_json(qs(SUPERJOB_API, {
                "keyword": q, "count": SUPERJOB_PAGE, "page": page,
                "period": _superjob_period(ctx.days),
                "order_field": "date", "order_direction": "desc",
            }), headers=headers)
            tally.requests += 1
            rows = data.get("objects") or []
            if total is None:
                total = data.get("total")
            if not rows:
                break
            tally.pages += 1
            _superjob_rows(rows, edge, out, seen, tally)
            if not data.get("more"):
                break
        else:
            if total and total > budget * SUPERJOB_PAGE:
                tally.note(_truncated_note(f"superjob «{q}»",
                                           budget * SUPERJOB_PAGE, total))
    _parsed_or_die(tally, SUPERJOB_API)
    tally.note("поиск серверный (keyword=), фильтр профессии не применяется; "
               "период вилки API не называет — суффикса у неё нет")
    out.append(tally.row())
    return out


def _superjob_rows(rows: list, edge, out: list, seen: set, tally: Tally) -> None:
    """Строка выдачи SuperJob → Vacancy. Вынесено ради теста на маппинг."""
    for j in rows:
        tally.offered += 1
        vid = str(j.get("id") or "")
        link = j.get("link") or ""
        if not vid or not link:
            # Ссылку НЕ достраиваем по id: постоянный адрес вакансии на SuperJob
            # содержит транслит названия, и собранный из id url ведёт в никуда.
            # Строка без адреса бесполезна, но обязана быть посчитана.
            tally.dropped += 1
            continue
        if vid in seen:
            tally.dupes += 1
            continue
        seen.add(vid)
        if j.get("is_closed"):
            # Закрытая вакансия — не вакансия. Отдельный счётчик, потому что
            # «отклик некуда слать» и «мы её потеряли» — разные вещи.
            tally.skipped_kind += 1
            continue
        tally.parsed += 1
        published = j.get("date_published")
        if _older_than(_iso_or_none(published), edge):
            tally.skipped_old += 1
            continue
        tally.kept += 1
        place = (j.get("place_of_work") or {}).get("title") or ""
        town = (j.get("town") or {}).get("title")
        desc = " ".join(_strip_tags(str(x)) for x in
                        (j.get("candidat"), j.get("work"), j.get("compensation")) if x)
        out.append(Vacancy(
            source="superjob",
            external_id=vid,
            url=link,
            title=j.get("profession") or "",
            company=j.get("firm_name") or (j.get("client") or {}).get("title"),
            # Ноль у SuperJob означает «не указано» — ровно как у careered
            # и trudvsem. Период вилки площадка не называет вовсе, и выдумывать
            # «в месяц» нельзя: суффикс в отчёте читается как факт площадки.
            salary_from=_int(j.get("payment_from")),
            salary_to=_int(j.get("payment_to")),
            currency=j.get("currency"),
            location=town,
            remote=_superjob_remote(place),
            published_at=published,
            tags=[x for x in ((j.get("type_of_work") or {}).get("title"),
                              (j.get("experience") or {}).get("title")) if x],
            description=desc or None,
            raw={"place_of_work": place or None,
                 "education": (j.get("education") or {}).get("title"),
                 "agreement": j.get("agreement"),
                 "client": (j.get("client") or {}).get("title"),
                 "id_client": j.get("id_client"),
                 "address": j.get("address"),
                 "note": "период вилки SuperJob в API не называет"},
        ))


def _superjob_remote(place: str) -> bool | None:
    """`place_of_work.title` → удалёнка. Пусто — None, а не False.

    «Площадка не сказала» и «сказала, что офис» — разные факты, и второй нельзя
    получать из первого: карточка с `remote=False` читается как проверенная.
    """
    if not place:
        return None
    low = place.lower()
    return bool("дом" in low or "удал" in low or "remote" in low)


def _iso_or_none(value):
    """Unix-время SuperJob → ISO. Отдельной функцией, чтобы `_older_than`
    сравнивал одно и то же, а не «как приехало»."""
    from .model import _iso  # noqa: PLC0415 — тот же разбор, что у самой Vacancy
    return _iso(value)


# ──────────────────────────────────────────────────────────────────────────────
# Adzuna — api.adzuna.com, app_id + app_key
# ──────────────────────────────────────────────────────────────────────────────

ADZUNA_API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
ADZUNA_PAGE = 50          # серверный потолок results_per_page
ADZUNA_MAX_PAGES = 5

# Страна — часть ПУТИ, а не параметр, и запрос всегда однонациональный. Умолчание
# короткое сознательно: каждая страна умножает число запросов на число
# формулировок, а «взять все девятнадцать» — это чужой бесплатный ключ,
# израсходованный на Мексику и Бразилию. Список меняется одной строкой
# ADZUNA_COUNTRIES в .auth/adzuna.env.
ADZUNA_COUNTRIES = ("gb", "de", "nl", "pl")

# Своего поля валюты в ответе нет — она следует из страны запроса. Это не
# догадка о вакансии, а свойство эндпоинта, поэтому подставлять честно.
ADZUNA_CURRENCY = {
    "gb": "GBP", "us": "USD", "ca": "CAD", "au": "AUD", "nz": "NZD",
    "de": "EUR", "at": "EUR", "nl": "EUR", "be": "EUR", "fr": "EUR",
    "es": "EUR", "it": "EUR", "pl": "PLN", "ch": "CHF", "sg": "SGD",
    "in": "INR", "za": "ZAR", "br": "BRL", "mx": "MXN",
}

ADZUNA_NOTE = "--days уходит в API параметром max_days_old"


def src_adzuna(ctx: Ctx, env: dict | None = None) -> list[Vacancy]:
    """Adzuna — агрегатор по 19 странам, из России вакансий не даёт.

    Ценность в другом: это релокационная выдача с настоящим окном по дате
    (`max_days_old`) — редкость среди агрегаторов.

    Две ловушки, из-за которых цифры в колонке «деньги» были бы враньём:

      * **`salary_is_predicted=1` — это оценка Adzuna, а не число работодателя.**
        Такая вилка в поля НЕ идёт: предсказание, напечатанное рядом с реальными
        вилками, неотличимо от факта. Оно уезжает в описание словами и в `raw`.
      * **Периода у вилки нет вовсе.** Документация про годовые суммы молчит,
        поэтому суффикса нет — по правилу модели это и означает «период
        неизвестен».

    Ответ приходит JSON только при `content-type=application/json`; без него
    Adzuna отдаёт XML, и разбор падает на первом же символе.
    """
    k = keys("adzuna", env)
    if not k:
        return [disabled_row("adzuna")]

    countries = _adzuna_countries(k)
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("adzuna")
    budget = _page_budget(ctx, ADZUNA_PAGE, ADZUNA_MAX_PAGES)
    for country in countries:
        for q in ctx.queries():
            for page in range(budget):
                if tally.requests:
                    _pause()
                url = ADZUNA_API.format(country=country, page=page + 1)
                data = fetch_json(qs(url, {
                    "app_id": k["ADZUNA_APP_ID"], "app_key": k["ADZUNA_APP_KEY"],
                    "results_per_page": ADZUNA_PAGE, "what": q,
                    "max_days_old": max(ctx.days, 1), "sort_by": "date",
                    "content-type": "application/json",
                }))
                tally.requests += 1
                rows = data.get("results") or []
                if not rows:
                    break
                tally.pages += 1
                _adzuna_rows(rows, country, ctx, out, seen, tally)
                if len(rows) < ADZUNA_PAGE:
                    break
            else:
                tally.note(_truncated_note(f"adzuna {country} «{q}»",
                                           budget * ADZUNA_PAGE, None))
    _parsed_or_die(tally, ADZUNA_API.format(country=",".join(countries), page=1))
    tally.note(f"страны: {', '.join(countries)} (меняются ADZUNA_COUNTRIES); "
               "предсказанные площадкой вилки в поля не идут; "
               "период вилки API не называет")
    out.append(tally.row())
    return out


def _adzuna_countries(env: dict) -> tuple[str, ...]:
    """Список стран из env или умолчание. Мусор молча не глотаем — только
    известные коды, иначе первый же опечатанный `gbr` даёт 404 на всю страну."""
    raw = (env.get("ADZUNA_COUNTRIES") or "").lower()
    picked = tuple(c.strip() for c in raw.split(",") if c.strip() in ADZUNA_CURRENCY)
    return picked or ADZUNA_COUNTRIES


def _adzuna_rows(rows: list, country: str, ctx: Ctx, out: list, seen: set,
                 tally: Tally) -> None:
    for j in rows:
        tally.offered += 1
        vid = str(j.get("id") or "")
        url = j.get("redirect_url") or ""
        if not vid or not url:
            tally.dropped += 1
            continue
        # id уникален внутри страны, а не глобально: одна и та же вакансия в двух
        # странах — две разные записи, и склеивать их по id нельзя.
        key = f"{country}-{vid}"
        if key in seen:
            tally.dupes += 1
            continue
        seen.add(key)
        tally.parsed += 1
        title = j.get("title") or ""
        if not _keep(title, ctx, tally):
            continue
        tally.kept += 1
        predicted = str(j.get("salary_is_predicted") or "0") in ("1", "true", "True")
        lo, hi = _int(j.get("salary_min")), _int(j.get("salary_max"))
        cur = ADZUNA_CURRENCY.get(country)
        guess = ""
        if predicted and (lo or hi):
            guess = (f"Adzuna ОЦЕНИВАЕТ вилку в {salary_str(lo, hi, cur)} — "
                     f"это её расчёт, а не число работодателя; в поля не переношу.")
            lo = hi = None
        area = [str(x) for x in ((j.get("location") or {}).get("area") or []) if x]
        out.append(Vacancy(
            source="adzuna",
            external_id=key,
            url=url,
            title=title,
            company=(j.get("company") or {}).get("display_name"),
            salary_from=lo, salary_to=hi, currency=cur if (lo or hi) else None,
            location=(j.get("location") or {}).get("display_name"),
            published_at=j.get("created"),
            tags=[x for x in (j.get("contract_time"), j.get("contract_type"),
                              (j.get("category") or {}).get("label")) if x],
            description=" ".join(x for x in (guess, _strip_tags(j.get("description") or ""))
                                 if x) or None,
            raw={"country": country, "area": area,
                 "salary_is_predicted": predicted,
                 "category": (j.get("category") or {}).get("tag"),
                 "note": "валюта выведена из страны запроса — своего поля у API нет"},
        ))


# ──────────────────────────────────────────────────────────────────────────────
# Jooble — jooble.org/api/{key}
# ──────────────────────────────────────────────────────────────────────────────

JOOBLE_HOST = "jooble.org"
# ⚠️ 100 — ПОТОЛОК страницы, и просить больше не «столько же, но зря», а ХУЖЕ:
# замер 07.08.2026 — ResultOnPage=500 отдаёт 20 строк, то есть в пять раз меньше
# сотни. Сервер молча заменяет негодное значение своим умолчанием, и «попросил
# больше — получил меньше» выглядит как обеднение выдачи, а не как ошибка вызова.
JOOBLE_PAGE = 100
# Серверный потолок — 1052 строки на формулировку (замер 07.08.2026: страницы
# 1–10 полные, 11-я отдаёт 52, 12-я и дальше пусты) при totalCount 21 584.
# То есть глубже не пустят, сколько ни проси, и это не наше ограничение.
# Раньше здесь стояло 5 — половина достижимого отбрасывалась молча.
# Цена подъёма — запросы: ключ живёт на 500 запросов в сутки, одна волна на трёх
# формулировках берёт 33 из них. Это тот размен, который стоит делать.
JOOBLE_MAX_PAGES = 11

JOOBLE_NOTE = "--days режется у нас: окна по дате у API нет"


def src_jooble(ctx: Ctx, env: dict | None = None) -> list[Vacancy]:
    """Jooble — агрегатор с POST-API, ключ живёт В ПУТИ запроса.

    Из-за этого здесь есть то, чего нет ни у кого: **маскирование адреса в
    ошибках**. `FetchError` кладёт URL в текст исключения, а текст уезжает в
    отчёт, в базу прогонов и в терминал — и вместе с ним уехал бы ключ. Поэтому
    любая осечка пересобирается с адресом `…/api/***`.

    Вилка приезжает СТРОКОЙ («от 250 000 руб.»), поэтому разбирается тем же
    `parse_salary`, что и текстовые площадки, а период — `period_from_text`:
    нет слова про период — нет суффикса.

    Окно по дате API не поддерживает, режем по `updated` у себя.
    """
    k = keys("jooble", env)
    if not k:
        return [disabled_row("jooble")]

    host = k.get("JOOBLE_HOST") or JOOBLE_HOST
    url = f"https://{host}/api/{k['JOOBLE_API_KEY']}"
    masked = f"https://{host}/api/***"
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("jooble")
    edge = _cutoff(ctx.days)
    budget = _page_budget(ctx, JOOBLE_PAGE, JOOBLE_MAX_PAGES)
    for q in ctx.queries():
        total: int | None = None
        got = 0
        for page in range(budget):
            if tally.requests:
                _pause()
            body = {"keywords": q, "page": str(page + 1),
                    "ResultOnPage": JOOBLE_PAGE}
            if k.get("JOOBLE_LOCATION"):
                body["location"] = k["JOOBLE_LOCATION"]
            data = _jooble_call(url, masked, body)
            tally.requests += 1
            rows = data.get("jobs") or []
            if total is None:
                total = data.get("totalCount")
            if not rows:
                break
            tally.pages += 1
            got += len(rows)
            _jooble_rows(rows, edge, ctx, out, seen, tally)
            # Конец обхода определяется по `totalCount`, а НЕ по «страница
            # пришла неполной». `ResultOnPage` нигде не описан потолком, и если
            # сервер молча урежет сотню до двадцати, признак «меньше, чем
            # просили» объявит конец выдачи на первой же странице — и площадка
            # отдаст пятую часть себя, выглядя при этом полностью обойдённой.
            if total is not None and got >= total:
                break
        else:
            tally.note(_truncated_note(f"jooble «{q}»", got, total))
    _parsed_or_die(tally, masked)
    tally.note("вилка приезжает строкой — разобрана текстовым парсером; "
               "окна по дате у API нет, режем по updated")
    out.append(tally.row())
    return out


def _jooble_call(url: str, masked: str, body: dict) -> dict:
    """POST с ключом в пути. Любая ошибка пересобирается БЕЗ ключа в адресе.

    `type(e)` сохраняется намеренно: `BlockedError` — это «стена», и подменить
    её обычной `FetchError` значит превратить «зайди руками» в «сломался код».
    `from None` — чтобы исходное исключение (в тексте которого лежит ключ) не
    приехало прицепом в traceback.
    """
    try:
        return fetch_json(url, method="POST", data=body)
    except FetchError as e:
        raise type(e)(masked, e.reason, e.status) from None


def _jooble_rows(rows: list, edge, ctx: Ctx, out: list, seen: set,
                 tally: Tally) -> None:
    for j in rows:
        tally.offered += 1
        vid = str(j.get("id") or "")
        link = j.get("link") or ""
        if not (vid or link):
            tally.dropped += 1
            continue
        key = vid or link
        if key in seen:
            tally.dupes += 1
            continue
        seen.add(key)
        tally.parsed += 1
        if _older_than(_iso_or_none(j.get("updated")), edge):
            tally.skipped_old += 1
            continue
        title = j.get("title") or ""
        if not _keep(title, ctx, tally):
            continue
        tally.kept += 1
        money = j.get("salary") or ""
        lo, hi, cur, gross = parse_salary(money)
        out.append(Vacancy(
            source="jooble",
            external_id=key,
            url=link,
            title=title,
            company=j.get("company"),
            salary_from=lo, salary_to=hi, currency=cur, salary_gross=gross,
            salary_period=period_from_text(money),
            location=j.get("location"),
            published_at=j.get("updated"),
            tags=[x for x in (j.get("type"),) if x],
            description=_strip_tags(j.get("snippet") or "") or None,
            # `source` у Jooble — это ДОСКА, с которой он взял объявление, а не
            # работодатель. Кладём в raw: в ресёрче это первый шаг к прямому
            # контакту, но выдать его за компанию нельзя.
            raw={"board": j.get("source"), "salary_text": money or None},
        ))


# ──────────────────────────────────────────────────────────────────────────────
# Careerjet — public.api.careerjet.net, affiliate id
# ──────────────────────────────────────────────────────────────────────────────

CAREERJET_API = "http://public.api.careerjet.net/search"
CAREERJET_PAGE = 99       # 100 и больше сервер молча трактует как 20
CAREERJET_MAX_PAGES = 5

# Локаль решает, ЧЬЮ выдачу мы видим: замер 05.08.2026 по «golang» — en_GB 153
# вакансии, ru_RU 318 (там же Сбербанк и «Россия» в locations). Поэтому по
# умолчанию обе, а не одна.
CAREERJET_LOCALES = ("ru_RU", "en_GB")

# Наш собственный запрос, а не переход посетителя: подставлять сюда чей-то
# реальный адрес незачем и нечестно, поэтому loopback. Пустым оставить нельзя —
# без user_ip API отвечает `{"type":"ERROR"}` (проверено).
CAREERJET_IP = "127.0.0.1"

# affid из ПРИМЕРОВ документации. Живёт здесь ровно для диагностики (`probe`) и
# теста: сделать его умолчанием нельзя — переходы засчитывались бы чужому
# партнёрскому аккаунту, а это подлог, пусть и без выгоды.
CAREERJET_DOC_AFFID = "213e213hd12344567"

# Период вилки Careerjet кодирует буквой. W (неделя) и D (день) честной подписи
# в модели не имеют, поэтому такие вилки в поля не идут — их разворачивает
# `_salary_with_period` словами в описание.
CAREERJET_PERIOD = {"Y": "yearly", "M": "monthly", "H": "hourly",
                    "W": "weekly", "D": "daily"}

CAREERJET_NOTE = "--days режется у нас: у API есть только сортировка по дате"


def src_careerjet(ctx: Ctx, env: dict | None = None) -> list[Vacancy]:
    """Careerjet — метапоиск по чужим доскам, включая российские.

    Три вещи, которых нет в копируемых примерах и без которых источник молча
    пуст (все проверены живьём 05.08.2026):

      1. **Заголовок `Referer` обязателен.** Без него — 403 и
         `{"error":"Undeclared referrer…"}`, то есть ноль вакансий при HTTP 200
         у соседних вызовов.
      2. **`user_ip` и `user_agent` обязательны.** Без `user_ip` ответ
         вырождается в `{"type":"ERROR"}` — снова ноль без объяснения.
      3. **HTTPS у этого эндпоинта НЕТ** (порт 443 закрыт). Поэтому запрос идёт
         по http: в нём нет ни пароля, ни токена — только поисковая строка и
         affiliate id, — но знать об этом надо.

    Своего `id` у вакансии нет: ключом служит хвост ссылки-редиректа
    (`jobviewtrack.com/v2/<token>`). Токен проверен на повторяемость — два
    подряд запроса дают тот же адрес; если он однажды начнёт крутиться, вакансии
    поедут как новые, и поймает это `dup_key` по паре «компания + название».
    """
    k = keys("careerjet", env)
    if not k:
        return [disabled_row("careerjet")]

    affid = k["CAREERJET_AFFID"]
    locales = tuple(x.strip() for x in (k.get("CAREERJET_LOCALES") or "").split(",")
                    if x.strip()) or CAREERJET_LOCALES
    # Referer площадка требует, чтобы понимать, кто зовёт. Сайта у нас нет,
    # поэтому — тот адрес, который владелец сам укажет, иначе локальный.
    referer = k.get("CAREERJET_URL") or "http://localhost/"
    out: list[Vacancy] = []
    seen: set[str] = set()
    tally = Tally("careerjet")
    edge = _cutoff(ctx.days)
    budget = _page_budget(ctx, CAREERJET_PAGE, CAREERJET_MAX_PAGES)
    for locale in locales:
        for q in ctx.queries():
            hits: int | None = None
            for page in range(budget):
                if tally.requests:
                    _pause()
                data = fetch_json(qs(CAREERJET_API, {
                    "keywords": q, "affid": affid, "locale_code": locale,
                    "user_ip": CAREERJET_IP, "user_agent": UA, "url": referer,
                    "pagesize": CAREERJET_PAGE, "page": page + 1, "sort": "date",
                }), headers={"Referer": referer})
                tally.requests += 1
                if str(data.get("type")) != "JOBS":
                    # ERROR при HTTP 200 — фирменная манера площадки. Молча
                    # прервать обход здесь значит отдать «ноль вакансий».
                    raise FetchError(CAREERJET_API,
                                     f"ответ типа {data.get('type')!r}: "
                                     f"{str(data.get('error') or data)[:200]}")
                rows = data.get("jobs") or []
                hits = data.get("hits") if hits is None else hits
                if not rows:
                    break
                tally.pages += 1
                _careerjet_rows(rows, locale, edge, ctx, out, seen, tally)
                if page + 1 >= (data.get("pages") or 1):
                    break
            else:
                tally.note(_truncated_note(f"careerjet {locale} «{q}»",
                                           budget * CAREERJET_PAGE, hits))
    _parsed_or_die(tally, CAREERJET_API)
    tally.note(f"локали: {', '.join(locales)} (меняются CAREERJET_LOCALES); "
               f"эндпоинт только по http — HTTPS у него нет")
    out.append(tally.row())
    return out


def _careerjet_rows(rows: list, locale: str, edge, ctx: Ctx, out: list, seen: set,
                    tally: Tally) -> None:
    for j in rows:
        tally.offered += 1
        url = j.get("url") or ""
        if not url:
            tally.dropped += 1
            continue
        vid = url.rstrip("/").rsplit("/", 1)[-1]
        if vid in seen:
            tally.dupes += 1
            continue
        seen.add(vid)
        tally.parsed += 1
        published = _careerjet_date(j.get("date"))
        if _older_than(published, edge):
            tally.skipped_old += 1
            continue
        title = j.get("title") or ""
        if not _keep(title, ctx, tally):
            continue
        tally.kept += 1
        lo, hi, cur, period, money_note = _salary_with_period(
            _int(j.get("salary_min")), _int(j.get("salary_max")),
            j.get("salary_currency_code"),
            CAREERJET_PERIOD.get(str(j.get("salary_type") or "").upper()))
        excerpt = _strip_tags(j.get("description") or "")
        out.append(Vacancy(
            source="careerjet",
            external_id=vid,
            url=url,
            title=title,
            company=j.get("company"),
            salary_from=lo, salary_to=hi, currency=cur, salary_period=period,
            location=j.get("locations"),
            published_at=published,
            description=" ".join(x for x in (money_note, excerpt) if x) or None,
            raw={"locale": locale, "salary_text": j.get("salary") or None,
                 "salary_type": j.get("salary_type"),
                 # `site` — доска, с которой Careerjet взял объявление. В живой
                 # выдаче он почти всегда пуст, но когда не пуст — это шаг
                 # к прямому контакту, и терять его незачем.
                 "board": j.get("site") or None},
        ))


def _careerjet_date(value) -> str | None:
    """Дата Careerjet → ISO.

    Формат — RFC 2822 («Wed, 05 Aug 2026 05:56:32 GMT»), и общий `model._iso`
    его НЕ понимает: возвращает None, потому что ни `fromisoformat`, ни запасной
    поиск `YYYY-MM-DD` на такую строку не срабатывают. Без этой функции у всей
    выдачи Careerjet не было бы даты — а значит, и окно `--days` резало бы
    вслепую (точнее, не резало бы вовсе).
    """
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    return _iso_or_none(dt)


def probe_careerjet(keywords: str = "golang", locale: str = "en_GB") -> dict:
    """Диагностика без своего ключа: жив ли эндпоинт и что он отдаёт.

    Существует потому, что «источник выключен» и «источник сломан» снаружи
    выглядят одинаково, а проверить второе, не заводя ключ, можно ровно здесь:
    affid из примеров документации площадка принимает (и, судя по замерам, не
    проверяет вовсе). В обход это НЕ подставляется — см. CAREERJET_DOC_AFFID.
    """
    data = fetch_json(qs(CAREERJET_API, {
        "keywords": keywords, "affid": CAREERJET_DOC_AFFID, "locale_code": locale,
        "user_ip": CAREERJET_IP, "user_agent": UA, "pagesize": 5, "page": 1,
    }), headers={"Referer": "http://localhost/"})
    return {"type": data.get("type"), "hits": data.get("hits"),
            "pages": data.get("pages"), "jobs": len(data.get("jobs") or [])}


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

KEYED_SOURCES = {
    "superjob": src_superjob,
    "adzuna": src_adzuna,
    "jooble": src_jooble,
    "careerjet": src_careerjet,
}

# Что за площадка обещает по окну свежести — и, ПОКА КЛЮЧА НЕТ, что она выключена.
# Состояние ключа читается один раз, при первом обращении к реестру, то есть
# в начале прогона: пометка описывает ровно тот прогон, в строке покрытия
# которого печатается.
KEYED_WINDOW_NOTES = {
    "superjob": SUPERJOB_NOTE,
    "adzuna": ADZUNA_NOTE,
    "jooble": JOOBLE_NOTE,
    "careerjet": CAREERJET_NOTE,
}


def source_notes(env_by_platform: dict | None = None) -> dict[str, str]:
    """Пометки источников. Нет ключа — пометка говорит об этом ПЕРВЫМ делом.

    Слово про окно свежести остаётся и у выключенного: человек читает эту строку
    как раз тогда, когда решает, заводить ключ или нет, и «что я с этого получу»
    ему нужнее, чем повтор про отсутствие файла.
    """
    out: dict[str, str] = {}
    for name, window in KEYED_WINDOW_NOTES.items():
        env = (env_by_platform or {}).get(name)
        if keys(name, env) is None:
            p = PLATFORMS[name]
            out[name] = (f"ВЫКЛЮЧЕН: нет ключа {p.env_file} — {p.where}. "
                         f"Когда включат: {window}")
        else:
            out[name] = window
    return out


KEYED_SOURCE_NOTES = source_notes()
