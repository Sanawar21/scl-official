from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ..authz import login_required
from ..db import row_to_dict

banking_bp = Blueprint("banking", __name__, url_prefix="/account")


def _my_account(bank_service, user):
    if not user.get("global_player_id"):
        return None
    return bank_service.get_or_create_account("player", user["global_player_id"])


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
            my_team = conn.execute(
                "SELECT id, name, season_id, purse_remaining FROM teams "
                "WHERE manager_player_id = ? LIMIT 1", (user["global_player_id"],)).fetchone()
    return render_template("banking/account.html", account=account,
                           vault_positions=vault_positions, seasons=seasons, txns=txns,
                           finalized_counts=finalized_counts, my_team=dict(my_team) if my_team else None)


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
        account = bank_service.adjust(account["id"], amount, request.form.get("comment", ""),
                                      tx_type="deposit")
        return jsonify({"ok": True, "account": account})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
