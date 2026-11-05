"""Group.get / Group.create / Group.update (GET /group, POST /group, PUT /group/{id_or_name})."""
import pytest

from parity.conftest import expect_error, ok, unique
from parity.normalize import ID, normalize

GROUP_FIELDS = {"id", "name", "description", "is_active", "is_bug_group", "user_regexp"}
MEMBER_FIELDS = {"id", "real_name", "name", "email", "can_login", "email_enabled", "login_denied_text"}
ADMIN = {"id": 1, "real_name": "Test Admin", "name": "admin@bugzilla.test", "email": "admin@bugzilla.test",
         "can_login": True, "email_enabled": True, "login_denied_text": ""}
ALICE = {"id": 2, "real_name": "Alice Ng", "name": "alice.ng@bugzilla.test", "email": "alice.ng@bugzilla.test",
         "can_login": True, "email_enabled": True, "login_denied_text": ""}


def gname(prefix="parity_group"):
    return unique(prefix).replace(" ", "_")


@pytest.fixture
def new_group(api, admin_token):
    """Factory: create a fresh group as admin and return (id, name)."""
    def _create(**overrides):
        body = {"name": gname(), "description": "created by the parity suite"}
        body.update(overrides)
        r = api.post("/group", token=admin_token, json=body)
        assert r.status_code == 201, r.text
        return r.json()["id"], body["name"]
    return _create


def get_groups(api, token, **params):
    return ok(api.get("/group", token=token, params=params))["groups"]


# ---------------------------------------------------------------- GET /group

def test_get_all_groups_as_admin_lists_system_groups(api, admin_token):
    groups = get_groups(api, admin_token)
    assert all(set(g) == GROUP_FIELDS for g in groups)
    by_name = {g["name"]: g for g in groups}
    assert by_name["admin"] == {"id": 1, "name": "admin", "description": "Administrators", "is_active": True,
                                "is_bug_group": False, "user_regexp": ""}
    assert by_name["editbugs"]["user_regexp"] == ".*"
    assert {"creategroups", "editusers", "editbugs", "canconfirm", "tweakparams"} <= set(by_name)
    # Bugzilla::Group LIST_ORDER = isbuggroup, name (case-insensitive DB collation)
    keys = [(g["is_bug_group"], g["name"].lower()) for g in groups]
    assert keys == sorted(keys)


def test_get_by_ids_and_names(api, admin_token, new_group):
    gid, name = new_group(description="  spaced description  ", user_regexp="  ^nobody@example\\.com$ ")
    (g,) = get_groups(api, admin_token, ids=gid)
    assert g == {"id": gid, "name": name, "description": "spaced description", "is_active": False,
                 "is_bug_group": True, "user_regexp": "^nobody@example\\.com$"}
    assert get_groups(api, admin_token, names=name) == [g]
    # names are trimmed and matched case-insensitively
    assert get_groups(api, admin_token, names=f"  {name.upper()}  ") == [g]


def test_get_ids_sorted_then_names_in_given_order(api, admin_token, new_group):
    gid, name = new_group()
    groups = get_groups(api, admin_token, ids=[gid, 1], names=["editbugs", "admin"])
    assert [g["id"] for g in groups] == [1, gid, 8]
    assert [g["name"] for g in groups] == ["admin", name, "editbugs"]


def test_get_names_deduped_by_exact_name_only(api, admin_token, new_group):
    gid, name = new_group()
    groups = get_groups(api, admin_token, ids=gid, names=[name, name.upper()])
    assert [g["id"] for g in groups] == [gid, gid]


def test_get_ids_are_not_comma_split(api, admin_token):
    expect_error(api.get("/group", token=admin_token, params={"ids": "1,2"}), 400, 52,
                 "Invalid parameter passed to Bugzilla::Group::new_from_list: It must be numeric.")
    expect_error(api.get("/group", token=admin_token, params={"ids": "abc"}), 400, 52)


def test_get_unknown_id_is_silently_skipped(api, admin_token):
    assert get_groups(api, admin_token, ids=999999) == []
    assert [g["id"] for g in get_groups(api, admin_token, ids=[999999, 1])] == [1]


def test_get_unknown_or_empty_name(api, admin_token):
    expect_error(api.get("/group", token=admin_token, params={"names": "no_such_parity_group"}), 404, 51,
                 "There is no group named 'no_such_parity_group'.")
    expect_error(api.get("/group", token=admin_token, params={"names": ""}), 400, 50,
                 "You must select/enter a group.")


def test_get_requires_login(api):
    expect_error(api.get("/group"), 401, 410, "You must log in before using this part of Bugzilla.")
    expect_error(api.get("/group", params={"ids": 1}), 401, 410)


def test_get_as_unprivileged_user(api, user_token):
    expect_error(api.get("/group", token=user_token), 400, 805,
                 "In order to view groups, you need to be a member of the 'editusers' or 'creategroups' group, "
                 "or have bless privileges to at least one group.")
    expect_error(api.get("/group", token=user_token, params={"names": "admin"}), 400, 805)


def test_get_membership(api, admin_token, new_group):
    # the regexp grants alice membership; admin inherits it through group_group_map
    gid, name = new_group(user_regexp="^ALICE\\.ng@bugzilla\\.test$")
    (g,) = get_groups(api, admin_token, ids=gid, membership=1)
    assert set(g) == GROUP_FIELDS | {"membership"}
    assert g["membership"] == [ADMIN, ALICE]
    assert all(set(m) == MEMBER_FIELDS for m in g["membership"])
    (admin,) = get_groups(api, admin_token, names="admin", membership="yes")
    assert admin["membership"] == [ADMIN]


@pytest.mark.parametrize("value", ["0", ""])
def test_get_membership_false_values(api, admin_token, value):
    (g,) = get_groups(api, admin_token, ids=1, membership=value)
    assert "membership" not in g


def test_get_ignores_include_and_exclude_fields(api, admin_token):
    (g,) = get_groups(api, admin_token, ids=1, include_fields="name")
    assert set(g) == GROUP_FIELDS
    (g,) = get_groups(api, admin_token, ids=1, exclude_fields="name,description")
    assert set(g) == GROUP_FIELDS


def test_get_single_group_path_is_not_a_resource(api, admin_token):
    expect_error(api.get("/group/1", token=admin_token), 404, 32614,
                 "A REST API resource was not found for 'GET /group/1'.")
    expect_error(api.get("/group/admin", token=admin_token), 404, 32614)
    expect_error(api.put("/group", token=admin_token, json={"description": "x"}), 404, 32614,
                 "A REST API resource was not found for 'PUT /group'.")


# --------------------------------------------------------------- POST /group

def test_create_returns_201_with_id_and_defaults(api, admin_token):
    name = gname()
    r = api.post("/group", token=admin_token, json={"name": f"  {name}  ", "description": " Parity desc "})
    assert normalize(ok(r, 201)) == {"id": ID}
    (g,) = get_groups(api, admin_token, ids=r.json()["id"])
    assert g == {"id": r.json()["id"], "name": name, "description": "Parity desc", "is_active": False,
                 "is_bug_group": True, "user_regexp": ""}


def test_create_with_all_fields(api, admin_token):
    name = gname()
    gid = ok(api.post("/group", token=admin_token, json={
        "name": name, "description": "all fields", "is_active": 1, "user_regexp": "^bruno\\.costa@",
        "icon_url": "http://example.com/i.png"}), 201)["id"]
    (g,) = get_groups(api, admin_token, ids=gid, membership=1)
    assert g["is_active"] is True and g["user_regexp"] == "^bruno\\.costa@"
    assert [m["name"] for m in g["membership"]] == ["admin@bugzilla.test", "bruno.costa@bugzilla.test"]


@pytest.mark.parametrize("value, expected", [(True, True), (False, False), ("false", True), ("0", False), (0, False)])
def test_create_is_active_uses_perl_truthiness(api, admin_token, value, expected):
    gid = ok(api.post("/group", token=admin_token, json={"name": gname(), "description": "d", "is_active": value}),
             201)["id"]
    assert get_groups(api, admin_token, ids=gid)[0]["is_active"] is expected


def test_create_requires_login_and_creategroups(api, user_token):
    body = {"name": gname(), "description": "d"}
    expect_error(api.post("/group", json=body), 401, 410)
    expect_error(api.post("/group", token=user_token, json=body), 401, 304,
                 "Sorry, you aren't a member of the 'creategroups' group")


def test_create_validation_errors(api, admin_token, new_group):
    _, existing = new_group()
    post = lambda **body: api.post("/group", token=admin_token, json=body)
    expect_error(post(description="d"), 400, 800, "You must enter a name for the group.")
    expect_error(post(name="   ", description="d"), 400, 800)
    expect_error(post(name=gname()), 400, 802, "You must enter a description for the group.")
    expect_error(post(name=gname(), description="  "), 400, 802)
    expect_error(post(name=existing, description="d"), 400, 801, f"The group {existing} already exists.")
    expect_error(post(name=existing.upper(), description="d"), 400, 801, f"The group {existing.upper()} already exists.")
    expect_error(post(name=gname(), description="d", user_regexp="("), 400, 803,
                 "The regular expression you entered is invalid.")
    # validators run in column order: description, name, userregexp
    expect_error(post(name="", description=""), 400, 802)
    expect_error(post(name="", description="d", user_regexp="("), 400, 800)


# ---------------------------------------------------- PUT /group/{id_or_name}

def test_update_all_fields_reports_changes(api, admin_token, new_group):
    gid, name = new_group(icon_url="http://example.com/old.png")
    new_name = gname()
    r = api.put(f"/group/{gid}", token=admin_token, json={
        "name": new_name, "description": "new desc", "is_active": 1, "user_regexp": "^dana\\.okafor@",
        "icon_url": "http://example.com/new.png"})
    assert ok(r) == {"groups": [{"id": gid, "changes": {
        "name": {"removed": name, "added": new_name},
        "description": {"removed": "created by the parity suite", "added": "new desc"},
        "is_active": {"removed": "0", "added": "1"},
        "user_regexp": {"removed": "", "added": "^dana\\.okafor@"},
        "icon_url": {"removed": "http://example.com/old.png", "added": "http://example.com/new.png"},
    }}]}
    (g,) = get_groups(api, admin_token, ids=gid, membership=1)
    assert g == {"id": gid, "name": new_name, "description": "new desc", "is_active": True, "is_bug_group": True,
                 "user_regexp": "^dana\\.okafor@", "membership": [ADMIN, {
                     "id": 5, "real_name": "Dana Okafor", "name": "dana.okafor@bugzilla.test",
                     "email": "dana.okafor@bugzilla.test", "can_login": True, "email_enabled": True,
                     "login_denied_text": ""}]}


def test_update_by_name_and_noop(api, admin_token, new_group):
    gid, name = new_group()
    assert ok(api.put(f"/group/{name}", token=admin_token, json={"description": "created by the parity suite"})) == \
        {"groups": [{"id": gid, "changes": {}}]}
    assert ok(api.put(f"/group/{name}", token=admin_token, json={"is_active": "false"})) == \
        {"groups": [{"id": gid, "changes": {"is_active": {"removed": "0", "added": "1"}}}]}
    assert ok(api.put(f"/group/{name}", token=admin_token, json={"is_active": False})) == \
        {"groups": [{"id": gid, "changes": {"is_active": {"removed": "1", "added": "0"}}}]}
    # the lookup is case-insensitive, the stored name keeps its case
    assert ok(api.put(f"/group/{name.upper()}", token=admin_token, json={"name": name.upper()})) == \
        {"groups": [{"id": gid, "changes": {"name": {"removed": name, "added": name.upper()}}}]}


def test_update_path_plus_ids_and_names_in_body(api, admin_token, new_group):
    gid1, name1 = new_group()
    gid2, name2 = new_group()
    r = ok(api.put(f"/group/{name1}", token=admin_token, json={"ids": [gid2], "description": "both"}))
    assert [g["id"] for g in r["groups"]] == [gid1, gid2]
    assert all(g["changes"] == {"description": {"removed": "created by the parity suite", "added": "both"}}
               for g in r["groups"])
    r = ok(api.put(f"/group/{gid1}", token=admin_token, json={"names": [name1], "description": "once"}))
    assert r == {"groups": [{"id": gid1, "changes": {"description": {"removed": "both", "added": "once"}}}]}


def test_update_unknown_field(api, admin_token, new_group):
    gid, _ = new_group()
    expect_error(api.put(f"/group/{gid}", token=admin_token, json={"foo": "bar"}), 400, 67399,
                 "The requested method 'Bugzilla::Group::set_foo' was not found.")
    # set_all applies the fields in sorted order, so the error depends on the position
    expect_error(api.put(f"/group/{gid}", token=admin_token, json={"foo": "x", "name": ""}), 400, 67399)
    expect_error(api.put(f"/group/{gid}", token=admin_token, json={"zzz": "x", "name": ""}), 400, 800)


def test_update_validation_errors(api, admin_token, new_group):
    gid, _ = new_group()
    put = lambda **body: api.put(f"/group/{gid}", token=admin_token, json=body)
    expect_error(put(name="admin"), 400, 801, "The group admin already exists.")
    expect_error(put(name="  "), 400, 800, "You must enter a name for the group.")
    expect_error(put(description=""), 400, 802, "You must enter a description for the group.")
    expect_error(put(user_regexp="("), 400, 803, "The regular expression you entered is invalid.")
    expect_error(put(name="admin", user_regexp="("), 400, 801)
    expect_error(put(name="", description=""), 400, 802)


def test_update_unknown_group(api, admin_token):
    expect_error(api.put("/group/999999", token=admin_token, json={"description": "x"}), 404, 51,
                 "There is no group with the id '999999'.")
    expect_error(api.put("/group/0", token=admin_token, json={"description": "x"}), 404, 51,
                 "There is no group with the id '0'.")
    expect_error(api.put("/group/no_such_parity_group", token=admin_token, json={"description": "x"}), 404, 51,
                 "There is no group named 'no_such_parity_group'.")


def test_update_requires_login_and_creategroups(api, user_token):
    expect_error(api.put("/group/admin", json={"description": "x"}), 401, 410)
    expect_error(api.put("/group/admin", token=user_token, json={"description": "x"}), 401, 304,
                 "Sorry, you aren't a member of the 'creategroups' group")
