"""Response shapes for the User WebService (Bugzilla/WebService/User.pm) — auth subset."""

from pydantic import BaseModel


class LoginResult(BaseModel):
    id: int
    token: str | None = None


class ValidLoginResult(BaseModel):
    result: bool
