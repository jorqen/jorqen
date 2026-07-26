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

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
# Слова, которыми площадки украшают одну и ту же роль; для ключа дубля они шум.
_NOISE = {
    "разработчик", "developer", "engineer", "инженер", "программист",
    "senior", "middle", "junior", "lead", "ведущий", "старший", "главный",
    "remote", "удаленно", "удалённо", "релокация", "relocation", "москва", "спб",
    "в", "на", "и", "с", "the", "a", "an", "of", "for", "to",
}


def norm_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    return _CUR.get(s, _CUR.get(s.upper(), s.upper() if len(s) <= 4 else None))


def norm_text(s: str | None) -> str:
    """Схлопывает пробелы и режет невидимые символы — иначе одинаковые строки не равны."""
    if not s:
        return ""
    s = s.replace("\xa0", " ").replace("​", "").replace("&nbsp;", " ")
    return _WS.sub(" ", s).strip()


def dup_key(company: str | None, title: str | None) -> str:
    """Грубый ключ для ПОДСКАЗКИ о дубле — не для автоматического слияния.

    Скилл прямо запрещает склеивать вакансии автоматикой по похожести текста:
    одна вакансия в двух формулировках даёт низкое сходство, а разные вакансии одной
    компании — высокое. Поэтому здесь только консервативный ключ (компания + костяк
    названия), а решение «это один и тот же наниматель» принимает модель.
    """
    def core(s: str) -> str:
        s = _PUNCT.sub(" ", norm_text(s).lower())
        words = [w for w in s.split() if w not in _NOISE and len(w) > 1]
        return " ".join(sorted(set(words)))

    return f"{core(company or '')}|{core(title or '')}"


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
        return dup_key(self.company, self.title)

    def salary_str(self) -> str:
        """Человекочитаемая вилка. Пустая строка — значит вилки нет, и это факт для карточки."""
        if self.salary_from is None and self.salary_to is None:
            return ""
        cur = self.currency or ""
        fmt = lambda n: f"{n:,}".replace(",", " ")
        if self.salary_from and self.salary_to:
            body = f"{fmt(self.salary_from)}–{fmt(self.salary_to)}"
        elif self.salary_from:
            body = f"от {fmt(self.salary_from)}"
        else:
            body = f"до {fmt(self.salary_to)}"
        gross = "" if self.salary_gross is None else (" gross" if self.salary_gross else " net")
        return f"{body} {cur}{gross}".strip()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dup_key"] = self.dup_key
        return d

    def to_row(self) -> dict:
        d = self.to_dict()
        d["tags"] = json.dumps(self.tags, ensure_ascii=False)
        d["raw"] = json.dumps(self.raw, ensure_ascii=False, default=str)
        return d
