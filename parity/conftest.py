"""Shared fixtures for the Bugzilla REST parity suite.

The suite is black-box: it only talks HTTP to ``BASE_URL`` and knows nothing
about the implementation behind it. Run it against the Perl app to record the
contract (golden files), then against the Python port to prove parity.
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import httpx
import pytest

from normalize import dumps, normalize_response

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080/rest.cgi").rstrip("/")
ADMIN_EMAIL = os.environ.get("BZ_ADMIN_EMAIL", "admin@bugzilla.test")
ADMIN_PASSWORD = os.environ.get("BZ_ADMIN_PASSWORD", "password01!")
ADMIN_REALNAME = os.environ.get("BZ_ADMIN_REALNAME", "Test Admin")
# Configurable identity is blurred in snapshots so goldens do not depend on docker-compose.yml.
IDENTITY_REPLACEMENTS = {ADMIN_EMAIL: "<admin-email>", ADMIN_REALNAME: "<admin-name>"}

PRODUCT = "Parity Product"
COMPONENT = "Core"
VERSION = "1.0"
USER_EMAIL = "parity.user@bugzilla.test"
USER_NAME = "Parity User"
USER_PASSWORD = "ParityPassw0rd!"

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"
# mod_perl workers are recycled by Apache2::SizeLimit after a handful of requests and
# drop keep-alive connections, so every request uses a fresh connection.
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "Connection": "close"}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="(Re)record golden snapshots from the current BASE_URL. Only do this against the Perl app.",
    )


@dataclass
class Response:
    status: int
    body: Any
    headers: httpx.Headers

    @property
    def error(self) -> bool:
        return isinstance(self.body, dict) and bool(self.body.get("error"))

    @property
    def code(self) -> int | None:
        return self.body.get("code") if isinstance(self.body, dict) else None


class Api:
    """Minimal httpx wrapper that speaks the Bugzilla REST conventions."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._client = httpx.Client(timeout=60.0, transport=httpx.HTTPTransport(retries=2))

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        token: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        query: dict[str, Any] = dict(params or {})
        credentials = {k: v for k, v in (("token", token), ("api_key", api_key)) if v is not None}
        if credentials and isinstance(json_body, dict):
            json_body = {**json_body, **credentials}  # keep credentials out of access logs
        else:
            query.update(credentials)
        resp = self._client.request(
            method,
            f"{self.base_url}{path}",
            params=query,
            json=json_body,
            headers={**JSON_HEADERS, **(headers or {})},
        )
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return Response(resp.status_code, body, resp.headers)

    def get(self, path: str, **kw: Any) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any, **kw: Any) -> Response:
        return self.request("POST", path, json_body=json_body, **kw)

    def put(self, path: str, json_body: Any, **kw: Any) -> Response:
        return self.request("PUT", path, json_body=json_body, **kw)

    def login(self, login: str, password: str) -> str:
        resp = self.get("/login", params={"login": login, "password": password})
        assert resp.status == 200, resp.body
        return resp.body["token"]


@pytest.fixture(scope="session")
def run_id() -> str:
    """Per-run marker embedded in every created name so runs never collide."""
    return "p" + uuid.uuid4().hex[:9]


@pytest.fixture(scope="session")
def api() -> Api:
    return Api(BASE_URL)


@pytest.fixture(scope="session")
def admin(api: Api) -> str:
    return api.login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def product(api: Api, admin: str) -> dict[str, str]:
    """Idempotently create the product/component the suite files bugs into."""
    existing = api.get("/product", params={"names": PRODUCT, "include_fields": "name,components"}, token=admin)
    assert existing.status == 200, existing.body
    if not existing.body["products"]:
        created = api.post(
            "/product",
            {
                "name": PRODUCT,
                "description": "Fixture product owned by the REST parity suite.",
                "version": VERSION,
                "has_unconfirmed": True,
                "is_open": True,
            },
            token=admin,
        )
        assert created.status == 201, created.body
        components: set[str] = set()
    else:
        components = {c["name"] for c in existing.body["products"][0]["components"]}
    if COMPONENT not in components:
        created = api.post(
            "/component",
            {
                "product": PRODUCT,
                "name": COMPONENT,
                "description": "Fixture component owned by the REST parity suite.",
                "default_assignee": ADMIN_EMAIL,
            },
            token=admin,
        )
        assert created.status == 201, created.body
    return {"product": PRODUCT, "component": COMPONENT, "version": VERSION}


@pytest.fixture(scope="session")
def user(api: Api, admin: str) -> dict[str, str]:
    """A plain (non-admin) user that exists on both engines."""
    found = api.get("/user", params={"names": USER_EMAIL}, token=admin)
    if found.status != 200 or not found.body.get("users"):
        created = api.post(
            "/user", {"email": USER_EMAIL, "full_name": USER_NAME, "password": USER_PASSWORD}, token=admin
        )
        assert created.status == 201, created.body
    return {"email": USER_EMAIL, "name": USER_NAME, "password": USER_PASSWORD, "token": api.login(USER_EMAIL, USER_PASSWORD)}


@pytest.fixture
def bug_payload(product: dict[str, str], run_id: str) -> Callable[..., dict[str, Any]]:
    """Valid ``POST /bug`` body; override any field via kwargs."""

    def build(**overrides: Any) -> dict[str, Any]:
        payload = {
            "product": product["product"],
            "component": product["component"],
            "version": product["version"],
            "summary": f"Parity bug {run_id} {uuid.uuid4().hex[:6]}",
            "description": "Created by the REST parity suite.",
            "op_sys": "All",
            "platform": "All",
            "priority": "Normal",
            "severity": "normal",
        }
        payload.update(overrides)
        return {k: v for k, v in payload.items() if v is not None}

    return build


@pytest.fixture
def make_bug(api: Api, admin: str, bug_payload: Callable[..., dict[str, Any]]) -> Callable[..., int]:
    """Create a bug (as admin unless ``token`` is given) and return its id."""

    def create(token: str | None = None, **overrides: Any) -> int:
        resp = api.post("/bug", bug_payload(**overrides), token=token or admin)
        assert resp.status in (200, 201) and not resp.error, resp.body
        return resp.body["id"]

    return create


@pytest.fixture
def snapshot(request: pytest.FixtureRequest, run_id: str) -> Callable[[Response, str | None], None]:
    """Compare a normalised response to the golden file recorded from Perl.

    Golden files live in ``golden/<test module>/<test name>[.<label>].json``.
    """
    update = request.config.getoption("--update-golden")
    module = pathlib.Path(str(request.node.fspath)).stem
    test_name = request.node.name.replace("/", "_")

    def check(resp: Response, label: str | None = None) -> None:
        name = test_name + (f".{label}" if label else "")
        path = GOLDEN_DIR / module / f"{name}.json"
        actual = dumps(normalize_response(resp.status, resp.body, run_id, IDENTITY_REPLACEMENTS))
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(actual)
            return
        if not path.exists():
            pytest.fail(
                f"missing golden file {path.relative_to(GOLDEN_DIR.parent)}; "
                "record it with `make parity-update-golden` against the Perl app"
            )
        expected = path.read_text()
        if expected != actual:
            diff = "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=f"golden/{module}/{name}.json (Perl contract)",
                    tofile=f"{BASE_URL} (actual)",
                )
            )
            pytest.fail(f"response differs from the recorded contract:\n{diff}")

    return check


def assert_error(resp: Response, http_status: int, code: int) -> None:
    """Assert the Bugzilla error envelope: HTTP status + ``code`` + ``error: true``."""
    assert resp.status == http_status, (resp.status, resp.body)
    assert isinstance(resp.body, dict), resp.body
    assert resp.body.get("error") is True, resp.body
    assert resp.body.get("code") == code, resp.body
    assert isinstance(resp.body.get("message"), str) and resp.body["message"], resp.body
    assert resp.body.get("documentation", "").startswith("http"), resp.body


def assert_keys(obj: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if k not in obj]
    assert not missing, f"missing keys {missing} in {sorted(obj)}"
