"""FastAPI entry point. Routers are auto-discovered from app/modules/*/router.py."""
import importlib
import logging
import pkgutil

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.modules as modules_pkg
from app.core.config import settings
from app.core.errors import BugzillaError, rest_resource_not_found
from app.core.rest import error_response

log = logging.getLogger("bugzilla")

app = FastAPI(title="Bugzilla REST (Python)", version=settings.version, docs_url="/docs", openapi_url="/openapi.json")


def discover_routers() -> list[str]:
    found = []
    for mod in pkgutil.iter_modules(modules_pkg.__path__):
        if not mod.ispkg:
            continue
        try:
            router_mod = importlib.import_module(f"{modules_pkg.__name__}.{mod.name}.router")
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.endswith(".router"):
                continue
            raise
        app.include_router(router_mod.router, prefix=settings.rest_prefix)
        found.append(mod.name)
    return sorted(found)


MODULES = discover_routers()
log.info("modules: %s", MODULES)


@app.exception_handler(BugzillaError)
async def bugzilla_error(request: Request, exc: BugzillaError):
    return error_response(request, exc)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (404, 405):
        return error_response(request, rest_resource_not_found(request.method, request.url.path.removeprefix(
            settings.rest_prefix)))
    return error_response(request, BugzillaError("unknown_error", str(exc.detail), code=32000))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return error_response(request, BugzillaError("param_required", str(exc), code=50))


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "modules": MODULES}
