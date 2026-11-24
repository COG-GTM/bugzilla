"""Bugzilla error semantics: ThrowUserError/ThrowCodeError -> REST error envelope."""
from app.core.error_codes import ERROR_UNKNOWN_TRANSIENT, REST_STATUS_CODE_MAP, WS_ERROR_CODE

DOCUMENTATION = "https://bugzilla.readthedocs.org/en/5.0/api/"


class BugzillaError(Exception):
    """A Perl ``ThrowUserError``/``ThrowCodeError`` equivalent.

    ``name`` is the error tag from the templates (e.g. ``bug_id_does_not_exist``);
    the numeric code comes from WS_ERROR_CODE and the HTTP status from
    REST_STATUS_CODE_MAP, exactly like Bugzilla::WebService::Server::REST.
    """

    def __init__(self, name: str, message: str, code: int | None = None):
        self.name = name
        self.message = message
        self.code = code if code is not None else WS_ERROR_CODE.get(name, ERROR_UNKNOWN_TRANSIENT)
        super().__init__(message)

    @property
    def http_status(self) -> int:
        return REST_STATUS_CODE_MAP.get(self.code, 400)

    def envelope(self) -> dict:
        return {"error": True, "code": self.code, "message": self.message, "documentation": DOCUMENTATION}


def param_required(param: str, function: str | None = None) -> BugzillaError:
    fn = f"The function requires a {param} argument" if function is None else \
        f"{function} requires a {param} argument"
    return BugzillaError("param_required", f"{fn}, and that argument was not set.")


def params_required(function: str, params: list[str]) -> BugzillaError:
    return BugzillaError("params_required",
                         f"{function} requires one of the following parameters: {', '.join(params)}")


def login_required() -> BugzillaError:
    return BugzillaError("login_required", "You must log in before using this part of Bugzilla.")


def rest_resource_not_found(method: str, path: str) -> BugzillaError:
    return BugzillaError("rest_invalid_resource", f"A REST API resource was not found for '{method} {path}'.",
                         code=32614)
