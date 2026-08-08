"""tgvacancy — пост из Telegram-канала становится строкой в `vacancy`.

Зачем модуль существует. До него телеграм-этап заканчивался дампами: `tg-fetch`
складывал .txt, `scout tg` печатал их человеку — и всё. В таблице `vacancy`
не было НИ ОДНОЙ строки с телеграмным источником при 1343 кандидатах за прогон
04.08.2026. Следствие ровно одно и оно дорогое: `shortlist` телеграм не видел,
и модель была вынуждена читать дампы глазами. Это и была главная статья расходов
того прогона — 2,8 МБ markdown и 3,3 МБ дампов, разобранных подагентами вместо
одного SQL-запроса.

Здесь пост разбирается один раз, детерминированно, и дальше живёт как обычная
вакансия: попадает в дельту, схлопывается с дублями с площадок, получает стаж,
скоринг и историю по компании — тем же кодом, что и все остальные источники.

Что модуль НЕ делает: не решает, годится ли вакансия. Отбор — работа модели.
Здесь только разбор и честный счёт отсеянного.

Главное правило то же, что во всём сборщике: **«тихо потерял» — худший баг**.
Поэтому отсев (`reject_reason`) всегда возвращает ПРИЧИНУ строкой, вызывающий
её считает и печатает примеры, а сомнительные посты попадают в базу, а не в
мусор: лишняя строка стоит одного взгляда, потерянная вакансия — вакансии.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .model import Vacancy
from .sources import ATS_ROLE_RE, parse_salary, period_from_text
from .tg import Message

# ──────────────────────────────────────────────────────────────────────────────
# Адрес поста
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ChatRef:
    """Кто отдал пост. Нужен целиком: из одного `title` ссылку не собрать.

    `chat_id` — модуль Telethon-овского `dialog.id` (без знака минус), то есть
    для канала это «100» + собственный id канала. Публичный ник, если он есть,
    важнее id: `t.me/<ник>/<N>` открывается у любого, `t.me/c/<id>/<N>` — только
    у участника канала.
    """

    chat_id: str
    title: str
    username: str | None = None

    @property
    def slug(self) -> str:
        """Хвост `source`. Ник, если канал публичный, иначе числовой id.

        Ник стабильнее человекочитаемого названия: канал переименовывают заметно
        чаще, чем меняют @ник, а source входит в первичный ключ вакансии — от его
        дрейфа вся история по каналу разошлась бы на две.
        """
        return (self.username or "").lstrip("@") or str(self.chat_id)

    @property
    def source(self) -> str:
        return f"tg:{self.slug}"

    def message_url(self, msg_id: str | int) -> str:
        """Ссылка на конкретный пост.

        Приватный канал получает форму `t.me/c/<id>/<N>`, где id — БЕЗ префикса
        «100»: в `dialog.id` он есть (-1001537669054), в ссылке его быть не должно
        (1537669054). Со «100» ссылка ведёт в никуда — и это тот случай, когда
        неверная ссылка хуже отсутствующей: она выглядит рабочей.
        """
        if self.username:
            return f"https://t.me/{self.username.lstrip('@')}/{msg_id}"
        bare = str(self.chat_id).lstrip("-")
        if bare.startswith("100"):
            bare = bare[3:]
        return f"https://t.me/c/{bare}/{msg_id}"


# ──────────────────────────────────────────────────────────────────────────────
# Чистка строки заголовка
# ──────────────────────────────────────────────────────────────────────────────

# Декор, которым каналы обрамляют заголовок: эмодзи, стрелки, рамки. Диапазоны,
# а не перечисление: каналов много, фантазия у авторов бесконечная.
_DECOR = re.compile(
    r"[\U0001F000-\U0001FAFF←-⇿⌀-➿⬀-⯿"
    r"️‍•▪▸●»«]+")
# Хвост-приписка канала к собственному посту: «· топ пост 🔥», «| Remote».
_TITLE_TAIL = re.compile(r"\s*[·|]\s*(?:топ пост|top post|срочно|urgent)\b.*$", re.I)
# Дефис и точка входят в тег: каналы пишут «#back-end», «#node.js», «#c#».
# Без них `#back-end` разбирался как тег `#back` плюс хвост `-end`, и заголовком
# вакансии становилось слово «end» — то есть чистка портила название вместо того,
# чтобы его очистить.
_HASHTAG = re.compile(r"#[\wЀ-ӿ][\wЀ-ӿ.-]*#?", re.U)
# Строки-пустышки, которыми канал открывает пост. Заголовком быть не могут.
_BOILERPLATE = re.compile(
    r"^(?:нов(?:ая|ые)\s+вакансь?[ия]\w*|вакансия дня|свежие вакансии|"
    r"new job|new vacancy|ищем|we are hiring|hiring|срочно)\W*$", re.I)


def _clean_title(s: str) -> str:
    """Снимает декор и хвосты, оставляя название роли.

    Хэштеги режутся только по краям: внутри они встречаются как часть строки
    («Senior #Golang разработчик»), и вырезать их оттуда — значит рвать название.

    NFKC первым делом — и это не косметика, а защита от тихой потери. Каналы
    набирают заголовки «жирным» математическим юникодом: «𝗙𝗼𝘂𝗻𝗱𝗶𝗻𝗴 𝗣𝗿𝗼𝗱𝘂𝗰𝘁
    𝗠𝗮𝗻𝗮𝗴𝗲𝗿» — это НЕ буквы ASCII, и `\\bgolang\\b` по ним не срабатывает никогда.
    Такой заголовок проходил бы мимо ATS_ROLE_RE, а `shortlist.on_profile`
    выбрасывал бы его как чужую профессию — вакансия исчезала бы, не будучи
    ни разу показанной. NFKC сводит эти начертания к обычным буквам.
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = _TITLE_TAIL.sub("", s)
    s = _DECOR.sub(" ", s)
    # Ведущая нумерация постов канала: «2419 #Vacancy …».
    s = re.sub(r"^\s*\d{2,6}\s+(?=#)", " ", s)
    # Хэштеги режутся ПРОБЕЖКОЙ, а не по одному: каналы пишут их через пробел
    # («#Vacancy #remote #Job Performance Marketing»), и шаблон без `\s*` внутри
    # повторения снимал ровно первый. Живой результат: заголовком вакансии
    # становилась строка из десяти хэштегов, и по ней не работало ничего —
    # ни ATS_ROLE_RE, ни ключ дубля, ни таблица «требование → что у тебя».
    # Одинокая решётка («# PerformanceMarketing») — тоже мусор разметки.
    s = re.sub(r"^\s*(?:%s\s*|#\s+)+" % _HASHTAG.pattern, " ", s)
    s = re.sub(r"(?:\s*%s|\s+#)+\s*$" % _HASHTAG.pattern, " ", s)
    s = re.sub(r"^[\s\-–—:,.>*]+|[\s\-–—:,.>*]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ──────────────────────────────────────────────────────────────────────────────
# Поля-анкеты
# ──────────────────────────────────────────────────────────────────────────────
#
# Половина job-каналов пишет пост анкетой («Позиция: …», «Компания: …», «ЗП: …»),
# и это самый надёжный источник: там, где поле есть, гадать не надо вовсе.

# Где кончается значение поля, когда пост пришёл ОДНОЙ строкой. Часть источников
# (dreamoffer отдаёт так все 7601 строку) схлопывает переносы, и `$` перестаёт
# быть границей: без этого в «Компания» уезжал весь остаток поста на килобайт.
# Границей служит то, чем размечен следующий раздел: жирный заголовок,
# эмодзи-маркер или хвост из хештегов.
_SECTION_MARK = "☑✅🔹🔸💡📍➡🔜🚀💰📄🌍⚡•"
_FIELD_END = re.compile(rf"\*\*|[{_SECTION_MARK}]|\s#\w")
# Слева от имени поля обязан стоять РАЗДЕЛИТЕЛЬ, а не любой символ: начало
# строки, жирная разметка или эмодзи-маркер раздела. Иначе «в нашей компании:
# гибкий график» из середины предложения записалось бы работодателем, а пустая
# компания честнее выдуманной (см. extract_company).
_FIELD_HEAD = rf"(?:^|\*\*|[{_SECTION_MARK}])[^\w\n]*"
# Длиннее этого названий компаний не бывает — бывает не снятая разметка.
_COMPANY_MAX = 80


def _field(body: str, names: str) -> str | None:
    """Значение поля-анкеты `Имя: значение` (первое вхождение), или None.

    Поле ищется не только в начале строки, и значение обрезается по началу
    следующего раздела. Причина одна: часть источников отдаёт пост ОДНОЙ
    строкой — dreamoffer так отдаёт все 7601 строку, — и переносы, на которых
    держался прежний разбор, там просто отсутствуют. Разметка при этом никуда
    не делась, только стала единственной границей: `**Компания**: Сбер
    ☑️**Обязанности** …`.

    Цена прежнего поведения измерена 08.08.2026: работодатель не читался ни у
    одной вакансии dreamoffer, включая Сбер и Авито в топе шорт-листа. Само
    поле при этом было на месте — просто ни `^`, ни `$` до него не доставали.
    """
    m = re.search(rf"{_FIELD_HEAD}(?:{names})[\s*_]*[:：]\s*(.+)$",
                  body, re.I | re.M)
    if not m:
        return None
    # Ведущую разметку снимаем ДО поиска границы. В форме «**Компания:**
    # Americor» двоеточие стоит внутри жира, поэтому значение начинается с `**`
    # — граница раздела оказывается нулевой позицией, и обрезка по ней молча не
    # срабатывала: назад уезжал весь пост под видом названия компании.
    value = value_start = m.group(1).lstrip("* _\t")
    cut = _FIELD_END.search(value_start)
    if cut and cut.start():
        value = value_start[:cut.start()]
    # Предохранитель на случай разметки, которой мы ещё не видели: название
    # длиннее строки — это уже пересказ вакансии, и в поле компании ему не место.
    # Пустая компания честнее выдуманной (см. extract_company).
    if len(value) > _COMPANY_MAX:
        return None
    return _clean_title(value) or None


_F_TITLE = r"позиция|должность|вакансия|position|role|job title"
_F_COMPANY = r"компания|company|работодатель|employer|проект"
_F_SALARY = r"зарплата|з/п|зп|вилка|оклад|salary|compensation|доход"
_F_LOCATION = r"локация|город|местоположение|location|офис"
_F_FORMAT = r"формат(?:\s+работы)?|занятость|график|work format|employment"

# «<Роль> в <Компания>: <ссылка>» — формат Remocate и ещё трёх каналов-витрин.
# Компания здесь ЕДИНСТВЕННЫЙ раз называется явно, и терять её нельзя: без неё
# вакансия уходит в базу с пустым работодателем и не склеивается ни с чем.
_ROLE_AT_COMPANY = re.compile(
    r"^(?P<role>.{4,90}?)\s+(?:в|в компанию|at|@)\s+(?P<company>[^:,\n]{2,60})\s*:\s*https?://",
    re.I)

# «<Роль> [Remote] @ <Компания>» — формат getmatch-бота и ещё двух витрин.
# Работодатель тут назван прямо в заголовке, и цена потери высокая: именно так
# приезжают вакансии VK, X5 Tech и TradingView — без компании они не склеиваются
# с теми же вакансиями с площадок и не находят канал найма в кэше.
_ROLE_AT_END = re.compile(
    r"^(?P<role>.{4,90}?)\s*@\s*(?P<company>[^@\n]{2,50})\s*$")
# Работодатель не назван: бот ставит на его место заглушку. Компанией это НЕ
# является — см. model.PLACEHOLDER_COMPANY, откуда берётся тот же запрет.
_HIDDEN_COMPANY = re.compile(
    r"^(?:hidden|nda|скрыт\w*|конфиденциальн\w*|undisclosed|stealth|"
    r"confidential|не указан\w*)$", re.I)
# Ведущее «Вакансия»/«Vacancy» без двоеточия: «💼 Вакансия Senior Backend
# Developer (NestJS)». Слово-этикетка, а не часть названия роли.
_TITLE_LABEL = re.compile(r"^(?:вакансия|вакансии|vacancy|job|позиция|position)\s+"
                          r"(?=\S)", re.I)

# «Вакансия <роль> от $45 000 …» — заголовок и вилка в одной строке (Job in IT).
# Режем по первому маркеру денег/формата: без обрезки в title уезжает вся строка.
_TITLE_CUT = re.compile(
    r"\s+(?:от|до|з/п|зп|зарплата|salary|from|up to)\s|[,·|]\s*(?:удал|remote|"
    r"офис|гибрид|hybrid|фулл|full[- ]time|part[- ]time)", re.I)


def _title_from_lines(body: str) -> str | None:
    """Первая строка, которая может быть названием роли.

    Шапки («Новая вакансия», ряды стрелок), голые хэштеги и служебные строки
    дампа (`[link]`, `[button]`, `[файл]`) заголовком не бывают — их пропускаем
    и идём дальше по посту, а не сдаёмся на первой же.
    """
    for raw in (body or "").split("\n")[:12]:
        line = raw.strip()
        if not line or line.startswith(("[link]", "[button]", "[файл]")):
            continue
        if _BOILERPLATE.match(_DECOR.sub("", line).strip()):
            continue
        cand = _clean_title(line)
        if not cand or len(cand) < 3:
            continue
        # Строка из одних хэштегов после чистки пустеет — она уже отсеяна выше.
        # Слишком длинная строка — это абзац текста, а не заголовок.
        if len(cand) > 120:
            continue
        cut = _TITLE_CUT.search(cand)
        if cut:
            cand = cand[:cut.start()].strip(" ,·|-–—")
        return cand or None
    return None


def _strip_company_suffix(title: str) -> str:
    """Снимает «@ Компания» с хвоста заголовка. Саму компанию берёт extract_company."""
    m = _ROLE_AT_END.match(title or "")
    return _clean_title(m.group("role")) if m else title


def extract_title(body: str) -> str | None:
    """Название роли: поле-анкета → «роль в компании» → первая живая строка."""
    field = _field(body, _F_TITLE)
    if field:
        cut = _TITLE_CUT.search(field)
        field = (field[:cut.start()].strip(" ,·|-–—") if cut else field)
        return _strip_company_suffix(_TITLE_LABEL.sub("", field)) or None
    for line in (body or "").split("\n")[:12]:
        m = _ROLE_AT_COMPANY.match(line.strip())
        if m:
            return _clean_title(m.group("role")) or None
    title = _title_from_lines(body)
    if not title:
        return None
    return _strip_company_suffix(_TITLE_LABEL.sub("", title)) or None


def extract_company(body: str) -> str | None:
    """Работодатель — только там, где он НАЗВАН. Догадок здесь нет.

    Пустая компания честнее выдуманной: по ней `dup_key` уходит в собственный
    адрес записи и вакансия не склеивается ни с чем — ровно то поведение, которое
    защищает от потери (см. model.no_dup_evidence).
    """
    field = _field(body, _F_COMPANY)
    if field and not re.match(r"^(?:nda|скрыт|конфиденциальн|не указан)", field, re.I):
        return field
    for line in (body or "").split("\n")[:12]:
        m = _ROLE_AT_COMPANY.match(line.strip())
        if m:
            return _clean_title(m.group("company")) or None
    # «<Роль> [Remote] @ <Компания>» — только в ПЕРВОЙ содержательной строке:
    # ниже по тексту «@ник» это контакт рекрутёра, а не работодатель, и принять
    # его за компанию значило бы записать в базу заведомую неправду.
    head = _title_from_lines(body)
    if head:
        m = _ROLE_AT_END.match(head)
        if m:
            name = _clean_title(m.group("company"))
            # Ник (@vasya) — контакт, а не название компании.
            if name and not name.startswith("@") and not _HIDDEN_COMPANY.match(name):
                return name
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Деньги
# ──────────────────────────────────────────────────────────────────────────────

# Вилка вне анкеты: строка, где есть и число, и валюта. Ищем именно ТАКУЮ строку,
# а не разбираем пост целиком — в теле полно чисел («7+ years», «5\2», «B2»),
# и parse_salary по всему тексту выдавал бы вилку из номера телефона.
_SALARY_LINE = re.compile(
    r"^.{0,80}?(?:[$€£₽]|\b(?:RUB|RUR|USD|EUR|GBP|KZT|BYN|UAH|GEL|AMD|PLN|TRY)\b)"
    r".{0,80}$", re.M | re.I)
_NO_SALARY = re.compile(
    r"по (?:результатам|итогам) (?:интервью|собеседовани)|обсуждается|"
    r"по договорённости|по договоренности|competitive|negotiable|не указана", re.I)

# Строка ГОВОРИТ о зарплате: есть слово про деньги за работу.
_MONEY_CONTEXT = re.compile(
    r"зарплат\w*|з/п|\bзп\b|вилк\w*|оклад\w*|доход\w*|ставк\w*|salary|compensation|"
    r"\bpay\b|\brate\b|\bgross\b|\bnet\b|на руки|до налогов|после налогов|"
    r"платим|получа\w*|предлага\w*", re.I)

# Строка про ЛЬГОТУ, а не про зарплату. Отдельный чёрный список нужен потому,
# что деньги в описании соцпакета выглядят как вилка и разбираются как вилка.
# Живой случай: «✔️ компенсацию семейных поездок до $2,000 gross в год» уехало
# в колонку «Деньги» как «от 2000 USD/год» у Senior Golang Backend Engineer —
# уверенно напечатанная ложь про зарплату, то есть ровно та ошибка, ради
# которой в этом проекте вообще заведён разбор вилок.
_PERK = re.compile(
    r"компенсац\w*|оплачива\w*|оплат\w+\s+(?:обучени|курс|спорт|связи|такси)|"
    r"бонус\w*|преми\w+|страхов\w*|\bдмс\b|поездк\w*|перел[её]т\w*|виз[аыу]\b|"
    r"обучени\w*|курс\w+|конференц\w*|техник\w*|оборудован\w*|подарок|подарк\w*|"
    r"reimburse\w*|allowance|stipend|budget|benefit\w*|perk\w*|insurance|"
    r"relocation package|бюджет\w*|abonement|абонемент\w*", re.I)

# Строка «голых денег»: короткая, и кроме суммы, валюты и периода в ней почти
# ничего нет («3500–5500 USD», «💰 от 250 000 ₽ на руки»). Такую можно принять
# и без слова «зарплата» — больше ей быть нечем.
_MONEY_NOISE = re.compile(
    r"[\d\s.,–—\-~≈]|[$€£₽]|\b(?:rub|rur|usd|eur|gbp|kzt|byn|uah|gel|amd|pln|try|"
    r"от|до|from|up|to|per|в|за|мес|месяц|год|году|year|month|hour|час|"
    r"тыс|k|net|gross)\b|[/|·•]", re.I)


def _looks_like_salary_line(line: str) -> bool:
    """Можно ли считать строку строкой про зарплату.

    Порядок проверок — от запрета к разрешению: льгота отсеивается ПЕРВОЙ,
    даже если в ней есть слово «gross», иначе «компенсация … $2,000 gross»
    прошла бы по контексту.
    """
    if _PERK.search(line):
        return False
    if _MONEY_CONTEXT.search(line):
        return True
    # «Голые деньги»: после вычёркивания сумм, валют и периодов остаётся
    # не больше двух слов. Порог не строгий — каналы любят эмодзи и «до налогов».
    rest = _MONEY_NOISE.sub(" ", line)
    rest = re.sub(r"[^\w\s]", " ", rest, flags=re.UNICODE)
    return len(line) <= 70 and len([w for w in rest.split() if len(w) > 1]) <= 2


def extract_salary(body: str, title: str | None = None) -> tuple:
    """(from, to, currency, gross, period). Пусто — вилки в посте нет.

    Поле-анкета имеет приоритет над свободным текстом: «ЗП: по результатам
    интервью» — это ЯВНОЕ «вилки нет», и лезть за ней в тело поста после такого
    значит выдать случайное число из описания за зарплату.
    """
    field = _field(body, _F_SALARY)
    if field:
        if _NO_SALARY.search(field):
            return None, None, None, None, None
        lo, hi, cur, gross = parse_salary(field)
        if lo or hi:
            return lo, hi, cur, gross, period_from_text(field)
    # Заголовок с вилкой внутри («Вакансия … от $45 000 до $55 000/год»).
    for text in (title or "", ):
        if text and re.search(r"[$€£₽]|\b(?:USD|EUR|RUB|GBP)\b", text):
            lo, hi, cur, gross = parse_salary(text)
            if lo or hi:
                return lo, hi, cur, gross, period_from_text(text)
    for m in _SALARY_LINE.finditer(body or ""):
        line = m.group(0)
        if line.strip().startswith(("[link]", "[button]")) or _NO_SALARY.search(line):
            continue
        if not _looks_like_salary_line(line):
            continue
        lo, hi, cur, gross = parse_salary(line)
        if lo or hi:
            return lo, hi, cur, gross, period_from_text(line)
    return None, None, None, None, None


# ──────────────────────────────────────────────────────────────────────────────
# Формат, ссылки, теги
# ──────────────────────────────────────────────────────────────────────────────

_REMOTE = re.compile(r"удал[её]нн?\w*|remote|from anywhere|распределённ\w* команд", re.I)
_ONSITE = re.compile(r"\bофис\w*\b|on[- ]?site|в офисе", re.I)

_LINK_RE = re.compile(r"^\s*\[(?:link|button)\][^\n]*?(https?://\S+)", re.M)
_INLINE_URL = re.compile(r"https?://[^\s<>\"')]+")
# Контакт-ник: «писать @nick», «@nick» в конце поста. Для канала это часто
# ЕДИНСТВЕННЫЙ канал отклика, и терять его нельзя — карточке нужен контакт.
_CONTACT = re.compile(r"(?:пиш\w+|обраща\w+|contact|dm|резюме)\D{0,30}?(@[A-Za-z]\w{3,31})"
                      r"|(@[A-Za-z]\w{3,31})\s*$", re.I | re.M)


def extract_links(body: str) -> list[str]:
    """Все ссылки поста в порядке появления, без повторов.

    Кнопки и скрытые гиперссылки идут ПЕРВЫМИ: у джоб-ботов текст поста говорит
    «Откликнуться», а настоящий адрес живёт в кнопке — из голого текста его
    не достать вовсе (см. tgclient._format_message).
    """
    out: list[str] = []
    for rx in (_LINK_RE, _INLINE_URL):
        for m in rx.finditer(body or ""):
            url = (m.group(1) if rx is _LINK_RE else m.group(0)).rstrip(".,;)")
            if url not in out:
                out.append(url)
    return out


def extract_contact(body: str) -> str | None:
    m = _CONTACT.search(body or "")
    return (m.group(1) or m.group(2)) if m else None


def extract_format(body: str) -> tuple[str | None, bool | None]:
    """(локация, удалёнка). None у удалёнки — «пост не сказал», а не «нет»."""
    loc = _field(body, _F_LOCATION)
    if loc and re.fullmatch(r"n/?a|-|—|не указана", loc, re.I):
        loc = None
    fmt = _field(body, _F_FORMAT) or ""
    scope = f"{fmt} {loc or ''}" if fmt or loc else (body or "")[:600]
    if _REMOTE.search(scope):
        return loc, True
    if _ONSITE.search(scope):
        return loc, False
    return loc, None


def extract_tags(body: str) -> list[str]:
    seen: list[str] = []
    for m in _HASHTAG.finditer(body or ""):
        t = m.group(0).lower()
        if t not in seen:
            seen.append(t)
    return seen[:20]


# ──────────────────────────────────────────────────────────────────────────────
# Вакансия ли это вообще
# ──────────────────────────────────────────────────────────────────────────────
#
# Канал публикует не только вакансии: реклама курсов, подборки «добавь папку»,
# сервисные объявления. Резюме и явную рекламу отсеивает `tg.classify` (по
# #резюме / #реклама / erid), но промо-пост без этих меток он пропускает.
#
# Признак вакансии выбран так, чтобы ошибаться в сторону ПРОПУСКА лишнего:
# достаточно ЛЮБОГО из четырёх сигналов. Пост-подборка каналов не имеет ни
# одного из них и отсеивается; настоящая вакансия почти всегда имеет два-три.

_VAC_TAG = re.compile(r"#(?:вакансия|vacancy|job|jobs|hiring|hh|наём|наем)\b", re.I)
_SECTIONS = re.compile(
    r"^[\s\W]{0,4}(?:требовани|обязанност|задачи|условия|мы ожидаем|ожидания|"
    r"что предстоит|стек|грейд|опыт|requirements|responsibilities|"
    r"qualifications|we offer|what you|about the role|tech stack)\w*\s*[:：]?",
    re.I | re.M)
# Роли, которых нет в ATS_ROLE_RE (она заточена под наш профиль), но пост про них
# — всё равно вакансия. Здесь мы НЕ отбираем, а только отличаем вакансию от
# рекламы, поэтому список широкий: чужая вакансия отсеется позже, в shortlist,
# и отсеется СО СЧЁТЧИКОМ.
_ANY_ROLE = re.compile(
    r"\b(?:developer|разработчик\w*|engineer|инженер|программист\w*|"
    r"analyst|аналитик\w*|designer|дизайнер\w*|manager|менеджер\w*|qa|тестировщ\w*|"
    r"devops|sre|admin|администратор\w*|architect|архитектор\w*|lead|лид\w*|"
    r"специалист\w*|marketer|маркетолог\w*|recruiter|рекрутер\w*|sales|"
    r"продакт|product owner|scrum|поддержк\w*|support)\b", re.I)


def reject_reason(msg: Message, title: str | None) -> str | None:
    """Почему пост НЕ вакансия, или None. Причина строкой — она уезжает в счётчик.

    Возврат именно причины, а не булева: без неё в отчёте стоит «отсеяно 118»
    без единого слова о том, ЧТО отсеяно, и проверить решение нечем.
    """
    if msg.category == "resume":
        return "резюме соискателя"
    if msg.category == "ad":
        return "реклама"
    body = msg.body or ""
    if not title:
        return "нет строки, похожей на название роли"
    if len(body.strip()) < 40:
        return "пост короче 40 символов — на вакансию не тянет"
    if (_VAC_TAG.search(body) or _SECTIONS.search(body)
            or ATS_ROLE_RE.search(title) or _ANY_ROLE.search(title)):
        return None
    # Заполненная анкета — тоже признак вакансии, даже когда роль в заголовке
    # не опознана. Живой пример: пост, начинающийся с «Работодатель: Нужен
    # человек, который умеет совмещать…» — ни хэштега, ни раздела требований,
    # ни знакомого слова в роли, но это вакансия, и терять её нельзя.
    if any(_field(body, n) for n in (_F_COMPANY, _F_SALARY, _F_FORMAT)):
        return None
    return "ни хэштега вакансии, ни разделов требований, ни роли в заголовке"


# ──────────────────────────────────────────────────────────────────────────────
# Сборка
# ──────────────────────────────────────────────────────────────────────────────

def to_vacancy(msg: Message, chat: ChatRef) -> Vacancy | None:
    """Пост → Vacancy, или None если это не вакансия (причина — reject_reason).

    Тело поста кладётся в `raw["text"]` ЦЕЛИКОМ: `description` обрезается моделью
    до 2000 символов, а `brief` и `card` должны показывать полный текст, не
    возвращаясь к дампу. Ради этого модуль и писался — дампы для отбора больше
    не читаются.
    """
    title = extract_title(msg.body)
    if reject_reason(msg, title) is not None:
        return None
    body = msg.body or ""
    lo, hi, cur, gross, period = extract_salary(body, title)
    loc, remote = extract_format(body)
    links = extract_links(body)
    # Прямая ссылка работодателя — первая НЕ телеграмная: t.me-ссылки ведут
    # в бота-посредника или в сам канал, работодателя за ними не видно.
    employer_url = next((u for u in links if "t.me/" not in u), None)
    return Vacancy(
        source=chat.source,
        external_id=str(msg.id),
        url=chat.message_url(msg.id),
        title=title,
        company=extract_company(body),
        salary_from=lo, salary_to=hi, currency=cur,
        salary_gross=gross, salary_period=period,
        location=loc, remote=remote,
        published_at=msg.date or None,
        employer_url=employer_url,
        tags=extract_tags(body),
        description=body,
        raw={"chat": chat.title, "chat_id": chat.chat_id, "message_id": msg.id,
             "author": msg.author, "text": body, "links": links,
             "contact": extract_contact(body)},
    )


@dataclass
class ParseStats:
    """Счёт разбора одного чата. Печатается всегда — включая нули."""

    messages: int = 0
    vacancies: int = 0
    rejected: int = 0
    reasons: dict[str, int] = None          # причина → сколько
    examples: list[str] = None              # что именно отсеяли, для проверки

    def __post_init__(self):
        self.reasons = self.reasons if self.reasons is not None else {}
        self.examples = self.examples if self.examples is not None else []

    def merge(self, other: "ParseStats") -> None:
        self.messages += other.messages
        self.vacancies += other.vacancies
        self.rejected += other.rejected
        for k, v in other.reasons.items():
            self.reasons[k] = self.reasons.get(k, 0) + v
        self.examples += other.examples[:3]

    def line(self) -> str:
        top = ", ".join(f"{k} — {v}" for k, v in
                        sorted(self.reasons.items(), key=lambda kv: -kv[1])[:4])
        return (f"сообщений {self.messages}, вакансий {self.vacancies}, "
                f"не вакансий {self.rejected}" + (f" ({top})" if top else ""))


def reparse_stored(conn, *, apply: bool = False) -> tuple[int, int, list[str]]:
    """Пересчитать поля телеграм-вакансий по СОХРАНЁННОМУ тексту поста.

    Возвращает (просмотрено, изменилось, примеры).

    Зачем. Парсер постов будет меняться и дальше, а уже разобранные строки
    останутся с полями, посчитанными старой версией. Идти за ними в Telegram
    незачем: полный текст поста лежит в `raw.text` — ровно ради этого он туда
    и кладётся. Сеть здесь не трогается вовсе.

    Пересчитываются только ВЫВОДИМЫЕ из текста поля. `url`, `source` и
    `external_id` не трогаются никогда: это адрес записи, и менять его значит
    завести дубль вместо исправления.
    """
    import json as _json  # noqa: PLC0415

    rows = conn.execute(
        "SELECT source, external_id, title, company, salary_from, salary_to, "
        "currency, salary_period, location, remote, raw FROM vacancy "
        "WHERE source LIKE 'tg:%' AND raw IS NOT NULL").fetchall()
    seen = changed = 0
    examples: list[str] = []
    for r in rows:
        seen += 1
        try:
            text = (_json.loads(r["raw"]) or {}).get("text") or ""
        except (TypeError, ValueError):
            continue
        if not text:
            continue
        title = extract_title(text)
        if not title:
            continue
        company = extract_company(text)
        lo, hi, cur, gross, period = extract_salary(text, title)
        loc, remote = extract_format(text)
        diff = (title != r["title"] or company != r["company"]
                or lo != r["salary_from"] or hi != r["salary_to"])
        if not diff:
            continue
        changed += 1
        if len(examples) < 8:
            examples.append(f"{r['source']}:{r['external_id']} "
                            f"«{(r['title'] or '')[:38]}» → «{title[:38]}»")
        if apply:
            conn.execute(
                "UPDATE vacancy SET title=?, company=COALESCE(?, company), "
                "salary_from=?, salary_to=?, currency=?, salary_period=?, "
                "location=COALESCE(?, location), remote=COALESCE(?, remote) "
                "WHERE source=? AND external_id=?",
                (title, company, lo, hi, cur, period, loc,
                 None if remote is None else int(remote),
                 r["source"], r["external_id"]))
    return seen, changed, examples


def from_dump(messages: list[Message], chat: ChatRef) -> tuple[list[Vacancy], ParseStats]:
    """Разбирает уже расклассифицированные сообщения чата.

    Классификацию (`tg.classify`) вызывает ВЫЗЫВАЮЩИЙ: она же нужна ему для
    печати отчёта, а звать её дважды по одному сообщению — значит разойтись
    в счётчиках между отчётом и базой.
    """
    out: list[Vacancy] = []
    st = ParseStats(messages=len(messages))
    for m in messages:
        v = to_vacancy(m, chat)
        if v is not None:
            out.append(v)
            st.vacancies += 1
            continue
        why = reject_reason(m, extract_title(m.body)) or "не разобрался"
        st.rejected += 1
        st.reasons[why] = st.reasons.get(why, 0) + 1
        if len(st.examples) < 5:
            head = (m.body or "").strip().split("\n")[0][:70]
            st.examples.append(f"#{m.id} [{why}] {head}")
    return out, st
