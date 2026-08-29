"""Гейт разбора: собранное резюме читается парсером ATS без потерь.

Зачем модуль. До 21.08.2026 у генератора резюме (`generate_resume_outputs.py`,
1775 строк) не было НИ ОДНОГО теста: единственной проверкой была валидация
схемы внутри сборки, то есть проверялся ИСТОЧНИК, а не то, что получилось.
Поэтому дефекты выгрузки доживали до продакшена молча — их некому было увидеть:
человек смотрит на PDF глазами, а работодатель отдаёт файл парсеру, и парсер
видит другое.

Что здесь проверяется. Файлы собираются во временный каталог из настоящего
`resume/resume.yaml`, а потом читаются ТЕМ ЖЕ способом, каким их прочитает ATS:

* PDF — через `pdftotext` (poppler). Это внешняя программа, и она НЕ
  пропускаемая: нет poppler — модуль падает с внятным сообщением. Пропуск
  превратил бы проверку в такую, которая не может провалиться.
* DOCX — распаковкой `word/document.xml` (zipfile + регулярка), без
  `python-docx`: парсеры читают именно XML, а не объектную модель.
* TXT — как есть.

Границы. `pdftotext` — приближение, а не сам ATS: он собирает строки по
координатам и вставляет разрыв там, где горизонтальный зазор велик. Именно так
ведут себя извлекатели на PDFBox и pdf.js, но НИ ОДИН файл в настоящий
коммерческий парсер не загружался, поэтому «зелёно здесь» означает «текст
извлекается и структура видна», а не «Sovren разложит по полям правильно».

    python3 -m scripts.test_resume_ats
"""

from __future__ import annotations

import atexit
import html as htmlmod
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import generate_resume_outputs as gen

FAILS: list[str] = []

LANGS = ("en", "ru")
FORMATS = ("pdf", "docx", "txt")


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        FAILS.append(f"{name}: {detail}" if detail else name)


def eq(name: str, got: Any, want: Any) -> None:
    check(name, got == want, f"получено {got!r}, ожидалось {want!r}")


# ──────────────────────────────────────────────────────────────────────────────
# Сборка и извлечение текста
# ──────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def source() -> dict[str, Any]:
    """Настоящий источник резюме — не фикстура.

    Фикстура проверяла бы генератор на выдуманных данных; дефекты же вылезают
    именно на реальных длинах строк (перенос слова, вылет за ширину полосы).
    """
    return gen.load_data(gen.ROOT / "resume/resume.yaml")


@lru_cache(maxsize=1)
def built() -> dict[str, dict[str, Path]]:
    """Собрать все шесть файлов во временный каталог. Замер: около 5 с.

    Сайт и картинки не трогаются намеренно: `generate_outputs` попутно
    пересобирает медиа в `assets/`, а тест не имеет права менять репозиторий.
    Вызываются ровно те три функции, которые вызывает сборка.
    """
    data = source()
    names = gen.download_file_names(data)
    root = Path(tempfile.mkdtemp(prefix="resume-ats-"))
    atexit.register(shutil.rmtree, root, True)
    made: dict[str, dict[str, Path]] = {}
    for lang in LANGS:
        lang_dir = root / lang
        txt = lang_dir / names["txt"]
        pdf = lang_dir / names["pdf"]
        docx = lang_dir / names["docx"]
        gen.generate_txt(data, lang, txt)
        scale = gen.generate_pdf(data, lang, pdf)
        gen.generate_docx(data, lang, docx, scale)
        made[lang] = {"pdf": pdf, "docx": docx, "txt": txt}
    return made


def pdf_text(path: Path) -> str:
    """Текст PDF глазами извлекателя: poppler, без `-layout`.

    🔴 Не пропускать при отсутствии poppler. Тест, который умеет молча не
    выполниться, — это тест, который не может провалиться.
    """
    tool = shutil.which("pdftotext")
    if tool is None:
        raise SystemExit(
            "не найден pdftotext (poppler) — читать PDF так же, как его читает ATS, "
            "нечем; поставь: brew install poppler / apt install poppler-utils"
        )
    done = subprocess.run([tool, "-enc", "UTF-8", str(path), "-"],
                          capture_output=True, text=True, check=True)
    return done.stdout


_TAG = re.compile(r"<[^>]+>")


def docx_text(path: Path) -> str:
    """Текст DOCX так, как его берёт парсер: прямо из `word/document.xml`."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"<w:(?:br|cr)\b[^>]*/?>", "\n", xml)
    xml = xml.replace("</w:p>", "\n")
    xml = re.sub(r"<w:tab\b[^>]*/?>", " ", xml)
    return htmlmod.unescape(_TAG.sub("", xml))


@lru_cache(maxsize=1)
def texts() -> dict[str, dict[str, str]]:
    """`{язык: {формат: текст}}` — шесть артефактов, извлечённых по-настоящему."""
    made = built()
    return {
        lang: {
            "pdf": pdf_text(made[lang]["pdf"]),
            "docx": docx_text(made[lang]["docx"]),
            "txt": made[lang]["txt"].read_text(encoding="utf-8"),
        }
        for lang in LANGS
    }


def lines(lang: str, fmt: str) -> list[str]:
    """Непустые строки артефакта со снятыми краевыми пробелами."""
    return [line.strip() for line in texts()[lang][fmt].splitlines() if line.strip()]


def experience_items(lang: str) -> list[dict[str, Any]]:
    return gen.experience_items(source(), lang)


def education_items(lang: str) -> list[dict[str, Any]]:
    return gen.section(source(), "education", lang)["items"]


# ──────────────────────────────────────────────────────────────────────────────
# Контроль самого инструмента
# ──────────────────────────────────────────────────────────────────────────────


def test_every_artifact_yields_readable_text() -> None:
    """Контроль на заведомо нужном: если извлекатель ослеп, красное ниже — ложь.

    Проверяется ровно то, что делает извлечение непредставительным: пустой
    выход, символы-замены (шрифт без `/ToUnicode`) и рассыпание текста на
    отдельные буквы (`letter-spacing` — отдельный пункт в списке Greenhouse
    «почему резюме не разобралось»).

    Граница, найденная нарочной поломкой 21.08.2026: разрежённые буквы этот
    тест поймать НЕ УСПЕВАЕТ — `letter-spacing: 0.9pt` раздувает выгрузку до
    трёх страниц, и сборка падает раньше, на потолке в две страницы
    (`resolve_download_pdf`). Отказ громкий, прогон всё равно красный, но
    красным становится не это утверждение.
    """
    for lang in LANGS:
        for fmt in FORMATS:
            text = texts()[lang][fmt]
            check(f"{lang}/{fmt}: текст извлёкся", len(text) > 5000,
                  f"символов {len(text)}")
            eq(f"{lang}/{fmt}: символов-замен нет", text.count("�"), 0)
            words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", text)
            long_enough = [w for w in words if len(w) > 3]
            check(f"{lang}/{fmt}: текст не рассыпан на буквы",
                  len(long_enough) > 500,
                  f"слов длиннее трёх букв: {len(long_enough)} из {len(words)}")


# ──────────────────────────────────────────────────────────────────────────────
# Заголовок записи — одна разбираемая строка
# ──────────────────────────────────────────────────────────────────────────────


def block_lines(lang: str, fmt: str, start_key: str, end_key: str) -> list[str]:
    """Строки между двумя заголовками разделов — сам раздел, без соседей.

    Сравнение без учёта регистра: в TXT заголовок печатается как есть, в PDF
    его поднимает в верхний регистр CSS, в DOCX — `.upper()`.
    """
    titles = {key: gen.download_section_title(source(), lang, key).lower()
              for key in (start_key, end_key)}
    body = lines(lang, fmt)
    lowered = [line.lower() for line in body]
    if titles[start_key] not in lowered or titles[end_key] not in lowered:
        FAILS.append(f"{lang}/{fmt}: не найден заголовок раздела "
                     f"{start_key!r} или {end_key!r} — искать в них нечего")
        return []
    return body[lowered.index(titles[start_key]) + 1:lowered.index(titles[end_key])]


def entry_header_line(lang: str, fmt: str, prefix: str) -> str | None:
    """Единственная строка артефакта, начинающаяся с заданного начала заголовка.

    Якорем берётся не одно название, а «компания | должность»: название
    компании начинает ещё и абзац-описание («ATOM develops …»), а оба
    образования у владельца получены в ОДНОМ вузе — по одному названию запись
    не опознать.
    """
    found = [line for line in lines(lang, fmt) if line.startswith(prefix)]
    return found[0] if len(found) == 1 else None


def test_generated_pdf_is_tagged() -> None:
    """PDF несёт дерево структуры (`/StructTreeRoot`), а не только координаты.

    Нетегированный PDF — набор кусков текста с координатами: порядок чтения и
    роли («это заголовок», «это пункт списка») извлекатель домысливает сам по
    зазорам. Ровно на этом домысле уже ломался заголовок места работы (см.
    `test_experience_header_is_one_parseable_line`). Тегированный несёт дерево
    явно, и парсеры на PDFBox читают именно его.

    Замер 21.08.2026: `pdfinfo` по обоим файлам — `Tagged: no`,
    `weasyprint.DEFAULT_OPTIONS['pdf_tags']` — False. С `pdf_tags=True` на
    WeasyPrint 69.0 текст извлекается тем же, число страниц и размер не меняются.

    🔴 Маркер ищется в РАЗЖАТЫХ объектных потоках, а не в сырых байтах. PDF 1.7
    держит служебные объекты в сжатых `ObjStm`, и поиск по сырому файлу дал бы
    «структуры нет» на любом файле — то есть проверку, которая не может
    провалиться. Контроль на заведомо нужном стоит рядом: `/Page` обязан
    находиться тем же способом.
    """
    import zlib

    for lang in LANGS:
        raw = built()[lang]["pdf"].read_bytes()
        blobs = [raw]
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
            try:
                blobs.append(zlib.decompress(m.group(1)))
            except zlib.error:
                continue
        joined = b"".join(blobs)
        check(f"{lang}: контроль извлекателя — объекты страниц не найдены вовсе",
              b"/Page" in joined,
              "разжать объектные потоки не удалось, красное ниже ничего не значит")
        check(f"{lang}: PDF не тегирован — в нём нет /StructTreeRoot",
              b"/StructTreeRoot" in joined,
              "порядок чтения и роли блоков извлекателю придётся домысливать")


def test_experience_header_is_one_parseable_line() -> None:
    """«Компания | Должность | даты» обязаны лежать на ОДНОЙ строке.

    🔴 Замер 21.08.2026 до правки: в PDF плашки «Remote» и «Employment
    contract» стояли отдельными inline-block без текстового разделителя, между
    ними получался зазор 13 pt, и poppler открывал НОВЫЙ блок — тип занятости
    отрывался от места работы и становился строкой-сиротой. По-русски сироты
    выглядели ещё хуже: «ТК РФ» подходит под вид заголовка раздела.

    Длительность («1 yr 6 mos») стояла ВНУТРИ скобок с датами, из-за чего
    содержимое скобок переставало быть диапазоном дат целиком.
    """
    period = r"[^|·]+ \d{4} - (?:[^|·]+ \d{4}|Present|настоящее время)"
    for lang in LANGS:
        for fmt in FORMATS:
            for item in experience_items(lang):
                anchor = f"{item['company']} | {item['role']}"
                line = entry_header_line(lang, fmt, anchor)
                if line is None:
                    FAILS.append(
                        f"{lang}/{fmt}: заголовок места работы {anchor!r} не найден "
                        f"ровно один раз — он либо разорван, либо продублирован")
                    continue
                pattern = rf"^{re.escape(anchor)} \| {period}(?: · .+)?$"
                check(f"{lang}/{fmt}: заголовок {anchor!r} разбирается",
                      re.match(pattern, line) is not None, repr(line))


def test_no_meta_value_becomes_an_orphan_line() -> None:
    """Локация и тип занятости стоят В заголовке записи и нигде больше.

    Утверждений здесь два, и второе не косметика. «Не стоит отдельной строкой»
    ВЫПОЛНЯЕТСЯ и тогда, когда значения исчезли из файлов совсем: убери из
    `experience_header_text` одну строку `parts.extend(experience_tag_values(item))`
    — «Remote», «Employment contract», «Удаленно», «ТК РФ» пропадут из всех
    шести файлов, а гейт останется зелёным. Поэтому рядом стоит встречное
    утверждение: каждое значение обязано ДОЙТИ до раздела об опыте.

    Ожидаемое НЕ считается вызовом `experience_header_text` — той самой
    функции, которая тут и проверяется: собранный ею эталон уехал бы вместе с
    поломкой, и проверка осталась бы зелёной (пробовал 21.08.2026). Берётся
    только список значений из источника.

    Сверяется склеенный текст раздела, а не отдельная строка: в PDF длинный
    заголовок переносится по ширине полосы («… · Remote · Service» +
    «contract · https://kskkaluga.ru»), и это законно — перенос парсеру не
    мешает, а вот пропажа значения мешает. Что заголовок НАЧИНАЕТСЯ одной
    строкой, стережёт соседний `test_experience_header_is_one_parseable_line`.

    Список значений берётся обходом источника, а не переписан руками: иначе он
    разошёлся бы с `resume.yaml` при первой же новой записи об опыте. Пустой
    список сделал бы обе половины теста непроваливаемыми, поэтому он тоже под
    проверкой.

    Контроль на заведомо лишнем: TXT и DOCX давали здесь ноль и ДО правки —
    расходился только PDF, значит тест не «всегда красный».

    Смотрим внутрь раздела об опыте, а не по всему файлу: место жительства
    («Москва, Россия») стоит отдельной строкой в шапке ЗАКОННО и совпадает по
    тексту с локацией одного из мест работы.
    """
    for lang in LANGS:
        values = {value
                  for item in experience_items(lang)
                  for value in gen.experience_tag_values(item)}
        check(f"{lang}: плашки в источнике есть, иначе проверять нечего",
              len(values) >= 2, str(sorted(values)))
        for fmt in FORMATS:
            block = block_lines(lang, fmt, "experience", "education")
            orphans = [line for line in block if line in values]
            eq(f"{lang}/{fmt}: строк-сирот из плашек", orphans, [])
            joined = " ".join(" ".join(block).split())
            for value in sorted(values):
                check(f"{lang}/{fmt}: плашка {value!r} дошла до раздела об опыте",
                      " ".join(value.split()) in joined)


def test_every_education_entry_is_one_line_with_a_date_range() -> None:
    """Учебное заведение, степень и ОБЕ граничные даты — на одной строке.

    🔴 До правки RU-запись первого образования не влезала в ширину полосы и
    рвалась пополам: «Воронежский … (заочно)» и «(2025 - Ожидаемое окончание:
    2030)» оказывались разными строками. Виноваты были скобки внутри скобок и
    слово «Ожидаемое окончание», засунутое ВНУТРЬ диапазона дат.

    Проверяется пара «вуз ↔ срок обучения» на одной строке, а не вся запись
    целиком: название степени по-русски длиннее английского на треть, и
    запретить ему переноситься нельзя — можно только сделать так, чтобы рвался
    именно он. Что степень не потерялась, стережёт вторая половина теста.
    """
    for lang in LANGS:
        for fmt in FORMATS:
            whole = " ".join(texts()[lang][fmt].split())
            for item in education_items(lang):
                start = item["startDate"].split("-")[0]
                end = item["endDate"].split("-")[0]
                anchor = f"{item['institution']} | {start} - {end}"
                if entry_header_line(lang, fmt, anchor) is None:
                    FAILS.append(
                        f"{lang}/{fmt}: строки {anchor!r} нет ровно одной — вуз и "
                        f"срок обучения разъехались по разным строкам")
                check(f"{lang}/{fmt}: степень {item['degree']!r} на месте",
                      " ".join(item["degree"].split()) in whole)


def test_files_contain_no_exotic_whitespace() -> None:
    """Неразрывные пробелы, мягкие переносы и типографские тире — вон из файлов.

    🔴 До правки DOCX нёс по 24 неразрывных пробела на язык: плашки в
    заголовке места работы обрамлялись `\\xa0`, чтобы Word их не рвал. Строгий
    токенизатор получал слово «\\xa0Remote\\xa0», а не «Remote».

    Контроль на заведомо лишнем: PDF и TXT давали здесь ноль уже тогда.
    """
    forbidden = "  ­‐‑–—ﬀﬁﬂﬃﬄ"
    # Символы невидимые — потеряй кто-нибудь один при правке, и проверка
    # молча станет слабее. Поэтому их число названо явно.
    eq("запрещённых символов в списке", len(set(forbidden)), 12)
    for lang in LANGS:
        for fmt in FORMATS:
            found = sorted({ch for ch in texts()[lang][fmt] if ch in forbidden})
            eq(f"{lang}/{fmt}: экзотических пробелов и тире нет",
               [f"U+{ord(ch):04X}" for ch in found], [])


def test_every_declared_contact_reaches_all_three_files() -> None:
    """Каждый объявленный контакт обязан быть ВИДИМЫМ текстом во всех файлах.

    Список контактов берётся обходом `resume.yaml`, а не переписан сюда: иначе
    новый контакт молча остался бы непроверенным.

    🔴 Замер 21.08.2026: адрес сайта печатался в PDF и DOCX как «jorqen.link» —
    без схемы и без слэша. Ни одна из трёх регулярок открытых парсеров такой
    строкой ссылку не признаёт, тогда как «linkedin.com/in/jorqen» признаёт.
    Ссылка в файле при этом БЫЛА — аннотацией `/URI`, невидимой для текста.
    """
    for lang in LANGS:
        for key in gen.contact_keys(source()):
            value = gen.contact_value(gen.contact(source(), key, lang))
            for fmt in FORMATS:
                whole = " ".join(texts()[lang][fmt].split())
                check(f"{lang}/{fmt}: контакт {key} = {value!r} виден текстом",
                      value in whole)


def test_files_say_where_the_candidate_lives_and_in_what_format() -> None:
    """Место жительства и формат работы — в файле, а не только на сайте.

    🔴 До правки строки локации в выгрузках не было вовсе: `grep Moscow` по EN
    TXT давал два совпадения, и оба — места работы, а не место жительства.
    Именно это поле форма отклика спрашивает первым.
    """
    for lang in LANGS:
        facts = gen.download_profile_facts(source(), lang)
        check(f"{lang}: факты для выгрузки нашлись по ключам", len(facts) == 2,
              f"нашлось {len(facts)}: {facts}")
        for fmt in FORMATS:
            head = " ".join(" ".join(lines(lang, fmt)[:6]).split())
            for value in facts:
                check(f"{lang}/{fmt}: {value!r} стоит в шапке файла",
                      " ".join(value.split()) in head, repr(head))


def resume_holder(tmp: str) -> Path:
    """Временная копия каталога `resume/`: источник и схема, без накладки.

    Накладка кладётся сюда, а не рядом с настоящим источником: тест не имеет
    права оставлять следов в репозитории, даже если он упадёт посередине.
    """
    holder = Path(tmp)
    for name in ("resume.yaml", "resume.schema.yaml"):
        (holder / name).write_bytes((gen.ROOT / "resume" / name).read_bytes())
    return holder


def test_a_started_but_empty_overlay_does_not_break_the_build() -> None:
    """Накладка заведена, а содержимого в ней ещё нет — сборка обязана идти.

    🔴 До правки `resume.local.yaml` из одних комментариев (или нулевой длины)
    ронял ВСЮ сборку: `yaml.safe_load` отдавал None, и `merge_overlay` падал с
    `AttributeError: 'NoneType' object has no attribute 'items'`. Порядок
    «файл завёл, содержимое потом» — обычный, а не экзотический: и `.gitignore`,
    и докстринг `load_data` прямо приглашают этот файл завести.

    Репозиторий публичный, накладка в `.gitignore` — значит в CI её нет вовсе,
    и случай «накладки нет» здесь контроль на заведомо рабочем.

    Не-словарь (список, скаляр) — наоборот, ошибка, и она обязана НАЗЫВАТЬ
    путь: молчаливо проглотить накладку значит собрать резюме без телефона и
    не сказать об этом.
    """
    for name, body in (("нет вовсе", None),
                       ("нулевой длины", ""),
                       ("одни комментарии", "# телефон пока не завожу\n")):
        with tempfile.TemporaryDirectory(prefix="resume-overlay-") as tmp:
            holder = resume_holder(tmp)
            if body is not None:
                (holder / gen.LOCAL_OVERLAY_NAME).write_text(body, encoding="utf-8")
            try:
                data = gen.load_data(holder / "resume.yaml")
            except Exception as exc:
                FAILS.append(f"накладка {name}: сборка упала — "
                             f"{type(exc).__name__}: {exc}")
                continue
            check(f"накладка {name}: источник прочитан целиком",
                  "contacts" in data and "experience" in data, str(sorted(data)))

    for name, body in (("список", "- телефон\n- почта\n"), ("скаляр", "телефон\n")):
        with tempfile.TemporaryDirectory(prefix="resume-overlay-") as tmp:
            holder = resume_holder(tmp)
            (holder / gen.LOCAL_OVERLAY_NAME).write_text(body, encoding="utf-8")
            try:
                gen.load_data(holder / "resume.yaml")
            except ValueError as exc:
                check(f"накладка-{name}: в ошибке назван файл-виновник",
                      gen.LOCAL_OVERLAY_NAME in str(exc), str(exc))
            except Exception as exc:
                FAILS.append(f"накладка-{name}: вместо внятной ошибки "
                             f"{type(exc).__name__}: {exc}")
            else:
                FAILS.append(f"накладка-{name}: проглочена молча — "
                             f"резюме соберётся без того, ради чего её завели")


def test_the_phone_slot_works_without_the_phone_being_in_git() -> None:
    """Место под телефон есть, а значение приходит из файла ВНЕ git.

    Репозиторий публичный (инвариант 4), поэтому настоящего телефона в
    `resume.yaml` нет и не будет. Но «поля нет» и «поле есть, значение снаружи»
    — разные вещи, и проверить надо именно вторую: собрать резюме с накладкой
    `resume/resume.local.yaml`, в которой лежит заведомо ненастоящий номер, и
    убедиться, что он доходит до всех шести файлов и получает ссылку `tel:`.
    """
    fake = "+7 495 000-00-00"
    with tempfile.TemporaryDirectory(prefix="resume-phone-") as tmp:
        holder = resume_holder(tmp)
        (holder / gen.LOCAL_OVERLAY_NAME).write_text(
            f'contacts:\n  phone:\n    value: "{fake}"\n', encoding="utf-8")
        data = gen.load_data(holder / "resume.yaml")

        if "phone" not in gen.contact_keys(data):
            FAILS.append(f"накладка не наложилась: телефона нет среди контактов "
                         f"{gen.contact_keys(data)} — механизма нет, а не значения")
            return
        eq("телефон получает ссылку tel:",
           gen.contact_href(gen.contact(data, "phone", "en")), "tel:+74950000000")

        names = gen.download_file_names(data)
        out = holder / "out"
        for lang in LANGS:
            txt = out / lang / names["txt"]
            pdf = out / lang / names["pdf"]
            docx = out / lang / names["docx"]
            gen.generate_txt(data, lang, txt)
            scale = gen.generate_pdf(data, lang, pdf)
            gen.generate_docx(data, lang, docx, scale)
            got = {"txt": txt.read_text(encoding="utf-8"),
                   "pdf": pdf_text(pdf), "docx": docx_text(docx)}
            for fmt, text in got.items():
                check(f"{lang}/{fmt}: телефон из накладки дошёл до файла",
                      fake in " ".join(text.split()))


# Слова, по которым раздел опознают парсеры. Список рукописный НАМЕРЕННО: это
# контракт вывода, а не отражение источника, и меняться он должен вместе с
# решением, а не молча вслед за `resume.yaml`. Английская половина взята из
# исходников открытого парсера резюме (эвристика по корню слова), русская — из
# названий разделов, к которым приучили hh и Хабр Карьера.
#
# 🔴 Голых корней «опыт» и «навыки» здесь НЕТ намеренно, хотя сначала были.
# Они поглощали соседей: любая строка с «опыт работы» содержит и «опыт»,
# поэтому точная формулировка hh становилась мёртвой записью, а сторож —
# непроваливаемым ровно на том, ради чего написан. Замер 21.08.2026: с голыми
# корнями откат `downloadTitle` по всем четырём разделам краснел только на
# `profile`, а «ПРОФЕССИОНАЛЬНЫЙ ОПЫТ» и «ТЕХНИЧЕСКИЕ НАВЫКИ» проходили молча.
# Английская половина остаётся корневой сознательно: там парсер и правда ищет
# корень, и «Professional Experience» ему разбирается.
SECTION_VOCABULARY = {
    "profile": {"summary", "objective", "about", "о себе", "обо мне"},
    "experience": {"experience", "employment", "опыт работы"},
    "education": {"education", "образование"},
    "skills": {"skills", "ключевые навыки"},
}
SECTION_ORDER = ("profile", "experience", "education", "skills")


def test_download_section_headings_are_from_the_standard_vocabulary() -> None:
    """Заголовок раздела обязан содержать слово, по которому его узнают.

    🔴 До правки над обычным summary стоял заголовок «PROFESSIONAL PROFILE» —
    название САЙТОВОЙ секции, которое в словарь ни одного парсера не входит, а
    по-русски «ПРОФЕССИОНАЛЬНЫЙ ОПЫТ» стоял вместо привычного «Опыт работы».
    Поменять их отдельно от сайта было нечем: у секции было одно название на
    оба применения.

    Контроль на заведомо нужном: «EDUCATION» и «ОБРАЗОВАНИЕ» проходили и до
    правки — то есть тест не «всегда красный».
    """
    for lang in LANGS:
        for key, allowed in SECTION_VOCABULARY.items():
            title = gen.download_section_title(source(), lang, key).lower()
            check(f"{lang}: заголовок раздела {key} = {title!r} из словаря",
                  any(word in title for word in allowed), f"словарь: {sorted(allowed)}")
            for fmt in FORMATS:
                seen = [line for line in lines(lang, fmt) if line.lower() == title]
                eq(f"{lang}/{fmt}: заголовок {title!r} встречается ровно раз",
                   len(seen), 1)


def test_sections_go_summary_experience_education_skills() -> None:
    """Порядок разделов один во всех шести файлах и совпадает с ожидаемым.

    Парсер, потерявший заголовок, приписывает текст ПРЕДЫДУЩЕМУ разделу —
    поэтому порядок не косметика: перепутанные местами разделы делают ошибку
    разбора не заметной, а правдоподобной.
    """
    for lang in LANGS:
        for fmt in FORMATS:
            lowered = [line.lower() for line in lines(lang, fmt)]
            positions = []
            for key in SECTION_ORDER:
                title = gen.download_section_title(source(), lang, key).lower()
                positions.append(lowered.index(title) if title in lowered else -1)
            eq(f"{lang}/{fmt}: порядок разделов {SECTION_ORDER}",
               positions, sorted(positions))
            check(f"{lang}/{fmt}: все четыре раздела на месте", -1 not in positions,
                  str(positions))


def test_no_skill_group_is_named_ambiguously_languages() -> None:
    """Группа навыков не смеет называться просто «Языки».

    🔴 До правки раздел навыков открывался строкой «Languages: Go (Golang),
    Java, …», а разговорные языки шли последней группой. Парсер, ищущий поле
    «языки», встречал первым перечень языков ПРОГРАММИРОВАНИЯ.

    Разговорной группе такое имя разрешено — она и есть про языки.
    """
    ambiguous = {"languages", "языки"}
    for lang in LANGS:
        groups = gen.section(source(), "skills", lang)["groups"]
        spoken = groups[-1]["title"]
        for group in groups:
            title = group["title"].strip().lower()
            check(f"{lang}: группа навыков {group['title']!r} не зовётся просто «языки»",
                  title not in ambiguous or group["title"] == spoken)


def test_pdf_words_are_a_subset_of_txt_words() -> None:
    """В PDF не имеет права появиться слово, которого нет в TXT того же языка.

    🔴 Замер 21.08.2026: RU PDF содержал токен «telegramоповещения», которого
    нет ни в TXT, ни в DOCX, а самого слова «telegram» в нём не было вовсе.
    Причина не в коде: WeasyPrint переносит строку после дефиса, а извлекатель
    считает висячий дефис знаком переноса и склеивает половинки. То есть любая
    правка текста — лотерея, и ловить это надо сторожем, а не глазами.

    Контроль на заведомо нужном: EN давал пустое множество уже тогда — тест не
    «всегда красный».
    """
    for lang in LANGS:
        word = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
        in_txt = set(word.findall(texts()[lang]["txt"].lower()))
        for fmt in ("pdf", "docx"):
            extra = sorted(set(word.findall(texts()[lang][fmt].lower())) - in_txt)
            eq(f"{lang}/{fmt}: слов, которых нет в TXT", extra, [])


# Разделы `resume.yaml`, которых в файлах НЕТ, и почему. Список короткий и
# рукописный намеренно: он и есть решение «это остаётся сайтом». Появился новый
# раздел верхнего уровня — тест падает, и его судьбу надо назвать явно.
NOT_IN_DOWNLOADS = {
    "schema": "служебная метка формата источника",
    "defaultLanguage": "служебное",
    "languages": "служебное",
    "site": "адрес сайта попадает в файлы контактом website",
    "resumeLabels": "подписи, а не содержание",
    "siteUi": "тексты интерфейса сайта: навигация, темы, футер",
    # 🔴 Единственное содержательное исключение, и оно упирается в замер, а не
    # во вкус. «Дополнительная информация» — 1463 символа в EN и 1445 в RU;
    # прогон 21.08.2026 через WeasyPrint на МИНИМАЛЬНОМ масштабе: EN с этим
    # разделом остаётся на двух страницах, RU уходит на три, то есть сборка
    # упрётся в потолок `resolve_download_pdf` и упадёт. Главное из раздела —
    # готовность работать из Турции и к релокации — в файлах есть: оно стоит
    # строкой формата работы под контактами. Остальное решает владелец: либо
    # третья страница, либо сокращённая версия пунктов.
    "preferences": "не влезает в две страницы: RU уходит на третью (замер 21.08.2026)",
}


def test_no_top_level_section_leaves_the_files_unnoticed() -> None:
    """Каждый раздел источника либо выгружается, либо назван в списке исключений.

    🔴 До правки в файлы не попадали `preferences` (7 пунктов, среди них
    готовность работать из Турции) и карточки `strengths`, и НИГДЕ не было
    сказано, что это намеренно: ни докстринга, ни теста. Владелец видел на
    сайте разделы, которых нет в файле, и был прав.

    Это сторож класса «рукописный список того, что на диске»: список коротенький
    и его расхождение с источником видно сразу — новый ключ уронит тест.

    Граница сторожа: он смотрит только на разделы ВЕРХНЕГО уровня. Два раздела
    выгружаются частично, и это осознанно: из `strengths` в файл идёт только
    название (оно служит заголовком «Summary»), карточки остаются сайтом; из
    `person.facts` — только место жительства и формат работы, остальные факты
    дублируют опыт. Частичность этот тест не ловит.
    """
    exported = set(gen.DOWNLOAD_SECTION_SOURCE_KEYS.values()) | {"person", "contacts"}
    unknown = set(source()) - exported - set(NOT_IN_DOWNLOADS)
    eq("разделы, про которые никто не сказал, выгружаются они или нет",
       sorted(unknown), [])
    stale = set(NOT_IN_DOWNLOADS) - set(source())
    eq("список исключений называет разделы, которых в источнике уже нет",
       sorted(stale), [])


def test_every_company_and_university_site_is_visible_text() -> None:
    """Адрес сайта работодателя обязан быть текстом, а не только гиперссылкой.

    🔴 Замер 21.08.2026: в EN PDF было 11 аннотаций `/URI`, в DOCX 10 внешних
    Target — то есть ссылки существовали, но в ТЕКСТЕ их не было: 12 значений
    источника нашлись в TXT и не нашлись в PDF, 19 — в DOCX. Парсер читает
    текст, а не аннотации.
    """
    for lang in LANGS:
        urls = [item["url"] for item in experience_items(lang) if item.get("url")]
        urls += [item["url"] for item in education_items(lang) if item.get("url")]
        check(f"{lang}: адреса вообще есть в источнике", len(urls) >= 5, str(urls))
        for fmt in FORMATS:
            whole = " ".join(texts()[lang][fmt].split())
            for url in urls:
                check(f"{lang}/{fmt}: адрес {url} виден текстом", url in whole)


def test_en_and_ru_carry_the_same_entries() -> None:
    """Сторож симметрии сторон на УРОВНЕ ФАЙЛОВ, а не источника.

    Схема уже требует обе языковые ветки у каждой строки (`localizedStringMap`
    с `required: [en, ru]`), но она ничего не знает про то, что получилось:
    правка вёрстки может уронить блок в одном языке и не уронить в другом.
    Поэтому здесь сверяется число заголовков и число буллитов в собранных
    файлах.
    """
    for fmt in FORMATS:
        counts = {}
        for lang in LANGS:
            body = lines(lang, fmt)
            companies = sum(1 for item in experience_items(lang) for line in body
                            if line.startswith(f"{item['company']} | {item['role']}"))
            bullets = sum(1 for line in body if line.startswith(("•", "- ")))
            counts[lang] = (companies, bullets)
        eq(f"{fmt}: EN и RU совпадают по числу заголовков и буллитов",
           counts["en"], counts["ru"])


def main() -> int:
    # Тесты собираются автоматически — все `test_*` этого модуля, в порядке
    # определения. Ручной список означал бы забытый тест, «зелёный» всегда.
    import inspect as _inspect

    mod = sys.modules[__name__]
    tests = [f for _, f in _inspect.getmembers(mod, _inspect.isfunction)
             if f.__name__.startswith("test_") and f.__module__ == __name__]
    tests.sort(key=lambda f: f.__code__.co_firstlineno)
    for fn in tests:
        fn()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)}:")
        for f in FAILS:
            print("  -", f)
        return 1
    print(f"все проверки прошли ({len(tests)} тестов, {len(LANGS) * len(FORMATS)} файлов)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
