"""Bugzilla error envelope and the REST status map
(Bugzilla/WebService/Constants.pm: WS_ERROR_CODE, REST_STATUS_CODE_MAP)."""
from fastapi import Request
from fastapi.responses import JSONResponse

from .config import DOCS_URL

NOT_FOUND = {51, 101, 32614}
UNAUTHORIZED = {102, 106, 109, 110, 113, 115, 120, 300, 301, 302, 303, 304, 410, 504, 505}


class BugzillaError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def status(self):
        if self.code in NOT_FOUND:
            return 404
        return 401 if self.code in UNAUTHORIZED else 400


def error_response(exc):
    body = {"error": True, "code": exc.code, "message": exc.message, "documentation": DOCS_URL}
    return JSONResponse(body, status_code=exc.status)


# Errors shared by every module. Module-specific errors live in the module.
def param_required(param, function=None):
    """Perl names the function only where the caller passes one."""
    subject = f"The function {function}" if function else "The function"
    return BugzillaError(50, f"{subject} requires a {param} argument, and that argument was not set.")


def invalid_json(detail):
    return BugzillaError(32000, f"Could not parse the 'params' argument as valid JSON. Error: {detail}")


def login_required():
    return BugzillaError(410, "You must log in before using this part of Bugzilla.")


def invalid_token():
    return BugzillaError(32000, "The cookies or token provide were not valid or have expired. "
                                "You may login again to get new cookies or a new token.")


def invalid_api_key():
    return BugzillaError(306, "The API key you specified is invalid. Please check that you typed it correctly.")


def illegal_date(value):
    return BugzillaError(56, f"'{value}' is not a legal date.")


def resource_not_found(request: Request):
    path = request.scope.get("bz_path", request.url.path)
    return BugzillaError(32614, f"A REST API resource was not found for '{request.method} {path}'.")
