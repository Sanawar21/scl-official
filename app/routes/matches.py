import re
from io import BytesIO

from flask import (Blueprint, current_app, flash, make_response, redirect,
                   render_template, render_template_string, request, send_file,
                   session, url_for)

from .. import rules as R
from ..authz import login_required
from ..services.scorer_service import MatchOverwriteConfirmationRequired, team_profile_slug

matches_bp = Blueprint("matches", __name__)


def _scorer_service():
    return current_app.extensions["scorer_service"]


def _auction_service():
    return current_app.extensions["auction_service"]


def _finance_service():
    return current_app.extensions["finance_service"]


def _season_id():
    return (request.args.get("season") or request.form.get("season") or "").strip().lower()


def _pick_season(requested=""):
    """Resolve ?season= against seasons that actually have match registry data."""
    svc = _scorer_service()
    match_seasons = svc.list_match_seasons()
    slugs = {s["slug"] for s in match_seasons}
    requested = (requested or "").strip().lower()
    if requested and requested in slugs:
        return requested, match_seasons
    if match_seasons:
        return match_seasons[0]["slug"], match_seasons
    return "", match_seasons


# ----------------------------------------------------------------------
# offline scorer (public, like the reference app)
# ----------------------------------------------------------------------
@matches_bp.get("/scorer")
def scorer_page():
    svc = _scorer_service()
    html = render_template_string(svc.template_source(), **svc.build_scorer_context())
    return make_response(html)


@matches_bp.get("/scorer/download")
def scorer_download():
    svc = _scorer_service()
    context = svc.build_scorer_context()
    html = render_template_string(svc.template_source(), **context)
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{context["scorer_download_filename"]}"')
    return response


# ----------------------------------------------------------------------
# public pages
# ----------------------------------------------------------------------
@matches_bp.get("/matches")
def matches_index():
    svc = _scorer_service()
    season_id, match_seasons = _pick_season(_season_id())
    registry = svc.list_match_registry(season_id) if season_id else svc.list_match_registry()
    return render_template("matches/index.html", season_id=season_id,
                           match_seasons=match_seasons, registry=registry)


@matches_bp.get("/matches/<season_id>")
def matches_season(season_id):
    season_id = season_id.lower()
    if season_id not in {s["slug"] for s in _scorer_service().list_match_seasons()}:
        flash("Season not found.", "error")
        return redirect(url_for("matches.matches_index"))
    svc = _scorer_service()
    registry = svc.list_match_registry(season_id)
    match_seasons = svc.list_match_seasons()
    return render_template("matches/index.html", season_id=season_id,
                           match_seasons=match_seasons, registry=registry)


@matches_bp.get("/matches/<season_id>/<match_id>")
def match_summary(season_id, match_id):
    svc = _scorer_service()
    summary = svc.match_summary(season_id.lower(), match_id)
    if not summary:
        flash("Match not found.", "error")
        return redirect(url_for("matches.matches_index", season=season_id))
    return render_template("matches/summary.html", summary=summary)


@matches_bp.get("/matches/<season_id>/<match_id>/scorecard")
def match_scorecard(season_id, match_id):
    """Official scorecard PDF, generated from the imported match data."""
    svc = _scorer_service()
    season_id = season_id.lower()
    summary = svc.match_summary(season_id, match_id)
    if not summary:
        flash("Match not found.", "error")
        return redirect(url_for("matches.matches_index", season=season_id))
    if not summary.get("has_uploaded_data"):
        flash("No uploaded data for this match yet.", "error")
        return redirect(url_for("matches.match_summary", season_id=season_id, match_id=match_id))
    entries = [e for e in _finance_service().list_finance_entries(season_id)
               if e.get("match_id") == match_id and not e.get("undone_at")]
    pdf = current_app.extensions["scorecard_service"].build(summary, entries)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", match_id).strip("-") or "match"
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{season_id}-{safe}-scorecard.pdf")


@matches_bp.get("/finances")
def finances_index():
    finance = _finance_service()
    season_id, match_seasons = _pick_season(_season_id())
    board = finance.list_season_finances(season_id) if season_id else []
    entries = finance.list_finance_entries(season_id) if season_id else []
    return render_template("matches/finances.html", season_id=season_id,
                           match_seasons=match_seasons, board=board, entries=entries)


@matches_bp.get("/finances/<season_id>")
def finances_season(season_id):
    season_id = season_id.lower()
    if season_id not in {s["slug"] for s in _scorer_service().list_match_seasons()}:
        flash("Season not found.", "error")
        return redirect(url_for("matches.finances_index"))
    finance = _finance_service()
    board = finance.list_season_finances(season_id)
    entries = finance.list_finance_entries(season_id)
    match_seasons = _scorer_service().list_match_seasons()
    return render_template("matches/finances.html", season_id=season_id,
                           match_seasons=match_seasons, board=board, entries=entries)


@matches_bp.get("/table")
def league_table():
    svc = _scorer_service()
    season_id, match_seasons = _pick_season(_season_id())
    standings = svc.league_table(season_id) if season_id else []
    return render_template("matches/table.html", season_id=season_id,
                           match_seasons=match_seasons, standings=standings)


@matches_bp.get("/leaderboards")
def leaderboards():
    svc = _scorer_service()
    season_id, match_seasons = _pick_season(_season_id())
    boards = svc.leaderboards(season_id) if season_id else svc.leaderboards()
    return render_template("matches/leaderboard.html", season_id=season_id,
                           match_seasons=match_seasons, boards=boards)


@matches_bp.get("/teams")
def teams_index():
    svc = _scorer_service()
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT * FROM teams ORDER BY season_id, name").fetchall()
    teams = []
    for t in rows:
        gid = (t["global_team_id"] or "").strip() or t["id"]
        teams.append({
            "team_id": gid,
            "name": t["name"],
            "season_id": t["season_id"],
            "slug": team_profile_slug(gid, t["name"]),
        })
    return render_template("teams/index.html", teams=teams)


@matches_bp.get("/teams/<slug>")
def team_detail(slug):
    svc = _scorer_service()
    profile = svc.team_profile(slug)
    if not profile:
        flash("Team not found.", "error")
        return redirect(url_for("matches.teams_index"))
    return render_template("teams/detail.html", profile=profile)


@matches_bp.get("/players/<slug>")
def player_detail(slug):
    svc = _scorer_service()
    profile = svc.player_profile(slug)
    if not profile:
        flash("Player not found.", "error")
        return redirect(url_for("matches.teams_index"))
    return render_template("players/detail.html", profile=profile)


# ----------------------------------------------------------------------
# admin scorer control room
# ----------------------------------------------------------------------
def _admin_context():
    svc = _scorer_service()
    auction = _auction_service()
    match_seasons = svc.list_match_seasons()
    config = svc.load_config()
    registry = svc.list_match_registry()
    recent_imports = svc.list_recent_imports()
    season_options = auction.list_seasons()
    team_options = svc.season_team_options(_season_id())
    return {
        "config": config,
        "match_seasons": match_seasons,
        "registry": registry,
        "recent_imports": recent_imports,
        "season_options": season_options,
        "team_options": team_options,
        "season_id": _season_id(),
    }


@matches_bp.get("/admin/scorer")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer():
    return render_template("matches/admin.html", **_admin_context())


@matches_bp.post("/admin/scorer/config")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer_config():
    svc = _scorer_service()
    try:
        svc.save_config({
            "season_slug": request.form.get("season_slug", ""),
            "max_overs": (request.form.get("max_overs") or "3").strip() or "3",
        })
        flash("Scorer config saved.", "success")
    except (TypeError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("matches.admin_scorer", season=_season_id()))


@matches_bp.post("/admin/scorer/registry")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer_registry():
    svc = _scorer_service()
    season_id = (request.form.get("season_id") or "").strip().lower()
    try:
        svc.upsert_match_registry_entry(
            season_id=season_id,
            match_id=(request.form.get("match_id") or "").strip(),
            match_number=(request.form.get("match_number") or "").strip(),
            match_title=(request.form.get("match_title") or "").strip(),
            between=(request.form.get("between") or "").strip(),
            venue=(request.form.get("venue") or "").strip(),
            match_date=(request.form.get("match_date") or "").strip(),
            team_a_global_id=(request.form.get("team_a_global_id") or "").strip(),
            team_b_global_id=(request.form.get("team_b_global_id") or "").strip(),
            walkover=(request.form.get("walkover") or "").strip().lower() in {"1", "on", "true", "yes"},
            walkover_winner_team_id=(request.form.get("walkover_winner_team_id") or "").strip(),
        )
        flash("Match saved.", "success")
        # Walkovers are finalized immediately -> auto reward + vault yield.
        _finance_service().on_match_finalized(season_id, (request.form.get("match_id") or "").strip())
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("matches.admin_scorer", season=season_id))


@matches_bp.post("/admin/scorer/registry/delete")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer_registry_delete():
    svc = _scorer_service()
    season_id = (request.form.get("season_id") or "").strip().lower()
    match_id = (request.form.get("match_id") or "").strip()
    try:
        result = svc.delete_match_registry_entry(season_id, match_id)
        flash("Match deleted." if result.get("ok") else "Match not found.", "success" if result.get("ok") else "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("matches.admin_scorer", season=season_id))


@matches_bp.post("/admin/scorer/import")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer_import():
    svc = _scorer_service()
    season_id = (request.form.get("season_id") or "").strip().lower()
    file_storage = request.files.get("csv")
    try:
        derived = svc.import_match_csv(
            file_storage,
            season_id=season_id,
            match_id_override=(request.form.get("match_id") or "").strip(),
            venue_override=(request.form.get("venue") or "").strip(),
            match_date=(request.form.get("match_date") or "").strip(),
            uploaded_by=session.get("user", {}).get("username", "admin"),
            confirm_overwrite=(request.form.get("confirm_overwrite") or "").strip().lower() in {"1", "on", "true", "yes"},
            include_in_fantasy_points=(request.form.get("include_in_fantasy_points") or "").strip().lower() not in {"0", "off", "no"},
        )
        match_id = (derived.get("match_row") or {}).get("match_id") or "match"
        finance = _finance_service().on_match_finalized(season_id, match_id)
        flash(f"Imported {match_id}: {len(derived.get('team_rows', []))} teams, "
              f"{len(derived.get('player_rows', []))} players"
              + (f" (+{finance.get('yield_applied', 0)} vault yields)" if finance.get("finalized") else "")
              + ".", "success")
    except MatchOverwriteConfirmationRequired as exc:
        flash(f"Match {exc.match_id} already has data — submit again with overwrite confirmed.",
              "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("matches.admin_scorer", season=season_id))


@matches_bp.post("/admin/scorer/undo")
@login_required(role=R.ROLE_ADMIN)
def admin_scorer_undo():
    svc = _scorer_service()
    season_id = (request.form.get("season_id") or "").strip().lower()
    match_id = (request.form.get("match_id") or "").strip()
    try:
        result = svc.undo_imported_match(f"{season_id}:{match_id.lower()}")
        flash("Import undone." if result.get("ok") else "No data found to undo.", "success" if result.get("ok") else "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("matches.admin_scorer", season=season_id))
