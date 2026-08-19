"""Portfolio routes: private data API + public rankings / finance view."""
from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..authz import login_required
from ..db import row_to_dict, rows_to_dicts

portfolio_bp = Blueprint("portfolio", __name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _account_data(db, bank_service, user):
    """Return the logged-in user's account + portfolio aggregates."""
    if not user.get("global_player_id"):
        return None, {}
    account = bank_service.get_or_create_account("player", user["global_player_id"])
    if not account:
        return None, {}
    # transactions (for chart)
    txns = bank_service.transactions(account["id"], limit=500)
    # reverse to chronological for chart
    txns_chrono = list(reversed(txns))
    # vault positions
    vaults = bank_service.vault_positions(account["id"])
    # active wagers (money committed)
    wager_bets = []
    with db.read() as conn:
        rows = conn.execute(
            "SELECT b.*, w.title, w.status AS wager_status, w.side_a, w.side_b, "
            "w.winning_side "
            "FROM wager_bets b JOIN wagers w ON w.id = b.wager_id "
            "WHERE b.user_id = ? AND b.status = 'open' ORDER BY b.created_at DESC",
            (user["global_player_id"],),
        ).fetchall()
        wager_bets = rows_to_dicts(rows)

    # yield schedules for user's seasons
    yield_schedules = []
    with db.read() as conn:
        seasons_in = set()
        for v in vaults:
            seasons_in.add(v["season_id"])
        if seasons_in:
            placeholders = ",".join("?" for _ in seasons_in)
            rows = conn.execute(
                f"SELECT * FROM yield_schedules WHERE season_id IN ({placeholders}) "
                "ORDER BY match_number ASC",
                list(seasons_in),
            ).fetchall()
            yield_schedules = rows_to_dicts(rows)

    # stats
    liquid = int(account["liquid_cash"])
    locked = int(account["locked_capital"])
    wager_committed = sum(int(b["amount"]) for b in wager_bets)
    vault_earned = locked - sum(int(v["principal"]) for v in vaults)
    net_worth = liquid + locked + wager_committed

    return account, {
        "liquid": liquid,
        "locked": locked,
        "net_worth": net_worth,
        "wager_committed": wager_committed,
        "vault_earned": max(0, vault_earned),
        "txns": txns_chrono,
        "vaults": vaults,
        "wager_bets": wager_bets,
        "yield_schedules": yield_schedules,
    }


# ---------------------------------------------------------------------------
# Private — portfolio JSON (for the account page charts)
# ---------------------------------------------------------------------------

@portfolio_bp.get("/portfolio/data")
@login_required()
def portfolio_data():
    db = current_app.extensions["db"]
    bank_service = current_app.extensions["bank_service"]
    user = session.get("user") or {}
    account, data = _account_data(db, bank_service, user)
    if not account:
        return jsonify({"ok": False, "error": "Account not linked"}), 400
    # slim txns for charting
    chart_txns = [
        {"t": t["created_at"], "bal": t["balance_after"], "type": t["type"],
         "amount": t["amount"], "comment": t.get("comment", "")}
        for t in data["txns"]
    ]
    return jsonify({
        "ok": True,
        "liquid": data["liquid"],
        "locked": data["locked"],
        "net_worth": data["net_worth"],
        "wager_committed": data["wager_committed"],
        "vault_earned": data["vault_earned"],
        "txns": chart_txns,
        "vaults": data["vaults"],
        "wager_bets": data["wager_bets"],
        "yield_schedules": data["yield_schedules"],
    })


# ---------------------------------------------------------------------------
# Public — portfolio rankings + per-account view
# ---------------------------------------------------------------------------

@portfolio_bp.get("/portfolio")
def public_portfolio():
    """Public leaderboard of all accounts by net worth."""
    db = current_app.extensions["db"]
    bank_service = current_app.extensions["bank_service"]
    rankings = []
    with db.read() as conn:
        accounts = conn.execute(
            "SELECT * FROM bank_accounts WHERE owner_type = 'player' "
            "ORDER BY (liquid_cash + locked_capital) DESC"
        ).fetchall()
        for acct in accounts:
            owner_id = acct["owner_id"]
            gp = conn.execute(
                "SELECT name FROM global_players WHERE id = ?", (owner_id,)
            ).fetchone()
            name = gp["name"] if gp else owner_id[:8]
            # team info
            gt = conn.execute(
                "SELECT name, logo FROM global_teams WHERE manager_player_id = ?",
                (owner_id,),
            ).fetchone()
            # vault positions
            vaults = conn.execute(
                "SELECT * FROM vault_positions WHERE account_id = ?",
                (acct["id"],),
            ).fetchall()
            # active bets
            bets = conn.execute(
                "SELECT SUM(amount) AS total FROM wager_bets "
                "WHERE user_id = ? AND status = 'open'",
                (owner_id,),
            ).fetchone()
            wager_committed = int(bets["total"] or 0) if bets else 0
            liquid = int(acct["liquid_cash"])
            locked = int(acct["locked_capital"])
            net_worth = liquid + locked + wager_committed
            if net_worth <= 0 and liquid == 0 and locked == 0:
                continue  # skip empty unlinked accounts
            rankings.append({
                "owner_id": owner_id,
                "name": name,
                "team_name": gt["name"] if gt else None,
                "team_logo": gt["logo"] if gt else None,
                "liquid": liquid,
                "locked": locked,
                "wager_committed": wager_committed,
                "net_worth": net_worth,
                "is_team": gt is not None,
            })
    rankings.sort(key=lambda r: r["net_worth"], reverse=True)
    for i, r in enumerate(rankings, 1):
        r["rank"] = i
    # yield schedules
    yield_schedules = []
    with db.read() as conn:
        rows = conn.execute(
            "SELECT ys.*, s.name AS season_name FROM yield_schedules ys "
            "JOIN seasons s ON s.id = ys.season_id "
            "ORDER BY ys.scheduled_at ASC"
        ).fetchall()
        yield_schedules = rows_to_dicts(rows)
    return render_template(
        "portfolio/public.html",
        rankings=rankings,
        yield_schedules=yield_schedules,
    )


@portfolio_bp.get("/portfolio/<owner_id>")
def public_account_view(owner_id):
    """Public view of a single account's finances."""
    db = current_app.extensions["db"]
    bank_service = current_app.extensions["bank_service"]
    owner_id = (owner_id or "").strip()
    if not owner_id:
        return "Not found", 404
    account = bank_service.account_for_owner("player", owner_id)
    if not account:
        return "Account not found", 404
    # resolve name
    with db.read() as conn:
        gp = conn.execute(
            "SELECT name FROM global_players WHERE id = ?", (owner_id,)
        ).fetchone()
        gt = conn.execute(
            "SELECT * FROM global_teams WHERE manager_player_id = ?",
            (owner_id,),
        ).fetchone()
    name = gp["name"] if gp else owner_id[:8]
    team_name = gt["name"] if gt else None
    team_obj = dict(gt) if gt else None
    # public-safe transaction history (no balance_after)
    txns = bank_service.transactions(account["id"], limit=100)
    vaults = bank_service.vault_positions(account["id"])
    liquid = int(account["liquid_cash"])
    locked = int(account["locked_capital"])
    # wager exposure
    with db.read() as conn:
        bets = conn.execute(
            "SELECT b.*, w.title, w.status AS wager_status "
            "FROM wager_bets b JOIN wagers w ON w.id = b.wager_id "
            "WHERE b.user_id = ? AND b.status = 'open' "
            "ORDER BY b.created_at DESC",
            (owner_id,),
        ).fetchall()
        wager_bets = rows_to_dicts(bets)
    wager_committed = sum(int(b["amount"]) for b in wager_bets)
    net_worth = liquid + locked + wager_committed
    # yield schedules for this owner's seasons
    yield_schedules = []
    seasons_in = set(v["season_id"] for v in vaults)
    with db.read() as conn:
        if seasons_in:
            placeholders = ",".join("?" for _ in seasons_in)
            rows = conn.execute(
                f"SELECT * FROM yield_schedules WHERE season_id IN ({placeholders}) "
                "ORDER BY match_number ASC",
                list(seasons_in),
            ).fetchall()
            yield_schedules = rows_to_dicts(rows)
    branding = current_app.extensions["branding_service"]
    return render_template(
        "portfolio/public_account.html",
        owner_id=owner_id,
        name=name,
        team_name=team_name,
        team=team_obj,
        liquid=liquid,
        locked=locked,
        wager_committed=wager_committed,
        net_worth=net_worth,
        txns=txns,
        vaults=vaults,
        wager_bets=wager_bets,
        yield_schedules=yield_schedules,
        team_logo_url=branding.team_logo(team_obj) if team_obj else "",
    )
