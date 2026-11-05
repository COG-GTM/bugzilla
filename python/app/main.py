"""FastAPI entry point. Every ``app/modules/<name>/router.py`` exposing ``router`` is mounted
automatically under ``/rest.cgi`` (and ``/rest``) so modules can be added without editing a list.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import BugzillaError, user_error
from app.core.responses import render, render_error
import app.modules as modules_pkg

log = logging.getLogger("bugzilla.api")

app = FastAPI(title="Bugzilla REST (Python)", docs_url="/rest.cgi/docs", openapi_url="/rest.cgi/openapi.json")


def discover_routers() -> list[str]:
    mounted = []
    for info in sorted(pkgutil.iter_modules(modules_pkg.__path__), key=lambda i: i.name):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{modules_pkg.__name__}.{info.name}.router")
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.endswith(".router"):
                continue
            raise
        router = getattr(mod, "router", None)
        if router is None:
            continue
        for prefix in ("/rest.cgi", "/rest"):
            app.include_router(router, prefix=prefix)
        mounted.append(info.name)
    return mounted


@app.exception_handler(BugzillaError)
async def bugzilla_error_handler(request: Request, exc: BugzillaError) -> Response:
    return render_error(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    # Perl never 422s: a malformed id falls through to the resource's own validation.
    return render_error(request, BugzillaError("invalid_parameter", str(exc.errors()[0].get("msg", "invalid")), code=53))


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> Response:
    log.exception("unhandled error")
    err = BugzillaError("internal_error", str(exc), code=100500, http_status=200 if settings().compat_die_200 else 500)
    return render_error(request, err)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}


def allowed_methods(path: str) -> list[str]:
    from starlette.routing import Match

    methods: set[str] = set()
    scope = {"type": "http", "path": path, "method": "GET", "root_path": "", "headers": []}

    def walk(routes, sub_path: str) -> None:
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is None and hasattr(route, "routes"):
                inner = route
            if inner is not None:
                # included routers keep un-prefixed paths; try each mount prefix
                for prefix in ("/rest.cgi", "/rest"):
                    if sub_path.startswith(prefix + "/"):
                        walk(inner.routes, sub_path[len(prefix):])
                continue
            route_methods = getattr(route, "methods", None)
            if not route_methods or getattr(route, "name", "") == "rest_not_found":
                continue
            for m in route_methods:
                if m != "HEAD" and route.matches({**scope, "path": sub_path, "method": m})[0] == Match.FULL:
                    methods.add(m)

    walk(app.routes, path)
    return sorted(methods)


@app.middleware("http")
async def options_and_fallback(request: Request, call_next):
    # Perl answers OPTIONS <resource> with the methods that resource supports (Allow + CORS header).
    if request.method == "OPTIONS":
        methods = allowed_methods(request.url.path)
        if methods:
            allow = ", ".join(methods)
            return Response(status_code=200, headers={
                "Allow": allow, "Access-Control-Allow-Methods": allow, "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "origin, content-type, accept, x-requested-with",
            })
    return await call_next(request)


MOUNTED_MODULES = discover_routers()
log.info("mounted modules: %s", ", ".join(MOUNTED_MODULES))


# Registered last so every module route wins; Perl reports unknown paths/methods as 32614.
@app.api_route("/rest.cgi/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
@app.api_route("/rest/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
async def rest_not_found(request: Request, path: str) -> Response:
    raise user_error("rest_invalid_resource", method=request.method, path="/" + path)
