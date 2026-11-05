"""Black-box parity fixtures.

Configuration (environment):
  BASE_URL            REST base, default http://127.0.0.1:8080/rest.cgi
  BZ_ADMIN_LOGIN / BZ_ADMIN_PASSWORD   admin account (docker-compose defaults)
  BZ_USER_LOGIN / BZ_USER_PASSWORD     a non-admin seeded account

The suite expects ``scripts/seed_demo_data.py`` to have run against BASE_URL's
database (bugs aliased seed-01..seed-20). Write tests create their own bugs.
"""
import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080/rest.cgi").rstrip("/")
ADMIN_LOGIN = os.environ.get("BZ_ADMIN_LOGIN", "admin@bugzilla.test")
ADMIN_PASSWORD = os.environ.get("BZ_ADMIN_PASSWORD", "password01!")
USER_LOGIN = os.environ.get("BZ_USER_LOGIN", "alice.ng@bugzilla.test")
USER_PASSWORD = os.environ.get("BZ_USER_PASSWORD", "Seed-pass-2024!")

DOC_URL = "https://bugzilla.readthedocs.org/en/5.0/api/"

SEED_PRODUCT = "Orbit CRM"
SEED_COMPONENT = "Contacts"
SEED_VERSION = "2.4"


class Api:
    """Thin httpx wrapper: ``api.get(path, token=..., params=...)`` -> httpx.Response."""

    def __init__(self, base_url):
        self.base_url = base_url
        # Apache/mod_perl drops idle keep-alive connections, which surfaces as
        # httpx.RemoteProtocolError mid-run; use one connection per request.
        self.client = httpx.Client(base_url=base_url, timeout=60.0,
                                   headers={"Accept": "application/json", "Connection": "close"},
                                   limits=httpx.Limits(max_keepalive_connections=0))

    def request(self, method, path, token=None, params=None, json=None, headers=None):
        params = dict(params or {})
        if token:
            params["token"] = token
        return self.client.request(method, path, params=params, json=json, headers=headers)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def login(self, login, password):
        r = self.get("/login", params={"login": login, "password": password})
        assert r.status_code == 200, r.text
        return r.json()["token"]


@pytest.fixture(scope="session")
def api():
    return Api(BASE_URL)


@pytest.fixture(scope="session")
def admin_token(api):
    return api.login(ADMIN_LOGIN, ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def user_token(api):
    return api.login(USER_LOGIN, USER_PASSWORD)


@pytest.fixture(scope="session")
def seed(api):
    """Ids of the seeded bugs keyed by alias; fails fast if the seed is missing."""
    r = api.get("/bug", params={"alias": ["seed-01", "seed-03", "seed-05"],
                                "include_fields": "id,alias"})
    assert r.status_code == 200, r.text
    ids = {b["alias"][0]: b["id"] for b in r.json()["bugs"]}
    if len(ids) != 3:
        pytest.fail(f"seed data missing at {BASE_URL}; run scripts/seed_demo_data.py first")
    return ids


def unique(prefix="parity"):
    return f"{prefix} {uuid.uuid4().hex[:10]}"


@pytest.fixture
def new_bug(api, admin_token):
    """Factory: create a fresh bug as admin and return its id."""
    def _create(**overrides):
        body = {
            "product": SEED_PRODUCT, "component": SEED_COMPONENT, "version": SEED_VERSION,
            "summary": unique(), "description": "created by the parity suite",
            "op_sys": "All", "platform": "All", "priority": "Normal", "severity": "normal",
        }
        body.update(overrides)
        r = api.post("/bug", token=admin_token, json=body)
        assert r.status_code == 200, r.text
        return r.json()["id"]
    return _create


def expect_error(resp, status, code, message_contains=None):
    """Assert a Bugzilla REST error envelope with the given HTTP status and error code."""
    assert resp.status_code == status, f"{resp.status_code} {resp.text}"
    body = resp.json()
    assert set(body) == {"error", "code", "message", "documentation"}, body
    assert body["error"] is True
    assert body["code"] == code, body
    assert body["documentation"] == DOC_URL
    assert isinstance(body["message"], str) and body["message"]
    if message_contains:
        assert message_contains in body["message"], body["message"]
    return body


def ok(resp, status=200):
    assert resp.status_code == status, f"{resp.status_code} {resp.text}"
    return resp.json()
