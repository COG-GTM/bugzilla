from pydantic import BaseModel


class LoginOut(BaseModel):
    id: int
    token: str


class LogoutOut(BaseModel):
    result: None = None


class ValidLoginOut(BaseModel):
    result: bool


class ErrorEnvelope(BaseModel):
    error: bool
    code: int
    message: str
    documentation: str


class GroupOut(BaseModel):
    id: int
    name: str
    description: str


class SavedObjectOut(BaseModel):
    id: int
    name: str
    query: str


class UserOut(BaseModel):
    """User.get item. Anonymous callers only get id/real_name/name; email_enabled and
    login_denied_text need editusers; saved_* are only returned for the caller's own account."""
    id: int | None = None
    real_name: str | None = None
    name: str | None = None
    email: str | None = None
    can_login: bool | None = None
    email_enabled: bool | None = None
    login_denied_text: str | None = None
    groups: list[GroupOut] | None = None
    saved_searches: list[SavedObjectOut] | None = None
    saved_reports: list[SavedObjectOut] | None = None


class UsersOut(BaseModel):
    users: list[UserOut]


class UserCreate(BaseModel):
    email: str
    full_name: str | None = None
    password: str | None = None


class GroupChanges(BaseModel):
    set: list[int | str] | None = None
    add: list[int | str] | None = None
    remove: list[int | str] | None = None


class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    password: str | None = None
    email_enabled: bool | None = None
    login_denied_text: str | None = None
    groups: GroupChanges | None = None
    bless_groups: GroupChanges | None = None


class IdOut(BaseModel):
    id: int


class ChangeOut(BaseModel):
    removed: str
    added: str


class UserChangesOut(BaseModel):
    id: int
    changes: dict[str, ChangeOut]


class UsersUpdateOut(BaseModel):
    users: list[UserChangesOut]
