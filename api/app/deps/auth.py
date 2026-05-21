"""FastAPI dependencies for authentication.

Provides the equivalent of ``Bugzilla->login()`` / ``Bugzilla->user`` that
can be injected into any route via ``Depends``.

Authentication is accepted via:
  1. ``Bugzilla_login`` + ``Bugzilla_password`` query params (legacy compat)
  2. ``login`` + ``password`` query params
  3. ``Bugzilla_token`` query/header param (session token from ``/login``)
  4. ``X-BUGZILLA-API-KEY`` header
"""

from datetime import datetime, timezone

from fastapi import Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import BugzillaApiError
from app.models.user import LoginCookie, Profile


def _verify_password(plain: str, hashed: str) -> bool:
    """Check a plain-text password against the stored crypt hash.

    Bugzilla stores passwords in several formats:
      - Modern: ``salt,b64digest{ALGORITHM}`` (e.g. SHA-256)
      - Legacy: standard crypt() output (des/md5/sha/bcrypt)

    This mirrors ``bz_crypt`` from ``Bugzilla/Util.pm`` (lines 683-730).
    """
    import base64
    import crypt as _crypt
    import hashlib
    import re

    m = re.search(r"\{([^}]+)\}$", hashed)
    if m:
        algorithm = m.group(1)
        prefix = hashed[: m.start()]
        salt, _, stored_digest = prefix.partition(",")
        if not stored_digest:
            return False
        algo_map = {"SHA-256": "sha256", "SHA-512": "sha512"}
        hash_name = algo_map.get(algorithm)
        if hash_name is None:
            return False
        h = hashlib.new(hash_name)
        h.update(plain.encode("utf-8"))
        h.update(salt.encode("utf-8"))
        computed = base64.b64encode(h.digest()).decode("utf-8").rstrip("=")
        return computed == stored_digest

    try:
        return _crypt.crypt(plain, hashed) == hashed
    except Exception:
        return False


def _authenticate_by_credentials(
    db: Session, login: str, password: str
) -> Profile:
    """Validate login + password, return the Profile or raise."""
    stmt = select(Profile).where(Profile.login_name == login)
    user = db.execute(stmt).scalar_one_or_none()
    if user is None or not _verify_password(password, user.cryptpassword or ""):
        raise BugzillaApiError(
            "invalid_login_or_password",
            "The username or password you entered is not valid.",
        )
    if not user.is_enabled:
        raise BugzillaApiError("account_disabled", "Your account has been disabled.")
    return user


def _authenticate_by_token(db: Session, token: str) -> Profile:
    """Validate a ``userid-cookie`` session token."""
    parts = token.split("-", 1)
    if len(parts) != 2:
        raise BugzillaApiError("auth_invalid_token", "The token is invalid or has expired.")
    user_id_str, cookie_val = parts
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise BugzillaApiError("auth_invalid_token", "The token is invalid or has expired.")

    stmt = select(LoginCookie).where(
        LoginCookie.cookie == cookie_val, LoginCookie.userid == user_id
    )
    lc = db.execute(stmt).scalar_one_or_none()
    if lc is None:
        raise BugzillaApiError("auth_invalid_token", "The token is invalid or has expired.")

    stmt = select(Profile).where(Profile.userid == user_id)
    user = db.execute(stmt).scalar_one_or_none()
    if user is None or not user.is_enabled:
        raise BugzillaApiError("account_disabled", "Your account has been disabled.")

    lc.lastused = datetime.now(timezone.utc)
    db.commit()

    return user


def get_current_user_optional(
    db: Session = Depends(get_db),
    login: str | None = Query(None, alias="login"),
    password: str | None = Query(None, alias="password"),
    bugzilla_login: str | None = Query(None, alias="Bugzilla_login"),
    bugzilla_password: str | None = Query(None, alias="Bugzilla_password"),
    bugzilla_token: str | None = Query(None, alias="Bugzilla_token"),
    api_key: str | None = Query(None, alias="Bugzilla_api_key"),
) -> Profile | None:
    """Return the authenticated user, or ``None`` for anonymous access.

    This mirrors the behaviour of ``Bugzilla->login()`` when login is not
    required — it will authenticate if credentials are present, but will
    not error if they are absent.
    """
    effective_login = bugzilla_login or login
    effective_password = bugzilla_password or password
    effective_token = bugzilla_token or api_key

    if effective_login and effective_password:
        return _authenticate_by_credentials(db, effective_login, effective_password)
    if effective_token:
        return _authenticate_by_token(db, effective_token)
    return None


def get_current_user_required(
    user: Profile | None = Depends(get_current_user_optional),
) -> Profile:
    """Require authentication — raises 401 if no valid credentials supplied."""
    if user is None:
        raise BugzillaApiError(
            "login_required",
            "You must log in before using this part of Bugzilla.",
        )
    return user


def user_in_group(user: Profile, group_name: str) -> bool:
    """Check whether *user* belongs to *group_name*."""
    for membership in user.group_memberships:
        if not membership.isbless and membership.group and membership.group.name == group_name:
            return True
    return False
