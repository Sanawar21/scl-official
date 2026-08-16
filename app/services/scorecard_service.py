"""Official scorecard PDF, generated from the DB (imported match data).

Ported from the reference app's `scoreCard.py`, but fed by `match_summary` +
`match_stats.delivery_log` + the season finance ledger instead of re-parsing a
CSV on disk — the imported match is the single source of truth.

- batting/bowling tables come from `match_summary` team sections (batsmen in
  call-up order via `batter_order`);
- Fall of Wickets comes from the stored ball-by-ball `delivery_log`;
- the revenue section lists the real `season_finance_entries` for the match
  (rewards, adjustments, transfers) instead of hardcoded numbers;
- fantasy points were already computed at import (DB tiers), no duplicated math.
"""

from collections import defaultdict
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

C_ACCENT = HexColor("#0B1E38")   # SCL navy
C_VOLT = HexColor("#A3FF00")     # SCL volt accent
C_WHITE = colors.white
C_BLACK = colors.black
C_GREY_LIGHT = HexColor("#F5F5F5")
C_GREY_MID = HexColor("#E0E0E0")

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm


def _ps(name, parent_name="Normal", **kw):
    base = getSampleStyleSheet()
    return ParagraphStyle(name, parent=base[parent_name], **kw)


S_CELL_L = _ps("cl", fontSize=8, fontName="Helvetica", alignment=TA_LEFT, textColor=C_BLACK)
S_CELL_C = _ps("cc", fontSize=8, fontName="Helvetica", alignment=TA_CENTER, textColor=C_BLACK)
S_CELL_B = _ps("cb", fontSize=8, fontName="Helvetica-Bold", alignment=TA_LEFT, textColor=C_BLACK)
S_CELL_BC = _ps("cbc", fontSize=8, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=C_BLACK)
S_FOW = _ps("fow", fontSize=7.5, fontName="Helvetica-Oblique", textColor=colors.darkgrey, spaceAfter=2)


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


def _team_header_bar(team_name, total_str):
    data = [[
        Paragraph(f"<b>{team_name.upper()}</b>",
                  _ps("tn", fontSize=14, fontName="Helvetica-Bold", textColor=C_WHITE)),
        Paragraph(f"<b>{total_str}</b>",
                  _ps("ts", fontSize=13, fontName="Helvetica-Bold",
                      textColor=C_WHITE, alignment=TA_RIGHT)),
    ]]
    tbl = Table(data, colWidths=[PAGE_W - 2 * MARGIN - 60 * mm, 60 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tbl


def _ov_str(valid_balls):
    return f"{int(valid_balls or 0) // 6}.{int(valid_balls or 0) % 6}"


def _econ_str(runs, valid_balls):
    if not valid_balls:
        return "—"
    return f"{float(runs or 0) / (float(valid_balls) / 6):.2f}"


def _fall_of_wickets(delivery_log):
    """{batting team name: [\"prog-wkt (name, over.ball)\", ...]}."""
    fow = defaultdict(list)
    wkt = defaultdict(int)
    for row in delivery_log or []:
        dismissed = str(row.get("Dismissed Batter") or "").strip()
        if not dismissed or dismissed == "None":
            continue
        team = str(row.get("Batting Team") or "").strip() or "?"
        wkt[team] += 1
        fow[team].append(
            f"{row.get('Progressive Runs')}-{wkt[team]} "
            f"({dismissed}, {row.get('Over Number')}.{row.get('Ball Number')})")
    return dict(fow)


def _team_section(section, fow):
    elements = []
    team = section["team"]
    wides = int(team.get("wides_faced") or 0)
    noballs = int(team.get("noballs_faced") or 0)
    extras = wides + noballs

    elements.append(_team_header_bar(section["team_name"], section["total"]))
    elements.append(Spacer(1, 2 * mm))

    # batting (already in call-up order from match_summary)
    elements.append(_subheader("BATTING"))
    bat_cols = ["#", "Batter", "Status", "R", "B", "4s", "6s", "SR"]
    bat_cw = [8 * mm, 42 * mm, PAGE_W - 2 * MARGIN - 8 * mm - 42 * mm - 10 * mm - 10 * mm - 8 * mm - 8 * mm - 16 * mm,
              10 * mm, 10 * mm, 8 * mm, 8 * mm, 16 * mm]
    bat_data = [_hdr_row(bat_cols)]
    for b in section["batting"]:
        bat_data.append([
            Paragraph(f"<b>{b.get('batter_order') if b.get('batter_order') is not None else '—'}</b>", S_CELL_BC),
            Paragraph(f"<b>{b['player_name']}</b>", S_CELL_B),
            Paragraph(b.get("status") or "not out", S_CELL_L),
            Paragraph(str(b.get("runs") or 0), S_CELL_BC),
            Paragraph(str(b.get("balls_faced") or 0), S_CELL_C),
            Paragraph(str(b.get("fours") or 0), S_CELL_C),
            Paragraph(str(b.get("sixes") or 0), S_CELL_C),
            Paragraph(b.get("sr_display") or "—", S_CELL_C),
        ])
    bat_data.append([
        Paragraph("Extras", S_CELL_L),
        Paragraph(f"wides {wides}  nb {noballs}", S_CELL_L),
        Paragraph(str(extras), S_CELL_BC),
        Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C),
        Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C),
    ])
    bat_data.append([
        Paragraph("<b>TOTAL</b>", S_CELL_B),
        Paragraph(f"{team.get('overs_faced') or _ov_str(team.get('balls_faced'))} overs", S_CELL_L),
        Paragraph(f"<b>{team.get('runs_scored') or 0}</b>", S_CELL_BC),
        Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C),
        Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C), Paragraph("—", S_CELL_C),
    ])
    bat_tbl = Table(bat_data, colWidths=bat_cw, repeatRows=1)
    style = BASE_TBL_STYLE[:] + _alt_rows(bat_data)
    style += [
        ("BACKGROUND", (0, len(bat_data) - 1), (-1, len(bat_data) - 1), C_GREY_LIGHT),
        ("FONTNAME", (0, len(bat_data) - 1), (-1, len(bat_data) - 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, len(bat_data) - 2), (-1, len(bat_data) - 2), C_WHITE),
    ]
    bat_tbl.setStyle(TableStyle(style))
    elements.append(bat_tbl)

    # fall of wickets
    fow_list = fow.get(section["team_name"], [])
    if fow_list:
        elements.append(Paragraph("Fall of Wickets:  " + "   |   ".join(fow_list), S_FOW))
    else:
        elements.append(Paragraph("Fall of Wickets: No wickets fell", S_FOW))
    elements.append(Spacer(1, 2 * mm))

    # bowling (opposition bowlers)
    elements.append(_subheader("BOWLING"))
    bowl_cols = ["Bowler", "O", "M", "R", "W", "Econ", "WD", "NB"]
    bw = PAGE_W - 2 * MARGIN
    bowl_cw = [35 * mm] + [(bw - 35 * mm) / 7] * 7
    bowl_data = [_hdr_row(bowl_cols)]
    for b in section["bowling"]:
        bowl_data.append([
            Paragraph(f"<b>{b['player_name']}</b>", S_CELL_B),
            Paragraph(b.get("overs_display") or "0.0", S_CELL_C),
            Paragraph("0", S_CELL_C),
            Paragraph(str(b.get("runs_conceded") or 0), S_CELL_C),
            Paragraph(f"<b>{b.get('wickets') or 0}</b>", S_CELL_BC),
            Paragraph(b.get("econ_display") or "—", S_CELL_C),
            Paragraph(str(b.get("wides") or 0), S_CELL_C),
            Paragraph(str(b.get("noballs") or 0), S_CELL_C),
        ])
    bowl_tbl = Table(bowl_data, colWidths=bowl_cw, repeatRows=1)
    bowl_tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(bowl_data)))
    elements.append(bowl_tbl)
    elements.append(Spacer(1, 5 * mm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=C_ACCENT, spaceAfter=5))
    return elements


def _fantasy_section(leaderboard):
    elements = [_subheader("FANTASY POINTS LEADERBOARD")]
    fan_cols = ["Player", "Team", "Role", "Points"]
    bw = PAGE_W - 2 * MARGIN
    fan_cw = [0.34 * bw, 0.28 * bw, 0.22 * bw, 0.16 * bw]
    fan_data = [_hdr_row(fan_cols)]
    for p in leaderboard or []:
        points = int(p.get("fantasy_score") or 0)
        if points >= 40:
            rating = "Elite"
        elif points >= 20:
            rating = "Strong"
        elif points >= 10:
            rating = "Average"
        else:
            rating = "Below Avg"
        fan_data.append([
            Paragraph(f"<b>{p.get('player_name') or '?'}</b>", S_CELL_B),
            Paragraph(p.get("team_name") or "", S_CELL_C),
            Paragraph(str(p.get("role") or "").replace("_", " "), S_CELL_C),
            Paragraph(f"<b>{points}</b> <font size='6' color='#6b7280'>({rating})</font>", S_CELL_BC),
        ])
    fan_tbl = Table(fan_data, colWidths=fan_cw, repeatRows=1)
    fan_tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(fan_data)))
    elements.append(fan_tbl)
    elements.append(Spacer(1, 5 * mm))
    return elements


def _revenue_section(entries):
    if not entries:
        return []
    elements = [Spacer(1, 3 * mm), _subheader("MATCH REVENUE & FINANCIAL SUMMARY"), Spacer(1, 3 * mm)]
    rev_cols = ["Team / Party", "Description", "Amount (PKR)"]
    bw = PAGE_W - 2 * MARGIN
    rev_cw = [0.30 * bw, 0.46 * bw, 0.24 * bw]
    rev_data = [_hdr_row(rev_cols)]
    for entry in entries:
        amount = int(entry.get("amount") or 0)
        sign = "−" if (entry.get("operation") == "remove" or amount < 0) else "+"
        party = entry.get("team_name") or entry.get("label") or entry.get("summary") or "—"
        desc = entry.get("comment") or entry.get("summary") or entry.get("type") or ""
        rev_data.append([
            Paragraph(f"<b>{party}</b>", S_CELL_B),
            Paragraph(str(desc), S_CELL_L),
            Paragraph(f"<b>{sign}{abs(amount)}</b>", S_CELL_BC),
        ])
    rev_tbl = Table(rev_data, colWidths=rev_cw, repeatRows=1)
    rev_tbl.setStyle(TableStyle(BASE_TBL_STYLE[:] + _alt_rows(rev_data)))
    elements.append(rev_tbl)
    elements.append(Spacer(1, 2 * mm))
    return elements


class ScorecardService:
    """Builds the official scorecard PDF from match data already in the DB."""

    def build(self, summary: dict, finance_entries: list = None) -> bytes:
        """summary: the dict from ScorerService.match_summary()."""
        match_line = (
            f"{summary.get('between') or summary.get('result') or ''}  |  "
            f"Match ID: {summary.get('match_id') or ''}  |  "
            f"Venue: {summary.get('venue') or '—'}  |  "
            f"Result: {summary.get('result') or '—'}"
        )
        header = {"match_line": match_line}

        BANNER_PATH = Path(__file__).resolve().parents[2] / "data" / "brandings" / "scl" / "wide-banner.JPG"
        BANNER_H = 24 * mm

        def on_page(canvas, doc):
            canvas.saveState()
            w, h = A4
            # SCL banner letterhead (falls back to a navy band if missing).
            try:
                if BANNER_PATH.exists():
                    canvas.drawImage(str(BANNER_PATH), 0, h - BANNER_H, w, BANNER_H,
                                     preserveAspectRatio=False, mask="auto")
                else:
                    canvas.setFillColor(C_ACCENT)
                    canvas.rect(0, h - BANNER_H, w, BANNER_H, fill=1, stroke=0)
            except Exception:
                canvas.setFillColor(C_ACCENT)
                canvas.rect(0, h - BANNER_H, w, BANNER_H, fill=1, stroke=0)
            canvas.setFillColor(C_VOLT)
            canvas.rect(0, h - BANNER_H - 2.5 * mm, w, 2.5 * mm, fill=1, stroke=0)  # volt underline
            # Title band under the banner.
            band_h = 13 * mm
            band_top = h - BANNER_H - 2.5 * mm - band_h
            canvas.setFillColor(C_ACCENT)
            canvas.rect(0, band_top, w, band_h, fill=1, stroke=0)
            canvas.setFont("Helvetica-Bold", 15)
            canvas.setFillColor(C_WHITE)
            canvas.drawCentredString(w / 2, band_top + 4 * mm, "SCL  —  OFFICIAL SCORECARD")
            canvas.setFont("Helvetica", 7.5)
            canvas.setFillColor(C_GREY_LIGHT)
            canvas.drawCentredString(w / 2, band_top - 0.5 * mm, header["match_line"])
            canvas.setFillColor(C_ACCENT)
            canvas.rect(0, 0, w, 9 * mm, fill=1, stroke=0)
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(C_WHITE)
            canvas.drawString(MARGIN, 3 * mm, "Auto-generated by scl Scoring Engine")
            canvas.drawRightString(w - MARGIN, 3 * mm, f"Page {doc.page}")
            canvas.restoreState()

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            topMargin=(BANNER_H + 3 * mm + 17 * mm), bottomMargin=13 * mm,
            leftMargin=MARGIN, rightMargin=MARGIN,
        )
        story = []
        result_tbl = Table([[Paragraph(
            f"<b>Result: {summary.get('result') or '—'}</b>",
            _ps("res", fontSize=11, fontName="Helvetica-Bold",
                textColor=C_WHITE, alignment=TA_CENTER)
        )]], colWidths=[PAGE_W - 2 * MARGIN])
        result_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(result_tbl)
        story.append(Spacer(1, 4 * mm))

        if summary.get("walkover"):
            story.append(Paragraph(
                "<b>Walkover.</b> No innings were played for this match.", S_CELL_L))
        else:
            fow = _fall_of_wickets(summary.get("delivery_log") or [])
            for section in summary.get("team_sections") or []:
                story.extend(_team_section(section, fow))

        story.append(HRFlowable(width="100%", thickness=1.5, color=C_ACCENT, spaceAfter=5))
        story.extend(_fantasy_section(summary.get("fantasy_leaderboard")))
        story.extend(_revenue_section(finance_entries or []))

        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(
            "This scorecard was auto-generated from the imported ball-by-ball match data.",
            _ps("foot", fontSize=7, textColor=colors.darkgrey,
                fontName="Helvetica-Oblique", alignment=TA_CENTER)))

        doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return buf.getvalue()
