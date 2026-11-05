"""REST routes for Bugzilla::WebService::Bugzilla (Bugzilla/WebService/Server/REST/Resources/Bugzilla.pm)."""
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import ANONYMOUS, authenticate
from app.core.db import get_db
from app.core.errors import BugzillaError
from app.core.filter import Filter
from app.core.rest import Ctx, context, json_response, merged_params
from app.modules.bugzilla import schemas, service

router = APIRouter(tags=["Bugzilla"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


def _check_callback(params: dict) -> None:
    # Bugzilla::WebService::Server::JSONRPC::_bz_callback_check, with Perl's exact wording
    callback = params.get("callback")
    if callback is not None and not re.match(r"^[\w.\[\]]+$", str(callback)):
        raise BugzillaError("json_rpc_invalid_callback", f"You cannot use '{callback}' as your 'callback' parameter. "
                            "For security reasons, only letters, numbers, and the following characters are allowed "
                            "in the 'callback' parameter: []._")


async def context_checked(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """app.core.rest.context, but with Perl's json_rpc_invalid_callback message."""
    _check_callback(await merged_params(request))
    return await context(request, db)


async def context_login_exempt(request: Request, db: Session = Depends(get_db)) -> Ctx:
    """LOGIN_EXEMPT methods (version, timezone): Bugzilla::WebService::Server::handle_login skips the
    login entirely unless Bugzilla_login is set, so a bad token/api_key is ignored but a wrong
    login/password still fails. (`parameters` is exempt too but calls Bugzilla->login itself.)"""
    params = await merged_params(request)
    _check_callback(params)
    user = authenticate(db, params) if "Bugzilla_login" in params else ANONYMOUS
    request.state.callback = params.get("callback")
    return Ctx(request=request, db=db, params=params, user=user, filter=Filter.from_params(params))


@router.get("/version", response_model=schemas.VersionOut, responses=ERR, summary="Bugzilla.version")
def version(ctx: Ctx = Depends(context_login_exempt)):
    return json_response(ctx.request, service.version())


@router.get("/extensions", response_model=schemas.ExtensionsOut, responses=ERR, summary="Bugzilla.extensions")
def extensions(ctx: Ctx = Depends(context_checked)):
    return json_response(ctx.request, service.extensions())


@router.get("/timezone", response_model=schemas.TimezoneOut, responses=ERR, summary="Bugzilla.timezone")
def timezone(ctx: Ctx = Depends(context_login_exempt)):
    return json_response(ctx.request, service.timezone())


@router.get("/time", response_model=schemas.TimeOut, responses=ERR, summary="Bugzilla.time")
def time(ctx: Ctx = Depends(context_checked)):
    return json_response(ctx.request, service.time(ctx))


@router.get("/last_audit_time", response_model=schemas.LastAuditTimeOut, responses=ERR,
            summary="Bugzilla.last_audit_time")
def last_audit_time(ctx: Ctx = Depends(context_checked)):
    return json_response(ctx.request, service.last_audit_time(ctx))


@router.get("/parameters", response_model=schemas.ParametersOut, responses=ERR, summary="Bugzilla.parameters")
def parameters(ctx: Ctx = Depends(context_checked)):
    return json_response(ctx.request, service.parameters(ctx))
