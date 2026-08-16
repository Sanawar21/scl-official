from flask import Blueprint, current_app, jsonify, render_template, request, session

from .. import emit_state
from .. import rules as R
from ..authz import login_required

manager_bp = Blueprint("manager", __name__, url_prefix="/manager")


def _my_team(season_id):
    auction_service = current_app.extensions["auction_service"]
    user = session.get("user") or {}
    if not user.get("team_id"):
        return None
    team = auction_service._get_team(season_id, user["team_id"])
    return team


def _user_season_id():
    user = session.get("user") or {}
    with current_app.extensions["db"].read() as conn:
        row = conn.execute(
            "SELECT t.season_id FROM teams t WHERE t.id = ?", (user.get("team_id"),)
        ).fetchone()
        return row["season_id"] if row else None


@manager_bp.get("")
@login_required(role=R.ROLE_MANAGER)
def dashboard():
    season_id = _user_season_id()
    if not season_id:
        return render_template("manager/dashboard.html", state=None, my_team=None,
                               trade_requests=None, error="No team assigned yet")
    auction_service = current_app.extensions["auction_service"]
    branding = current_app.extensions["branding_service"]
    team = auction_service._get_team(season_id, session["user"]["team_id"])
    state = auction_service.get_state(season_id)
    # Use the enriched team from state (has player_labels/bench_labels/wallet);
    # _get_team alone lacks the label lists the template renders.
    my_team = next((t for t in state["teams"] if t["id"] == team["id"]), team) if team else None
    if my_team:
        my_team["logo_url"] = branding.team_logo(my_team)
        my_team["banner_url"] = branding.team_banner(my_team)
    trade_requests = auction_service.get_trade_requests_for_team(season_id, team["id"])
    return render_template("manager/dashboard.html", state=state, my_team=my_team,
                           trade_requests=trade_requests, error=None)


@manager_bp.get("/state")
@login_required(role=R.ROLE_MANAGER)
def state_json():
    season_id = _user_season_id()
    if not season_id:
        return jsonify({"ok": False, "error": "No team assigned"}), 400
    auction_service = current_app.extensions["auction_service"]
    team = auction_service._get_team(season_id, session["user"]["team_id"])
    state = auction_service.get_state(season_id)
    state["my_team"] = team
    state["trade_requests"] = auction_service.get_trade_requests_for_team(season_id, team["id"])
    return jsonify(state)


@manager_bp.post("/bid")
@login_required(role=R.ROLE_MANAGER)
def bid():
    return _manager_action("bid", {"amount": request.form.get("amount", 0)})


@manager_bp.post("/pass")
@login_required(role=R.ROLE_MANAGER)
def pass_turn():
    return _manager_action("pass", {})


@manager_bp.post("/trade")
@login_required(role=R.ROLE_MANAGER)
def trade():
    return _manager_action("trade", {
        "to_team_id": request.form.get("to_team_id", ""),
        "offered_player_id": request.form.get("offered_player_id", ""),
        "requested_player_id": request.form.get("requested_player_id", "") or None,
        "cash_from_initiator": request.form.get("cash_from_initiator", 0),
        "cash_from_target": request.form.get("cash_from_target", 0),
    })


@manager_bp.post("/trade/respond")
@login_required(role=R.ROLE_MANAGER)
def trade_respond():
    return _manager_action("trade_respond", {
        "trade_id": request.form.get("trade_id", ""),
        "action": request.form.get("action", ""),
    })


def _manager_action(kind, payload):
    season_id = _user_season_id()
    if not season_id:
        return jsonify({"ok": False, "error": "No team assigned"}), 400
    auction_service = current_app.extensions["auction_service"]
    team = auction_service._get_team(season_id, session["user"]["team_id"])
    user = session.get("user") or {}
    actor = user.get("role") or "manager"
    try:
        if kind == "bid":
            result = auction_service.place_bid(season_id, team["id"], int(payload.get("amount") or 0), actor=actor)
        elif kind == "pass":
            result = auction_service.pass_current(season_id, team["id"], actor=actor)
        elif kind == "trade":
            result = auction_service.request_trade(
                season_id, team["id"], payload["to_team_id"],
                payload["offered_player_id"], payload["requested_player_id"],
                int(payload.get("cash_from_initiator") or 0),
                int(payload.get("cash_from_target") or 0), actor=actor,
            )
        else:
            result = auction_service.respond_trade(
                season_id, payload["trade_id"], team["id"], payload["action"], actor=actor
            )
        emit_state(season_id)
        return jsonify({"ok": True, "result": result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
