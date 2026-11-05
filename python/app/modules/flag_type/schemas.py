"""Response shapes of the FlagType WebService (documentation only; the service builds the dicts)."""
from pydantic import BaseModel, Field


class FlagTypeOut(BaseModel):
    id: int
    name: str
    description: str
    type: str = Field(description="'bug' or 'attachment'")
    values: list[str]
    is_active: bool
    is_requesteeble: bool
    is_multiplicable: bool
    inclusions: list[str] | None = None
    exclusions: list[str] | None = None


class GetOut(BaseModel):
    bug: list[FlagTypeOut]
    attachment: list[FlagTypeOut]


class IdOut(BaseModel):
    id: int


class Change(BaseModel):
    removed: str | None
    added: str | None


class UpdatedFlagType(BaseModel):
    id: int
    name: str
    changes: dict[str, Change]


class UpdateOut(BaseModel):
    flagtypes: list[UpdatedFlagType]


class ErrorOut(BaseModel):
    error: bool = True
    code: int
    message: str
    documentation: str
