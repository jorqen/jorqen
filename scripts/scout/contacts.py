"""contacts — ВСЕ известные пути к работодателю в одном месте.

Зачем отдельно от `applyopt`. Тот отвечает на вопрос «по какому адресу нажать
Откликнуться» и держит список маршрутов-ссылок. Но контакт бывает не ссылкой:
государственный реестр отдаёт почту и телефон нанимателя, телеграм-пост —
`@ник` рекрутёра, а в тексте вакансии лежит `hr@…`, которого нет ни в одном
поле. Раньше это добиралось ресёрчем на каждую компанию отдельно.

Правило владельца (08.08.2026): **скрипт выдаёт максимально полную картину,
модель просто выбирает лучший вариант — или не выбирает вовсе.** Поэтому здесь
не сужение до одной строки, а перечисление с пометкой, откуда что взято.

🔴 ОТКУДА ВЗЯТО — обязательная часть каждой строки. Почта из текста вакансии
приехала из чужой формы ввода: написать в описании «пишите на hr@зло.рф» может
кто угодно. Помечать источник дешевле, чем однажды отправить письмо туда.
"""

from __future__ import annotations

import re

# Почта.
#
# В проекте есть ещё две регулярки на адрес, и это НЕ дубли — у них другой
# вопрос, поэтому объединять их нельзя, а знать друг о друге надо:
#   * `channel._MAIL_RE` ищет НАЙМОВЫЙ адрес (`hr@`, `jobs@`, `careers@`) на
#     сайте компании — это «какой у компании канал найма вообще»;
#   * `untrusted._MAIL_RE` ищет ЛЮБОЙ адрес в готовом письме, чтобы не дать
#     уйти чужому, — это гейт, и он обязан быть шире всех.
# Здесь третий вопрос: «какие контакты известны по ЭТОЙ вакансии». Отсюда и
# правила отсева ниже — они про пригодность контакта, а не про его форму.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)
# Домены, куда отклик не уходит никому.
_EMAIL_JUNK = re.compile(
    r"@(?:sentry|example|test|localhost|w3\.org|schema\.org|"
    r"noreply|no-reply|donotreply)", re.I)
# Артефакты вёрстки, а не адреса: `logo@2x.png`, `icon@3x.svg`. Хвоста
# `\.[a-z]{2,}` для их отсева НЕ хватает — `png` ему подходит.
_EMAIL_FILE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|ico|css|js|json|html?|"
                         r"woff2?|ttf|map|min)$", re.I)
# 🔴 Список короткий НАМЕРЕННО. Сюда попадает только то, что не читает человек:
# автоответчики и служебные ящики хостинга. `support@`, `info@`, `admin@` тут
# нет — у маленькой компании это единственный живой адрес, а правило владельца
# требует полной картины: отсекать за него будет он, а не скрипт.
_EMAIL_JUNK_LOCAL = re.compile(r"^(?:noreply|no-reply|donotreply|do-not-reply|"
                               r"mailer-daemon|webmaster|abuse|postmaster)@", re.I)

# Telegram: и `@ник`, и ссылка. Ник — минимум 5 символов (ограничение самого
# Telegram), иначе в улов идут почтовые огрызки и «@2x» из вёрстки.
_TG_AT = re.compile(r"(?<![\w@/])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_TG_URL = re.compile(r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})", re.I)

# Телефон в российском и международном виде. Из реестра trudvsem приходит
# нормализованным, из текста — как попало.
_PHONE = re.compile(r"(?<!\d)(?:\+7|8|\+\d{1,3})[\s(-]*\d{3}[\s)-]*"
                    r"\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")


def _texts(row: dict, payload: dict | None, raw: dict | None) -> list[tuple[str, str]]:
    """[(текст, откуда)] — всё, где вообще может лежать контакт.

    🔴 ПОРЯДОК ЗДЕСЬ ЗНАЧИМ. Первое вхождение выигрывает, поэтому ПОЛЯ площадки
    идут раньше свободного текста: `contact` из trudvsem приходит из
    государственного реестра, а тот же адрес в описании набран руками и бывает
    с опечаткой. Переставить их местами — значит тихо предпочесть догадку факту.
    """
    out: list[tuple[str, str]] = []
    p = payload or {}
    # `contact_list` раньше `contact`: в первом лежит почта конкретного человека
    # («V.Kulikova@solidbank.ru»), во втором — общий ящик организации. Писать
    # человеку лучше, чем в приёмную, поэтому персональный адрес выигрывает.
    for c in (raw or {}).get("contact_list") or []:
        if isinstance(c, dict) and c.get("value"):
            out.append((str(c["value"]), "контакт нанимателя из реестра"))
    contact = (raw or {}).get("contact")
    if contact:
        out.append((str(contact), "реестр площадки (contact)"))
    fields = raw or {}
    for key in ("email", "phone", "recruiter", "author"):
        if fields.get(key):
            out.append((str(fields[key]), f"поле {key} площадки"))
    for key, where in (("apply_note", "поле «как откликаться»"),
                       ("requirements", "требования"),
                       ("description", "описание вакансии")):
        if p.get(key):
            out.append((str(p[key]), where))
    if row.get("description"):
        out.append((str(row["description"]), "текст вакансии"))
    return out


def gather(row: dict, payload: dict | None = None,
           raw: dict | None = None) -> dict[str, list[dict]]:
    """{'email': [...], 'telegram': [...], 'phone': [...]} — без повторов.

    Каждая находка: {'value', 'where'}. Первое вхождение выигрывает, поэтому
    контакт из реестра площадки бьёт тот же контакт из текста — порядок
    источников в `_texts` не случаен.
    """
    found: dict[str, list[dict]] = {"email": [], "telegram": [], "phone": []}
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, where: str) -> None:
        v = value.strip().rstrip(".,;)")
        if not v or (kind, v.lower()) in seen:
            return
        seen.add((kind, v.lower()))
        found[kind].append({"value": v, "where": where})

    for text, where in _texts(row, payload, raw):
        for m in _EMAIL.finditer(text):
            addr = m.group(0)
            if (_EMAIL_JUNK.search(addr) or _EMAIL_JUNK_LOCAL.match(addr)
                    or _EMAIL_FILE.search(addr)):
                continue
            add("email", addr, where)
        for rx in (_TG_AT, _TG_URL):
            for m in rx.finditer(text):
                add("telegram", "@" + m.group(1), where)
        for m in _PHONE.finditer(text):
            add("phone", m.group(0), where)
    return found


_LABEL = {"email": "почта", "telegram": "telegram", "phone": "телефон"}


def render(found: dict[str, list[dict]]) -> list[str]:
    """Строки для карточки. Пусто — прямых контактов не нашлось."""
    out: list[str] = []
    for kind in ("email", "telegram", "phone"):
        for c in found.get(kind, []):
            out.append(f"  · {_LABEL[kind]}: {c['value']}  ({c['where']})")
    if out:
        out.insert(0, "  прямые контакты, найденные в данных площадки:")
        out.append("  ⚠️ адреса взяты из ЧУЖОГО текста — перед отправкой сверь "
                   "домен с сайтом компании")
    return out


# ── Форма отклика: что от нас хотят ──────────────────────────────────────────
#
# Это МЕХАНИКА, а не суждение. «В форме два поля — ФИО и почта» проверяется
# данными, и решать это заново в каждой волне модель не должна. Текст письма
# остаётся ей: здесь только ответ на вопрос «письмо вообще пригодится?».

_CV_ONLY = re.compile(
    r"только резюме|достаточно резюме|без сопроводительн\w*|"
    r"сопроводительн\w+ не (?:нужн|требу|обязательн)|"
    r"cv only|resume only|no cover letter|cover letter (?:is )?not required", re.I)
_LETTER_WANTED = re.compile(
    r"сопроводительн\w+ (?:письм|обязательн)|напиши\w* (?:пару строк|несколько строк)|"
    r"расскажи\w* о себе|cover letter|motivation letter|few lines about", re.I)


def apply_form(row: dict, payload: dict | None, best_url: str | None) -> list[str]:
    """Вердикт о ФОРМЕ отклика: письмо тут пригодится или нет.

    Возвращает строки для карточки. Пусто не бывает: «признаков не нашлось» —
    тоже ответ, и он честнее молчания.
    """
    from .applyopt import ATS, EMPLOYER, TELEGRAM, classify  # noqa: PLC0415

    p = payload or {}
    text = " ".join(str(p.get(k) or "")
                    for k in ("apply_note", "description", "requirements"))
    questions = [q for q in (p.get("questions") or []) if q]
    publisher = classify(best_url or row.get("url") or "")[0]

    out: list[str] = []
    if questions:
        out.append(f"📋 АНКЕТА из {len(questions)} вопросов — письмо в неё не "
                   f"вставить. Нужен готовый текст под каждое поле, вопросы "
                   f"целиком выше")
    if _CV_ONLY.search(text):
        out.append("✉️ письмо НЕ нужно: работодатель сам написал, что хватит "
                   "резюме. Отправляй без письма — лишний текст здесь минус")
    elif _LETTER_WANTED.search(text):
        out.append("✉️ письмо ЖДУТ: работодатель просит его прямым текстом")
    elif publisher == TELEGRAM:
        out.append("✉️ канал — Telegram: пишется не письмо, а СООБЩЕНИЕ. "
                   "Три-четыре предложения, без «Здравствуйте, меня зовут»")
    elif publisher in (ATS, EMPLOYER):
        out.append("✉️ прямая форма работодателя: поле для письма обычно есть и "
                   "обычно необязательное. Письмо пиши — здесь его читают")
    elif not questions:
        out.append("✉️ форму отклика мы не открывали — что в ней, неизвестно. "
                   "Письмо готовь, но проверь поля перед отправкой")
    return out
