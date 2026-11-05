"""Bugzilla::WebService::User login/logout/valid_login and the REST credential rules."""
import re

from parity.conftest import ADMIN_LOGIN, ADMIN_PASSWORD, USER_LOGIN, USER_PASSWORD, expect_error, ok
from parity.normalize import ID, TOKEN, normalize


def test_login_returns_id_and_token(api):
    body = ok(api.get("/login", params={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}))
    assert normalize(body) == {"id": ID, "token": TOKEN}
    assert body["token"].startswith(f"{body['id']}-")


def test_login_wrong_password(api):
    expect_error(api.get("/login", params={"login": ADMIN_LOGIN, "password": "wrong"}),
                 401, 300, "The login or password you entered is not valid.")


def test_login_unknown_user_same_error_as_wrong_password(api):
    expect_error(api.get("/login", params={"login": "nobody@example.test", "password": "x"}),
                 401, 300, "The login or password you entered is not valid.")


def test_login_requires_login_param(api):
    expect_error(api.get("/login"), 400, 50, "requires a login argument")


def test_login_requires_password_param(api):
    expect_error(api.get("/login", params={"login": ADMIN_LOGIN}), 400, 50,
                 "requires a password argument")


def test_login_is_get_only(api):
    expect_error(api.post("/login", json={"login": USER_LOGIN, "password": USER_PASSWORD}), 404, 32614,
                 "A REST API resource was not found for 'POST /login'.")


def test_valid_login_true_with_token(api):
    token = api.login(USER_LOGIN, USER_PASSWORD)
    assert ok(api.get("/valid_login", params={"login": USER_LOGIN}, token=token)) == {"result": True}


def test_valid_login_false_without_token(api):
    assert ok(api.get("/valid_login", params={"login": USER_LOGIN})) == {"result": False}


def test_valid_login_false_for_other_user(api, admin_token):
    assert ok(api.get("/valid_login", params={"login": USER_LOGIN}, token=admin_token)) == {"result": False}


def test_valid_login_requires_login(api, admin_token):
    expect_error(api.get("/valid_login", token=admin_token), 400, 50, "requires a login argument")


def test_bogus_token_is_rejected(api):
    expect_error(api.get("/valid_login", params={"login": ADMIN_LOGIN}, token="1-bogus"), 400, 32000,
                 "The cookies or token provide were not valid or have expired")


def test_malformed_token_is_treated_as_anonymous(api, seed):
    # a token without the "<userid>-" prefix is not parsed as a login cookie at all
    body = ok(api.get("/bug/seed-01", params={"include_fields": "alias"}, token="not-a-token"))
    assert body["bugs"][0]["alias"] == ["seed-01"]


def test_logout_invalidates_token(api):
    token = api.login(USER_LOGIN, USER_PASSWORD)
    assert ok(api.get("/logout", token=token)) == {"result": None}
    expect_error(api.get("/valid_login", params={"login": USER_LOGIN}, token=token), 400, 32000)


def test_logout_anonymous_is_noop(api):
    assert ok(api.get("/logout")) == {"result": None}


def test_logout_bogus_token(api):
    expect_error(api.get("/logout", token="1-bogus"), 400, 32000)


def test_query_string_credentials_authenticate_any_call(api):
    # login/password in the query string log the caller in; the request then fails
    # on validation (code 50) instead of on login_required (410).
    expect_error(api.post("/bug", params={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD},
                          json={"product": "x"}), 400, 50)


def test_write_without_credentials_requires_login(api):
    expect_error(api.post("/bug", json={"product": "x"}), 401, 410,
                 "You must log in before using this part of Bugzilla.")


def test_token_format(api, admin_token):
    assert re.match(r"^\d+-[A-Za-z0-9]{10}$", admin_token), admin_token
