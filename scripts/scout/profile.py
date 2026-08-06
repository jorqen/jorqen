"""profile — чем рынок отличается от резюме, посчитано по собственной базе.

Две задачи скилла упираются в один и тот же вопрос, и обе решались на глаз:

1. **Точность профиля.** Отбор идёт по `resume/resume.yaml`, но резюме — выжимка.
   Термин, стоящий в списке навыков и ни разу не подтверждённый в опыте, и термин,
   которого нет вовсе, для отбора одинаковы, а для найма — нет. Здесь они наконец
   различаются, и видно, ЧТО именно стоит спросить у пользователя: не «расскажи
   о себе», а «вот эти 6 технологий требуют 200+ вакансий, а резюме про них молчит».
2. **Конверсия.** Что править в резюме, было вопросом вкуса. Теперь это разность
   двух множеств: чего рынок просит часто, а резюме не подтверждает ничем.

Считается ПО РАЗНЫМ ВАКАНСИЯМ И РАЗНЫМ КОМПАНИЯМ, а не по вхождениям: одна
компания, разместившая вакансию на пяти площадках, иначе выглядит как спрос
впятеро больше настоящего. Ключ компании — тот же `shortlist.norm`, что и в
дедупликации, чтобы «Ozon» и «ОЗОН» не считались двумя нанимателями.

Сеть не трогается: всё уже лежит в `.scout/scout.db`.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import card
from .shortlist import _has, norm, on_profile

# ── Словарь спроса ───────────────────────────────────────────────────────────
# Названия технологий в русских вакансиях пишутся латиницей всегда («Kubernetes»,
# «Kafka», «PostgreSQL»), поэтому берём только латинские токены. А вот отличить
# технологию от канцелярита ATS оказалось делом четырёх замеров по живой базе
# (11 866 профильных вакансий), и три из них — тупики. Они записаны здесь, чтобы
# никто не «починил» отбор обратно:
#
# 🔴 Стоп-лист. Первый прогон выдал в топ спроса job description, position,
#    location, role, looking — шапку ATS-шаблона. Догонять канцелярит всех
#    площадок стоп-листом — та же ручная работа, что и вести список технологий,
#    только бесконечная.
# 🔴 Форма слова («заглавная не в начале»). Шапки набирают капсом: JOB
#    DESCRIPTION, POSITION, LOCATION. Проверка на смешанный регистр их убрала,
#    но пропустила Title Case через дефис: Full-Stack, End-to-End, AI-Powered,
#    Île-de-France.
# 🔴 Соседство по строке (слово стоит рядом с термином из резюме). Домашние
#    слова вакансий стоят там же: hands-on — 28% строк с технологией, azure —
#    37%. Порога между ними нет.
# 🔴 Английский словарь (технологии — не слова языка). Половина настоящих имён
#    словарные: python, ruby, rust, react, spring, docker, kafka, prometheus,
#    typescript — все нашлись в /usr/share/dict/words.
#
# Работают два признака, и оба меряются долей, а не фактом «встретилось раз»:
#
# 1. **Имя собственное.** Технология пишется с заглавной ПОСЕРЕДИНЕ фразы
#    («опыт с Azure»), обычное слово — нет («работа с cloud»). Замер: azure
#    100%, terraform 99%, react 99%, kafka 100% против cloud 28%, build 2%,
#    infrastructure 14%, hands-on 2%. Начала строк, буллеты и заголовки в Title
#    Case не в счёт — там заглавная у всех.
# 2. **Перечисление.** Технология стоит в списке стека через разделитель
#    («AWS, Azure», «React / TypeScript»), рядом с заведомой технологией.
#    Именно этот признак и добивает остаток: Job, Location, Format, Salary,
#    Erfahrung пишутся с заглавной посреди фразы, но в перечислениях стека
#    не стоят НИ РАЗУ, как и артефакт вёрстки moreShow и гендерная пометка
#    m/w/d из немецких объявлений.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]{1,24}")
# «we'll», «you're», «Kubernetes's» → «we», «you», «Kubernetes».
_APOS = re.compile(r"['’][A-Za-z]+")
# Разделители перечисления. Пробела среди них нет намеренно: «AWS cloud» —
# не перечисление, а словосочетание, и «cloud» технологией не становится.
_SEP = re.compile(r"^\s*[,;/|&·•+]+\s*$|^\s+(?:and|or|und|oder|et|y|или|и)\s+$", re.I)
# Последний значащий символ перед словом. Буква, цифра, запятая или скобка —
# слово внутри фразы; двоеточие, буллет и начало строки — подпись поля.
_MID = re.compile(r"[A-Za-zЀ-ӿ0-9,)]$")
# Доля заглавных среди слов строки, начиная с которой строка считается
# заголовком: «Job Description», «What You Will Do» — там капс ничего не значит.
_HEADING_RATIO = 0.6
# Знаки перечисления. Строка с ними — список стека, а не заголовок.
_LIST_PUNCT = re.compile(r"[,;]|(?<=\w)\s*/\s*(?=\w)")
# Доля «технологичных» написаний, начиная с которой форма характерна для слова,
# а не случайность одной вакансии.
_SHAPE_RATIO = 0.5
# Доля заглавных посреди фразы, начиная с которой слово — имя собственное.
_CAP_RATIO = 0.7
# Сколько раз слово должно встретиться посреди фразы, чтобы доле верить.
_CAP_MIN_HITS = 10
# Сколько раз слово должно попасть в перечисление стека. Не ноль: одиночное
# совпадение даёт и «Sauer BA | Account Manager».
_LIST_MIN_HITS = 3


def _shape_tech(tok: str) -> bool:
    """Технология по одной лишь форме слова, без статистики.

    Цифра, «+», «#», «/» или camelCase-перелом (строчная, сразу за ней
    заглавная) — так пишутся k8s, s3, oauth2, c++, c#, ci/cd, gRPC, mTLS,
    PostgreSQL, ClickHouse, TypeScript.

    🔴 Именно перелом «строчная→заглавная», а не «заглавная не в начале»:
    капс шапок (JOB DESCRIPTION) ловился на первом варианте правила, Title
    Case через дефис (Full-Stack, AI-Powered) — на втором. Там заглавная
    стоит после дефиса, то есть после НЕ-буквы, и это видно.

    Капсовые аббревиатуры (AWS, SQL, AI, ML) отсюда не теряются: их держат
    эталон из резюме и признак имени собственного.
    """
    return (any(c.isdigit() for c in tok)
            or any(c in "+#" for c in tok)
            # «m/w/d», «h/f» — гендерная пометка немецких и французских
            # объявлений, а не «ci/cd»: у неё все части в одну букву.
            or ("/" in tok and any(len(p) > 1 for p in tok.split("/")))
            or any(a.islower() and b.isupper() for a, b in zip(tok, tok[1:])))

# Латинские слова, которые встречаются в требованиях часто и технологией не являются.
_STOP = frozenset("""
a about above after all also an and any are as at back be been before being both but by
can come could day do does doing done down each even every few first for from get give go
good great had has have he her here him his how i if in into is it its just know like look
make many may me more most much must my new no not now of off on once one only or other
our out over own people plus same say see she should since so some such take team than
that the their them then there these they thing think this those through time to too two
up us use used using very want was way we well were what when where which while who why
will with within work working would year years you your
also etc ie eg vs via per non pre post multi self full part high low new old big small
experience level senior junior middle lead strong deep solid proven hands
english russian remote office hybrid fulltime
""".split())

# Слова из вакансий, которые почти всегда шум требований, а не стек.
_STOP_JOB = frozenset("""
опыт работы знание умение навыки требования обязанности условия задачи проекты команда
разработка разработки разработке технологии стек будет плюсом желательно обязательно
понимание владение готовность способность коммуникация ответственность внимание
development developer engineer engineering software backend frontend fullstack code coding
manager managers architect analyst consultant specialist director recruiter intern internship
skills knowledge understanding ability responsibilities requirements qualifications
bonus advantage nice preferred required minimum degree computer science bachelor master
company product project products projects business customer customers user users client
clients service services system systems platform solution solutions application applications
tools tool technologies technology stack quality performance security support
""".split())


def _is_term(tok: str) -> bool:
    if len(tok) < 2 or tok in _STOP or tok in _STOP_JOB:
        return False
    if tok.isdigit() or re.fullmatch(r"[a-z]\d*", tok):
        return False
    return True


# ── Что подтверждает резюме ──────────────────────────────────────────────────
# Уровни намеренно разные: рекрутёр и ATS видят список навыков, нанимающий
# менеджер ищет подтверждение в пунктах опыта. Термин «в списке, но нигде не
# делался» — это не навык, а заявка, и на интервью её будут ковырять первой.
#
# 🔴 Уровень «сам» отделён от «дела» намеренно, и слить их нельзя. Пет-проект
# и самостоятельное изучение описаны прозой в «Дополнительной информации» —
# это НЕ пустая строчка списка, человек про них рассказывает. Но и за
# коммерческий опыт их выдавать запрещено (см. память проекта про Python
# и WebRTC), а один общий уровень «дело» ровно это и делал бы.
LEVELS = {"работа": 3, "дело": 2, "сам": 1.5, "язык": 1.2, "список": 1, "—": 0}
LEVEL_NOTE = {
    "работа": "в стеке места работы",
    "дело": "названо в пункте опыта",
    "сам": "пет-проект или самостоятельное изучение, не коммерческий опыт",
    "язык": "разговорный язык, подтверждается не пунктом опыта",
    "список": "только в списке навыков, ничем не подтверждено",
    "—": "в резюме нет",
}
# Группа разговорных языков в `skills`. 🔴 Её пункты обязаны иметь свой
# уровень: «english» просят 191 компания, и в списке «заявлено, но ничем не
# подтверждено» он вставал на третье место — вместе с «native» и «b2»,
# растащенными из «Russian (Native)» и «English (B2 …)». Совет этого раздела
# («назови делом в пункте опыта») к владению языком неприменим, а место
# в начале списка правок он занимал.
_SPOKEN_TITLES = ("spoken languages", "разговорные языки")


def resume_evidence(path: str = card.RESUME_PATH) -> tuple[dict[str, str], str | None]:
    """{термин: уровень подтверждения}. Термины — те же, что видит `card`."""
    data, why = card._resume(path)
    if why:
        return {}, why

    exp = (data.get("experience") or {}).get("items") or []

    # Сплошной текст опыта: пункты highlights + summary. По нему проверяем,
    # подтверждён ли термин делом, а не только перечислен.
    prose: list[str] = []
    for item in exp:
        for key in ("summary", "highlights"):
            val = item.get(key)
            for chunk in (val if isinstance(val, list) else [val]):
                if isinstance(chunk, dict):
                    prose += [str(v) for v in chunk.values()]
                elif chunk:
                    prose.append(str(chunk))
    for card_ in ((data.get("strengths") or {}).get("cards") or []):
        body = card_.get("body")
        prose += [str(v) for v in body.values()] if isinstance(body, dict) else [str(body or "")]
    prose_text = " ".join(prose).lower()

    # 🔴 «Дополнительная информация» — отдельный текст, а не часть опыта. Там
    # живут пет-проекты и самостоятельное изучение: WebRTC с кодеками, Python,
    # Rust. Пока этот раздел не читался вовсе, все трое выходили в отчёт как
    # «заявлено, ничем не подтверждено» — то есть как пустые строчки ATS, — и
    # список правок звал подтверждать делом то, что уже описано прозой.
    own_prose: list[str] = []
    for chunk in ((data.get("preferences") or {}).get("items") or []):
        own_prose += ([str(v) for v in chunk.values()] if isinstance(chunk, dict)
                      else [str(chunk or "")])
    own_text = " ".join(own_prose).lower()

    stacks: set[str] = set()
    for item in exp:
        for raw in (item.get("stack") or []):
            stacks.add(str(raw).lower())

    spoken = _spoken_terms(data)

    groups, _ = card.skill_variants(path)
    out: dict[str, str] = {}
    for variants in groups:
        if any(v in spoken for v in variants):
            for term in variants:
                out[term] = "язык"
            continue
        # Уровень считается на ГРУППУ: «Go (Golang)» подтверждён работой, даже
        # если в стеках стоит только «Go». Иначе «golang» попадал бы в
        # неподтверждённые заявки — ложный пробел из-за формы записи.
        best = "список"
        for term in variants:
            if any(_says(s, term) for s in stacks):
                best = "работа"
                break
            if _says(prose_text, term):
                best = "дело"
            elif _says(own_text, term) and LEVELS[best] < LEVELS["сам"]:
                best = "сам"
        for term in variants:
            out[term] = best
    return out, None


def _spoken_terms(data: dict) -> set[str]:
    """Слова из группы разговорных языков — по заголовку группы, на обоих языках.

    Заголовок локализован ({en: …, ru: …}), поэтому смотрим все его значения:
    какой из них попадётся, зависит от того, чьё резюме читают.
    """
    out: set[str] = set()
    for group in ((data.get("skills") or {}).get("groups") or []):
        title = group.get("title")
        names = ([str(v) for v in title.values()] if isinstance(title, dict)
                 else [str(title or "")])
        if not any(n.strip().lower() in _SPOKEN_TITLES for n in names):
            continue
        for item in (group.get("items") or []):
            raw = (str(item.get("en") or next(iter(item.values()), ""))
                   if isinstance(item, dict) else str(item))
            out |= {p.strip().lower() for p in re.split(r"[(),/]", raw)
                    if len(p.strip()) > 1}
    return out


# Английские окончания, которые в резюме и в вакансии разные у одного слова:
# в списке навыков «Mentoring», в пункте опыта «Mentored ~10 interns».
_TAIL = re.compile(r"(?:ing|ed|es|s|ment)$")


def _says(text: str, term: str) -> bool:
    """`_has`, но терпимый к форме слова ВНУТРИ резюме.

    🔴 Только для сверки резюме с самим собой, не для вакансий: здесь цена
    ошибки — уровень на ступеньку выше, а в отборе вакансий была бы лишняя
    карточка.

    🔴 Свободный хвост даётся ТОЛЬКО слову от шести букв. Короткое слово
    сверяется как есть, по границам: иначе «go» ловит «going», «rust» —
    «rusty», «ai» — «api», и текст опыта подтверждает пол-списка навыков.
    """
    if _has(text, term):
        return True
    if not term.isascii():
        return False
    parts = []
    for word in term.split():
        if len(word) < 6:
            parts.append(re.escape(word) + r"(?![\w])")
            continue
        stem = _TAIL.sub("", word)
        if len(stem) < 4:
            return False
        parts.append(re.escape(stem) + r"[a-z-]*")
    return bool(re.search(r"(?<![\w])" + r"[\s-]+".join(parts), text, re.I))


# ── Спрос ────────────────────────────────────────────────────────────────────
def _rows(conn, days: int | None, *, profile_only: bool = True) -> list[dict]:
    sql = ("SELECT title, company, description, location, first_seen, published_at "
           "FROM vacancy")
    args: list = []
    if days is not None:
        sql += (" WHERE COALESCE(published_at, first_seen) >= "
                "date('now', ?)")
        args.append(f"-{int(days)} day")
    rows = [dict(r) for r in conn.execute(sql, args)]
    if profile_only:
        rows = [r for r in rows if on_profile(r.get("title") or "")]
    return rows


def demand(rows: list[dict]) -> tuple[dict[str, int], dict[str, int], int]:
    """(вакансий на термин, компаний на термин, всего вакансий).

    Компания считается один раз, даже если разместила вакансию на пяти
    площадках: иначе один активный наниматель выглядит как рыночный тренд.

    Отбор — в самом конце, по всему корпусу сразу: признаки из шапки файла
    меряются долей, а доля по одной вакансии не считается.
    """
    per_vac: dict[str, int] = defaultdict(int)
    per_co: dict[str, set[str]] = defaultdict(set)
    word_hits: dict[str, int] = defaultdict(int)      # всего вхождений
    shape_hits: dict[str, int] = defaultdict(int)     # из них «технологичных» по форме
    cap_hits: dict[str, int] = defaultdict(int)       # с заглавной посреди фразы
    low_hits: dict[str, int] = defaultdict(int)       # со строчной там же
    list_hits: dict[str, int] = defaultdict(int)      # в перечислении стека
    seed = _seed_terms()      # опоры для «это перечисление»
    own = _own_terms()        # всё, что заявлено в резюме, — считаем всегда

    for row in rows:
        text = "\n".join(str(row.get(k) or "") for k in ("title", "description"))
        # Место работы этой вакансии — не технология. Порту и Лиссабон стоят
        # в шапке объявления через запятую, ровно как стек, и без этой отсечки
        # portugal выходил в спрос с 255 компаниями. Глобальным списком мест
        # это делать нельзя: среди них есть Spring (Техас) и Snowflake (Аризона).
        place = {w.lower() for w in _TOKEN.findall(str(row.get("location") or ""))}
        toks: set[str] = set()
        for line in card.requirements(text, limit=60) or _lines(text):
            line = _APOS.sub("", line)
            spots = [m for m in _TOKEN.finditer(line)]
            if not spots:
                continue
            heading = _heading(line, spots)
            here: set[str] = set()
            for i, spot in enumerate(spots):
                word = spot.group()
                low = word.lower()
                mid = not heading and bool(_MID.search(line[:spot.start()].rstrip()))
                # Термины резюме проходят мимо стоп-листов: «go» и «backend»
                # стоят в них как обычные английские слова, и без этой оговорки
                # спрос на них не измерялся вовсе — Go, главный язык владельца,
                # уходил в «балласт» с нулём вакансий.
                if low not in place and (_is_term(low) or low in own):
                    here.add(low)
                    word_hits[low] += 1
                    if _shape_tech(word):
                        shape_hits[low] += 1
                    if mid:
                        (cap_hits if word[0].isupper() else low_hits)[low] += 1
                    if _in_list(line, spots, i, i, seed):
                        list_hits[low] += 1

                # Биграммы: «service mesh», «clean architecture», «apache kafka».
                # Только через пробел: «Kafka, RabbitMQ» — перечисление двух
                # технологий, а не составное название.
                #
                # 🔴 Считается НЕЗАВИСИМО от того, прошло ли первое слово само:
                # в «service mesh» и «infrastructure as code» первое слово стоит
                # в стоп-листе шума, и пока пара считалась внутри его ветки,
                # оба словосочетания молча выпадали в «балласт».
                if i + 1 >= len(spots):
                    continue
                nxt = spots[i + 1].group()
                if line[spot.end():spots[i + 1].start()].strip():
                    continue
                pair = f"{low} {nxt.lower()}"
                if low in place or nxt.lower() in place:
                    continue
                # Пара проходит, если она сама названа в резюме: пословная
                # проверка теряла ровно такие словосочетания.
                if pair not in own and not (
                        (_is_term(low) or low in own)
                        and (_is_term(nxt.lower()) or nxt.lower() in own)):
                    continue
                here.add(pair)
                word_hits[pair] += 1
                if _shape_tech(word) and _shape_tech(nxt):
                    shape_hits[pair] += 1
                if mid:
                    (cap_hits if word[0].isupper() and nxt[0].isupper()
                     else low_hits)[pair] += 1
                if _in_list(line, spots, i, i + 1, seed):
                    list_hits[pair] += 1
            toks |= here
        company = norm(row.get("company")) or f"?{id(row)}"
        for t in toks:
            per_vac[t] += 1
            per_co[t].add(company)

    def keep(t: str) -> bool:
        if t in own:
            return True
        if list_hits[t] < _LIST_MIN_HITS:
            return False
        if shape_hits[t] >= word_hits[t] * _SHAPE_RATIO:
            return True
        mid = cap_hits[t] + low_hits[t]
        return mid >= _CAP_MIN_HITS and cap_hits[t] >= mid * _CAP_RATIO

    kept = {t for t in per_vac if keep(t)}
    return ({t: n for t, n in per_vac.items() if t in kept},
            {t: len(c) for t, c in per_co.items() if t in kept},
            len(rows))


def _heading(line: str, spots: list[re.Match]) -> bool:
    """Строка набрана в Title Case, то есть это заголовок, а не фраза.

    🔴 Перечисление заголовком не бывает, даже если заглавных в нём больше
    порога. «Stack: Kubernetes, Terraform, Kafka, PostgreSQL» — четыре имени
    собственных из пяти слов, и без этой оговорки строка целиком объявлялась
    заголовком, а вместе с ней терялся весь стек, который в ней перечислен.
    """
    if _LIST_PUNCT.search(line):
        return False
    big = [m.group() for m in spots if len(m.group()) > 2]
    return bool(big) and sum(w[0].isupper() for w in big) / len(big) >= _HEADING_RATIO


def _in_list(line: str, spots: list[re.Match], first: int, last: int,
             seed: set[str]) -> bool:
    """Термин стоит в перечислении рядом с заведомой технологией.

    Заведомая — из резюме или опознанная по форме: «Java, Spring Boot» ставит
    в список и «spring boot», хотя сам он ни там, ни там не опознан.
    """
    for i, gap in ((first - 1, (first - 1, first)), (last + 1, (last, last + 1))):
        if not 0 <= i < len(spots):
            continue
        left, right = spots[gap[0]], spots[gap[1]]
        if not _SEP.match(line[left.end():right.start()]):
            continue
        nb = spots[i].group()
        if nb.lower() in seed or _shape_tech(nb):
            return True
    return False


# 🔴 Запасной путь обязан резать текст на СТРОКИ, а не отдавать страницу одним
# куском. Пока он отдавал блоб, «Job Description» и «Kubernetes» оказывались
# в одной «строке», признак соседства срабатывал на шапке ATS, и она проходила
# в спрос на первых местах с 4000 вакансий.
def _lines(text: str, limit: int = 200) -> list[str]:
    out = []
    for chunk in text.splitlines():
        for part in re.split(r"(?<=[.;:•])\s+", chunk):
            part = part.strip()
            if 8 <= len(part) <= 300:
                out.append(part)
    return out[:limit]


def _seed_terms() -> set[str]:
    """ЯКОРЬ для распознавания перечислений — из резюме. Односложные общие
    слова («go», «build», «backend») выброшены: они стоят в любой строке
    и опорой для «это перечисление стека» быть не могут."""
    terms, _ = card.load_skills()
    return {t for t in terms
            if t not in _STOP and t not in _STOP_JOB and len(t) > 2}


def _own_terms() -> set[str]:
    """ВСЕ термины резюме, без отсечек.

    🔴 Множество отдельное от якоря, и это не дублирование. Якорю нужны только
    надёжные опоры, а измерять спрос надо по всему, что заявлено в резюме, —
    иначе главный язык владельца молча выпадает из подсчёта: «go» короче трёх
    букв, «backend» стоит в стоп-листе шума, и оба уходили в «балласт» с нулём
    вакансий на корпусе, где Go требуют сотни компаний.
    """
    terms, _ = card.load_skills()
    return set(terms)


# ── Воронка откликов ─────────────────────────────────────────────────────────
# Всё, что известно о конверсии, лежит в negotiation (ответы площадок и почты)
# и decision (наши собственные отметки). Без этой таблицы любая правка резюме —
# вкусовщина: не с чем сравнить «до» и «после».
_FUNNEL_ORDER = ("applied", "not_viewed", "viewed", "invitation", "interview",
                 "rejection", "pending", "other")
_FUNNEL_RU = {
    "applied": "отклик отправлен", "not_viewed": "не просмотрен",
    "viewed": "просмотрен", "invitation": "приглашение",
    "interview": "интервью", "rejection": "отказ",
    "pending": "в ожидании", "other": "прочее",
}


def funnel(conn) -> tuple[dict[str, int], dict[str, int]]:
    neg: dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT status, COUNT(*) n FROM negotiation GROUP BY status"):
        neg[r["status"]] += r["n"]
    dec: dict[str, int] = defaultdict(int)
    for r in conn.execute("SELECT state, COUNT(*) n FROM decision GROUP BY state"):
        dec[r["state"]] += r["n"]
    return dict(neg), dict(dec)


def titles(rows: list[dict], top: int = 12) -> list[tuple[str, int]]:
    """Самые частые формулировки роли — с чем должен совпадать заголовок резюме."""
    cnt: dict[str, int] = defaultdict(int)
    for row in rows:
        t = re.sub(r"\s+", " ", (row.get("title") or "")).strip().lower()
        t = re.sub(r"\s*[(\[].*?[)\]]", "", t)
        t = re.sub(r"[,/|].*$", "", t).strip()
        if t:
            cnt[t] += 1
    return sorted(cnt.items(), key=lambda kv: -kv[1])[:top]


# ── Отчёт ────────────────────────────────────────────────────────────────────
def build(conn, *, days: int | None = None, top: int = 25,
          min_companies: int = 3) -> str:
    rows = _rows(conn, days)
    per_vac, per_co, total = demand(rows)
    ev, why = resume_evidence()
    out: list[str] = []
    window = f"за {days} дн." if days else "по всей базе"
    out.append(f"# Профиль против рынка ({window}, профильных вакансий: {total})")
    if why:
        out.append(f"\n⚠️ {why}")
    if not total:
        out.append("\nВ окне нет профильных вакансий — расширь `--days` или сними фильтр.")
        return "\n".join(out)

    neg, dec = funnel(conn)
    out.append("\n## Воронка откликов\n")
    out.append("| статус | сколько |")
    out.append("|---|---|")
    for k in _FUNNEL_ORDER:
        if neg.get(k):
            out.append(f"| {_FUNNEL_RU[k]} | {neg[k]} |")
    seen = sum(neg.get(k, 0) for k in ("viewed", "invitation", "interview", "rejection"))
    answered = sum(neg.get(k, 0) for k in ("invitation", "interview", "rejection"))
    good = sum(neg.get(k, 0) for k in ("invitation", "interview"))
    tot = sum(neg.values())
    if tot:
        out.append(f"\nВсего записей: {tot}. Дошло до просмотра: {seen} "
                   f"({seen * 100 // tot}%). Есть ответ: {answered}. "
                   f"Из ответов приглашений/интервью: {good}"
                   + (f" ({good * 100 // answered}%)" if answered else ""))
    if dec:
        out.append("Наши отметки: "
                   + ", ".join(f"{k} {v}" for k, v in sorted(dec.items())))

    # Спрос против резюме
    ranked = sorted(per_co.items(), key=lambda kv: (-kv[1], -per_vac.get(kv[0], 0)))
    ranked = [(t, c) for t, c in ranked if c >= min_companies]

    out.append(f"\n## Чего требует рынок — топ-{top}\n")
    out.append("| термин | вакансий | компаний | в резюме |")
    out.append("|---|---:|---:|---|")
    for term, co in ranked[:top]:
        lvl = _resume_level(term, ev)
        out.append(f"| {term} | {per_vac[term]} | {co} | {lvl} |")

    gaps = [(t, c) for t, c in ranked if _resume_level(t, ev) == "—"]
    out.append(f"\n## Пробелы: рынок просит, резюме молчит — топ-{top}\n")
    if not gaps:
        out.append("_Пусто: всё, что просят от трёх компаний и чаще, в резюме есть._")
    else:
        out.append("Это НЕ список «чего он не умеет» — это список вопросов к нему. "
                   "Термин мог просто не попасть в выжимку.\n")
        out.append("| термин | вакансий | компаний |")
        out.append("|---|---:|---:|")
        for term, co in gaps[:top]:
            out.append(f"| {term} | {per_vac[term]} | {co} |")

    weak = [(t, per_co.get(t, 0)) for t, lvl in ev.items()
            if lvl == "список" and per_co.get(t, 0) >= min_companies]
    weak.sort(key=lambda kv: -kv[1])
    out.append("\n## Заявлено, но ничем не подтверждено\n")
    if not weak:
        out.append("_Пусто: всё востребованное подтверждено опытом._")
    else:
        out.append("Стоит в списке навыков, не встречается ни в одном стеке и ни в одном "
                   "пункте опыта. Спрос есть — значит, спросят на интервью именно про это.\n")
        out.append("| термин | компаний просят |")
        out.append("|---|---:|")
        for term, co in weak[:top]:
            out.append(f"| {term} | {co} |")

    # Разговорный язык балластом не бывает: «russian» рынок в списке требований
    # не пишет, но убирать его из резюме от этого не следует.
    dead = sorted((t for t, lvl in ev.items()
                   if lvl not in ("—", "язык") and per_co.get(t, 0) == 0))
    if dead:
        out.append("\n## Балласт: есть в резюме, рынок не просит ни разу\n")
        out.append(", ".join(dead))

    out.append("\n## Как рынок называет роль\n")
    out.append("| заголовок | сколько |")
    out.append("|---|---:|")
    for t, n in titles(rows):
        out.append(f"| {t} | {n} |")

    return "\n".join(out)


def _resume_level(term: str, ev: dict[str, str]) -> str:
    """Уровень подтверждения термина спроса. Термин рынка и термин резюме
    редко совпадают буквой в букву («k8s» против «kubernetes»), поэтому
    сверяем по вхождению слова в обе стороны, а заодно снимаем множественное
    число: рынок пишет «APIs», резюме — «REST API», и без этого «apis»
    попадал в пробелы при подтверждённом работой «api»."""
    variants = {term, term[:-1]} if term.endswith("s") else {term}
    best = "—"
    for own, lvl in ev.items():
        own_variants = {own, own[:-1]} if own.endswith("s") else {own}
        for t in variants:
            if any(o == t or _has(t, o) or _has(o, t) for o in own_variants):
                if LEVELS[lvl] > LEVELS[best]:
                    best = lvl
                break
    return best
