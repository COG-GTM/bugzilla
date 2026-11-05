"""Bugzilla::WebService::Bugzilla - version, extensions, timezone, time, last_audit_time, parameters.

Every expectation here was taken from the running Perl app (see docs/migration/PLAN.md); the
Python port must reproduce them byte for byte after ``normalize()``.
"""
import re

import pytest

from parity.conftest import ADMIN_LOGIN, ADMIN_PASSWORD, USER_LOGIN, USER_PASSWORD, expect_error, ok
from parity.normalize import DATETIME, normalize

ENDPOINTS = ["/version", "/extensions", "/timezone", "/time", "/last_audit_time", "/parameters"]
# LOGIN_EXEMPT in Bugzilla::WebService::Bugzilla: the server does not even look at token/api_key.
# (`parameters` is exempt too, but calls Bugzilla->login(LOGIN_OPTIONAL) itself.)
LOGIN_EXEMPT = ["/version", "/timezone"]
LOGIN_CHECKED = ["/extensions", "/time", "/last_audit_time", "/parameters"]

# PARAMETERS_LOGGED_OUT / PARAMETERS_LOGGED_IN, verbatim from Bugzilla/WebService/Bugzilla.pm
PARAMETERS_LOGGED_OUT = {"maintainer", "requirelogin"}
PARAMETERS_LOGGED_IN = {
    "allowemailchange", "attachment_base", "commentonchange_resolution", "commentonduplicate", "cookiepath",
    "defaultopsys", "defaultplatform", "defaultpriority", "defaultseverity", "duplicate_or_move_bug_status",
    "emailregexpdesc", "emailsuffix", "letsubmitterchoosemilestone", "letsubmitterchoosepriority", "mailfrom",
    "maintainer", "maxattachmentsize", "maxlocalattachment", "musthavemilestoneonaccept",
    "noresolveonopenblockers", "password_complexity", "rememberlogin", "requirelogin",
    "search_allow_no_criteria", "urlbase", "use_see_also", "useclassification", "usemenuforusers",
    "useqacontact", "usestatuswhiteboard", "usetargetmilestone",
}
# audit_log classes checksetup.pl / the seed always write (products, components, groups, users, ...)
AUDITED_CLASSES = ["Bugzilla::Product", "Bugzilla::Component", "Bugzilla::Group", "Bugzilla::User",
                   "Bugzilla::Field", "Bugzilla::Classification", "Bugzilla::Version", "Bugzilla::Milestone"]

INVALID_TOKEN_MSG = "The cookies or token provide were not valid or have expired."
INVALID_API_KEY_MSG = "The API key you specified is invalid."
INVALID_LOGIN_MSG = "The login or password you entered is not valid."
# A wrong password for a REAL account counts towards Bugzilla's lockout (5 failures / 30 min), so the
# negative login tests use an account that does not exist: same 401/300 answer, no login_failure row.
NO_SUCH_LOGIN = "nobody.parity@bugzilla.test"


# --- version -----------------------------------------------------------------------------------

def test_version(api):
    body = ok(api.get("/version"))
    assert set(body) == {"version"}
    assert re.match(r"^\d+\.\d+(\.\d+)?\+?$", body["version"]), body


def test_version_ignores_include_fields(api):
    assert ok(api.get("/version", params={"include_fields": "nothing"})) == ok(api.get("/version"))


def test_version_same_for_every_caller(api, admin_token, user_token):
    anon = ok(api.get("/version"))
    assert ok(api.get("/version", token=admin_token)) == anon
    assert ok(api.get("/version", token=user_token)) == anon
    assert ok(api.get("/version", params={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD})) == anon


# --- extensions --------------------------------------------------------------------------------

def test_extensions(api):
    body = ok(api.get("/extensions"))
    assert set(body) == {"extensions"}
    assert isinstance(body["extensions"], dict)
    for name, info in body["extensions"].items():
        assert set(info) == {"version"}, (name, info)
        assert isinstance(info["version"], str) and info["version"], (name, info)


def test_extensions_shipped_ones_are_disabled(api):
    # extensions/{BmpConvert,Example,MoreBugUrl,OldBugMove,Voting} all carry a `disabled` file
    body = ok(api.get("/extensions"))
    assert body == {"extensions": {}}


def test_extensions_same_for_logged_in_users(api, admin_token, user_token):
    anon = ok(api.get("/extensions"))
    assert ok(api.get("/extensions", token=admin_token)) == anon
    assert ok(api.get("/extensions", token=user_token)) == anon


# --- timezone / time ---------------------------------------------------------------------------

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


def test_time_db_and_web_clocks_agree(api):
    from datetime import datetime, timezone
    body = ok(api.get("/time"))
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    db_time = datetime.strptime(body["db_time"], fmt)
    web_time = datetime.strptime(body["web_time"], fmt)
    assert abs((db_time - web_time).total_seconds()) <= 2, body
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - web_time).total_seconds()) < 120, body


def test_time_ignores_field_filters(api):
    full_keys = set(ok(api.get("/time")))
    assert set(ok(api.get("/time", params={"include_fields": "db_time"}))) == full_keys
    assert set(ok(api.get("/time", params={"exclude_fields": "db_time"}))) == full_keys


# --- last_audit_time ---------------------------------------------------------------------------

def test_last_audit_time(api):
    body = ok(api.get("/last_audit_time"))
    assert normalize(body) == {"last_audit_time": DATETIME}


def test_last_audit_time_for_class(api):
    body = ok(api.get("/last_audit_time", params={"class": "Bugzilla::Product"}))
    assert normalize(body) == {"last_audit_time": DATETIME}


@pytest.mark.parametrize("klass", AUDITED_CLASSES)
def test_last_audit_time_per_class_never_exceeds_global_max(api, klass):
    overall = ok(api.get("/last_audit_time"))["last_audit_time"]
    per_class = ok(api.get("/last_audit_time", params={"class": klass}))["last_audit_time"]
    assert per_class is not None, klass
    assert per_class <= overall, (klass, per_class, overall)


def test_last_audit_time_unknown_class_is_null(api):
    body = ok(api.get("/last_audit_time", params={"class": "Bugzilla::NoSuchClass"}))
    assert body == {"last_audit_time": None}


def test_last_audit_time_bare_bugzilla_class_is_null(api):
    # "Bugzilla" matches the regex but nothing is logged under it
    body = ok(api.get("/last_audit_time", params={"class": "Bugzilla"}))
    assert body == {"last_audit_time": None}


@pytest.mark.parametrize("klass", ["DROP TABLE audit_log", "bugzilla::product", "Bugzilla::", "Bugzilla::Pro-duct",
                                   "", "Bugzilla::Product OR 1=1"])
def test_last_audit_time_malformed_class_is_ignored(api, klass):
    # class values not matching ^Bugzilla(::\w+)*$ are dropped silently -> global maximum
    overall = ok(api.get("/last_audit_time"))
    assert ok(api.get("/last_audit_time", params={"class": klass})) == overall


def test_last_audit_time_class_is_not_comma_split(api):
    # validate(@_, 'class') wraps the scalar in a list; the whole string fails the regex -> no filter
    overall = ok(api.get("/last_audit_time"))
    body = ok(api.get("/last_audit_time", params={"class": "Bugzilla::NoSuchClass,Bugzilla::Other"}))
    assert body == overall


def test_last_audit_time_repeated_class_params_are_a_list(api):
    product_only = ok(api.get("/last_audit_time", params={"class": "Bugzilla::Product"}))
    body = ok(api.get("/last_audit_time", params={"class": ["Bugzilla::NoSuchClass", "Bugzilla::Product"]}))
    assert body == product_only
    both = ok(api.get("/last_audit_time", params={"class": ["Bugzilla::Product", "Bugzilla::User"]}))
    product = ok(api.get("/last_audit_time", params={"class": "Bugzilla::Product"}))["last_audit_time"]
    user = ok(api.get("/last_audit_time", params={"class": "Bugzilla::User"}))["last_audit_time"]
    assert both == {"last_audit_time": max(product, user)}


def test_last_audit_time_mixed_valid_and_malformed_classes(api):
    product_only = ok(api.get("/last_audit_time", params={"class": "Bugzilla::Product"}))
    body = ok(api.get("/last_audit_time", params={"class": ["not valid", "Bugzilla::Product", "x::y"]}))
    assert body == product_only


def test_last_audit_time_same_when_logged_in(api, admin_token):
    assert ok(api.get("/last_audit_time", token=admin_token)) == ok(api.get("/last_audit_time"))


# --- parameters --------------------------------------------------------------------------------

def test_parameters_anonymous_sees_public_params_only(api):
    body = ok(api.get("/parameters"))
    assert set(body) == {"parameters"}
    assert set(body["parameters"]) == PARAMETERS_LOGGED_OUT
    assert body["parameters"]["requirelogin"] == "0"
    assert body["parameters"]["maintainer"] == ADMIN_LOGIN


def test_parameters_logged_in_user_sees_exactly_the_logged_in_list(api, user_token):
    params = ok(api.get("/parameters", token=user_token))["parameters"]
    assert set(params) == PARAMETERS_LOGGED_IN
    assert all(isinstance(v, str) for v in params.values()), params
    assert params["defaultseverity"] == "normal"
    assert params["defaultpriority"] == "--"
    assert params["urlbase"] == "http://127.0.0.1:8080/"
    assert params["useclassification"] == "1"


def test_parameters_admin_sees_full_set(api, admin_token):
    params = ok(api.get("/parameters", token=admin_token))["parameters"]
    assert len(params) > 50
    for key in ("urlbase", "mail_delivery_method", "insidergroup", "defaultpriority",
                "defaultseverity", "maintainer", "requirelogin", "user_info_class",
                "shadowdb", "smtpserver", "LDAPserver", "memcached_servers"):
        assert key in params, key
    assert all(isinstance(v, str) for v in params.values())
    assert params["defaultseverity"] == "normal"
    assert params["mail_delivery_method"] == "None"
    assert params["insidergroup"] == "admin"


def test_parameters_logged_in_user_sees_full_set(api, user_token, admin_token):
    as_user = ok(api.get("/parameters", token=user_token))["parameters"]
    as_admin = ok(api.get("/parameters", token=admin_token))["parameters"]
    # a logged-in non-admin gets the non-sensitive subset; admins (tweakparams) get everything
    assert PARAMETERS_LOGGED_OUT < set(as_user) < set(as_admin)
    for key, value in as_user.items():
        assert as_admin[key] == value, key


def test_parameters_via_login_and_password_query(api):
    as_admin = ok(api.get("/parameters", params={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}))["parameters"]
    assert len(as_admin) > 50
    as_user = ok(api.get("/parameters", params={"login": USER_LOGIN, "password": USER_PASSWORD}))["parameters"]
    assert set(as_user) == PARAMETERS_LOGGED_IN


def test_parameters_include_fields(api, user_token):
    assert ok(api.get("/parameters", params={"include_fields": "maintainer"})) == \
        {"parameters": {"maintainer": ADMIN_LOGIN}}
    # a field outside the caller's visibility is simply absent, not an error
    assert ok(api.get("/parameters", params={"include_fields": "urlbase"})) == {"parameters": {}}
    body = ok(api.get("/parameters", token=user_token, params={"include_fields": "urlbase,maintainer"}))
    assert body == {"parameters": {"urlbase": "http://127.0.0.1:8080/", "maintainer": ADMIN_LOGIN}}
    # list form
    body = ok(api.get("/parameters", token=user_token, params={"include_fields": ["urlbase", "maintainer"]}))
    assert body == {"parameters": {"urlbase": "http://127.0.0.1:8080/", "maintainer": ADMIN_LOGIN}}


def test_parameters_exclude_fields(api, user_token):
    assert ok(api.get("/parameters", params={"exclude_fields": "maintainer"})) == \
        {"parameters": {"requirelogin": "0"}}
    params = ok(api.get("/parameters", token=user_token, params={"exclude_fields": "urlbase,maintainer"}))["parameters"]
    assert set(params) == PARAMETERS_LOGGED_IN - {"urlbase", "maintainer"}


def test_parameters_field_type_shortcuts(api, user_token, admin_token):
    assert ok(api.get("/parameters", token=admin_token, params={"exclude_fields": "_all"})) == {"parameters": {}}
    assert ok(api.get("/parameters", token=admin_token, params={"include_fields": "_custom"})) == {"parameters": {}}
    full = ok(api.get("/parameters", token=admin_token))
    assert ok(api.get("/parameters", token=admin_token, params={"include_fields": "_all"})) == full
    assert ok(api.get("/parameters", token=admin_token, params={"include_fields": "_default"})) == full
    body = ok(api.get("/parameters", token=user_token,
                      params={"exclude_fields": "_default", "include_fields": "urlbase"}))
    assert body == {"parameters": {"urlbase": "http://127.0.0.1:8080/"}}
    # explicit exclusion wins over explicit inclusion
    body = ok(api.get("/parameters", params={"include_fields": "maintainer", "exclude_fields": "maintainer"}))
    assert body == {"parameters": {}}


def test_parameters_token_without_userid_prefix_is_anonymous(api):
    body = ok(api.get("/parameters", params={"token": "bogus"}))
    assert set(body["parameters"]) == PARAMETERS_LOGGED_OUT


# --- authentication errors ---------------------------------------------------------------------

@pytest.mark.parametrize("path", LOGIN_CHECKED)
def test_invalid_token_is_rejected(api, path):
    expect_error(api.get(path, params={"token": "1-bogus"}), 400, 32000, INVALID_TOKEN_MSG)


@pytest.mark.parametrize("path", LOGIN_CHECKED)
def test_invalid_api_key_is_rejected(api, path):
    expect_error(api.get(path, params={"api_key": "bogus"}), 400, 306, INVALID_API_KEY_MSG)


@pytest.mark.parametrize("path", LOGIN_EXEMPT)
def test_login_exempt_methods_ignore_bad_token_and_api_key(api, path):
    anon = ok(api.get(path))
    assert ok(api.get(path, params={"token": "1-bogus"})) == anon
    assert ok(api.get(path, params={"api_key": "bogus"})) == anon


@pytest.mark.parametrize("path", ENDPOINTS)
def test_wrong_login_password_is_rejected_everywhere(api, path):
    expect_error(api.get(path, params={"login": NO_SUCH_LOGIN, "password": "nope"}), 401, 300, INVALID_LOGIN_MSG)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_valid_login_password_is_accepted_everywhere(api, path):
    body = ok(api.get(path, params={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}))
    assert isinstance(body, dict) and "error" not in body


# --- transport / resource errors ---------------------------------------------------------------

def test_unknown_resource(api):
    expect_error(api.get("/nosuch"), 404, 32614, "A REST API resource was not found for 'GET /nosuch'")


def test_unknown_method_on_known_resource(api):
    expect_error(api.put("/version", json={}), 404, 32614, "PUT /version")


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_only_get_is_routed(api, path, method):
    r = api.request(method, path, json={}) if method != "DELETE" else api.request(method, path)
    expect_error(r, 404, 32614, f"A REST API resource was not found for '{method} {path}'.")


def test_jsonp_callback(api):
    r = api.get("/version", params={"callback": "cb"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert re.match(r"^\s*(/\*\*/)?cb\(", r.text) and r.text.rstrip().rstrip(";").endswith(")"), r.text[:40]


@pytest.mark.parametrize("path", ["/version", "/time"])
def test_invalid_jsonp_callback(api, path):
    expect_error(api.get(path, params={"callback": "bad!x"}), 400, 32611,
                 "You cannot use 'bad!x' as your 'callback' parameter.")


def test_cors_and_etag_headers(api):
    r = api.get("/version")
    assert r.headers.get("access-control-allow-origin") == "*"
    assert r.headers.get("etag")
