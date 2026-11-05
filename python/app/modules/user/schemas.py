from pydantic import BaseModel


class LoginOut(BaseModel):
    id: int
    token: str


class LogoutOut(BaseModel):
    result: None = None


class ValidLoginOut(BaseModel):
    result: bool
