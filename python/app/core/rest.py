"""REST plumbing shared by every module: parameter merging, auth, response envelope.

Mirrors Bugzilla::WebService::Server::REST: query-string params + JSON body merged
into one hash, credential renaming, include/exclude_fields splitting, JSONP via
``callback``, ``Access-Control-Allow-Origin: *``, an ETag, and error envelopes
``{"error": true, "code": N, "message": "...", "documentation": "..."}``.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.auth import ANONYMOUS, User, authenticate
from app.core.db import get_db
from app.core.errors import BugzillaError
from app.core.filter import Filter

CREDENTIAL_RENAMES = {"login": "Bugzilla_login", "password": "Bugzilla_password",
                      "api_key": "Bugzilla_api_key", "token": "Bugzilla_token"}


@dataclass
class Ctx:
    request: Request
    db: Session
    params: dict[str, Any]
    user: User
    filter: Filter

    def param_list(self, *names) -> list:
        """First present param among ``names`` as a list (REST params can be scalar, csv or list)."""
        for n in names:
            v = self.params.get(n)
            if v is None:
                continue
            if isinstance(v, list):
                return v
            if isinstance(v, str) and "," in v:
                return [p.strip() for p in v.split(",") if p.strip()]
            return [v]
        return []


async def merged_params(request: Request) -> dict[str, Any]:
    params: dict[str, Any] = {}
    body = await request.body()
    if body:
        try:
            data = json.loads(body)
        except ValueError:
            raise BugzillaError("json_rpc_invalid_params", "The JSON body is not valid.", code=32602)
        if isinstance(data, dict):
            params.update(data)
    # Perl merges the JSON body first and then overlays the query string, so ?priority=High wins
    for key in request.query_params.keys():
        values = request.query_params.getlist(key)
        params[key] = values if len(values) > 1 else values[0]
    has_login_pair = "login" in params and "password" in params
    for raw, bz in CREDENTIAL_RENAMES.items():
        if raw in params and (has_login_pair or raw not in ("login", "password")):
            params[bz] = params.pop(raw)
    params["_remote_ip"] = request.client.host if request.client else None
    return params


async def context(request: Request, db: Session = Depends(get_db)) -> Ctx:
    params = await merged_params(request)
    user = authenticate(db, params) if any(k in params for k in CREDENTIAL_RENAMES.values()) else ANONYMOUS
    callback = params.get("callback")
    if callback is not None and not re.match(r"^[A-Za-z0-9_.\[\]]+$", str(callback)):
        raise BugzillaError("json_rpc_invalid_callback",
                            f"You cannot use '{callback}' as your 'callback' parameter. For security reasons, only "
                            "letters, numbers, and the following characters are allowed in the 'callback' parameter: []._")
    request.state.callback = callback
    return Ctx(request=request, db=db, params=params, user=user, filter=Filter.from_params(params))


def json_response(request: Request, payload: Any, status: int = 200) -> Response:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    headers = {"Access-Control-Allow-Origin": "*", "X-Bugzilla-Backend": "python",
               "Access-Control-Allow-Headers": "origin, content-type, accept, x-requested-with",
               "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"'}
    callback = getattr(request.state, "callback", None)
    if callback and re.match(r"^[A-Za-z0-9_.\[\]]+$", str(callback)):
        return Response(f"/**/{callback}({body})", status_code=status, media_type="text/javascript",
                        headers=headers)
    return Response(body, status_code=status, media_type="application/json", headers=headers)


def error_response(request: Request, err: BugzillaError) -> Response:
    return json_response(request, err.envelope(), err.http_status)
