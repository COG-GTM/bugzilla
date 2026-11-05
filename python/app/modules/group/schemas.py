"""Response/request shapes for the Group module (Bugzilla/WebService/Group.pm)."""
from pydantic import BaseModel


class ErrorEnvelope(BaseModel):
    error: bool
    code: int
    message: str
    documentation: str


class Member(BaseModel):
    id: int
    real_name: str
    name: str
    email: str
    can_login: bool
    email_enabled: bool
    login_denied_text: str


class Group(BaseModel):
    id: int
    name: str
    description: str
    # only for callers in the creategroups group
    is_active: bool | None = None
    is_bug_group: bool | None = None
    user_regexp: str | None = None
    # only when ?membership=1
    membership: list[Member] | None = None


class GroupsOut(BaseModel):
    groups: list[Group]


class IdOut(BaseModel):
    id: int


class GroupCreate(BaseModel):
    name: str
    description: str
    user_regexp: str | None = None
    is_active: bool | int | str | None = None
    icon_url: str | None = None


class GroupUpdate(BaseModel):
    ids: list[int] | None = None
    names: list[str] | None = None
    name: str | None = None
    description: str | None = None
    user_regexp: str | None = None
    is_active: bool | int | str | None = None
    icon_url: str | None = None


class Change(BaseModel):
    removed: str
    added: str


class GroupUpdateResult(BaseModel):
    id: int
    changes: dict[str, Change]


class GroupsUpdateOut(BaseModel):
    groups: list[GroupUpdateResult]
