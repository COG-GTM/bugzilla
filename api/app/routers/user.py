"""User API router — authentication endpoints.

Ticket 1: ``/login``, ``/logout``, ``/valid_login``

Route definitions mirror ``Bugzilla/WebService/Server/REST/Resources/User.pm``
and method logic is ported from ``Bugzilla/WebService/User.pm``.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import BugzillaApiError
from app.deps.auth import _authenticate_by_credentials, get_current_user_optional
from app.models.user import LoginCookie, Profile
from app.schemas.user import LoginResponse, ValidLoginResponse

router = APIRouter(tags=["User"])


def _generate_token() -> str:
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# GET /login — port of User.pm login() (lines 56-73)
# ---------------------------------------------------------------------------
@router.get(
    "/login",
    response_model=LoginResponse,
    summary="Log in and obtain a session token",
    responses={401: {"description": "Invalid credentials"}},
)
def login(
    db: Session = Depends(get_db),
    login: str | None = Query(None, description="User login name (email)"),
    password: str | None = Query(None, description="User password"),
    Bugzilla_login: str | None = Query(None, description="Alias for login"),
    Bugzilla_password: str | None = Query(None, description="Alias for password"),
    restrict_login: bool | None = Query(None, description="Restrict token to this IP"),
    current_user: Profile | None = Depends(get_current_user_optional),
) -> LoginResponse:
    """Authenticate and return ``{id, token}``.

    If the caller is already authenticated (via token/cookie), the existing
    user's info is returned without requiring login/password again — matching
    the Perl API behaviour (User.pm line 60-63).
    """
    if current_user is not None:
        return _login_to_response(db, current_user)

    effective_login = Bugzilla_login or login
    effective_password = Bugzilla_password or password

    if not effective_login or not effective_password:
        raise BugzillaApiError("param_required", "A 'login' and 'password' parameter is required.")

    user = _authenticate_by_credentials(db, effective_login, effective_password)
    return _login_to_response(db, user)


def _login_to_response(db: Session, user: Profile) -> LoginResponse:
    """Create a session token and return the login response.

    Mirrors ``_login_to_hash`` in User.pm (lines 412-419).
    """
    cookie_value = _generate_token()
    lc = LoginCookie(
        cookie=cookie_value,
        userid=user.userid,
        lastused=datetime.now(timezone.utc),
    )
    db.add(lc)
    db.commit()

    token = f"{user.userid}-{cookie_value}"
    return LoginResponse(id=user.userid, token=token)


# ---------------------------------------------------------------------------
# GET /logout — port of User.pm logout() (lines 75-78)
# ---------------------------------------------------------------------------
@router.get(
    "/logout",
    status_code=204,
    summary="Log out the current user",
    responses={204: {"description": "Logged out successfully"}},
)
def logout(
    db: Session = Depends(get_db),
    current_user: Profile | None = Depends(get_current_user_optional),
    Bugzilla_token: str | None = Query(None, description="Session token to invalidate"),
) -> None:
    """Invalidate the current session token.

    Does nothing if there is no user logged in — matching the Perl
    behaviour (User.pm line 77).
    """
    if current_user is None or Bugzilla_token is None:
        return None

    parts = Bugzilla_token.split("-", 1)
    if len(parts) == 2:
        cookie_val = parts[1]
        lc = db.get(LoginCookie, cookie_val)
        if lc is not None and lc.userid == current_user.userid:
            db.delete(lc)
            db.commit()
    return None


# ---------------------------------------------------------------------------
# GET /valid_login — port of User.pm valid_login() (lines 80-89)
# ---------------------------------------------------------------------------
@router.get(
    "/valid_login",
    response_model=ValidLoginResponse,
    summary="Check whether a login token is still valid",
)
def valid_login(
    login: str = Query(..., description="Login name to validate against"),
    current_user: Profile | None = Depends(get_current_user_optional),
) -> ValidLoginResponse:
    """Return ``true`` if the current credentials belong to *login*.

    Port of ``valid_login()`` from User.pm (lines 80-89).
    """
    if current_user is not None and current_user.login_name == login:
        return ValidLoginResponse(result=True)
    return ValidLoginResponse(result=False)
