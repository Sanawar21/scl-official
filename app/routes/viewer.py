from flask import Blueprint, abort, current_app, jsonify, render_template, request

from ..db import json_loads

viewer_bp = Blueprint("viewer", __name__)


@viewer_bp.get("/")
def home():
    auction_service = current_app.extensions["auction_service"]
    seasons = auction_service.list_seasons()
    published = []
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT s.*, se.name AS season_name FROM season_snapshots s "
            "JOIN seasons se ON se.id = s.season_id ORDER BY s.published_at DESC"
        ).fetchall()
        published = [dict(r) for r in rows]
    return render_template("viewer/home.html", seasons=seasons, published=published)


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
