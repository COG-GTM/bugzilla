"""Error codes and HTTP status mapping ported from Bugzilla::WebService::Constants.

Error code numbers and names are kept identical to the Perl API so that
existing clients continue to work unchanged.
"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

# ---------------------------------------------------------------------------
# HTTP status constants (mirrors Constants.pm)
# ---------------------------------------------------------------------------
STATUS_OK = 200
STATUS_CREATED = 201
STATUS_ACCEPTED = 202
STATUS_NO_CONTENT = 204
STATUS_MULTIPLE_CHOICES = 300
STATUS_BAD_REQUEST = 400
STATUS_NOT_AUTHORIZED = 401
STATUS_NOT_FOUND = 404
STATUS_GONE = 410

# ---------------------------------------------------------------------------
# WS_ERROR_CODE — maps symbolic error names to numeric codes.
# Ported from Constants.pm lines 56-247.
# ---------------------------------------------------------------------------
WS_ERROR_CODE: dict[str, int] = {
    # Generic errors (50-99)
    "object_not_specified": 50,
    "reassign_to_empty": 50,
    "param_required": 50,
    "params_required": 50,
    "undefined_field": 50,
    "object_does_not_exist": 51,
    "param_must_be_numeric": 52,
    "number_not_numeric": 52,
    "param_invalid": 53,
    "number_too_large": 54,
    "number_too_small": 55,
    "illegal_date": 56,
    "param_integer_required": 57,
    "param_scalar_array_required": 58,
    # Authentication errors (300-410)
    "invalid_login_or_password": 300,
    "account_disabled": 301,
    "auth_invalid_email": 302,
    "extern_id_conflict": -303,
    "auth_failure": 304,
    "password_too_short": 305,
    "password_not_complex": 305,
    "api_key_not_valid": 306,
    "api_key_revoked": 306,
    "auth_invalid_token": 307,
    "login_required": 410,
    # User errors (500-600)
    "account_exists": 500,
    "illegal_email_address": 501,
    "auth_cant_create_account": 501,
    "account_creation_disabled": 501,
    "account_creation_restricted": 501,
    "password_too_short_user": 502,
    "invalid_username": 504,
    "invalid_user_group": 504,
    "user_access_by_id_denied": 505,
    "user_access_by_match_denied": 505,
    # Group errors (800-900)
    "empty_group_name": 800,
    "group_exists": 801,
    "empty_group_description": 802,
    "invalid_regexp": 803,
    "invalid_group_name": 804,
    "group_cannot_view": 805,
    # REST-specific
    "rest_invalid_resource": 32614,
}

# ---------------------------------------------------------------------------
# REST_STATUS_CODE_MAP — maps numeric error codes to HTTP status codes.
# Ported from Constants.pm lines 266-293.
# ---------------------------------------------------------------------------
REST_STATUS_CODE_MAP: dict[int, int] = {
    51: STATUS_NOT_FOUND,
    101: STATUS_NOT_FOUND,
    102: STATUS_NOT_AUTHORIZED,
    106: STATUS_NOT_AUTHORIZED,
    109: STATUS_NOT_AUTHORIZED,
    110: STATUS_NOT_AUTHORIZED,
    113: STATUS_NOT_AUTHORIZED,
    115: STATUS_NOT_AUTHORIZED,
    120: STATUS_NOT_AUTHORIZED,
    300: STATUS_NOT_AUTHORIZED,
    301: STATUS_NOT_AUTHORIZED,
    302: STATUS_NOT_AUTHORIZED,
    303: STATUS_NOT_AUTHORIZED,
    304: STATUS_NOT_AUTHORIZED,
    410: STATUS_NOT_AUTHORIZED,
    504: STATUS_NOT_AUTHORIZED,
    505: STATUS_NOT_AUTHORIZED,
    32614: STATUS_NOT_FOUND,
}


def _http_status_for_code(error_code: int) -> int:
    return REST_STATUS_CODE_MAP.get(error_code, STATUS_BAD_REQUEST)


class BugzillaApiError(HTTPException):
    """Raise to return the canonical ``{error, message, code}`` envelope."""

    def __init__(self, error_name: str, message: str) -> None:
        code = WS_ERROR_CODE.get(error_name, 0)
        status = _http_status_for_code(code)
        detail = {"error": True, "message": message, "code": code}
        super().__init__(status_code=status, detail=detail)
        self.error_name = error_name
        self.ws_code = code


async def bugzilla_error_handler(_request: Request, exc: BugzillaApiError) -> JSONResponse:
    """Return errors in the Bugzilla REST API envelope format."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
    )
