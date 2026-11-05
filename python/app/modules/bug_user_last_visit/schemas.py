"""Pydantic models documenting the BugUserLastVisit REST contract (Bugzilla::WebService::BugUserLastVisit)."""
from pydantic import BaseModel, Field

from app.modules.bug.schemas import ErrorEnvelope  # noqa: F401  (shared error envelope)


class LastVisit(BaseModel):
    id: int = Field(description="bug id")
    last_visit_ts: str = Field(description="Bugzilla dateTime, e.g. 2026-11-05T13:29:39Z")


LastVisitsOut = list[LastVisit]


class UpdateIn(BaseModel):
    ids: list[int | str] | int | str | None = Field(
        default=None, description="Bug ids or aliases to mark as visited; overrides the id in the path")
