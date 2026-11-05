from pydantic import BaseModel, Field


class ComponentCreateIn(BaseModel):
    """Bugzilla::WebService::Component::create params (MAPPED_FIELDS names; the raw DB column names
    initialowner/initialqacontact/initial_cc/isactive are accepted too, like Perl's translate())."""
    product: str = Field(description="Name of an existing product the caller may administer (editcomponents)")
    name: str = Field(max_length=64)
    description: str
    default_assignee: str = Field(description="Login name of the default assignee")
    default_qa_contact: str | None = Field(default=None, description="Ignored unless the useqacontact param is on")
    default_cc: list[str] | None = None
    is_open: bool | None = Field(default=None, description="Defaults to true (component active)")
    create_series: bool | None = Field(default=None, description="Also create the two default charting series")


class ComponentCreateOut(BaseModel):
    id: int
