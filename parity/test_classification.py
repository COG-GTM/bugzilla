"""Bugzilla::WebService::Classification - Classification.get (GET /classification/{id_or_name}).

There is no REST route that creates classifications, groups or group/product restrictions, so the
fixtures below insert them straight into the shared MariaDB through ``docker compose exec`` (same
rows for both backends) and delete them afterwards. Tests needing those fixtures are skipped when
the database container is not reachable; everything else only needs the seeded data.
"""
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from parity.conftest import USER_LOGIN, USER_PASSWORD, expect_error, ok
from parity.normalize import ID, normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_SERVICE = os.environ.get("BZ_DB_SERVICE", "bugzilla5.db")
WEB_SERVICE = os.environ.get("BZ_WEB_SERVICE", "bugzilla5.web")
PARAMS_JSON = "/var/www/html/data/params.json"
SEED_PRODUCTS = {"Orbit CRM", "Lighthouse Billing"}
PRODUCT_KEYS = {"id", "name", "description"}
CLASSIFICATION_KEYS = {"id", "name", "description", "sort_key", "products"}

NUMERIC_MSG = "Invalid parameter passed to Bugzilla::Classification::_load_from_db: It must be numeric."


def _compose(*args, check=True, input=None):
    cmd = ["docker", "compose", "exec", "-T", *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, input=input, capture_output=True, text=True, check=check)


def _sql(statement: str) -> str:
    user = os.environ.get("BZ_DB_USER", "bugs")
    password = os.environ.get("BZ_DB_PASS", "bugzilla")
    name = os.environ.get("BZ_DB_NAME", "bugs")
    r = _compose(DB_SERVICE, "mysql", f"-u{user}", f"-p{password}", name, "-N", "-e", statement)
    return r.stdout.strip()


def _db_available() -> bool:
    try:
        return _sql("SELECT 1") == "1"
    except (OSError, subprocess.CalledProcessError):
        return False


@pytest.fixture(scope="module")
def db():
    if not _db_available():
        pytest.skip("docker compose database not reachable; cannot create classification fixtures")
    return _sql


def _q(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


@pytest.fixture(scope="module")
def extra_classification(db):
    """A second classification holding an open product and one restricted to a mandatory group
    nobody is a member of: only ``editclassifications`` users (admin) see the restricted one."""
    tag = uuid.uuid4().hex[:8]
    name = f"Parity Class {tag}"
    empty = f"Parity Empty {tag}"
    group = f"parity_class_group_{tag}"
    open_product = f"Parity Open {tag}"
    secret_product = f"Parity Secret {tag}"
    db(f"INSERT INTO classifications (name, description, sortkey) VALUES ({_q(name)}, 'parity suite classification', 7)")
    cid = int(db(f"SELECT id FROM classifications WHERE name = {_q(name)}"))
    db(f"INSERT INTO classifications (name, description, sortkey) VALUES ({_q(empty)}, 'no products at all', 9)")
    empty_id = int(db(f"SELECT id FROM classifications WHERE name = {_q(empty)}"))
    db(f"INSERT INTO groups (name, description, isbuggroup, userregexp, isactive) VALUES ({_q(group)}, 'parity', 1, '', 1)")
    gid = int(db(f"SELECT id FROM groups WHERE name = {_q(group)}"))
    db(f"INSERT INTO products (name, description, classification_id, isactive) VALUES "
       f"({_q(open_product)}, 'visible to everyone', {cid}, 1), ({_q(secret_product)}, 'members only', {cid}, 1)")
    pid_open = int(db(f"SELECT id FROM products WHERE name = {_q(open_product)}"))
    pid_secret = int(db(f"SELECT id FROM products WHERE name = {_q(secret_product)}"))
    # CONTROLMAPMANDATORY = 3 for members and others: the product is invisible outside the group
    db(f"INSERT INTO group_control_map (group_id, product_id, entry, membercontrol, othercontrol, canedit) "
       f"VALUES ({gid}, {pid_secret}, 0, 3, 3, 0)")
    try:
        yield {"id": cid, "name": name, "open": open_product, "secret": secret_product,
               "open_id": pid_open, "secret_id": pid_secret, "empty": empty, "empty_id": empty_id}
    finally:
        db(f"DELETE FROM group_control_map WHERE group_id = {gid}; DELETE FROM products WHERE id IN ({pid_open}, {pid_secret}); "
           f"DELETE FROM groups WHERE id = {gid}; DELETE FROM classifications WHERE id IN ({cid}, {empty_id})")


def _set_useclassification(value: str):
    _compose(WEB_SERVICE, "sed", "-i", f's/"useclassification" : "[01]"/"useclassification" : "{value}"/', PARAMS_JSON)


@pytest.fixture
def classification_disabled(db):
    """Flip ``useclassification`` off in Perl's params.json for one test (both backends read it)."""
    try:
        _compose(WEB_SERVICE, "grep", "-q", '"useclassification" : "1"', PARAMS_JSON)
    except subprocess.CalledProcessError:
        pytest.skip("useclassification is not enabled in params.json")
    _set_useclassification("0")
    try:
        yield
    finally:
        _set_useclassification("1")


def _wait_for(fn, timeout=8.0):
    """The Python service re-reads params.json at most every 2s; poll until the predicate holds."""
    deadline = time.time() + timeout
    while True:
        result = fn()
        if result or time.time() > deadline:
            return result
        time.sleep(0.5)


def _classification(api, id_or_name, **kw):
    body = ok(api.get(f"/classification/{id_or_name}", **kw))
    assert set(body) == {"classifications"}
    return body["classifications"]


# ---------------------------------------------------------------- happy paths (seed data only)
def test_get_by_id_anonymous_default_fields(api):
    (cls,) = _classification(api, 1)
    assert set(cls) == CLASSIFICATION_KEYS
    assert normalize({k: cls[k] for k in ("id", "name", "description", "sort_key")}) == {
        "id": ID, "name": "Unclassified", "description": "Not assigned to any classification", "sort_key": 0}
    assert all(set(p) == PRODUCT_KEYS for p in cls["products"])
    assert all(isinstance(p["id"], int) and isinstance(p["name"], str) for p in cls["products"])
    assert SEED_PRODUCTS <= {p["name"] for p in cls["products"]}


def test_products_are_sorted_by_name(api):
    (cls,) = _classification(api, "Unclassified")
    names = [p["name"] for p in cls["products"]]
    assert names == sorted(names) and len(set(names)) == len(names)


def test_get_by_name_equals_get_by_id(api):
    assert _classification(api, "Unclassified") == _classification(api, 1)


def test_name_lookup_is_case_insensitive(api):
    assert _classification(api, "UNCLASSIFIED") == _classification(api, "Unclassified")


def test_logged_in_user_and_admin_see_unclassified(api, user_token, admin_token):
    as_user = _classification(api, 1, token=user_token)
    as_admin = _classification(api, 1, token=admin_token)
    assert as_user[0]["name"] == as_admin[0]["name"] == "Unclassified"
    assert SEED_PRODUCTS <= {p["name"] for p in as_user[0]["products"]}
    assert {p["name"] for p in as_user[0]["products"]} <= {p["name"] for p in as_admin[0]["products"]}


def test_login_and_password_in_query_authenticate(api):
    (cls,) = _classification(api, 1, params={"login": USER_LOGIN, "password": USER_PASSWORD})
    assert cls["name"] == "Unclassified"


def test_token_without_userid_prefix_is_anonymous(api):
    assert _classification(api, 1, params={"token": "nonsense"}) == _classification(api, 1)


def test_path_id_and_same_query_id_are_deduplicated(api):
    assert [c["id"] for c in _classification(api, 1, params={"ids": 1})] == [1]
    assert [c["id"] for c in _classification(api, 1, params={"ids": [1, 1]})] == [1]
    assert [c["id"] for c in _classification(api, "Unclassified", params={"ids": 1, "names": "Unclassified"})] == [1]


# ---------------------------------------------------------------- include_fields / exclude_fields
def test_include_fields_scalar(api):
    assert _classification(api, 1, params={"include_fields": "name"}) == [{"name": "Unclassified"}]


def test_include_fields_products_subfields(api):
    (cls,) = _classification(api, 1, params={"include_fields": "products.name,sort_key"})
    assert set(cls) == {"products", "sort_key"} and cls["sort_key"] == 0
    assert all(set(p) == {"name"} for p in cls["products"])
    (cls,) = _classification(api, 1, params={"include_fields": "id,products.id"})
    assert set(cls) == {"id", "products"} and all(set(p) == {"id"} for p in cls["products"])


def test_include_fields_products_keeps_whole_sub_objects(api):
    (cls,) = _classification(api, 1, params={"include_fields": "products"})
    assert set(cls) == {"products"} and all(set(p) == PRODUCT_KEYS for p in cls["products"])


def test_exclude_fields(api):
    (cls,) = _classification(api, 1, params={"exclude_fields": "products"})
    assert set(cls) == CLASSIFICATION_KEYS - {"products"}
    (cls,) = _classification(api, 1, params={"exclude_fields": "products.description"})
    assert set(cls) == CLASSIFICATION_KEYS and all(set(p) == {"id", "name"} for p in cls["products"])
    (cls,) = _classification(api, 1, params={"include_fields": "_all", "exclude_fields": "products"})
    assert set(cls) == CLASSIFICATION_KEYS - {"products"}
    assert _classification(api, 1, params={"exclude_fields": "_all"}) == [{}]


def test_include_all_equals_default(api, user_token):
    assert _classification(api, 1, token=user_token, params={"include_fields": "_all"}) == \
        _classification(api, 1, token=user_token)


# ---------------------------------------------------------------- error cases
def test_unknown_id(api):
    expect_error(api.get("/classification/999999"), 404, 51, "There is no classification with the id '999999'.")


def test_unknown_name(api):
    expect_error(api.get("/classification/nosuchclass"), 404, 51, "There is no classification named 'nosuchclass'.")


def test_id_zero_and_id_above_int32_do_not_exist(api):
    expect_error(api.get("/classification/0"), 404, 51, "There is no classification with the id '0'.")
    expect_error(api.get("/classification/99999999999"), 404, 51,
                 "There is no classification with the id '99999999999'.")


def test_non_numeric_ids_param(api):
    expect_error(api.get("/classification/1", params={"ids": "abc"}), 400, 52, NUMERIC_MSG)


def test_params_are_not_csv_split(api):
    expect_error(api.get("/classification/1", params={"ids": "1,2"}), 400, 52, NUMERIC_MSG)
    expect_error(api.get("/classification/a,b"), 404, 51, "There is no classification named 'a,b'.")


def test_empty_name_is_not_specified(api):
    expect_error(api.get("/classification/1", params={"names": ""}), 400, 50,
                 "You must select/enter a classification.")


def test_one_missing_classification_fails_the_whole_call(api):
    expect_error(api.get("/classification/Unclassified", params={"ids": 999999}), 404, 51,
                 "There is no classification with the id '999999'.")


def test_invalid_token_with_userid_prefix(api):
    expect_error(api.get("/classification/1", params={"token": "1-abcdefghij"}), 400, 32000,
                 "The cookies or token provide were not valid or have expired.")


def test_bad_login_or_password(api):
    expect_error(api.get("/classification/1", params={"login": USER_LOGIN, "password": "wrong"}), 401, 300,
                 "The login or password you entered is not valid.")


def test_resource_only_supports_get(api, admin_token):
    expect_error(api.post("/classification/1", token=admin_token, json={}), 404, 32614,
                 "A REST API resource was not found for 'POST /classification/1'.")
    expect_error(api.put("/classification/1", token=admin_token, json={}), 404, 32614,
                 "A REST API resource was not found for 'PUT /classification/1'.")
    expect_error(api.get("/classification"), 404, 32614,
                 "A REST API resource was not found for 'GET /classification'.")


# ---------------------------------------------------------------- fixtures created in the database
def test_extra_classification_exact_shape(api, extra_classification):
    c = extra_classification
    assert normalize(_classification(api, c["id"])) == [{
        "id": ID, "name": c["name"], "description": "parity suite classification", "sort_key": 7,
        "products": [{"id": ID, "name": c["open"], "description": "visible to everyone"}],
    }]


def test_restricted_product_hidden_from_anonymous_and_users(api, extra_classification, user_token):
    c = extra_classification
    for kw in ({}, {"token": user_token}):
        (cls,) = _classification(api, c["name"], **kw)
        assert [p["name"] for p in cls["products"]] == [c["open"]]


def test_editclassifications_user_sees_restricted_products(api, extra_classification, admin_token):
    c = extra_classification
    (cls,) = _classification(api, c["id"], token=admin_token)
    assert normalize(cls["products"]) == [
        {"id": ID, "name": c["open"], "description": "visible to everyone"},
        {"id": ID, "name": c["secret"], "description": "members only"},
    ]


def test_classification_without_selectable_products_is_silently_dropped(api, extra_classification, user_token):
    """get_selectable_classifications: non-editclassifications users only see classifications that hold
    at least one product they can select; the classification is filtered out, not reported as missing."""
    c = extra_classification
    for kw in ({}, {"token": user_token}):
        assert _classification(api, c["empty_id"], **kw) == []
        assert _classification(api, c["empty"], **kw) == []
        assert [x["name"] for x in _classification(api, 1, params={"ids": c["empty_id"]}, **kw)] == ["Unclassified"]


def test_editclassifications_user_sees_empty_classification(api, extra_classification, admin_token):
    c = extra_classification
    assert normalize(_classification(api, c["empty"], token=admin_token)) == [{
        "id": ID, "name": c["empty"], "description": "no products at all", "sort_key": 9, "products": []}]


def test_names_come_before_ids_and_duplicates_collapse(api, extra_classification):
    c = extra_classification
    by = lambda **p: [x["name"] for x in _classification(api, 1, params={"include_fields": "name", **p})]
    assert by(names=c["name"]) == [c["name"], "Unclassified"]
    assert by(ids=c["id"]) == ["Unclassified", c["name"]]
    assert by(ids=[c["id"], 1], names=["Unclassified", c["name"]]) == ["Unclassified", c["name"]]
    # path values come first, then the query string ones (Bugzilla::WebService::Server::REST)
    assert [x["name"] for x in _classification(api, "Unclassified", params={"names": c["name"], "include_fields": "name"})] == \
        ["Unclassified", c["name"]]
    assert [x["name"] for x in _classification(api, c["id"], params={"ids": 1, "include_fields": "name"})] == \
        [c["name"], "Unclassified"]
    assert [x["name"] for x in _classification(api, c["name"], params={"ids": 1, "include_fields": "name"})] == \
        [c["name"], "Unclassified"]


def test_classification_not_enabled(api, classification_disabled, user_token, admin_token):
    def disabled():
        return api.get("/classification/1").status_code == 400
    assert _wait_for(disabled), "useclassification=0 not picked up by the backend"
    expect_error(api.get("/classification/1"), 400, 900, "Sorry, classification is not enabled.")
    expect_error(api.get("/classification/Unclassified", token=user_token), 400, 900,
                 "Sorry, classification is not enabled.")
    # editclassifications members keep access and see every product of the classification
    (cls,) = _classification(api, 1, token=admin_token)
    assert cls["name"] == "Unclassified" and SEED_PRODUCTS <= {p["name"] for p in cls["products"]}


def test_classification_enabled_again(api):
    """Runs after the toggle test: make sure the flag was restored for the rest of the suite."""
    assert _wait_for(lambda: api.get("/classification/1").status_code == 200)
