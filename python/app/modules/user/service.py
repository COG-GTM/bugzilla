"""User.login / User.logout / User.valid_login — the auth subset every module depends on.
The rest of Bugzilla/WebService/User.pm belongs to the User migration unit."""

from __future__ import annotations

from app.core import auth
from app.core.request import Ctx


def login(ctx: Ctx) -> dict:
    # authenticate() already consumed the credentials; re-read them from the raw request
    # so the required-parameter checks see what the client sent.
    raw = dict(ctx.request.query_params)
    raw.update({k: v for k, v in ctx.params.items()})
    if ctx.user.is_logged_in:
        out = {"id": ctx.user.id}
        if ctx.user.login_token:
            out["token"] = f"{ctx.user.id}-{ctx.user.login_token}"
        return out
    for name in ("login", "password"):
        if raw.get(name) is None and raw.get("Bugzilla_" + name) is None:
            raise auth.param_required(name)
    # credentials were present but did not authenticate -> Perl throws 300 from Bugzilla->login
    raise auth.user_error("invalid_username_or_password")


def logout(ctx: Ctx) -> None:
    auth.logout_endpoint(ctx.session, ctx.user)
    return None


def valid_login(ctx: Ctx) -> bool:
    return auth.valid_login_endpoint(ctx.session, ctx.params, ctx.user)
