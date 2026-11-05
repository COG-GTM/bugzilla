"""REST routes of Bugzilla/WebService/Server/REST/Resources/Bug.pm. Each handler forwards to the
WebService method of the same name in service.py with the path parameters Perl injects."""

from fastapi import APIRouter, Depends, Request

from app.core.request import Ctx, ctx
from app.core.responses import render
from app.modules.bug import service

router = APIRouter(tags=["Bug"])


def _with(c: Ctx, **extra) -> Ctx:
    c.params.update(extra)
    return c


@router.get("/bug")
async def search(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.search(c))


@router.post("/bug")
async def create(request: Request, c: Ctx = Depends(ctx)):
    # Perl's resource declares status_code (not success_code) so a created bug answers 200.
    return render(request, service.create(c), status=200)


@router.get("/bug/")
async def get_many(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.get(c))


@router.get("/bug/comment/{comment_id}")
async def comment_by_id(comment_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.comments(_with(c, comment_ids=[comment_id])))


@router.get("/bug/attachment/{attachment_id}")
async def attachment_by_id(attachment_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.attachments(_with(c, attachment_ids=[attachment_id])))


@router.put("/bug/attachment/{attachment_id}")
async def update_attachment(attachment_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.update_attachment(_with(c, ids=[attachment_id])))


@router.get("/bug/{bug_id}")
async def get(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.get(_with(c, ids=[bug_id])))


@router.put("/bug/{bug_id}")
async def update(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.update(_with(c, ids=[bug_id])))


@router.get("/bug/{bug_id}/comment")
async def comments(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.comments(_with(c, ids=[bug_id])))


@router.post("/bug/{bug_id}/comment")
async def add_comment(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.add_comment(_with(c, id=bug_id)), status=201)


@router.get("/bug/{bug_id}/history")
async def history(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.history(_with(c, ids=[bug_id])))


@router.get("/bug/{bug_id}/attachment")
async def attachments(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.attachments(_with(c, ids=[bug_id])))


@router.post("/bug/{bug_id}/attachment")
async def add_attachment(bug_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.add_attachment(_with(c, ids=[bug_id])), status=201)


@router.get("/field/bug")
async def fields(request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.fields(c))


@router.get("/field/bug/{name_or_id}")
async def field(name_or_id: str, request: Request, c: Ctx = Depends(ctx)):
    key = "ids" if name_or_id.isdigit() else "names"
    return render(request, service.fields(_with(c, **{key: [name_or_id]})))


@router.get("/field/bug/{name}/values")
async def legal_values(name: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.legal_values(_with(c, field=name)))


@router.get("/field/bug/{name}/{product_id}/values")
async def legal_values_for_product(name: str, product_id: str, request: Request, c: Ctx = Depends(ctx)):
    return render(request, service.legal_values(_with(c, field=name, product_id=product_id)))
