"""Bugzilla REST, Python port. One FastAPI app; every package under
app/modules/ that has a ``router.py`` exposing ``router`` is mounted at
startup, so adding a module never touches this file."""
import importlib
import pkgutil

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from . import modules
from .core.errors import BugzillaError, error_response
from .core.fallback import NotPorted, forward_to_perl

# Apache proxies the original path, so accept it with or without the prefix.
PREFIXES = ("/rest.cgi", "/rest")
# Sent by Perl on every REST response (Bugzilla/WebService/Server/REST.pm).
CORS = [(b"access-control-allow-origin", b"*"),
        (b"access-control-allow-headers", b"origin, content-type, accept, x-requested-with")]


class StripRestPrefix:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            for prefix in PREFIXES:
                if path == prefix or path.startswith(prefix + "/"):
                    path = path[len(prefix):] or "/"
                    break
            scope = dict(scope, path=path, raw_path=path.encode(), bz_path=path)

            async def send_with_backend(message):
                if message["type"] == "http.response.start" and not any(
                        k == b"x-bugzilla-backend" for k, _ in message.get("headers", [])):
                    message["headers"] = [*message["headers"], (b"x-bugzilla-backend", b"python"), *CORS]
                await send(message)
            return await self.app(scope, receive, send_with_backend)
        return await self.app(scope, receive, send)


def discover_routers(app):
    for info in sorted(pkgutil.iter_modules(modules.__path__), key=lambda i: i.name):
        try:
            module = importlib.import_module(f"{modules.__name__}.{info.name}.router")
        except ModuleNotFoundError as exc:
            if exc.name != f"{modules.__name__}.{info.name}.router":
                raise
            continue
        app.include_router(module.router, tags=[info.name])


app = FastAPI(title="Bugzilla REST (Python port)", version="5.2+", redirect_slashes=False)
discover_routers(app)
app.add_middleware(StripRestPrefix)


# Anything no module routes is still Perl's (strangler fallback).
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
               include_in_schema=False)
async def unrouted(request: Request):
    return await forward_to_perl(request)


@app.exception_handler(NotPorted)
async def not_ported(request: Request, exc: NotPorted):
    return await forward_to_perl(request)


@app.exception_handler(BugzillaError)
async def bugzilla_error(request: Request, exc: BugzillaError):
    return error_response(exc)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if exc.status_code in (404, 405):
        # A ported path with a method this service does not route.
        return await forward_to_perl(request)
    return error_response(BugzillaError(100500, str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_response(BugzillaError(32000, str(exc.errors())))
