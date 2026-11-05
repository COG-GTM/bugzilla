"""REST routes for BugUserLastVisit (Bugzilla/WebService/Server/REST/Resources/BugUserLastVisit.pm).

Perl only registers ``qr{^/bug_user_last_visit/(\\d+)$}``: the collection path and non-numeric ids are 32614.
"""
from fastapi import APIRouter, Depends

from app.core.errors import rest_resource_not_found
from app.core.rest import Ctx, context, json_response
from app.modules.bug_user_last_visit import schemas, service
from app.modules.bug.schemas import ErrorEnvelope

router = APIRouter(tags=["BugUserLastVisit"])
ERR = {"4XX": {"model": ErrorEnvelope}}


def numeric(ctx: Ctx, bug_id: str) -> str:
    if not bug_id.isdecimal():  # Perl \d+ (Unicode Nd), unlike str.isdigit()
        raise rest_resource_not_found(ctx.request.method, f"/bug_user_last_visit/{bug_id}")
    return bug_id


@router.get("/bug_user_last_visit/{bug_id}", response_model=schemas.LastVisitsOut, responses=ERR,
            summary="BugUserLastVisit.get (dies in Perl 5.2: HTTP 200, code 100500)")
def get(bug_id: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx, numeric(ctx, bug_id)), 200)


@router.post("/bug_user_last_visit/{bug_id}", response_model=schemas.LastVisitsOut, responses=ERR, status_code=200,
             summary="BugUserLastVisit.update", openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.UpdateIn.model_json_schema()}}}})
def update(bug_id: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.update(ctx, numeric(ctx, bug_id)), 200)
