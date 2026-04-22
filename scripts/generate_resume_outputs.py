#!/usr/bin/env python3
"""Generate website data and downloadable resume files from one YAML source."""

from __future__ import annotations

import argparse
from calendar import monthrange
from datetime import date
import html
import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - optional; a constrained fallback parser is used.
    yaml = None

try:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError as exc:  # pragma: no cover - handled at runtime for local setup clarity.
    raise SystemExit(
        "Missing DOCX dependencies. Install python-docx or run through scripts/build_resume_formats.sh."
    ) from exc

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError as exc:  # pragma: no cover - handled at runtime for local setup clarity.
    raise SystemExit(
        "Missing PDF dependencies. Install reportlab or run through scripts/build_resume_formats.sh."
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
ACCENT_HEX = "1D5FD1"
TEXT_HEX = "0F1F36"
MUTED_HEX = "526581"
LINE_HEX = "DBE4F2"
CONTACT_KEYS = ("website", "linkedin", "github", "telegram")
WEBSITE_CONTACT_URL = "https://jorqen.github.io"
WEBSITE_CONTACT_PATH = "jorqen.github.io/jorqen"
MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ru": ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"),
}


def localized(value: Any, lang: str) -> str:
    if isinstance(value, dict) and set(value.keys()).issubset({"en", "ru"}):
        return str(value.get(lang) or value.get("en") or "")
    return str(value or "")


def resolve_localized(value: Any, lang: str) -> Any:
    if isinstance(value, dict) and set(value.keys()).issubset({"en", "ru"}):
        return value.get(lang) if value.get(lang) is not None else value.get("en")
    if isinstance(value, dict):
        return {key: resolve_localized(item, lang) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_localized(item, lang) for item in value]
    return value


def parse_scalar(value: str) -> Any:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_yaml_subset(path: Path) -> dict[str, Any]:
    if yaml is not None:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise ValueError("Resume source must be a YAML mapping.")
        return parsed

    lines = [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return {}, index
        stripped = lines[index].strip()
        if line_indent(lines[index]) < indent:
            return {}, index
        if stripped.startswith("-"):
            return parse_list(index, indent)
        return parse_map(index, indent)

    def parse_list(index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(lines):
            line = lines[index]
            current_indent = line_indent(line)
            stripped = line.strip()
            if current_indent < indent or not stripped.startswith("-"):
                break
            if current_indent != indent:
                raise ValueError(f"Invalid list indentation near: {line}")
            rest = stripped[1:].strip()
            index += 1
            if rest:
                result.append(parse_scalar(rest))
            else:
                item, index = parse_block(index, indent + 2)
                result.append(item)
        return result, index

    def parse_map(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(lines):
            line = lines[index]
            current_indent = line_indent(line)
            stripped = line.strip()
            if current_indent < indent or stripped.startswith("-"):
                break
            if current_indent != indent:
                raise ValueError(f"Invalid mapping indentation near: {line}")
            key, separator, rest = stripped.partition(":")
            if not separator:
                raise ValueError(f"Invalid YAML mapping line: {line}")
            key = key.strip()
            rest = rest.strip()
            index += 1
            if rest:
                result[key] = parse_scalar(rest)
            else:
                result[key], index = parse_block(index, indent + 2)
        return result, index

    parsed, index = parse_block(0, 0)
    if index != len(lines):
        raise ValueError(f"Could not parse YAML fully. Stopped at line {index + 1}.")
    if not isinstance(parsed, dict):
        raise ValueError("Resume source must be a YAML mapping.")
    return parsed


def normalize_website_candidate(value: Any) -> str:
    text = str(value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.rstrip("/")


def has_current_website_contact(contact: dict[str, Any]) -> bool:
    expected = normalize_website_candidate(WEBSITE_CONTACT_URL)
    return any(
        normalize_website_candidate(contact.get(key)) == expected
        for key in ("text", "url")
    )


def format_resume_date(value: str, lang: str) -> str:
    if not value:
        return ""
    parts = value.split("-")
    if len(parts) == 1:
        return parts[0]
    year, month = parts
    return f"{MONTHS[lang][int(month) - 1]} {year}"


def format_period(item: dict[str, Any], lang: str, labels: dict[str, str]) -> str:
    start = format_resume_date(item["startDate"], lang)
    end_date = item.get("endDate")
    end = format_resume_date(end_date, lang) if end_date else labels["present"]
    return f"{start} - {end}"


def resume_date_upper_bound(value: str | None) -> date | None:
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        if len(parts) == 1:
            return date(year, 12, 31)
        month = int(parts[1])
        return date(year, month, monthrange(year, month)[1])
    except (IndexError, TypeError, ValueError):
        return None


def is_expected_education(item: dict[str, Any]) -> bool:
    end_date = resume_date_upper_bound(item.get("endDate"))
    return bool(end_date and end_date > date.today())


def format_education_period(item: dict[str, Any], lang: str, labels: dict[str, str]) -> str:
    if is_expected_education(item):
        end = f"{labels['expectedGraduation']}: {format_resume_date(item['endDate'], lang)}"
        if item.get("startDate"):
            return f"{format_resume_date(item['startDate'], lang)} - {end}"
        return end
    if item.get("startDate") and item.get("endDate"):
        return f"{format_resume_date(item['startDate'], lang)} - {format_resume_date(item['endDate'], lang)}"
    return format_resume_date(item.get("endDate") or item.get("startDate") or "", lang)


def validate_source(source: dict[str, Any]) -> None:
    languages = source.get("languages") or []
    if languages != ["en", "ru"]:
        raise ValueError("Expected languages to be ['en', 'ru'].")

    contacts = source.get("contacts", {})
    missing_contacts = [key for key in CONTACT_KEYS if not contacts.get(key, {}).get("url")]
    if missing_contacts:
        raise ValueError(f"Missing required contact URLs: {', '.join(missing_contacts)}")
    if not has_current_website_contact(contacts["website"]):
        raise ValueError("Downloaded resumes must include the current website contact URL.")

    experience_items = source.get("experience", {}).get("items") or []
    starts = [item.get("startDate") for item in experience_items]
    if any(not start for start in starts):
        raise ValueError("Every experience item must have startDate.")
    if starts != sorted(starts, reverse=True):
        raise ValueError("Experience must be sorted by descending startDate.")
    for index, item in enumerate(experience_items, start=1):
        highlights = item.get("highlights") or []
        if not highlights:
            raise ValueError(f"Experience item #{index} must have highlights.")
        if not item.get("stack"):
            raise ValueError(f"Experience item #{index} must have stack.")


def normalize_contact(source: dict[str, Any], lang: str, key: str) -> dict[str, str]:
    url = WEBSITE_CONTACT_URL if key == "website" else source["url"]
    text = localized(source["text"], lang)
    fallback_text = WEBSITE_CONTACT_PATH if key == "website" else url
    return {
        "label": localized(source["label"], lang),
        "text": text or fallback_text,
        "url": url,
    }


def normalized_download_names(content: dict[str, Any], lang: str) -> dict[str, str]:
    base_name = "_".join(str(content["hero"]["name"]).split())
    suffix = lang.upper()
    return {
        "pdf": f"{base_name}_{suffix}.pdf",
        "docx": f"{base_name}_{suffix}.docx",
        "txt": f"{base_name}_{suffix}.txt",
    }


def normalize_for_language(source: dict[str, Any], lang: str) -> dict[str, Any]:
    site = resolve_localized(source["site"], lang)
    labels = resolve_localized(source["resumeLabels"], lang)

    experience = resolve_localized(source["experience"], lang)
    experience_items = []
    for item in experience["items"]:
        normalized = {
            "company": item["company"],
            "companyIcon": item.get("icon", ""),
            "companyIconDark": item.get("iconDark", ""),
            "companyUrl": item.get("url", ""),
            "role": item["role"],
            "period": format_period(item, lang, labels),
            "location": item["location"],
            "intro": item["summary"],
            "bullets": item["highlights"],
            "stack": item["stack"],
            "startDate": item["startDate"],
            "endDate": item.get("endDate"),
        }
        experience_items.append(normalized)
    experience["items"] = experience_items

    education = resolve_localized(source["education"], lang)
    education["items"] = [
        {
            "institution": item["institution"],
            "degree": item["degree"],
            "period": format_education_period(item, lang, labels),
            "startDate": item.get("startDate"),
            "endDate": item.get("endDate"),
        }
        for item in education["items"]
    ]

    resume = site["resumeDownloads"]
    resume["files"] = {
        "pdf": f"resume/{lang}/resume.pdf",
        "docx": f"resume/{lang}/resume.docx",
        "txt": f"resume/{lang}/resume.txt",
    }
    resume["downloadNames"] = normalized_download_names(site, lang)

    return {
        "meta": site["meta"],
        "brand": site["brand"],
        "nav": site["nav"],
        "langSwitcherLabel": site["langSwitcherLabel"],
        "theme": site["theme"],
        "hero": site["hero"],
        "resume": resume,
        "experience": experience,
        "education": education,
        "strengths": resolve_localized(source["strengths"], lang),
        "skills": resolve_localized(source["skills"], lang),
        "preferences": resolve_localized(source["preferences"], lang),
        "gallery": resolve_localized(source["gallery"], lang),
        "lightbox": site["lightbox"],
        "footer": site["footer"],
    }


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    validate_source(source)
    languages = source["languages"]
    content = {lang: normalize_for_language(source, lang) for lang in languages}
    contacts = {
        key: {lang: normalize_contact(source["contacts"][key], lang, key) for lang in languages}
        for key in CONTACT_KEYS
    }
    labels = {lang: resolve_localized(source["resumeLabels"], lang) for lang in languages}
    return {
        "meta": {
            "schema": source["schema"],
            "defaultLanguage": source["defaultLanguage"],
            "languages": languages,
        },
        "contacts": contacts,
        "labels": labels,
        "content": content,
    }


def load_data(path: Path) -> dict[str, Any]:
    source = parse_yaml_subset(path)
    return normalize_source(source)


def contact_parts(data: dict[str, Any], lang: str) -> list[str]:
    parts = []
    for key in CONTACT_KEYS:
        contact = data["contacts"][key][lang]
        parts.append(f"{contact['label']}: {contact['url']}")
    return parts


def wrap_line(text: str, width: int = 100) -> list[str]:
    if not text:
        return []
    return textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [text]


def add_wrapped(lines: list[str], text: str = "", width: int = 100) -> None:
    if text:
        lines.extend(wrap_line(text, width))
    else:
        lines.append("")


def generate_txt(data: dict[str, Any], lang: str, output_path: Path) -> None:
    content = data["content"][lang]
    labels = data["labels"][lang]
    lines: list[str] = []

    lines.append(content["hero"]["name"])
    lines.append("=" * len(content["hero"]["name"]))
    lines.append(f"{content['hero']['facts'][0]['value']} | {content['hero']['facts'][3]['value']}")
    lines.append(" | ".join(contact_parts(data, lang)))
    add_wrapped(lines)
    add_wrapped(lines, content["hero"]["role"])
    add_wrapped(lines, content["hero"]["summary"])

    add_wrapped(lines)
    lines.append(content["experience"]["title"].upper())
    lines.append("-" * len(content["experience"]["title"]))
    for item in content["experience"]["items"]:
        add_wrapped(lines)
        lines.append(f"{item['company']} | {item['role']}")
        if item.get("companyUrl"):
            lines.append(f"{content['experience']['companySiteLabel']}: {item['companyUrl']}")
        lines.append(f"{item['period']} | {item['location']}")
        add_wrapped(lines, item["intro"])
        lines.append(f"{labels['achievements']}:")
        for bullet in item["bullets"]:
            for index, wrapped in enumerate(wrap_line(bullet, 96)):
                prefix = "- " if index == 0 else "  "
                lines.append(f"{prefix}{wrapped}")
        lines.append(f"{labels['stack']}: {', '.join(item['stack'])}")

    add_wrapped(lines)
    lines.append(content["education"]["title"].upper())
    lines.append("-" * len(content["education"]["title"]))
    for item in content["education"]["items"]:
        add_wrapped(lines)
        lines.append(item["institution"])
        add_wrapped(lines, item["degree"])
        lines.append(item["period"])

    add_wrapped(lines)
    lines.append(content["strengths"]["title"].upper())
    lines.append("-" * len(content["strengths"]["title"]))
    for card in content["strengths"]["cards"]:
        add_wrapped(lines)
        lines.append(card["title"])
        add_wrapped(lines, card["body"])

    add_wrapped(lines)
    lines.append(content["skills"]["title"].upper())
    lines.append("-" * len(content["skills"]["title"]))
    for group in content["skills"]["groups"]:
        lines.append(f"{group['title']}: {', '.join(group['items'])}")

    add_wrapped(lines)
    lines.append(content["preferences"]["title"].upper())
    lines.append("-" * len(content["preferences"]["title"]))
    for item in content["preferences"]["items"]:
        for index, wrapped in enumerate(wrap_line(item, 96)):
            prefix = "- " if index == 0 else "  "
            lines.append(f"{prefix}{wrapped}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def add_hyperlink(paragraph: Any, text: str, url: str) -> None:
    part = paragraph.part
    relationship_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)

    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), ACCENT_HEX)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(color)
    properties.append(underline)
    run.append(properties)
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)  # noqa: SLF001 - python-docx exposes no public hyperlink writer.


def configure_docx(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Inches(0.55)
    section.bottom_margin = Inches(0.55)
    section.left_margin = Inches(0.62)
    section.right_margin = Inches(0.62)

    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0x0F, 0x1F, 0x36)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.08

    for style_name, size, color in [
        ("Heading 1", 22, RGBColor(0x0F, 0x1F, 0x36)),
        ("Heading 2", 12, RGBColor(0x1D, 0x5F, 0xD1)),
        ("Heading 3", 11, RGBColor(0x0F, 0x1F, 0x36)),
    ]:
        style = styles[style_name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before = Pt(8 if style_name != "Heading 1" else 0)
        style.paragraph_format.space_after = Pt(5)

    bullet = styles["List Bullet"]
    bullet.font.name = "Calibri"
    bullet.font.size = Pt(10)
    bullet.paragraph_format.space_after = Pt(2)
    bullet.paragraph_format.left_indent = Inches(0.22)


def convert_svg_to_png(source: Path, build_dir: Path, size: int = 96) -> Path | None:
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        return None
    build_dir.mkdir(parents=True, exist_ok=True)
    output = build_dir / f"{source.stem}-{size}.png"
    if output.exists() and output.stat().st_mtime >= source.stat().st_mtime:
        return output
    subprocess.run(
        [rsvg, "-w", str(size), "-h", str(size), "-o", str(output), str(source)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output


def resolve_image(path_value: str, build_dir: Path, size: int = 96) -> Path | None:
    if not path_value:
        return None
    source = ROOT / path_value
    if not source.exists():
        return None
    if source.suffix.lower() == ".svg":
        return convert_svg_to_png(source, build_dir, size)
    if source.suffix.lower() in {".png", ".jpg", ".jpeg"}:
        return source
    return None


def generate_docx(data: dict[str, Any], lang: str, output_path: Path, build_dir: Path) -> None:
    content = data["content"][lang]
    labels = data["labels"][lang]
    document = Document()
    configure_docx(document)

    document.add_heading(content["hero"]["name"], level=1)
    contact_paragraph = document.add_paragraph()
    contact_paragraph.add_run(f"{content['hero']['facts'][0]['value']} | {content['hero']['facts'][3]['value']}\n")
    for index, key in enumerate(CONTACT_KEYS):
        contact = data["contacts"][key][lang]
        if index:
            contact_paragraph.add_run(" | ")
        contact_paragraph.add_run(f"{contact['label']}: ")
        add_hyperlink(contact_paragraph, contact["text"], contact["url"])

    document.add_paragraph(content["hero"]["role"])
    document.add_paragraph(content["hero"]["summary"])

    document.add_heading(content["experience"]["title"].upper(), level=2)
    for item in content["experience"]["items"]:
        title = document.add_paragraph()
        title.style = document.styles["Heading 3"]
        image_path = resolve_image(item.get("companyIconDark") or item.get("companyIcon", ""), build_dir / "icons", 96)
        if image_path:
            title.add_run().add_picture(str(image_path), width=Inches(0.16))
            title.add_run(" ")
        if item.get("companyUrl"):
            add_hyperlink(title, item["company"], item["companyUrl"])
        else:
            title.add_run(item["company"]).bold = True

        role = document.add_paragraph()
        role.add_run(item["role"]).bold = True
        role.add_run(f" | {item['period']} | {item['location']}")
        document.add_paragraph(item["intro"])
        document.add_paragraph(f"{labels['achievements']}:").runs[0].bold = True
        for bullet in item["bullets"]:
            document.add_paragraph(bullet, style="List Bullet")
        stack = document.add_paragraph()
        stack.add_run(f"{labels['stack']}: ").bold = True
        stack.add_run(", ".join(item["stack"]))

    document.add_heading(content["education"]["title"].upper(), level=2)
    for item in content["education"]["items"]:
        paragraph = document.add_paragraph()
        paragraph.add_run(item["institution"]).bold = True
        paragraph.add_run().add_break(WD_BREAK.LINE)
        paragraph.add_run(item["degree"])
        paragraph.add_run(f" | {item['period']}")

    document.add_heading(content["strengths"]["title"].upper(), level=2)
    for card in content["strengths"]["cards"]:
        paragraph = document.add_paragraph()
        paragraph.add_run(f"{card['title']}: ").bold = True
        paragraph.add_run(card["body"])

    document.add_heading(content["skills"]["title"].upper(), level=2)
    for group in content["skills"]["groups"]:
        paragraph = document.add_paragraph()
        paragraph.add_run(f"{group['title']}: ").bold = True
        paragraph.add_run(", ".join(group["items"]))

    document.add_heading(content["preferences"]["title"].upper(), level=2)
    for item in content["preferences"]["items"]:
        document.add_paragraph(item, style="List Bullet")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)


def find_font(candidates: list[str]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    fc_match = shutil.which("fc-match")
    if fc_match:
        for family in ("DejaVu Sans", "Arial Unicode MS", "Arial"):
            result = subprocess.run(
                [fc_match, "-f", "%{file}", family],
                check=False,
                text=True,
                capture_output=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                path = Path(result.stdout.strip())
                if path.exists():
                    return path
    return None


def register_pdf_fonts(lang: str) -> tuple[str, str]:
    if lang == "en":
        return "Helvetica", "Helvetica-Bold"

    regular = find_font([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ])
    bold = find_font([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]) or regular

    if not regular:
        raise RuntimeError("Could not find a Unicode TrueType font for PDF generation.")

    pdfmetrics.registerFont(TTFont("ResumeRegular", str(regular)))
    pdfmetrics.registerFont(TTFont("ResumeBold", str(bold)))
    return "ResumeRegular", "ResumeBold"


def pdf_link(text: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}"><font color="#{ACCENT_HEX}">{html.escape(text)}</font></a>'


def pdf_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>") if "<" not in text else text, style)


def build_pdf_styles(lang: str) -> dict[str, ParagraphStyle]:
    regular, bold = register_pdf_fonts(lang)
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ResumeTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=22,
            leading=26,
            textColor=colors.HexColor(f"#{TEXT_HEX}"),
            spaceAfter=6,
            alignment=TA_CENTER,
        ),
        "contact": ParagraphStyle(
            "ResumeContact",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor(f"#{MUTED_HEX}"),
            alignment=TA_CENTER,
            spaceAfter=7,
        ),
        "body": ParagraphStyle(
            "ResumeBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.4,
            leading=12.3,
            textColor=colors.HexColor(f"#{TEXT_HEX}"),
            spaceAfter=5,
        ),
        "section": ParagraphStyle(
            "ResumeSection",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=11.2,
            leading=13.5,
            textColor=colors.HexColor(f"#{ACCENT_HEX}"),
            spaceBefore=9,
            spaceAfter=5,
        ),
        "company": ParagraphStyle(
            "ResumeCompany",
            parent=base["Heading3"],
            fontName=bold,
            fontSize=10.3,
            leading=12,
            textColor=colors.HexColor(f"#{TEXT_HEX}"),
            spaceBefore=4,
            spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "ResumeMeta",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.7,
            leading=10.5,
            textColor=colors.HexColor(f"#{MUTED_HEX}"),
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.9,
            leading=11.2,
            leftIndent=11,
            firstLineIndent=-7,
            textColor=colors.HexColor(f"#{TEXT_HEX}"),
            spaceAfter=2.2,
        ),
    }


def generate_pdf(data: dict[str, Any], lang: str, output_path: Path, build_dir: Path) -> None:
    content = data["content"][lang]
    labels = data["labels"][lang]
    styles = build_pdf_styles(lang)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.52 * inch,
        bottomMargin=0.52 * inch,
        title=content["meta"]["title"],
        author=content["hero"]["name"],
    )
    story: list[Any] = []

    story.append(Paragraph(html.escape(content["hero"]["name"]), styles["title"]))
    contact_links = [
        f"{html.escape(data['contacts'][key][lang]['label'])}: {pdf_link(data['contacts'][key][lang]['text'], data['contacts'][key][lang]['url'])}"
        for key in CONTACT_KEYS
    ]
    story.append(Paragraph(
        f"{html.escape(content['hero']['facts'][0]['value'])} | {html.escape(content['hero']['facts'][3]['value'])}<br/>"
        + " | ".join(contact_links),
        styles["contact"],
    ))
    story.append(pdf_paragraph(content["hero"]["role"], styles["body"]))
    story.append(pdf_paragraph(content["hero"]["summary"], styles["body"]))

    story.append(Paragraph(html.escape(content["experience"]["title"].upper()), styles["section"]))
    for item in content["experience"]["items"]:
        company_text = pdf_link(item["company"], item["companyUrl"]) if item.get("companyUrl") else html.escape(item["company"])
        company_para = Paragraph(company_text, styles["company"])
        image_path = resolve_image(item.get("companyIconDark") or item.get("companyIcon", ""), build_dir / "icons", 96)
        if image_path:
            icon = Image(str(image_path), width=0.18 * inch, height=0.18 * inch)
            table = Table([[icon, company_para]], colWidths=[0.24 * inch, None], hAlign="LEFT")
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(table)
        else:
            story.append(company_para)
        story.append(pdf_paragraph(f"{item['role']} | {item['period']} | {item['location']}", styles["meta"]))
        story.append(pdf_paragraph(item["intro"], styles["body"]))
        story.append(pdf_paragraph(f"{labels['achievements']}:", styles["body"]))
        for bullet in item["bullets"]:
            story.append(Paragraph(f"• {html.escape(bullet)}", styles["bullet"]))
        story.append(pdf_paragraph(f"{labels['stack']}: {', '.join(item['stack'])}", styles["meta"]))

    story.append(Paragraph(html.escape(content["education"]["title"].upper()), styles["section"]))
    for item in content["education"]["items"]:
        story.append(pdf_paragraph(f"{item['institution']} - {item['degree']} | {item['period']}", styles["body"]))

    story.append(Paragraph(html.escape(content["strengths"]["title"].upper()), styles["section"]))
    for card in content["strengths"]["cards"]:
        story.append(pdf_paragraph(f"{card['title']}: {card['body']}", styles["body"]))

    story.append(Paragraph(html.escape(content["skills"]["title"].upper()), styles["section"]))
    for group in content["skills"]["groups"]:
        story.append(pdf_paragraph(f"{group['title']}: {', '.join(group['items'])}", styles["body"]))

    story.append(Paragraph(html.escape(content["preferences"]["title"].upper()), styles["section"]))
    for item in content["preferences"]["items"]:
        story.append(Paragraph(f"• {html.escape(item)}", styles["bullet"]))

    doc.build(story)


def copy_public_data(data_path: Path, output_root: Path) -> None:
    target_dir = output_root / "data"
    target_dir.mkdir(parents=True, exist_ok=True)
    data_target = target_dir / data_path.name
    if data_path.resolve() != data_target.resolve():
        shutil.copy2(data_path, data_target)
    schema_path = data_path.with_name("resume.schema.yaml")
    schema_target = target_dir / schema_path.name
    if schema_path.exists() and schema_path.resolve() != schema_target.resolve():
        shutil.copy2(schema_path, schema_target)


def write_site_data(data: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": data["meta"],
        "contacts": {
            key: data["contacts"][key][data["meta"]["defaultLanguage"]]
            for key in CONTACT_KEYS
        },
        "content": data["content"],
    }
    output_path.write_text(
        "window.JORQEN_RESUME_DATA = "
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )


def generate_outputs(
    data_path: Path,
    data: dict[str, Any],
    output_root: Path,
    site_output: Path,
    build_dir: Path,
) -> None:
    copy_public_data(data_path, output_root)
    write_site_data(data, site_output)
    for lang in data["meta"]["languages"]:
        lang_dir = output_root / lang
        generate_txt(data, lang, lang_dir / "resume.txt")
        generate_docx(data, lang, lang_dir / "resume.docx", build_dir)
        generate_pdf(data, lang, lang_dir / "resume.pdf", build_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "resume/data/resume.yaml")
    parser.add_argument("--output-root", type=Path, default=ROOT / "resume")
    parser.add_argument("--site-output", type=Path, default=ROOT / "assets/generated/resume-content.js")
    parser.add_argument("--build-dir", type=Path, default=ROOT / ".build/resume-assets")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_data(args.data)
    generate_outputs(args.data, data, args.output_root, args.site_output, args.build_dir)
    print("Resume outputs generated successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - keeps CI/local failures readable.
        print(f"Resume generation failed: {exc}", file=sys.stderr)
        raise
