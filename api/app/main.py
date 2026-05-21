"""FastAPI application — Bugzilla User WebService REST API.

This is the modernised Python port of the Perl REST endpoints defined in
``Bugzilla/WebService/Server/REST/Resources/User.pm``.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.errors import BugzillaApiError, bugzilla_error_handler
from app.routers import user

app = FastAPI(
    title="Bugzilla REST API",
    description="FastAPI port of the Bugzilla WebService User API",
    version="0.1.0",
)

# CORS — match the Perl API's "Access-Control-Allow-Origin: *" header
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["origin", "content-type", "accept", "x-requested-with"],
)

# Register the Bugzilla-style error handler
app.add_exception_handler(BugzillaApiError, bugzilla_error_handler)  # type: ignore[arg-type]

# Mount routers
app.include_router(user.router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Bugzilla REST API is running"}
