from functools import wraps

from flask import redirect, request, session, url_for


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            if not user:
                return redirect(url_for("auth.login_page", next=request.path))
            if role and user.get("role") != role:
                return redirect(url_for("viewer.home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def current_user():
    return session.get("user")
