"""Per-request plumbing: merge query string + JSON body into one param dict (as
Bugzilla::WebService::Server::REST does), authenticate, and hand modules a context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import User, authenticate
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import BugzillaError


@dataclass
class Ctx:
    request: Request
    session: Session
    params: dict[str, Any]
    user: User

    @property
    def bz_params(self) -> dict[str, Any]:
        return settings().params()


def _query_params(request: Request) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in request.query_params.multi_items():
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    return out


async def _body_params(request: Request) -> dict[str, Any]:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return {}
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise BugzillaError("json_rpc_invalid_params", "malformed JSON string", code=32700)
    if not isinstance(data, dict):
        raise BugzillaError("json_rpc_invalid_params", "params must be an object", code=32602)
    return data


async def ctx(request: Request, session: Session = Depends(get_session)) -> Ctx:
    params = _query_params(request)
    params.update(await _body_params(request))  # body wins, as in Perl's REST server
    params.pop("callback", None)
    user = authenticate(session, params, request)
    return Ctx(request=request, session=session, params=params, user=user)
