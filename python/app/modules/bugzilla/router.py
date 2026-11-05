from fastapi import APIRouter, Depends, Request

from app.core.request import Ctx, ctx
from app.core.responses import render
from app.modules.bugzilla import service

router = APIRouter(tags=["Bugzilla"])


@router.get("/version")
async def version(request: Request):
    return render(request, service.version())


@router.get("/extensions")
async def extensions(request: Request):
    return render(request, service.extensions())


@router.get("/timezone")
async def timezone(request: Request):
    return render(request, service.timezone_())


@router.get("/time")
async def time(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.time_(c))


@router.get("/last_audit_time")
async def last_audit_time(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.last_audit_time(c))


@router.get("/parameters")
async def parameters(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.parameters(c))
