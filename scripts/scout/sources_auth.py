"""Площадки, у которых сессия пользователя что-то решает.

Четыре штуки, и они РАЗНЫЕ по тому, что именно даёт вход. Это главное, что здесь
надо держать в голове, потому что раньше все четыре считались «нужен логин», и
из-за этого сборщик просил вход там, где он бесполезен, и молчал там, где вход
действительно был нужен:

* **shadowhint** — без входа НИЧЕГО: выдача целиком под Bearer, аноним получает
  401 «authorization token is not provided». Зато после входа не нужен ни браузер,
  ни рендер: приложение зеркалит токен в куку `auth_token` на год (не httpOnly),
  и stdlib-слой читает её живьём.
* **hirehi** — без входа ВСЁ: 683 вакансии по go+backend, полные описания, вилки,
  работодатель. Вход добавляет ровно счётчик оставшихся раскрытий прямого контакта
  и сам контакт. Никакого рендера для выдачи не нужно.
* **geekjob** — вход не даёт вообще ничего (сверено: documentsCount совпал один
  в один анонимно и с куками). Просить его — врать про пользу.
* **wantapply** — без входа весь каталог с полными описаниями, но с API-хоста
  `api.wantapply.com`; вход нужен ровно за «Apply on corporate website» — прямой
  ссылкой в ATS работодателя.

Пять ловушек, вокруг которых написан весь модуль:

1. **Молча проигнорированный фильтр.** У wantapply неизвестный ключ в `filters`
   не даёт ошибку, а возвращает ПОЛНЫЙ каталог: `{"query":"golang"}` → 9168
   вместо 94. Выглядит как «нашлось много», а на деле фильтра нет. У hirehi ровно
   то же: `q=golang` → 17563, то есть весь сайт, и точно так же ведёт себя ЛЮБАЯ
   несуществующая подкатегория: `subcategory=devops` отдаёт 4379 — всю категорию
   `development`. Поэтому каждый фильтрованный запрос сверяется с эталоном ровно
   своего уровня (сайт для поиска, категория для подкатегорий), и совпадение
   считается ОШИБКОЙ, а не удачей.
2. **Молча обрезанный limit.** hirehi режет limit до 100 независимо от того, что
   попросили: `limit=500` при total 692 отдаёт ровно 100. shadowhint на
   `per_page=200` отвечает `perPage=20` — то есть просить больше ста ХУЖЕ, чем
   просить сто. Страницы обходятся всегда, и собранное сверяется с заявленным total.
3. **Ротация refresh-токена.** У hirehi refresh-кука ОДНА на браузер пользователя
   и на любой наш заход; `POST /api/auth/refresh` её ротирует, и у того, кто
   обновил не последним, сессия протухает мгновенно — причём симптом обманчивый,
   не «войдите», а 403/аноним. Мы этот POST не делаем НИКОГДА: за выдачей ходим
   анонимно, а редкий счётчик лимитов ПОДСЛУШИВАЕМ в настоящем браузере, где
   приложение обновляет токен само и результат оседает в постоянном профиле.
4. **Одна формулировка вместо набора.** Ни у одной из четырёх площадок поиск не
   является тегом: это полнотекст по названию, и наборы по разным словам НЕ
   вложены друг в друга. Замер 30.07.2026: shadowhint `Go` 756 и `Golang` 581,
   при этом только у `Golang` — 318 записей, которых нет в `Go`; wantapply
   `Golang` 94 против `Go` 330 и `backend` 344 (объединение 625). Поэтому у
   каждой площадки СВОЙ проверенный набор формулировок, и он объединяется по id.
5. **Старый сток, выданный за выдачу.** hirehi отдаёт по go+backend 692 записи,
   но внутри трёх дней их 97, а старше месяца — 242. Число в отчёте без окна
   свежести вводит в заблуждение сильнее, чем ноль. Где площадка сортирует по
   дате (hirehi, shadowhint — сверено, сортировка монотонная), окно применяется
   на нашей стороне, обход прекращается на первой странице без единой свежей
   записи, а всё отрезанное печатается в сводке отдельным числом.

Границы те же, что у всего сборщика: только GET и чтение. Ни одной формы,
ни одного отклика, ни одного мутирующего запроса. Пароли и коды вводит только
пользователь. Антибот-проверки не обходятся — Cloudflare на самом wantapply.com
мы не трогаем вовсе, потому что весь контент лежит на хосте без него.

Вежливость к площадкам не опция: rabota.ru забанила сборщик по TLS после ~25
запросов за 20 минут, и это была наша вина. Между страницами стоит пауза
(`PAGE_PAUSE`), а обход, который можно закончить раньше, заканчивается раньше.
"""

from __future__ import annotations

import html as H
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field

from .model import Vacancy, norm_period
from .net import FetchError, fetch, fetch_json, qs
from .sources import Ctx, expand_k, parse_salary, period_from_text
# Окно свежести считается ровно теми же двумя функциями, что и у web-площадок.
# Свои завести было бы проще, но тогда «старше окна» у hirehi и у hackoffer
# означало бы разное, а расходятся такие копии молча.
from .sources_web import cutoff, older_than

# Пауза между страницами одного источника. Не украшение: rabota.ru закрыла нам
# TLS после ~25 запросов за 20 минут, и это была наша вина, а не её. Секунда на
# страницу дешевле, чем площадка, выпавшая из прогона на неделю.
PAGE_PAUSE = 1.0


def _pause() -> None:
    """Пауза между запросами к одной площадке. Читает глобал на каждом вызове —
    тесты обнуляют `PAGE_PAUSE` и не ждут по секунде на страницу."""
    if PAGE_PAUSE > 0:
        time.sleep(PAGE_PAUSE)


# Полнотекстовый поиск у hirehi и wantapply складывает слова через И и на
# кириллицу не отвечает вовсе: hirehi «бэкенд» → 0 при 277 по «backend»,
# wantapply «Go разработчик» → 0 при 330 по «Go». Поэтому запрос пользователя
# разбирается на ЛАТИНСКИЕ слова, и каждое уходит отдельным проходом: объединение
# по словам всегда шире, чем И-запрос из тех же слов («Backend Go» → 2 записи
# против 277 + 180 по отдельности).
_LATIN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*")


def latin_terms(queries: list[str]) -> list[str]:
    """Латинские слова из формулировок, без повторов и с сохранением порядка."""
    seen, out = set(), []
    for q in queries:
        for term in _LATIN_TERM.findall(q or ""):
            low = term.lower()
            if low not in seen:
                seen.add(low)
                out.append(term)
    return out


def merge_queries(base: list[str], vetted: tuple[str, ...]) -> list[str]:
    """Формулировки пользователя + проверенный набор площадки, без повторов.

    Набор площадки НЕ заменяет запрос пользователя, а дополняет его: `--query`
    остаётся главным, а константа закрывает то, чего одна формулировка не достаёт
    (замеры — в комментарии к каждой константе)."""
    seen, out = set(), []
    for q in [*base, *vetted]:
        low = (q or "").strip().lower()
        if low and low not in seen:
            seen.add(low)
            out.append(q.strip())
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Ошибки
# ──────────────────────────────────────────────────────────────────────────────


class NeedsLogin(FetchError):
    """Площадка ответила «не авторизован». Это НЕ поломка и НЕ ноль вакансий.

    Отдельный класс, потому что три состояния обязаны различаться в отчёте:
    сломался парсер, площадка пуста, у пользователя нет сессии. Последнее чинится
    одним заходом руками, первые два — нет, и предлагать «залогинься» на сломанный
    парсер значит гонять человека впустую.
    """


class FilterIgnored(FetchError):
    """Сервер молча проигнорировал фильтр и отдал полный каталог.

    Худший вид тихой ошибки: выдача выглядит богатой (9165 вместо 94), счётчики
    растут, отчёт бодрый — а в нём вакансии дизайнеров и бухгалтеров. Ловится
    сверкой с базовым total, и это ОБЯЗАНО быть падением, а не примечанием.
    """


# ──────────────────────────────────────────────────────────────────────────────
# Разбор денег: «150K — 200K ₽»
# ──────────────────────────────────────────────────────────────────────────────

# geekjob и hirehi пишут суммы через K: «от 350K ₽», «150K — 200K ₽». Раньше
# разворот множителя жил здесь, потому что общий parse_salary про суффикс не знал
# и отдавал 350 рублей вместо 350 000. Теперь он знает — и это единственно верное
# место: тот же «350K» приезжает с Glassdoor и из текстов вакансий, а починенным
# он был ровно у двух источников из двадцати двух.
#
# Обёртки оставлены: имена используются по всему модулю и в его тестах, а лишний
# разворот безвреден — в «350000» разворачивать уже нечего.
def parse_money(text: str | None):
    """(от, до, валюта, gross) для площадок, где суммы пишут через K."""
    return parse_salary(expand_k(text))


def _text(html_or_text: str | None) -> str | None:
    """HTML описания → плоский текст. У wantapply описание приезжает полным HTML."""
    if not html_or_text:
        return None
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_or_text)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", s)
    s = H.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t ]+", " ", s).strip() or None


def _ld_json(html: str, want: str = "JobPosting") -> dict | None:
    """Первый `<script type="application/ld+json">` нужного @type.

    Годится и для hirehi, и для geekjob: обе SPA рендерят ld+json на сервере,
    поэтому Playwright для деталей не нужен нигде. Атрибуты у скрипта идут в разном
    порядке (у hirehi между ними ещё и nonce), поэтому ищем по типу, а не по
    точной строке тега."""
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if isinstance(node, dict) and node.get("@type") == want:
                return node
    return None


def _ld_salary(node: dict | None) -> tuple[int | None, int | None, str | None, str | None]:
    """baseSalary из ld+json → (от, до, валюта, период).

    Значение бывает строкой-заглушкой («зпнеуказана» у hirehi) — тогда это НЕ
    ноль и не вилка, а отсутствие данных."""
    if not isinstance(node, dict):
        return None, None, None, None
    cur = node.get("currency") or node.get("salaryCurrency")
    val = node.get("value")
    if not isinstance(val, dict):
        return None, None, cur, None
    period = norm_period(val.get("unitText"))
    nums = []
    for key in ("minValue", "maxValue", "value"):
        v = val.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            nums.append((key, int(v)))
    lo = dict(nums).get("minValue")
    hi = dict(nums).get("maxValue")
    single = dict(nums).get("value")
    if lo is None and hi is None and single is not None:
        lo = single
    if lo is None and hi is None:
        # Вилки нет — значит и периода нет. hirehi кладёт unitText=MONTH даже
        # при value='зпнеуказана', и «/мес» без суммы выглядит как обещание
        # ежемесячной выплаты, о которой площадка ничего не говорила.
        return None, None, cur, None
    return lo, hi, cur, period


# ──────────────────────────────────────────────────────────────────────────────
# Счётчики без потерь
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Tally:
    """Сколько площадка НАЗВАЛА и куда делось каждое из названных чисел.

    Нужно потому, что самая дорогая потеря — тихая: сервер обрезал limit, страница
    не доехала, дубль съел запись, — и в отчёте появляется «hirehi 100» при 692
    доступных. Расхождение обязано быть видно строкой, а не выясняться через месяц.

    Баланс, который обязан сходиться:

        claimed = got + dropped_dup + skipped_old + unparsed + beyond_window + lost

    Каждое слагаемое кроме `lost` — осознанное решение, которое можно объяснить.
    `lost` — это то, чего мы объяснить НЕ можем, и только он печатается капсом.
    Раньше в нём тонули две разные вещи: shadowhint показывал «НЕ ДОСЧИТАЛИСЬ 6»,
    а это были шесть записей без id — не потеря, а мусор в выдаче площадки.
    """
    source: str
    # Сколько сервер назвал: total_count / totalCount / documentsCount.
    claimed: int = 0
    got: int = 0              # сколько нормализовали в вакансии
    dropped_dup: int = 0      # тот же id второй раз (между запросами и внутри запроса)
    skipped_old: int = 0      # разобрано, но старше окна --days
    unparsed: int = 0         # строка приехала, но это не вакансия: нет id или названия
    # Сервер их посчитал, а мы за ними не пошли ОСОЗНАННО: выдача отсортирована
    # по дате, целая страница оказалась старше окна — значит дальше только старше.
    # Считается отдельно от `lost`: это экономия запросов, а не потеря вакансий.
    beyond_window: int = 0
    pages: int = 0
    requests: int = 0
    notes: list[str] = field(default_factory=list)
    per_query: dict = field(default_factory=dict)

    @property
    def lost(self) -> int:
        return max(0, self.claimed - self.got - self.dropped_dup - self.skipped_old
                   - self.unparsed - self.beyond_window)

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def summary(self) -> Vacancy:
        """Служебная строка сводки. В счётчики выдачи не идёт: у неё пустой url,
        и store.query такие строки режет (тот же приём, что у ATS-досок)."""
        head = [f"[сводка {self.source}] сервер назвал {self.claimed}",
                f"унесли {self.got}",
                f"дублей между запросами {self.dropped_dup}"]
        if self.skipped_old:
            head.append(f"старше окна {self.skipped_old}")
        if self.beyond_window:
            head.append(f"за окном не забирали {self.beyond_window}")
        if self.unparsed:
            head.append(f"не вакансий {self.unparsed}")
        head.append(f"страниц {self.pages}")
        if self.lost:
            head.append(f"НЕ ДОСЧИТАЛИСЬ {self.lost}")
        title = ", ".join(head)
        return Vacancy(
            source=self.source, external_id="_summary", url="",
            title=title + ("; " + "; ".join(self.notes) if self.notes else ""),
            raw={"claimed": self.claimed, "got": self.got,
                 "dropped_dup": self.dropped_dup, "skipped_old": self.skipped_old,
                 "unparsed": self.unparsed, "beyond_window": self.beyond_window,
                 "lost": self.lost, "pages": self.pages, "requests": self.requests,
                 "notes": self.notes, "per_query": self.per_query},
        )


def _guard_filter(name: str, baseline: int, filtered: int, what: str) -> None:
    """Фильтр применился? Совпадение с базовым total — это НЕ удача, а провал."""
    if baseline and filtered == baseline:
        raise FilterIgnored(
            name, f"фильтр {what} сервер молча проигнорировал: с ним и без него "
                  f"одинаковые {filtered} записей — это весь каталог, а не выдача "
                  f"по запросу. Так парсер и приносит бухгалтеров вместо Go.")


# ──────────────────────────────────────────────────────────────────────────────
# shadowhint.com — вся выдача под Bearer
# ──────────────────────────────────────────────────────────────────────────────

SHADOWHINT_API = "https://api.shadowhint.com/api/v1"
# Ровно сто. Проверено живьём: per_page=50 отдаёт 50, per_page=100 отдаёт 100,
# а per_page=200 отдаёт ДВАДЦАТЬ (сервер отвечает perPage=20). То есть попросить
# больше сотни здесь не «может быть, повезёт», а гарантированно хуже.
SHADOWHINT_PAGE = 100
SHADOWHINT_MAX_PAGES = 40      # предохранитель от бесконечности: 39 тысяч целиком не нужны

# Формулировки, проверенные по totalCount 30.07.2026 (архив площадки — 39 190):
#   Go 756 · Golang 581 · backend 1717 · бэкенд 258 · микросервис 293 · гоу 7.
# Наборы РАЗНЫЕ, а не вложенные: у «Golang» 318 записей, которых нет в «Go»,
# у «backend» — 1464, у «бэкенд» — 180. Одна формулировка теряет большинство.
# «Go разработчик» (213) и «Backend Go» (152) в набор НЕ входят: поиск складывает
# слова через И, и обе целиком лежат внутри «Go» — лишний запрос без единой новой
# записи. Кириллица здесь работает (в отличие от hirehi и wantapply), поэтому
# «бэкенд» остаётся.
SHADOWHINT_QUERIES = ("Go", "Golang", "backend", "бэкенд", "микросервис")

SHADOWHINT_DAYS_NOTE = ("--days применяется на нашей стороне по messageDate: "
                        "выдача отсортирована по дате, обход прекращается на "
                        "первой странице без единой свежей записи")

SHADOWHINT_HOWTO = (
    "нет сессии shadowhint. Признак живого входа ровно один — кука `auth_token` "
    "на shadowhint.com (приложение зеркалит в неё Bearer на год). Куки _ym_d, "
    "_ym_uid и g_state — это Метрика и Google One Tap, входом они не являются.\n"
    "  Починить: python3 -m scripts.scout auth login shadowhint — откроется твой "
    "браузер, войдёшь сам (Google/Яндекс/VK или пароль).\n"
    "  Ротации и refresh-ручки у площадки нет: один долгоживущий токен, сессия "
    "не сгорит от фонового прогона."
)


def shadowhint_token(cookies_from: str | None = None) -> str:
    from .auth import session_token  # noqa: PLC0415 — цикл: auth не знает про источники

    token, why = session_token("shadowhint", cookies_from=cookies_from)
    if not token:
        raise NeedsLogin("https://shadowhint.com/", f"{why}. {SHADOWHINT_HOWTO}", 401)
    return token


def _rows(payload) -> list[dict]:
    """Список записей из ответа неизвестной формы.

    Форму ответа shadowhint зафиксировать заранее не вышло: анонимно все ручки
    отдают 401, а в JS-бандле ответ не перемаппливается на клиентские имена, так
    что имён полей там просто нет. Поэтому здесь честный поиск первого списка
    словарей по известным ключам, а если не нашли — падение с перечислением того,
    что реально приехало. Молча вернуть ноль тут нельзя: это выглядело бы как
    «вакансий нет» на площадке с 37 тысячами.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "vacancies", "results", "list", "records"):
        v = payload.get(key)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):  # {"data": {"items": [...]}}
            got = _rows(v)
            if got:
                return got
    return []


def _total(payload) -> int:
    if not isinstance(payload, dict):
        return 0
    for key in ("total", "total_count", "totalCount", "count", "total_items"):
        v = payload.get(key)
        if isinstance(v, int):
            return v
    for holder in ("meta", "pagination", "page_info", "pageInfo"):
        sub = payload.get(holder)
        if isinstance(sub, dict):
            got = _total(sub)
            if got:
                return got
    return 0


def _pick(d: dict, *names, default=None):
    for n in names:
        v = d.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def _int(value) -> int | None:
    """Число из чего угодно. Ноль — это НЕ вилка: у площадок он означает
    «не указано», и «0–0 ₽» в карточке читается как настоящее предложение."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) or None if digits else None


def _as_str(value) -> str | None:
    """Скаляр → строка, вложенная структура → её имя. Форма ответа shadowhint
    заранее не известна, и `location` вполне может приехать словарём: без этого
    нормализация вакансии падала бы на живом ответе прямо в момент разбора."""
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "title", "name_ru", "name_en", "city"):
            if isinstance(value.get(key), str):
                return value[key]
        return None
    if isinstance(value, (list, tuple)):
        parts = [p for p in (_as_str(x) for x in value) if p]
        return ", ".join(parts) or None
    return str(value)


def _shadowhint_vacancy(item: dict) -> Vacancy | None:
    vid = _pick(item, "id", "_id", "uuid", "vacancy_id", "message_id")
    title = _pick(item, "title", "name", "position", "vacancy_title", "header")
    if vid is None or not title:
        return None
    company = _pick(item, "company", "company_name", "companyName", "employer",
                    "employer_name")
    if isinstance(company, dict):
        company = _pick(company, "name", "title")
    # Ссылка: приоритет — на исходный пост в Telegram, он ближе всего к работодателю.
    #
    # `messageLink` в этом списке ГЛАВНОЕ имя, и раньше его здесь не было. Форма
    # ответа площадки оказалась camelCase (`messageLink`, `messageDate`), а список
    # кандидатов был на snake_case — совпадений ноль, и КАЖДАЯ из 789 вакансий
    # получала одну и ту же ссылку-заглушку на витрину. Это не косметика: с общим
    # url вакансия неотличима от соседней ни для человека, ни для ключа дубля.
    url = _pick(item, "messageLink", "message_url", "messageUrl", "tg_url",
                "telegram_url", "link", "url", "source_url")
    guessed = False
    if not url:
        url = "https://shadowhint.com/profile/tg-vacancies"
        guessed = True
    # externalLink — ссылка, которую оставил сам работодатель в посте. Это и есть
    # «контакт как можно ближе к работодателю», поэтому она идёт в employer_url,
    # а не подменяет ссылку на пост.
    external = _pick(item, "externalLink", "external_link", "apply_url")
    if not (isinstance(external, str) and external.startswith("http")):
        external = None
    salary_text = _pick(item, "salary", "salary_text", "salaryText", "compensation")
    lo = _pick(item, "salary_min", "salaryMin", "min_salary")
    hi = _pick(item, "salary_max", "salaryMax", "max_salary")
    # camelCase здесь не «на всякий случай», а живая форма ответа площадки:
    # salaryCurrency, remoteType, messageDate. Snake_case-имена оставлены рядом,
    # потому что форму ответа заранее зафиксировать было не на чем.
    cur = _pick(item, "salaryCurrency", "salary_currency", "currency")
    lo, hi = _int(lo), _int(hi)
    if lo is None and hi is None:
        lo, hi, cur2, _gross = parse_money(
            salary_text if isinstance(salary_text, str) else None)
        cur = cur or cur2
    cur = _as_str(cur)
    period = norm_period(_pick(item, "salary_period", "salary_unit", "period")) \
        or period_from_text(salary_text if isinstance(salary_text, str) else None)
    remote_raw = _pick(item, "remoteType", "remote_type", "remote", "work_format",
                       "format")
    remote = None
    if isinstance(remote_raw, bool):
        remote = remote_raw
    elif isinstance(remote_raw, str):
        low = remote_raw.lower()
        if "remote" in low or "удал" in low:
            remote = True
        elif "office" in low or "офис" in low or "onsite" in low:
            remote = False
    return Vacancy(
        source="shadowhint",
        external_id=str(vid),
        url=str(url),
        title=str(title),
        company=_as_str(company),
        salary_from=lo, salary_to=hi, currency=cur, salary_period=period,
        location=_as_str(_pick(item, "location", "city", "place")),
        remote=remote,
        employer_url=external,
        # messageDate — дата поста в канале, то есть настоящая дата публикации.
        # Стоит первой: createdAt — это когда площадка утащила пост к себе, и на
        # архиве в 39 тысяч записей разница между ними решает, попадёт вакансия
        # в окно --days или нет.
        published_at=_pick(item, "messageDate", "published_at", "publishedAt",
                           "created_at", "createdAt", "date", "posted_at",
                           "message_date"),
        description=_text(_pick(item, "description", "rawText", "text", "body",
                                "message", "content")),
        tags=[str(t) for t in (_pick(item, "tags", "skills", "categoryNames",
                                     "categories", default=[]) or []) if t][:12],
        # Форма ответа зафиксирована не была — кладём объект целиком, чтобы ни одно
        # поле не потерялось до первого живого прогона.
        raw={"item": item, "url_guessed": guessed},
    )


def src_shadowhint(ctx: Ctx, *, cookies_from: str | None = None) -> list[Vacancy]:
    """39 190 вакансий из Telegram-каналов с полнотекстовым поиском.

    Единственная площадка из четырёх, где без входа нет НИЧЕГО. Зато после входа
    браузер не нужен: токен лежит в обычной куке.

    Поиск серверный (`search_query`), поэтому запросы уходят на сервер, а не
    фильтруются у нас. Формулировок несколько, и это не запас прочности: наборы
    по `Go` (756) и `Golang` (581) пересекаются лишь частично — у второго 318
    записей, которых нет в первом. Склейка по id.

    Пагинация до конца: страница ровно 100 (больше сервер отдаёт ХУЖЕ — на
    per_page=200 приходит 20), обход идёт по totalPages, а не «пока не пусто».

    Окно `--days` применяется у нас по messageDate. Без него площадка честно
    отдаёт архив: из 2835 записей объединения старше месяца 2500+, и «shadowhint
    789» в отчёте читается как «789 свежих», чем оно не является. Выдача
    отсортирована по дате (сверено: порядок монотонный), поэтому обход
    прекращается на первой странице без единой свежей записи, а число незабранных
    печатается в сводке отдельно от потерь.
    """
    token = shadowhint_token(cookies_from)
    headers = {"Accept": "application/json", "Origin": "https://shadowhint.com",
               "Authorization": f"Bearer {token}"}
    tally = Tally("shadowhint")
    out: list[Vacancy] = []
    seen: set[str] = set()
    edge = cutoff(ctx.days)
    queries = merge_queries(ctx.queries(), SHADOWHINT_QUERIES)

    for query in queries:
        page, claimed, total_pages, rows_seen = 1, 0, 1, 0
        stopped_by_window = False
        while page <= min(total_pages, SHADOWHINT_MAX_PAGES):
            url = qs(f"{SHADOWHINT_API}/tg-vacancies",
                     {"page": page, "per_page": SHADOWHINT_PAGE,
                      "search_query": query, "sort_by": "date"})
            if tally.requests:
                _pause()
            try:
                payload = fetch_json(url, headers=headers)
            except FetchError as e:
                if e.status in (401, 403):
                    raise NeedsLogin(url, f"площадка ответила {e.status}: токен из "
                                          f"куки не принят. {SHADOWHINT_HOWTO}",
                                     e.status) from e
                raise
            tally.requests += 1
            rows = _rows(payload)
            if page == 1:
                claimed = _total(payload)
                # totalPages площадка отдаёт сама; ceil от total — запасной путь
                # на случай, если поле однажды пропадёт. max(1) — чтобы первая
                # страница всё равно была прочитана.
                total_pages = int(payload.get("totalPages") or 0) or (
                    math.ceil(claimed / SHADOWHINT_PAGE) if claimed else 1)
                total_pages = max(1, total_pages)
                tally.claimed += claimed
                tally.per_query[query] = {"claimed": claimed, "got": 0, "old": 0,
                                          "beyond": 0}
                if not rows:
                    if claimed:
                        raise FetchError(url, f"сервер назвал {claimed} вакансий, "
                                              f"а список пуст — парсер отстал от "
                                              f"формата; ключи ответа: "
                                              f"{sorted(payload)[:12]}")
                    break
            if not rows:
                break
            tally.pages += 1
            rows_seen += len(rows)
            page_fresh = page_parsed = 0
            for item in rows:
                v = _shadowhint_vacancy(item)
                if v is None:
                    tally.unparsed += 1
                    continue
                page_parsed += 1
                # Свежесть считается ДО дедупликации и независимо от неё. Иначе
                # запись, уже виденная по другой формулировке, выпадает из счёта
                # страницы, страница перестаёт быть «целиком старой», и обход
                # честно докачивает восемнадцать страниц архива ради нуля вакансий
                # (замерено: «backend» — 1717 записей, свежих 20, страниц 18).
                stale = older_than(v.published_at, edge)
                if not stale:
                    page_fresh += 1
                if v.external_id in seen:
                    tally.dropped_dup += 1
                    continue
                seen.add(v.external_id)
                if stale:
                    tally.skipped_old += 1
                    tally.per_query[query]["old"] += 1
                    continue
                out.append(v)
                tally.got += 1
                tally.per_query[query]["got"] += 1
            # На странице не осталось ни одной записи внутри окна — дальше только
            # старше, сортировка по дате это гарантирует. Запись без даты «старой»
            # не считается, поэтому недатированная страница обход не обрывает.
            # `page_parsed` — сторож против той же ошибки со стороны парсера:
            # страница, которая целиком не разобралась, это поломка, а не старьё.
            if page_parsed and page_fresh == 0:
                stopped_by_window = True
                break
            page += 1

        if stopped_by_window:
            beyond = max(0, claimed - rows_seen)
            tally.beyond_window += beyond
            tally.per_query[query]["beyond"] = beyond
        elif claimed and page > SHADOWHINT_MAX_PAGES:
            tally.note(f"«{query}»: остановились на потолке в "
                       f"{SHADOWHINT_MAX_PAGES} страниц")

    if tally.unparsed and not out and not tally.skipped_old:
        raise FetchError(SHADOWHINT_API,
                         f"ни одна из {tally.unparsed} записей не разобралась — "
                         f"у ответа другая форма, парсер надо чинить (это НЕ "
                         f"«вакансий нет»)")
    if tally.unparsed:
        tally.note(f"без id или названия: {tally.unparsed} — это мусор площадки, "
                   f"а не наша потеря")
    tally.note(f"формулировки: {', '.join(queries)}")
    tally.note(SHADOWHINT_DAYS_NOTE)
    tally.note("вход обязателен: анонимно площадка отдаёт 401")
    out.append(tally.summary())
    return out


# ──────────────────────────────────────────────────────────────────────────────
# hirehi.ru — вся выдача АНОНИМНО
# ──────────────────────────────────────────────────────────────────────────────

HIREHI_API = "https://hirehi.ru/api/search/jobs"
# Сервер режет limit до 100 молча: limit=200 и limit=500 при total_count=692
# всё равно отдают ровно 100. Понадеяться на limit=1000 — потерять 85% выдачи.
HIREHI_PAGE = 100
HIREHI_MAX_PAGES = 30
HIREHI_CATEGORY = "development"

# Подкатегории, ПРОВЕРЕННЫЕ поштучно 30.07.2026 (в скобках — total_count):
#   go 354 · backend 338 · fullstack 258 · python 510 · java 469 · kotlin 78 ·
#   nodejs 82 · cpp 247 · rust 31.
#
# Брать только go+backend (692) значило не смотреть на 3687 остальных вакансий
# категории `development` вовсе. Frontend (278), mobile (83), ios (82),
# android (81) в набор не входят — это другая профессия, а не другая отрасль.
#
# ГЛАВНАЯ ловушка этого места: НЕСУЩЕСТВУЮЩИЙ слаг сервер не отвергает, а молча
# отдаёт ВСЮ категорию. Замерено: devops, sre, qa, ml, scala, ruby, csharp,
# javascript, node-js, 1c, embedded — каждый вернул ровно 4379, то есть весь
# `development`. Поэтому набор фиксирован константой, а не собирается из строки
# пользователя, и результат сверяется с total самой категории (`_guard_filter`).
HIREHI_SUBCATEGORIES = ("go", "backend", "fullstack", "python", "java",
                        "kotlin", "nodejs", "cpp", "rust")

# Полнотекст по названию, слова через И, кириллица не находится вовсе:
#   golang 190 · go 180 · backend 277 · бэкенд 0 · бекенд 0 · back-end 0 ·
#   microservices 0 · grpc 0 · kubernetes 0 · highload 0.
# «Backend Go» одним запросом — 2 записи против 277 и 180 по словам отдельно.
# Поэтому в набор входят только слова, которые площадка реально находит.
HIREHI_QUERIES = ("golang", "go", "backend")

HIREHI_NOTE = ("вход не нужен: вся выдача и описания отдаются анонимно. "
               "Вход добавляет только счётчик раскрытий прямого контакта")
HIREHI_DAYS_NOTE = ("--days применяется на нашей стороне по created_at: у поиска "
                    "нет окна по дате, а без него из 692 записей по go+backend "
                    "внутри трёх дней только 97, старше месяца — 242")

# Формат работы приезжает строкой: «удалённо», «удалённо по РФ», «гибрид Москва»,
# «офис Санкт-Петербург». Первое слово — формат, остальное — город.
_HIREHI_FORMATS = (
    ("удалённо по рф", True), ("удалённо", True), ("удаленно", True),
    ("гибрид", None), ("офис", False),
)


def _hirehi_format(value: str | None) -> tuple[bool | None, str | None]:
    """(remote, город). Гибрид — это None, а не True и не False.

    Осознанно: «гибрид Москва» означает и офис, и удалёнку, и любое из двух
    значений булева поля было бы фактом, которого площадка не сообщала. Полная
    строка при этом сохраняется в raw, так что ничего не теряется."""
    if not value:
        return None, None
    low = value.strip().lower()
    for prefix, remote in _HIREHI_FORMATS:
        if low.startswith(prefix):
            city = value.strip()[len(prefix):].strip(" ,·")
            return remote, city or None
    return None, value.strip() or None


def hirehi_url(job: dict) -> str:
    """Ссылка на вакансию из одного id: slug угадывать не нужно.

    Проверено живьём: /development/x-69754 отдаёт 200 и редиректит на канонический
    /development/golang-developer-69754. Поэтому конструировать «правдоподобный»
    slug (и промахиваться) не приходится."""
    cat = str(job.get("category") or HIREHI_CATEGORY)
    return f"https://hirehi.ru/{cat}/x-{job.get('id')}"


def _hirehi_vacancy(job: dict) -> Vacancy | None:
    jid = job.get("id")
    if jid is None or not job.get("title"):
        return None
    money = job.get("salary_display") or job.get("salary")
    lo, hi, cur, gross = parse_money(money if isinstance(money, str) else None)
    remote, city = _hirehi_format(job.get("format"))
    return Vacancy(
        source="hirehi",
        external_id=str(jid),
        url=hirehi_url(job),
        title=str(job.get("title")),
        company=job.get("company") or None,
        salary_from=lo, salary_to=hi, currency=cur, salary_gross=gross,
        # Период площадка не называет. Вилка без суффикса честно значит «период
        # неизвестен»; подставить «/мес» было бы догадкой, выданной за факт.
        salary_period=period_from_text(money if isinstance(money, str) else None),
        location=city,
        remote=remote,
        published_at=job.get("created_at"),
        tags=[t for t in (job.get("level"), job.get("industry")) if t],
        raw={"format": job.get("format"), "level": job.get("level"),
             "industry": job.get("industry"), "is_premium": job.get("is_premium"),
             "is_from_recruiter": job.get("is_from_recruiter"),
             "salary_raw": money},
    )


def _hirehi_page(params: dict, page: int) -> dict:
    return fetch_json(qs(HIREHI_API, {**params, "page": page, "limit": HIREHI_PAGE}),
                      headers={"Accept": "application/json"})


def _hirehi_baseline(params: dict | None = None) -> int:
    """Сколько вакансий отдаёт запрос БЕЗ проверяемого фильтра — эталон.

    Эталонов два, и они разного уровня, потому что и провалиться фильтр может
    по-разному:

    * весь сайт (17 563) — эталон для полнотекстового `search`: несуществующий
      ключ `q=golang` возвращает именно его;
    * категория `development` (4379) — эталон для `subcategory`: НЕСУЩЕСТВУЮЩИЙ
      слаг (devops, sre, qa, ruby…) отдаёт ровно её.

    Сверять подкатегорию с эталоном сайта бесполезно: 4379 ≠ 17 563, проверка
    пройдёт, и в выдаче окажется вся категория вместо девяти подкатегорий.
    """
    try:
        return int(_hirehi_page(params or {}, 1).get("total_count") or 0)
    except (FetchError, TypeError, ValueError):
        return 0


def src_hirehi(ctx: Ctx, **_kw) -> list[Vacancy]:
    """Категория development (4379) через девять подкатегорий, анонимно, stdlib-GET.

    Ни браузера, ни кук, ни рендера. Прежний путь через headless-Playwright ловил
    403 в 48 байт и объявлял это антибот-стеной — но 403 отдаётся строго на UA
    со словом `HeadlessChrome`, а стены нет вовсе: тот же URL обычным клиентом
    отдаёт 437 КБ. Ложная стена хуже настоящей: настоящая чинится заходом человека,
    ложная не чинится ничем.

    Обход: один проход по всем подкатегориям из `HIREHI_SUBCATEGORIES` (раньше
    были только go+backend, то есть 692 из 4379) плюс по проходу на каждое
    ЛАТИНСКОЕ слово запроса — кириллицу полнотекст не находит вовсе.

    Окно `--days` применяется у нас по created_at: у площадки фильтра по дате нет,
    а из 692 записей go+backend внутри трёх дней 97 и старше месяца 242. Выдача
    отсортирована по дате (`sort=date`, монотонность сверена), поэтому обход
    прекращается на первой странице без единой свежей записи.

    Каждый фильтрованный запрос сверяется с эталоном СВОЕГО уровня — совпадение
    значит, что фильтр проигнорирован.
    """
    base = {"category": HIREHI_CATEGORY, "sort": "date", "include_counts": "true"}
    tally = Tally("hirehi")
    out: list[Vacancy] = []
    seen: set[str] = set()
    edge = cutoff(ctx.days)

    site_total = _hirehi_baseline()
    _pause()
    category_total = _hirehi_baseline({"category": HIREHI_CATEGORY})
    tally.requests += 2

    subs = "+".join(HIREHI_SUBCATEGORIES)
    # Третий элемент — эталоны, совпадение с которыми означает «фильтр не сработал».
    # Их ДВА у поиска: и «весь сайт» (ключ не понят вовсе), и «вся категория»
    # (понят category, но не search). Проверять только первый — та же дыра,
    # из-за которой несуществующая подкатегория приносила 4379 чужих вакансий.
    passes: list[tuple[str, dict, tuple[int, ...]]] = [
        (f"category={HIREHI_CATEGORY}, subcategory={subs}",
         {**base, "subcategory": list(HIREHI_SUBCATEGORIES)}, (category_total,))]
    for term in merge_queries(latin_terms(ctx.queries()), HIREHI_QUERIES):
        passes.append((f"search={term}", {**base, "search": term},
                       (site_total, category_total)))

    filter_counts: dict = {}
    for label, params, baselines in passes:
        _pause()
        first = _hirehi_page(params, 1)
        tally.requests += 1
        claimed = int(first.get("total_count") or 0)
        for baseline in baselines:
            _guard_filter("hirehi", baseline, claimed, label)
        if not filter_counts:
            filter_counts = first.get("filter_counts") or {}
        tally.claimed += claimed
        tally.per_query[label] = {"claimed": claimed, "got": 0, "old": 0, "beyond": 0}
        pages = min(HIREHI_MAX_PAGES,
                    max(1, math.ceil(claimed / HIREHI_PAGE)) if claimed else 1)
        payload = first
        rows_seen = 0
        stopped_by_window = False
        page = 0
        for page in range(1, pages + 1):
            if page > 1:
                _pause()
                payload = _hirehi_page(params, page)
                tally.requests += 1
            jobs = payload.get("jobs") or []
            if not jobs:
                break
            tally.pages += 1
            rows_seen += len(jobs)
            # Сервер обрезал бы limit молча — ловим это явно, а не доверяем.
            if len(jobs) > HIREHI_PAGE:
                tally.note(f"страница отдала {len(jobs)} записей при limit "
                           f"{HIREHI_PAGE} — сверь пагинацию")
            page_fresh = page_parsed = 0
            for job in jobs:
                v = _hirehi_vacancy(job)
                if v is None:
                    tally.unparsed += 1
                    continue
                page_parsed += 1
                # Свежесть считается ДО дедупликации: иначе запись, уже виденная
                # в другом проходе, выпадает из счёта страницы, страница перестаёт
                # быть «целиком старой», и обход докачивает месячный сток до конца.
                stale = older_than(v.published_at, edge)
                if not stale:
                    page_fresh += 1
                if v.external_id in seen:
                    tally.dropped_dup += 1
                    continue
                seen.add(v.external_id)
                if stale:
                    tally.skipped_old += 1
                    tally.per_query[label]["old"] += 1
                    continue
                out.append(v)
                tally.got += 1
                tally.per_query[label]["got"] += 1
            # Ни одной записи внутри окна на всей странице — при сортировке по дате
            # дальше только старше. Дочитывать — качать месячный сток ради нуля.
            # `page_parsed` обязателен: страница, которая целиком не разобралась,
            # это сломанный парсер, а не «дальше старьё», и обрывать по ней обход
            # значит спрятать поломку под видом экономии.
            if page_parsed and page_fresh == 0:
                stopped_by_window = True
                break
            if not payload.get("has_more"):
                break
        if stopped_by_window:
            beyond = max(0, claimed - rows_seen)
            tally.beyond_window += beyond
            tally.per_query[label]["beyond"] = beyond
        elif claimed and rows_seen < claimed and page >= HIREHI_MAX_PAGES:
            tally.note(f"«{label}»: остановились на потолке в "
                       f"{HIREHI_MAX_PAGES} страниц")

    if not out and not tally.skipped_old:
        raise FetchError(HIREHI_API, "API ответил, но вакансий ноль — проверь формат "
                                     "ответа, парсер мог отстать")
    tally.note(f"подкатегории: {subs}")
    tally.note(HIREHI_DAYS_NOTE)
    tally.note(HIREHI_NOTE)
    summary = tally.summary()
    # filter_counts — бесплатная статистика площадки (раскладка по грейдам, формату,
    # странам, наличию вилки). Считать её самим по выдаче было бы дороже и менее точно.
    summary.raw["filter_counts"] = filter_counts
    out.append(summary)
    return out


def hirehi_detail(vacancy_id: str | int, category: str = HIREHI_CATEGORY) -> dict:
    """Полное описание вакансии — тоже анонимно, из ld+json на её странице.

    Возвращает поля JobPosting: description, hiringOrganization, jobLocation,
    baseSalary, validThrough, skills, benefits. Работодатель раскрыт (иногда 'NDA').
    """
    url = f"https://hirehi.ru/{category}/x-{vacancy_id}"
    html, final = fetch(url)
    node = _ld_json(html)
    if not node:
        raise FetchError(final, "на странице нет ld+json JobPosting — вёрстка "
                                "изменилась, парсер деталей надо чинить")
    lo, hi, cur, period = _ld_salary(node.get("baseSalary"))
    org = node.get("hiringOrganization") or {}
    loc = ((node.get("jobLocation") or {}).get("address") or {})
    return {
        "url": node.get("url") or final,
        "title": node.get("title"),
        "description": _text(node.get("description")),
        "company": org.get("name") if isinstance(org, dict) else None,
        "published_at": node.get("datePosted"),
        "valid_through": node.get("validThrough"),
        "employment_type": node.get("employmentType"),
        "location": ", ".join(x for x in (loc.get("addressLocality"),
                                          loc.get("addressCountry")) if x) or None,
        "salary_from": lo, "salary_to": hi, "currency": cur, "salary_period": period,
        "skills": node.get("skills"),
        "benefits": node.get("benefits"),
        "direct_apply": node.get("directApply"),
    }


HIREHI_LIMITS_WARNING = (
    "Счётчик лимитов живёт под Bearer, а Bearer у hirehi выдаётся только в обмен "
    "на refresh-куку, которая при этом РОТИРУЕТСЯ. Кука одна на все заходы: чей "
    "запрос пришёл последним, тот и валиден, у второго сессия протухает мгновенно "
    "и выглядит не как «войдите», а как 403/аноним.\n"
    "  Поэтому мы сами `POST /api/auth/refresh` не делаем никогда, а открываем "
    "страницу в настоящем браузере на постоянном профиле scout и ПОДСЛУШИВАЕМ "
    "ответ, который приложение получило само. Ротация оседает в том же профиле "
    "и ничего не жжёт — но живой вкладке пользователя на hirehi это всё равно "
    "может стоить сессии, поэтому вызов не автоматический."
)


def hirehi_direct_contact_left(*, browser: str | None = None) -> dict:
    """Сколько раскрытий прямого контакта осталось (счётчик на кнопке).

    ОТДЕЛЬНЫЙ вызов, а не часть сбора: см. HIREHI_LIMITS_WARNING. Возвращает
    {'left': N|None, 'pro': bool|None, 'limits': {...}, 'why': пояснение}.
    Не увидели ответа — честное None, а не ноль: «осталось 0 откликов» и «мы не
    смогли посмотреть» это разные новости.
    """
    from .render import watch_json  # noqa: PLC0415 — Playwright опционален

    seen = watch_json("https://hirehi.ru/vacancies/go,backend", ("/api/limits",),
                      browser=browser, domains=("hirehi.ru",))
    data = seen.get("/api/limits")
    if not isinstance(data, dict):
        return {"left": None, "pro": None, "limits": {},
                "why": "приложение не запрашивало /api/limits — почти наверняка "
                       "сессии нет. Проверить: python3 -m scripts.scout auth login hirehi"}
    left = data.get("direct_contact_left")
    if left is None:
        for key, val in data.items():
            if "direct_contact" in key and isinstance(val, int):
                left = val
                break
    return {"left": left, "pro": data.get("pro"), "limits": data,
            "why": "счётчик подслушан у самого приложения, свой refresh мы не делали"}


# ──────────────────────────────────────────────────────────────────────────────
# geekjob.ru — анонимно, вход не даёт ничего
# ──────────────────────────────────────────────────────────────────────────────

GEEKJOB_LIST = "https://geekjob.ru/json/find/vacancy"
GEEKJOB_MAX_PAGES = 12

# Формулировки, проверенные по documentsCount 30.07.2026:
#   Go 18 · Golang 7 · Backend Go 74 · backend 93 · бэкенд 10 · Go engineer 27 ·
#   microservices 5 · микросервисы 2 · highload 3 · grpc 3.
# `qs` здесь работает как ИЛИ (в отличие от hirehi и wantapply, где И): «Backend
# Go» шире, чем «Go». Кириллица находится. Одна «Golang» — это 7 записей, то есть
# 6% от объединения (93 разных id), поэтому набор и нужен целиком.
#
# «бекенд» (0), «гоу» (0) и «распределённые системы» (0) в набор не входят:
# площадка их не находит, а запрос без единой записи — это чистая невежливость.
GEEKJOB_QUERIES = ("Go", "Golang", "Backend Go", "backend", "бэкенд", "Go engineer",
                   "microservices", "микросервисы", "highload", "grpc")

GEEKJOB_NOTE = (
    "вход НЕ нужен и ничего не даёт: выдача сверена анонимно и с куками "
    "пользователя, documentsCount совпал один в один (go 18/18, golang 7/7, "
    "backend 92/92, без запроса 271/271)"
)
# Площадка отдаёт одну вакансию несколько раз В ОДНОМ ответе: по «backend»
# documentsCount 93, а разных id в них 69. Дедуп по id обязателен ещё до склейки
# запросов, иначе «унесли 93» — цифра о строках, а не о вакансиях.
GEEKJOB_DUP_NOTE = ("площадка дублирует записи внутри одного ответа (backend: "
                    "93 строки, 69 разных id) — дедуп по id идёт до склейки запросов")
# Раньше здесь стояло «--days применяется приблизительно», и это была неправда:
# приблизительно оно не применялось НИКАК. В списке нет даты вовсе — только
# человекочитаемое «30 июля» без года, а достроить год догадкой значит соврать
# на границе года. Проверено живьём: `--days 1` и `--days 120` дают одинаковые 89.
# Пометка, обещающая окно, которого нет, хуже отсутствия пометки: по ней решают,
# что выдача свежая.
GEEKJOB_DAYS_NOTE = ("--days не применяется: в списке нет даты публикации — только "
                     "«30 июля» без года. Точная дата приезжает с карточки "
                     "(geekjob_detail), по списку окно посчитать не из чего")


def _geekjob_format(fmt: dict | None) -> bool | None:
    if not isinstance(fmt, dict):
        return None
    if fmt.get("remote"):
        return True
    if fmt.get("inhouse"):
        return False
    return None


def _geekjob_vacancy(item: dict) -> Vacancy | None:
    vid = item.get("id")
    if not vid or not item.get("position"):
        return None
    company = item.get("company") or {}
    # salary — ПУСТАЯ СТРОКА, когда вилки нет (не null). Пустую строку нельзя
    # спутать с нулём: «0 ₽» в карточке читается как настоящее предложение.
    money = item.get("salary") or None
    lo, hi, cur, gross = parse_money(money)
    return Vacancy(
        source="geekjob",
        external_id=str(vid),
        url=f"https://geekjob.ru/vacancy/{vid}",
        title=str(item.get("position")),
        company=(company.get("name") if isinstance(company, dict) else None),
        salary_from=lo, salary_to=hi, currency=cur, salary_gross=gross,
        salary_period=period_from_text(money),
        location=", ".join(x for x in (item.get("city"), item.get("country")) if x) or None,
        remote=_geekjob_format(item.get("jobFormat")),
        # Даты в списке нет: log.modify это «30 июля» БЕЗ ГОДА. Достроить год
        # догадкой значит соврать на границе года, поэтому published_at пустой,
        # а человекочитаемая строка лежит в raw; точная дата — в geekjob_detail().
        published_at=None,
        raw={"modified_human": ((item.get("log") or {}).get("modify")),
             "archived": ((item.get("log") or {}).get("archived")),
             "jobFormat": item.get("jobFormat"),
             "company_id": (company.get("id") if isinstance(company, dict) else None),
             "salary_raw": item.get("salary")},
    )


def src_geekjob(ctx: Ctx, **_kw) -> list[Vacancy]:
    """271 вакансия на всю площадку, по Go-профилю — 93 разных id. Анонимно.

    Маленький объём — это свойство площадки, а не поломка парсера: искать здесь
    «потерянные» вакансии не надо. Но одной формулировкой из этих 93 берётся 7
    («Golang»), то есть 6% — вот это уже поломка, и лечится она набором.

    `qs` — полнотекст, а не тег, и работает как ИЛИ: `go` (18) шире `golang` (7),
    а `Backend Go` (74) шире обоих. Кириллица находится («бэкенд» — 10). Запросы
    объединяются и склеиваются по id.

    Дедуп по id — ВНУТРИ ответа, а не только между запросами: по «backend»
    площадка отдаёт 93 строки, в которых 69 разных вакансий.
    """
    tally = Tally("geekjob")
    out: list[Vacancy] = []
    seen: set[str] = set()
    queries = merge_queries(ctx.queries(), GEEKJOB_QUERIES)
    dup_inside = 0

    for query in queries:
        page, pagecount = 1, 1
        while page <= min(pagecount, GEEKJOB_MAX_PAGES):
            if tally.requests:
                _pause()
            payload = fetch_json(qs(GEEKJOB_LIST, {"page": page, "qs": query}),
                                 headers={"Accept": "application/json"}, timeout=90)
            tally.requests += 1
            if page == 1:
                claimed = int(payload.get("documentsCount") or 0)
                pagecount = int(payload.get("pagecount") or 1)
                tally.claimed += claimed
                tally.per_query[query] = {"claimed": claimed, "got": 0}
            rows = payload.get("data") or []
            if not rows:
                break
            tally.pages += 1
            # Дубли внутри ОДНОГО ответа считаем отдельно: они говорят про площадку,
            # а не про пересечение наших формулировок, и путать их нельзя.
            here: set[str] = set()
            for item in rows:
                v = _geekjob_vacancy(item)
                if v is None:
                    tally.unparsed += 1
                    continue
                if v.external_id in here:
                    dup_inside += 1
                here.add(v.external_id)
                if v.external_id in seen:
                    tally.dropped_dup += 1
                    continue
                seen.add(v.external_id)
                out.append(v)
                tally.got += 1
                tally.per_query[query]["got"] += 1
            page += 1

    if not out:
        raise FetchError(GEEKJOB_LIST, "площадка ответила, но вакансий ноль — "
                                       "проверь формат ответа, парсер мог отстать")
    tally.note(f"формулировки: {', '.join(queries)}")
    if dup_inside:
        tally.note(f"{GEEKJOB_DUP_NOTE}; повторов внутри ответов: {dup_inside}")
    tally.note(GEEKJOB_NOTE)
    out.append(tally.summary())
    return out


def geekjob_detail(vacancy_id: str) -> dict:
    """Описание и работодатель с карточки. JSON-ручки для детали НЕТ:
    /json/vacancy/{id} и /json/find/vacancy/{id} оба отдают 404 — берём ld+json
    со страницы, он отрендерен сервером и Playwright не требует."""
    url = f"https://geekjob.ru/vacancy/{vacancy_id}"
    html, final = fetch(url, timeout=90)
    node = _ld_json(html)
    if not node:
        raise FetchError(final, "на странице нет ld+json JobPosting — вёрстка "
                                "изменилась, парсер деталей надо чинить")
    lo, hi, cur, period = _ld_salary(node.get("baseSalary"))
    org = node.get("hiringOrganization") or {}
    loc = ((node.get("jobLocation") or {}) or {}).get("address") or {}
    site = org.get("sameAs") if isinstance(org, dict) else None
    # sameAs у geekjob иногда равен самому geekjob.ru — это не сайт работодателя.
    if site and "geekjob.ru" in str(site):
        site = None
    return {
        "url": final,
        "title": node.get("title"),
        "description": _text(node.get("description")),
        "company": org.get("name") if isinstance(org, dict) else None,
        "employer_url": site,
        "published_at": node.get("datePosted"),
        "employment_type": node.get("employmentType"),
        "location": ", ".join(x for x in (loc.get("addressLocality"),
                                          loc.get("addressCountry")) if x) or None,
        "salary_from": lo, "salary_to": hi, "currency": cur, "salary_period": period,
    }


# ──────────────────────────────────────────────────────────────────────────────
# wantapply.com — каталог анонимно, ссылка в ATS под сессией
# ──────────────────────────────────────────────────────────────────────────────

WANTAPPLY_API = "https://api.wantapply.com/api/v1"
WANTAPPLY_PAGE = 50
WANTAPPLY_MAX_PAGES = 20
WANTAPPLY_HEADERS = {"Accept": "application/json", "Origin": "https://wantapply.com"}

# Формулировки, проверенные по total 30.07.2026 (каталог целиком — 9168):
#   Golang 94 · Go 330 · backend 344 · software engineer 139 · back-end 4.
# Объединение по id — 625, и это не сумма: «Golang» целиком лежит внутри «Go»
# (собственных записей 0), зато у «Go» их 212, у «backend» — 295. Одна «Golang»
# доставала 94 из 625, то есть 15%.
#
# Кириллица не находится вовсе («Go разработчик» → 0, «бэкенд» → 0), а слова
# складываются через И («Backend Go» → 1). Поэтому запрос пользователя разбирается
# на латинские слова, а фразы из набора уходят как есть — их счётчики измерены.
WANTAPPLY_QUERIES = ("Golang", "Go", "backend", "software engineer")

WANTAPPLY_NOTE = (
    "сам wantapply.com под управляемым челленджем Cloudflare — мы туда не ходим "
    "вовсе и проверку не решаем; весь каталог лежит на api.wantapply.com без стены"
)
# Окно здесь НЕ применяется осознанно: каталог релокационный и небольшой (625 по
# профилю), сортировки по дате у ручки нет, а значит и остановиться на «дальше
# только старьё» нельзя — пришлось бы качать всё и фильтровать, потеряв смысл.
# Молчать об этом нельзя: «wantapply 625» иначе читается как «625 свежих».
WANTAPPLY_DAYS_NOTE = ("--days не применяется: у ручки нет ни фильтра по дате, "
                       "ни сортировки по ней — дата публикации есть у каждой "
                       "записи, смотри published_at")
WANTAPPLY_LOGIN_HOWTO = (
    "python3 -m scripts.scout auth login wantapply — откроется твой браузер, "
    "проверку Cloudflare и вход проходишь ты. После этого кука auth-token-data "
    "читается живьём, и прямые ссылки в ATS начинают приезжать."
)


def _wantapply_filters(spec: dict) -> str:
    return urllib.parse.quote(json.dumps(spec, ensure_ascii=False))


def _wantapply_page(spec: dict, page: int, limit: int = WANTAPPLY_PAGE) -> dict:
    url = (f"{WANTAPPLY_API}/jobs?page={page}&limit={limit}"
           f"&filters={_wantapply_filters(spec)}")
    return fetch_json(url, headers=WANTAPPLY_HEADERS)


def _wantapply_vacancy(job: dict) -> Vacancy | None:
    slug = job.get("url")
    if not slug or not job.get("title"):
        return None
    company = job.get("companyName") or ((job.get("company") or {}).get("name"))
    locs = []
    for loc in (job.get("jobLocations") or []):
        if isinstance(loc, dict):
            locs.append(loc.get("name_ru") or loc.get("name_en") or loc.get("iso3"))
    lo, hi = _int(job.get("salaryMin")), _int(job.get("salaryMax"))
    money_text = job.get("salary") if isinstance(job.get("salary"), str) else None
    cur = job.get("salaryCurrency")
    if lo is None and hi is None and money_text:
        lo, hi, cur2, _g = parse_money(money_text)
        cur = cur or cur2
    period = norm_period(job.get("salaryUnit")) or period_from_text(money_text)
    return Vacancy(
        source="wantapply",
        external_id=str(job.get("id") or slug),
        url=f"https://wantapply.com/jobs/{slug}",
        title=str(job.get("title")),
        company=company,
        salary_from=lo, salary_to=hi, currency=cur, salary_period=period,
        location=", ".join(x for x in locs if x) or None,
        remote=job.get("remote") if isinstance(job.get("remote"), bool) else None,
        published_at=job.get("publishedAt") or job.get("createdAt"),
        updated_at=job.get("updatedAt"),
        # decoyApplyUrl и decoyContactEmail — ПРИМАНКИ площадки, а не контакт
        # работодателя. Класть их в employer_url значит отправить человека
        # по подложной ссылке; настоящая берётся ручкой contacts под сессией.
        employer_url=None,
        tags=[str(t) for t in (job.get("levels") or []) + (job.get("tags") or []) if t][:12],
        description=_text(job.get("description")),
        raw={"slug": slug, "levels": job.get("levels"),
             "workplaceTypes": job.get("workplaceTypes"),
             "employmentTypes": job.get("employmentTypes"),
             "relocationSupport": job.get("relocationSupport"),
             "validThrough": job.get("validThrough"),
             "expirationDate": job.get("expirationDate"),
             "isCreatedByRecruiter": job.get("isCreatedByRecruiter"),
             "jobDomains": job.get("jobDomains"),
             "decoy": {"applyUrl": job.get("decoyApplyUrl"),
                       "contactEmail": job.get("decoyContactEmail")},
             "salary_raw": job.get("salary"), "salaryUnit": job.get("salaryUnit")},
    )


def src_wantapply(ctx: Ctx, *, cookies_from: str | None = None,
                  with_apply_urls: int = 0) -> list[Vacancy]:
    """9168 вакансий с полными описаниями — анонимно, с API-хоста.

    Cloudflare обходить не нужно и мы его не трогаем: под стеной только фронтенд
    wantapply.com, а весь контент отдаёт api.wantapply.com, где стены нет.

    Рабочий ключ фильтра — `search` (синоним `title`). Ключи `query` и `remote`
    сервер молча игнорирует и отдаёт весь каталог: 9168 вместо 94. Поэтому каждый
    запрос сверяется с базовым total, и совпадение считается ошибкой.

    Формулировок несколько, и это главное отличие от прежней версии: одна «Golang»
    доставала 94 записи из 625 доступных по профилю. Транспортных потерь при этом
    не было вовсе (94 из 94 заявленных) — терялось не по дороге, а на входе.

    `with_apply_urls=N` дозапрашивает прямые ссылки в ATS для первых N вакансий —
    только это и требует сессии. По умолчанию 0: ручка платная по лимитам
    пользователя, и тратить их фоном нельзя.
    """
    tally = Tally("wantapply")
    out: list[Vacancy] = []
    seen: set[str] = set()
    baseline = int(_wantapply_page({}, 1, limit=1).get("total") or 0)
    tally.requests += 1
    # Латинские слова из запроса пользователя + проверенный набор площадки:
    # «Go разработчик» целиком даёт 0, а слово «Go» — 330.
    queries = merge_queries(latin_terms(ctx.queries()), WANTAPPLY_QUERIES)

    for query in queries:
        spec = {"search": query}
        _pause()
        first = _wantapply_page(spec, 1)
        tally.requests += 1
        claimed = int(first.get("total") or 0)
        _guard_filter("wantapply", baseline, claimed, f'filters={{"search": "{query}"}}')
        tally.claimed += claimed
        tally.per_query[query] = {"claimed": claimed, "got": 0}
        pages = min(WANTAPPLY_MAX_PAGES,
                    max(1, math.ceil(claimed / WANTAPPLY_PAGE)) if claimed else 1)
        payload = first
        rows_seen = 0
        page = 0
        for page in range(1, pages + 1):
            if page > 1:
                _pause()
                payload = _wantapply_page(spec, page)
                tally.requests += 1
            rows = payload.get("data") or []
            if not rows:
                break
            tally.pages += 1
            rows_seen += len(rows)
            for job in rows:
                v = _wantapply_vacancy(job)
                if v is None:
                    tally.unparsed += 1
                    continue
                if v.external_id in seen:
                    tally.dropped_dup += 1
                    continue
                seen.add(v.external_id)
                out.append(v)
                tally.got += 1
                tally.per_query[query]["got"] += 1
            if not payload.get("hasNextPage"):
                break
        if claimed and rows_seen < claimed and page >= WANTAPPLY_MAX_PAGES:
            tally.note(f"«{query}»: остановились на потолке в "
                       f"{WANTAPPLY_MAX_PAGES} страниц")

    if not out:
        raise FetchError(WANTAPPLY_API, "API ответил, но вакансий ноль — проверь "
                                        "формат ответа, парсер мог отстать")
    if with_apply_urls:
        tally.note(enrich_apply_urls(out, limit=with_apply_urls,
                                     cookies_from=cookies_from))
    tally.note(f"формулировки: {', '.join(queries)}")
    tally.note(WANTAPPLY_DAYS_NOTE)
    tally.note(WANTAPPLY_NOTE)
    out.append(tally.summary())
    return out


def wantapply_apply_url(slug: str, token: str) -> str | None:
    """«Apply on corporate website» — прямая ссылка в ATS работодателя.

    Приоритет №1 из «контакт как можно ближе к работодателю»: в самом объекте
    вакансии лежат только приманки decoyApplyUrl/decoyContactEmail, а настоящая
    ссылка отдаётся ТОЛЬКО этой ручкой и только под Bearer.
    """
    url = f"{WANTAPPLY_API}/jobs/url/{urllib.parse.quote(slug)}/contacts?mode=apply-url-only"
    headers = {**WANTAPPLY_HEADERS, "Authorization": f"Bearer {token}"}
    try:
        data = fetch_json(url, headers=headers, retries=1)
    except FetchError as e:
        if e.status in (401, 403):
            raise NeedsLogin(url, f"ручка контактов ответила {e.status}: сессия "
                                  f"wantapply не принята. {WANTAPPLY_LOGIN_HOWTO}",
                             e.status) from e
        raise
    if isinstance(data, str):
        return data or None
    if isinstance(data, dict):
        for key in ("applyUrl", "apply_url", "url", "link", "corporateUrl"):
            v = data.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        contacts = data.get("contacts")
        if isinstance(contacts, dict):
            for key in ("applyUrl", "apply_url", "url"):
                v = contacts.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    return v
    return None


def enrich_apply_urls(vacancies: list[Vacancy], *, limit: int = 20,
                      cookies_from: str | None = None) -> str:
    """Проставляет employer_url первым `limit` вакансиям wantapply.

    Возвращает строку-пояснение для сводки. Нет сессии — НЕ падение всего сбора:
    каталог уже собран анонимно и он полный, отсутствуют только прямые ссылки.
    Именно это и надо сказать, а не потерять 9165 вакансий из-за протухшего токена.
    """
    from .auth import session_token  # noqa: PLC0415

    token, why = session_token("wantapply", cookies_from=cookies_from)
    if not token:
        return (f"прямые ссылки в ATS не добраны: {why}. {WANTAPPLY_LOGIN_HOWTO}")
    done = failed = 0
    for v in vacancies:
        if done >= limit:
            break
        slug = (v.raw or {}).get("slug")
        if not slug or v.employer_url:
            continue
        try:
            link = wantapply_apply_url(slug, token)
        except NeedsLogin as e:
            return f"прямые ссылки в ATS оборвались: {e.reason}"
        except FetchError:
            failed += 1
            continue
        if link:
            v.employer_url = link
            done += 1
        else:
            failed += 1
    return (f"прямые ссылки в ATS: добыто {done}"
            + (f", не отдано {failed}" if failed else ""))


# ──────────────────────────────────────────────────────────────────────────────
# Реестр
# ──────────────────────────────────────────────────────────────────────────────

# Регистрацию в общем SOURCES делает интегратор — здесь только готовые адаптеры
# с той же сигнатурой `(ctx) -> list[Vacancy]`, что и у анонимных источников.
SOURCES_AUTH = {
    "shadowhint": src_shadowhint,
    "hirehi": src_hirehi,
    "geekjob": src_geekjob,
    "wantapply": src_wantapply,
}

# Что именно даёт вход. Ровно то, что честно проверено — ни строкой больше.
LOGIN_VALUE = {
    "shadowhint": "ВСЮ выдачу: анонимно площадка отдаёт 401",
    "hirehi": "только счётчик раскрытий прямого контакта и сам контакт",
    "geekjob": None,
    "wantapply": "только прямую ссылку в ATS работодателя",
}

SOURCE_NOTES_AUTH = {
    # Один источник — одна строка, и это строка про --days там, где окно ведёт
    # себя не как у всех: иначе «hirehi 692» и «wantapply 625» читаются как
    # «столько свежих за три дня», чем они не являются.
    "hirehi": HIREHI_DAYS_NOTE,
    "geekjob": GEEKJOB_DAYS_NOTE,
    "wantapply": WANTAPPLY_DAYS_NOTE,
    "shadowhint": SHADOWHINT_DAYS_NOTE,
}

# За вход просим ТОЛЬКО здесь, и у каждого исключения своя причина:
#
# * geekjob — вход не даёт ничего, сверено по цифрам;
# * hirehi — вход даёт счётчик раскрытий контакта, но добывается он только в обмен
#   на ротацию единственной refresh-куки, то есть может стоить пользователю его
#   живой сессии на площадке. Ради счётчика такую цену не назначаем; выдача при
#   этом собирается целиком и анонимно.
#
# Список «залогинься» ценен ровно настолько, насколько он короток: одна лишняя
# строка в нём — и его перестают читать целиком.
ASK_LOGIN = ("shadowhint", "wantapply")


def needs_login() -> list[str]:
    """Площадки, где вход действительно нужен И его сейчас нет.

    Отвечает на единственный практический вопрос «что от меня требуется».
    """
    from .auth import session_probe  # noqa: PLC0415

    out = []
    for name in ASK_LOGIN:
        state, _why = session_probe(name)
        if state == "anonymous":
            out.append(name)
    return out
