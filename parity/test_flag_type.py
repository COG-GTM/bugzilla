"""Parity tests for the FlagType WebService (GET/POST/PUT /flag_type)."""
import uuid

import pytest

from parity.conftest import SEED_COMPONENT, SEED_PRODUCT, expect_error, ok

OTHER_PRODUCT = "Lighthouse Billing"
ALL_PRODUCTS = ["TestProduct", SEED_PRODUCT, OTHER_PRODUCT]
NO_GROUPS_VALUES = ["X", "?", "+", "-"]


def ft_name():
    return "pft-" + uuid.uuid4().hex[:10]


def base_item(ft_id, name, **over):
    item = {"id": ft_id, "name": name, "description": "parity flag type", "type": "bug",
            "values": NO_GROUPS_VALUES, "is_active": True, "is_requesteeble": True, "is_multiplicable": True}
    item.update(over)
    return item


@pytest.fixture
def new_flag_type(api, admin_token):
    """Factory: create a flag type as admin -> (id, name). Hidden from every product on teardown."""
    created = []

    def _create(**body):
        body.setdefault("name", ft_name())
        body.setdefault("description", "parity flag type")
        r = api.post("/flag_type", token=admin_token, json=body)
        assert r.status_code == 201, r.text
        created.append(r.json()["id"])
        return r.json()["id"], body["name"]

    yield _create
    for ft_id in created:
        api.put(f"/flag_type/{ft_id}", token=admin_token,
                json={"is_active": 0, "inclusions": [], "exclusions": ALL_PRODUCTS})


def find(items, ft_id):
    found = [i for i in items if i["id"] == ft_id]
    return found[0] if found else None


def get_types(api, product, component=None, token=None):
    path = f"/flag_type/{product}" + (f"/{component}" if component else "")
    body = ok(api.get(path, token=token))
    assert set(body) == {"bug", "attachment"}
    return body


# ------------------------------------------------------------------ GET
def test_get_unknown_product(api):
    expect_error(api.get("/flag_type/no-such-product-xyz"), 401, 106,
                 "Either the product 'no-such-product-xyz' does not exist or you don't have access to it.")


def test_get_unknown_component(api):
    expect_error(api.get(f"/flag_type/{SEED_PRODUCT}/no-such-comp"), 404, 51,
                 f"There is no component named 'no-such-comp' in the '{SEED_PRODUCT}' product.")


def test_get_default_flag_type_everywhere(api, new_flag_type):
    ft_id, name = new_flag_type()
    for product in ALL_PRODUCTS:
        body = get_types(api, product)
        assert find(body["bug"], ft_id) == base_item(ft_id, name)
        assert find(body["attachment"], ft_id) is None
    body = get_types(api, SEED_PRODUCT, SEED_COMPONENT)
    assert find(body["bug"], ft_id) == base_item(ft_id, name)


def test_get_ignores_include_fields(api, new_flag_type):
    ft_id, name = new_flag_type()
    body = ok(api.get(f"/flag_type/{SEED_PRODUCT}", params={"include_fields": "bug"}))
    assert set(body) == {"bug", "attachment"}
    assert find(body["bug"], ft_id) == base_item(ft_id, name)


def test_get_attachment_type_and_false_booleans(api, new_flag_type):
    ft_id, name = new_flag_type(target_type="attachment", is_requestable=False, is_active="0",
                                is_multiplicable=0, is_specifically_requestable=False, description="  att  ")
    body = get_types(api, SEED_PRODUCT, SEED_COMPONENT)
    assert find(body["bug"], ft_id) is None
    assert find(body["attachment"], ft_id) == base_item(
        ft_id, name, description="att", type="attachment", values=["X", "+", "-"],
        is_active=False, is_requesteeble=False, is_multiplicable=False)


def test_get_boolean_string_quirks(api, new_flag_type):
    # Perl only treats '0' (and JSON false) as off: "false" is truthy, "" is falsy via check_boolean.
    ft_id, _ = new_flag_type(is_multiplicable="false", is_active="", is_specifically_requestable="0")
    item = find(get_types(api, SEED_PRODUCT)["bug"], ft_id)
    assert (item["is_multiplicable"], item["is_active"], item["is_requesteeble"]) == (True, False, False)


def test_get_values_depend_on_groups(api, admin_token, user_token, new_flag_type):
    a, _ = new_flag_type(is_requestable=True, grant_group="editbugs", request_group="canconfirm")
    b, _ = new_flag_type(is_requestable=True, grant_group="admin", request_group="editbugs")
    c, _ = new_flag_type(is_requestable=True, grant_group="admin", request_group="admin")
    d, _ = new_flag_type(is_requestable=False, grant_group="editbugs")

    def values(token):
        bug = get_types(api, "TestProduct", token=token)["bug"]
        return [find(bug, i)["values"] for i in (a, b, c, d)]

    assert values(None) == [["X"], ["X"], ["X"], ["X"]]
    assert values(user_token) == [["X", "?", "+", "-"], ["X", "?"], ["X"], ["X", "+", "-"]]
    assert values(admin_token) == [["X", "?", "+", "-"]] * 3 + [["X", "+", "-"]]


def test_get_product_inclusions_and_exclusions(api, new_flag_type):
    inc, _ = new_flag_type(inclusions=[SEED_PRODUCT])
    exc, _ = new_flag_type(exclusions=[SEED_PRODUCT])
    both, _ = new_flag_type(inclusions=[SEED_PRODUCT], exclusions=[SEED_PRODUCT])
    dup, _ = new_flag_type(inclusions=[SEED_PRODUCT, SEED_PRODUCT])
    seed = get_types(api, SEED_PRODUCT)["bug"]
    comp = get_types(api, SEED_PRODUCT, SEED_COMPONENT)["bug"]
    other = get_types(api, OTHER_PRODUCT)["bug"]
    assert find(seed, inc) is not None and find(comp, inc) is not None and find(other, inc) is None
    assert find(seed, exc) is None and find(comp, exc) is None and find(other, exc) is not None
    assert find(seed, both) is None and find(other, both) is None
    assert find(seed, dup) is not None
    for item in (find(seed, inc), find(other, exc)):
        assert "inclusions" not in item and "exclusions" not in item


def test_get_hash_and_empty_clusions_match_nothing(api, new_flag_type):
    # Perl silently drops hash-shaped {product: [components]} items, leaving the type with no inclusion rows.
    h, _ = new_flag_type(inclusions=[{SEED_PRODUCT: [SEED_COMPONENT]}])
    e, _ = new_flag_type(inclusions=[])
    for product in ALL_PRODUCTS:
        bug = get_types(api, product)["bug"]
        assert find(bug, h) is None and find(bug, e) is None
    assert find(get_types(api, SEED_PRODUCT, SEED_COMPONENT)["bug"], h) is None


def test_get_order_is_sortkey_then_name(api, new_flag_type):
    suffix = uuid.uuid4().hex[:8]
    c, _ = new_flag_type(name=f"pft-c-{suffix}", sortkey=20)
    b, _ = new_flag_type(name=f"pft-b-{suffix}", sortkey=20)
    a, _ = new_flag_type(name=f"pft-a-{suffix}", sortkey=19)
    d, _ = new_flag_type(name=f"pft-d-{suffix}", sortkey="007")
    ids = [i["id"] for i in get_types(api, OTHER_PRODUCT)["bug"] if i["id"] in (a, b, c, d)]
    assert ids == [d, a, b, c]


# ------------------------------------------------------------------ POST
def test_create_requires_login(api):
    expect_error(api.post("/flag_type", json={"name": ft_name(), "description": "d"}), 401, 410,
                 "You must log in before using this part of Bugzilla.")


def test_create_requires_editcomponents(api, user_token):
    expect_error(api.post("/flag_type", token=user_token, json={"name": ft_name(), "description": "d"}), 401, 304,
                 "Sorry, you aren't a member of the 'editcomponents' group, and so you are not authorized to "
                 "add new flag types.")


@pytest.mark.parametrize("body,param", [
    ({"description": "d"}, "name"),
    ({"name": "0", "description": "d"}, "name"),
    ({"name": "", "description": "d"}, "name"),
    ({"name": "pft-x"}, "description"),
    ({"name": "pft-x", "description": "0"}, "description"),
    ({"name": "bad name", "description": "", "cc_list": "bad", "sortkey": -1}, "description"),
])
def test_create_missing_params(api, admin_token, body, param):
    expect_error(api.post("/flag_type", token=admin_token, json=body), 400, 50,
                 f"The function requires a {param} argument, and that argument was not set.")


@pytest.mark.parametrize("name,shown", [
    ("bad name", "bad name"), ("a,b", "a,b"), ("a" * 51, "a" * 51), (" ", ""), ("  x y  ", "x y"),
])
def test_create_invalid_name(api, admin_token, name, shown):
    body = expect_error(api.post("/flag_type", token=admin_token, json={"name": name, "description": "d"}), 400, 1101)
    expected = f"The name {shown} must be" if shown else "The name must be"
    assert body["message"] == expected + " 1-50 characters long and must not contain any spaces or commas."


def test_create_blank_description(api, admin_token):
    expect_error(api.post("/flag_type", token=admin_token, json={"name": ft_name(), "description": "   "}), 400, 1102,
                 "You must enter a description for this flag type.")


EMAIL_MSG = ("The e-mail address you entered ({}) didn't pass our syntax checking for a legal email address. "
             "A legal address must contain exactly one '@', and at least one '.' after the @. "
             "It also must not contain any illegal characters.")


@pytest.mark.parametrize("cc_list,addr", [
    ("not-an-email", "not-an-email"), ("a@@b.com", "a@@b.com"), ("a b@c.com", "a"), (" , a@b.com", ""),
    ("ok@b.com, bad", "bad"),
])
def test_create_invalid_cc_list(api, admin_token, cc_list, addr):
    expect_error(api.post("/flag_type", token=admin_token, json={"name": ft_name(), "description": "d", "cc_list": cc_list}),
                 400, 501, EMAIL_MSG.format(addr))


def test_create_cc_list_too_long(api, admin_token):
    cc = ",".join(f"u{i:03d}@example.com" for i in range(20))
    expect_error(api.post("/flag_type", token=admin_token, json={"name": ft_name(), "description": "d", "cc_list": cc}),
                 400, 1103, f"The CC list {cc} must be less than 200 characters long.")


@pytest.mark.parametrize("sortkey,shown", [("abc", None), (40000, "40000"), (-1, None), (7.5, None), ("", None)])
def test_create_invalid_sortkey(api, admin_token, sortkey, shown):
    body = expect_error(api.post("/flag_type", token=admin_token,
                                 json={"name": ft_name(), "description": "d", "sortkey": sortkey}), 400, 1104)
    expected = f"The sort key {shown} must be" if shown else "The sort key must be"
    assert body["message"] == expected + " an integer between 0 and 32767."


def test_create_invalid_target_type(api, admin_token):
    expect_error(api.post("/flag_type", token=admin_token,
                          json={"name": ft_name(), "description": "d", "target_type": "foo"}), 400, 68000,
                 "The target type was neither bug nor attachment but rather foo.")


@pytest.mark.parametrize("field", ["grant_group", "request_group"])
def test_create_unknown_group(api, admin_token, field):
    expect_error(api.post("/flag_type", token=admin_token,
                          json={"name": ft_name(), "description": "d", field: "no-such-group"}), 404, 51,
                 "There is no group named 'no-such-group'.")


@pytest.mark.parametrize("field", ["inclusions", "exclusions"])
@pytest.mark.parametrize("item", ["no-such-product-xyz", 5])
def test_create_clusion_unknown_product(api, admin_token, field, item):
    expect_error(api.post("/flag_type", token=admin_token,
                          json={"name": ft_name(), "description": "d", field: [item]}), 401, 106,
                 f"Either the product '{item}' does not exist or you don't have access to it.")


def test_create_clusion_invalid_item(api, admin_token):
    expect_error(api.post("/flag_type", token=admin_token,
                          json={"name": ft_name(), "description": "d", "inclusions": [["x"]]}), 400, 53)


def test_create_validation_precedence(api, admin_token):
    # inclusions are processed before any field validator; validators run in alphabetical field order
    expect_error(api.post("/flag_type", token=admin_token, json={
        "name": "bad name", "description": "d", "cc_list": "bad", "inclusions": ["no-such-product-xyz"]}), 401, 106)
    expect_error(api.post("/flag_type", token=admin_token, json={
        "name": "bad name", "description": "d", "grant_group": "zz", "sortkey": -1, "target_type": "x"}), 404, 51,
        "There is no group named 'zz'.")
    expect_error(api.post("/flag_type", token=admin_token, json={
        "name": "bad name", "description": "d", "sortkey": -1, "target_type": "x"}), 400, 1101)
    expect_error(api.post("/flag_type", token=admin_token, json={
        "name": ft_name(), "description": "d", "sortkey": -1, "target_type": "x"}), 400, 1104)


def test_create_returns_id_and_applies_defaults(api, admin_token, new_flag_type):
    name = ft_name()
    r = api.post("/flag_type", token=admin_token, json={"name": f"  {name}  ", "description": " d ", "foo": "x",
                                                         "is_requesteeble": 0})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id"} and isinstance(body["id"], int)
    ft_id = body["id"]
    # defaults: re-sending them must produce no changes
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={
        "name": name, "description": "d", "cc_list": "", "sortkey": 1, "is_active": 1, "is_requestable": 1,
        "is_specifically_requestable": 1, "is_multiplicable": 1, "grant_group": "", "request_group": ""})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": name, "changes": {}}]}
    assert find(get_types(api, "TestProduct")["bug"], ft_id) == base_item(ft_id, name, description="d")
    api.put(f"/flag_type/{ft_id}", token=admin_token, json={"is_active": 0, "exclusions": ALL_PRODUCTS})


def test_create_with_all_fields_is_stored(api, admin_token, new_flag_type):
    ft_id, name = new_flag_type(cc_list="a@b.com c@d.org,e@f", sortkey="007", target_type="attachment",
                                is_requestable=0, grant_group="editbugs", request_group="canconfirm")
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={
        "cc_list": "a@b.com c@d.org,e@f", "sortkey": 7, "is_requestable": 0, "grant_group": "editbugs",
        "request_group": "canconfirm", "is_specifically_requestable": "on"})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": name, "changes": {}}]}


# ------------------------------------------------------------------ PUT
def test_update_requires_login(api, new_flag_type):
    ft_id, _ = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", json={"description": "x"}), 401, 410)


def test_update_requires_editcomponents(api, user_token, new_flag_type):
    ft_id, _ = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", token=user_token, json={"description": "x"}), 401, 304,
                 "Sorry, you aren't a member of the 'editcomponents' group, and so you are not authorized to "
                 "add, modify or delete flag types.")


def test_update_unknown_id(api, admin_token):
    expect_error(api.put("/flag_type/999999", token=admin_token, json={"description": "x"}), 404, 51,
                 "There is no flagtype with the id '999999'.")


def test_update_unknown_name_matches_nothing(api, admin_token):
    assert ok(api.put("/flag_type/no-such-flag-type", token=admin_token, json={"description": "x"})) == {"flagtypes": []}
    assert ok(api.put("/flag_type/1", token=admin_token, json={"ids": [], "description": "x"})) == {"flagtypes": []}


def test_update_non_numeric_id_in_body(api, admin_token):
    expect_error(api.put("/flag_type/1", token=admin_token, json={"ids": ["abc"], "description": "x"}), 400, 52,
                 "Invalid parameter passed to Bugzilla::FlagType::_load_from_db: It must be numeric.")


def test_update_one_bad_id_fails_everything(api, admin_token, new_flag_type):
    ft_id, name = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, json={"ids": [999999, ft_id], "sortkey": 3}),
                 404, 51)
    assert ok(api.put(f"/flag_type/{ft_id}", token=admin_token, json={"sortkey": 1})) == {
        "flagtypes": [{"id": ft_id, "name": name, "changes": {}}]}


def test_update_no_changes_and_token_in_body(api, admin_token, new_flag_type):
    ft_id, name = new_flag_type()
    assert ok(api.put(f"/flag_type/{ft_id}", token=admin_token, json={})) == {
        "flagtypes": [{"id": ft_id, "name": name, "changes": {}}]}
    r = api.put(f"/flag_type/{ft_id}", json={"token": admin_token, "description": "d2", "Bugzilla_token": "zz"})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": name,
                                    "changes": {"description": {"removed": "parity flag type", "added": "d2"}}}]}


def test_update_all_fields(api, admin_token, new_flag_type):
    ft_id, name = new_flag_type()
    new_name = ft_name()
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={
        "name": f" {new_name} ", "description": "  d3  ", "cc_list": "a@b.com", "sortkey": 7, "is_active": False,
        "is_requestable": 0, "is_specifically_requestable": "0", "is_multiplicable": False,
        "grant_group": "editbugs", "request_group": "canconfirm", "inclusions": [SEED_PRODUCT],
        "exclusions": [OTHER_PRODUCT]})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": new_name, "changes": {
        "name": {"removed": name, "added": new_name},
        "description": {"removed": "parity flag type", "added": "d3"},
        "cc_list": {"removed": "", "added": "a@b.com"},
        "sortkey": {"removed": "1", "added": "7"},
        "is_active": {"removed": "1", "added": "0"},
        "is_requestable": {"removed": "1", "added": "0"},
        "is_requesteeble": {"removed": "1", "added": "0"},
        "is_multiplicable": {"removed": "1", "added": "0"},
        "grant_group_id": {"removed": None, "added": "8"},
        "request_group_id": {"removed": None, "added": "9"},
        "inclusions": {"removed": "0", "added": "1"},
        "exclusions": {"removed": "0", "added": "1"},
    }}]}
    # same values again -> nothing but the clusions flags
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={
        "name": new_name, "sortkey": "7", "is_active": 0, "grant_group": "editbugs", "inclusions": [SEED_PRODUCT]})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": new_name,
                                    "changes": {"inclusions": {"removed": "0", "added": "1"}}}]}
    item = find(get_types(api, SEED_PRODUCT)["bug"], ft_id)
    assert item == base_item(ft_id, new_name, description="d3", values=["X"], is_active=False,
                             is_requesteeble=False, is_multiplicable=False)
    assert find(get_types(api, OTHER_PRODUCT)["bug"], ft_id) is None
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={
        "grant_group": "", "request_group": "", "is_active": "yes", "is_requestable": True, "cc_list": ""})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": new_name, "changes": {
        "cc_list": {"removed": "a@b.com", "added": ""},
        "is_active": {"removed": "0", "added": "1"},
        "is_requestable": {"removed": "0", "added": "1"},
        "grant_group_id": {"removed": "8", "added": None},
        "request_group_id": {"removed": "9", "added": None},
    }}]}


def test_update_clusions(api, admin_token, new_flag_type):
    ft_id, name = new_flag_type(inclusions=[SEED_PRODUCT])
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={"inclusions": [], "exclusions": []})
    assert ok(r) == {"flagtypes": [{"id": ft_id, "name": name, "changes": {
        "inclusions": {"removed": "0", "added": "1"}, "exclusions": {"removed": "0", "added": "1"}}}]}
    assert find(get_types(api, SEED_PRODUCT)["bug"], ft_id) is None
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={"inclusions": [OTHER_PRODUCT, SEED_PRODUCT]})
    assert ok(r)["flagtypes"][0]["changes"] == {"inclusions": {"removed": "0", "added": "1"}}
    assert find(get_types(api, SEED_PRODUCT)["bug"], ft_id) is not None
    assert find(get_types(api, OTHER_PRODUCT)["bug"], ft_id) is not None
    assert find(get_types(api, "TestProduct")["bug"], ft_id) is None
    r = api.put(f"/flag_type/{ft_id}", token=admin_token, json={"inclusions": [{SEED_PRODUCT: [SEED_COMPONENT]}]})
    assert ok(r)["flagtypes"][0]["changes"] == {"inclusions": {"removed": "0", "added": "1"}}
    assert find(get_types(api, SEED_PRODUCT)["bug"], ft_id) is None
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, json={"exclusions": ["no-such-product-xyz"]}),
                 401, 106, "Either the product 'no-such-product-xyz' does not exist or you don't have access to it.")
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, json={"exclusions": [["x"]]}), 400, 53)


@pytest.mark.parametrize("key", ["foo", "is_requesteeble", "target_type", "include_fields"])
def test_update_unknown_field(api, admin_token, new_flag_type, key):
    ft_id, _ = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, json={key: "attachment"}), 400, 67399,
                 f"The requested method 'Bugzilla::FlagType::set_{key}' was not found.")


def test_update_query_string_extras_are_fields_too(api, admin_token, new_flag_type):
    ft_id, _ = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, params={"include_fields": "id"},
                         json={"sortkey": 2}), 400, 67399,
                 "The requested method 'Bugzilla::FlagType::set_include_fields' was not found.")


@pytest.mark.parametrize("body,status,code,msg", [
    ({"name": "has space"}, 400, 1101, "The name has space must be 1-50"),
    ({"name": None}, 400, 1101, "The name must be 1-50"),
    ({"description": ""}, 400, 1102, "You must enter a description for this flag type."),
    ({"grant_group": "nogroup"}, 404, 51, "There is no group named 'nogroup'."),
    ({"request_group": "nogroup"}, 404, 51, "There is no group named 'nogroup'."),
    ({"sortkey": -1}, 400, 1104, "The sort key must be an integer between 0 and 32767."),
    ({"sortkey": 32768}, 400, 1104, "The sort key 32768 must be an integer between 0 and 32767."),
    ({"cc_list": "bad"}, 400, 501, EMAIL_MSG.format("bad")),
    ({"description": "", "foo": "x", "name": "bad name"}, 400, 1102, None),
    ({"foo": "x", "name": "bad name"}, 400, 67399, None),
    ({"name": "bad name", "sortkey": -1}, 400, 1101, None),
])
def test_update_invalid_values(api, admin_token, new_flag_type, body, status, code, msg):
    ft_id, _ = new_flag_type()
    expect_error(api.put(f"/flag_type/{ft_id}", token=admin_token, json=body), status, code, msg)


def test_update_by_name_and_id_lists(api, admin_token, new_flag_type):
    a, name = new_flag_type()
    b, _ = new_flag_type(name=name, description="dup")
    r = ok(api.put(f"/flag_type/{name}", token=admin_token, json={"description": "byname"}))
    assert sorted(r["flagtypes"], key=lambda x: x["id"]) == [
        {"id": a, "name": name, "changes": {"description": {"removed": "parity flag type", "added": "byname"}}},
        {"id": b, "name": name, "changes": {"description": {"removed": "dup", "added": "byname"}}}]
    r = ok(api.put(f"/flag_type/{a}", token=admin_token, json={"ids": [b, a, b], "sortkey": 9}))
    assert sorted(r["flagtypes"], key=lambda x: x["id"]) == [
        {"id": a, "name": name, "changes": {"sortkey": {"removed": "1", "added": "9"}}},
        {"id": b, "name": name, "changes": {"sortkey": {"removed": "1", "added": "9"}}}]
    r = ok(api.put(f"/flag_type/{a}", token=admin_token, json={"names": [name], "sortkey": 10}))
    assert sorted(x["id"] for x in r["flagtypes"]) == [a, b]
    r = ok(api.put(f"/flag_type/{a}", token=admin_token, json={"names": ["no-such-flag-type"], "sortkey": 11}))
    assert r == {"flagtypes": [{"id": a, "name": name, "changes": {"sortkey": {"removed": "10", "added": "11"}}}]}
