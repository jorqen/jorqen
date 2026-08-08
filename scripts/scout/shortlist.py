"""shortlist — дельта, свёрнутая до списка, по которому модель сразу пишет карточки.

Зачем команда существует. Раньше конвейер выглядел так: `scan` писал отчёт
markdown на 3 МБ, а модель раскладывала подагентов ВЫЧИТЫВАТЬ этот markdown
обратно в структуру. Живой прогон 04.08.2026: 14 агентов и ~2,6 млн токенов
ушло на то, чтобы получить 711 строк, которые всё это время лежали в SQLite
в готовом виде. Это чистая потеря: разбор собственного вывода.

`shortlist` делает ту же работу детерминированно и бесплатно:

1. **дельта из базы** — те же фильтры, что у `new`;
2. **схлопывание дублей** — одна вакансия с пяти площадок становится ОДНОЙ
   строкой со списком источников (ключ: нормализованная пара компания+роль,
   плюс `dup_key` площадок);
3. **отсев отработанного** — по `decision` и по `negotiation` (отклик, отказ,
   приглашение): то, что модель раньше сверяла глазами по блоку «уже отработано»;
4. **требуемый стаж** вытаскивается регуляркой из выжимки `detail` — главный
   критерий отбора, который модель иначе выясняет чтением каждой вакансии;
5. **контакт работодателя** подставляется из кэша `employer_channel`, если он
   был найден в прошлые прогоны.

Что команда НЕ делает: не оценивает фит, не решает, кому писать, не сочиняет
карточки. Отбор — работа модели; здесь только механика.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse

from . import store
from .sources import ATS_ROLE_RE

# ── Требуемый стаж ────────────────────────────────────────────────────────────
# Все формы, встреченные живьём на 22 площадках. Порядок важен: сначала более
# специфичные (с указанием языка), потом общие.
_YEARS_RE = (
    re.compile(r"опыт[^.]{0,70}?(?:от|более)\s+(\d+)[\s-]*(?:х|ти)?\s*лет", re.I),
    re.compile(r"(?:от|более)\s+(\d+)\s*(?:\+\s*)?лет[^.\n]{0,30}?опыт", re.I),
    # «N+ years» засчитывается ТОЛЬКО рядом со словом про опыт: на живой вакансии
    # «delivering technology at scale for 20+ years» — это история компании,
    # и max() по всем совпадениям делал из неё требование в 20 лет стажа.
    re.compile(r"(?:experience|опыт|стаж|worked|разработк\w*)[^.\n]{0,60}?"
               r"(\d+)\s*\+\s*(?:years?|лет|года?)", re.I),
    re.compile(r"(\d+)\s*\+\s*(?:years?|лет|года?)[^.\n]{0,40}?"
               r"(?:experience|опыт|стаж|разработк\w*)", re.I),
    re.compile(r"at least\s+(\d+)\s+years?", re.I),
    # У диапазона важна ВЕРХНЯЯ граница: «3–5 years of experience» — это 5,
    # а не 3. Раньше вторая цифра матчилась, но не захватывалась, и функция
    # занижала требование вопреки собственному правилу «берём максимум».
    re.compile(r"\d+\s*(?:-|–|—)\s*(\d+)\s*years? of experience", re.I),
    re.compile(r"минимум\s+(\d+)\s*(?:лет|года?)", re.I),
)

# hh отдаёт бакет опыта отдельным полем — он режет резюме автофильтром, поэтому
# важен не меньше текста. between3And6 → 3, moreThan6 → 6.
_HH_BUCKET = {"noExperience": 0, "between1And3": 1, "between3And6": 3, "moreThan6": 6}
# hh кладёт в выжимку человекочитаемую форму («3–6 лет», «более 6 лет»), а не ключ API.
_HH_HUMAN = re.compile(r"(?:более\s+)?(\d+)\s*(?:–|-|—)?\s*(?:\d+)?\s*лет", re.I)


def required_years(payload: dict | None) -> int | None:
    """Наибольший из названных порогов стажа. None — порог не назван вовсе.

    Берём МАКСИМУМ, а не первое совпадение: в одной вакансии часто стоят и
    «опыт от 3 лет», и «Go от 5 лет» — отсекает именно старшее требование."""
    if not payload:
        return None
    found: list[int] = []
    bucket = (payload.get("extra") or {}).get("experience")
    if isinstance(bucket, str):
        if bucket in _HH_BUCKET:
            found.append(_HH_BUCKET[bucket])
        else:
            m = _HH_HUMAN.search(bucket)
            if m:
                found.append(int(m.group(1)))
    # Переносы строк схлопываем ДО поиска: в живой вакансии Ozon требование
    # выглядит как «важен опыт:\n\n• коммерческой бэкенд-разработки от 3 лет»,
    # и шаблон с [^.\n] на нём не срабатывал — стаж молча оставался «не назван».
    text = " ".join(str(payload.get(k) or "")
                    for k in ("requirements", "description", "title")).replace("\n", " ")
    for rx in _YEARS_RE:
        for m in rx.finditer(text):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 < n <= 20:          # 30+ лет — это не стаж, а мусор разметки
                found.append(n)
    return max(found) if found else None


# ── Право на работу: маркеры, которые нельзя пропустить ──────────────────────
_RTW_RE = (
    (re.compile(r"nationals? of|citizenship|citizens? of|гражданств", re.I),
     "гражданство"),
    (re.compile(r"work (?:authorization|permit|eligibility)|right to work|"
                r"authorized to work|разрешени[ея] на работу|право на работу", re.I),
     "право на работу"),
    (re.compile(r"(?:without|no) (?:visa )?sponsorship|not sponsor", re.I),
     "без спонсорства визы"),
    (re.compile(r"sponsor(?:ship)? (?:is )?(?:available|provided)|"
                r"we can sponsor|visa support|релокацион", re.I),
     "релокация/виза есть"),
    (re.compile(r"excluding russia|вне (?:РФ|России)|кроме (?:РФ|России)", re.I),
     "вне РФ"),
)


def rtw_flags(payload: dict | None) -> str:
    """Маркеры права на работу — по ним модель решает, барьер это или гео-метка."""
    if not payload:
        return ""
    text = " ".join(str(payload.get(k) or "")
                    for k in ("requirements", "description", "apply_note"))
    hits = [label for rx, label in _RTW_RE if rx.search(text)]
    return ", ".join(dict.fromkeys(hits))


# ── Профиль: своя профессия против чужой ─────────────────────────────────────
# Берём ту же регулярку, что и ATS-доски (ATS_ROLE_RE): две регулярки на один
# вопрос расходятся всегда, это уже проверено замером на 4113 заголовках.
# Здесь она работает как ФИЛЬТР, а не как сортировка, поэтому рядом обязателен
# счётчик отсеянного: «тихо потерял» и «отфильтровал» — разные вещи.
_OFF_PROFILE = re.compile(
    r"\b(qa|тестировщ\w*|автотест\w*|sdet|аналитик\w*|analyst|data scientist|"
    r"дизайн\w*|design(er)?|продакт|product manager|project manager|менеджер|"
    r"маркет\w*|marketing|sales|прода(?:ж|вец)\w*|recruit\w*|рекрут\w*|hr\b|"
    r"бухгалт\w*|юрист|支持|поддержк\w*|support engineer|"
    r"android|ios\b|мобильн\w*|mobile|frontend|фронтенд\w*|front-end|"
    r"1c|1с\b|копирайт\w*|контент|smm)\b", re.I)


def on_profile(title: str) -> bool:
    """Профильная ли роль. Перекос сознательно в сторону лишнего: пропустить
    чужую вакансию дешевле, чем потерять свою."""
    t = title or ""
    if _OFF_PROFILE.search(t) and not re.search(r"\b(go|golang|бэкенд|backend)\b", t, re.I):
        return False
    return bool(ATS_ROLE_RE.search(t))


# ── Схлопывание дублей ────────────────────────────────────────────────────────
_NORM_RE = re.compile(r"[^a-zа-я0-9]+")
# 🔴 Грейдовые слова (senior/middle/lead/старший/ведущий) здесь НАМЕРЕННО
# отсутствуют. Когда они входили в шум, «Backend Engineer - Cards» и «Senior
# Backend Engineer - Cards» у SumUp давали один ключ и схлопывались в одну
# строку — младшая позиция исчезала из выдачи совсем (190 таких групп на живой
# базе). Показать две строки по одной вакансии не страшно, потерять открытую
# позицию — самая дорогая ошибка этого проекта.
_ROLE_NOISE = re.compile(
    r"\b(разработчик|developer|engineer|инженер|программист|backend|бэкенд|"
    r"go|golang|специалист|remote|удал[её]нно|м/ж|m/f|f/m)\b", re.I)


def norm(s: str | None) -> str:
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def dup_group(row: dict) -> str:
    """Ключ склейки: компания + роль без грейдовых и языковых слов.

    Автоматическую склейку по похожести ТЕКСТА мы уже пробовали и отказались
    (коэффициент Дайса 0.29 на паре, которая была одной вакансией). Здесь склейка
    консервативная и объяснимая: тот же работодатель + та же роль после снятия
    шума. Компания пустая → строка не склеивается ни с чем, кроме своего dup_key:
    у нераскрытых работодателей одинаковых заголовков много, а вакансии разные."""
    company = norm(row.get("company"))
    title = row.get("title") or ""
    role = norm(_ROLE_NOISE.sub(" ", title))
    if not company:
        return f"~{row.get('dup_key') or row.get('source')}:{row.get('external_id')}"
    if not role:
        # Заголовок состоит ТОЛЬКО из шумовых слов («Senior Backend Engineer»),
        # и после чистки от него ничего не осталось. Склеивать по пустой роли
        # нельзя: живьём так слиплись «Backend Engineer - Cards» и «Senior
        # Backend Engineer - Cards» у SumUp — две разные открытые позиции, и
        # младшая исчезала из выдачи совсем (190 таких групп на реальной базе).
        role = norm(title)
    return f"{company}|{role}"


# ── Третий слой дедупа: похожесть описаний (SimHash) ─────────────────────────
#
# Первые два слоя — ключ «компания + роль» (здесь) и `dup_key` площадок (в model).
# Они ловят одну вакансию, размещённую на пяти площадках под одинаковым названием.
# Чего они не ловят: ту же вакансию, названную по-разному («Go-разработчик» на hh
# и «Backend Engineer (Golang)» на getmatch) — роль после чистки шума разная,
# и группы не сходятся.
#
# SimHash сравнивает ОПИСАНИЯ. Он включается только там, где ошибиться дёшево:
#
#   * только внутри ОДНОЙ компании — межкомпанейские склейки запрещены вовсе;
#   * только при СОВПАДАЮЩЕМ грейде — правило «разные грейды одной команды это
#     разные вакансии» не трогается ничем (инцидент SumUp: две реальные вакансии
#     слиплись в одну, и младшая исчезла из выдачи совсем);
#   * только при достаточно длинном описании — на трёх строках любой текст похож
#     на любой;
#   * порог — ручка (`--simhash-bits`), а не константа в коде.
#
# Решение сохраняется в `dup_decision` и в следующей волне не пересчитывается:
# пересчёт молча меняет выдачу от прогона к прогону, а запись можно прочитать
# и оспорить.

SIMHASH_BITS = 64
# Максимальное расстояние Хэмминга, при котором два описания считаются одним
# текстом. 3 из 64 — это ~95% совпадения. Порог намеренно жёсткий: цена лишнего
# раскола — одна лишняя строка в выдаче, цена лишней склейки — потерянная вакансия.
SIMHASH_MAX_DIST = 3
# Короче этого описания не сравниваем: на коротком тексте SimHash сближает всё.
SIMHASH_MIN_CHARS = 400


def _shingles(text: str, size: int = 4) -> list[str]:
    words = re.findall(r"[a-zа-яё0-9]+", (text or "").lower())
    if len(words) < size:
        return words
    return [" ".join(words[i:i + size]) for i in range(len(words) - size + 1)]


def simhash(text: str, *, bits: int = SIMHASH_BITS) -> int:
    """SimHash описания. 0 — сравнивать не по чему.

    Свой, а не из библиотеки: ядро сборщика на stdlib, а алгоритм — двадцать
    строк. `blake2b` вместо `hash()`: встроенный hash солится на каждый запуск
    процесса, и значения не совпали бы между прогонами — то есть сохранённое
    решение о дубле нельзя было бы сверить с пересчитанным.
    """
    import hashlib  # noqa: PLC0415

    shingles = _shingles(text)
    if not shingles:
        return 0
    vector = [0] * bits
    for sh in shingles:
        h = int.from_bytes(
            hashlib.blake2b(sh.encode("utf-8"), digest_size=bits // 8).digest(), "big")
        for i in range(bits):
            vector[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i, v in enumerate(vector):
        if v > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def grade_of(title: str) -> str:
    """Грейд из названия, нормализованный. Пустая строка — грейд не назван.

    Нужен ровно для одного: НЕ склеивать «Backend Engineer» и «Senior Backend
    Engineer». Это правило в проекте оплачено потерей живых вакансий и
    не смягчается никакими порогами похожести."""
    from .model import GRADE_WORDS  # noqa: PLC0415 — один список на проект

    words = set(re.findall(r"[a-zа-яё]+", (title or "").lower()))
    hit = sorted(words & GRADE_WORDS)
    # «senior» и «старший» — один грейд, названный на разных языках.
    ru2en = {"старший": "senior", "ведущий": "lead", "главный": "principal",
             "младший": "junior", "стажер": "intern", "стажёр": "intern"}
    return ",".join(sorted({ru2en.get(w, w) for w in hit}))


def _key(row: dict) -> str:
    return f"{row.get('source')}:{row.get('external_id')}"


def similar_groups(groups: list[dict], *, max_dist: int = SIMHASH_MAX_DIST,
                   decisions: dict | None = None) -> list[tuple[str, str, str]]:
    """Какие группы описывают одну вакансию. Возвращает [(ключ_а, ключ_б, причина)].

    Сравниваются только пары из одной компании с одинаковым грейдом —
    см. комментарий к слою выше. Уже записанное решение (`decisions`) считается
    окончательным и не пересчитывается.
    """
    decisions = decisions or {}
    by_company: dict[str, list[dict]] = {}
    for g in groups:
        company = norm(g.get("company"))
        if not company:
            continue                 # нет работодателя — доказательства дубля нет
        by_company.setdefault(company, []).append(g)

    pairs: list[tuple[str, str, str]] = []
    for company, items in by_company.items():
        if len(items) < 2:
            continue
        prepared = []
        for g in items:
            text = g.get("_dup_text") or g.get("description") or ""
            if len(text) < SIMHASH_MIN_CHARS:
                continue
            prepared.append((g, grade_of(g.get("title") or ""), simhash(text)))
        for i in range(len(prepared)):
            for j in range(i + 1, len(prepared)):
                (ga, grade_a, ha), (gb, grade_b, hb) = prepared[i], prepared[j]
                if grade_a != grade_b:
                    continue         # разные грейды — разные вакансии, точка
                ka, kb = _key(ga), _key(gb)
                pair = (ka, kb) if ka <= kb else (kb, ka)
                known = decisions.get(pair)
                if known:
                    if known.get("verdict") == "same":
                        pairs.append((ka, kb, f"решено раньше: {known.get('reason')}"))
                    continue
                dist = hamming(ha, hb)
                if dist <= max_dist:
                    pairs.append((ka, kb,
                                  f"описания совпадают на {(64 - dist) / 64:.0%} "
                                  f"(simhash, расстояние {dist})"))
    return pairs


# Параметры, которые НЕ ЧАСТЬ адреса вакансии, а метки того, как мы на неё попали.
# Список явный и закрытый: неизвестный параметр СОХРАНЯЕТСЯ. Направление ошибки
# выбрано осознанно — лишний раскол стоит одной строки в выдаче, а склейка двух
# разных вакансий из-за срезанного идентификатора стоит вакансии. Поэтому здесь
# нет «срезать всё, кроме известного»: если площадка положит id в незнакомый
# параметр, мы его не тронем.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "utm_id", "utm_name", "utm_referrer",
    # LinkedIn: позиция в выдаче и идентификаторы показа — меняются КАЖДЫЙ обход,
    # поэтому одна и та же вакансия из linkedin и из dreamoffer никогда
    # не совпадала по адресу. На живой базе это 733 несклеенные группы.
    "trackingid", "refid", "position", "pagenum", "trk", "trkinfo",
    "originalsubdomain", "eblocaltoken",
    # Общие метки перехода.
    "ref", "referer", "referrer", "from", "src", "fbclid", "gclid", "yclid",
    "_ga", "mc_cid", "mc_eid",
    # getmatch-бот: метка того, из какого блока поста пришёл клик.
    "s",
})


def canonical_url(url: str | None) -> str | None:
    """Адрес без меток перехода: схема и хост в нижнем регистре, якорь снят,
    трекинговые параметры вырезаны, остальные — СОХРАНЕНЫ и отсортированы.

    Сортировка нужна ради устойчивости: `?a=1&b=2` и `?b=2&a=1` — одна страница,
    и без сортировки они дали бы разные ключи.
    """
    if not url:
        return None
    u = str(url).strip()
    if not u.startswith("http"):
        return None
    try:
        parts = urllib.parse.urlsplit(u.split("#", 1)[0])
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query,
                                                          keep_blank_values=True)
                 if k.lower() not in _TRACKING_PARAMS]
        return urllib.parse.urlunsplit((
            parts.scheme.lower(), parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            urllib.parse.urlencode(sorted(query)), ""))
    except ValueError:
        return u.split("#", 1)[0].rstrip("/").lower()


def same_url_key(row: dict) -> str | None:
    """Ключ «это буквально одна и та же страница», или None.

    Самое сильное доказательство дубля из существующих — и единственное, которое
    не может ошибиться в опасную сторону: одна страница не бывает двумя разными
    грейдами. Поэтому оно работает ДАЖЕ когда работодатель не назван, где все
    остальные слои сознательно молчат.

    Живой случай №1: пост `t.me/runello_rus_goland/1527` приезжает тремя записями —
    из самого канала (`tg:runello_rus_goland`), из dreamoffer и из shadowhint:
    оба агрегатора перепечатывают телеграм-посты и отдают ТУ ЖЕ ссылку. Компании
    ни у одной из трёх нет, поэтому ни `dup_group`, ни SimHash их не склеивали,
    и в топ-30 одна вакансия занимала три строки.

    Живой случай №2 (замер по базе 05.08.2026): у LinkedIn в адресе едут
    `trackingId`, `refId`, `position` и `pageNum` — они меняются при каждом
    обходе, поэтому одна вакансия из `linkedin` и из `dreamoffer` не совпадала
    по адресу НИКОГДА. Без канонизации не склеивались 733 группы.
    """
    return canonical_url(row.get("url"))


def merge(rows: list[dict]) -> list[dict]:
    """Схлопывает дубли, сохраняя ВСЕ источники и лучшую вилку.

    Два ключа, а не один: сначала совпадение АДРЕСА (одна страница — одна
    вакансия, что бы ни было в полях), потом обычный «компания + роль».
    Объединяются они через union-find: если A и B — одна страница, а B и C
    совпали по компании с ролью, то все три обязаны попасть в одну группу,
    а не в две.

    Базой группы становится САМАЯ РАННЯЯ запись, а не первая попавшаяся из
    выборки: порядок строк SQL не гарантирован, и без явной сортировки один и
    тот же прогон на той же базе мог бы дать разный «канон» — а значит, разные
    ссылки в карточках. Тот же принцип у freehire: канон обязан быть старше
    любой новой строки, иначе онлайн- и батч-склейка дерутся между собой."""
    ordered = sorted(rows, key=lambda x: (x.get("first_seen") or "",
                                          x.get("source") or "",
                                          str(x.get("external_id") or "")))
    # Union-find по двум признакам сразу.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for r in ordered:
        own = f"@row:{r.get('source')}:{r.get('external_id')}"
        find(own)
        union(own, f"@key:{dup_group(r)}")
        url_key = same_url_key(r)
        if url_key:
            union(own, f"@url:{url_key}")

    groups: dict[str, dict] = {}
    for r in ordered:
        key = find(f"@row:{r.get('source')}:{r.get('external_id')}")
        g = groups.get(key)
        if g is None:
            g = dict(r)
            g["_sources"] = []
            g["_urls"] = []
            g["_locations"] = []
            groups[key] = g
        g["_sources"].append(r.get("source"))
        g["_urls"].append(r.get("url"))
        # Города группы. Собираются потому, что 82% всего, что прячет дедуп на
        # живой базе (1100 строк из 1329), — это ОДНА вакансия одной компании,
        # размещённая в разных городах: adesso SE даёт «Software Engineer
        # Defense» в 30 немецких городах, Bending Spoons — «Graduate software
        # engineer» в 20 городах пяти стран. Схлопывать их правильно: 30 строк
        # об одной работе — шум. Но показывать город ОДНОЙ выжившей строки —
        # вранье: канон выбирается по first_seen, и в таблицу попадал Штральзунд
        # при том, что та же вакансия открыта в Берлине.
        if r.get("location"):
            g["_locations"].append(r["location"])
        # Вилка: побеждает та, где есть цифры (площадки часто отдают пустую).
        if not g.get("salary_from") and r.get("salary_from"):
            for k in ("salary_from", "salary_to", "currency", "salary_period",
                      "salary_gross"):
                g[k] = r.get(k)
        if not g.get("company") and r.get("company"):
            g["company"] = r["company"]
        if not g.get("employer_url") and r.get("employer_url"):
            g["employer_url"] = r["employer_url"]
    return list(groups.values())


# ── Совпадение с профилем: детерминированно, без модели ──────────────────────
# Правило знаменателя подсмотрено у freehire (cvmatch, «No LLM, ever»): то, чего
# в тексте вакансии НЕТ, уходит из знаменателя, а не получает ноль — вакансия без
# описания это «неизвестно», а не «не подходит».
#
# 🔴 Совпадения ищутся ТОЛЬКО по границам слов. Подстрочный поиск здесь —
# готовый источник вранья: «go» лежит внутри «Django», «algorithm» и «Diego»,
# и на первом же прогоне Scala-вакансия Codacy получила 100 из 100.
_WORD_CACHE: dict[str, re.Pattern] = {}


def _has(text: str, term: str) -> bool:
    rx = _WORD_CACHE.get(term)
    if rx is None:
        # Термины со слэшем и пробелом (ci/cd, service mesh) — как есть,
        # остальные — по границам слова с учётом русской морфологии.
        body = re.escape(term).replace(r"\ ", r"\s+")
        rx = re.compile(rf"(?<![\w]){body}(?![\w])" if term.isascii()
                        else rf"(?<![а-яё]){body}", re.I)
        _WORD_CACHE[term] = rx
    return bool(rx.search(text))


PROFILE_CORE = ("kubernetes", "k8s", "postgresql", "postgres", "kafka", "grpc",
                "redis", "docker", "ci/cd", "микросервис", "highload",
                "высоконагруж", "распределённ", "распределенн")
PROFILE_PLUS = ("istio", "service mesh", "mtls", "tls", "mqtt", "helm",
                "prometheus", "grafana", "observability", "наблюдаемост",
                "clickhouse", "nats", "rabbitmq", "terraform", "linux",
                "llm", "платформ", "infra", "инфраструктур")
# Чужой основной язык в ЗАГОЛОВКЕ — сильный сигнал, что роль не Go-шная.
# \b после «#», «+» и точки не срабатывает никогда: `\bc#\b` не матчит «C#».
# Из-за этого штраф за чужой основной язык молча не применялся к 222 живым
# заголовкам с C#/.NET/C++ — а скоринг теперь главный ключ сортировки.
_OTHER_LANG = re.compile(
    r"(?<![\w#+.])(scala|java(?!script)|python|php|ruby|rails|c#|\.net|"
    r"node\.?js|kotlin|swift|c\+\+|rust|elixir|perl)(?![\w+#])", re.I)
_GO_RE = re.compile(r"\b(go|golang|голанг)\b", re.I)

# Во сколько раз чужой язык должен пересилить Go в тексте, чтобы считаться
# ЯЗЫКОМ РОЛИ, а не соседней командой. Три — не круглое число ради красоты:
# при двукратном перевесе под штраф попадали живые Go-вакансии, где перечислен
# весь стек компании («Go, а также Java-сервисы и Python-скрипты»); при
# трёхкратном остаются только те, где Go упомянут вскользь.
_LANG_DOMINANCE = 3
# Считаем по тем же словам, что и _OTHER_LANG, но по отдельности — нужно знать,
# КАКОЙ язык преобладает, а не просто «какой-то чужой есть».
_LANG_COUNTERS = (
    ("Java", re.compile(r"(?<![\w#+.])java(?!script)(?![\w+#])", re.I)),
    ("Python", re.compile(r"(?<![\w#+.])python(?![\w+#])", re.I)),
    ("C#/.NET", re.compile(r"(?<![\w#+.])(?:c#|\.net)(?![\w+])", re.I)),
    ("PHP", re.compile(r"(?<![\w#+.])php(?![\w+#])", re.I)),
    ("Ruby", re.compile(r"(?<![\w#+.])(?:ruby|rails)(?![\w+#])", re.I)),
    ("Node.js", re.compile(r"(?<![\w#+.])node\.?js(?![\w+#])", re.I)),
    ("Scala", re.compile(r"(?<![\w#+.])scala(?![\w+#])", re.I)),
    ("Kotlin", re.compile(r"(?<![\w#+.])kotlin(?![\w+#])", re.I)),
    ("Rust", re.compile(r"(?<![\w#+.])rust(?![\w+#])", re.I)),
    ("C++", re.compile(r"(?<![\w#+.])c\+\+(?![\w+#])", re.I)),
)


def _dominant_other_lang(body: str) -> tuple[str | None, str]:
    """(язык, «5 против 1») — чужой язык, ЯВНО преобладающий над Go в тексте.

    Возвращает None, когда Go упомянут сопоставимо часто: тогда чужой язык —
    это часть стека компании, а не язык роли, и понижать балл не за что.
    Живая проверка правила: «Backend Developer» с Java по всему тексту и одним
    «знание Go будет плюсом» — не наша вакансия, и раньше она не понижалась
    вовсе, потому что штраф смотрел ТОЛЬКО в заголовок.
    """
    if not body:
        return None, ""
    go = len(_GO_RE.findall(body))
    best_lang, best_n = None, 0
    for name, rx in _LANG_COUNTERS:
        n = len(rx.findall(body))
        if n > best_n:
            best_lang, best_n = name, n
    if not best_lang or best_n < 2:
        return None, ""
    if best_n < max(1, go) * _LANG_DOMINANCE:
        return None, ""
    return best_lang, f"{best_n} против {go}"


def match_score(row: dict, payload: dict | None) -> tuple[int | None, str]:
    """(0..100, пояснение) или (None, 'нет данных') — если судить не по чему."""
    title = (row.get("title") or "") + " " + str((payload or {}).get("title") or "")
    body = " ".join(str((payload or {}).get(k) or "")
                    for k in ("requirements", "description"))
    if len(body.strip()) < 80:
        return None, "нет данных: выжимки нет, судить не по чему"
    full = f"{title} {body}".lower()

    # 1. Go — основной критерий поиска, поэтому он и весит больше всего.
    if _GO_RE.search(title.lower()):
        score, why = 45, "Go в названии"
    elif _GO_RE.search(body.lower()):
        score, why = 28, "Go в тексте"
    else:
        score, why = 0, "Go не упомянут"

    # 2. Стек. Ядро дороже, «плюсы» дешевле — но и то и другое только по словам.
    core = [w for w in PROFILE_CORE if _has(full, w)]
    plus = [w for w in PROFILE_PLUS if _has(full, w)]
    score += min(36, len(core) * 6) + min(14, len(plus) * 2)

    # 3. Чужой основной язык в заголовке — почти всегда не наша роль.
    other = _OTHER_LANG.search(title)
    if other and not _GO_RE.search(title.lower()):
        score = max(0, score - 30)
        why += f"; в названии {other.group(0)}"
    else:
        # Заголовок нейтральный («Backend Developer»), но язык роли виден в теле.
        # Решение принято осознанно и слабее заголовочного: упоминание чужого
        # языка в тексте — это ещё не «чужая роль» (у Go-вакансии в стеке часто
        # стоит «есть сервисы на Java»). Штраф даётся, только когда чужой язык
        # ЯВНО ПРЕОБЛАДАЕТ над Go по числу упоминаний — тогда это язык роли,
        # а не соседней команды. Тридцати очков, как за заголовок, здесь не даём:
        # текст менее надёжен, и перепутать «упомянут» с «основной» дороже.
        lang, ratio = _dominant_other_lang(body)
        if lang:
            score = max(0, score - 15)
            why += f"; в тексте преобладает {lang} ({ratio})"



    # 4. Требуемый стаж выше формальных пяти лет — понижаем, но не прячем:
    #    решение по такой вакансии всё равно за пользователем.
    years = required_years(payload)
    if years is not None and years > 5:
        score = max(0, score - (years - 5) * 12)
        why += f"; требуют {years} лет"

    if core:
        why += f"; стек: {', '.join(core[:4])}"
    return max(0, min(100, score)), why


# ── Сверка с историей ─────────────────────────────────────────────────────────

_WORKED = {"applied", "rejection", "invitation", "interview", "viewed", "not_viewed"}


def worked_index(negotiations: list[dict]) -> dict[str, list[str]]:
    """{нормализованная компания: [статусы]} — по чему сверяем «уже отработано».

    Статус `other` не участвует: среди них рекламные рассылки, и по ним вакансию
    объявляли отработанной ошибочно."""
    idx: dict[str, list[str]] = {}
    for n in negotiations:
        if n.get("status") not in _WORKED:
            continue
        key = norm(n.get("company") or n.get("company_key"))
        if not key:
            continue
        idx.setdefault(key, []).append(
            f"{n.get('status')} {(n.get('event_at') or '')[:10]}".strip())
    return idx


def own_text_payload(row: dict) -> dict | None:
    """Псевдо-выжимка из текста самой записи, когда страницу мы не качали.

    Зачем. `detail` есть только у того, что прошло `enrich` — а `enrich` ходит
    в сеть и ограничен лимитом. У телеграм-постов страницы нет вовсе: пост САМ
    и есть описание, оно уже лежит в базе целиком. Пока стаж, право на работу
    и фит читались исключительно из `detail`, все 187 телеграм-строк приезжали
    в шорт-лист с пустыми колонками «Стаж», «RTW» и «Фит» — то есть без всех
    трёх признаков, по которым и делается отбор. Это не отсев, а хуже: строка
    показана, но судить по ней нечем, и модель идёт читать пост руками.

    `raw["text"]` предпочтительнее `description`: описание модель обрезает
    до 2000 символов, а требования к стажу в длинном посте стоят внизу.
    """
    text = ""
    raw = row.get("raw")
    if isinstance(raw, str) and raw:
        try:
            text = (json.loads(raw) or {}).get("text") or ""
        except (TypeError, ValueError):
            text = ""
    elif isinstance(raw, dict):
        text = raw.get("text") or ""
    text = text or row.get("description") or ""
    if len(text.strip()) < 80:
        return None
    return {"title": row.get("title") or "", "description": text}


def _collapse_similar(groups: list[dict], db: str, *,
                      max_dist: int) -> tuple[list[dict], int]:
    """Схлопывает группы, чьи ОПИСАНИЯ совпадают. Возвращает (группы, сколько склеено).

    Решение по каждой паре записывается в `dup_decision` — и «одна вакансия»,
    и «разные». Второе не менее важно: без записи «разные» следующая волна
    пересчитает пару заново и может решить иначе, а человек, разобравший её
    руками, увидит, что его правку отменили.
    """
    if max_dist < 0 or len(groups) < 2:
        return groups, 0
    try:
        with store.connect(db) as conn:
            known = store.dup_decisions(conn)
            pairs = similar_groups(groups, max_dist=max_dist, decisions=known)
            for ka, kb, why in pairs:
                store.save_dup_decision(conn, ka, kb, "same", reason=why)
    except Exception:  # noqa: BLE001 — дедуп не имеет права ронять выдачу
        return groups, 0
    if not pairs:
        return groups, 0

    # Union-find: пара (a,b) и пара (b,c) должны дать одну группу, а не две.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for ka, kb, _ in pairs:
        union(ka, kb)

    out: list[dict] = []
    by_root: dict[str, dict] = {}
    collapsed = 0
    for g in groups:
        root = find(_key(g)) if _key(g) in parent else None
        if root is None:
            out.append(g)
            continue
        base = by_root.get(root)
        if base is None:
            by_root[root] = g
            out.append(g)
            continue
        # Схлопываем в уже добавленную группу, сохраняя ВСЕ источники и ссылки:
        # у схлопнутой группы человек должен видеть, из скольких мест она пришла.
        base["_sources"] = list(base.get("_sources") or []) + list(g.get("_sources") or [])
        base["_urls"] = list(base.get("_urls") or []) + list(g.get("_urls") or [])
        if not base.get("salary_from") and g.get("salary_from"):
            for k in ("salary_from", "salary_to", "currency", "salary_period",
                      "salary_gross"):
                base[k] = g.get(k)
        base["_near_dup"] = True
        collapsed += 1
    return out, collapsed


def build(db: str, *, since: str | None, by: str = "seen",
          sources: list[str] | None = None, limit: int = 0,
          simhash_bits: int = SIMHASH_MAX_DIST) -> dict:
    """Собирает шорт-лист. Возвращает {rows, stats} — печать отдельно.

    `simhash_bits` — ручка третьего слоя дедупа: максимальное расстояние
    Хэмминга между описаниями, при котором записи считаются одной вакансией.
    Отрицательное значение выключает слой целиком.
    """
    kw = dict(since=since if by == "published" else None,
              first_seen_since=since if by == "seen" else None,
              sources=sources, exclude_decided=True)
    with store.connect(db) as conn:
        total = store.count(conn, **kw)
        rows = store.query(conn, limit=0, **kw)
        negs = store.negotiations(conn)
        details = {(d["source"], d["external_id"]): d
                   for d in _details(conn)}
        channels = _channels(conn)

    kept = [r for r in rows if on_profile(r.get("title") or "")]
    off = len(rows) - len(kept)
    off_examples = [r.get("title") for r in rows
                    if not on_profile(r.get("title") or "")][:5]
    merged = merge(kept)
    worked = worked_index(negs)

    for g in merged:
        key = (g.get("source"), g.get("external_id"))
        d = details.get(key)
        payload = None
        if d and d.get("payload"):
            try:
                payload = json.loads(d["payload"])
            except (TypeError, ValueError):
                payload = None
        g["_text_src"] = "выжимка" if payload else ""
        if payload is None:
            payload = own_text_payload(g)
            g["_text_src"] = "текст вакансии" if payload else ""
        g["_years"] = required_years(payload)
        g["_score"], g["_score_why"] = match_score(g, payload)
        g["_rtw"] = rtw_flags(payload)
        g["_enriched"] = bool(payload)
        g["_worked"] = worked.get(norm(g.get("company")), [])
        g["_channel"] = channels.get(norm(g.get("company")), "")
        # Текст для сравнения описаний берём тот же, по которому судим о фите:
        # два разных текста на два решения об одной вакансии разошлись бы.
        g["_dup_text"] = (payload or {}).get("description") or ""

    # Третий слой дедупа — по описаниям. Идёт ПОСЛЕ обогащения: до него текста,
    # по которому сравнивать, ещё нет.
    merged, near = _collapse_similar(merged, db, max_dist=simhash_bits)

    # Порядок: свежее и подготовленное — выше. Алфавит по компании как ключ
    # сортировки был бы прямым вредом: наверх всплывали бы «13tm» и «1406
    # Consulting», а не то, что появилось сегодня.
    merged.sort(key=lambda g: (
        bool(g["_worked"]),                       # без истории — раньше
        -(g["_score"] if g["_score"] is not None else -1),  # совпадение с профилем
        not bool(g.get("salary_from")),           # с вилкой — раньше
        -(_freshness(g)),                         # свежее — раньше
    ))
    stats = {"delta": total, "groups": len(merged),
             "off_profile": off, "off_examples": off_examples,
             "collapsed": len(kept) - len(merged),
             "near_dup": near,
             "with_years": sum(1 for g in merged if g["_years"] is not None),
             "scored": sum(1 for g in merged if g["_score"] is not None),
             "worked": sum(1 for g in merged if g["_worked"])}
    if limit:
        merged = merged[:limit]
    return {"rows": merged, "stats": stats}


def _freshness(g: dict) -> int:
    """Дата в виде числа для сортировки: 20260804 > 20260731. Нет даты — 0."""
    raw = (g.get("published_at") or g.get("updated_at") or g.get("first_seen") or "")
    digits = re.sub(r"\D", "", raw)[:8]
    return int(digits) if len(digits) == 8 else 0


def _details(conn) -> list[dict]:
    cur = conn.execute("SELECT source, external_id, payload FROM detail "
                       "WHERE status IN ('ok', 'generic')")
    return [{"source": r[0], "external_id": r[1], "payload": r[2]} for r in cur]


def _channels(conn) -> dict[str, str]:
    """Кэш прямых каналов найма — заполняется командой `employer set`."""
    try:
        cur = conn.execute("SELECT company_key, channel FROM employer_channel")
    except Exception:  # noqa: BLE001 — таблицы может не быть на старой базе
        return {}
    return {r[0]: r[1] for r in cur}


def _money(g: dict) -> str:
    lo, hi = g.get("salary_from"), g.get("salary_to")
    if not lo and not hi:
        return "—"
    cur = g.get("currency") or ""
    per = {"hour": "/час", "month": "/мес", "year": "/год"}.get(g.get("salary_period"), "")
    if lo and hi:
        return f"{lo}–{hi} {cur}{per}".strip()
    return f"от {lo or hi} {cur}{per}".strip()


def render(res: dict, *, fmt: str = "table") -> str:
    """Компактная выдача: одна строка — одна вакансия, без повторов и воды."""
    rows, st = res["rows"], res["stats"]
    if fmt == "json":
        return json.dumps(res, ensure_ascii=False, default=str)
    out = [
        f"# shortlist: {st['groups']} вакансий "
        f"(дельта {st['delta']}, чужая профессия {st['off_profile']}, "
        f"схлопнуто дублей {st['collapsed']}"
        + (f" + {st['near_dup']} по описанию" if st.get("near_dup") else "")
        + f", стаж распознан у {st['with_years']}, "
        f"с историей по компании {st['worked']})",
        "",
        "Отсев по профессии — не тихий: примеры отсеянного — "
        + "; ".join((t or "")[:40] for t in st.get("off_examples") or []) or "—",
        "",
        "Отбор по фиту — не здесь: это механическая свёртка. Колонки: "
        "стаж — МАКСИМАЛЬНЫЙ названный порог (пусто = не назван), "
        "история — что компания уже отвечала, RTW — маркеры права на работу.",
        "",
        "| # | Роль | Компания | Деньги | Формат | Фит | Стаж | RTW | История | Источники | Ссылка |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    multi: list[str] = []
    geo: list[str] = []
    for i, g in enumerate(rows, 1):
        uniq = list(dict.fromkeys(s for s in g["_sources"] if s))
        srcs = ",".join(uniq)
        # Число источников, а не только их имена: «×3» сразу говорит, что
        # вакансия подтверждена тремя площадками, — это признак живой вакансии,
        # а не залежавшегося объявления.
        if len(uniq) > 1:
            srcs = f"×{len(uniq)} {srcs}"
        cities = list(dict.fromkeys(x for x in (g.get("_locations") or []) if x))
        loc = (g.get("location") or "")[:22]
        if len(cities) > 1:
            # «+29» в колонке — единственный признак того, что город в строке
            # не единственный. Без него схлопнутая по городам вакансия выглядит
            # привязанной к одному месту, и решение «не поеду» принимается по
            # ложному факту.
            loc = f"{loc[:16]} +{len(cities) - 1}"
            geo.append(f"  {i}. {(g.get('title') or '')[:40]} — "
                       + " · ".join(c[:28] for c in cities[:8])
                       + (f" (+{len(cities) - 8})" if len(cities) > 8 else ""))
        if g.get("remote"):
            loc = (loc + " remote").strip()
        out.append(
            f"| {i} | {(g.get('title') or '')[:58].replace('|', '/')} "
            f"| {(g.get('company') or '—')[:26].replace('|', '/')} "
            f"| {_money(g)} | {loc.replace('|', '/')} "
            f"| {g['_score'] if g['_score'] is not None else '—'} "
            f"| {g['_years'] if g['_years'] is not None else ''} "
            f"| {g['_rtw'][:34]} "
            f"| {'; '.join(g['_worked'][:2])[:38]} "
            f"| {srcs[:28]} | {g.get('url')} |")
        urls = list(dict.fromkeys(u for u in (g.get("_urls") or []) if u))
        if len(urls) > 1:
            multi.append(f"  {i}. {(g.get('title') or '')[:44]} — "
                         + " · ".join(urls[:5])
                         + (f" (+{len(urls) - 5})" if len(urls) > 5 else ""))
    if not rows:
        out.append("| — | дельта пуста | | | | | | | | | |")
    # Все адреса схлопнутой группы. Раньше `_urls` собирался и не печатался
    # нигде: в таблице стоял ОДИН адрес, а остальные площадки были недостижимы —
    # при том, что на одной из них вакансия может быть жива, а на другой снята.
    if multi:
        out.append("")
        out.append(f"Схлопнутые группы ({len(multi)}) — все адреса, "
                   f"кроме показанного в таблице:")
        out += multi
    if geo:
        out.append("")
        out.append(f"Одна вакансия в нескольких городах ({len(geo)}) — "
                   f"откликаться один раз, но город выбираешь ты:")
        out += geo
    return "\n".join(out)


def cli(args) -> int:
    since = store.since_arg(args.since, db=args.db) if args.since else None
    res = build(args.db, since=since, by=args.by,
                sources=args.sources.split(",") if args.sources else None,
                limit=args.limit,
                simhash_bits=getattr(args, "simhash_bits", SIMHASH_MAX_DIST))
    print(render(res, fmt=args.format))
    if not res["rows"]:
        print("\nдельта пуста — окно слишком узкое или всё уже отработано",
              file=sys.stderr)
        return 1
    return 0
