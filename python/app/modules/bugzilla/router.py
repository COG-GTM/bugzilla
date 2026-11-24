from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.bugzilla import schemas, service

router = APIRouter(tags=["Bugzilla"])


@router.get("/version", response_model=schemas.VersionOut)
def version(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.version())


@router.get("/extensions", response_model=schemas.ExtensionsOut)
def extensions(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.extensions())


@router.get("/timezone", response_model=schemas.TimezoneOut)
def timezone(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.timezone())


@router.get("/time", response_model=schemas.TimeOut)
def time(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.time(ctx))


@router.get("/last_audit_time", response_model=schemas.LastAuditTimeOut)
def last_audit_time(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.last_audit_time(ctx))


@router.get("/parameters", response_model=schemas.ParametersOut)
def parameters(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.parameters(ctx))
