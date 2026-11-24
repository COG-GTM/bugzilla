"""Strangler fallback: anything this service has not ported is answered by
Perl. Used for paths no module routes, and by a module that meets a
parameter it does not support yet (raise ``NotPorted``)."""
import os

import httpx
from fastapi import Request, Response

from .errors import error_response, resource_not_found

PERL_REST_URL = os.environ.get("PERL_REST_URL", "").rstrip("/")
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "host"}


class NotPorted(Exception):
    """Raised by a service for a request only Perl can serve so far."""


async def forward_to_perl(request: Request):
    if not PERL_REST_URL:
        return error_response(resource_not_found(request))
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    # Apache does not proxy a request carrying this header back to Python.
    headers["x-bugzilla-backend"] = "perl"
    body = getattr(request.state, "raw_body", None)
    if body is None:
        body = await request.body()
    path = request.scope.get("bz_path", request.url.path)
    async with httpx.AsyncClient(timeout=120) as client:
        # The raw query string is kept: repeated parameters must reach Perl unchanged.
        query = request.scope.get("query_string", b"").decode()
        upstream = await client.request(request.method, PERL_REST_URL + path + (f"?{query}" if query else ""),
                                        content=body, headers=headers)
    out = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    out["x-bugzilla-backend"] = "perl"
    return Response(upstream.content, upstream.status_code, out)
