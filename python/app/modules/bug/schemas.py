"""Pydantic v2 models for the Bug module.

Request models are deliberately lenient (every field optional, unknown fields
ignored): Bugzilla's own validators run in the service so that the error code
and message match Perl, which a Pydantic 422 never would. Response models
document the shapes in OpenAPI; responses are not passed through them because
include_fields / exclude_fields decide which keys are present."""
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Request(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    def given(self):
        """Only the fields the client actually sent."""
        return self.model_dump(exclude_unset=True)


class BugCreate(_Request):
    product: Any = None
    component: Any = None
    summary: Any = None
    version: Any = None
    description: Any = None
    op_sys: Any = None
    platform: Any = None
    priority: Any = None
    severity: Any = None
    status: Any = None
    target_milestone: Any = None
    assigned_to: Any = None
    qa_contact: Any = None
    cc: Any = None
    alias: Any = None
    depends_on: Any = None
    blocks: Any = None
    url: Any = None
    whiteboard: Any = None
    groups: Any = None
    comment_is_private: Any = None
    estimated_time: Any = None
    deadline: Any = None


class BugUpdate(_Request):
    ids: Any = None
    summary: Any = None
    priority: Any = None
    severity: Any = None
    op_sys: Any = None
    platform: Any = None
    version: Any = None
    target_milestone: Any = None
    url: Any = None
    whiteboard: Any = None
    component: Any = None
    assigned_to: Any = None
    qa_contact: Any = None
    status: Any = None
    resolution: Any = None
    dupe_of: Any = None
    cc: Any = None
    depends_on: Any = None
    blocks: Any = None
    comment: Any = None
    deadline: Any = None
    estimated_time: Any = None
    remaining_time: Any = None
    is_cc_accessible: Any = None
    is_creator_accessible: Any = None
    # Accepted by Perl, not ported yet: sending one is an explicit error here.
    product: Any = None
    groups: Any = None
    keywords: Any = None
    see_also: Any = None
    flags: Any = None
    alias: Any = None
    reset_assigned_to: Any = None
    reset_qa_contact: Any = None
    comment_is_private: Any = None
    work_time: Any = None


class CommentCreate(_Request):
    comment: Any = None
    is_private: Any = None
    work_time: Any = None


class AttachmentCreate(_Request):
    ids: Any = None
    data: Any = None
    file_name: Any = None
    summary: Any = None
    content_type: Any = None
    comment: Any = None
    is_patch: Any = None
    is_private: Any = None


class UserDetail(BaseModel):
    id: int
    name: str
    email: str
    real_name: str


class Bug(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[int] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    resolution: Optional[str] = None
    product: Optional[str] = None
    component: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_detail: Optional[UserDetail] = None
    creator: Optional[str] = None
    cc: Optional[list[str]] = None
    depends_on: Optional[list[int]] = None
    blocks: Optional[list[int]] = None
    creation_time: Optional[str] = None
    last_change_time: Optional[str] = None


class BugList(BaseModel):
    bugs: list[Bug]
    faults: Optional[list] = Field(default=None, description="Present on GET /bug/{id} only.")


class Created(BaseModel):
    id: int


class Change(BaseModel):
    added: str
    removed: str


class UpdatedBug(BaseModel):
    id: int
    alias: list[str]
    last_change_time: str
    changes: dict[str, Change]


class UpdateResult(BaseModel):
    bugs: list[UpdatedBug]


class Comment(BaseModel):
    """Every key is optional: include_fields / exclude_fields choose them."""
    id: Optional[int] = None
    bug_id: Optional[int] = None
    attachment_id: Optional[int] = None
    count: Optional[int] = None
    text: Optional[str] = None
    creator: Optional[str] = None
    time: Optional[str] = None
    creation_time: Optional[str] = None
    is_private: Optional[bool] = None
    tags: Optional[list[str]] = None


class CommentResult(BaseModel):
    bugs: dict[str, dict[str, list[Comment]]]
    comments: dict[str, Comment]


class HistoryChange(BaseModel):
    field_name: str
    added: str
    removed: str
    attachment_id: Optional[int] = None


class HistoryEntry(BaseModel):
    when: str
    who: str
    changes: list[HistoryChange]


class BugHistory(BaseModel):
    id: int
    alias: list[str]
    history: list[HistoryEntry]


class HistoryResult(BaseModel):
    bugs: list[BugHistory]


class Attachment(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[int] = None
    bug_id: Optional[int] = None
    file_name: Optional[str] = None
    summary: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    data: Optional[str] = Field(default=None, description="Base64.")


class AttachmentResult(BaseModel):
    bugs: dict[str, list[Attachment]]
    attachments: dict[str, Attachment]


class AttachmentCreated(BaseModel):
    ids: list[int]
