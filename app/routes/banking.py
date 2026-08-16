from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..authz import login_required
from ..db import row_to_dict

banking_bp = Blueprint("banking", __name__, url_prefix="/account")


def _my_account(bank_service, user):
    if not user.get("global_player_id"):
        return None
    return bank_service.get_or_create_account("player", user["global_player_id"])


def _latest_season_id():
    auction_service = current_app.extensions["auction_service"]
    seasons = auction_service.list_seasons()  # newest first
    if not seasons:
        return None
    return seasons[0]["id"]


@banking_bp.get("")
@login_required()
def account():
    bank_service = current_app.extensions["bank_service"]
    db = current_app.extensions["db"]
    user = session.get("user") or {}
    account = _my_account(bank_service, user)
    vault_positions = bank_service.vault_positions(account["id"]) if account else []
    seasons = current_app.extensions["auction_service"].list_seasons()
    txns = bank_service.transactions(account["id"], limit=50) if account else []
    finalized_counts = {}
    with db.read() as conn:
        for row in conn.execute("SELECT season_id, COUNT(*) AS n FROM match_stats GROUP BY season_id"):
            finalized_counts[row["season_id"]] = row["n"]
    my_team = None
    if user.get("global_player_id"):
        with db.read() as conn:
            gt = conn.execute(
                "SELECT * FROM global_teams WHERE manager_player_id = ? LIMIT 1",
                (user["global_player_id"],)).fetchone()
            if gt:
                my_team = dict(gt)
                acct = conn.execute(
                    "SELECT liquid_cash FROM bank_accounts WHERE owner_type = 'player' "
                    "AND owner_id = ?", (user["global_player_id"],)).fetchone()
                my_team["wallet"] = int(acct["liquid_cash"]) if acct else 0
                my_team["seasons"] = [
                    dict(r) for r in conn.execute(
                        "SELECT season_id, name FROM teams "
                        "WHERE global_team_id = ? ORDER BY season_id", (my_team["id"],)).fetchall()
                ]
    match_reward_amount = 0
    if seasons:
        with db.read() as conn:
            ruleset = current_app.extensions["auction_service"]._get_ruleset(conn, seasons[0]["id"])
            match_reward_amount = ruleset.match_reward_amount
    return render_template("banking/account.html", account=account,
                           vault_positions=vault_positions, seasons=seasons, txns=txns,
                           finalized_counts=finalized_counts, my_team=my_team,
                           match_reward_amount=match_reward_amount)


@banking_bp.post("/team/create")
@login_required()
def team_create():
    auction_service = current_app.extensions["auction_service"]
    user = session.get("user") or {}
    if not user.get("global_player_id"):
        return jsonify({"ok": False, "error": "Account not linked to a player"}), 400
    try:
        team = auction_service.create_team_account(
            user["global_player_id"], request.form.get("name", ""))
        return jsonify({"ok": True, "team": team})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@banking_bp.post("/team/update")
@login_required()
def team_update():
    auction_service = current_app.extensions["auction_service"]
    user = session.get("user") or {}
    if not user.get("global_player_id"):
        return jsonify({"ok": False, "error": "Account not linked to a player"}), 400
    team_id = (request.form.get("team_id") or "").strip()
    team = auction_service.get_global_team(team_id)
    if not team or team.get("manager_player_id") != user["global_player_id"]:
        return jsonify({"ok": False, "error": "You don't manage this team"}), 403
    try:
        team = auction_service.update_team_profile(
            team_id,
            name=request.form.get("name"),
            logo=request.form.get("logo"),
            about=request.form.get("about"),
        )
        return jsonify({"ok": True, "team": team})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@banking_bp.post("/vault/lock")
@login_required()
def vault_lock():
    bank_service = current_app.extensions["bank_service"]
    user = session.get("user") or {}
    account = _my_account(bank_service, user)
    if not account:
        return jsonify({"ok": False, "error": "Account not linked to a player"}), 400
    try:
        account = bank_service.lock_to_vault(
            account["id"],
            request.form.get("season_id", ""),
            int(request.form.get("amount") or 0),
            reinvest=request.form.get("reinvest", "1") != "0",
        )
        return jsonify({"ok": True, "account": account})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@banking_bp.post("/vault/<position_id>/reinvest")
@login_required()
def vault_reinvest(position_id):
    bank_service = current_app.extensions["bank_service"]
    try:
        position = bank_service.set_reinvest(position_id, request.form.get("reinvest", "1") != "0")
        return jsonify({"ok": True, "position": position})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@banking_bp.post("/deposit")
@login_required()
def deposit():
    bank_service = current_app.extensions["bank_service"]
    user = session.get("user") or {}
    account = _my_account(bank_service, user)
    if not account:
        return jsonify({"ok": False, "error": "Account not linked to a player"}), 400
    try:
        amount = int(request.form.get("amount") or 0)
        if amount <= 0:
            raise ValueError("Amount must be positive")
        # Auto mode: deposits go straight into the vault of the latest season.
        season_id = request.form.get("season_id") or _latest_season_id()
        account = bank_service.credit(account["id"], amount,
                                      request.form.get("comment", ""),
                                      tx_type="deposit", season_id=season_id)
        return jsonify({"ok": True, "account": account})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@banking_bp.post("/auto")
@login_required()
def auto_toggle():
    bank_service = current_app.extensions["bank_service"]
    user = session.get("user") or {}
    account = _my_account(bank_service, user)
    if not account:
        return jsonify({"ok": False, "error": "Account not linked to a player"}), 400
    try:
        account = bank_service.set_auto(account["id"], request.form.get("auto", "1") != "0")
        return jsonify({"ok": True, "account": account})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
