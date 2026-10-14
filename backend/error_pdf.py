"""Render the rejected-record UI table as a downloadable PDF."""
from io import BytesIO
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, TableStyle, Spacer


def error_details_pdf(csv_path, dataset_name: str) -> bytes:
    frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    buffer = BytesIO()
    page_width, page_height = landscape(A4)
    document = SimpleDocTemplate(buffer, pagesize=(page_width, page_height), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=30, title=f"{dataset_name} - Rejected-record details")
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#30251f"), wordWrap="CJK")
    muted = ParagraphStyle("muted", parent=body, textColor=colors.HexColor("#78685d"))
    heading = ParagraphStyle("heading", parent=body, fontSize=14, leading=19)
    status = ParagraphStyle("status", parent=body, textColor=colors.HexColor("#a54238"), backColor=colors.HexColor("#f9e9e6"), borderPadding=5)
    def paragraph(value, style=body):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)
    headers = ["Status", "Rule / row", "Column", "Reason", "Observed value"]
    rows = [[paragraph(header.upper(), muted) for header in headers]]
    for record in frame.to_dict("records"):
        rows.append([paragraph(record.get(header, ""), status if header == "Status" else muted if header == "Reason" else body) for header in headers])
    if len(rows) == 1:
        rows.append([paragraph("No rejected-record issues."), "", "", "", ""])
    width = page_width - 48
    table = LongTable(rows, colWidths=[width*n for n in (.10,.19,.16,.36,.19)], repeatRows=1, splitInRow=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#fff7ed")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
        ("TOPPADDING",(0,0),(-1,-1),12), ("BOTTOMPADDING",(0,0),(-1,-1),12),
        ("LINEBELOW",(0,0),(-1,-1),.5,colors.HexColor("#f3e8df")),
        ("BOX",(0,0),(-1,-1),.5,colors.HexColor("#e7e2dd")),
    ]))
    def page_number(canvas, doc):
        canvas.setFont("Helvetica",8)
        canvas.setFillColor(colors.HexColor("#78685d"))
        canvas.drawRightString(page_width-24,15,f"Page {doc.page}")
    document.build([paragraph(f"Rejected-record details ({len(frame):,} issues)",heading), Spacer(1,6),paragraph(dataset_name,muted),Spacer(1,14),table],onFirstPage=page_number,onLaterPages=page_number)
    return buffer.getvalue()
