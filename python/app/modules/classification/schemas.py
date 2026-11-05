"""Pydantic models documenting the Classification REST contract (Bugzilla::WebService::Classification)."""
from pydantic import BaseModel


class Product(BaseModel):
    id: int
    name: str
    description: str


class Classification(BaseModel):
    id: int
    name: str
    description: str
    sort_key: int
    products: list[Product]


class ClassificationsOut(BaseModel):
    classifications: list[Classification]


class ErrorEnvelope(BaseModel):
    error: bool = True
    code: int
    message: str
    documentation: str
