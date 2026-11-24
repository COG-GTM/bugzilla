from pydantic import BaseModel


class VersionOut(BaseModel):
    version: str


class TimezoneOut(BaseModel):
    timezone: str


class TimeOut(BaseModel):
    db_time: str
    web_time: str
    web_time_utc: str
    tz_name: str
    tz_offset: str
    tz_short_name: str


class ExtensionsOut(BaseModel):
    extensions: dict[str, dict[str, str]]


class LastAuditTimeOut(BaseModel):
    last_audit_time: str | None


class ParametersOut(BaseModel):
    parameters: dict[str, str]
