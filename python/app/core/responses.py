"""REST envelope behaviour copied from Bugzilla::WebService::Server::REST / JSONRPC:
JSON (or JSONP via ``callback``), CORS headers, ETag + 304, Perl-style error envelope.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastapi import Request, Response

from app.core.errors import BugzillaError

JSON_CT = "application/json; charset=UTF-8"
JSONP_CT = "text/javascript; charset=UTF-8"


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def etag_for(content: str, content_type: str) -> str:
    return base64.b64encode(hashlib.md5((content + content_type).encode("utf-8")).digest()).decode().rstrip("=")


def render(request: Request, result: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> Response:
    callback = request.query_params.get("callback")
    if not isinstance(result, dict):
        result = {"result": result}  # JSONRPC-over-REST wraps scalar results
    body = dumps(result)
    ct = JSON_CT
    if callback:
        body = f"/**/{callback}({body})"
        ct = JSONP_CT
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "origin, content-type, accept, x-requested-with",
        **(extra_headers or {}),
    }
    etag = etag_for(body, ct)
    headers["ETag"] = f'"{etag}"'
    if request.headers.get("if-none-match", "").strip('"') == etag and status == 200:
        return Response(status_code=304, headers=headers)
    return Response(content=body, status_code=status, media_type=ct, headers=headers)


def render_error(request: Request, err: BugzillaError) -> Response:
    return render(request, err.to_dict(), status=err.http_status)
