"""Bugzilla::WebService::User session endpoints: login, logout, valid_login."""
import re

from conftest import ADMIN_LOGIN, ALICE, Api, assert_error, login
from normalize import normalize

BAD_TOKEN = ("The cookies or token provide were not valid or have expired. "
             "You may login again to get new cookies or a new token.")


def test_login(http):
    body = login(http)
    assert isinstance(body["id"], int)
    assert re.match(rf"^{body['id']}-[A-Za-z0-9]{{10}}$", body["token"])
    assert normalize(body) == {"id": "<ID>", "token": "<TOKEN>"}


def test_login_tokens_are_unique_per_login(http):
    assert login(http)["token"] != login(http)["token"]


def test_login_wrong_password(anon):
    assert_error(anon.get("/login", login=ADMIN_LOGIN, password="wrong-password"), 401, 300,
                 "The login or password you entered is not valid.")


def test_login_unknown_user(anon):
    assert_error(anon.get("/login", login="nobody@bugzilla.test", password="x"), 401, 300,
                 "The login or password you entered is not valid.")


def test_login_missing_login(anon):
    assert_error(anon.get("/login"), 400, 50,
                 "The function requires a login argument, and that argument was not set.")


def test_login_missing_password(anon):
    assert_error(anon.get("/login", login=ADMIN_LOGIN), 400, 50,
                 "The function requires a password argument, and that argument was not set.")


def test_valid_login(admin):
    resp = admin.get("/valid_login", login=ADMIN_LOGIN)
    assert (resp.status_code, resp.json()) == (200, {"result": True})


def test_valid_login_for_another_user(admin):
    resp = admin.get("/valid_login", login=ALICE)
    assert (resp.status_code, resp.json()) == (200, {"result": False})


def test_valid_login_without_credentials(anon):
    resp = anon.get("/valid_login", login=ADMIN_LOGIN)
    assert (resp.status_code, resp.json()) == (200, {"result": False})


def test_valid_login_missing_login(admin):
    assert_error(admin.get("/valid_login"), 400, 50,
                 "The function requires a login argument, and that argument was not set.")


def test_valid_login_bad_token(anon):
    assert_error(anon.get("/valid_login", login=ADMIN_LOGIN, token="1-abcdefghij"), 400, 32000, BAD_TOKEN)


def test_invalid_api_key(anon):
    assert_error(anon.get("/valid_login", login=ADMIN_LOGIN, api_key="x" * 40), 400, 306,
                 "The API key you specified is invalid. Please check that you typed it correctly.")


def test_logout_invalidates_the_token(http):
    api = Api(http, login(http)["token"])
    assert api.get("/valid_login", login=ADMIN_LOGIN).json() == {"result": True}
    resp = api.get("/logout")
    assert (resp.status_code, resp.json()) == (200, {"result": None})
    assert_error(api.get("/valid_login", login=ADMIN_LOGIN), 400, 32000, BAD_TOKEN)


def test_logout_without_credentials(anon):
    resp = anon.get("/logout")
    assert (resp.status_code, resp.json()) == (200, {"result": None})
