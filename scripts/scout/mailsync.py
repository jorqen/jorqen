"""Статусы откликов из почты — вторая половина картины после hh-sync.

Зарубежные ATS (Greenhouse, Lever, Ashby, …) и российские компании с прямым откликом
не отчитываются в кабинет hh — их ответы приходят письмами. Здесь imap-tools
читает Gmail за N дней, отбирает письма найма, классифицирует
(подтверждение отклика / отказ / приглашение / прочее) и пишет в ту же таблицу
`negotiation`, что и hh-sync, с source=mail.

Классификатор смотрит на отправителя, тему И ТЕЛО. Раньше тело не запрашивалось
вовсе — и 78% писем падало в `other`, включая почти все отказы иностранных ATS:
тема у них нейтральная («Your application results»), а решение стоит в первом
абзаце («we decided to proceed with other candidates»).

Три правила точности, каждое написано по конкретному ложному срабатыванию:

* **Скорим только ЗАЧИН тела** (~700 символов). Дальше идёт boilerplate про воронку,
  который ломает всё: в ПОДТВЕРЖДЕНИИ отклика N26 написано «If unfortunately you
  don't make it past the CV review…», у BetterMe — «If you don't hear back from us,
  it means we've decided to move forward with other candidates». По полному телу оба
  дают ложный отказ.
* **Условное наклонение не считается.** Совпадение отбрасывается, если перед ним
  стоит «if / unless / should you / если / в случае / при отказе».
* **Порядок: NOISE → rejection → invitation → applied → other.** «Спасибо за интерес»
  открывает и отказ, и подтверждение, поэтому отказ проверяется раньше.

Ложное срабатывание дороже пропуска: сомнительное письмо уходит в `other`, и счётчик
«не классифицировано» печатается всегда — молчаливое «всё разобрали» здесь хуже всего.

Только чтение. Ящик открывается в readonly, тело берётся через BODY.PEEK
(`mark_seen=False`) — ни одно письмо не отправляется, не удаляется и даже
не помечается прочитанным. Собственные отправленные письма отсекаются по From:
ящик открыт как All Mail, и свои сопроводительные иначе классифицировались бы
как входящие статусы.

Разбор письма (multipart, charset, base64, свёрнутые заголовки) отдан imap-tools:
это был самый хрупкий кусок модуля, и каждая новая кодировка означала правку.
Классификатор с его правилами — наш и не менялся. Пакет опционален и импортируется
лениво; `mail-ingest` работает и без него.

Креды: `.auth/gmail.env` с GMAIL_USER и GMAIL_APP_PASSWORD (App Password, не основной
пароль — его выпускает и кладёт в файл сам пользователь). Файл не покидает машину.
"""

from __future__ import annotations

import email.utils          # только parseaddr в company_guess — разбор писем ушёл в imap-tools
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import store
from .auth import AUTH_DIR

ENV_PATH = os.path.join(AUTH_DIR, "gmail.env")

ENV_HOWTO = f"""Нет файла {ENV_PATH} — для IMAP нужен App Password (не основной пароль!).

Как получить (один раз):
  1. https://myaccount.google.com → Безопасность → двухэтапная аутентификация включена.
  2. Там же «Пароли приложений» (App passwords) → создать для «Почта».
  3. Положи в {ENV_PATH}:

       GMAIL_USER=you@gmail.com
       GMAIL_APP_PASSWORD=abcd efgh ijkl mnop

Файл остаётся на этой машине: .auth/ в .gitignore. Скрипт почту только ЧИТАЕТ —
ничего не отправляет, не удаляет и не помечает прочитанным.
"""

IMAP_TOOLS_HOWTO = """Нужен imap-tools — им читается почтовый ящик по IMAP.
  .venv/bin/pip install imap-tools
Пакет без зависимостей (Apache-2.0). Без него `mail-sync` не работает, а
`mail-ingest <файл.json>` — работает: это тот же классификатор по готовой выгрузке.
"""


def _require_imap_tools():
    """Ленивый импорт: ядро сборщика — stdlib, IMAP-путь опционален.

    Разбор multipart/charset руками жил здесь до этого и был самым хрупким куском
    модуля; imap-tools его закрывает целиком, но платить за него импортом там, где
    почту не читают (collect, detail, tg), незачем."""
    try:
        from imap_tools import AND, MailBox  # noqa: PLC0415
    except ImportError:
        print(IMAP_TOOLS_HOWTO, file=sys.stderr)
        raise SystemExit(3)
    return MailBox, AND

# Домены отправителей найма. Список собран по реально приходившим письмам:
# ATS-нотификаторы + российские площадки + компании, у которых HR-рассылка идёт
# со своего домена. Дополняется по мере появления новых — письмо с незнакомого
# домена всё равно ловится subject-эвристикой ниже.
HIRING_SENDERS = (
    # площадки и агрегаторы
    "hh.ru", "career.habr.com", "habr.team", "getmatch.ru", "geekjob.ru",
    "hirehi.ru", "wellfound.com", "top.co", "calendly.com",
    # ATS-нотификаторы
    "greenhouse-mail.io", "greenhouse.io", "lever.co", "hire.lever.co",
    "ashbyhq.com", "teamtailor-mail.com", "teamtailor.com", "recruitee.com",
    "workablemail.com", "workable.com", "bamboohr.com", "join.com", "msg.join.com",
    "personio.de", "personio.com", "smartrecruiters.com", "myworkday.com",
    "icims.com", "breezy.hr", "breezy-mail.com", "jobvite.com", "huntflow.ru",
    "potok.io",
    # компании, писавшие напрямую
    "corp.mail.ru", "vk.team", "sberbank.ru", "sber.ru", "yandex-team.ru",
    "tbank.ru", "tinkoff.ru", "ozon.ru", "wildberries.ru", "avito.ru",
    "andersenlab.com", "epam.com", "luxoft.com", "dataart.com", "exness.com",
    "mts.ru", "alpaca.markets", "n26.com", "tutu.ru", "joom.com", "magnit.ru",
)

# Тема похожа на переписку о найме — ловит письма с доменов не из списка.
# Ищется по теме И по зачину тела: у tutu/magnit/joom hiring-слов в теме нет
# («Re: Golang Dev»), а в первой строке тела стоит «Спасибо за ваш отклик».
RE_HIRING_SUBJECT = re.compile(
    r"отклик|ваканси|собеседован|интервью|оффер|резюме|кандидат|"
    r"application|interview|vacancy|position|candidate|recruit|offer|"
    r"your cv|hiring|talent", re.I)

# Не про найм вовсе: коды, юридические уведомления, подписочные рассылки, реклама.
# Такие письма в базу не пишутся — иначе отчёт сообщает «возможный дубль» про
# маркетинговую рассылку, что живьём и происходило.
RE_NOISE = re.compile(
    r"код подтверждени|проверочный код|одноразовый (?:код|пароль)|"
    r"security code|verification code|confirmation code|one[- ]time (?:code|password)|"
    r"privacy notice|соглашени[ея] (?:по|об) обработк[еи] персональных данных|"
    r"вакансии по подписке|лучшие вакансии|подборк[аи] вакансий|"
    r"new jobs?:|one new job matching|jobs? match(?:ing|es) your|"
    r"remote jobs match|job alert|"
    r"грант(?:ы|ов)? |магистратур|вебинар|дайджест|newsletter|"
    r"заявка на регистрацию приложения", re.I)

# Классификация. Порядок важен: отказ раньше приглашения — «после интервью решили
# не продолжать» содержит и «интервью», и отказ, и это отказ.
RE_REJECTION = re.compile(
    r"не готов[аы]? (?:пригласить|рассмотреть|продолж|предложить)|"
    r"не сможем (?:предложить|продолжить)|остановил[аи]сь на друг|"
    r"выбрал[аи] друг|решили не продолжать|не будем продолжать|"
    r"вынуждены отказать|отказ по (?:ваканси|отклик)|получен отказ|\bотказ:|"
    r"не подош(?:ли|ёл|ел)|"
    r"пока не готовы|приняли решение,? что|к сожалению|"
    r"unfortunately|regret to inform|we regret|"
    r"decided not to (?:move forward|proceed|continue)|not (?:to )?mov(?:e|ing) forward|"
    r"(?:decided|chosen) to (?:move|proceed|continue) (?:forward )?with (?:other|another)|"
    r"other candidates whose|not (?:be )?(?:progressing|proceeding)|"
    r"no longer under consideration|not selected|were not able to", re.I)
# «next step(s)» здесь СОЗНАТЕЛЬНО нет: в подтверждениях Workato и Sumsub стоит
# «we will be in touch soon on next steps», и это давало ложное приглашение.
RE_INVITATION = re.compile(
    r"приглаша(?:ем|ет)|приглашение на|готовы пригласить|"
    r"пройдите (?:короткое |первичное )?(?:ai-)?интервью|ai-интервью|"
    r"назначен[оа]? (?:интервью|собеседован)|выбер(?:и|ите) (?:удобное )?время|"
    r"предлагаем созвон|давай(?:те)? познаком|когда (?:тебе|вам) удобно|"
    r"is scheduled|invitation to interview|interview invitation|invite you to|"
    r"would like to (?:invite|schedule|set up)|"
    r"schedule (?:an? )?(?:call|interview|meeting|time)|"
    r"book a (?:time|slot|call)|pick a (?:time|slot)|calendly\.com|"
    # «interview with» безопасно: отказ проверяется РАНЬШЕ, поэтому
    # «after your interview with X we decided not to proceed» уйдёт в rejection.
    r"\binterview with\b", re.I)
RE_APPLIED = re.compile(
    r"вы откликнулись на ваканси|мы получили ваш[еи]? (?:резюме|отклик|заявк|письмо)|"
    r"получили резюме|спасибо за (?:ваш )?(?:отклик|резюме|заявк|интерес)|"
    r"отклик (?:доставлен|отправлен|получен|принят)|заявка (?:принята|получена)|"
    r"обязательно (?:его )?рассмотрим|мы рассмотрим (?:ваш|тво)|"
    r"вас добавили в число кандидат|вас рассматривают на ваканси|"
    r"application (?:has been |was )?(?:received|submitted|sent|landed)|"
    r"(?:we|i)(?:'ve| have) received your (?:application|cv|resume)|"
    r"thank(?:s| you) for (?:your )?(?:application|applying|apply)|"
    r"successfully (?:applied|received your applic)|успешно откликнул", re.I)

# Условное наклонение перед совпадением: «если не подойдёте», «if unfortunately…».
RE_CONDITIONAL = re.compile(
    r"\bif\b|\bunless\b|should you|in case|"
    r"\bесли\b|в случае|при отказе|в противном случае", re.I)
_COND_WINDOW = 140     # сколько символов перед совпадением смотреть
LEAD_CHARS = 700       # зачин тела: решение всегда в первом абзаце


def lead_of(body: str | None) -> str:
    """Зачин письма — то единственное, по чему можно судить о статусе."""
    if not body:
        return ""
    return re.sub(r"[ \t]+", " ", body).strip()[:LEAD_CHARS]


def _hit(rx: re.Pattern, text: str) -> bool:
    """Совпадение, не стоящее под условным наклонением."""
    for m in rx.finditer(text):
        window = text[max(0, m.start() - _COND_WINDOW):m.start()]
        if not RE_CONDITIONAL.search(window):
            return True
    return False


def classify_mail(sender: str, subject: str, body: str | None = None) -> str | None:
    """(отправитель, тема, тело) → rejection | invitation | applied | other | None.

    None — письмо не про найм (или служебный шум); такие в базу не пишутся вовсе.
    `other` — про найм, но без уверенного статуса: пишется, чтобы отчёт сказал
    «что-то пришло, посмотри глазами». Сомнение всегда трактуется в пользу `other`.
    """
    sender_l = (sender or "").lower()
    subj = subject or ""
    lead = lead_of(body)
    known = any(d in sender_l for d in HIRING_SENDERS)

    # api@hh.ru шлёт «Ваша заявка на регистрацию приложения рассмотрена» — это
    # не про найм, но домен знакомый и «рассмотрена» тянет в статусы.
    if sender_l.startswith("api@") or "<api@" in sender_l:
        return None
    if RE_NOISE.search(subj) or (lead and RE_NOISE.search(lead[:300])):
        return None
    if not known and not RE_HIRING_SUBJECT.search(subj) \
            and not (lead and RE_HIRING_SUBJECT.search(lead[:300])):
        return None

    scope = f"{subj}\n{lead}"
    if _hit(RE_REJECTION, scope):
        return "rejection"
    if _hit(RE_INVITATION, scope):
        return "invitation"
    if _hit(RE_APPLIED, scope):
        return "applied"
    return "other"


# Вакансия и работодатель прямо из тела письма. Это не украшение: без них ключ
# таблицы negotiation строился по шаблонной теме и площадке — 25 разных отказов
# с темой «Работодатель не готов пригласить вас» схлопывались в ОДНУ строку
# с компанией «hh.ru», и 82 письма из 184 исчезали молча.
_RE_BODY_VACANCY = (
    re.compile(r"Вакансия:\s*(?P<title>.+?)\s+компании:\s*(?P<company>[^\n.]+)", re.I),
    re.compile(r"Вы откликнулись на ваканси[юя]\s*«?(?P<title>[^»\n]+?)»?\s+"
               r"(?:компании|в компани[июя])\s*«?(?P<company>[^»\n.]+)", re.I),
    re.compile(r"отклик на ваканси[юя]\s*«?(?P<title>[^»\n]+?)»?\s+"
               r"(?:компании|в компани[июя])\s*«?(?P<company>[^»\n.]+)", re.I),
)
_RE_BODY_TITLE_ONLY = re.compile(
    r"(?:на ваканси[юя]|на позици[июя]|for the position of|your application (?:to|for))\s+"
    r"«?(?P<title>[^»\n.]{4,80})»?", re.I)


# Ссылки в письмах Хабра стоят прямо после названия — «Ведущий разработчик GO
# (https://career.habr.com/vacancies/100…) компании АВТОФОРМУЛА (https://…)».
# Без чистки они уезжают в название вакансии и в имя компании и попадают в ключ.
_URL_TAIL = re.compile(r"\s*[(\[<]?\s*https?://\S+[)\]>]?", re.I)


def _clean_field(s: str) -> str:
    s = _URL_TAIL.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" «»\"'.,:;–—-()")


def parse_vacancy(body: str | None) -> tuple[str | None, str | None]:
    """(название вакансии, компания) из тела письма. Не нашлось — (None, None)."""
    if not body:
        return None, None
    head = body[:2000]
    for rx in _RE_BODY_VACANCY:
        m = rx.search(head)
        if m:
            title, comp = _clean_field(m.group("title")), _clean_field(m.group("company"))
            if title and comp:
                return title[:120], comp[:80]
    m = _RE_BODY_TITLE_ONLY.search(head)
    if m:
        got = _clean_field(m.group("title"))
        return (got[:120] or None), None
    return None, None


def company_guess(from_header: str, subject: str, body: str | None = None) -> str | None:
    """Компания: сначала ТЕЛО письма (там она названа прямо), потом display-имя
    отправителя, потом «в <Компания>» из темы, и лишь в последнюю очередь домен.

    Домен последним принципиально: у hh/Хабра/getmatch он даёт имя ПЛОЩАДКИ вместо
    работодателя, и именно это схлопывало таблицу статусов."""
    _title, comp = parse_vacancy(body)
    if comp:
        return comp
    name, addr = email.utils.parseaddr(from_header or "")
    # ATS шлют от имени компании: "Acme Inc" <no-reply@greenhouse-mail.io>
    if name and not re.fullmatch(r"[\w .-]*no-?reply[\w .-]*", name, re.I):
        # Отрезаем служебные хвосты вида «Acme | Careers».
        return re.split(r"\s*[|—–]\s*", name)[0].strip() or None
    # Захватываются только слова с заглавной: «в Яндекс на вакансию» → «Яндекс»,
    # а не весь хвост темы.
    m = re.search(r"\b(?:в|от|from|at)\s+«?([A-ZА-ЯЁ][\w&.-]*(?:\s+[A-ZА-ЯЁ][\w&.-]*)*)»?",
                  subject or "")
    if m:
        return m.group(1).strip()
    dom = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    return dom or None


def _flat(s: str | None) -> str:
    """Заголовок в одну строку. Классификатор ищет фразы регулярками, а свёрнутый
    по RFC 2047 Subject приезжает с переводами строк и рвёт совпадение пополам."""
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class MailItem:
    subject: str
    sender: str
    date: str | None
    kind: str                        # rejection | invitation | applied | other
    lead: str = ""                   # зачин тела — по нему принято решение
    vacancy: str | None = None       # название вакансии из тела, если названо
    company: str | None = None       # работодатель из тела, если назван
    msg_id: str | None = None        # страховка ключа от схлопывания


def read_env(path: str = ENV_PATH) -> dict[str, str] | None:
    from .tgclient import read_env as _read  # noqa: PLC0415 — тот же формат KEY=VALUE
    return _read(path)


def body_text(msg) -> str:
    """Тело письма чистым текстом (msg — imap_tools.MailMessage).

    Разбор multipart, charset и base64 отдан imap-tools: `.text` собирает все
    text/plain-части, декодируя каждую её собственной кодировкой (письма Авито
    приходят в koi8-r, Хабра — base64), `.html` — то же для text/html. Ручной
    обход `msg.walk()` жил здесь ровно ради этого и был самым хрупким куском
    модуля: каждая новая кодировка означала правку.

    Своё правило остаётся одно: text/plain приоритетнее, а если его нет или он
    пустой — берём HTML и чистим своим html_to_text."""
    from .detail import html_to_text  # noqa: PLC0415

    plain = msg.text or ""
    if plain.strip():
        return plain
    return html_to_text(msg.html) if msg.html else ""


def sender_of(msg) -> str:
    """`Имя <адрес>` — в том же виде, в каком раньше приезжал заголовок From.

    Именно полная форма, а не `msg.from_` (только адрес): по ней работают и отбор
    по домену (HIRING_SENDERS), и company_guess, которому нужно имя отправителя."""
    fv = msg.from_values
    if fv is None:
        return _flat(msg.from_)
    return _flat(fv.full or fv.email)


def date_of(msg) -> str | None:
    """Дата письма в ISO-8601 UTC. None, если дата не разобралась.

    imap-tools на неразобранной дате отдаёт 1900-01-01 — это заглушка, а не факт,
    и в базу она попасть не должна. Наивную дату считаем UTC: `.astimezone()` на
    наивной взял бы локальную зону машины, а прогон крутится и в облаке."""
    dt = getattr(msg, "date", None)
    if dt is None or dt.year <= 1900:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _msg_id(msg) -> str | None:
    raw = (msg.headers or {}).get("message-id") or ()
    return (raw[0].strip() if raw else "") or None


def _message_item(msg, *, own_address: str = "") -> MailItem | None:
    sender = sender_of(msg)
    subject = _flat(msg.subject)
    # Ящик открыт как All Mail — в выборку попадают и СВОИ отправленные письма.
    # Классифицировать своё сопроводительное как входящий статус нельзя.
    if own_address and own_address.lower() in sender.lower():
        return None
    body = body_text(msg)
    kind = classify_mail(sender, subject, body)
    if kind is None:
        return None
    vac, comp = parse_vacancy(body)
    return MailItem(subject=subject, sender=sender, date=date_of(msg), kind=kind,
                    lead=lead_of(body), vacancy=vac, company=comp, msg_id=_msg_id(msg))


def is_candidate(sender: str, subject: str, own_address: str = "") -> bool:
    """Стоит ли тянуть тело этого письма. Решается по одним заголовкам: второй
    проход дорогой, а писем про скидки в ящике на порядок больше."""
    if own_address and own_address.lower() in sender.lower():
        return False
    if RE_NOISE.search(subject):
        return False
    if any(d in sender.lower() for d in HIRING_SENDERS):
        return True
    # Тело тянем и у писем с нейтральной темой от незнакомого домена: ровно там
    # лежали пропущенные отказы Magnit/tutu/Joom. Отсекаем только явную рассылку —
    # у неё в теме уже всё сказано.
    return bool(RE_HIRING_SUBJECT.search(subject)) or len(subject) < 60


def _select_readonly(mailbox) -> str:
    """Открывает «[Gmail]/All Mail», иначе INBOX — и только на чтение.

    readonly=True — жёсткая гарантия на уровне протокола: сервер отклонит любую
    попытку смены флагов, даже случайную. Поверх неё на fetch стоит mark_seen=False
    (это и есть BODY.PEEK). Две страховки на одно и то же — сознательно."""
    from imap_tools.errors import MailboxFolderSelectError  # noqa: PLC0415

    for folder in ("[Gmail]/All Mail", "INBOX"):
        try:
            mailbox.folder.set(folder, readonly=True)
            return folder
        except MailboxFolderSelectError:
            continue
    raise RuntimeError("не открылся ни [Gmail]/All Mail, ни INBOX")


def fetch_mail(days: int = 30, *, limit: int = 2000) -> list[MailItem]:
    """Читает письма за N дней (заголовки + тело) и отбирает письма найма.

    Смотрим «[Gmail]/All Mail», а не INBOX: разобранное письмо архивируют,
    и в INBOX его больше нет — а статус из него всё ещё статус.

    Два прохода: сначала дешёвые заголовки пачками, потом ПОЛНЫЕ тела только тех
    писем, что прошли первичный отбор. Тянуть тело каждого письма из ящика — это
    минуты трафика ради писем про скидки.

    Письмо и его UID связаны РАЗБОРОМ ОТВЕТА, а не порядком: imap-tools просит
    UID внутри самого FETCH и достаёт его из ответа сервера. Раньше здесь стоял
    `zip(batch, got)` — сопоставление по позиции. Сервер имеет право вернуть ответы
    в другом порядке или пропустить письмо, и тогда отправитель с темой приклеивались
    к чужому UID: во втором проходе тянулось не то письмо, а решение о статусе
    записывалось от имени соседнего."""
    MailBox, AND = _require_imap_tools()
    env = read_env()
    if env is None:
        raise SystemExit(2)  # инструкцию печатает вызывающий
    user, pwd = env.get("GMAIL_USER"), env.get("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        raise RuntimeError(f"в {ENV_PATH} нужны GMAIL_USER и GMAIL_APP_PASSWORD")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    out: list[MailItem] = []
    # initial_folder=None — по дороге не открывать INBOX на запись: папку выбирает
    # _select_readonly, и только на чтение.
    with MailBox("imap.gmail.com").login(user, pwd, initial_folder=None) as mailbox:
        _select_readonly(mailbox)
        uids = mailbox.uids(AND(date_gte=since))
        uids = uids[-limit:]  # свежие важнее; потолок против ящика-миллионника
        if not uids:
            return out

        # ── проход 1: заголовки, отбор кандидатов ────────────────────────────
        # mark_seen=False — это BODY.PEEK: прочитать, НЕ поставив \Seen.
        candidates: list[str] = []
        skipped_no_uid = 0
        for msg in mailbox.fetch(uid_list=uids, headers_only=True,
                                 mark_seen=False, bulk=100):
            if not is_candidate(sender_of(msg), _flat(msg.subject), own_address=user):
                continue
            # UID из ответа не разобрался — второй проход по такому письму не
            # заказать. Молча пропустить нельзя: это потерянный статус, а не ноль.
            if not msg.uid:
                skipped_no_uid += 1
                continue
            candidates.append(msg.uid)
        if skipped_no_uid:
            print(f"⚠️  {skipped_no_uid} писем без UID в ответе сервера — тело не "
                  f"запрошено, их статусы в базу не попадут", file=sys.stderr)
        # Пустой uid_list у imap-tools означает «критерий не задан» и разворачивается
        # в ALL — то есть в выкачку ящика целиком. Выходим раньше.
        if not candidates:
            return out

        # ── проход 2: полные тела только кандидатов ──────────────────────────
        for msg in mailbox.fetch(uid_list=candidates, mark_seen=False, bulk=40):
            item = _message_item(msg, own_address=user)
            if item is not None:
                out.append(item)
    return out


# Канонизация в статусы таблицы negotiation: applied-подтверждение — это pending
# с точки зрения ожидания ответа, но храним отдельным статусом applied, чтобы
# отчёт отличал «отклик дошёл» от «работодатель что-то ответил».
_KIND_TO_STATUS = {"rejection": "rejection", "invitation": "invitation",
                   "applied": "applied", "other": "other"}


def record_items(db_path: str, items: list[MailItem]) -> tuple[dict, list, list]:
    """Пишет классифицированные письма в таблицу negotiation (source=mail).
    Возвращает (counts_по_видам, new, changed). Общая для mail-sync и mail-ingest.

    Ключ строки: настоящие название вакансии и компания из тела письма, если они
    там названы. Если не названы — к ключу добавляется Message-ID (или дата), чтобы
    ДВА РАЗНЫХ ПИСЬМА НИКОГДА не стали одной строкой. Раньше 25 отказов от 25
    компаний с одинаковой шаблонной темой схлопывались в одну запись."""
    counts: dict[str, int] = {}
    new, changed = [], []
    with store.connect(db_path) as conn:
        for it in items:
            counts[it.kind] = counts.get(it.kind, 0) + 1
            company = it.company or company_guess(it.sender, it.subject, it.lead)
            title = it.vacancy or it.subject or "(без темы)"
            # Разобрали и вакансию, и компанию — ключ осмысленный, склейка hh+почта
            # по одной вакансии работает как задумано. Не разобрали — страхуемся.
            key_extra = None if (it.vacancy and it.company) else \
                (it.msg_id or it.date or it.subject)
            note = f"от: {it.sender}"
            if it.vacancy:
                note += f" · тема: {it.subject[:80]}"
            what, old = store.upsert_negotiation(
                conn, title=title, company=company,
                status=_KIND_TO_STATUS[it.kind], source="mail",
                event_at=it.date, note=note[:200], key_extra=key_extra)
            if what == "new":
                new.append(it)
            elif what == "changed":
                changed.append((it, old))
    return counts, new, changed


def _print_mail_summary(header: str, items: list, counts: dict, new: list,
                        changed: list, *, scanned: int | None = None) -> None:
    if items:
        by_kind = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        print(f"# {header}: писем найма {len(items)} ({by_kind})")
    else:
        print(f"# {header}: писем найма не нашлось")
    # Счётчик «не классифицировано» печатается ВСЕГДА: это мера доверия к разбору.
    # Молчаливое «всё разобрали» при 78% в other — ровно та ошибка, что тут была.
    unknown = counts.get("other", 0)
    if items:
        share = 100 * unknown / len(items)
        print(f"  не классифицировано (other): {unknown} из {len(items)} ({share:.0f}%) "
              f"— статус не определён уверенно, смотри глазами"
              + ("\n  ⚠️  больше половины писем без статуса — правила классификатора "
                 "отстали от реальных формулировок" if share > 50 else ""))
    if scanned is not None:
        print(f"  просмотрено писем: {scanned}, отсеяно как не про найм: "
              f"{scanned - len(items)}")
    for it in new:
        print(f"  NEW [{it.kind:<10}] {(it.vacancy or it.subject)[:60]} — "
              f"{(it.company or '?')[:28]}  ({(it.date or '—')[:10]})")
    for it, old in changed:
        print(f"  {old} → {it.kind:<10} {(it.vacancy or it.subject)[:60]}")
    if not new and not changed and items:
        print("  всё уже было в базе — нового нет")


def _is_login_error(exc: Exception) -> bool:
    """«Пароль не подошёл» отличается от «почта не прочиталась» одной подсказкой —
    про App Password. Импорт защищённый: сюда можно попасть и без imap-tools."""
    try:
        from imap_tools.errors import MailboxLoginError  # noqa: PLC0415
    except ImportError:
        return False
    return isinstance(exc, MailboxLoginError)


def sync(db_path: str, days: int = 30) -> int:
    env = read_env()
    if env is None:
        print(ENV_HOWTO, file=sys.stderr)
        return 2
    try:
        items = fetch_mail(days)
    except SystemExit as e:
        # 2 — нет .auth/gmail.env, 3 — нет imap-tools. Инструкцию по пакету печатает
        # сам _require_imap_tools; путать её с инструкцией про App Password нельзя.
        if e.code == 2:
            print(ENV_HOWTO, file=sys.stderr)
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:  # noqa: BLE001
        if _is_login_error(e):
            print(f"IMAP не пустил: {e}\nПроверь GMAIL_APP_PASSWORD в {ENV_PATH} "
                  f"(это App Password, не основной пароль).", file=sys.stderr)
            return 1
        print(f"почта не прочиталась: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    counts, new, changed = record_items(db_path, items)
    _print_mail_summary(f"mail-sync: за {days} дн.", items, counts, new, changed)
    return 0


def items_from_dump(raw: list[dict]) -> list[MailItem]:
    """JSON-выгрузка писем → классифицированные MailItem.

    Каждый объект: sender/from, subject, date, snippet/body. Не про найм
    (classify_mail → None) отсекается. Это путь БЕЗ App Password: детерминированная
    классификация того, что уже достали из почты другим способом."""
    out: list[MailItem] = []
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        sender = str(obj.get("sender") or obj.get("from") or "")
        subject = str(obj.get("subject") or "")
        body = str(obj.get("body") or obj.get("snippet") or obj.get("preview")
                   or obj.get("text") or "")
        date = obj.get("date") or obj.get("internalDate")
        kind = classify_mail(sender, subject, body)
        if kind is None:
            continue
        vac, comp = parse_vacancy(body)
        out.append(MailItem(subject=subject or "(без темы)", sender=sender,
                            date=str(date) if date else None, kind=kind,
                            lead=lead_of(body), vacancy=vac, company=comp,
                            msg_id=str(obj.get("id") or obj.get("messageId") or "") or None))
    return out


def ingest(db_path: str, source_file: str) -> int:
    """`mail-ingest <file.json|->`: принять выгрузку писем и записать статусы."""
    try:
        text = sys.stdin.read() if source_file == "-" else \
            open(source_file, encoding="utf-8").read()
    except OSError as e:
        print(f"файл не читается: {e}", file=sys.stderr)
        return 1
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"не JSON: {e}. Жду массив объектов sender/subject/date/snippet.",
              file=sys.stderr)
        return 2
    if isinstance(raw, dict):
        # Терпим обёртки {"messages": [...]} / {"items": [...]}.
        raw = raw.get("messages") or raw.get("items") or raw.get("emails") or []
    if not isinstance(raw, list):
        print("ожидаю JSON-массив писем", file=sys.stderr)
        return 2

    items = items_from_dump(raw)
    counts, new, changed = record_items(db_path, items)
    _print_mail_summary(f"mail-ingest: из выгрузки {len(raw)} писем", items, counts,
                        new, changed, scanned=len(raw))
    return 0
