from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.component import schemas, service

router = APIRouter(tags=["Component"])


# Bugzilla::WebService::Server::REST::Resources::Component::_rest_resources (the only Component resource in 5.2)
@router.post("/component", response_model=schemas.ComponentCreateOut, status_code=201,
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.ComponentCreateIn.model_json_schema()}}}})
def create(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.create(ctx), 201)
