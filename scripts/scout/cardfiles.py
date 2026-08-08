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


def card_path(root: str, date: str, company: str | None, title: str) -> str:
    """Путь карточки. Пустая компания → `_hidden/`, а не каталог с пустым именем."""
    company_slug = slug(company)
    role = slug(title)[:60] or "vakansiya"
    return os.path.join(root, date, "companies", company_slug, f"{date}-{role}.md")


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


def write(db: str, urls: list[str], *, date: str, root: str = ".jobs",
          force: bool = False, skills=None, skills_note=None) -> list[tuple[str, str]]:
    """[(путь, что сделано)]. Существующие файлы не трогает без `force`."""
    from . import card  # noqa: PLC0415 — тяжёлый импорт (резюме, разбор вилок)

    if skills is None:
        skills, skills_note = card.load_skills()
    out: list[tuple[str, str]] = []
    with store.connect(db) as conn:
        for url in urls:
            row = conn.execute(
                "SELECT title, company FROM vacancy WHERE url = ?", (url,)).fetchone()
            if row is None:
                out.append((url, "нет в базе — ссылку возьми из `scout shortlist`"))
                continue
            path = card_path(root, date, row["company"], row["title"] or "")
            if os.path.exists(path) and not force:
                # Тот же довод, что у `wavedoc`: в файле уже может лежать фит и
                # письмо, которых в базе нет и восстановить их нечем.
                out.append((path, "уже есть — не перезаписан (`--force`, если надо)"))
                continue
            text = card.build(conn, url, skills=skills, skills_note=skills_note)
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


def letter_of(text: str) -> str:
    """Тело письма из карточки. Пусто — модель его ещё не написала.

    Раздел режется до СЛЕДУЮЩЕГО заголовка любого уровня: письмо в карточке
    последнее, но полагаться на «до конца файла» нельзя — под него в живых
    карточках дописывают и заметки, и вторую редакцию.
    """
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
    if "⚠️" in text:
        bad.append("осталось предупреждение ⚠️ — разберись с ним или убери")
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
    return bad


def lint(path: str) -> list[tuple[str, list[str]]]:
    """[(файл, замечания)] по каталогу волны или одному файлу."""
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
                 force=getattr(args, "force", False))
    for path, what in rows:
        print(f"{path}: {what}")
    return 1 if any("нет в базе" in w for _, w in rows) else 0


def cli_lint(args) -> int:
    path = getattr(args, "path", None) or ".jobs"
    found, total = lint(path)
    if not found:
        print(f"{path}: {total} карточек, замечаний нет")
        return 0
    print(f"{path}: {len(found)} из {total} карточек с замечаниями")
    for f, bad in found:
        print(f"  {f}")
        for b in bad:
            print(f"    - {b}")
    print("\nСуждение это НЕ проверяет: верность фита, качество письма и то, "
          "достойна ли вакансия карточки, остаются на тебе.")
    return 1
