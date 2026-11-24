import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080/rest.cgi").rstrip("/")
ADMIN_LOGIN = os.environ.get("BZ_ADMIN_EMAIL", "admin@bugzilla.test")
ADMIN_PASSWORD = os.environ.get("BZ_ADMIN_PASSWORD", "password01!")

# Created by scripts/seed_demo_data.py.
PRODUCT = "WebStore"
ALICE = "alice.nguyen@bugzilla.test"
BOB = "bob.martins@bugzilla.test"
DOCS = "https://bugzilla.readthedocs.org/en/5.0/api/"

USER = {
    "admin": {"email": ADMIN_LOGIN, "id": "<ID>", "name": ADMIN_LOGIN, "real_name": "Test Admin"},
    "alice": {"email": ALICE, "id": "<ID>", "name": ALICE, "real_name": "Alice Nguyen"},
    "bob": {"email": BOB, "id": "<ID>", "name": BOB, "real_name": "Bob Martins"},
}


class Api:
    """Thin REST client. A token, when set, is sent as the ``token`` parameter."""

    def __init__(self, http, token=None):
        self.http = http
        self.token = token

    def request(self, method, path, params=None, json=None):
        params = dict(params or {})
        if self.token:
            params.setdefault("token", self.token)
        return self.http.request(method, path, params=params, json=json)

    def get(self, path, **params):
        return self.request("GET", path, params)

    def post(self, path, json=None, **params):
        return self.request("POST", path, params, json)

    def put(self, path, json=None, **params):
        return self.request("PUT", path, params, json)


def error(code, message):
    return {"error": True, "code": code, "message": message, "documentation": DOCS}


def assert_error(resp, status, code, message):
    assert (resp.status_code, resp.json()) == (status, error(code, message))


@pytest.fixture(scope="session")
def http():
    # The Perl app closes the connection after each response, so do not reuse connections.
    limits = httpx.Limits(max_keepalive_connections=0)
    headers = {"Accept": "application/json"}
    if os.environ.get("PARITY_TARGET", "perl") == "perl":
        # Apache proxies ported modules to Python; this header keeps the request on Perl.
        headers["X-Bugzilla-Backend"] = "perl"
    with httpx.Client(base_url=BASE_URL, timeout=120, limits=limits, headers=headers) as client:
        yield client


@pytest.fixture(scope="session")
def anon(http):
    return Api(http)


def login(http, user=ADMIN_LOGIN, password=ADMIN_PASSWORD):
    resp = http.get("/login", params={"login": user, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="session")
def admin(http):
    api = Api(http, login(http)["token"])
    seeded = api.get("/product", names=PRODUCT).json().get("products")
    assert seeded, f"product {PRODUCT} not found: run scripts/seed_demo_data.py first"
    return api


@pytest.fixture(scope="session")
def run_tag():
    """Unique per run, so searches only see the bugs this run created."""
    return "parity-" + uuid.uuid4().hex[:12]


def bug_fields(summary, **overrides):
    fields = {
        "product": PRODUCT, "component": "Catalog", "version": "1.0",
        "summary": summary, "description": "Created by the parity suite.",
        "op_sys": "All", "platform": "All", "priority": "Low", "severity": "minor",
    }
    fields.update(overrides)
    return fields


@pytest.fixture(scope="session")
def make_bug(admin, run_tag):
    def create(name, **overrides):
        resp = admin.post("/bug", bug_fields(f"{run_tag} {name}", **overrides))
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]
    return create


@pytest.fixture
def bug(make_bug):
    return make_bug("scratch")
