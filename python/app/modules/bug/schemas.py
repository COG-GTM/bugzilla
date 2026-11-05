"""Pydantic models documenting the Bug REST contract (shapes mirror Bugzilla::WebService::Bug)."""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserDetail(BaseModel):
    id: int
    name: str
    email: str
    real_name: str


class Bug(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    alias: list[str] = []
    summary: str
    status: str
    resolution: str
    product: str
    component: str
    version: str
    platform: str
    op_sys: str
    priority: str
    severity: str
    creator: str
    creator_detail: UserDetail
    assigned_to: str
    assigned_to_detail: UserDetail
    qa_contact: str
    cc: list[str]
    cc_detail: list[UserDetail]
    creation_time: str
    last_change_time: str
    is_open: bool
    is_confirmed: bool
    is_creator_accessible: bool
    is_cc_accessible: bool
    blocks: list[int]
    depends_on: list[int]
    dupe_of: int | None
    keywords: list[str]
    groups: list[str]
    flags: list[dict]
    see_also: list[str]
    target_milestone: str
    whiteboard: str
    url: str
    classification: str
    deadline: str | None


class BugsOut(BaseModel):
    bugs: list[Bug]
    faults: list[dict] | None = None


class BugCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    product: str
    component: str
    summary: str
    version: str
    description: str | None = None
    op_sys: str | None = None
    platform: str | None = None
    priority: str | None = None
    severity: str | None = None
    alias: list[str] | str | None = None
    assigned_to: str | None = None
    cc: list[str] | None = None
    comment_is_private: bool | None = None
    groups: list[str] | None = None
    qa_contact: str | None = None
    status: str | None = None
    resolution: str | None = None
    target_milestone: str | None = None
    flags: list[dict] | None = None
    depends_on: list[int] | None = None
    blocks: list[int] | None = None
    keywords: list[str] | None = None
    url: str | None = None
    whiteboard: str | None = None


class ListChange(BaseModel):
    add: list[Any] | None = None
    remove: list[Any] | None = None
    set: list[Any] | None = None


class CommentIn(BaseModel):
    body: str | None = None
    comment: str | None = None
    is_private: bool | None = None


class BugUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")
    alias: ListChange | list[str] | str | None = None
    assigned_to: str | None = None
    blocks: ListChange | None = None
    depends_on: ListChange | None = None
    cc: ListChange | None = None
    comment: CommentIn | None = None
    component: str | None = None
    dupe_of: int | None = None
    keywords: ListChange | None = None
    op_sys: str | None = None
    platform: str | None = None
    priority: str | None = None
    qa_contact: str | None = None
    resolution: str | None = None
    severity: str | None = None
    status: str | None = None
    summary: str | None = None
    target_milestone: str | None = None
    url: str | None = None
    version: str | None = None
    whiteboard: str | None = None
    is_cc_accessible: bool | None = None
    is_creator_accessible: bool | None = None


class IdOut(BaseModel):
    id: int


class IdsOut(BaseModel):
    ids: list[int]


class FieldChange(BaseModel):
    removed: str
    added: str


class BugUpdateResult(BaseModel):
    id: int
    alias: list[str]
    last_change_time: str
    changes: dict[str, FieldChange]


class BugsUpdateOut(BaseModel):
    bugs: list[BugUpdateResult]


class Comment(BaseModel):
    id: int
    bug_id: int
    attachment_id: int | None
    count: int
    text: str
    creator: str
    time: str
    creation_time: str
    is_private: bool
    tags: list[str] = []


class CommentsOut(BaseModel):
    bugs: dict[str, dict[str, list[Comment]]]
    comments: dict[str, Comment]


class AddCommentIn(BaseModel):
    comment: str
    is_private: bool | None = None
    work_time: float | None = None


class HistoryChange(BaseModel):
    field_name: str
    removed: str
    added: str
    attachment_id: int | None = None


class HistoryEntry(BaseModel):
    when: str
    who: str
    changes: list[HistoryChange]


class BugHistory(BaseModel):
    id: int
    alias: list[str]
    history: list[HistoryEntry]


class HistoryOut(BaseModel):
    bugs: list[BugHistory]


class Attachment(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    bug_id: int
    file_name: str
    summary: str
    content_type: str
    size: int
    creator: str
    data: str | None = Field(default=None, description="base64")
    is_private: int
    is_obsolete: int
    is_patch: int
    flags: list[dict]
    creation_time: str
    last_change_time: str


class AttachmentsOut(BaseModel):
    bugs: dict[str, list[Attachment]]
    attachments: dict[str, Attachment]


class AddAttachmentIn(BaseModel):
    ids: list[int] | None = None
    data: str = Field(description="base64")
    file_name: str
    summary: str
    content_type: str
    comment: str | None = None
    is_patch: bool | None = None
    is_private: bool | None = None


class FieldValue(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None
    sort_key: int | None = None
    sortkey: int | None = None
    visibility_values: list[str] = []


class FieldDef(BaseModel):
    id: int
    type: int
    is_custom: bool
    name: str
    display_name: str
    is_mandatory: bool
    is_on_bug_entry: bool
    visibility_field: str | None
    visibility_values: list[str]
    value_field: str | None = None
    values: list[FieldValue] | None = None


class FieldsOut(BaseModel):
    fields: list[FieldDef]


class LegalValuesOut(BaseModel):
    values: list[str]


class ErrorEnvelope(BaseModel):
    error: bool = True
    code: int
    message: str
    documentation: str
