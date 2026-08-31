"""Shared authorization helpers for internal routes."""

from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def role_required(roles):
    allowed_roles = frozenset(roles)

    def decorator(view_function):
        @wraps(view_function)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in allowed_roles:
                flash("Zugriff verweigert: Unzureichende Rechte.")
                return redirect(url_for("index"))
            return view_function(*args, **kwargs)
        return wrapped
    return decorator
