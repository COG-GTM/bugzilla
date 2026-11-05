"""REST routes for the Group module (Bugzilla/WebService/Server/REST/Resources/Group.pm)."""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.group import schemas, service

router = APIRouter(tags=["Group"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


@router.get("/group", response_model=schemas.GroupsOut, responses=ERR, summary="Group.get")
def get(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx))


# Perl declares POST /group with `success_code => STATUS_CREATED`, so it answers 201.
@router.post("/group", response_model=schemas.IdOut, responses=ERR, summary="Group.create", status_code=201,
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.GroupCreate.model_json_schema()}}}})
def create(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.create(ctx), 201)


@router.put("/group/{id_or_name}", response_model=schemas.GroupsUpdateOut, responses=ERR, summary="Group.update",
            openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": schemas.GroupUpdate.model_json_schema()}}}})
def update(id_or_name: str, ctx: Ctx = Depends(context)):
    # Perl: a numeric path segment is `ids => [x]`, anything else is `names => [x]`
    ctx.params[("ids" if id_or_name.isdigit() else "names")] = [id_or_name]
    return json_response(ctx.request, service.update(ctx))
