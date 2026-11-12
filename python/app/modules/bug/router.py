"""REST routes for the Bug module (Bugzilla/WebService/Server/REST/Resources/Bug.pm)."""
from fastapi import APIRouter, Depends

from app.core.rest import Ctx, context, json_response
from app.modules.bug import schemas, service, writes

router = APIRouter(tags=["Bug"])
ERR = {"4XX": {"model": schemas.ErrorEnvelope}}


@router.get("/bug", response_model=schemas.BugsOut, responses=ERR, summary="Bug.search")
def search(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.search(ctx))


# Perl declares POST /bug with `status_code` (not `success_code`), so it answers 200, not 201.
@router.post("/bug", response_model=schemas.IdOut, responses=ERR, summary="Bug.create", status_code=200,
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.BugCreate.model_json_schema()}}}})
def create(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.create(ctx), 200)


@router.get("/bug/", response_model=schemas.BugsOut, responses=ERR, summary="Bug.get (ids=...)", include_in_schema=False)
def get_many(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx))


@router.get("/bug/comment/{comment_id}", response_model=schemas.CommentsOut, responses=ERR, summary="Bug.comments")
def comment_by_id(comment_id: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.comments(ctx, comment_ids=[comment_id]))


@router.get("/bug/attachment/{attachment_id}", response_model=schemas.AttachmentsOut, responses=ERR,
            summary="Bug.attachments")
def attachment_by_id(attachment_id: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.attachments(ctx, attachment_ids=[attachment_id]))


@router.get("/bug/{id_or_alias}", response_model=schemas.BugsOut, responses=ERR, summary="Bug.get")
def get(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.get(ctx, ids=[id_or_alias]))


@router.put("/bug/{id_or_alias}", response_model=schemas.BugsUpdateOut, responses=ERR, summary="Bug.update",
            openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": schemas.BugUpdate.model_json_schema()}}}})
def update(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.update(ctx, ids=[id_or_alias]))


@router.get("/bug/{id_or_alias}/comment", response_model=schemas.CommentsOut, responses=ERR, summary="Bug.comments")
def comments(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.comments(ctx, ids=[id_or_alias]))


@router.post("/bug/{id_or_alias}/comment", response_model=schemas.IdOut, responses=ERR, status_code=201,
             summary="Bug.add_comment", openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.AddCommentIn.model_json_schema()}}}})
def add_comment(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.add_comment(ctx, id_or_alias), 201)


@router.get("/bug/{id_or_alias}/history", response_model=schemas.HistoryOut, responses=ERR, summary="Bug.history")
def history(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.history(ctx, ids=[id_or_alias]))


@router.get("/bug/{id_or_alias}/attachment", response_model=schemas.AttachmentsOut, responses=ERR,
            summary="Bug.attachments")
def attachments(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.attachments(ctx, ids=[id_or_alias]))


@router.post("/bug/{id_or_alias}/attachment", response_model=schemas.IdsOut, responses=ERR, status_code=201,
             summary="Bug.add_attachment", openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.AddAttachmentIn.model_json_schema()}}}})
def add_attachment(id_or_alias: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, writes.add_attachment(ctx, ids=[id_or_alias]), 201)


@router.get("/field/bug", response_model=schemas.FieldsOut, responses=ERR, summary="Bug.fields")
def fields(ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.fields(ctx))


@router.get("/field/bug/{name_or_id}", response_model=schemas.FieldsOut, responses=ERR, summary="Bug.fields")
def field(name_or_id: str, ctx: Ctx = Depends(context)):
    kw = {"ids": [name_or_id]} if name_or_id.isdigit() else {"names": [name_or_id]}
    return json_response(ctx.request, service.fields(ctx, **kw))


@router.get("/field/bug/{name}/values", response_model=schemas.LegalValuesOut, responses=ERR, summary="Bug.legal_values")
def legal_values(name: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.legal_values(ctx, name))


@router.get("/field/bug/{name}/{product_id}/values", response_model=schemas.LegalValuesOut, responses=ERR,
            summary="Bug.legal_values")
def legal_values_for_product(name: str, product_id: str, ctx: Ctx = Depends(context)):
    return json_response(ctx.request, service.legal_values(ctx, name, product_id))
