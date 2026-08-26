import secrets

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from .. import rules as R
from ..authz import login_required

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.get("/login")
def login_page():
    return render_template("auth/login.html")


@auth_bp.post("/login")
def login():
    auth_service = current_app.extensions["auth_service"]
    user = auth_service.login(request.form.get("username", ""), request.form.get("password", ""))
    if not user:
        flash("Invalid username or password", "error")
        return redirect(url_for("auth.login_page"))
    session["user"] = user
    nxt = request.form.get("next") or ""
    if nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    view = current_app.extensions["auth_service"].user_view(user)
    if view["role"] == R.ROLE_ADMIN:
        return redirect(url_for("admin.dashboard"))
    # Managers with a team in a season go to their team hub; everyone else
    # (players, and managers whose team isn't in a season yet) lands on the
    # account page where team profiles live.
    if view["role"] == R.ROLE_MANAGER and view.get("season_id"):
        return redirect(url_for("manager.dashboard"))
    return redirect(url_for("banking.account"))


@auth_bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("viewer.home"))


@auth_bp.get("/forgot")
def forgot_page():
    return render_template("auth/forgot.html")


@auth_bp.post("/forgot")
def forgot():
    """Password recovery entry point. The app has no email system, so the flow
    hands the player to the admin: the admin resets the password from the
    admin link page and shares it out-of-band."""
    auth_service = current_app.extensions["auth_service"]
    username = (request.form.get("username", "") or "").strip()
    user = auth_service.get_by_username(username) if username else None
    if not user:
        flash("No account found with that username. Double-check the spelling, or sign up.", "error")
        return redirect(url_for("auth.forgot_page"))
    if user["role"] == R.ROLE_ADMIN:
        flash("The administrator account is managed through the server configuration (.env).", "info")
        return redirect(url_for("auth.forgot_page"))
    flash(f"Found the account '{user['username']}'. Ask the admin to reset its password "
          "(Admin → Link accounts → Reset password), then log in with the new one.", "info")
    return redirect(url_for("auth.forgot_page"))


@auth_bp.get("/signup")
def signup_page():
    return render_template("auth/signup.html")


@auth_bp.post("/signup")
def signup():
    auth_service = current_app.extensions["auth_service"]
    try:
        user = auth_service.signup(
            request.form.get("username", ""),
            request.form.get("password", ""),
            request.form.get("display_name", ""),
        )
        flash("Account created. Ask an admin to link it to your player profile.", "success")
        session["user"] = user
        return redirect(url_for("banking.account"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("auth.signup_page"))


# --- admin linking -------------------------------------------------------
@auth_bp.get("/admin/link")
@login_required(role=R.ROLE_ADMIN)
def link_page():
    auth_service = current_app.extensions["auth_service"]
    auction_service = current_app.extensions["auction_service"]
    unlinked = auth_service.list_unlinked_users()
    global_players = auction_service.list_global_players()
    linked = []
    with current_app.extensions["db"].read() as conn:
        rows = conn.execute(
            "SELECT u.username, u.display_name, u.role, u.id AS user_id, "
            "g.name AS player_name, g.tier, g.id AS global_player_id "
            "FROM users u LEFT JOIN global_players g ON g.id = u.global_player_id "
            "WHERE u.role != 'admin' ORDER BY u.username"
        ).fetchall()
        linked = [dict(r) for r in rows]
        # Attach bank account info (auto_vault, account_id) for linked players
        for row in linked:
            gp_id = row.get("global_player_id")
            if gp_id:
                acct = conn.execute(
                    "SELECT id, auto_vault FROM bank_accounts "
                    "WHERE owner_type = 'player' AND owner_id = ?", (gp_id,)
                ).fetchone()
                if acct:
                    row["account_id"] = acct["id"]
                    row["auto_vault"] = bool(acct["auto_vault"])
    for row in linked:
        view = auth_service.user_view({"id": row["user_id"], "role": row["role"],
                                       "global_player_id": row["global_player_id"]})
        row["role"] = view["role"] if view else row["role"]
    return render_template("admin/link.html", unlinked=unlinked,
                           global_players=global_players, linked=linked)


@auth_bp.post("/admin/link/<user_id>")
@login_required(role=R.ROLE_ADMIN)
def link_user(user_id):
    auth_service = current_app.extensions["auth_service"]
    global_player_id = request.form.get("global_player_id", "")
    try:
        user = auth_service.link_user_to_player(user_id, global_player_id)
        flash(f"Linked {user['username']} to a player profile.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("auth.link_page"))


@auth_bp.post("/admin/unlink/<user_id>")
@login_required(role=R.ROLE_ADMIN)
def unlink_user(user_id):
    current_app.extensions["auth_service"].unlink_user(user_id)
    flash("Account unlinked.", "success")
    return redirect(url_for("auth.link_page"))


@auth_bp.post("/admin/reset-password/<user_id>")
@login_required(role=R.ROLE_ADMIN)
def reset_password(user_id):
    """Admin sets (or generates) a new password for a player account."""
    auth_service = current_app.extensions["auth_service"]
    new_password = request.form.get("new_password", "")
    action = request.form.get("action", "set")
    try:
        if action == "generate":
            new_password = secrets.token_urlsafe(9)
        user = auth_service.reset_password(user_id, new_password)
        if action == "generate":
            flash(f"Generated a new password for {user['username']}: {new_password} "
                  "— share it with the player.", "success")
        else:
            flash(f"Password reset for {user['username']}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("auth.link_page"))



