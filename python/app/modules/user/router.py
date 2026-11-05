"""REST routes for the User module (Bugzilla/WebService/Server/REST/Resources/User.pm).

offer_account_by_email has no REST resource in Perl (JSON-RPC only) and is not ported.
"""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.user import schemas, service, writes

router = APIRouter(tags=["User"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


@router.get("/login", response_model=schemas.LoginOut, responses=ERR, summary="User.login")
def login(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.login(ctx))


@router.get("/logout", response_model=schemas.LogoutOut, responses=ERR, summary="User.logout")
def logout(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.logout(ctx))


@router.get("/valid_login", response_model=schemas.ValidLoginOut, responses=ERR, summary="User.valid_login")
def valid_login(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.valid_login(ctx))


@router.get("/user", response_model=schemas.UsersOut, responses=ERR, summary="User.get")
def get_users(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx))


@router.post("/user", response_model=schemas.IdOut, responses=ERR, summary="User.create", status_code=201,
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.UserCreate.model_json_schema()}}}})
def create_user(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.create(ctx), 201)


@router.get("/user/{id_or_name}", response_model=schemas.UsersOut, responses=ERR, summary="User.get")
def get_user(id_or_name: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx, **service.path_param(id_or_name)))


@router.put("/user/{id_or_name}", response_model=schemas.UsersUpdateOut, responses=ERR, summary="User.update",
            openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": schemas.UserUpdate.model_json_schema()}}}})
def update_user(id_or_name: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.update(ctx, **service.path_param(id_or_name)))
