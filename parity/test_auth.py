"""Contract tests for ``User.login``/``logout``/``valid_login`` and the REST auth matrix."""

import re

from conftest import ADMIN_EMAIL, ADMIN_PASSWORD, Api, assert_error

TOKEN_RE = re.compile(r"^\d+-[A-Za-z0-9]+$")


def test_login_returns_id_and_token(api: Api, snapshot):
    resp = api.get("/login", params={"login": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status == 200
    assert isinstance(resp.body["id"], int) and resp.body["id"] > 0
    assert TOKEN_RE.match(resp.body["token"])
    assert resp.body["token"].split("-")[0] == str(resp.body["id"])
    snapshot(resp)


def test_login_wrong_password(api: Api, snapshot):
    resp = api.get("/login", params={"login": ADMIN_EMAIL, "password": "definitely-wrong"})
    assert_error(resp, 401, 300)
    snapshot(resp)


def test_login_unknown_user_is_indistinguishable(api: Api, snapshot):
    resp = api.get("/login", params={"login": "nobody@bugzilla.test", "password": "definitely-wrong"})
    assert_error(resp, 401, 300)
    snapshot(resp)


def test_login_missing_password(api: Api, snapshot):
    resp = api.get("/login", params={"login": ADMIN_EMAIL})
    assert_error(resp, 400, 50)
    snapshot(resp)


def test_login_missing_login(api: Api, snapshot):
    resp = api.get("/login", params={"password": ADMIN_PASSWORD})
    assert_error(resp, 400, 50)
    snapshot(resp)


def test_valid_login_true_for_fresh_token(api: Api, snapshot):
    token = api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = api.get("/valid_login", params={"login": ADMIN_EMAIL}, token=token)
    assert resp.status == 200
    assert resp.body == {"result": True}
    snapshot(resp)


def test_valid_login_false_without_token(api: Api, snapshot):
    resp = api.get("/valid_login", params={"login": ADMIN_EMAIL})
    assert resp.status == 200
    assert resp.body == {"result": False}
    snapshot(resp)


def test_valid_login_false_for_other_user(api: Api, user: dict):
    resp = api.get("/valid_login", params={"login": ADMIN_EMAIL}, token=user["token"])
    assert resp.status == 200
    assert resp.body == {"result": False}


def test_valid_login_with_garbage_token(api: Api, snapshot):
    resp = api.get("/valid_login", params={"login": ADMIN_EMAIL}, token="1-notavalidtoken")
    assert_error(resp, 400, 32000)
    snapshot(resp)


def test_logout_invalidates_token(api: Api, snapshot):
    token = api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = api.get("/logout", token=token)
    assert resp.status == 200
    assert resp.body == {"result": None}
    snapshot(resp)

    after = api.get("/valid_login", params={"login": ADMIN_EMAIL}, token=token)
    assert_error(after, 400, 32000)


def test_logout_anonymous_is_noop(api: Api, snapshot):
    resp = api.get("/logout")
    assert resp.status == 200
    assert resp.body == {"result": None}
    snapshot(resp)


def test_write_requires_login(api: Api, bug_payload, snapshot):
    resp = api.post("/bug", bug_payload())
    assert_error(resp, 401, 410)
    snapshot(resp)


def test_invalid_token_on_read(api: Api, snapshot):
    resp = api.get("/bug/1", token="1-notavalidtoken")
    assert_error(resp, 400, 32000)
    snapshot(resp)


def test_invalid_api_key(api: Api, snapshot):
    resp = api.get("/bug/1", api_key="notavalidapikey")
    assert_error(resp, 400, 306)
    snapshot(resp)


def test_inline_login_and_password_params(api: Api, make_bug):
    bug_id = make_bug()
    resp = api.get(f"/bug/{bug_id}", params={"login": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
                                               "include_fields": "id,creator"})
    assert resp.status == 200
    # include_fields=creator also pulls in creator_detail
    assert resp.body["bugs"][0]["id"] == bug_id and resp.body["bugs"][0]["creator"] == ADMIN_EMAIL


def test_inline_login_with_wrong_password(api: Api, snapshot):
    resp = api.get("/bug/1", params={"login": ADMIN_EMAIL, "password": "nope"})
    assert_error(resp, 401, 300)
    snapshot(resp)


def test_bugzilla_prefixed_credentials(api: Api, make_bug):
    bug_id = make_bug()
    resp = api.get(f"/bug/{bug_id}", params={"Bugzilla_login": ADMIN_EMAIL, "Bugzilla_password": ADMIN_PASSWORD,
                                               "include_fields": "id"})
    assert resp.status == 200
    assert resp.body["bugs"][0] == {"id": bug_id}


def test_token_in_json_body_for_post(api: Api, admin: str, bug_payload):
    body = bug_payload()
    body["token"] = admin
    resp = api.post("/bug", body)
    assert resp.status in (200, 201) and not resp.error, resp.body
    assert isinstance(resp.body["id"], int)


def test_plain_user_can_create_and_read(api: Api, user: dict, make_bug):
    bug_id = make_bug(token=user["token"])
    resp = api.get(f"/bug/{bug_id}", params={"include_fields": "id,creator"}, token=user["token"])
    assert resp.status == 200
    assert resp.body["bugs"][0]["id"] == bug_id and resp.body["bugs"][0]["creator"] == user["email"]


def test_plain_user_cannot_create_product(fixture_api: Api, user: dict, snapshot):
    # Product.create is not migrated yet: exercised on the engine that owns it.
    resp = fixture_api.post("/product", {"name": "Forbidden", "description": "x", "version": "1"}, token=user["token"])
    assert_error(resp, 401, 304)
    snapshot(resp)


def test_plain_user_cannot_create_user(fixture_api: Api, user: dict, snapshot):
    resp = fixture_api.post("/user", {"email": "x@bugzilla.test", "password": "Passw0rd!!"}, token=user["token"])
    assert_error(resp, 401, 304)
    snapshot(resp)
