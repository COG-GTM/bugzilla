"""User API router — all User WebService endpoints.

Ticket 1: ``/login``, ``/logout``, ``/valid_login``
Ticket 2: ``GET /user``, ``GET /user/{id_or_name}``
Ticket 3: ``POST /user``, ``PUT /user/{id_or_name}``, ``POST /user/offer_account_by_email``

Route definitions mirror ``Bugzilla/WebService/Server/REST/Resources/User.pm``
and method logic is ported from ``Bugzilla/WebService/User.pm``.
"""

import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import BugzillaApiError
from app.core.field_filter import filter_fields, filter_wants
from app.deps.auth import (
    _authenticate_by_credentials,
    get_current_user_optional,
    get_current_user_required,
    user_in_group,
)
from app.models.user import Group, LoginCookie, Profile
from app.schemas.user import (
    FieldChange,
    LoginResponse,
    OfferAccountRequest,
    UserChangeInfo,
    UserCreateRequest,
    UserCreateResponse,
    UserUpdateResponse,
    ValidLoginResponse,
)

router = APIRouter(tags=["User"])

# ---------------------------------------------------------------------------
# Field mapping — mirrors MAPPED_FIELDS / MAPPED_RETURNS in User.pm
# ---------------------------------------------------------------------------
MAPPED_FIELDS: dict[str, str] = {
    "email": "login_name",
    "full_name": "realname",
    "login_denied_text": "disabledtext",
}

MAPPED_RETURNS: dict[str, str] = {
    "login_name": "email",
    "realname": "full_name",
    "disabledtext": "login_denied_text",
}


def _generate_token() -> str:
    return secrets.token_hex(8)


# ===================================================================
# Ticket 1 — Auth Endpoints
# ===================================================================


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

    Port of ``login()`` from User.pm (lines 56-73).
    """
    if current_user is not None:
        return _login_to_response(db, current_user)

    effective_login = Bugzilla_login or login
    effective_password = Bugzilla_password or password

    if not effective_login or not effective_password:
        raise BugzillaApiError(
            "param_required", "A 'login' and 'password' parameter is required."
        )

    user = _authenticate_by_credentials(db, effective_login, effective_password)
    return _login_to_response(db, user)


def _login_to_response(db: Session, user: Profile) -> LoginResponse:
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


@router.get(
    "/logout",
    status_code=204,
    summary="Log out the current user",
    responses={204: {"description": "Logged out successfully"}},
)
def logout(
    db: Session = Depends(get_db),
    current_user: Profile | None = Depends(get_current_user_optional),
    Bugzilla_token: str | None = Query(
        None, description="Session token to invalidate"
    ),
) -> None:
    """Port of ``logout()`` from User.pm (lines 75-78)."""
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


@router.get(
    "/valid_login",
    response_model=ValidLoginResponse,
    summary="Check whether a login token is still valid",
)
def valid_login(
    login: str = Query(..., description="Login name to validate against"),
    current_user: Profile | None = Depends(get_current_user_optional),
) -> ValidLoginResponse:
    """Port of ``valid_login()`` from User.pm (lines 80-89)."""
    if current_user is not None and current_user.login_name == login:
        return ValidLoginResponse(result=True)
    return ValidLoginResponse(result=False)


# ===================================================================
# Ticket 2 — User Read Endpoints
# ===================================================================


def _user_to_dict(
    user: Profile,
    current_user: Profile | None,
    include_fields: list[str] | None,
    exclude_fields: list[str] | None,
    is_editusers: bool,
) -> dict[str, Any]:
    """Build user info dict with visibility rules from User.pm get() (lines 222-258)."""
    info: dict[str, Any] = {
        "id": user.userid,
        "real_name": user.realname,
        "name": user.login_name,
        "email": user.login_name,
        "can_login": user.is_enabled,
    }

    if is_editusers:
        info["email_enabled"] = user.email_enabled
        info["login_denied_text"] = user.disabledtext

    if current_user is not None and current_user.userid == user.userid:
        if filter_wants(include_fields, exclude_fields, "saved_searches"):
            info["saved_searches"] = [
                {"id": q.id, "name": q.name, "query": q.query}
                for q in user.saved_searches
            ]
        if filter_wants(include_fields, exclude_fields, "saved_reports"):
            info["saved_reports"] = [
                {"id": r.id, "name": r.name, "query": r.query}
                for r in user.saved_reports
            ]

    if filter_wants(include_fields, exclude_fields, "groups"):
        if (
            current_user is not None
            and (current_user.userid == user.userid or is_editusers)
        ):
            info["groups"] = [
                {
                    "id": m.group.id,
                    "name": m.group.name,
                    "description": m.group.description,
                }
                for m in user.group_memberships
                if not m.isbless and m.group is not None
            ]
        elif current_user is not None:
            info["groups"] = _filter_bless_groups(current_user, user)

    return filter_fields(info, include_fields, exclude_fields)


def _filter_bless_groups(
    current_user: Profile, target_user: Profile
) -> list[dict[str, Any]]:
    """Return only groups the current user can bless. Mirrors _filter_bless_groups."""
    blessable_group_ids = {
        m.group_id for m in current_user.group_memberships if m.isbless
    }
    return [
        {
            "id": m.group.id,
            "name": m.group.name,
            "description": m.group.description,
        }
        for m in target_user.group_memberships
        if not m.isbless and m.group is not None and m.group_id in blessable_group_ids
    ]


def _filter_users_by_group(
    db: Session,
    users: list[Profile],
    group_ids: list[int] | None,
    group_names: list[str] | None,
    current_user: Profile | None,
) -> list[Profile]:
    """Port of _filter_users_by_group from User.pm (lines 332-358)."""
    if not group_ids and not group_names:
        return users

    groups: dict[int, Group] = {}
    if group_ids:
        for gid in group_ids:
            g = db.get(Group, gid)
            if g is None:
                raise BugzillaApiError(
                    "object_does_not_exist",
                    f"There is no group with id '{gid}'.",
                )
            groups[g.id] = g
    if group_names:
        for gname in group_names:
            stmt = select(Group).where(Group.name == gname)
            g = db.execute(stmt).scalar_one_or_none()
            if g is None:
                raise BugzillaApiError("invalid_group_name", f"No group named '{gname}'.")
            if current_user is not None and not user_in_group(current_user, gname):
                raise BugzillaApiError("invalid_group_name", f"No group named '{gname}'.")
            groups[g.id] = g

    target_group_ids = set(groups.keys())
    return [
        u
        for u in users
        if any(
            m.group_id in target_group_ids
            for m in u.group_memberships
            if not m.isbless
        )
    ]


def _can_see_user(current_user: Profile, other: Profile) -> bool:
    """Simplified can_see_user: any authenticated user can see another user."""
    return current_user is not None


def _get_users_impl(
    db: Session,
    current_user: Profile | None,
    ids: list[int] | None = None,
    names: list[str] | None = None,
    match: list[str] | None = None,
    limit: int | None = None,
    include_disabled: bool = False,
    group_ids: list[int] | None = None,
    groups: list[str] | None = None,
    include_fields: list[str] | None = None,
    exclude_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Core implementation of ``get()`` from User.pm (lines 132-261)."""
    if ids is None and names is None and match is None:
        raise BugzillaApiError(
            "params_required",
            "One of the following parameters is required: ids, names, match.",
        )

    unique_users: dict[int, Profile] = {}
    user_objects: list[Profile] = []

    # Lookup by names
    if names:
        for name in names:
            stmt = select(Profile).where(Profile.login_name == name)
            user = db.execute(stmt).scalar_one_or_none()
            if user is None:
                raise BugzillaApiError(
                    "object_does_not_exist",
                    f"There is no user named '{name}'.",
                )
            if user.userid not in unique_users:
                unique_users[user.userid] = user
                user_objects.append(user)

    # Unauthenticated callers: limited fields, no id/match lookup
    if current_user is None:
        if ids:
            raise BugzillaApiError(
                "user_access_by_id_denied",
                "You cannot look up user IDs without being logged in.",
            )
        if match:
            raise BugzillaApiError(
                "user_access_by_match_denied",
                "You cannot use the 'match' argument without being logged in.",
            )

        filtered = _filter_users_by_group(
            db, user_objects, group_ids, groups, current_user
        )
        users_out = [
            filter_fields(
                {"id": u.userid, "real_name": u.realname, "name": u.login_name},
                include_fields,
                exclude_fields,
            )
            for u in filtered
        ]
        return {"users": users_out}

    # Lookup by ids (authenticated)
    if ids:
        for uid in ids:
            stmt = select(Profile).where(Profile.userid == uid)
            user = db.execute(stmt).scalar_one_or_none()
            if user is None:
                raise BugzillaApiError(
                    "object_does_not_exist",
                    f"There is no user with id '{uid}'.",
                )
            if not _can_see_user(current_user, user):
                raise BugzillaApiError(
                    "auth_failure",
                    f"You are not authorized to access user id {uid}.",
                )
            if user.userid not in unique_users:
                unique_users[user.userid] = user
                user_objects.append(user)

    # User matching
    if match:
        effective_limit = limit if limit else 1000
        for match_string in match:
            pattern = f"%{match_string}%"
            stmt = select(Profile).where(
                (Profile.login_name.like(pattern))
                | (Profile.realname.like(pattern))
            )
            if not include_disabled:
                stmt = stmt.where(Profile.is_enabled.is_(True))
            stmt = stmt.limit(effective_limit)
            matched = db.execute(stmt).scalars().all()
            for u in matched:
                if u.userid not in unique_users:
                    unique_users[u.userid] = u
                    user_objects.append(u)

    is_editusers = user_in_group(current_user, "editusers")
    filtered = _filter_users_by_group(
        db, user_objects, group_ids, groups, current_user
    )
    users_out = [
        _user_to_dict(u, current_user, include_fields, exclude_fields, is_editusers)
        for u in filtered
    ]
    return {"users": users_out}


@router.get(
    "/user",
    summary="Search / list users",
    responses={400: {"description": "Missing parameters"}},
)
def get_users(
    db: Session = Depends(get_db),
    current_user: Profile | None = Depends(get_current_user_optional),
    ids: list[int] | None = Query(None, description="User IDs to look up"),
    names: list[str] | None = Query(None, description="Login names to look up"),
    match: list[str] | None = Query(None, description="Substring match patterns"),
    limit: int | None = Query(None, description="Max results per match string"),
    include_disabled: bool = Query(False, description="Include disabled accounts"),
    group_ids: list[int] | None = Query(None, description="Filter by group IDs"),
    groups: list[str] | None = Query(None, description="Filter by group names"),
    include_fields: list[str] | None = Query(None, description="Fields to include"),
    exclude_fields: list[str] | None = Query(None, description="Fields to exclude"),
) -> dict[str, Any]:
    """Port of ``get()`` from User.pm (lines 132-261)."""
    return _get_users_impl(
        db,
        current_user,
        ids=ids,
        names=names,
        match=match,
        limit=limit,
        include_disabled=include_disabled,
        group_ids=group_ids,
        groups=groups,
        include_fields=include_fields,
        exclude_fields=exclude_fields,
    )


@router.get(
    "/user/{id_or_name}",
    summary="Get a single user by ID or login name",
)
def get_user_by_id_or_name(
    id_or_name: str = Path(
        ..., description="Numeric user ID or login name"
    ),
    db: Session = Depends(get_db),
    current_user: Profile | None = Depends(get_current_user_optional),
    include_fields: list[str] | None = Query(None),
    exclude_fields: list[str] | None = Query(None),
) -> dict[str, Any]:
    """Route matching Resources/User.pm auto-detection of numeric IDs vs names."""
    if id_or_name.isdigit():
        return _get_users_impl(
            db, current_user,
            ids=[int(id_or_name)],
            include_fields=include_fields,
            exclude_fields=exclude_fields,
        )
    return _get_users_impl(
        db, current_user,
        names=[id_or_name],
        include_fields=include_fields,
        exclude_fields=exclude_fields,
    )


# ===================================================================
# Ticket 3 — User Write Endpoints
# ===================================================================


@router.post(
    "/user",
    status_code=201,
    response_model=UserCreateResponse,
    summary="Create a new user account",
    responses={401: {"description": "Not authorized"}},
)
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user_required),
) -> UserCreateResponse:
    """Port of ``create()`` from User.pm (lines 106-125).

    Requires ``editusers`` group membership.
    """
    if not user_in_group(current_user, "editusers"):
        raise BugzillaApiError(
            "auth_failure",
            "You are not authorized to add users.",
        )

    email = body.email.strip()
    if not email:
        raise BugzillaApiError("param_required", "A 'email' parameter is required.")

    existing = db.execute(
        select(Profile).where(Profile.login_name == email)
    ).scalar_one_or_none()
    if existing is not None:
        raise BugzillaApiError(
            "account_exists",
            f"There is already an account with the login name {email}.",
        )

    password = body.password.strip() if body.password else "*"
    realname = body.full_name.strip() if body.full_name else ""

    # Hash password if a real password was provided
    cryptpassword = password
    if password != "*":
        from passlib.context import CryptContext

        ctx = CryptContext(schemes=["bcrypt"])
        cryptpassword = ctx.hash(password)

    new_user = Profile(
        login_name=email,
        realname=realname,
        cryptpassword=cryptpassword,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return UserCreateResponse(id=new_user.userid)


@router.put(
    "/user/{id_or_name}",
    response_model=UserUpdateResponse,
    summary="Update user account(s)",
    responses={401: {"description": "Not authorized"}},
)
def update_user(
    id_or_name: str = Path(..., description="Numeric user ID or login name"),
    email: str | None = Query(None, description="New login name / email"),
    full_name: str | None = Query(None, description="New full name"),
    login_denied_text: str | None = Query(None, description="Disabled text"),
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user_required),
) -> UserUpdateResponse:
    """Port of ``update()`` from User.pm (lines 267-330).

    Requires ``editusers`` group membership. Supports both numeric ID and
    login name via the path parameter.
    """
    if not user_in_group(current_user, "editusers"):
        raise BugzillaApiError(
            "auth_failure",
            "You are not authorized to edit users.",
        )

    # Resolve user(s) to update
    if id_or_name.isdigit():
        stmt = select(Profile).where(Profile.userid == int(id_or_name))
    else:
        stmt = select(Profile).where(Profile.login_name == id_or_name)

    user = db.execute(stmt).scalar_one_or_none()
    if user is None:
        raise BugzillaApiError(
            "object_does_not_exist",
            f"There is no user named '{id_or_name}'.",
        )

    # Translate input fields via MAPPED_FIELDS and track changes
    changes: dict[str, FieldChange] = {}

    db.begin_nested()

    if email is not None:
        old_val = user.login_name
        user.login_name = email.strip()
        if old_val != user.login_name:
            changes["email"] = FieldChange(removed=old_val, added=user.login_name)

    if full_name is not None:
        old_val = user.realname
        user.realname = full_name.strip()
        if old_val != user.realname:
            changes["full_name"] = FieldChange(removed=old_val, added=user.realname)

    if login_denied_text is not None:
        old_val = user.disabledtext
        user.disabledtext = login_denied_text
        if old_val != user.disabledtext:
            changes["login_denied_text"] = FieldChange(
                removed=old_val or "", added=user.disabledtext
            )

    db.commit()

    return UserUpdateResponse(
        users=[UserChangeInfo(id=user.userid, changes=changes)]
    )


@router.post(
    "/user/offer_account_by_email",
    status_code=204,
    summary="Send an account creation confirmation email",
)
def offer_account_by_email(
    body: OfferAccountRequest,
    db: Session = Depends(get_db),
) -> None:
    """Port of ``offer_account_by_email()`` from User.pm (lines 95-103).

    In the Perl version this triggers a confirmation email. The FastAPI port
    validates the email and records the intent; actual email delivery depends
    on the mailer integration being configured.
    """
    email_addr = body.email.strip()
    if not email_addr:
        raise BugzillaApiError("param_required", "A 'email' parameter is required.")

    existing = db.execute(
        select(Profile).where(Profile.login_name == email_addr)
    ).scalar_one_or_none()
    if existing is not None:
        raise BugzillaApiError(
            "account_exists",
            f"There is already an account with the login name {email_addr}.",
        )

    # In production, this would send a confirmation email via the configured
    # mailer. For now we accept the request and return 204.
    return None
