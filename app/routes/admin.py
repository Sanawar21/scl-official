import json
import re

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

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


def _latest_season_id():
    seasons = current_app.extensions["auction_service"].list_seasons()  # newest first
    return seasons[0]["id"] if seasons else None


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

    branding = current_app.extensions["branding_service"]
    for team in state["teams"]:
        team["logo_url"] = branding.team_logo(team)
        team["banner_url"] = branding.team_banner(team)
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


@admin_bp.get("/changelog")
@login_required(role=R.ROLE_ADMIN)
def changelog():
    from ..services.doc_service import md_to_html
    entries = current_app.extensions["changelog_service"].list_entries()
    for e in entries:
        e["body_html"] = md_to_html(e["body"])
    return render_template("admin/changelog.html", entries=entries,
                           active_admin_tab="changelog")


@admin_bp.post("/changelog/add")
@login_required(role=R.ROLE_ADMIN)
def changelog_add():
    user = session.get("user") or {}
    try:
        current_app.extensions["changelog_service"].add_entry(
            request.form.get("title", ""),
            request.form.get("body", ""),
            request.form.get("change_date", ""),
            user.get("username") or "admin",
        )
        flash("Change log entry added.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.changelog"))


@admin_bp.post("/changelog/<entry_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def changelog_delete(entry_id):
    current_app.extensions["changelog_service"].delete_entry(entry_id)
    flash("Change log entry removed.", "success")
    return redirect(url_for("admin.changelog"))


@admin_bp.get("")
@login_required(role=R.ROLE_ADMIN)
def dashboard():
    """Admin overview: one place to see the whole app + jump to each section."""
    return render_template("admin/overview.html", **_overview_context(_season_id()))


@admin_bp.get("/auction")
@login_required(role=R.ROLE_ADMIN)
def auction():
    context = _build_context(_season_id())
    context["active_admin_tab"] = "auction"
    return render_template("admin/dashboard.html", **context)


@admin_bp.post("/season/create")
@login_required(role=R.ROLE_ADMIN)
def season_create():
    auction_service = current_app.extensions["auction_service"]
    try:
        season = auction_service.create_season(
            request.form.get("name", ""),
            ruleset_overrides=_ruleset_form(),
        )
        flash(f"Season '{season['name']}' created. Set up its managers and auction pool.", "success")
        return redirect(url_for("admin.season_setup", season_id=season["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.dashboard"))


@admin_bp.get("/season/<season_id>/setup")
@login_required(role=R.ROLE_ADMIN)
def season_setup(season_id):
    auction = current_app.extensions["auction_service"]
    ctx = auction.season_setup_context(season_id)
    if not ctx:
        flash("Season not found.", "error")
        return redirect(url_for("admin.dashboard"))
    ctx["seasons"] = auction.list_seasons()
    ctx["season_id"] = season_id
    ctx["active_admin_tab"] = "setup"
    return render_template("admin/season_setup.html", **ctx)


@admin_bp.post("/season/<season_id>/setup/save")
@login_required(role=R.ROLE_ADMIN)
def season_setup_save(season_id):
    auction = current_app.extensions["auction_service"]
    try:
        auction.sync_season_setup(
            season_id,
            auction_player_ids=request.form.getlist("auction_players"),
            manager_team_names=_manager_team_names(),
        )
        flash("Setup saved.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.season_setup", season_id=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/manager")
@login_required(role=R.ROLE_ADMIN)
def season_team_manager(season_id, team_id):
    auction = current_app.extensions["auction_service"]
    try:
        auction.reassign_team_manager(
            season_id, team_id,
            (request.form.get("manager_player_id") or "").strip(),
        )
        flash("Team manager updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.season_setup", season_id=season_id))


def _manager_team_names():
    """Parse {global_player_id: team_name} from the setup form."""
    result = {}
    for gp_id in request.form.getlist("managers"):
        result[gp_id] = (request.form.get(f"team_name_{gp_id}") or "").strip()
    return result


@admin_bp.post("/season/<season_id>/ruleset")
@login_required(role=R.ROLE_ADMIN)
def season_ruleset(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.update_ruleset(season_id, _ruleset_form())
        flash("Ruleset updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


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
    # S2: no per-tier purse — the field is gone from the UI; S1 ruleset rows
    # keep their stored purses untouched.
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
    return redirect(url_for("admin.auction", season=season_id))


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
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/player/<player_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def player_delete(season_id, player_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.delete_player(season_id, player_id)
        flash("Player removed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


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
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def team_delete(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.delete_team(season_id, team_id)
        flash("Team removed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


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
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/phase")
@login_required(role=R.ROLE_ADMIN)
def phase_set(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.set_phase(season_id, request.form.get("phase", ""))
        flash("Phase changed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/nominate")
@login_required(role=R.ROLE_ADMIN)
def nominate(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.nominate_next(season_id)
        flash("Next player nominated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/previous")
@login_required(role=R.ROLE_ADMIN)
def previous(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.previous_player(season_id)
        flash("Stepped back to previous lot.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


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
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/complete")
@login_required(role=R.ROLE_ADMIN)
def complete(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.complete_draft(season_id)
        # S2: automatically apply the squad-cost levy to non-spenders.
        finance = current_app.extensions["finance_service"]
        levy = finance.apply_squad_levy(season_id)
        if levy.get("applied"):
            flash(f"Draft completed; squad-cost levy {levy['levy']:,} charged to "
                  f"{levy['charged']} wallets ({levy['exempt']} exempt).", "success")
        else:
            flash("Draft completed; incomplete teams filled with penalties.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/undo")
@login_required(role=R.ROLE_ADMIN)
def undo(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        result = auction_service.undo_last_action(season_id)
        flash(f"Undid '{result['action_type']}'.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


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
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/takeover")
@login_required(role=R.ROLE_ADMIN)
def takeover(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.takeover_team(season_id, team_id, request.form.get("reason", ""))
        flash("Team taken over by admin.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/team/<team_id>/restore")
@login_required(role=R.ROLE_ADMIN)
def restore(season_id, team_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.restore_team(season_id, team_id)
        flash("Control restored to manager.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


@admin_bp.post("/season/<season_id>/publish")
@login_required(role=R.ROLE_ADMIN)
def publish(season_id):
    auction_service = current_app.extensions["auction_service"]
    try:
        auction_service.publish(season_id, request.form.get("name", ""))
        flash("Season published.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=season_id))


# ---------------------------------------------------------------------------
# Admin teams control panel (persistent team branding, manager, profile)
# ---------------------------------------------------------------------------
def _teams_context():
    auction = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    global_players = auction.list_global_players()
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT gt.*, g.name AS manager_name, g.tier AS manager_tier, "
            "(SELECT COUNT(*) FROM teams t WHERE t.global_team_id = gt.id) AS season_count "
            "FROM global_teams gt LEFT JOIN global_players g ON g.id = gt.manager_player_id "
            "ORDER BY gt.name").fetchall()
        teams = []
        for r in rows:
            team = dict(r)
            acct = conn.execute(
                "SELECT liquid_cash FROM bank_accounts WHERE owner_type = 'player' "
                "AND owner_id = ?", (team["manager_player_id"],)).fetchone() if team["manager_player_id"] else None
            team["wallet"] = int(acct["liquid_cash"]) if acct else 0
            team["assets"] = branding.team_assets(team)
            teams.append(team)
        seasons = conn.execute("SELECT id, name FROM seasons ORDER BY created_at DESC").fetchall()
        season_names = {s["id"]: s["name"] for s in seasons}
        season_rows = conn.execute("SELECT global_team_id, season_id FROM teams").fetchall()
    team_seasons = {}
    for r in season_rows:
        gid = (r["global_team_id"] or "").strip()
        if gid:
            team_seasons.setdefault(gid, []).append(season_names.get(r["season_id"], r["season_id"]))
    manager_ids = {t["manager_player_id"] for t in teams if t["manager_player_id"]}
    available_managers = [gp for gp in global_players if gp["id"] not in manager_ids]
    return {"teams": teams, "team_seasons": team_seasons,
            "available_managers": available_managers,
            "active_admin_tab": "teams"}


@admin_bp.get("/teams")
@login_required(role=R.ROLE_ADMIN)
def teams_page():
    return render_template("admin/teams.html", **_teams_context())


@admin_bp.post("/teams/create")
@login_required(role=R.ROLE_ADMIN)
def teams_create():
    auction = current_app.extensions["auction_service"]
    try:
        team = auction.create_team_account(
            (request.form.get("manager_player_id") or "").strip(),
            request.form.get("name", ""),
        )
        flash(f"Team '{team['name']}' created.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.teams_page"))


@admin_bp.post("/teams/<team_id>/update")
@login_required(role=R.ROLE_ADMIN)
def teams_update(team_id):
    auction = current_app.extensions["auction_service"]
    try:
        team = auction.update_team_profile(
            team_id,
            name=request.form.get("name"),
            about=request.form.get("about"),
        )
        flash(f"Team '{team['name']}' updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.teams_page"))


@admin_bp.post("/teams/<team_id>/branding")
@login_required(role=R.ROLE_ADMIN)
def teams_branding(team_id):
    auction = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    try:
        updates = {}
        for kind in ("logo", "banner"):
            file = request.files.get(kind)
            if file and file.filename:
                updates[kind] = branding.save_team_asset(team_id, kind, file)
        if not updates:
            raise ValueError("Choose a logo or banner image to upload.")
        team = auction.update_team_profile(team_id, **updates)
        flash(f"Branding updated for {team['name']}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.teams_page"))


@admin_bp.post("/teams/<team_id>/branding/remove")
@login_required(role=R.ROLE_ADMIN)
def teams_branding_remove(team_id):
    auction = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    kind = (request.form.get("kind") or "").strip()
    try:
        branding.remove_team_asset(team_id, kind)
        auction.update_team_profile(team_id, **{kind: ""})
        flash("Asset removed — SCL branding is used by default.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.teams_page"))


@admin_bp.post("/teams/<team_id>/delete")
@login_required(role=R.ROLE_ADMIN)
def teams_delete(team_id):
    """Remove the team profile. The manager's wallet is never touched."""
    with current_app.extensions["db"].write() as conn:
        row = conn.execute("SELECT name FROM global_teams WHERE id = ?", (team_id,)).fetchone()
        if not row:
            flash("Team not found.", "error")
            return redirect(url_for("admin.teams_page"))
        deleted_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM teams WHERE global_team_id = ?", (team_id,)).fetchall()]
        conn.execute("DELETE FROM global_teams WHERE id = ?", (team_id,))
        conn.execute("DELETE FROM teams WHERE global_team_id = ?", (team_id,))
        if deleted_ids:
            placeholders = ",".join("?" for _ in deleted_ids)
            conn.execute(f"UPDATE users SET team_id = NULL, role = 'player' "
                         f"WHERE team_id IN ({placeholders})", deleted_ids)
    flash(f"Team '{row['name']}' removed (wallet untouched).", "success")
    return redirect(url_for("admin.teams_page"))


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
        if amount >= 0:
            # Grants/deposits: credit() routes straight to the vault for auto
            # accounts (the owner chose not to manage liquid cash).
            season = _season_id() or _latest_season_id()
            account = bank_service.credit(account_id, amount, comment,
                                          tx_type="admin_adjust", season_id=season)
        else:
            # Deductions (fines/take-back) always come from liquid cash.
            account = bank_service.adjust(account_id, amount, comment, tx_type="admin_adjust")
        flash(f"Account adjusted. Liquid cash: {account['liquid_cash']}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.auction", season=_season_id()))


@admin_bp.get("/season/<season_id>/state")
@login_required(role=R.ROLE_ADMIN)
def state_json(season_id):
    return jsonify(current_app.extensions["auction_service"].get_state(season_id))


# ---------------------------------------------------------------------------
# Admin overview (status cards + recent activity)
# ---------------------------------------------------------------------------
def _overview_context(season_id=None):
    auction = current_app.extensions["auction_service"]
    scorer = current_app.extensions["scorer_service"]
    finance = current_app.extensions["finance_service"]
    wager = current_app.extensions["wager_service"]
    auth_svc = current_app.extensions["auth_service"]
    db = current_app.extensions["db"]
    seasons = auction.list_seasons()
    context = {
        "seasons": seasons,
        "season_id": season_id or "",
        "state": None,
        "active_admin_tab": "overview",
        "registry_count": 0, "finalized_count": 0, "pending_finance": 0,
        "wallet_total": 0, "vault_positions": 0, "yield_progress": 0,
        "wagers": [], "house": None,
        "unlinked_count": 0, "linked_count": 0,
        "recent_imports": [], "recent_actions": [], "recent_finance": [],
    }
    if not seasons:
        return context
    if season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"]
    context["season_id"] = season_id

    state = auction.get_state(season_id)
    branding = current_app.extensions["branding_service"]
    for team in state["teams"]:
        team["logo_url"] = branding.team_logo(team)
    context["state"] = state
    context["phase"] = state["phase"]
    context["teams_count"] = len(state["teams"])
    context["players_total"] = len(state["players"])
    context["players_sold"] = sum(1 for p in state["players"] if p["status"] == "sold")
    context["current_lot"] = state.get("current_player")
    context["snapshots"] = state.get("snapshots") or []
    context["recent_actions"] = auction.action_log(season_id, limit=8)

    registry = scorer.list_match_registry(season_id)
    context["registry_count"] = len(registry)
    context["recent_imports"] = scorer.list_recent_imports(limit=8)
    with db.read() as conn:
        context["finalized_count"] = conn.execute(
            "SELECT COUNT(*) FROM match_stats WHERE season_id = ?", (season_id,)).fetchone()[0]
        context["pending_finance"] = conn.execute(
            "SELECT COUNT(*) FROM match_stats s WHERE s.season_id = ? AND NOT EXISTS ("
            "SELECT 1 FROM season_finance_entries e WHERE e.season_id = s.season_id "
            "AND e.match_id = s.match_id AND e.type = 'match_reward' AND e.undone_at IS NULL)",
            (season_id,)).fetchone()[0]
        context["vault_positions"] = conn.execute(
            "SELECT COUNT(*) FROM vault_positions v JOIN bank_accounts a ON a.id = v.account_id "
            "WHERE v.season_id = ?", (season_id,)).fetchone()[0]
        max_match = 0
        for r in conn.execute(
                "SELECT r.match_number, r.match_id FROM match_stats s "
                "JOIN match_registry r ON r.match_key = s.match_key WHERE s.season_id = ?",
                (season_id,)).fetchall():
            text = str(r["match_number"] or r["match_id"] or "")
            m = re.search(r"\d+", text)
            if m and int(m.group(0)) > max_match:
                max_match = int(m.group(0))
        context["yield_progress"] = min(max_match, 12)  # vault yield caps at Match 12

    board = finance.list_season_finances(season_id)
    context["wallet_total"] = sum(int(r["wallet"] or 0) for r in board
                                   if r.get("kind") == "team")
    context["recent_finance"] = finance.list_finance_entries(season_id, limit=8)

    context["wagers"] = wager.list_wagers()
    context["house"] = wager.house_account()
    context["unlinked_count"] = len(auth_svc.list_unlinked_users())
    with db.read() as conn:
        context["linked_count"] = conn.execute(
            "SELECT COUNT(*) FROM users WHERE global_player_id IS NOT NULL").fetchone()[0]
    return context


# ---------------------------------------------------------------------------
# Finances + Vault (season finance ledger, match rewards, yield, M12 unlock)
# ---------------------------------------------------------------------------
def _finance_context(season_id=None):
    finance = current_app.extensions["finance_service"]
    auction = current_app.extensions["auction_service"]
    scorer = current_app.extensions["scorer_service"]
    db = current_app.extensions["db"]
    seasons = auction.list_seasons()
    if not seasons:
        return {"seasons": [], "season_id": "", "board": [], "entries": [], "hints": [],
                "vault_positions": [], "finalized_count": 0, "max_match": 0,
                "match_reward_amount": 0, "registry": []}
    if not season_id or season_id not in {s["id"] for s in seasons}:
        season_id = seasons[0]["id"]
    board = finance.list_season_finances(season_id)
    entries = finance.list_finance_entries(season_id)
    hints = finance.credit_refund_hint(season_id)
    registry = scorer.list_match_registry(season_id)
    finalized_count = 0
    max_match = 0
    with db.read() as conn:
        finalized_count = conn.execute(
            "SELECT COUNT(*) FROM match_stats WHERE season_id = ?", (season_id,)).fetchone()[0]
        vault_positions = conn.execute(
            "SELECT v.*, a.owner_type, a.owner_id FROM vault_positions v "
            "JOIN bank_accounts a ON a.id = v.account_id WHERE v.season_id = ? "
            "ORDER BY v.created_at", (season_id,)).fetchall()
        ruleset = auction._get_ruleset(conn, season_id)
        for r in conn.execute(
                "SELECT r.match_number, r.match_id FROM match_stats s "
                "JOIN match_registry r ON r.match_key = s.match_key WHERE s.season_id = ?",
                (season_id,)).fetchall():
            text = str(r["match_number"] or r["match_id"] or "")
            m = re.search(r"\d+", text)
            if m and int(m.group(0)) > max_match:
                max_match = int(m.group(0))
    return {
        "seasons": seasons,
        "season_id": season_id,
        "board": board,
        "entries": entries,
        "hints": hints,
        "vault_positions": [dict(v) for v in vault_positions],
        "finalized_count": finalized_count,
        "max_match": max_match,
        "match_reward_amount": ruleset.match_reward_amount,
        "registry": registry,
        "active_admin_tab": "finances",
    }


@admin_bp.get("/finances")
@login_required(role=R.ROLE_ADMIN)
def finances():
    return render_template("admin/finances.html", **_finance_context(_season_id()))


@admin_bp.post("/finances/fund-all")
@login_required(role=R.ROLE_ADMIN)
def finances_fund_all():
    bank = current_app.extensions["bank_service"]
    try:
        amount = int(request.form.get("amount") or 10000)
        result = bank.fund_all_players(amount)
        if result["funded"]:
            flash(f"Funded {result['funded']} players with {amount:,} each "
                  f"({result['skipped']} already funded).", "success")
        else:
            flash(f"All {result['skipped']} players already funded — nothing to do.", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=_season_id()))


@admin_bp.post("/finances/levy")
@login_required(role=R.ROLE_ADMIN)
def finances_levy():
    """Manual fallback for the squad-cost levy (auto-runs on draft complete)."""
    finance = current_app.extensions["finance_service"]
    season_id = (request.form.get("season_id") or _season_id() or "").strip().lower()
    try:
        result = finance.apply_squad_levy(season_id)
        if result.get("applied"):
            flash(f"Squad-cost levy {result['levy']:,} charged to {result['charged']} "
                  f"wallets ({result['exempt']} exempt).", "success")
        else:
            flash("Squad levy already applied (or nothing to levy).", "info")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/adjust")
@login_required(role=R.ROLE_ADMIN)
def finances_adjust():
    finance = current_app.extensions["finance_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    try:
        finance.post_adjust(
            season_id,
            request.form.get("team_id", ""),
            request.form.get("operation", "add"),
            _safe_int(request.form.get("amount"), 0),
            request.form.get("comment", ""),
            actor=session.get("user", {}).get("username", "admin"),
        )
        flash("Adjustment posted.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/transfer")
@login_required(role=R.ROLE_ADMIN)
def finances_transfer():
    finance = current_app.extensions["finance_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    try:
        finance.post_transfer(
            season_id,
            request.form.get("from_team_id", ""),
            request.form.get("to_team_id", ""),
            _safe_int(request.form.get("amount"), 0),
            request.form.get("comment", ""),
            actor=session.get("user", {}).get("username", "admin"),
        )
        flash("Transfer posted.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/process-pending")
@login_required(role=R.ROLE_ADMIN)
def finances_process_pending():
    finance = current_app.extensions["finance_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    results = finance.process_pending(season_id)
    flash(f"Processed {len(results)} finalized match(es).", "success")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/yield")
@login_required(role=R.ROLE_ADMIN)
def finances_yield():
    bank = current_app.extensions["bank_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    match_number = _safe_int(request.form.get("match_number"), 0)
    try:
        results = bank.apply_match_yield(season_id, match_number)
        flash(f"Yield applied for {len(results)} position-step(s).", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/unlock")
@login_required(role=R.ROLE_ADMIN)
def finances_unlock():
    bank = current_app.extensions["bank_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    try:
        results = bank.unlock_vault(season_id, force=_boolish(request.form.get("force")))
        released = sum(r["released"] for r in results)
        flash(f"Vault unlocked: {len(results)} position(s), {released} released to liquid.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))


@admin_bp.post("/finances/undo")
@login_required(role=R.ROLE_ADMIN)
def finances_undo():
    finance = current_app.extensions["finance_service"]
    season_id = (request.form.get("season_id") or "").strip().lower()
    try:
        result = finance.undo_last_finance_entry(season_id)
        flash(f"Undid {result['type']} of {result['amount']}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.finances", season=season_id))
