"""REST routes for the Classification module (Bugzilla/WebService/Server/REST/Resources/Classification.pm)."""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.classification import schemas, service

router = APIRouter(tags=["Classification"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


@router.get("/classification/{id_or_name}", response_model=schemas.ClassificationsOut, responses=ERR,
            summary="Classification.get")
def get(id_or_name: str, ctx: Ctx = Depends(context)):
    # Perl maps a purely numeric path segment to ``ids`` and anything else to ``names``
    key = "ids" if id_or_name.isdigit() and id_or_name.isascii() else "names"
    return json_response(ctx.request, service.get(ctx, **{key: [id_or_name]}))
