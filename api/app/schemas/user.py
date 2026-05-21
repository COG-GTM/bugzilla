"""Pydantic request/response models for the User API.

Field names match the Bugzilla REST API contract.  ``MAPPED_RETURNS`` from
``User.pm`` (``login_name`` → ``email``, ``realname`` → ``full_name``, etc.)
is expressed directly in the model field aliases/names.
"""

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Auth schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    login: str
    password: str
    restrict_login: bool | None = None


class LoginResponse(BaseModel):
    id: int
    token: str | None = None


class ValidLoginRequest(BaseModel):
    login: str


class ValidLoginResponse(BaseModel):
    result: bool


# ---------------------------------------------------------------------------
# Error envelope (matches Bugzilla REST error format)
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    error: bool = True
    message: str
    code: int

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"error": True, "message": "The login or password is wrong.", "code": 300}
            ]
        }
    }
