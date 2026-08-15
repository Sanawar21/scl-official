from flask import Blueprint, abort, current_app, jsonify, render_template, request, url_for

from ..db import json_loads

viewer_bp = Blueprint("viewer", __name__)


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
