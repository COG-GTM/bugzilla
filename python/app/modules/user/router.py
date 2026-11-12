from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.user import schemas, service

router = APIRouter(tags=["User"])


@router.get("/login", response_model=schemas.LoginOut)
def login(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.login(ctx))


@router.get("/logout", response_model=schemas.LogoutOut)
def logout(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.logout(ctx))


@router.get("/valid_login", response_model=schemas.ValidLoginOut)
def valid_login(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.valid_login(ctx))
