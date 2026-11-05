"""Bugzilla::WebService::Bugzilla - version, extensions, timezone, time, last_audit_time, parameters."""
import re

from parity.conftest import expect_error, ok
from parity.normalize import DATETIME, normalize


def test_version(api):
    body = ok(api.get("/version"))
    assert set(body) == {"version"}
    assert re.match(r"^\d+\.\d+(\.\d+)?\+?$", body["version"]), body


def test_extensions(api):
    body = ok(api.get("/extensions"))
    assert set(body) == {"extensions"}
    assert isinstance(body["extensions"], dict)
    for name, info in body["extensions"].items():
        assert set(info) == {"version"}, (name, info)


def test_timezone_is_utc_offset(api):
    body = ok(api.get("/timezone"))
    assert body == {"timezone": "+0000"}


def test_time(api):
    body = ok(api.get("/time"))
    assert normalize(body) == {
        "web_time_utc": DATETIME, "web_time": DATETIME, "db_time": DATETIME,
        "tz_offset": "+0000", "tz_name": "UTC", "tz_short_name": "UTC",
    }
    assert body["web_time"] == body["web_time_utc"]


def test_last_audit_time(api):
    body = ok(api.get("/last_audit_time"))
    assert normalize(body) == {"last_audit_time": DATETIME}


def test_last_audit_time_for_class(api):
    body = ok(api.get("/last_audit_time", params={"class": "Bugzilla::Product"}))
    assert normalize(body) == {"last_audit_time": DATETIME}


def test_parameters_anonymous_sees_public_params_only(api):
    body = ok(api.get("/parameters"))
    assert set(body) == {"parameters"}
    assert set(body["parameters"]) == {"maintainer", "requirelogin"}
    assert body["parameters"]["requirelogin"] == "0"


def test_parameters_admin_sees_full_set(api, admin_token):
    params = ok(api.get("/parameters", token=admin_token))["parameters"]
    assert len(params) > 50
    for key in ("urlbase", "mail_delivery_method", "insidergroup", "defaultpriority",
                "defaultseverity", "maintainer", "requirelogin", "user_info_class"):
        assert key in params, key
    assert all(isinstance(v, str) for v in params.values())
    assert params["defaultseverity"] == "normal"


def test_parameters_logged_in_user_sees_full_set(api, user_token, admin_token):
    as_user = ok(api.get("/parameters", token=user_token))["parameters"]
    as_admin = ok(api.get("/parameters", token=admin_token))["parameters"]
    # a logged-in non-admin gets the non-sensitive subset; admins get everything
    assert 20 < len(as_user) < len(as_admin) and set(as_user) < set(as_admin)


def test_unknown_resource(api):
    expect_error(api.get("/nosuch"), 404, 32614, "A REST API resource was not found for 'GET /nosuch'")


def test_unknown_method_on_known_resource(api):
    expect_error(api.put("/version", json={}), 404, 32614, "PUT /version")


def test_jsonp_callback(api):
    r = api.get("/version", params={"callback": "cb"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert re.match(r"^\s*(/\*\*/)?cb\(", r.text) and r.text.rstrip().rstrip(";").endswith(")"), r.text[:40]


def test_cors_and_etag_headers(api):
    r = api.get("/version")
    assert r.headers.get("access-control-allow-origin") == "*"
    assert r.headers.get("etag")
