"""Parity tests for Bugzilla::WebService::User get/create/update
(GET /user, GET /user/{id_or_name}, POST /user, PUT /user/{id_or_name}).

login/logout/valid_login are covered by test_auth.py. offer_account_by_email has no REST
resource in Bugzilla/WebService/Server/REST/Resources/User.pm (JSON-RPC only) and is
intentionally not ported.
"""
import uuid

import pytest

from parity.conftest import ADMIN_LOGIN, USER_LOGIN, expect_error, ok

LIMITED_FIELDS = {"id", "real_name", "name"}
PUBLIC_FIELDS = LIMITED_FIELDS | {"email", "can_login", "groups"}
EDITUSERS_FIELDS = PUBLIC_FIELDS | {"email_enabled", "login_denied_text"}
SELF_FIELDS = PUBLIC_FIELDS | {"saved_searches", "saved_reports"}
GROUP_KEYS = {"id", "name", "description"}


def unique_email(prefix="parity-user"):
    return f"{prefix}-{uuid.uuid4().hex[:10]}@parity.test"


def group_names(user):
    # other parity modules (Group) create "parity_group_*" groups whose user_regexp can match seeded users
    return [g["name"] for g in user["groups"] if not g["name"].lower().startswith("parity_group")]


@pytest.fixture(scope="module")
def new_user(api, admin_token):
    """A fresh account created through POST /user (shared by the read-only tests of this module)."""
    email = unique_email()
    body = ok(api.post("/user", token=admin_token, json={
        "email": email, "full_name": "  Parity Fixture  ", "password": "Parity-pass-1!"}), 201)
    return {"id": body["id"], "email": email, "password": "Parity-pass-1!"}


@pytest.fixture
def scratch_user(api, admin_token):
    """Factory for throw-away accounts used by the update tests."""
    def _create(**overrides):
        body = {"email": unique_email("parity-scratch"), "full_name": "Scratch User", "password": "Scratch-pass-1!"}
        body.update(overrides)
        r = ok(api.post("/user", token=admin_token, json=body), 201)
        return {"id": r["id"], **body}
    return _create


# ------------------------------------------------------------------ GET /user (anonymous)
def test_anonymous_names_returns_limited_fields(api):
    body = ok(api.get("/user", params={"names": ADMIN_LOGIN}))
    assert len(body["users"]) == 1
    user = body["users"][0]
    assert set(user) == LIMITED_FIELDS
    assert user == {"id": 1, "real_name": "Test Admin", "name": ADMIN_LOGIN}


def test_anonymous_names_path_variant(api):
    body = ok(api.get(f"/user/{ADMIN_LOGIN}"))
    assert set(body["users"][0]) == LIMITED_FIELDS


def test_anonymous_ids_denied(api):
    expect_error(api.get("/user", params={"ids": 1}), 401, 505,
                 'Logged-out users cannot use the "ids" argument')


def test_anonymous_id_path_denied(api):
    expect_error(api.get("/user/1"), 401, 505, '"ids" argument')


def test_anonymous_match_denied(api):
    expect_error(api.get("/user", params={"match": "ali"}), 401, 505,
                 'Logged-out users cannot use the "match" argument')


def test_anonymous_unknown_name(api):
    expect_error(api.get("/user/nobody@parity.test"), 404, 51, "There is no user named 'nobody@parity.test'")


def test_anonymous_group_filter_rejected(api):
    expect_error(api.get("/user", params={"names": ADMIN_LOGIN, "groups": "admin"}), 400, 804,
                 "The group you specified, admin, is not valid here.")


def test_anonymous_include_fields(api):
    body = ok(api.get("/user", params={"names": ADMIN_LOGIN, "include_fields": "id"}))
    assert body["users"] == [{"id": 1}]


# ------------------------------------------------------------------ GET /user (parameters)
@pytest.mark.parametrize("token_fixture", [None, "admin_token"])
def test_get_requires_ids_names_or_match(api, request, token_fixture):
    token = request.getfixturevalue(token_fixture) if token_fixture else None
    expect_error(api.get("/user", token=token), 400, 50,
                 "The function User.get requires that you set one of the following parameters: ids, names, match")


def test_get_ids_must_be_numeric(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"ids": "abc"}), 400, 52,
                 "Invalid parameter passed to Bugzilla::User::new_from_list: It must be numeric.")


def test_get_csv_names_is_a_single_name(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"names": f"{ADMIN_LOGIN},{USER_LOGIN}"}), 404, 51,
                 f"There is no user named '{ADMIN_LOGIN},{USER_LOGIN}'")


def test_get_unknown_id_is_empty(api, admin_token):
    assert ok(api.get("/user/999999", token=admin_token)) == {"users": []}


def test_get_unknown_name_as_admin(api, admin_token):
    expect_error(api.get("/user/nobody@parity.test", token=admin_token), 404, 51,
                 "Either you mis-typed the name or that user has not yet registered for a Bugzilla account.")


def test_get_limit_must_be_numeric(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"match": "ali", "limit": "abc"}), 400, 52,
                 "Invalid parameter limit passed to Bugzilla::WebService::User::match: It must be numeric.")


def test_get_invalid_group_name(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"names": ADMIN_LOGIN, "groups": "nosuchgroup"}),
                 400, 804, "The group you specified, nosuchgroup, is not valid here.")


def test_get_invalid_group_id(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"names": ADMIN_LOGIN, "group_ids": 99999}),
                 404, 51, "There is no group with the id '99999'.")


def test_get_group_filter_requires_membership(api, user_token):
    expect_error(api.get("/user", token=user_token, params={"names": ADMIN_LOGIN, "groups": "admin"}),
                 400, 804, "The group you specified, admin, is not valid here.")


# ------------------------------------------------------------------ GET /user (logged in)
def test_admin_sees_editusers_fields_and_all_groups(api, admin_token):
    user = ok(api.get("/user/1", token=admin_token))["users"][0]
    assert set(user) == EDITUSERS_FIELDS | {"saved_searches", "saved_reports"}
    assert user["id"] == 1 and user["name"] == user["email"] == ADMIN_LOGIN
    assert user["can_login"] is True and user["email_enabled"] is True and user["login_denied_text"] == ""
    assert user["saved_searches"] == [] and user["saved_reports"] == []
    names = [g["name"] for g in user["groups"]]
    assert {"admin", "editusers", "editbugs", "canconfirm"} <= set(names)  # order: Perl lists groups by id
    assert all(set(g) == GROUP_KEYS for g in user["groups"])
    admin_group = next(g for g in user["groups"] if g["name"] == "admin")
    assert admin_group == {"id": 1, "name": "admin", "description": "Administrators"}


def test_admin_gets_other_user_by_name(api, admin_token):
    user = ok(api.get(f"/user/{USER_LOGIN}", token=admin_token))["users"][0]
    assert set(user) == EDITUSERS_FIELDS
    assert user["id"] == 2 and user["real_name"] == "Alice Ng"
    assert group_names(user) == ["bz_canusewhines", "editbugs"]


def test_ids_and_names_are_merged_and_deduplicated(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"ids": [1, 2], "names": USER_LOGIN}))
    assert sorted(u["id"] for u in body["users"]) == [1, 2]


def test_ids_csv_is_not_split(api, admin_token):
    expect_error(api.get("/user", token=admin_token, params={"ids": "1,2"}), 400, 52,
                 "Invalid parameter passed to Bugzilla::User::new_from_list: It must be numeric.")


def test_regular_user_sees_public_fields_only(api, user_token):
    user = ok(api.get(f"/user/{ADMIN_LOGIN}", token=user_token))["users"][0]
    assert set(user) == PUBLIC_FIELDS
    assert user["groups"] == []  # alice cannot bless any group
    assert user["can_login"] is True and user["email"] == ADMIN_LOGIN


def test_regular_user_sees_own_saved_objects_and_groups(api, user_token):
    user = ok(api.get(f"/user/{USER_LOGIN}", token=user_token))["users"][0]
    assert set(user) == SELF_FIELDS
    assert user["saved_searches"] == [] and user["saved_reports"] == []
    assert group_names(user) == ["bz_canusewhines", "editbugs"]


def test_saved_objects_only_when_requested(api, user_token):
    user = ok(api.get(f"/user/{USER_LOGIN}", token=user_token, params={"include_fields": "id,name"}))["users"][0]
    assert set(user) == {"id", "name"}


def test_include_fields_keeps_editusers_fields(api, admin_token):
    user = ok(api.get("/user/1", token=admin_token, params={"include_fields": "id,groups"}))["users"][0]
    assert set(user) == {"id", "groups", "email_enabled", "login_denied_text"}


def test_exclude_fields_groups(api, admin_token):
    user = ok(api.get("/user/1", token=admin_token, params={"exclude_fields": "groups,saved_reports"}))["users"][0]
    assert set(user) == EDITUSERS_FIELDS - {"groups"} | {"saved_searches"}


def test_group_filter_by_name(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"names": [ADMIN_LOGIN, USER_LOGIN], "groups": "admin"}))
    assert [u["id"] for u in body["users"]] == [1]
    body = ok(api.get("/user", token=admin_token, params={"names": [ADMIN_LOGIN, USER_LOGIN], "groups": "editbugs"}))
    assert sorted(u["id"] for u in body["users"]) == [1, 2]


def test_group_filter_by_id(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"ids": [1, 2], "group_ids": 1}))
    assert [u["id"] for u in body["users"]] == [1]


def test_match_substring(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": "alice"}))
    assert [u["id"] for u in body["users"]] == [2]
    assert set(body["users"][0]) == EDITUSERS_FIELDS


def test_match_real_name(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": "Alice N"}))
    assert [u["id"] for u in body["users"]] == [2]


def test_match_wildcard(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": "*ali*"}))
    assert [u["id"] for u in body["users"]] == [2]


def test_match_too_short_without_exact_match(api, admin_token):
    assert ok(api.get("/user", token=admin_token, params={"match": "al"})) == {"users": []}


def test_match_exact_login(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": USER_LOGIN}))
    assert [u["id"] for u in body["users"]] == [2]


def test_match_limit(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": "bugzilla.test", "limit": 1}))
    assert len(body["users"]) == 1


def test_match_as_regular_user(api, user_token):
    body = ok(api.get("/user", token=user_token, params={"match": "admin"}))
    assert [u["id"] for u in body["users"]] == [1]
    assert set(body["users"][0]) == PUBLIC_FIELDS


def test_match_multiple_strings(api, admin_token):
    body = ok(api.get("/user", token=admin_token, params={"match": ["alice", "Test Admin"]}))
    assert sorted(u["id"] for u in body["users"]) == [1, 2]


def test_match_excludes_disabled_unless_requested(api, admin_token, scratch_user):
    real_name = f"Disabled {uuid.uuid4().hex[:8]} Target"
    u = scratch_user(full_name=real_name)
    ok(api.put(f"/user/{u['id']}", token=admin_token, json={"login_denied_text": "disabled by parity"}))
    body = ok(api.get("/user", token=admin_token, params={"match": real_name[:-7]}))
    assert body["users"] == []
    body = ok(api.get("/user", token=admin_token, params={"match": real_name[:-7], "include_disabled": 1}))
    assert [x["id"] for x in body["users"]] == [u["id"]]
    assert body["users"][0]["can_login"] is False
    assert body["users"][0]["login_denied_text"] == "disabled by parity"
    # exact matches do not care about disabled accounts
    body = ok(api.get("/user", token=admin_token, params={"match": u["email"]}))
    assert [x["id"] for x in body["users"]] == [u["id"]]


# ------------------------------------------------------------------ POST /user
def test_create_requires_editusers(api, user_token):
    expect_error(api.post("/user", token=user_token, json={"email": unique_email()}), 401, 304,
                 "Sorry, you aren't a member of the 'editusers' group, and so you are not authorized to add new users.")


def test_create_anonymous(api):
    expect_error(api.post("/user", json={"email": unique_email()}), 401, 304, "'editusers' group")


def test_create_requires_email(api, admin_token):
    expect_error(api.post("/user", token=admin_token, json={"full_name": "x"}), 400, 50,
                 "The function requires a email argument, and that argument was not set.")


def test_create_blank_email(api, admin_token):
    expect_error(api.post("/user", token=admin_token, json={"email": "   "}), 400, 50, "email argument")


def test_create_invalid_email(api, admin_token):
    expect_error(api.post("/user", token=admin_token, json={"email": "not-an-email"}), 400, 501,
                 "The e-mail address you entered (not-an-email) didn't pass our syntax checking for a legal email "
                 "address. A legal address must contain exactly one '@', and at least one '.' after the @. "
                 "It also must not contain any illegal characters.")


def test_create_short_password(api, admin_token):
    expect_error(api.post("/user", token=admin_token, json={"email": unique_email(), "password": "abc"}), 400, 502,
                 "The password must be at least 6 characters long.")


def test_create_returns_201_and_id(api, admin_token, new_user):
    assert isinstance(new_user["id"], int) and new_user["id"] > 2


def test_create_duplicate(api, admin_token, new_user):
    expect_error(api.post("/user", token=admin_token, json={"email": new_user["email"]}), 400, 500,
                 f"There is already an account with the login name {new_user['email']}.")


def test_create_duplicate_is_case_insensitive(api, admin_token, new_user):
    upper = new_user["email"].upper()
    expect_error(api.post("/user", token=admin_token, json={"email": upper}), 400, 500,
                 f"There is already an account with the login name {upper}.")


def test_created_user_defaults(api, admin_token, new_user):
    user = ok(api.get(f"/user/{new_user['id']}", token=admin_token))["users"][0]
    assert user == {
        "id": new_user["id"], "name": new_user["email"], "email": new_user["email"],
        "real_name": "Parity Fixture", "can_login": True, "email_enabled": True, "login_denied_text": "",
        "groups": [{"id": 11, "name": "bz_canusewhines", "description": "User can configure whine reports for self"},
                   {"id": 8, "name": "editbugs", "description": "Can edit all bug fields"}],
    }


def test_created_user_can_login(api, new_user):
    body = ok(api.get("/login", params={"login": new_user["email"], "password": new_user["password"]}))
    assert body["id"] == new_user["id"]


def test_created_user_without_password_cannot_login(api, admin_token):
    email = unique_email()
    ok(api.post("/user", token=admin_token, json={"email": email}), 201)
    expect_error(api.get("/login", params={"login": email, "password": "*"}), 401, 300)


# ------------------------------------------------------------------ PUT /user/{id_or_name}
def test_update_requires_login(api, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", json={"full_name": "x"}), 401, 410,
                 "You must log in before using this part of Bugzilla.")


def test_update_requires_editusers(api, user_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=user_token, json={"full_name": "x"}), 401, 304,
                 "Sorry, you aren't a member of the 'editusers' group, and so you are not authorized to add, "
                 "modify or delete users.")


def test_update_unknown_id(api, admin_token):
    expect_error(api.put("/user/999999", token=admin_token, json={"full_name": "x"}), 404, 51,
                 "There is no user with the id '999999'.")


def test_update_unknown_name(api, admin_token):
    expect_error(api.put("/user/nobody@parity.test", token=admin_token, json={"full_name": "x"}), 404, 51,
                 "There is no user named 'nobody@parity.test'.")


def test_update_full_name(api, admin_token, scratch_user):
    u = scratch_user()
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"full_name": "  Renamed User "}))
    assert body == {"users": [{"id": u["id"], "changes": {"full_name": {"removed": "Scratch User", "added": "Renamed User"}}}]}
    assert ok(api.put(f"/user/{u['id']}", token=admin_token, json={"full_name": "Renamed User"})) == {
        "users": [{"id": u["id"], "changes": {}}]}
    assert ok(api.get(f"/user/{u['id']}", token=admin_token))["users"][0]["real_name"] == "Renamed User"


def test_update_by_login_name(api, admin_token, scratch_user):
    u = scratch_user()
    body = ok(api.put(f"/user/{u['email']}", token=admin_token, json={"full_name": "By Name"}))
    assert body["users"][0]["id"] == u["id"]
    assert body["users"][0]["changes"]["full_name"]["added"] == "By Name"


def test_update_disable_and_reenable(api, admin_token, scratch_user):
    u = scratch_user()
    body = ok(api.put(f"/user/{u['id']}", token=admin_token,
                      json={"email_enabled": False, "login_denied_text": "gone fishing"}))
    assert body == {"users": [{"id": u["id"], "changes": {
        "disable_mail": {"removed": "0", "added": "1"},
        "is_enabled": {"removed": "1", "added": "0"},
        "login_denied_text": {"removed": "", "added": "gone fishing"}}}]}
    got = ok(api.get(f"/user/{u['id']}", token=admin_token))["users"][0]
    assert got["can_login"] is False and got["email_enabled"] is False and got["login_denied_text"] == "gone fishing"
    expect_error(api.get("/login", params={"login": u["email"], "password": u["password"]}), 401, 301)
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"email_enabled": True, "login_denied_text": ""}))
    assert body["users"][0]["changes"] == {
        "disable_mail": {"removed": "1", "added": "0"},
        "is_enabled": {"removed": "0", "added": "1"},
        "login_denied_text": {"removed": "gone fishing", "added": ""}}


def test_update_disabling_logs_the_user_out(api, admin_token, scratch_user):
    u = scratch_user()
    token = api.login(u["email"], u["password"])
    assert ok(api.get("/valid_login", token=token, params={"login": u["email"]}))["result"] is True
    ok(api.put(f"/user/{u['id']}", token=admin_token, json={"login_denied_text": "bye"}))
    expect_error(api.get("/valid_login", token=token, params={"login": u["email"]}), 400, 32000)


def test_update_password(api, admin_token, scratch_user):
    u = scratch_user()
    token = api.login(u["email"], u["password"])
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"password": "New-pass-2!"}))
    assert body == {"users": [{"id": u["id"], "changes": {
        "cryptpassword": {"removed": "", "added": "hashed_with_SHA-256"}}}]}
    expect_error(api.get("/login", params={"login": u["email"], "password": u["password"]}), 401, 300)
    ok(api.get("/login", params={"login": u["email"], "password": "New-pass-2!"}))
    # password changes invalidate existing login tokens
    expect_error(api.get("/valid_login", token=token, params={"login": u["email"]}), 400, 32000)


def test_update_short_password(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"password": "ab"}), 400, 502,
                 "The password must be at least 6 characters long.")


def test_update_email(api, admin_token, scratch_user):
    u = scratch_user()
    new_email = unique_email("parity-renamed")
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"email": new_email}))
    assert body == {"users": [{"id": u["id"], "changes": {"email": {"removed": u["email"], "added": new_email}}}]}
    got = ok(api.get(f"/user/{new_email}", token=admin_token))["users"][0]
    assert got["id"] == u["id"] and got["name"] == got["email"] == new_email
    expect_error(api.get(f"/user/{u['email']}", token=admin_token), 404, 51)


def test_update_email_invalid(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"email": "bad"}), 400, 501,
                 "The e-mail address you entered (bad) didn't pass our syntax checking")


def test_update_email_taken(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"email": ADMIN_LOGIN}), 400, 500,
                 f"There is already an account with the login name {ADMIN_LOGIN}.")


def test_update_groups_add_set_remove(api, admin_token, scratch_user):
    u = scratch_user()
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"groups": {"add": ["canconfirm", "editkeywords"]}}))
    assert body == {"users": [{"id": u["id"], "changes": {"groups": {"removed": "", "added": "canconfirm,editkeywords"}}}]}
    got = ok(api.get(f"/user/{u['id']}", token=admin_token))["users"][0]
    assert group_names(got) == ["bz_canusewhines", "canconfirm", "editbugs", "editkeywords"]

    body = ok(api.put(f"/user/{u['id']}", token=admin_token,
                      json={"groups": {"set": [7]}, "bless_groups": {"add": ["editkeywords"]}}))
    assert body == {"users": [{"id": u["id"], "changes": {
        "groups": {"removed": "canconfirm", "added": ""},
        "bless_groups": {"removed": "", "added": "editkeywords"}}}]}

    body = ok(api.put(f"/user/{u['id']}", token=admin_token,
                      json={"groups": {"remove": ["editkeywords"]}, "bless_groups": {"remove": [7]}}))
    assert body == {"users": [{"id": u["id"], "changes": {
        "groups": {"removed": "editkeywords", "added": ""},
        "bless_groups": {"removed": "editkeywords", "added": ""}}}]}
    got = ok(api.get(f"/user/{u['id']}", token=admin_token))["users"][0]
    assert group_names(got) == ["bz_canusewhines", "editbugs"]


def test_update_groups_noop(api, admin_token, scratch_user):
    u = scratch_user()
    assert ok(api.put(f"/user/{u['id']}", token=admin_token, json={"groups": {"remove": ["canconfirm"]}})) == {
        "users": [{"id": u["id"], "changes": {}}]}


def test_update_groups_unknown_group(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"groups": {"add": ["nosuchgroup"]}}),
                 401, 304, "Sorry, you aren't a member of the 'nosuchgroup' group, and you don't have permissions to "
                 "add or remove people from a group, and so you are not authorized to add, modify or delete users.")


def test_update_groups_must_be_arrays(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"groups": {"add": "editbugs"}}),
                 400, 53, "editbugs is not a valid parameter for the add function.")


def test_update_unknown_field(api, admin_token, new_user):
    expect_error(api.put(f"/user/{new_user['id']}", token=admin_token, json={"bogus": "x"}), 400, 67399,
                 "The requested method 'Bugzilla::User::set_bogus' was not found.")


def test_update_password_star_disables_db_login(api, admin_token, scratch_user):
    u = scratch_user()
    body = ok(api.put(f"/user/{u['id']}", token=admin_token, json={"password": "*"}))
    assert body == {"users": [{"id": u["id"], "changes": {
        "cryptpassword": {"removed": "", "added": "hashed_with_unknown_algorithm"}}}]}
    expect_error(api.get("/login", params={"login": u["email"], "password": "*"}), 401, 300)
    expect_error(api.get("/login", params={"login": u["email"], "password": u["password"]}), 401, 300)
