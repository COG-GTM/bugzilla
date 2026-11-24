"""REST routes of Bugzilla/WebService/Server/REST/Resources/Bug.pm. Paths,
methods and success status codes match Perl (POST /bug answers 200, the
comment and attachment POSTs answer 201)."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import User, current_user
from app.core.db import get_session
from app.core.params import request_params

from . import schemas, service

router = APIRouter()


def _ip(request: Request):
    return request.client.host if request.client else ""


def _given(model, params):
    """The body fields the model knows plus everything else Perl would also see."""
    return {**params, **model.model_validate(params).given()}


@router.get("/bug", response_model=schemas.BugList, summary="Bug.search")
def search(request: Request, params: dict = Depends(request_params), user: User = Depends(current_user),
           session: Session = Depends(get_session)):
    return JSONResponse(service.search(session, user, params, _ip(request)))


@router.post("/bug", response_model=schemas.Created, summary="Bug.create",
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.BugCreate.model_json_schema()}}}})
def create(params: dict = Depends(request_params), user: User = Depends(current_user),
           session: Session = Depends(get_session)):
    return JSONResponse(service.create(session, user, _given(schemas.BugCreate, params)))


@router.get("/bug/comment/{comment_id}", response_model=schemas.CommentResult, summary="Bug.comments by comment id")
def comment(comment_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
            session: Session = Depends(get_session)):
    return JSONResponse(service.comments(session, user, params, comment_ids=[comment_id]))


@router.get("/bug/attachment/{attachment_id}", response_model=schemas.AttachmentResult,
            summary="Bug.attachments by attachment id")
def attachment(attachment_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    return JSONResponse(service.attachments(session, user, params, attachment_ids=[attachment_id]))


@router.get("/bug/{bug_id}", response_model=schemas.BugList, summary="Bug.get")
def get(bug_id: str, request: Request, params: dict = Depends(request_params), user: User = Depends(current_user),
        session: Session = Depends(get_session)):
    return JSONResponse(service.get(session, user, bug_id, params, _ip(request)))


@router.put("/bug/{bug_id}", response_model=schemas.UpdateResult, summary="Bug.update",
            openapi_extra={"requestBody": {"content": {"application/json": {
                "schema": schemas.BugUpdate.model_json_schema()}}}})
def update(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
           session: Session = Depends(get_session)):
    return JSONResponse(service.update_bugs(session, user, bug_id, _given(schemas.BugUpdate, params)))


@router.get("/bug/{bug_id}/comment", response_model=schemas.CommentResult, summary="Bug.comments")
def comments(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
             session: Session = Depends(get_session)):
    return JSONResponse(service.comments(session, user, params, bug_ids=[bug_id]))


@router.post("/bug/{bug_id}/comment", response_model=schemas.Created, status_code=201, summary="Bug.add_comment",
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.CommentCreate.model_json_schema()}}}})
def add_comment(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    return JSONResponse(service.add_comment(session, user, bug_id, _given(schemas.CommentCreate, params)),
                        status_code=201)


@router.get("/bug/{bug_id}/history", response_model=schemas.HistoryResult, summary="Bug.history")
def history(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
            session: Session = Depends(get_session)):
    return JSONResponse(service.history(session, user, [bug_id], params))


@router.get("/bug/{bug_id}/attachment", response_model=schemas.AttachmentResult, summary="Bug.attachments")
def attachments(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
                session: Session = Depends(get_session)):
    return JSONResponse(service.attachments(session, user, params, bug_ids=[bug_id]))


@router.post("/bug/{bug_id}/attachment", response_model=schemas.AttachmentCreated, status_code=201,
             summary="Bug.add_attachment",
             openapi_extra={"requestBody": {"content": {"application/json": {
                 "schema": schemas.AttachmentCreate.model_json_schema()}}}})
def add_attachment(bug_id: str, params: dict = Depends(request_params), user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
    return JSONResponse(service.add_attachment(session, user, bug_id, _given(schemas.AttachmentCreate, params)),
                        status_code=201)
