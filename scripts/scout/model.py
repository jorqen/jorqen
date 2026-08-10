"""Нормализованная вакансия — общий формат для всех источников.

Задача модели — быть достаточно плоской, чтобы влезать в отчёт строкой, и достаточно
полной, чтобы по ней можно было решать, стоит ли открывать вакансию целиком.
Всё, что не влезло, лежит в `raw` и достаётся по требованию.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# Валюты, которые реально встречаются в выдаче российских и релокационных площадок.
_CUR = {
    "RUR": "RUB", "RUB": "RUB", "₽": "RUB", "руб": "RUB",
    "USD": "USD", "$": "USD", "EUR": "EUR", "€": "EUR",
    "KZT": "KZT", "BYR": "BYN", "BYN": "BYN", "UAH": "UAH", "GEL": "GEL",
    "AMD": "AMD", "UZS": "UZS", "KGS": "KGS", "AZN": "AZN", "GBP": "GBP",
}

# `external_id` служебной строки-сводки источника. Такая запись хранится рядом
# с вакансиями (в ней лежат счётчики обхода), но вакансией НЕ является: её нельзя
# считать в «найдено», «новых» и показывать человеку.
SUMMARY_ID = "_summary"

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
# Слова, которыми площадки украшают одну и ту же роль; для ключа дубля они шум.
#
# ГРЕЙДА здесь НЕТ, и это разбор живой потери. Пока «senior/junior/старший» лежали
# в шуме, костяк у «Junior Software Engineer with Accounting Experience» и
# «Senior Software Engineer with Accounting Experience» совпадал до символа —
# и `run_enrich` выбрасывал вторую как дубль первой. Это разные вакансии: разные
# требования и разные деньги. Замер по базе 30.07.2026 (4091 запись): таких
# склеек «под одним костяком названия РАЗНЫЕ» было 42 (Sezzle Junior/Senior в
# четырёх странах, Canonical Junior/Senior Ubuntu, OKX Junior/Senior PM,
# datadog Staff/Senior Staff, Авито «Python-разработчик»/«Старший Python»).
#
# Цена размена нулевая: все 15 групп НАСТОЯЩИХ кросс-площадочных дублей (17
# записей — Okko на hh+habr+shadowhint, Exness на getmatch+linkedin+wantapply)
# склеиваются по-прежнему, потому что грейд в них совпадает. Вернулось 19 записей,
# рискованных склеек осталось 18 вместо 42.
_NOISE = {
    "разработчик", "developer", "engineer", "инженер", "программист",
    "remote", "удаленно", "удалённо", "релокация", "relocation", "москва", "спб",
    "в", "на", "и", "с", "the", "a", "an", "of", "for", "to",
}

# Грейд — не доказательство, а уточнение: сам по себе костяк «senior» ничего
# не различает. Поэтому грейд не шум (его нельзя терять), но и не различитель
# (см. STACK_ONLY_TITLE ниже — костяк из одного грейда и стека доказательством
# дубля не считается).
GRADE_WORDS = frozenset({
    "senior", "middle", "junior", "lead", "principal", "staff", "intern",
    "ведущий", "старший", "главный", "младший", "стажер", "стажёр",
})

# ──────────────────────────────────────────────────────────────────────────────
# Заглушки вместо работодателя
# ──────────────────────────────────────────────────────────────────────────────
#
# Строки, которыми площадка закрывает НАСТОЯЩЕГО нанимателя. Для ключа дубля это
# яд: ключ «компания + костяк названия» склеивает по ним не одну вакансию, а всех,
# кто спрятался под одним словом. Замер по базе 30.07.2026 (4091 запись):
#
#   «nda|backend»                     → 62 РАЗНЫХ работодателя (hirehi);
#   «nda|go», «nda|golang»            → по 34;
#   «jobgether|full software stack»   → 43 разных объявления одной доски.
#
# Всего таких ложных склеек было 296 групп из 397, а стоили они 1032 настоящие
# вакансии: `run_enrich` выбрасывает записи с уже виденным dup_key, то есть до
# карточки доезжала одна из шестидесяти двух.
#
# Проверка ПОТОКЕННАЯ, а не подстрокой, и это принципиально: «Anaconda» содержит
# «nda», «Netcompany» — «company», и подстрочное сравнение выкинуло бы настоящих
# работодателей — ошибка ровно того же рода, только в другую сторону.
# ── Резюме соискателя ≠ вакансия ─────────────────────────────────────────────
#
# 🔴 Площадки путают это сами: careered отдал запись с `kind: job`, внутри
# которой лежало «Сейчас нахожусь в поиске новых возможностей… Немного обо мне».
# По ней была написана карточка с письмом — то есть предлагалось откликнуться
# на резюме другого разработчика (08.08.2026).
#
# Детектор жил в `tg` и знал только телеграмные формы. Здесь он общий: в группах
# половина сообщений — резюме, а на досках они попадаются как «job».
#
# Порог консервативный, и это осознанный размен: ложное срабатывание выбрасывает
# настоящую вакансию, что дороже лишней строки в списке (инвариант 7 в том же
# духе). Поэтому ловим только речь от ПЕРВОГО ЛИЦА о собственном поиске.
_RESUME_STRONG = re.compile(
    r"#резюме|#cv\b|#resume\b|#opentowork\b|"
    r"ищу\s+работу|ищу\s+(?:новую\s+)?(?:вакансию|позицию|проект)|"
    r"нахожусь\s+в\s+поиске|в\s+поиске\s+(?:новых\s+)?(?:возможностей|работы)|"
    r"открыт\s+к\s+предложениям|рассматриваю\s+предложения|"
    r"\bopen\s+to\s+work\b|looking\s+for\s+a\s+(?:job|new\s+role|position)|"
    r"^\s*обо\s+мне\s*[:—-]|немного\s+обо\s+мне", re.I | re.M)


def looks_like_resume(text: str) -> bool:
    """Текст — это резюме соискателя, а не вакансия?

    Отвечает только по явным признакам первого лица («ищу работу», «нахожусь в
    поиске», «обо мне»). Вакансии, где компания рассказывает о себе («о нас»,
    «мы ищем разработчика»), под них не подпадают: там ищут ДРУГОГО, а не себя.
    """
    return bool(_RESUME_STRONG.search(text or ""))


PLACEHOLDER_COMPANY = frozenset({
    # Наниматель скрыт самой площадкой.
    "nda", "скрыт", "скрыта", "скрыто", "скрытая", "скрыты",
    "конфиденциально", "конфиденциальная", "конфиденциальный",
    "confidential", "hidden", "anonymous", "undisclosed", "stealth",
    # «не указана» / «не указан» / «не разглашается» — те же нули, но словами.
    "указана", "указано", "указан", "разглашается", "неизвестна", "неизвестен",
    # «N/A» и «N.A.»: careered отдаёт этим «работодателя» вполне живых вакансий,
    # и без запрета карточка уезжала в каталог `companies/n-a/` вместо
    # `_hidden/` — заглушка становилась именем компании прямо в отчёте
    # (08.08.2026). Ловятся ЦЕЛОЙ строкой: костяк выбрасывает односимвольные
    # слова, и от «N/A» в нём не остаётся ничего (см. is_placeholder_company).
    "n/a", "n\\a", "n.a.", "н/д",
    # Токены досок-агрегаторов: у них `company` — имя ДОСКИ, а не нанимателя.
    # Признак ровно один: под одним и тем же company лежат вакансии разных
    # компаний (jobgether — 709 строк в базе, 43 из них с одним названием).
    # Обычные ATS-доски (canonical, datadog, okx, gitlab) сюда НЕ входят: там
    # company — настоящий работодатель, чья это доска, и склейка по нему честная.
    "jobgether",
})

# Слова, которые называют СТЕК, а не конкретное объявление. Костяк названия,
# целиком собранный из них, — не доказательство, а совпадение: у «Ozon» в одной
# только hirehi 21 разная вакансия с костяком «go», у «Wildberries» — 16.
# Поэтому «ozon|go» это не «одна вакансия на двух площадках», а «двадцать одна
# вакансия одного работодателя», и склеивать их нельзя.
STACK_ONLY_TITLE = frozenset({
    "go", "golang", "гоу", "backend", "back", "end", "бэкенд", "бекенд",
    "frontend", "фронтенд", "fullstack", "full", "stack", "python", "java",
    "kotlin", "scala", "rust", "ruby", "php", "perl", "swift", "js",
    "javascript", "typescript", "node", "nodejs", "cpp", "csharp", "dotnet",
    "net", "elixir", "erlang", "haskell", "sql", "nosql", "devops", "sre",
    "qa", "aqa", "ml", "mlops", "ai", "data", "science", "web", "mobile",
    "ios", "android", "desktop", "cloud", "platform", "infrastructure", "infra",
    "system", "systems", "network", "embedded", "gamedev", "game", "software",
    "hardware", "разработка", "разработки", "программирование", "специалист",
# Грейд сюда входит по той же причине: «senior go» — это стек плюс уточнение,
# а не название конкретного объявления. Без этой строки грейд, убранный из
# `_NOISE`, начал бы работать различителем: «Senior Backend» перестало бы
# считаться костяком-из-стека и склеивалось бы со всеми «Senior Backend»
# того же работодателя (замер: 157 склеек превратились бы в 200).
} | GRADE_WORDS)


def norm_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    return _CUR.get(s, _CUR.get(s.upper(), s.upper() if len(s) <= 4 else None))


# Период вилки. Пять значений — ровно те, для которых есть честная подпись.
#
# День и неделя появились 06.08.2026: до этого «1000 EUR per day» и «$150/week»
# сохранялись БЕЗ периода, то есть в таблице стояли неотличимо от месячных, и
# дневная ставка в 1000 EUR читалась как нищая месячная вилка. Живых дневных
# строк в базе на момент правки 30 штук (EURES/dreamoffer, Румыния и Польша),
# недельных — четыре; молчать про них дороже, чем завести два суффикса.
#
# Всё, чего здесь нет (смена, спринт, проект), по-прежнему None — и вилка
# показывается без суффикса.
PERIOD_SUFFIX = {"hour": "/час", "day": "/день", "week": "/нед",
                 "month": "/мес", "year": "/год"}


def norm_period(raw: str | None) -> str | None:
    """`annual` / `per-day` / `per-year-salary` / `в час` → hour|day|week|month|year|None.

    Период приезжает от каждой площадки по-своему: himalayas — `annual`,
    jobicy — `yearly`, careered — `year`, lever — `per-year-salary`, hh — `MONTH`,
    EURES — `day` и `week`. Сводим к пяти значениям и НЕ придумываем недостающее:
    неизвестный период — это None, а не «месяц по умолчанию». Ровно из-за такой
    подстановки почасовые 19–23 USD стояли в таблице рядом с годовыми
    168 000–333 500 USD и читались как одна и та же зарплата.

    Порядок проверок не алфавитный, а от самого короткого периода к самому
    длинному: пересечений между подстроками нет, но читать правило сверху вниз
    как «час, день, неделя, месяц, год» проще, чем вспоминать, почему `annual`
    стоит выше `day`.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if "hour" in s or "hr" in s or "час" in s:
        return "hour"
    # «daily» перечислен отдельно: подстроки «day» в нём нет (d-a-i-l-y), и на
    # этом правило уже один раз молча промахнулось. «сутки» здесь же — телеграм-
    # каналы пишут «в сутки» вместо «в день».
    if "day" in s or "daily" in s or "дн" in s or "ден" in s or "сутк" in s:
        return "day"
    if "week" in s or "нед" in s:
        return "week"
    if "month" in s or "мес" in s:
        return "month"
    if "year" in s or "annual" in s or "год" in s:
        return "year"
    return None


def norm_text(s: str | None) -> str:
    """Схлопывает пробелы и режет невидимые символы — иначе одинаковые строки не равны."""
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("​", "").replace("&nbsp;", " ")
    return _WS.sub(" ", s).strip()


def _core(s: str | None) -> str:
    """Костяк строки: без пунктуации, без слов-украшений, слова отсортированы."""
    s = _PUNCT.sub(" ", norm_text(s).lower())
    words = [w for w in s.split() if w not in _NOISE and len(w) > 1]
    return " ".join(sorted(set(words)))


_PLACEHOLDER_EXACT = frozenset(p.lower() for p in PLACEHOLDER_COMPANY)


def is_placeholder_company(company: str | None) -> bool:
    """Работодатель за заглушкой агрегатора («NDA», «Не указана», «jobgether»)?

    Единственное место, где этот вопрос решается. Раньше он был записан тремя
    разными способами, и третий — `company == PLACEHOLDER_COMPANY` в `crawl` —
    сравнивал строку с множеством, то есть был ложью всегда: раскрытый обходом
    работодатель не записывался ровно для тех вакансий, ради которых раскрытие
    и заведено.

    Проверок две, и обе нужны. По КОСТЯКУ ловится заглушка внутри длинного имени
    («JobGether Inc»). ЦЕЛОЙ строкой — «N/A» и «n\\a»: костяк выбрасывает слова
    короче двух букв, и от них не остаётся ничего. Раньше первую знал `card`,
    вторую — `wavedoc`, и каждый не знал случая другого.
    """
    if set(_core(company).split()) & PLACEHOLDER_COMPANY:
        return True
    return (company or "").strip().lower() in _PLACEHOLDER_EXACT


def no_dup_evidence(company_core: str, title_core: str) -> str | None:
    """Почему по этой паре нельзя утверждать «это одна и та же вакансия», или None.

    Возвращает ПРИЧИНУ строкой, а не булево: причина уезжает в комментарий теста
    и в отладку, и без неё «ключ вдруг стал другим» не диагностируется.
    """
    if not company_core:
        return "работодатель не назван"
    if is_placeholder_company(company_core):
        return f"компания-заглушка «{company_core}»"
    if not title_core:
        return "от названия ничего не осталось"
    if set(title_core.split()) <= STACK_ONLY_TITLE:
        return f"костяк названия — только стек «{title_core}»"
    return None


def dup_key(company: str | None, title: str | None, *, source: str | None = None,
            external_id: str | None = None, url: str | None = None) -> str:
    """Грубый ключ для ПОДСКАЗКИ о дубле — не для автоматического слияния.

    Скилл прямо запрещает склеивать вакансии автоматикой по похожести текста:
    одна вакансия в двух формулировках даёт низкое сходство, а разные вакансии одной
    компании — высокое. Поэтому здесь только консервативный ключ (компания + костяк
    названия), а решение «это один и тот же наниматель» принимает модель.

    Главное правило: **нет доказательства — нет склейки**. Доказательства нет,
    когда работодатель спрятан за заглушкой (`PLACEHOLDER_COMPANY`) или когда от
    названия остался один стек (`STACK_ONLY_TITLE`). В обоих случаях ключом
    становится собственный адрес записи — `источник:id` (или `источник:url`),
    то есть внутри одного источника разные url дают РАЗНЫЕ ключи и не склеиваются
    никогда. Это не педантизм: ровно такая склейка съедала 1032 живые вакансии,
    потому что `run_enrich` выбрасывает запись с уже виденным ключом.

    Ошибаться этот ключ обязан в сторону РАЗДЕЛЕНИЯ: лишний раскол стоит одного
    повторного запроса деталей, лишняя склейка — потерянной вакансии.

    Без `source`/`external_id`/`url` (голый вызов «сравни две строки») развести
    записи нечем, и ключ остаётся прежним «компания|название»: это честнее, чем
    выдумывать идентичность там, где о записи ничего не известно.
    """
    cc, tc = _core(company), _core(title)
    if no_dup_evidence(cc, tc):
        own = external_id or url
        if source and own:
            # «|» в ключе не бывает: он разделитель обычного ключа, и запись
            # с собственным адресом не должна случайно совпасть ни с одним из них.
            return f"@{source}:{str(own).replace('|', '/')}"
    return f"{cc}|{tc}"


def _iso(value) -> str | None:
    """Приводит дату к ISO-8601 в UTC. Понимает datetime, unix-таймстемп и ISO-строку."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        # Миллисекунды встречаются у Lever и Ashby.
        ts = float(value) / 1000.0 if float(value) > 1e11 else float(value)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    s = str(value).strip()
    if s.isdigit():
        return _iso(int(s))
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
        if not m:
            return None
        dt = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _amount(v) -> int | None:
    """Ноль — это «вилка не указана», а не «платят ноль» (rabota maxValue=0,
    shadowhint salary_from=0). Прежде 0 доживал до печати и валил salary_str:
    (0, None) уходил в ветку «до …» с None внутри f-строки — TypeError ронял
    ВЕСЬ вывод `new` на середине таблицы (04.08.2026: потерялось 1 458 строк)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) or None
    digits = re.sub(r"[^\d]", "", str(v))
    return (int(digits) or None) if digits else None


def salary_str(salary_from: int | None, salary_to: int | None,
               currency: str | None, gross: bool | None = None,
               period: str | None = None) -> str:
    """Форматирует вилку. Вынесена из Vacancy, чтобы `detail` не собирал фиктивный объект.

    `period` печатается суффиксом (`/час`, `/день`, `/нед`, `/мес`, `/год`). Неизвестный период —
    без суффикса: «60 000–90 000 RUB» честно означает «период площадка не назвала»,
    а дописать «/мес» значило бы выдать догадку за факт площадки.
    """
    # Числа приезжают и строками («250» у Recruitee) — приводим, иначе форматирование
    # падает прямо в момент печати денег.
    salary_from, salary_to = _amount(salary_from), _amount(salary_to)
    if salary_from is None and salary_to is None:
        return ""
    cur = norm_currency(currency) or ""
    fmt = lambda n: f"{n:,}".replace(",", " ")
    if salary_from and salary_to:
        body = f"{fmt(salary_from)}–{fmt(salary_to)}"
    elif salary_from:
        body = f"от {fmt(salary_from)}"
    else:
        body = f"до {fmt(salary_to)}"
    g = "" if gross is None else (" gross" if gross else " net")
    per = PERIOD_SUFFIX.get(norm_period(period) or "", "")
    return f"{body} {cur}{per}{g}".strip()


@dataclass
class Vacancy:
    source: str
    external_id: str
    url: str
    title: str
    company: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    salary_gross: bool | None = None
    # hour | day | week | month | year | None. None — площадка период не назвала;
    # подставлять месяц по умолчанию нельзя, разница между почасовой и годовой
    # вилкой — 12–2000 раз.
    salary_period: str | None = None
    location: str | None = None
    remote: bool | None = None
    published_at: str | None = None
    updated_at: str | None = None
    # Прямая ссылка в ATS/на сайт работодателя, если источник её отдал.
    # Это приоритет №1 из «контакт как можно ближе к работодателю».
    employer_url: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.title = norm_text(self.title)
        self.company = norm_text(self.company) or None
        self.location = norm_text(self.location) or None
        self.currency = norm_currency(self.currency)
        self.salary_period = norm_period(self.salary_period)
        self.published_at = _iso(self.published_at)
        self.updated_at = _iso(self.updated_at)
        self.external_id = str(self.external_id)
        if self.description:
            self.description = norm_text(self.description)[:2000]

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def dup_key(self) -> str:
        # Источник и id передаются ОБЯЗАТЕЛЬНО: без них ключ не сможет развести
        # записи, у которых работодатель скрыт, и 62 разных нанимателя снова
        # схлопнутся в один «nda|backend».
        return dup_key(self.company, self.title, source=self.source,
                       external_id=self.external_id, url=self.url)

    def salary_str(self) -> str:
        """Человекочитаемая вилка. Пустая строка — значит вилки нет, и это факт для карточки."""
        return salary_str(self.salary_from, self.salary_to, self.currency,
                          self.salary_gross, self.salary_period)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dup_key"] = self.dup_key
        return d

    def to_row(self) -> dict:
        d = self.to_dict()
        d["tags"] = json.dumps(self.tags, ensure_ascii=False)
        d["raw"] = json.dumps(self.raw, ensure_ascii=False, default=str)
        return d
