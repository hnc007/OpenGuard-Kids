"""Render the six network measurements, with their captured image, for the submission PDF."""
import argparse
import csv
import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


DOCS = Path(__file__).resolve().parents[1] / "docs"
EVIDENCE = DOCS / "evidence" / "network-2026-11-23"
TEAL = colors.HexColor("#165D5B")
INK = colors.HexColor("#152F3A")
WIDTH = A4[0] - 88


def paragraph(text, *, size=10, leading=14, bold=False, color=INK):
    style = ParagraphStyle("text", fontName="UIBold" if bold else "UI", fontSize=size,
                           leading=leading, textColor=color, spaceAfter=0)
    return Paragraph(escape(text), style)


def clean(text):
    # Reproduction links and long raw paths remain in the companion Markdown report.
    text = re.split(r"\s+\[[^\]]+\]\(", text, maxsplit=1)[0]
    return text.replace("`", "").replace("*", "").strip()


def table(rows, fractions):
    cells = [[paragraph(clean(cell), size=8.6, leading=11.6, bold=i == 0,
                        color=colors.white if i == 0 else INK) for cell in row]
             for i, row in enumerate(rows)]
    item = Table(cells, colWidths=[WIDTH * f for f in fractions], repeatRows=1)
    item.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F2F7F6"), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#C6D8D7")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return item


def footer(canvas, doc):
    width, height = A4
    canvas.saveState()
    canvas.setFillColor(TEAL)
    canvas.setFont("UIBold", 9)
    canvas.drawString(44, height - 34, "OPENGUARD KIDS")
    canvas.setFont("UI", 9)
    canvas.drawRightString(width - 44, height - 34, "Kỹ thuật hệ thống và mạng   /   23.11.2026")
    canvas.setStrokeColor(colors.HexColor("#C6D8D7"))
    canvas.line(44, height - 42, width - 44, height - 42)
    canvas.line(44, 41, width - 44, 41)
    canvas.drawString(44, 26, "Phương pháp, tệp đo và cách tái hiện: docs/NETWORK_EVIDENCE.md")
    canvas.drawRightString(width - 44, 26, str(doc.page))
    canvas.restoreState()


def render(out, fonts):
    pdfmetrics.registerFont(TTFont("UI", str(fonts / "segoeui.ttf")))
    pdfmetrics.registerFont(TTFont("UIBold", str(fonts / "segoeuib.ttf")))
    raw = (DOCS / "NETWORK_EVIDENCE.md").read_text(encoding="utf-8")
    sections = re.findall(r"^## (M[1-6]) · (.+)\n([\s\S]*?)(?=^## |\Z)", raw, re.M)
    if len(sections) != 6:
        raise ValueError("Cần đủ M1–M6 trong phụ lục thực nghiệm.")
    widths = {"M1": [.15, .85], "M2": [.14, .185, .18, .18, .155, .16],
              "M3": [.19, .42, .39], "M4": [.13, .18, .25, .20, .24], "M5": [.17, .18, .455, .195]}
    story = []
    for code, title, body in sections:
        if story:
            story.append(PageBreak())
        story.extend([paragraph(code, size=13, leading=18, bold=True, color=TEAL),
                      paragraph(title, size=24, leading=30, bold=True), Spacer(1, 14)])
        for block in body.strip().split("\n\n"):
            if block.startswith("```mermaid"):
                story.append(paragraph("Trình duyệt → Bộ phân giải Windows → DNS proxy → Quy tắc của hồ sơ",
                                       bold=True, color=TEAL))
                story.append(paragraph("Khi chặn, proxy trả NXDOMAIN qua bộ phân giải về trình duyệt. Khi cho phép, "
                                       "proxy tra cache hoặc hỏi máy chủ DNS thượng nguồn."))
            elif block.startswith("!["):
                picture = Image(str(EVIDENCE / "wireshark-offline.png"))
                picture._restrictSize(WIDTH, 220)
                story.append(picture)
            elif block.startswith("|"):
                rows = [line.strip("| ").split("|") for line in block.splitlines()
                        if not re.match(r"^\|[\s|:-]+$", line)]
                story.append(table(rows, widths[code]))
            elif block.strip():
                story.append(paragraph(clean(block.replace("\n", " "))))
            story.append(Spacer(1, 10))
        if code == "M6":
            with (EVIDENCE / "clock.csv").open(encoding="utf-8-sig") as handle:
                rows = [["Độ lệch (giây)", "Cảnh báo đã lưu", "Cảnh báo lặp sau restart", "Giây đã tính"]]
                for row in csv.DictReader(handle):
                    rows.append([row["wall_offset_s"], row["durable_alerts"],
                                 "Có" if row["duplicate_after_restart"] == "True" else "Không", row["billed_s"]])
            story.append(table(rows, [.2, .25, .35, .2]))
    pdf = io.BytesIO()
    doc = SimpleDocTemplate(pdf, pagesize=A4, leftMargin=38, rightMargin=38, topMargin=53,
                            bottomMargin=51, title="OpenGuard Kids · Phụ lục kỹ thuật M1–M6")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    with out.open("xb") as target:
        target.write(pdf.getvalue())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fonts", type=Path, default=Path("C:/Windows/Fonts"))
    args = parser.parse_args()
    render(args.output, args.fonts)
