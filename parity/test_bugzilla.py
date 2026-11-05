"""Contract tests for the ``Bugzilla`` WebService module and REST dispatch."""

from conftest import Api, assert_error, assert_keys


def test_version(api: Api, snapshot):
    resp = api.get("/version")
    assert resp.status == 200
    assert isinstance(resp.body["version"], str) and resp.body["version"]
    snapshot(resp)


def test_timezone_is_always_utc(api: Api, snapshot):
    resp = api.get("/timezone")
    assert resp.status == 200
    assert resp.body == {"timezone": "+0000"}
    snapshot(resp)


def test_time(api: Api, snapshot):
    resp = api.get("/time")
    assert resp.status == 200
    assert_keys(resp.body, "db_time", "web_time", "web_time_utc", "tz_name", "tz_offset", "tz_short_name")
    assert resp.body["tz_name"] == "UTC"
    assert resp.body["tz_offset"] == "+0000"
    assert resp.body["web_time"] == resp.body["web_time_utc"]
    snapshot(resp)


def test_extensions(api: Api, snapshot):
    resp = api.get("/extensions")
    assert resp.status == 200
    assert isinstance(resp.body["extensions"], dict)
    snapshot(resp)


def test_last_audit_time(api: Api, admin: str, snapshot):
    resp = api.get("/last_audit_time")
    assert resp.status == 200
    assert resp.body["last_audit_time"] is None or isinstance(resp.body["last_audit_time"], str)
    snapshot(resp, "anonymous")

    resp = api.get("/last_audit_time", params={"class": "Bugzilla::Product"}, token=admin)
    assert resp.status == 200
    assert isinstance(resp.body["last_audit_time"], str)
    snapshot(resp, "product_class")


def test_last_audit_time_unknown_class_is_null(api: Api, snapshot):
    resp = api.get("/last_audit_time", params={"class": "Bugzilla::DoesNotExist"})
    assert resp.status == 200
    assert resp.body == {"last_audit_time": None}
    snapshot(resp)


def test_parameters_logged_out(api: Api, snapshot):
    resp = api.get("/parameters")
    assert resp.status == 200
    assert set(resp.body["parameters"]) == {"maintainer", "requirelogin"}
    snapshot(resp)


def test_parameters_logged_in_exposes_full_set(api: Api, admin: str):
    resp = api.get("/parameters", token=admin)
    assert resp.status == 200
    params = resp.body["parameters"]
    assert_keys(params, "maintainer", "requirelogin", "defaultpriority", "defaultseverity",
                "useclassification", "usetargetmilestone", "mail_delivery_method", "urlbase")
    assert len(params) > 20


def test_parameters_logged_in_as_plain_user(api: Api, user: dict):
    resp = api.get("/parameters", token=user["token"])
    assert resp.status == 200
    assert_keys(resp.body["parameters"], "maintainer", "requirelogin", "defaultpriority")


def test_unknown_resource(api: Api, snapshot):
    resp = api.get("/no_such_resource")
    assert_error(resp, 404, 32614)
    snapshot(resp)


def test_unsupported_method_on_known_path(api: Api, snapshot):
    resp = api.request("DELETE", "/version")
    assert_error(resp, 404, 32614)
    snapshot(resp)


def test_options_lists_allowed_methods(api: Api):
    resp = api.request("OPTIONS", "/version")
    assert resp.status == 200
    assert "GET" in resp.headers.get("Allow", "")
    assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")


def test_cors_and_etag_headers(api: Api):
    first = api.get("/version")
    assert first.headers.get("Access-Control-Allow-Origin") == "*"
    assert first.headers.get("Content-Type", "").startswith("application/json")
    etag = first.headers.get("ETag")
    assert etag
    second = api.request("GET", "/version", headers={"If-None-Match": etag})
    assert second.status == 304


def test_rest_alias_path_without_cgi_suffix(api: Api):
    """``.htaccess`` rewrites ``/rest/...`` to ``/rest.cgi/...``."""
    alias_base = api.base_url.rsplit("/", 1)[0] + "/rest"
    resp = Api(alias_base).get("/version")
    assert resp.status == 200
    assert resp.body == api.get("/version").body


def test_jsonp_callback(api: Api):
    resp = api.request("GET", "/version", params={"callback": "cb"}, headers={"Accept": "application/javascript"})
    assert resp.status == 200
    assert resp.headers.get("Content-Type", "").startswith("text/javascript")
    assert isinstance(resp.body, str) and resp.body.startswith("/**/cb(")
