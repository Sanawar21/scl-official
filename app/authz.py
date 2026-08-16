from functools import wraps

from flask import current_app, redirect, request, session, url_for


def _derived_user(user):
    """Run a raw session user through the auth resolver (derived role/team)."""
    if not user:
        return None
    try:
        return current_app.extensions["auth_service"].user_view(user)
    except Exception:
        return user


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            if not user:
                return redirect(url_for("auth.login_page", next=request.path))
            if role:
                view = _derived_user(user)
                if not view or view.get("role") != role:
                    return redirect(url_for("viewer.home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_user():
    return _derived_user(session.get("user"))
