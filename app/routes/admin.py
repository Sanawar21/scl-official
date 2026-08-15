import json

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import rules as R
from ..authz import login_required
from ..db import json_loads

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _boolish(value):
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _season_id():
    return (request.args.get("season") or request.form.get("season") or "").strip().lower()


def _build_context(season_id=None):
    auction_service = current_app.extensions["auction_service"]
    seasons = auction_service.list_seasons()
    if not seasons:
        return {"seasons": [], "season": None, "state": None, "action_log": [],
                "transfers": [], "global_players": [], "assignable_users": [],
                "teams_by_id": {}}

    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"]

    state = auction_service.get_state(season_id)
    action_log = auction_service.action_log(season_id, limit=50)
    transfers = auction_service.list_transfers(season_id)
    global_players = auction_service.list_global_players()
    assignable_users = _assignable_users()

    teams_by_id = {t["id"]: t for t in state["teams"]}
    players_by_id = {p["id"]: p for p in state["players"]}
    manager_ids = {t["manager_player_id"] for t in state["teams"] if t["manager_player_id"]}
    available_manager_players = [
        gp for gp in global_players if gp["id"] not in manager_ids
    ]
    user_by_gp = {}
    with current_app.extensions["db"].read() as conn:
        for row in conn.execute("SELECT id, username, global_player_id, role FROM users").fetchall():
            if row["global_player_id"]:
                user_by_gp[row["global_player_id"]] = dict(row)

    return {
        "seasons": seasons,
        "season_id": season_id,
        "state": state,
        "action_log": action_log,
        "transfers": transfers,
        "global_players": global_players,
        "available_manager_players": available_manager_players,
        "assignable_users": assignable_users,
        "teams_by_id": teams_by_id,
        "players_by_id": players_by_id,
        "user_by_gp": user_by_gp,
    }


def _assignable_users():
    """Linked player accounts that can be assigned as manager of their team."""
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.global_player_id, u.role, g.name AS player_name, "
            "t.id AS team_id, t.name AS team_name, t.season_id "
            "FROM users u "
            "JOIN global_players g ON g.id = u.global_player_id "
            "LEFT JOIN teams t ON t.manager_player_id = u.global_player_id "
            "WHERE u.role != 'admin' AND u.team_id IS NULL "
            "ORDER BY u.username"
        ).fetchall()
        return [dict(r) for r in rows]


@admin_bp.get("")
@login_required(role=R.ROLE_ADMIN)
def dashboard():
    return render_template("admin/dashboard.html", **_build_context(_season_id()))


@admin_bp.post("/season/create")
@login_required(role=R.ROLE_ADMIN)
def season_create():
    auction_service = current_app.extensions["auction_service"]
    try:
        season = auction_service.create_season(
            request.form.get("name", ""),
            ruleset_overrides=_ruleset_form(),
        )
        flash(f"Season '{season['name']}' created.", "success")
        return redirect(url_for("admin.dashboard", season=season["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.dashboard"))


@admin_bp.post("/season/<season_id>/ruleset")
@login_required(role=R.ROLE_ADMIN)
def season_ruleset(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.update_ruleset(season_id, _ruleset_form())
        flash("Ruleset updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


def _ruleset_form():
    def int_or_none(key):
        value = (request.form.get(key) or "").strip()
        return int(value) if value else None

    overrides = {
        "total_credits": int_or_none("total_credits"),
        "bid_increment": int_or_none("bid_increment"),
        "phase_b_price": int_or_none("phase_b_price"),
        "credit_refund_rate": int_or_none("credit_refund_rate"),
        "required_players": int_or_none("required_players"),
        "roster_size": int_or_none("roster_size"),
        "break_minutes": int_or_none("break_minutes"),
    }
    order_raw = request.form.get("phase_order")
    if order_raw:
        order = [item.strip().lower() for item in order_raw.split(",") if item.strip()]
        if order:
            overrides["phase_order"] = order
    purses = _tier_json_form("tier_purses", "purse")
    if purses:
        overrides["tier_purses"] = purses
    bases = _tier_json_form("tier_base_prices", "base")
    if bases:
        overrides["tier_base_prices"] = bases
    credits = _tier_json_form("tier_credits", "credit")
    if credits:
        overrides["tier_credits"] = credits
    return {k: v for k, v in overrides.items() if v is not None}


def _tier_json_form(prefix, suffix):
    values = {}
    for tier in R.TIERS:
        value = (request.form.get(f"{prefix}_{tier}_{suffix}") or "").strip()
        if value:
            values[tier] = int(value)
    return values or None


@admin_bp.post("/season/<season_id>/player/add")
@login_required(role=R.ROLE_ADMIN)
def player_add(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.add_player(
            season_id,
            request.form.get("name", ""),
            request.form.get("tier", ""),
            request.form.get("speciality", "ALL_ROUNDER"),
        )
        flash("Player added.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/player/<player_id>/update")
@login_required(role=R.ROLE_ADMIN)
def player_update(season_id, player_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.update_player(
            season_id, player_id,
            name=request.form.get("name") or None,
            tier=(request.form.get("tier") or "").strip() or None,
            speciality=(request.form.get("speciality") or "").strip().upper() or None,
        )
        flash("Player updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/player/<player_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def player_delete(season_id, player_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.delete_player(season_id, player_id)
        flash("Player removed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/team/create")
@login_required(role=R.ROLE_ADMIN)
def team_create(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.create_team(
            season_id,
            request.form.get("name", ""),
            request.form.get("manager_player_id", ""),
        )
        flash("Team created.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def team_delete(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.delete_team(season_id, team_id)
        flash("Team removed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/gift")
@login_required(role=R.ROLE_ADMIN)
def team_gift(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.gift_team(
            season_id, team_id,
            amount=_safe_int(request.form.get("amount"), 0),
            operation=(request.form.get("operation") or "add").lower(),
            comment=request.form.get("comment", ""),
            actor=request.form.get("actor", "admin"),
        )
        flash("Gift applied.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/phase")
@login_required(role=R.ROLE_ADMIN)
def phase_set(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.set_phase(season_id, request.form.get("phase", ""))
        flash("Phase changed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/nominate")
@login_required(role=R.ROLE_ADMIN)
def nominate(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.nominate_next(season_id)
        flash("Next player nominated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/previous")
@login_required(role=R.ROLE_ADMIN)
def previous(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.previous_player(season_id)
        flash("Stepped back to previous lot.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/close")
@login_required(role=R.ROLE_ADMIN)
def close_lot(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        result = auction_service.close_current(season_id)
        if result["sold"]:
            flash(f"Sold to {result['team_name']} for {result['price']}.", "success")
        else:
            flash("Lot closed — no bid (player stays unsold).", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/complete")
@login_required(role=R.ROLE_ADMIN)
def complete(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.complete_draft(season_id)
        flash("Draft completed; incomplete teams filled with penalties.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/undo")
@login_required(role=R.ROLE_ADMIN)
def undo(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        result = auction_service.undo_last_action(season_id)
        flash(f"Undid '{result['action_type']}'.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/transfer")
@login_required(role=R.ROLE_ADMIN)
def transfer(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.admin_transfer(
            season_id,
            team_to=request.form.get("team_to", ""),
            player_id=request.form.get("player_id", ""),
            team_from=(request.form.get("team_from") or "").strip() or None,
            price=_safe_int(request.form.get("price"), 0),
            credits=_safe_int(request.form.get("credits"), 0),
            note=request.form.get("note", ""),
        )
        flash("Transfer completed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/takeover")
@login_required(role=R.ROLE_ADMIN)
def takeover(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.takeover_team(season_id, team_id, request.form.get("reason", ""))
        flash("Team taken over by admin.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/restore")
@login_required(role=R.ROLE_ADMIN)
def restore(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.restore_team(season_id, team_id)
        flash("Control restored to manager.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/season/<season_id>/publish")
@login_required(role=R.ROLE_ADMIN)
def publish(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.publish(season_id, request.form.get("name", ""))
        flash("Season published.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=season_id))


@admin_bp.post("/bank/adjust")
@login_required(role=R.ROLE_ADMIN)
def bank_adjust():
    bank_service = current_app.extensions["bank_service"]
    account_ref = request.form.get("account_id", "")
    amount = _safe_int(request.form.get("amount"), 0)
    comment = request.form.get("comment", "")
    try:
        if ":" in account_ref:
            owner_type, owner_id = account_ref.split(":", 1)
            account = bank_service.get_or_create_account(owner_type, owner_id)
            account_id = account["id"]
        else:
            account_id = account_ref
        account = bank_service.adjust(account_id, amount, comment, tx_type="admin_adjust")
        flash(f"Account adjusted. Liquid cash: {account['liquid_cash']}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard", season=_season_id()))


@admin_bp.get("/season/<season_id>/state")
@login_required(role=R.ROLE_ADMIN)
def state_json(season_id):
    return jsonify(current_app.extensions["auction_service"].get_state(season_id))
