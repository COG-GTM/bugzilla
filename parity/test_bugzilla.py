"""Bugzilla::WebService::Bugzilla: version, time; plus REST dispatch errors."""
import re

from conftest import assert_error
from normalize import normalize

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_version(anon):
    resp = anon.get("/version")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json; charset=UTF-8"
    assert resp.json() == {"version": "5.2+"}


def test_version_ignores_invalid_login_token_format(anon):
    # A token that is not "<user_id>-<token>" is ignored rather than rejected.
    resp = anon.get("/version", token="garbage")
    assert (resp.status_code, resp.json()) == (200, {"version": "5.2+"})


def test_time(anon):
    resp = anon.get("/time")
    assert resp.status_code == 200
    body = resp.json()
    assert all(ISO.match(body[k]) for k in ("db_time", "web_time", "web_time_utc"))
    assert normalize(body) == {
        "db_time": "<TIMESTAMP>", "web_time": "<TIMESTAMP>", "web_time_utc": "<TIMESTAMP>",
        "tz_name": "UTC", "tz_offset": "+0000", "tz_short_name": "UTC",
    }


def test_time_authenticated_is_the_same_shape(admin):
    resp = admin.get("/time")
    assert resp.status_code == 200
    assert sorted(resp.json()) == ["db_time", "tz_name", "tz_offset", "tz_short_name", "web_time", "web_time_utc"]


def test_time_rejects_expired_or_unknown_token(anon):
    assert_error(anon.get("/time", token="1-abcdefghij"), 400, 32000,
                 "The cookies or token provide were not valid or have expired. "
                 "You may login again to get new cookies or a new token.")


def test_unknown_resource(anon):
    assert_error(anon.get("/nosuch"), 404, 32614, "A REST API resource was not found for 'GET /nosuch'.")


def test_unsupported_method(anon):
    assert_error(anon.request("DELETE", "/version"), 404, 32614,
                 "A REST API resource was not found for 'DELETE /version'.")
