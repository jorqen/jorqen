#!/usr/bin/env python3
"""Generate a styled Pandoc reference DOCX for resume exports."""

from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor

ACCENT = RGBColor(0x0B, 0x4F, 0x8A)
BODY = RGBColor(0x1F, 0x29, 0x37)


def configure_page_layout(doc: Document) -> None:
    section = doc.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.7)
    section.right_margin = Inches(0.7)


def style_normal(doc: Document) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.font.color.rgb = BODY

    paragraph = style.paragraph_format
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(5)
    paragraph.line_spacing = 1.15


def style_heading_1(doc: Document) -> None:
    style = doc.styles["Heading 1"]
    style.font.name = "Calibri"
    style.font.size = Pt(24)
    style.font.bold = True
    style.font.color.rgb = BODY

    paragraph = style.paragraph_format
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(8)
    paragraph.keep_with_next = True


def style_heading_2(doc: Document) -> None:
    style = doc.styles["Heading 2"]
    style.font.name = "Calibri"
    style.font.size = Pt(12.5)
    style.font.bold = True
    style.font.all_caps = True
    style.font.color.rgb = ACCENT

    paragraph = style.paragraph_format
    paragraph.space_before = Pt(14)
    paragraph.space_after = Pt(6)
    paragraph.keep_with_next = True


def style_heading_3(doc: Document) -> None:
    style = doc.styles["Heading 3"]
    style.font.name = "Calibri"
    style.font.size = Pt(11.5)
    style.font.bold = True
    style.font.color.rgb = BODY

    paragraph = style.paragraph_format
    paragraph.space_before = Pt(9)
    paragraph.space_after = Pt(4)
    paragraph.keep_with_next = True


def style_list_bullet(doc: Document) -> None:
    style = doc.styles["List Bullet"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.8)
    style.font.color.rgb = BODY

    paragraph = style.paragraph_format
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(2)
    paragraph.line_spacing = 1.1


def style_hyperlink(doc: Document) -> None:
    try:
        style = doc.styles["Hyperlink"]
    except KeyError:
        return

    style.font.color.rgb = ACCENT
    style.font.underline = True


def create_reference_docx(output_path: Path) -> None:
    document = Document()
    configure_page_layout(document)
    style_normal(document)
    style_heading_1(document)
    style_heading_2(document)
    style_heading_3(document)
    style_list_bullet(document)
    style_hyperlink(document)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        default="resume/templates/reference.docx",
        help="Output path for the generated reference docx",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    create_reference_docx(output_path)
    print(f"Generated DOCX reference template: {output_path}")


if __name__ == "__main__":
    main()
