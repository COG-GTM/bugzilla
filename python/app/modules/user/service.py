"""Port of the login/logout/valid_login part of Bugzilla::WebService::User.

The rest of the User module (get/create/update/...) is a separate migration unit.
"""
from app.core.auth import authenticate, issue_login_token, logout_all
from app.core.errors import param_required
from app.core.rest import Ctx


def login(ctx: Ctx) -> dict:
    user = ctx.user
    if user.is_logged_in:
        token = f"{user.id}-{user.login_token}" if user.login_token else issue_login_token(ctx.db, user)
        return {"id": user.id, "token": token}
    for name in ("login", "password"):
        if ctx.params.get(name) is None and ctx.params.get("Bugzilla_" + name) is None:
            raise param_required(name)
    creds = {"Bugzilla_login": ctx.params.get("Bugzilla_login", ctx.params.get("login")),
             "Bugzilla_password": ctx.params.get("Bugzilla_password", ctx.params.get("password"))}
    user = authenticate(ctx.db, creds)
    restrict = str(ctx.params.get("restrict_login", "0")) in ("1", "true", "True")
    return {"id": user.id, "token": issue_login_token(ctx.db, user, ctx.params.get("_remote_ip"), restrict)}


def logout(ctx: Ctx) -> dict:
    logout_all(ctx.db, ctx.user)
    return {"result": None}


def valid_login(ctx: Ctx) -> dict:
    login_name = ctx.params.get("login")
    if login_name is None:
        raise param_required("login")
    user = ctx.user
    return {"result": bool(user.is_logged_in and user.login == str(login_name))}
