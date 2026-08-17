"""Auction results PDF — full draft details, exported from the DB state.

Rendered with the same SCL branding as the scorecard (navy/volt, 16:9 logo
mark on the first page, running header/footer). Sections:

- phase hero + season name
- auction performance table (team / manager / squad / spend / avg / credits / wallet)
- final squads with team logos
- all players with sold status, team and price
- bid feed
"""

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

C_ACCENT = HexColor("#0B1E38")   # SCL navy
C_VOLT = HexColor("#A3FF00")     # SCL volt accent
C_WHITE = colors.white
C_BLACK = colors.black
C_GREY_LIGHT = HexColor("#F5F5F5")
C_GREY_MID = HexColor("#E0E0E0")
C_BAND_BG = HexColor("#F6F7EF")  # matches the logo-mark background

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
LOGO_MARK_PATH = Path(__file__).resolve().parents[2] / "data" / "brandings" / "scl" / "logo-mark-16-9.JPG"


def _ps(name, parent_name="Normal", **kw):
    return ParagraphStyle(name, parent=getSampleStyleSheet()[parent_name], **kw)


S_CELL_L = _ps("cl", fontSize=8, fontName="Helvetica", alignment=TA_LEFT, textColor=C_BLACK)
S_CELL_C = _ps("cc", fontSize=8, fontName="Helvetica", alignment=TA_CENTER, textColor=C_BLACK)
S_CELL_B = _ps("cb", fontSize=8, fontName="Helvetica-Bold", alignment=TA_LEFT, textColor=C_BLACK)
S_CELL_BC = _ps("cbc", fontSize=8, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=C_BLACK)
S_MUTED = _ps("mu", fontSize=7.5, fontName="Helvetica-Oblique", textColor=colors.darkgrey, spaceAfter=2)


def _hdr_row(cols):
    return [Paragraph(f"<b>{c}</b>", _ps(f"h{i}", fontSize=8, fontName="Helvetica-Bold",
                                          alignment=TA_CENTER, textColor=C_WHITE))
            for i, c in enumerate(cols)]


def _alt_rows(data, start=1, odd=C_WHITE, even=C_GREY_LIGHT):
    cmds = []
    for i in range(start, len(data)):
        bg = odd if i % 2 != 0 else even
        cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
    return cmds


BASE_TBL_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), C_ACCENT),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("GRID", (0, 0), (-1, -1), 0.25, C_GREY_MID),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
]


def _subheader(text):
    tbl = Table([[Paragraph(f"  <b>{text}</b>",
                            _ps("sh", fontSize=9, fontName="Helvetica-Bold", textColor=C_WHITE))]],
                colWidths=[PAGE_W - 2 * MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def _team_logo_flowable(team, size=11 * mm):
    """Small team logo (fallback: SCL mark). Returns a Paragraph if missing."""
    path = (team or {}).get("logo_path") or (team or {}).get("logo_file")
    if path and Path(path).is_file():
        try:
            img = Image(str(path))
            iw, ih = img.imageWidth, img.imageHeight
            if iw and ih:
                img.drawWidth = size
                img.drawHeight = size * ih / iw
                img.hAlign = "CENTER"
                return img
        except Exception:
            pass
    if LOGO_MARK_PATH.is_file():
        try:
            img = Image(str(LOGO_MARK_PATH))
            iw, ih = img.imageWidth, img.imageHeight
            if iw and ih:
                img.drawWidth = size
                img.drawHeight = size * ih / iw
                img.hAlign = "CENTER"
                return img
        except Exception:
            pass
    return Paragraph("", S_CELL_L)


class AuctionPdfService:
    """Builds the auction results PDF from auction_service.get_state()."""

    def build(self, state: dict) -> bytes:
        season = state.get("season") or {}
        teams = state.get("teams") or []
        players = state.get("players") or []
        bids = state.get("bids") or []
        ruleset = state.get("ruleset") or {}
        phase = state.get("phase") or ""
        total_credits = int(ruleset.get("total_credits") or 8)

        title = season.get("name") or "Auction"
        phase_line = phase.upper() if phase else "SETUP"

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4, topMargin=24 * mm, bottomMargin=13 * mm,
            leftMargin=MARGIN, rightMargin=MARGIN,
        )

        def on_page(canvas, doc):
            canvas.saveState()
            w, h = A4
            HEADER_H = 12 * mm
            canvas.setFillColor(C_ACCENT)
            canvas.rect(0, h - HEADER_H, w, HEADER_H, fill=1, stroke=0)
            canvas.setFillColor(C_VOLT)
            canvas.rect(0, h - HEADER_H - 2 * mm, w, 2 * mm, fill=1, stroke=0)
            canvas.setFont("Helvetica-Bold", 10)
            canvas.setFillColor(C_WHITE)
            canvas.drawString(MARGIN, h - HEADER_H + 3.5 * mm, "SCL — AUCTION RESULTS")
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(C_GREY_LIGHT)
            canvas.drawRightString(w - MARGIN, h - HEADER_H + 3.5 * mm, title)
            canvas.setFillColor(C_ACCENT)
            canvas.rect(0, 0, w, 9 * mm, fill=1, stroke=0)
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(C_WHITE)
            canvas.drawString(MARGIN, 3 * mm, "Auto-generated by scl Scoring Engine")
            canvas.drawRightString(w - MARGIN, 3 * mm, f"Page {doc.page}")
            canvas.restoreState()

        story = []
        # Letterhead logo mark (first page only), on its matching background.
        try:
            if LOGO_MARK_PATH.is_file():
                mark = Image(str(LOGO_MARK_PATH))
                iw, ih = mark.imageWidth, mark.imageHeight
                if iw and ih:
                    mark.drawWidth = 80 * mm
                    mark.drawHeight = 80 * mm * ih / iw
                    mark.hAlign = "CENTER"
                    band = Table([[mark]], colWidths=[PAGE_W - 2 * MARGIN])
                    band.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), C_BAND_BG),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]))
                    story.append(band)
                    story.append(Spacer(1, 6 * mm))
        except Exception:
            pass

        hero = Table([[Paragraph(
            f"<b>{title} — {phase_line}</b>",
            _ps("hero", fontSize=14, fontName="Helvetica-Bold",
                textColor=C_WHITE, alignment=TA_CENTER)
        )]], colWidths=[PAGE_W - 2 * MARGIN])
        hero.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(hero)
        story.append(Spacer(1, 5 * mm))

        # --- auction performance ---
        if teams:
            story.append(_subheader("AUCTION PERFORMANCE — SQUAD SPEND VS WALLET"))
            story.append(Spacer(1, 3 * mm))
            cols = ["Team", "Manager", "Squad", "Spend", "Avg", "Credits used", "Credits left", "Wallet left"]
            bw = PAGE_W - 2 * MARGIN
            cw = [0.20 * bw, 0.14 * bw, 0.09 * bw, 0.10 * bw, 0.08 * bw, 0.13 * bw, 0.13 * bw, 0.13 * bw]
            data = [_hdr_row(cols)]
            for t in sorted(teams, key=lambda x: x.get("spent") or 0, reverse=True):
                squad = f"{len(t.get('players') or [])} + {len(t.get('bench') or [])}"
                n_players = len(t.get("players") or [])
                avg = int((t.get("spent") or 0) / n_players) if n_players else "—"
                data.append([
                    Paragraph(f"<b>{t.get('name') or '?'}</b>{'' if t.get('is_active') else ' (inactive)'}", S_CELL_B),
                    Paragraph(str(t.get("manager_name") or "—"), S_CELL_L),
                    Paragraph(squad, S_CELL_C),
                    Paragraph(f"<b>{t.get('spent') or 0}</b>", S_CELL_BC),
                    Paragraph(str(avg), S_CELL_C),
                    Paragraph(str(total_credits - int(t.get("credits_remaining") or 0)), S_CELL_C),
                    Paragraph(str(t.get("credits_remaining") or 0), S_CELL_C),
                    Paragraph(str(t.get("wallet") or 0), S_CELL_C),
                ])
            tbl = Table(data, colWidths=cw, repeatRows=1)
            tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(data)))
            story.append(tbl)
            story.append(Spacer(1, 5 * mm))

        # --- squads with logos ---
        if teams:
            story.append(_subheader("FINAL SQUADS"))
            story.append(Spacer(1, 3 * mm))
            for t in sorted(teams, key=lambda x: (x.get("name") or "").lower()):
                row = [_team_logo_flowable(t)]
                labels = "  ".join((t.get("player_labels") or []) + (t.get("bench_labels") or [])) or "—"
                body = Table([[
                    Paragraph(f"<b>{t.get('name') or '?'}</b>"
                              f"  <font size='7' color='#6b7280'>Mgr: {t.get('manager_name') or '—'}"
                              f" · Spent {t.get('spent') or 0} · Wallet {t.get('wallet') or 0}</font>", S_CELL_B),
                    Paragraph(labels, S_CELL_L),
                ]], colWidths=[0.34 * bw, 0.66 * bw])
                body.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, C_GREY_MID),
                ]))
                story.append(Table([[row[0], body]], colWidths=[16 * mm, bw - 16 * mm],
                                   style=TableStyle([
                                       ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                       ("LEFTPADDING", (0, 0), (0, 0), 0),
                                   ])))
                story.append(Spacer(1, 2 * mm))
            story.append(Spacer(1, 3 * mm))

        # --- all players ---
        if players:
            story.append(_subheader(f"ALL PLAYERS ({len(players)})"))
            story.append(Spacer(1, 3 * mm))
            cols = ["Player", "Tier", "Status", "Team", "Price"]
            cw = [0.28 * bw, 0.12 * bw, 0.14 * bw, 0.30 * bw, 0.16 * bw]
            data = [_hdr_row(cols)]
            for p in players:
                sold = p.get("status") == "sold"
                data.append([
                    Paragraph(f"<b>{p.get('name') or '?'}</b>", S_CELL_B),
                    Paragraph(str(p.get("tier") or "—"), S_CELL_C),
                    Paragraph(str(p.get("status") or "—"), S_CELL_C),
                    Paragraph(str(p.get("sold_to_team_name") or "—"), S_CELL_L),
                    Paragraph(str(p.get("sold_price") or 0) if sold else "—", S_CELL_C),
                ])
            tbl = Table(data, colWidths=cw, repeatRows=1)
            tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(data)))
            story.append(tbl)
            story.append(Spacer(1, 5 * mm))

        # --- bid feed ---
        if bids:
            story.append(_subheader("BID FEED"))
            story.append(Spacer(1, 3 * mm))
            cols = ["Team", "Player", "Bid", "Time"]
            cw = [0.28 * bw, 0.30 * bw, 0.22 * bw, 0.20 * bw]
            data = [_hdr_row(cols)]
            for b in bids[:60]:
                label = "pass" if b.get("kind") == "pass" else str(b.get("amount"))
                data.append([
                    Paragraph(f"<b>{b.get('team_name') or '—'}</b>", S_CELL_B),
                    Paragraph(str(b.get("player_name") or "—"), S_CELL_L),
                    Paragraph(label, S_CELL_C),
                    Paragraph(str(b.get("ts_display") or ""), S_CELL_C),
                ])
            tbl = Table(data, colWidths=cw, repeatRows=1)
            tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(data)))
            story.append(tbl)
            story.append(Spacer(1, 5 * mm))

        story.append(HRFlowable(width="100%", thickness=1.5, color=C_ACCENT, spaceAfter=5))
        story.append(Paragraph(
            "This auction summary was auto-generated from the draft state.",
            _ps("foot", fontSize=7, textColor=colors.darkgrey,
                fontName="Helvetica-Oblique", alignment=TA_CENTER)))

        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue()
