"""Раскладка карточек по каталогам и их проверка — работа алгоритма, не модели.

Две команды:

* `card --write` — кладёт скелет карточки туда, где ему место по SKILL.md:
  `.jobs/<дата>/companies/<слаг>/<дата>-<слаг роли>.md`. Слаг сворачивает
  организационно-правовую форму, поэтому «АО «Каргономика»» и «Каргономика»
  дают ОДИН каталог. Работодатель за заглушкой агрегатора уходит в `_hidden/`.
* `lint-cards` — проверяет готовые карточки по формальным признакам: есть ли
  раздел «Отклик», не остались ли незаполненные заглушки, нет ли предупреждений.

Почему это здесь, а не в голове модели. Раскладку она делала руками — двадцать
восемь путей на волну 04.08.2026 и класс ошибок «две папки на одну компанию».
Проверку карточек — перечитыванием всех файлов перед сдачей. И то и другое
механика: путь считается, признаки конечны, ошибка объективна.

Чего эти команды НЕ делают: не пишут фит, не пишут письмо, не решают, достойна
ли вакансия карточки. Это суждение, оно остаётся модели.
"""

from __future__ import annotations

import os
import re

from . import store
from .wavedoc import slug

# Признаки незаполненного места. Модель оставляет их намеренно (скелет так и
# печатается), но карточка с ними ГОТОВОЙ не является.
_HOLES = (
    "<!-- ЗАПОЛНЯЕТ МОДЕЛЬ",
    "<заполни",
    "TODO",
    "…дописать",
    "нет в базе —",
)

# Разделы, без которых карточка бесполезна: по ней нельзя откликнуться.
_NEEDED = ("## Отклик",)


def card_path(root: str, date: str, company: str | None, title: str,
              url: str | None = None) -> str:
    """Путь карточки. Пустая компания → `_hidden/`, а не каталог с пустым именем.

    🔴 `url` — различитель на случай КОЛЛИЗИИ. У безымянных вакансий (работодатель
    скрыт, заголовок общий) путь совпадает целиком: двенадцать разных постов
    давали один `_hidden/2026-08-08-golang-razrabotchik.md`, и одиннадцать из них
    молча не записывались — «уже есть, не перезаписан» (09.08.2026). Терялись
    именно те вакансии, у которых работодатель скрыт, то есть требующие большего
    внимания, а не меньшего.

    Различитель добавляется ТОЛЬКО когда файл занят чужой вакансией: у карточек,
    чьё имя уникально, путь не меняется, и ссылки в готовых документах живы.
    """
    company_slug = slug(company)
    role = slug(title)[:60] or "vakansiya"
    base = os.path.join(root, date, "companies", company_slug)
    path = os.path.join(base, f"{date}-{role}.md")
    if not url or not os.path.exists(path):
        return path
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2000)
    except OSError:
        return path
    if f"**Ссылка:** {url}" in head:
        return path                       # тот же файл той же вакансии
    # Занято другой вакансией — добавляем хвост из её адреса: он устойчив между
    # прогонами, в отличие от порядкового номера.
    tail = re.sub(r"\W+", "", (url or "").rsplit("/", 2)[-1])[-8:] or "x"
    return os.path.join(base, f"{date}-{role}-{tail}.md")


def _dead_links(text: str) -> list[str]:
    """Мёртвые ATS-ссылки в тексте карточки. Проверяются только те, что умеем.

    Живость обычной страницы вакансии стоит запроса на каждую и в предфлайт не
    лезет: `check-links` для того и остался отдельной командой. Здесь — дешёвая
    проверка ровно тех досок, у которых есть API и известна ротация id.
    """
    from .atsapi import board, parse_job_url  # noqa: PLC0415

    bad: list[str] = []
    seen: set[str] = set()
    for url in re.findall(r"https?://\S+", text):
        url = url.rstrip(").,;»\"'")
        if url in seen:
            continue
        seen.add(url)
        p = parse_job_url(url)
        if not p:
            continue
        try:
            b = board(p[0], p[1])
        except Exception:  # noqa: BLE001 — доска не ответила, это не «мертва»
            continue
        if not any(str(j.id) == str(p[2]) for j in (b.jobs or [])):
            bad.append(f"{url} — на доске такой вакансии нет (id ротирован?)")
    return bad


# Раздел, с которого в карточке начинается работа МОДЕЛИ. Всё выше — факты из
# базы, всё ниже — суждение и письмо. Граница нужна `--refresh`: пересобрать
# факты, не тронув написанное.
_MODEL_PART = "### Фит"


def refresh_text(old: str, fresh: str) -> str | None:
    """Свежие факты + сохранённое суждение. None — склеить нечем.

    Карточка делится ровно надвое: до «### Фит» — сгенерированное (деньги,
    маршруты, флаги, требования), дальше — фит и письмо, которых в базе нет и
    восстановить их нечем. `--force` затирал всё целиком, поэтому обновление
    фактов в готовой волне делалось руками — а факты меняются: 13 карточек
    волны 08.08.2026 показывали витрину как «прямой канал в компанию» просто
    потому, что реестр витрин был неполон (09.08.2026).
    """
    i = old.find(_MODEL_PART)
    if i < 0:
        return None                     # модель ещё не писала — обычная перезапись
    j = fresh.find(_MODEL_PART)
    head = fresh[:j] if j >= 0 else fresh.rstrip() + "\n\n"
    return head + old[i:]


def card_is_empty(text: str) -> tuple[bool, str]:
    """Есть ли в карточке хоть что-то, по чему можно судить о вакансии.

    Признак ровно один и объективный: нет НИ требований, НИ описания. Всё
    остальное (деньги, формат, маршруты) в карточке есть всегда — оно берётся
    из записи, а не из текста вакансии, и пустой карточку не спасает.

    🔴 Судит алгоритм, а не глаз. 09.08.2026 я отсеивал такие карточки руками
    («все требования — про бота»), и вместе с полутора десятками настоящих
    пустышек снёс полсотни годных: у постов Runello требования разбираются
    отлично («Go, REST API, gRPC, PostgreSQL, Redis, Docker, Git»), просто их
    единственная служебная строка попадала под мой фильтр.
    """
    body = re.search(r"### Требование → что у тебя\n(.*?)(?:\n### |\Z)", text, re.S)
    table = (body.group(1) if body else "").strip()
    has_reqs = bool(re.search(r"^\| (?!требование)\S", table, re.M))
    if has_reqs:
        return False, ""
    # Требований нет — спасти карточку может только внятное описание в теле.
    described = re.search(r"### (?:Описание|Текст вакансии)[^\n]*\n(.{200,})", text, re.S)
    if described:
        return False, ""
    return True, ("текста вакансии нет — ни требований, ни описания. Площадка "
                  "отдала одну ссылку; смотреть надо глазами")


def duplicate_cards(root: str, date: str) -> list[list[str]]:
    """Группы карточек, описывающих ОДНУ вакансию. Считает алгоритм, не глаз.

    Подпись вакансии — заголовок плюс набор требований. Телеграм-каналы
    перепечатывают один и тот же пост: «Golang-разработчик» с одинаковым стеком
    приезжает из `runello_rus_goland`, `runello_rus_backend` и
    `runello_rus_webdevelopment`, и дедуп базы их намеренно не склеивает
    (инвариант: ошибаться в сторону РАЗДЕЛЕНИЯ, лучше показать дубль, чем
    потерять вакансию).

    Но человеку, который открывает волну, знать об этом надо: 46 карточек на
    38 вакансий читаются как 46 разных предложений (09.08.2026). Поэтому
    карточки не удаляем, а НАЗЫВАЕМ двойников.
    """
    base = os.path.join(root, date, "companies")
    if not os.path.isdir(base):
        return []
    groups: dict[tuple, list[str]] = {}
    for dirpath, _dirs, files in os.walk(base):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            head = text.splitlines()[0].lstrip("# ").split(" — ")[0].strip().lower()
            reqs = tuple(m.group(1).strip() for m in
                         re.finditer(r"^\| (.{4,}?) *\| (?:🔴 обяз\.|плюсом|—) *\|",
                                     text, re.M))
            if not reqs:
                continue          # без требований подпись не отличает вакансии
            groups.setdefault((head, reqs), []).append(path)
    return [sorted(v) for v in groups.values() if len(v) > 1]


def card_with_url(root: str, date: str, url: str) -> str | None:
    """Уже написанная карточка ЭТОЙ вакансии — по ссылке внутри файла.

    🔴 Имя файла устойчивым идентификатором не является: карточку переименовывают
    руками, когда автослаг выходит нечитаемым. Ссылка на вакансию — является.
    Без этого `--refresh` не находил переименованную карточку и писал рядом
    голый скелет: в волне 08.08.2026 так появился второй файл на одну вакансию,
    причём с фитом и письмом остался старый (09.08.2026).
    """
    base = os.path.join(root, date, "companies")
    if not os.path.isdir(base):
        return None
    needle = f"**Ссылка:** {url}"
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as f:
                    head = f.read(2000)
            except OSError:
                continue
            if needle in head:
                return path
    return None


def find_vacancy(conn, url: str):
    """Запись вакансии по ссылке. Сначала точное совпадение, потом — по ПУТИ.

    🔴 Витрины меняют параметры ссылки между прогонами. У jooble один и тот же
    `/away/-2503628600313974170` приезжает то с `pos=446`, то с `pos=456`, плюс
    `scr`, `bscr`, `aq`, `relb` — всё это счётчики выдачи, а не адрес вакансии.
    Ссылка в готовой карточке становилась «не в базе», и `--refresh` молча
    пропускал её: 4 карточки волны 08.08.2026 (Censys, Cirrus Data, Mirantis,
    goteleport) остались с устаревшими фактами (09.08.2026).

    По пути ищем ТОЛЬКО когда он даёт ровно одну запись: у площадок, где id
    сидит в query, путь общий для всех вакансий, и «одна» там не выйдет.
    """
    row = conn.execute(
        "SELECT title, company FROM vacancy WHERE url = ?", (url,)).fetchone()
    if row is not None:
        return row
    base = (url or "").split("?", 1)[0].split("#", 1)[0]
    if not base or base == url:
        return None
    rows = conn.execute(
        "SELECT title, company FROM vacancy WHERE url = ? OR url LIKE ? LIMIT 2",
        (base, base + "?%")).fetchall()
    return rows[0] if len(rows) == 1 else None


def write(db: str, urls: list[str], *, date: str, root: str = ".jobs",
          force: bool = False, skills=None, skills_note=None,
          walk: bool = True, refresh: bool = False) -> list[tuple[str, str]]:
    """[(путь, что сделано)]. Существующие файлы не трогает без `force`.

    `walk=False` — собирать без обхода ссылок вакансии (без сети).
    `refresh=True` — обновить ФАКТЫ существующей карточки, сохранив фит и письмо.
    """
    from . import card  # noqa: PLC0415 — тяжёлый импорт (резюме, разбор вилок)

    if skills is None:
        skills, skills_note = card.load_skills()
    out: list[tuple[str, str]] = []
    with store.connect(db) as conn:
        for url in urls:
            row = find_vacancy(conn, url)
            if row is None:
                out.append((url, "нет в базе — ссылку возьми из `scout shortlist`"))
                continue
            path = card_path(root, date, row["company"], row["title"] or "", url)
            if refresh and not os.path.exists(path):
                # Карточку могли переименовать руками — ищем по ссылке внутри.
                path = card_with_url(root, date, url) or path
            exists = os.path.exists(path)
            if exists and not (force or refresh):
                # Тот же довод, что у `wavedoc`: в файле уже может лежать фит и
                # письмо, которых в базе нет и восстановить их нечем.
                out.append((path, "уже есть — не перезаписан (`--force`, если надо)"))
                continue
            text = card.build(conn, url, skills=skills, skills_note=skills_note,
                              walk=walk)
            empty, why_empty = card_is_empty(text)
            if empty and not exists:
                # 🔴 Карточка без единого факта о вакансии бесполезна: судить по
                # ней нечего, а в волне она выглядит разобранной. Решает это
                # АЛГОРИТМ, а не глаз агента: 09.08.2026 я отсеивал такие руками
                # ad-hoc-фильтром и снёс вместе с ними полсотни годных скелетов,
                # у которых требования разбирались прекрасно.
                out.append((path, f"НЕ записана: {why_empty}"))
                continue
            if exists and refresh and not force:
                with open(path, encoding="utf-8") as f:
                    merged = refresh_text(f.read(), text)
                if merged is None:
                    out.append((path, "фита и письма ещё нет — пересобран целиком"))
                else:
                    text, _note = merged, None
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text if text.endswith("\n") else text + "\n")
                    out.append((path, "факты обновлены, фит и письмо сохранены"))
                    continue
            dead = _dead_links(text)
            if dead:
                # Ссылка проверяется ДО записи, а не после. Ashby ротирует UUID
                # вакансии при переопубликации, и ссылка вчерашнего скана бывает
                # мёртвой при живой вакансии — это уже случалось. Карточку всё
                # равно пишем (в ней есть всё остальное), но помечаем сверху:
                # молча положить мёртвую ссылку значит отправить человека
                # откликаться в никуда.
                text = ("⚠️ ПРОВЕРЬ ССЫЛКИ: " + "; ".join(dead)
                        + "\n\n" + text)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text if text.endswith("\n") else text + "\n")
            out.append((path, f"записан скелет ({len(text.splitlines())} строк)"))
    return out


# Заголовок раздела с письмом в скелете карточки (`card.build`). Константой,
# потому что по нему режется текст: разойдись эта строка с card.py — линт
# перестанет находить письмо и будет молча отчитываться «замечаний нет».
LETTER_HEADING = "### Письмо"

# Строки скелета внутри раздела «Письмо»: пояснение курсивом, команда гейта,
# перечисление белого списка. Письмом они не являются и в линт письма не идут.
# Признак — НАЧАЛО строки: все пояснения в `card.build` открываются `_`, а вот
# кончаются они по-разному («…_ `команда`»), и по концу строки они не ловятся.
_SKELETON_LINE = re.compile(r"^\s*(?:_|`|>)")

# Предупреждения, которые печатает сам `card`/`brief`: они часть контракта
# вывода, а не недоделка модели. Ловить их линтом значит требовать стереть
# дисклеймер про оценку вилки — то есть соврать про происхождение цифры.
_GENERATED_WARNING = re.compile(
    r"Это ОЦЕНКА|требования выделены из описания эвристикой|"
    r"описание собрано текстовым разбором|ПОХОЖЕ НА КАРКАС SPA|"
    r"generic: парсера под источник нет")


# Разделы, которые печатает САМ генератор от первой до последней строки.
# Всё внутри них — вывод scout, а не работа модели, и требовать «сними вопрос»
# там бессмысленно: вопрос задал не человек.
_GENERATED_SECTIONS = ("### Сколько просить", "### Связь")


def _outside_generated_sections(text: str) -> list[str]:
    """Строки карточки ВНЕ разделов, целиком принадлежащих генератору."""
    out, skipping = [], False
    for ln in text.splitlines():
        if ln.startswith("#"):
            skipping = any(ln.startswith(h) for h in _GENERATED_SECTIONS)
        if not skipping:
            out.append(ln)
    return out


def letter_of(text: str) -> str:
    """Тело письма из карточки. Пусто — модель его ещё не написала.

    Раздел режется до СЛЕДУЮЩЕГО заголовка любого уровня: письмо в карточке
    последнее, но полагаться на «до конца файла» нельзя — под него в живых
    карточках дописывают и заметки, и вторую редакцию.
    """
    # 🔴 Сначала ```-блок: в живых карточках письмо лежит копируемым блоком
    # внутри раздела «Отклик», а не под заголовком «### Письмо». Пока искали
    # только заголовок, letter_of возвращал ПУСТО на всех 49 карточках волны —
    # то есть ни линт письма, ни гейт по ним не отрабатывали, а команда при
    # этом рапортовала «замечаний нет». Заголовок остаётся запасным путём: по
    # нему письмо лежит в скелете, пока модель не перенесла его в «Отклик».
    fenced = re.search(r"^```[a-z]*\s*\n(.*?)^```\s*$", text, re.S | re.M)
    if fenced and fenced.group(1).strip():
        return fenced.group(1).strip()
    i = text.find(LETTER_HEADING)
    if i < 0:
        return ""
    body = text[i + len(LETTER_HEADING):]
    nxt = re.search(r"^#{1,6}\s", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    keep = [ln for ln in body.splitlines()[1:] if not _SKELETON_LINE.match(ln)]
    return "\n".join(keep).strip()


def check_card(text: str) -> list[str]:
    """Замечания к одной карточке. Пусто — формально готова."""
    bad: list[str] = []
    for needed in _NEEDED:
        if needed not in text:
            bad.append(f"нет раздела «{needed.strip('# ')}» — откликнуться по ней нельзя")
    for hole in _HOLES:
        if hole in text:
            bad.append(f"осталась заглушка «{hole}»")
    # ⚠️ означает «незаданный вопрос про опыт кандидата» (SKILL.md), и такому
    # знаку в готовой карточке не место. Но часть предупреждений ставит САМ
    # генератор: дисклеймер «это оценка, а не вилка» обязателен в блоке
    # «Сколько просить», а пометки о качестве выжимки честно говорят, что текст
    # разобран эвристикой. Пока правило ловило любой знак, `lint-cards` ругался
    # на собственный вывод `card --write` — три карточки волны 08.08.2026 были
    # объявлены недоделанными из-за строки, которую scout обязан печатать.
    #
    # 🔴 Список формулировок оказался хрупким сам по себе: генератор добавил
    # «Выборка маленькая (8 вакансий)» и «Регион вакансии не назван», и семь
    # карточек снова стали «недоделанными» (09.08.2026). Поэтому разделы,
    # которые целиком печатает scout, исключаются ЦЕЛИКОМ — это механизм, а не
    # очередная строка в списке, за которым надо гоняться.
    own = [ln for ln in _outside_generated_sections(text)
           if "⚠️" in ln and not _GENERATED_WARNING.search(ln)]
    if own:
        bad.append(f"осталось предупреждение ⚠️ ({len(own)} шт.) — сними вопрос "
                   f"или назови гэп прямо; для барьеров вакансии есть 🔴")
    # Ссылка на вакансию обязана быть: без неё карточка не привязана ни к чему.
    if not re.search(r"https?://", text):
        bad.append("нет ни одной ссылки")

    # Письмо лежит ВНУТРИ карточки, и проверка у него ровно одна. Держать её
    # отдельной командой значило заставлять модель вырезать текст в файл и
    # звать `lint-letter` руками на каждую карточку — то есть не звать.
    letter = letter_of(text)
    if letter:
        from . import lintletter  # noqa: PLC0415 — не нужен, пока письма нет
        for code, what, _how in lintletter.check(letter):
            bad.append(f"письмо [{code}]: {what}")
        # Гейт на письмо. Раньше он стоял СТРОКОЙ В КАРТОЧКЕ («прогони готовое
        # письмо гейтом …»), то есть работа модели была выложена человеку,
        # который открыл документ, чтобы откликнуться. Теперь его гоняет линт:
        # письмо уходит работодателю от имени владельца, и чужая ссылка в нём
        # дороже неудачной формулировки, а проверять это должен алгоритм.
        from . import untrusted  # noqa: PLC0415
        from .card import own_links  # noqa: PLC0415
        links, mails = own_links()
        # Что гейт обязан ловить — ЧУЖОЙ адрес, попавший в письмо из текста
        # вакансии (отражённая инъекция) или из головы модели. А вот ссылка на
        # саму вакансию и на выбранный канал отклика — часть канона письма
        # («Вакансия: <url> · резюме: <сайт>», letter-guide) и стоят в этой же
        # карточке вне письма. Разрешаем всё, что человек и так видит в
        # документе: иначе гейт краснеет на каждой второй карточке, и его
        # перестают читать — а тогда он не поймает и настоящую подмену.
        outside = text.replace(letter, " ")
        allowed = links + re.findall(r"https?://[^\s)>\]\"'`,]+", outside)
        for problem in untrusted.letter_issues(letter, allowed_urls=allowed,
                                               allowed_emails=mails):
            bad.append(f"письмо [гейт]: {problem}")
    return bad


def lint(path: str) -> tuple[list[tuple[str, list[str]]], int]:
    """([(файл, замечания)], сколько файлов проверено) по каталогу волны или файлу."""
    files: list[str] = []
    if os.path.isfile(path):
        files = [path]
    else:
        # Только то, что лежит в `companies/`. Индекс волн и главный документ —
        # тоже .md, но карточками не являются и раздела «Отклик» иметь не
        # обязаны: ругаться на них значит выдавать три ложных замечания в каждом
        # прогоне, после чего линт перестают читать.
        for base, _dirs, names in os.walk(path):
            if f"{os.sep}companies{os.sep}" not in base + os.sep:
                continue
            files.extend(os.path.join(base, n) for n in sorted(names)
                         if n.endswith(".md"))
    out: list[tuple[str, list[str]]] = []
    for f in sorted(files):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            out.append((f, [f"не читается: {e}"]))
            continue
        bad = check_card(text)
        if bad:
            out.append((f, bad))
    return out, len(files)


def cli_write(args) -> int:
    date = getattr(args, "date", None) or store.now()[:10]
    rows = write(args.db, list(args.urls), date=date,
                 force=getattr(args, "force", False),
                 refresh=getattr(args, "refresh", False),
                 walk=not getattr(args, "no_crawl", False))
    for path, what in rows:
        print(f"{path}: {what}")
    return 1 if any("нет в базе" in w for _, w in rows) else 0


_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def lint_doc_links(doc_path: str) -> list[str]:
    """Ссылки главного документа волны, которые никуда не ведут.

    🔴 Проверяет главное, чего линт не проверял вовсе: можно ли из таблицы дойти
    до карточки. Документ лежит в `.jobs/<дата>.md`, то есть РЯДОМ с каталогом
    `.jobs/<дата>/`, и ссылка `companies/…` резолвится в `.jobs/companies/…`.
    Волна 09.08.2026 вышла с 49 битыми ссылками — образец с этой ошибкой стоял
    в самом SKILL.md, так что повторялось бы каждый раз.

    Внешние адреса (http, mailto) не трогаем: их живость — дело `check-links`.
    """
    try:
        with open(doc_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    base = os.path.dirname(os.path.abspath(doc_path))
    bad: list[str] = []
    for target in _MD_LINK.findall(text):
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not os.path.exists(os.path.join(base, target)):
            bad.append(target)
    return bad


def cli_lint(args) -> int:
    path = getattr(args, "path", None) or ".jobs"
    found, total = lint(path)
    # Главный документ волны лежит РЯДОМ с каталогом карточек: `.jobs/<дата>.md`
    # против `.jobs/<дата>/`. Проверяем его ссылки здесь же — из таблицы человек
    # и ходит по карточкам, и битая ссылка обесценивает весь документ.
    doc = path.rstrip("/") + ".md"
    broken = lint_doc_links(doc)
    if broken:
        print(f"🔴 {doc}: {len(broken)} ссылок никуда не ведут — из таблицы "
              f"до карточки не дойти. Путь считается ОТ каталога документа, "
              f"поэтому он начинается с даты волны:")
        for b in broken[:8]:
            print(f"    - {b}")
        if len(broken) > 8:
            print(f"    … и ещё {len(broken) - 8}")
    # Двойники называем всегда, даже когда замечаний нет: 46 карточек на 38
    # вакансий читаются как 46 разных предложений, и человек тратит время на
    # уже прочитанное. Ошибкой это не считается — телеграм-каналы законно
    # перепечатывают один пост, и удалять карточку нельзя (лучше дубль, чем
    # потерянная вакансия).
    date = os.path.basename(path.rstrip("/"))
    twins = duplicate_cards(os.path.dirname(path.rstrip("/")) or ".", date)
    if twins:
        dup = sum(len(g) - 1 for g in twins)
        print(f"ℹ️  один пост в нескольких каналах: {len(twins)} вакансий "
              f"продублированы {dup} карточками — читать достаточно первую:")
        for g in twins[:6]:
            print(f"    · {os.path.basename(g[0])}")
            for extra in g[1:]:
                print(f"        то же: {os.path.basename(extra)}")
        if len(twins) > 6:
            print(f"    … и ещё {len(twins) - 6} групп")
    if not found:
        print(f"{path}: {total} карточек, замечаний нет")
        return 1 if broken else 0
    print(f"{path}: {len(found)} из {total} карточек с замечаниями")
    for f, bad in found:
        print(f"  {f}")
        for b in bad:
            print(f"    - {b}")
    print("\nСуждение это НЕ проверяет: верность фита, качество письма и то, "
          "достойна ли вакансия карточки, остаются на тебе.")
    return 1
