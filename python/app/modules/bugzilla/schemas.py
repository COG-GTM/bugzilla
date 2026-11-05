from pydantic import BaseModel


class ErrorEnvelope(BaseModel):
    error: bool = True
    code: int
    message: str
    documentation: str


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


class ExtensionVersion(BaseModel):
    version: str


class ExtensionsOut(BaseModel):
    extensions: dict[str, ExtensionVersion]


class LastAuditTimeOut(BaseModel):
    last_audit_time: str | None


class ParametersOut(BaseModel):
    parameters: dict[str, str | None]
