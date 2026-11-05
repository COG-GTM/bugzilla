"""Response/request shapes of the Product WebService (documentation only; responses are built by service.py)."""
from pydantic import BaseModel, Field


class ErrorEnvelope(BaseModel):
    error: bool
    code: int
    message: str
    documentation: str


class IdsOut(BaseModel):
    ids: list[int]


class IdOut(BaseModel):
    id: int


class FlagTypeOut(BaseModel):
    id: int
    name: str
    description: str
    cc_list: str
    sort_key: int
    is_active: bool
    is_requestable: bool
    is_requesteeble: bool
    is_multiplicable: bool
    grant_group: int | None
    request_group: int | None


class ComponentOut(BaseModel):
    id: int
    name: str
    description: str
    default_assigned_to: str
    default_qa_contact: str
    sort_key: int
    is_active: bool
    flag_types: dict[str, list[FlagTypeOut]] | None = None


class ValueOut(BaseModel):
    id: int
    name: str
    sort_key: int
    is_active: bool


class ProductOut(BaseModel):
    id: int
    name: str
    description: str
    is_active: bool
    default_milestone: str
    has_unconfirmed: bool
    classification: str
    components: list[ComponentOut] | None = None
    versions: list[ValueOut] | None = None
    milestones: list[ValueOut] | None = None


class ProductsOut(BaseModel):
    products: list[ProductOut]


class ProductCreate(BaseModel):
    name: str
    description: str
    version: str
    default_milestone: str | None = Field(default=None, description="Defaults to '---'")
    has_unconfirmed: bool | None = None
    is_open: bool | None = None
    classification: str | None = Field(default=None, description="Defaults to 'Unclassified'")
    create_series: bool | None = Field(default=None, description="Defaults to true")


class ProductUpdate(BaseModel):
    ids: list[int] | None = None
    names: list[str] | None = None
    name: str | None = None
    description: str | None = None
    default_milestone: str | None = None
    has_unconfirmed: bool | None = None
    is_open: bool | None = None


class Change(BaseModel):
    removed: str
    added: str


class ProductChanges(BaseModel):
    id: int
    changes: dict[str, Change]


class ProductsUpdateOut(BaseModel):
    products: list[ProductChanges]
