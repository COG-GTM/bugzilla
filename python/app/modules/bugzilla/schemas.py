"""Response shapes for the Bugzilla WebService (Bugzilla/WebService/Bugzilla.pm)."""

from pydantic import BaseModel


class Version(BaseModel):
    version: str


class Extensions(BaseModel):
    extensions: dict[str, dict[str, str]]


class Timezone(BaseModel):
    timezone: str


class Time(BaseModel):
    db_time: str
    web_time: str
    web_time_utc: str
    tz_name: str
    tz_offset: str
    tz_short_name: str


class LastAuditTime(BaseModel):
    last_audit_time: str | None


class Parameters(BaseModel):
    parameters: dict[str, str]
