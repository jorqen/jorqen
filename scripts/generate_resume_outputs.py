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
except ImportError as exc:  # pragma: no cover - handled at runtime for local setup clarity.
    raise SystemExit(
        "Missing YAML dependency. Install PyYAML or run through scripts/build_resume_formats.sh."
    ) from exc

try:
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.text import WD_BREAK
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Mm, Pt, RGBColor
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


def localized_tree(value: Any, lang: str) -> Any:
    if isinstance(value, dict) and set(value.keys()).issubset({"en", "ru"}):
        return value[lang]
    if isinstance(value, dict):
        return {key: localized_tree(item, lang) for key, item in value.items()}
    if isinstance(value, list):
        return [localized_tree(item, lang) for item in value]
    return value


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def hex_color(value: str) -> str:
    return str(value).strip().removeprefix("#").upper()


def rgb_color(hex_value: str) -> RGBColor:
    color = hex_color(hex_value)
    return RGBColor(
        int(color[0:2], 16),
        int(color[2:4], 16),
        int(color[4:6], 16),
    )


def download_styles(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "colors": {
            key: hex_color(value)
            for key, value in source["downloadStyles"]["colors"].items()
        },
        "typography": source["downloadStyles"]["typography"],
    }


def format_resume_date(value: str, month_names: list[str]) -> str:
    parts = value.split("-")
    if len(parts) == 1:
        return parts[0]
    year, month = parts
    return f"{month_names[int(month) - 1]} {year}"


def format_period(item: dict[str, Any], labels: dict[str, str], month_names: list[str]) -> str:
    start = format_resume_date(item["startDate"], month_names)
    end_date = item.get("endDate")
    end = format_resume_date(end_date, month_names) if end_date else labels["present"]
    return f"{start} - {end}"


def resume_date_upper_bound(value: str | None) -> date | None:
    parts = value.split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return date(year, 12, 31)
    month = int(parts[1])
    return date(year, month, monthrange(year, month)[1])


def is_expected_education(item: dict[str, Any]) -> bool:
    return resume_date_upper_bound(item["endDate"]) > date.today()


def format_education_period(item: dict[str, Any], labels: dict[str, str], month_names: list[str]) -> str:
    start = format_resume_date(item["startDate"], month_names)
    end_date = format_resume_date(item["endDate"], month_names)
    end = f"{labels['expectedGraduation']}: {end_date}" if is_expected_education(item) else end_date
    return f"{start} - {end}"


def download_file_names(file_base_name: str) -> dict[str, str]:
    return {
        "pdf": f"{file_base_name}.pdf",
        "docx": f"{file_base_name}.docx",
        "txt": f"{file_base_name}.txt",
    }


def language_content(source: dict[str, Any], lang: str) -> dict[str, Any]:
    site = localized_tree(source["site"], lang)
    labels = localized_tree(source["resumeLabels"], lang)
    month_names = source["dateFormats"]["monthNames"][lang]

    experience = localized_tree(source["experience"], lang)
    experience_items = []
    optional_experience_fields = (
        ("companyIcon", "icon"),
        ("companyIconDark", "iconDark"),
        ("companyUrl", "url"),
    )
    for item in experience["items"]:
        output_item = {
            "company": item["company"],
            "role": item["role"],
            "period": format_period(item, labels, month_names),
            "location": item["location"],
            "intro": item["summary"],
            "bullets": item["highlights"],
            "stack": item["stack"],
        }
        output_item.update({
            output_key: item[source_key]
            for output_key, source_key in optional_experience_fields
            if source_key in item
        })
        experience_items.append(output_item)
    experience["items"] = experience_items

    education = localized_tree(source["education"], lang)
    education["items"] = [
        {
            "institution": item["institution"],
            "degree": item["degree"],
            "period": format_education_period(item, labels, month_names),
        }
        for item in education["items"]
    ]

    resume = site["resumeDownloads"]
    file_names = download_file_names(resume["fileBaseName"])
    resume["files"] = {
        extension: f"resume/{lang}/{name}"
        for extension, name in file_names.items()
    }
    resume["downloadNames"] = file_names

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
        "strengths": localized_tree(source["strengths"], lang),
        "skills": localized_tree(source["skills"], lang),
        "preferences": localized_tree(source["preferences"], lang),
        "gallery": localized_tree(source["gallery"], lang),
        "lightbox": site["lightbox"],
        "footer": site["footer"],
    }


def build_resume_data(source: dict[str, Any]) -> dict[str, Any]:
    languages = source["languages"]
    contact_keys = list(source["contacts"].keys())
    content = {lang: language_content(source, lang) for lang in languages}
    contacts = {
        key: {lang: localized_tree(contact, lang) for lang in languages}
        for key, contact in source["contacts"].items()
    }
    labels = {lang: localized_tree(source["resumeLabels"], lang) for lang in languages}
    return {
        "meta": {
            "schema": source["schema"],
            "defaultLanguage": source["defaultLanguage"],
            "languages": languages,
        },
        "contactKeys": contact_keys,
        "downloadStyles": download_styles(source),
        "contacts": contacts,
        "labels": labels,
        "content": content,
    }


def load_data(path: Path) -> dict[str, Any]:
    source = load_yaml_mapping(path)
    return build_resume_data(source)


def contact_parts(data: dict[str, Any], lang: str) -> list[str]:
    parts = []
    for key in data["contactKeys"]:
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


def add_hyperlink(paragraph: Any, text: str, url: str, color_hex: str, bold: bool = False) -> None:
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
    color.set(qn("w:val"), color_hex)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(color)
    properties.append(underline)
    if bold:
        properties.append(OxmlElement("w:b"))
    run.append(properties)
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)  # noqa: SLF001 - python-docx exposes no public hyperlink writer.


def docx_paragraph_style(styles: Any, name: str) -> Any:
    try:
        return styles[name]
    except KeyError:
        return styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)


def configure_docx_paragraph_style(
    styles: Any,
    name: str,
    font_name: str,
    font_size: float,
    color: RGBColor,
    *,
    bold: bool = False,
    alignment: Any = None,
    keep_with_next: bool = False,
    line_spacing: float = 12,
    space_before: float = 0,
    space_after: float = 0,
    left_indent: float | None = None,
    first_line_indent: float | None = None,
) -> None:
    style = docx_paragraph_style(styles, name)
    style.font.name = font_name
    style.font.size = Pt(font_size)
    style.font.bold = bold
    style.font.color.rgb = color
    style.paragraph_format.alignment = alignment
    style.paragraph_format.keep_with_next = keep_with_next
    style.paragraph_format.space_before = Pt(space_before)
    style.paragraph_format.space_after = Pt(space_after)
    style.paragraph_format.line_spacing = Pt(line_spacing)
    if left_indent is not None:
        style.paragraph_format.left_indent = Inches(left_indent)
    if first_line_indent is not None:
        style.paragraph_format.first_line_indent = Inches(first_line_indent)


def configure_docx(document: Document, download_style: dict[str, Any]) -> None:
    style_colors = download_style["colors"]
    typography = download_style["typography"]
    section = document.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Inches(0.55)
    section.bottom_margin = Inches(0.55)
    section.left_margin = Inches(0.62)
    section.right_margin = Inches(0.62)

    font_name = "Arial"
    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = font_name
    normal.font.size = Pt(9.4)
    normal.font.color.rgb = rgb_color(style_colors["text"])
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = Pt(12.3)

    configure_docx_paragraph_style(
        styles,
        "ResumeTitle",
        font_name,
        typography["title"]["fontSize"],
        rgb_color(style_colors["text"]),
        bold=True,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
        line_spacing=typography["title"]["lineHeight"],
        space_after=6,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeContact",
        font_name,
        typography["contact"]["fontSize"],
        rgb_color(style_colors["muted"]),
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        line_spacing=typography["contact"]["lineHeight"],
        space_after=7,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeBody",
        font_name,
        typography["body"]["fontSize"],
        rgb_color(style_colors["text"]),
        line_spacing=typography["body"]["lineHeight"],
        space_after=5,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeSection",
        font_name,
        typography["section"]["fontSize"],
        rgb_color(style_colors["accent"]),
        bold=True,
        keep_with_next=True,
        line_spacing=typography["section"]["lineHeight"],
        space_before=9,
        space_after=5,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeCompany",
        font_name,
        typography["company"]["fontSize"],
        rgb_color(style_colors["text"]),
        bold=True,
        keep_with_next=True,
        line_spacing=typography["company"]["lineHeight"],
        space_before=4,
        space_after=2,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeMeta",
        font_name,
        typography["meta"]["fontSize"],
        rgb_color(style_colors["muted"]),
        line_spacing=typography["meta"]["lineHeight"],
        space_after=4,
    )
    configure_docx_paragraph_style(
        styles,
        "ResumeBullet",
        font_name,
        typography["bullet"]["fontSize"],
        rgb_color(style_colors["text"]),
        line_spacing=typography["bullet"]["lineHeight"],
        space_after=2.2,
        left_indent=0.15,
        first_line_indent=-0.1,
    )


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
    download_style = data["downloadStyles"]
    style_colors = download_style["colors"]
    document = Document()
    configure_docx(document, download_style)

    document.add_paragraph(content["hero"]["name"], style="ResumeTitle")
    contact_paragraph = document.add_paragraph(style="ResumeContact")
    contact_paragraph.add_run(f"{content['hero']['facts'][0]['value']} | {content['hero']['facts'][3]['value']}\n")
    for index, key in enumerate(data["contactKeys"]):
        contact = data["contacts"][key][lang]
        if index:
            contact_paragraph.add_run(" | ")
        add_hyperlink(contact_paragraph, contact["label"], contact["url"], style_colors["accent"])

    document.add_paragraph(content["hero"]["role"], style="ResumeBody")
    document.add_paragraph(content["hero"]["summary"], style="ResumeBody")

    document.add_paragraph(content["experience"]["title"].upper(), style="ResumeSection")
    for item in content["experience"]["items"]:
        title = document.add_paragraph(style="ResumeCompany")
        image_path = resolve_image(item.get("companyIconDark") or item.get("companyIcon", ""), build_dir / "icons", 96)
        if image_path:
            title.add_run().add_picture(str(image_path), width=Inches(0.16))
            title.add_run(" ")
        if item.get("companyUrl"):
            add_hyperlink(title, item["company"], item["companyUrl"], style_colors["accent"], bold=True)
        else:
            title.add_run(item["company"]).bold = True

        role = document.add_paragraph(style="ResumeMeta")
        role.add_run(item["role"]).bold = True
        role.add_run(f" | {item['period']} | {item['location']}")
        document.add_paragraph(item["intro"], style="ResumeBody")
        document.add_paragraph(f"{labels['achievements']}:", style="ResumeBody").runs[0].bold = True
        for bullet in item["bullets"]:
            document.add_paragraph(f"• {bullet}", style="ResumeBullet")
        stack = document.add_paragraph(style="ResumeMeta")
        stack.add_run(f"{labels['stack']}: ").bold = True
        stack.add_run(", ".join(item["stack"]))

    document.add_paragraph(content["education"]["title"].upper(), style="ResumeSection")
    for item in content["education"]["items"]:
        paragraph = document.add_paragraph(style="ResumeBody")
        paragraph.add_run(item["institution"]).bold = True
        paragraph.add_run().add_break(WD_BREAK.LINE)
        paragraph.add_run(item["degree"])
        paragraph.add_run(f" | {item['period']}")

    document.add_paragraph(content["strengths"]["title"].upper(), style="ResumeSection")
    for card in content["strengths"]["cards"]:
        paragraph = document.add_paragraph(style="ResumeBody")
        paragraph.add_run(f"{card['title']}: ").bold = True
        paragraph.add_run(card["body"])

    document.add_paragraph(content["skills"]["title"].upper(), style="ResumeSection")
    for group in content["skills"]["groups"]:
        paragraph = document.add_paragraph(style="ResumeBody")
        paragraph.add_run(f"{group['title']}: ").bold = True
        paragraph.add_run(", ".join(group["items"]))

    document.add_paragraph(content["preferences"]["title"].upper(), style="ResumeSection")
    for item in content["preferences"]["items"]:
        document.add_paragraph(f"• {item}", style="ResumeBullet")

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


def pdf_link(text: str, url: str, color_hex: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}"><font color="#{color_hex}">{html.escape(text)}</font></a>'


def pdf_text(text: str) -> str:
    return html.escape(text).replace("\n", "<br/>")


def pdf_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(pdf_text(text), style)


def pdf_markup(markup: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(markup, style)


def build_pdf_styles(lang: str, download_style: dict[str, Any]) -> dict[str, ParagraphStyle]:
    style_colors = download_style["colors"]
    typography = download_style["typography"]
    regular, bold = register_pdf_fonts(lang)
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ResumeTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=typography["title"]["fontSize"],
            leading=typography["title"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['text']}"),
            spaceAfter=6,
            alignment=TA_CENTER,
        ),
        "contact": ParagraphStyle(
            "ResumeContact",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=typography["contact"]["fontSize"],
            leading=typography["contact"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['muted']}"),
            alignment=TA_CENTER,
            spaceAfter=7,
        ),
        "body": ParagraphStyle(
            "ResumeBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=typography["body"]["fontSize"],
            leading=typography["body"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['text']}"),
            spaceAfter=5,
        ),
        "section": ParagraphStyle(
            "ResumeSection",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=typography["section"]["fontSize"],
            leading=typography["section"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['accent']}"),
            spaceBefore=9,
            spaceAfter=5,
        ),
        "company": ParagraphStyle(
            "ResumeCompany",
            parent=base["Heading3"],
            fontName=bold,
            fontSize=typography["company"]["fontSize"],
            leading=typography["company"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['text']}"),
            spaceBefore=4,
            spaceAfter=2,
        ),
        "meta": ParagraphStyle(
            "ResumeMeta",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=typography["meta"]["fontSize"],
            leading=typography["meta"]["lineHeight"],
            textColor=colors.HexColor(f"#{style_colors['muted']}"),
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "ResumeBullet",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=typography["bullet"]["fontSize"],
            leading=typography["bullet"]["lineHeight"],
            leftIndent=11,
            firstLineIndent=-7,
            textColor=colors.HexColor(f"#{style_colors['text']}"),
            spaceAfter=2.2,
        ),
    }


def generate_pdf(data: dict[str, Any], lang: str, output_path: Path, build_dir: Path) -> None:
    content = data["content"][lang]
    labels = data["labels"][lang]
    download_style = data["downloadStyles"]
    style_colors = download_style["colors"]
    styles = build_pdf_styles(lang, download_style)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=0.62 * inch,
        leftMargin=0.62 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=content["meta"]["title"],
        author=content["hero"]["name"],
    )
    story: list[Any] = []

    story.append(Paragraph(html.escape(content["hero"]["name"]), styles["title"]))
    contact_links = [
        pdf_link(
            data["contacts"][key][lang]["label"],
            data["contacts"][key][lang]["url"],
            style_colors["accent"],
        )
        for key in data["contactKeys"]
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
        company_text = (
            pdf_link(item["company"], item["companyUrl"], style_colors["accent"])
            if item.get("companyUrl")
            else html.escape(item["company"])
        )
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
        story.append(pdf_markup(
            f"<b>{pdf_text(item['role'])}</b> | {pdf_text(item['period'])} | {pdf_text(item['location'])}",
            styles["meta"],
        ))
        story.append(pdf_paragraph(item["intro"], styles["body"]))
        story.append(pdf_markup(f"<b>{pdf_text(labels['achievements'])}:</b>", styles["body"]))
        for bullet in item["bullets"]:
            story.append(Paragraph(f"• {pdf_text(bullet)}", styles["bullet"]))
        story.append(pdf_markup(
            f"<b>{pdf_text(labels['stack'])}:</b> {pdf_text(', '.join(item['stack']))}",
            styles["meta"],
        ))

    story.append(Paragraph(html.escape(content["education"]["title"].upper()), styles["section"]))
    for item in content["education"]["items"]:
        story.append(pdf_markup(
            f"<b>{pdf_text(item['institution'])}</b><br/>{pdf_text(item['degree'])} | {pdf_text(item['period'])}",
            styles["body"],
        ))

    story.append(Paragraph(html.escape(content["strengths"]["title"].upper()), styles["section"]))
    for card in content["strengths"]["cards"]:
        story.append(pdf_markup(
            f"<b>{pdf_text(card['title'])}:</b> {pdf_text(card['body'])}",
            styles["body"],
        ))

    story.append(Paragraph(html.escape(content["skills"]["title"].upper()), styles["section"]))
    for group in content["skills"]["groups"]:
        story.append(pdf_markup(
            f"<b>{pdf_text(group['title'])}:</b> {pdf_text(', '.join(group['items']))}",
            styles["body"],
        ))

    story.append(Paragraph(html.escape(content["preferences"]["title"].upper()), styles["section"]))
    for item in content["preferences"]["items"]:
        story.append(Paragraph(f"• {pdf_text(item)}", styles["bullet"]))

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


def contact_site_payload(contact: dict[str, str]) -> dict[str, str]:
    return {
        key: contact[key]
        for key in ("url", "icon", "iconDark")
        if key in contact
    }


def write_site_data(data: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    default_language = data["meta"]["defaultLanguage"]
    site_contact_keys = data["content"][default_language]["hero"]["links"].keys()
    payload = {
        "contacts": {
            key: contact_site_payload(data["contacts"][key][default_language])
            for key in site_contact_keys
            if key in data["contacts"]
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
        file_names = data["content"][lang]["resume"]["downloadNames"]
        generate_txt(data, lang, lang_dir / file_names["txt"])
        generate_docx(data, lang, lang_dir / file_names["docx"], build_dir)
        generate_pdf(data, lang, lang_dir / file_names["pdf"], build_dir)


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
