"""REST routes of Bugzilla::WebService::Server::REST::Resources::FlagType."""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.flag_type import schemas, service

router = APIRouter(tags=["FlagType"])
ERR = {400: {"model": schemas.ErrorOut}, 401: {"model": schemas.ErrorOut}, 404: {"model": schemas.ErrorOut}}


# Perl declares POST /flag_type with success_code => STATUS_CREATED.
@router.post("/flag_type", response_model=schemas.IdOut, responses=ERR, status_code=201, summary="FlagType.create")
def create(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.create(ctx), 201)


@router.get("/flag_type/{product}/{component}", response_model=schemas.GetOut, responses=ERR, summary="FlagType.get")
def get_component(product: str, component: str, ctx: Ctx = Depends(context)):
    ctx.params.update({"product": product, "component": component})
    return json_response(ctx.request, service.get(ctx))


@router.get("/flag_type/{product}", response_model=schemas.GetOut, responses=ERR, summary="FlagType.get")
def get_product(product: str, ctx: Ctx = Depends(context)):
    ctx.params["product"] = product
    return json_response(ctx.request, service.get(ctx))


@router.put("/flag_type/{id_or_name}", response_model=schemas.UpdateOut, responses=ERR, summary="FlagType.update")
def update(id_or_name: str, ctx: Ctx = Depends(context)):
    # the path param maps to ids when numeric, otherwise names; the JSON body overrides it (Perl merges body last)
    key = "ids" if id_or_name.isdigit() else "names"
    ctx.params.setdefault(key, [id_or_name])
    return json_response(ctx.request, service.update(ctx))
