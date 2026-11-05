"""REST routes for the Product module (Bugzilla/WebService/Server/REST/Resources/Product.pm)."""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.product import schemas, service

router = APIRouter(tags=["Product"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


@router.get("/product_accessible", response_model=schemas.IdsOut, responses=ERR,
            summary="Product.get_accessible_products")
def accessible(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.id_list(ctx, "accessible"))


@router.get("/product_enterable", response_model=schemas.IdsOut, responses=ERR,
            summary="Product.get_enterable_products")
def enterable(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.id_list(ctx, "enterable"))


@router.get("/product_selectable", response_model=schemas.IdsOut, responses=ERR,
            summary="Product.get_selectable_products")
def selectable(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.id_list(ctx, "selectable"))


@router.get("/product", response_model=schemas.ProductsOut, responses=ERR, summary="Product.get")
def get_many(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx))


@router.post("/product", response_model=schemas.IdOut, responses=ERR, status_code=201, summary="Product.create",
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.ProductCreate.model_json_schema()}}}})
def create(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.create(ctx), 201)


@router.get("/product/{id_or_name}", response_model=schemas.ProductsOut, responses=ERR, summary="Product.get")
def get(id_or_name: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx, **service.path_param(id_or_name)))


@router.put("/product/{id_or_name}", response_model=schemas.ProductsUpdateOut, responses=ERR,
            summary="Product.update", openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": schemas.ProductUpdate.model_json_schema()}}}})
def update(id_or_name: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.update(ctx, **service.path_param(id_or_name)))
