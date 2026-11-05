"""Pydantic shapes for the Bug WebService (documentation / OpenAPI).

Responses are built as plain dicts in service.py because Perl's include_fields /
exclude_fields filtering decides per request which keys exist; these models describe
the full shapes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserDetail(BaseModel):
    id: int
    real_name: str
    name: str
    email: str


class Bug(BaseModel):
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
    classification: str
    target_milestone: str
    creator: str
    creator_detail: UserDetail
    assigned_to: str
    assigned_to_detail: UserDetail
    qa_contact: str
    qa_contact_detail: UserDetail | None = None
    cc: list[str] = []
    cc_detail: list[UserDetail] = []
    keywords: list[str] = []
    groups: list[str] = []
    flags: list[dict[str, Any]] = []
    blocks: list[int] = []
    depends_on: list[int] = []
    dupe_of: int | None = None
    see_also: list[str] = []
    url: str
    whiteboard: str
    deadline: str | None = None
    is_open: bool
    is_confirmed: bool
    is_cc_accessible: bool
    is_creator_accessible: bool
    creation_time: str
    last_change_time: str
    estimated_time: float | None = None
    remaining_time: float | None = None
    actual_time: float | None = None
    update_token: str | None = None


class Comment(BaseModel):
    id: int
    bug_id: int
    count: int
    creator: str
    time: str
    creation_time: str
    text: str
    is_private: bool
    attachment_id: int | None = None
    tags: list[str] | None = None


class Attachment(BaseModel):
    id: int
    bug_id: int
    file_name: str
    summary: str
    content_type: str
    size: int
    data: str | None = Field(default=None, description="base64")
    is_patch: int
    is_private: int
    is_obsolete: int
    creator: str
    creation_time: str
    last_change_time: str
    flags: list[dict[str, Any]] = []


class Change(BaseModel):
    removed: str
    added: str


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


class CreateBugRequest(BaseModel):
    product: str
    component: str
    summary: str
    version: str
    description: str | None = None
    op_sys: str | None = None
    platform: str | None = None
    priority: str | None = None
    severity: str | None = None
    alias: str | list[str] | None = None
    assigned_to: str | None = None
    cc: list[str] | None = None
    comment_is_private: bool | None = None
    groups: list[str] | None = None
    qa_contact: str | None = None
    status: str | None = None
    resolution: str | None = None
    target_milestone: str | None = None
    depends_on: list[int] | None = None
    blocks: list[int] | None = None
    keywords: list[str] | None = None
    url: str | None = None
    whiteboard: str | None = None
    deadline: str | None = None


class UpdateBugRequest(BaseModel):
    model_config = {"extra": "allow"}
    ids: list[int | str] | None = None
    alias: str | list[str] | dict[str, list[str]] | None = None
    assigned_to: str | None = None
    blocks: dict[str, list[int]] | None = None
    depends_on: dict[str, list[int]] | None = None
    cc: dict[str, list[str]] | None = None
    comment: dict[str, Any] | None = None
    dupe_of: int | None = None
    priority: str | None = None
    severity: str | None = None
    status: str | None = None
    resolution: str | None = None
    summary: str | None = None
    url: str | None = None
    whiteboard: str | None = None


class UpdateBugResult(BaseModel):
    id: int
    alias: list[str]
    last_change_time: str
    changes: dict[str, Change]
