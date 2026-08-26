from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from .. import rules as R
from ..authz import login_required

wagers_bp = Blueprint("wagers", __name__, url_prefix="/wagers")


def _wager_service():
    return current_app.extensions["wager_service"]


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_probability(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("Probability must be a number")


def _user_is_linked(user):
    return bool(user and user.get("global_player_id"))


def _redirect_back(default="wagers.board", **kwargs):
    nxt = request.form.get("next") or request.args.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect(url_for(default, **kwargs))


@wagers_bp.get("")
def board():
    svc = _wager_service()
    user = session.get("user")
    wagers = svc.list_wagers()
    my_bets = svc.my_bets(user["id"]) if user and user.get("id") else []
    account = None
    if _user_is_linked(user):
        account = svc.bank.get_or_create_account("player", user["global_player_id"])
    seasons = current_app.extensions["auction_service"].list_seasons()
    return render_template("wagers/board.html", wagers=wagers, my_bets=my_bets,
                           account=account, seasons=seasons, user=user)


@wagers_bp.get("/live")
def live_board():
    """JSON snapshot of every wager with live house coverage — polled by the
    board so the house guarantee auto-adjusts as new bids land."""
    svc = _wager_service()
    wagers = svc.list_wagers()
    return jsonify([{
        "id": w["id"], "title": w["title"], "status": w["status"],
        "side_a": w["side_a"], "side_b": w["side_b"],
        "yes_total": w["yes_total"], "no_total": w["no_total"],
        "pot": w["pot"], "house_injected": w["house_injected"],
        "house_probability": w["house_probability"],
        "cover_a": w["cover_a"], "cover_b": w["cover_b"],
        "bet_count": w["bet_count"],
    } for w in wagers])


@wagers_bp.get("/<wager_id>")
def detail(wager_id):
    svc = _wager_service()
    wager = svc.get_wager(wager_id)
    if not wager:
        flash("Wager not found.", "error")
        return redirect(url_for("wagers.board"))
    user = session.get("user")
    account = None
    if _user_is_linked(user):
        account = svc.bank.get_or_create_account("player", user["global_player_id"])
    return render_template("wagers/detail.html", wager=wager, account=account, user=user)


@wagers_bp.post("")
@login_required()
def create():
    svc = _wager_service()
    user = session.get("user") or {}
    form = request.form
    try:
        svc.create_wager(
            user,
            form.get("title", ""),
            form.get("description", ""),
            form.get("side_a", "Yes"),
            form.get("side_b", "No"),
            (form.get("side") or "Yes").strip(),
            _safe_int(form.get("amount"), 0),
            season_id=(form.get("season_id") or "").strip() or None,
        )
        flash("Market proposed. Awaiting admin calibration.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back("wagers.board")


@wagers_bp.get("/<wager_id>/live")
def live_detail(wager_id):
    """JSON snapshot of a single wager for the detail page's live poller."""
    svc = _wager_service()
    w = svc.get_wager(wager_id)
    if not w:
        return jsonify({"error": "Wager not found"}), 404
    return jsonify({
        "id": w["id"], "title": w["title"], "status": w["status"],
        "side_a": w["side_a"], "side_b": w["side_b"],
        "yes_total": w["yes_total"], "no_total": w["no_total"],
        "pot": w["pot"], "house_injected": w["house_injected"],
        "house_probability": w["house_probability"],
        "cover_a": w["cover_a"], "cover_b": w["cover_b"],
        "bet_count": len(w["bets"]),
    })


@wagers_bp.post("/<wager_id>/bet")
@login_required()
def bet(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.place_bet(user, wager_id, (request.form.get("side") or "").strip(),
                      _safe_int(request.form.get("amount"), 0))
        flash("Stake placed.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_back("wagers.detail", wager_id=wager_id)


# --------------------------------------------------------------------------
# admin control room
# --------------------------------------------------------------------------
@wagers_bp.get("/admin")
@login_required(role=R.ROLE_ADMIN)
def admin():
    svc = _wager_service()
    auction = current_app.extensions["auction_service"]
    wagers = svc.list_wagers()
    for w in wagers:
        w["bets"] = svc.get_wager(w["id"])["bets"]
    house = svc.house_account()
    global_players = auction.list_global_players()
    seasons = auction.list_seasons()
    return render_template("wagers/admin.html", wagers=wagers, house=house,
                           global_players=global_players, seasons=seasons)


@wagers_bp.post("/admin/<wager_id>/calibrate")
@login_required(role=R.ROLE_ADMIN)
def admin_calibrate(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        estimate = _safe_probability(request.form.get("estimate", ""))
        wager = svc.calibrate(wager_id, user.get("username") or "admin", estimate)
        flash(f"Estimate recorded. Consensus probability: {round(wager['house_probability'], 2)}%.",
              "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/finalize")
@login_required(role=R.ROLE_ADMIN)
def admin_finalize(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.finalize_calibration(wager_id, user.get("username") or "admin")
        flash("Calibration locked; betting opened.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/veto")
@login_required(role=R.ROLE_ADMIN)
def admin_veto(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.veto(wager_id, user.get("username") or "admin", request.form.get("reason", ""))
        flash("Market vetoed (bankruptcy veto); all stakes refunded.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/freeze")
@login_required(role=R.ROLE_ADMIN)
def admin_freeze(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.freeze(wager_id, user.get("username") or "admin")
        flash("Pools frozen.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/unfreeze")
@login_required(role=R.ROLE_ADMIN)
def admin_unfreeze(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.unfreeze(wager_id, user.get("username") or "admin")
        flash("Pools reopened.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/bets/<bet_id>/remove")
@login_required(role=R.ROLE_ADMIN)
def admin_remove_bet(wager_id, bet_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.remove_bet(wager_id, bet_id, user.get("username") or "admin")
        flash("Bet removed; stake refunded.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/inject")
@login_required(role=R.ROLE_ADMIN)
def admin_inject(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.inject_house(wager_id, user.get("username") or "admin",
                         _safe_int(request.form.get("amount"), 0))
        flash("House funds injected into the pot.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/resolve")
@login_required(role=R.ROLE_ADMIN)
def admin_resolve(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.resolve(wager_id, user.get("username") or "admin",
                    (request.form.get("winning_side") or "").strip())
        flash("Market resolved; winners paid.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/void")
@login_required(role=R.ROLE_ADMIN)
def admin_void(wager_id):
    svc = _wager_service()
    user = session.get("user") or {}
    try:
        svc.void(wager_id, user.get("username") or "admin", request.form.get("reason", ""))
        flash("Market voided; all stakes refunded 100%.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/create")
@login_required(role=R.ROLE_ADMIN)
def admin_create():
    """Admin creates a wager directly (no player opening stake required)."""
    svc = _wager_service()
    user = session.get("user") or {}
    form = request.form
    try:
        svc.admin_create_wager(
            actor=user.get("username") or "admin",
            title=form.get("title", ""),
            description=form.get("description", ""),
            side_a=form.get("side_a", "Yes"),
            side_b=form.get("side_b", "No"),
            season_id=(form.get("season_id") or "").strip() or None,
        )
        flash("Wager created.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))


@wagers_bp.post("/admin/<wager_id>/bet-behalf")
@login_required(role=R.ROLE_ADMIN)
def admin_bet_behalf(wager_id):
    """Admin places a bet on behalf of a player (deducts from their wallet)."""
    svc = _wager_service()
    user = session.get("user") or {}
    form = request.form
    try:
        svc.admin_bet_on_behalf(
            actor=user.get("username") or "admin",
            wager_id=wager_id,
            global_player_id=form.get("global_player_id", ""),
            side=form.get("side", ""),
            amount=_safe_int(form.get("amount"), 0),
        )
        flash("Bet placed on player's behalf.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("wagers.admin"))
