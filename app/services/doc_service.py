"""Renders the SCL participant documents (markdown) to HTML and PDF.

Small, dependency-light markdown subset renderer for the four official
documents (rule book, vault, wagers, economy). Supports the constructs those
docs use: #/##/### headings, **bold**, `code`, bullet + numbered lists,
tables (| ... |), blockquotes (>), horizontal rules (---), and paragraphs.
"""

import html
import re
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Image, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

C_ACCENT = HexColor("#0B1E38")  # SCL navy
C_VOLT = HexColor("#A3FF00")    # SCL volt accent
C_WHITE = colors.white
C_BLACK = colors.black
C_GREY_LIGHT = HexColor("#F5F5F5")
C_GREY_MID = HexColor("#E0E0E0")

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm


def _ps(name, parent_name="Normal", **kw):
    base = getSampleStyleSheet()
    return ParagraphStyle(name, parent=base[parent_name], **kw)


# ---------------------------------------------------------------------------
# markdown -> token stream
# ---------------------------------------------------------------------------
def _inline(text):
    """Escape HTML, then apply **bold**, `code`, and _italic_ spans."""
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"_([^_]+)_", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def parse_blocks(md: str):
    """Yield ('h1'|'h2'|'h3'|'p'|'ul'|'ol'|'table'|'quote'|'hr', payload)."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped == "---":
            yield ("hr", None)
            i += 1
            continue
        m_img = re.match(r"^!\[([^]]*)\]\(([^)]+)\)\s*$", stripped)
        if m_img:
            yield ("img", {"alt": m_img.group(1), "src": m_img.group(2)})
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            yield (f"h{level}", _inline(m.group(2).strip()))
            i += 1
            continue
        if stripped.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                buf.append(lines[i].strip()[2:])
                i += 1
            yield ("quote", _inline(" ".join(buf)))
            continue
        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            # Drop the |---| separator row if present.
            if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1]):
                rows.pop(1)
            yield ("table", rows)
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*[-*]\s+", "", lines[i])))
                i += 1
            yield ("ul", items)
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+\.\s+", "", lines[i])))
                i += 1
            yield ("ol", items)
            continue
        # Paragraph: collect until a blank line or another block start.
        buf = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip():
            buf.append(lines[i].strip())
            i += 1
        yield ("p", _inline(" ".join(buf)))


# ---------------------------------------------------------------------------
# HTML renderer (for the website docs viewer)
# ---------------------------------------------------------------------------
def _table_html(rows):
    out = ['<div class="table-wrap"><table>']
    for r, cells in enumerate(rows):
        tag = "th" if r == 0 else "td"
        out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
    out.append("</table></div>")
    return "\n".join(out)


def md_to_html(md: str) -> str:
    parts = []
    for kind, payload in parse_blocks(md):
        if kind in ("h1", "h2", "h3"):
            parts.append(f"<h{kind[1]}>{payload}</h{kind[1]}>")
        elif kind == "p":
            parts.append(f"<p>{payload}</p>")
        elif kind == "ul":
            parts.append("<ul>" + "".join(f"<li>{it}</li>" for it in payload) + "</ul>")
        elif kind == "ol":
            parts.append("<ol>" + "".join(f"<li>{it}</li>" for it in payload) + "</ol>")
        elif kind == "table":
            parts.append(_table_html(payload))
        elif kind == "quote":
            parts.append(f"<blockquote>{payload}</blockquote>")
        elif kind == "img":
            parts.append(f'<figure class="doc-figure"><img src="{payload["src"]}" '
                         f'alt="{html.escape(payload["alt"]).strip()}" loading="lazy"></figure>')
        elif kind == "hr":
            parts.append("<hr>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PDF renderer (reportlab)
# ---------------------------------------------------------------------------
def _draw_running_header(canvas, document, title):
    """Slim running header on every page: navy title band + volt underline."""
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, h - HEADER_H, w, HEADER_H, fill=1, stroke=0)
    canvas.setFillColor(C_VOLT)
    canvas.rect(0, h - HEADER_H - 2 * mm, w, 2 * mm, fill=1, stroke=0)  # volt underline
    canvas.setFont("Helvetica-Bold", 10)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(MARGIN, h - HEADER_H + 3.5 * mm, title)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(C_GREY_LIGHT)
    canvas.drawRightString(w - MARGIN, h - HEADER_H + 3.5 * mm,
                           "Section-C Cricket League — Official Document")
    # Footer.
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, 0, w, 9 * mm, fill=1, stroke=0)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(MARGIN, 3 * mm, "Section-C Cricket League — Official Document")
    canvas.drawRightString(w - MARGIN, 3 * mm, f"Page {document.page}")
    canvas.restoreState()


LOGO_BG = HexColor("#F6F7EF")  # logo mark background (image is not transparent)


def _logo_mark_flowable():
    """The 16:9 SCL logo mark on its own background, first page only.

    The JPG is not transparent, so it sits on a full-width band of its own
    background color to blend in rather than floating on white.
    """
    if not LOGO_MARK_PATH.exists():
        return None
    try:
        img = Image(str(LOGO_MARK_PATH))
        iw, ih = img.imageWidth, img.imageHeight
        if not iw or not ih:
            return None
        img.drawWidth = LOGO_MARK_W
        img.drawHeight = LOGO_MARK_W * ih / iw  # preserve 16:9 aspect
        img.hAlign = "CENTER"
        band = Table([[img]], colWidths=[PAGE_W - 2 * MARGIN])
        band.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LOGO_BG),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return band
    except Exception:
        return None


def md_to_pdf(md: str, title: str, subtitle: str = "") -> bytes:
    def on_page(canvas, document):
        _draw_running_header(canvas, document, title)

    S_H1 = _ps("dh1", fontSize=17, fontName="Helvetica-Bold", leading=22,
               spaceAfter=6, textColor=C_ACCENT)
    S_H2 = _ps("dh2", fontSize=13, fontName="Helvetica-Bold", leading=17,
               spaceBefore=10, spaceAfter=4, textColor=C_BLACK)
    S_H3 = _ps("dh3", fontSize=11, fontName="Helvetica-Bold", leading=15,
               spaceBefore=8, spaceAfter=3, textColor=C_BLACK)
    S_P = _ps("dp", fontSize=9.5, leading=14, spaceAfter=5)
    S_Q = _ps("dq", fontSize=9.5, leading=14, spaceAfter=6, leftIndent=8,
              textColor=colors.darkgrey, fontName="Helvetica-Oblique")
    S_CELL = _ps("dc", fontSize=8.5, leading=11, spaceAfter=0)
    S_CELL_B = _ps("dcb", fontSize=8.5, leading=11, fontName="Helvetica-Bold",
                   spaceAfter=0)
    S_ITEM = _ps("di", fontSize=9.5, leading=13, spaceAfter=2)

    story = []
    mark = _logo_mark_flowable()
    if mark is not None:
        story.append(mark)
        story.append(Spacer(1, 10))
    if subtitle:
        story.append(Paragraph(_inline(subtitle), S_P))
    for kind, payload in parse_blocks(md):
        if kind == "h1":
            story.append(Paragraph(payload, S_H1))
        elif kind == "h2":
            story.append(Paragraph(payload, S_H2))
        elif kind == "h3":
            story.append(Paragraph(payload, S_H3))
        elif kind == "p":
            story.append(Paragraph(payload, S_P))
        elif kind == "ul":
            story.append(ListFlowable(
                [ListItem(Paragraph(it, S_ITEM), leftIndent=10) for it in payload],
                bulletType="bullet", start="•", leftIndent=14, spaceAfter=5))
        elif kind == "ol":
            story.append(ListFlowable(
                [ListItem(Paragraph(it, S_ITEM), leftIndent=10) for it in payload],
                bulletType="1", leftIndent=16, spaceAfter=5))
        elif kind == "quote":
            story.append(Paragraph(payload, S_Q))
        elif kind == "img":
            img = _load_image(payload["src"], payload["alt"])
            if img is not None:
                story.append(img)
                story.append(Spacer(1, 6))
        elif kind == "hr":
            story.append(HRFlowable(width="100%", thickness=1, color=C_GREY_MID,
                                    spaceBefore=4, spaceAfter=4))
        elif kind == "table":
            rows = payload
            data = [[Paragraph(_inline(c), S_CELL_B if r == 0 else S_CELL) for c in row]
                    for r, row in enumerate(rows)]
            tbl = Table(data, hAlign="LEFT", repeatRows=1)
            style = [
                ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("GRID", (0, 0), (-1, -1), 0.25, C_GREY_MID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
            for r in range(1, len(data)):
                if r % 2 == 0:
                    style.append(("BACKGROUND", (0, r), (-1, r), C_GREY_LIGHT))
            tbl.setStyle(TableStyle(style))
            story.append(Spacer(1, 3))
            story.append(tbl)
            story.append(Spacer(1, 6))

    out = BytesIO()
    SimpleDocTemplate(
        out, pagesize=A4, topMargin=(HEADER_H + 8 * mm), bottomMargin=13 * mm,
        leftMargin=MARGIN, rightMargin=MARGIN,
    ).build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out.getvalue()


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGO_MARK_PATH = PROJECT_ROOT / "data" / "brandings" / "scl" / "logo-mark-16-9.JPG"
LOGO_MARK_W = 85 * mm
HEADER_H = 12 * mm


def _load_image(src: str, alt: str):
    """Resolve an image src to a local file for the PDF, or None.

    Supports ``/branding/<relpath>`` URLs (served by the app) and plain
    filesystem paths relative to the project root.
    """
    try:
        if src.startswith("/branding/"):
            path = PROJECT_ROOT / "data" / "brandings" / src[len("/branding/"):]
        else:
            path = PROJECT_ROOT / src.lstrip("/")
        if not path.exists():
            return None
        img = Image(str(path))
        iw, ih = img.imageWidth, img.imageHeight
        if not iw or not ih:
            return None
        # Scale to fit the text width (A4 minus margins), keep aspect ratio.
        max_w = PAGE_W - 2 * MARGIN
        max_h = 60 * mm
        scale = min(1.0, max_w / iw, max_h / ih)
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        img.hAlign = "CENTER"
        return img
    except Exception:
        return None


# ---------------------------------------------------------------------------
# document registry
# ---------------------------------------------------------------------------
DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"

DOCS = [
    {
        "slug": "rulebook",
        "title": "SCL Season 3 — Official Rule Book",
        "summary": "The binding rules: format, standings, draft, teams, discipline, fines.",
        "file": "SCL_RULEBOOK.md",
    },
    {
        "slug": "vault",
        "title": "The Vault — S3 Guide",
        "summary": "7% per-match yield, the Iron Lock, compounding vs manual harvest, auto mode.",
        "file": "SCL_VAULT_GUIDE.md",
    },
    {
        "slug": "wagers",
        "title": "Wager & Risk Management — S3 Guide",
        "summary": "Pooled Yes/No markets, calibration, the automatic House guarantee, integrity.",
        "file": "SCL_WAGERS_GUIDE.md",
    },
    {
        "slug": "economy",
        "title": "The SCL Economy — S3 Guide",
        "summary": "Wallets, carry-over funding, match credits, the squad levy, fines.",
        "file": "SCL_ECONOMY_GUIDE.md",
    },
]


def doc_path(slug: str):
    for d in DOCS:
        if d["slug"] == slug:
            return DOCS_ROOT / d["file"]
    return None


def read_doc(slug: str):
    path = doc_path(slug)
    if not path or not path.exists():
        return None
    return path.read_text(encoding="utf-8")
