"""Pydantic request/response models for the User API.

Field names match the Bugzilla REST API contract.  ``MAPPED_RETURNS`` from
``User.pm`` (``login_name`` → ``email``, ``realname`` → ``full_name``, etc.)
is expressed directly in the model field aliases/names.
"""

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    login: str
    password: str
    restrict_login: bool | None = None


class LoginResponse(BaseModel):
    id: int
    token: str | None = None


class ValidLoginRequest(BaseModel):
    login: str


class ValidLoginResponse(BaseModel):
    result: bool


# ---------------------------------------------------------------------------
# Error envelope (matches Bugzilla REST error format)
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    error: bool = True
    message: str
    code: int

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"error": True, "message": "The login or password is wrong.", "code": 300}
            ]
        }
    }


# ---------------------------------------------------------------------------
# User read schemas (Ticket 2)
# ---------------------------------------------------------------------------

class GroupInfo(BaseModel):
    id: int
    name: str
    description: str


class SavedSearchInfo(BaseModel):
    id: int
    name: str
    query: str


class SavedReportInfo(BaseModel):
    id: int
    name: str
    query: str


class UserInfo(BaseModel):
    """Full user representation for authenticated callers."""

    id: int
    real_name: str | None = None
    name: str | None = None
    email: str | None = None
    can_login: bool | None = None
    email_enabled: bool | None = None
    login_denied_text: str | None = None
    saved_searches: list[SavedSearchInfo] | None = None
    saved_reports: list[SavedReportInfo] | None = None
    groups: list[GroupInfo] | None = None


class UserInfoLimited(BaseModel):
    """Limited user representation for unauthenticated callers."""

    id: int
    real_name: str | None = None
    name: str | None = None


class UserListResponse(BaseModel):
    users: list[UserInfo]


# ---------------------------------------------------------------------------
# User write schemas (Ticket 3)
# ---------------------------------------------------------------------------

class UserCreateRequest(BaseModel):
    email: str
    full_name: str | None = None
    password: str | None = None


class UserCreateResponse(BaseModel):
    id: int


class FieldChange(BaseModel):
    removed: str
    added: str


class UserChangeInfo(BaseModel):
    id: int
    changes: dict[str, FieldChange]


class UserUpdateResponse(BaseModel):
    users: list[UserChangeInfo]


class OfferAccountRequest(BaseModel):
    email: str
