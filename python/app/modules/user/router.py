from fastapi import APIRouter, Depends, Request

from app.core.request import Ctx, ctx
from app.core.responses import render
from app.modules.user import service

router = APIRouter(tags=["User"])


@router.api_route("/login", methods=["GET", "POST"])
async def login(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.login(c))


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.logout(c))


@router.get("/valid_login")
async def valid_login(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.valid_login(c))
