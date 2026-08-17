from io import BytesIO
import re

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, url_for

from ..db import json_loads
from ..services.doc_service import DOCS, DOCS_ROOT, md_to_html, read_doc
from ..services import doc_service
from ..services.branding_service import BrandingService

viewer_bp = Blueprint("viewer", __name__)


@viewer_bp.get("/branding/<path:relpath>")
def branding_asset(relpath):
    """Serve SCL + team brand assets from data/brandings/ (read-only)."""
    try:
        response = current_app.extensions["branding_service"].serve(relpath)
    except ValueError:
        abort(404)
    if response is None:
        abort(404)
    return response


@viewer_bp.get("/changelog")
def changelog():
    entries = current_app.extensions["changelog_service"].list_entries()
    for e in entries:
        e["body_html"] = md_to_html(e["body"])
    return render_template("viewer/changelog.html", entries=entries, active="changelog")


@viewer_bp.get("/docs")
def docs_index():
    return render_template("viewer/docs.html", docs=DOCS, active="docs")


@viewer_bp.get("/docs/<slug>")
def doc_detail(slug):
    md = read_doc(slug)
    if md is None:
        abort(404)
    doc = next((d for d in DOCS if d["slug"] == slug), None)
    return render_template("viewer/doc_detail.html", doc=doc, html=md_to_html(md),
                           active="docs")


@viewer_bp.get("/docs/<slug>/pdf")
def doc_pdf(slug):
    path = DOCS_ROOT.parent / "app" / "static" / "docs" / f"{slug}.pdf"
    if not path.exists():
        # Regenerate on demand if the static copy is missing.
        md = read_doc(slug)
        if md is None:
            abort(404)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = next((d for d in DOCS if d["slug"] == slug), {})
        path.write_bytes(doc_service.md_to_pdf(md, doc.get("title", slug),
                                               "Official SCL Season 2 document"))
    return send_file(path, mimetype="application/pdf", as_attachment=False,
                     download_name=f"SCL-{slug}.pdf")


@viewer_bp.get("/")
def home():
    auction_service = current_app.extensions["auction_service"]
    scorer = current_app.extensions["scorer_service"]
    seasons = auction_service.list_seasons()
    published = []
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT s.*, se.name AS season_name FROM season_snapshots s "
            "JOIN seasons se ON se.id = s.season_id ORDER BY s.published_at DESC"
        ).fetchall()
        published = [dict(r) for r in rows]

    # Latest results: finalized matches (have a match_stats row) from the most
    # recent season, newest first, capped at 4.
    latest_results = []
    current_season = seasons[0] if seasons else None
    if current_season:
        finalized_keys = []
        with current_app.extensions["db"].read() as conn:
            for row in conn.execute(
                "SELECT match_key FROM match_stats WHERE season_id = ?",
                (current_season["id"],)).fetchall():
                finalized_keys.append(row["match_key"])
        registry_by_key = {r["match_key"]: r for r in scorer.list_match_registry(current_season["id"])}
        for key in finalized_keys:
            summary = scorer.match_summary(current_season["id"],
                                           (registry_by_key.get(key) or {}).get("match_id") or key.split(":")[-1])
            if not summary:
                continue
            latest_results.append({
                "match_number": summary.get("match_number") or "",
                "match_title": summary.get("match_title") or "",
                "between": summary.get("between") or "",
                "venue": summary.get("venue") or "",
                "match_date": summary.get("match_date") or "",
                "result": summary.get("result") or "",
                "winner_name": summary.get("winner_name") or "",
                "scores": [(s["team_name"], s["total"]) for s in summary.get("team_sections", [])],
                "url": url_for("matches.match_summary", season_id=current_season["id"],
                               match_id=(registry_by_key.get(key) or {}).get("match_id") or key.split(":")[-1]),
            })
        latest_results.sort(key=lambda r: r["match_number"] or r["match_title"] or "")
        latest_results = latest_results[-4:][::-1]

    return render_template("viewer/home.html", seasons=seasons, published=published,
                           current_season=current_season, latest_results=latest_results)


@viewer_bp.get("/live")
def live():
    auction_service = current_app.extensions["auction_service"]
    seasons = auction_service.list_seasons()
    season_id = (request.args.get("season") or "").strip().lower()
    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"] if seasons else None
    if not season_id:
        return render_template("viewer/live.html", state=None, seasons=[], season_id=None)
    state = auction_service.get_state(season_id)
    return render_template("viewer/live.html", state=state, seasons=seasons, season_id=season_id)


@viewer_bp.get("/auction")
def auction():
    """Public auction page — full draft details (squads, spend, players,
    bid feed) for any phase; after completion it shows the final results."""
    auction_service = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    seasons = auction_service.list_seasons()
    season_id = (request.args.get("season") or "").strip().lower()
    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"] if seasons else None
    if not season_id:
        return render_template("viewer/auction.html", state=None, seasons=[], season_id=None)
    state = auction_service.get_state(season_id)
    for team in state.get("teams") or []:
        team["logo_url"] = branding.team_logo(team)
    return render_template("viewer/auction.html", state=state, seasons=seasons, season_id=season_id)


@viewer_bp.get("/auction/export")
def auction_export():
    """PDF of the full auction details for a season (any phase)."""
    auction_service = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    seasons = auction_service.list_seasons()
    season_id = (request.args.get("season") or "").strip().lower()
    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"] if seasons else None
    if not season_id:
        abort(404)
    state = auction_service.get_state(season_id)
    # Resolve each team's logo/banner to a local file path for PDF embedding.
    with current_app.extensions["db"].read() as conn:
        globals_by_id = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM global_teams").fetchall()}
        season_map = {}
        for r in conn.execute("SELECT id, global_team_id FROM teams").fetchall():
            season_map[r["id"]] = (r["global_team_id"] or "").strip() or r["id"]
    for team in state.get("teams") or []:
        gid = season_map.get(team.get("id")) or team.get("id") or ""
        gt = globals_by_id.get(gid) or {}
        team["logo_path"] = branding.team_logo_file(gt)
        team["banner_path"] = branding.team_banner_file(gt)
    pdf = current_app.extensions["auction_pdf_service"].build(state)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", season_id).strip("-") or "auction"
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{safe}-auction.pdf")


@viewer_bp.get("/api/state")
def api_state():
    season_id = (request.args.get("season") or "").strip().lower()
    auction_service = current_app.extensions["auction_service"]
    seasons = auction_service.list_seasons()
    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"] if seasons else None
    if not season_id:
        return jsonify({"ok": False, "error": "No seasons"}), 404
    return jsonify(auction_service.get_state(season_id))


@viewer_bp.get("/season/<slug>")
def published(slug):
    slug = slug.lower()
    if "." in slug:
        abort(404)
    with current_app.extensions["db"].read() as conn:
        row = conn.execute("SELECT * FROM season_snapshots WHERE season_id = ?", (slug,)).fetchone()
    if not row:
        abort(404)
    payload = json_loads(row["payload"], {})
    payload["published_name"] = row["name"]
    payload["published_at"] = row["published_at"]
    return render_template("viewer/published.html", state=payload)
