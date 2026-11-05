"""Bug.get and Bug.search (GET /bug, GET /bug/{id_or_alias})."""
from parity.conftest import expect_error, ok
from parity.normalize import DATETIME, ID, normalize, shape

DEFAULT_BUG_FIELDS = {
    "id", "alias", "summary", "status", "resolution", "product", "component", "version",
    "platform", "op_sys", "priority", "severity", "creator", "creator_detail", "assigned_to",
    "assigned_to_detail", "qa_contact", "cc", "cc_detail", "creation_time", "last_change_time",
    "is_open", "is_confirmed", "is_creator_accessible", "is_cc_accessible", "blocks", "depends_on",
    "dupe_of", "keywords", "groups", "flags", "see_also", "target_milestone", "whiteboard",
    "url", "classification", "deadline", "actual_time", "estimated_time", "remaining_time",
}


def test_get_by_alias_default_fields(api, seed):
    body = ok(api.get("/bug/seed-01"))
    assert set(body) == {"bugs", "faults"} and body["faults"] == [] and len(body["bugs"]) == 1
    bug = body["bugs"][0]
    assert set(bug) >= DEFAULT_BUG_FIELDS - {"actual_time", "estimated_time", "remaining_time", "deadline"}, \
        sorted(DEFAULT_BUG_FIELDS - set(bug))
    stable = {k: bug[k] for k in ("alias", "summary", "status", "resolution", "product", "component",
                                  "version", "platform", "op_sys", "priority", "severity", "creator",
                                  "assigned_to", "qa_contact", "is_open", "is_confirmed",
                                  "depends_on", "dupe_of", "keywords", "groups", "flags", "see_also",
                                  "target_milestone", "whiteboard", "url", "classification")}
    assert stable == {
        "alias": ["seed-01"],
        "summary": "CSV import drops rows with non-ASCII names",
        "status": "CONFIRMED", "resolution": "",
        "product": "Orbit CRM", "component": "Contacts", "version": "2.4",
        "platform": "All", "op_sys": "All", "priority": "High", "severity": "major",
        "creator": "alice.ng@bugzilla.test", "assigned_to": "chen.wei@bugzilla.test", "qa_contact": "",
        "is_open": True, "is_confirmed": True,
        "depends_on": [], "dupe_of": None, "keywords": [], "groups": [], "flags": [], "see_also": [],
        "target_milestone": "---", "whiteboard": "", "url": "", "classification": "Unclassified",
    }
    assert bug["blocks"] == [seed["seed-03"]]
    assert "bruno.costa@bugzilla.test" in bug["cc"] and bug["cc"] == sorted(bug["cc"])
    assert normalize(bug["creator_detail"]) == {"id": ID, "name": "alice.ng@bugzilla.test",
                                                "email": "alice.ng@bugzilla.test", "real_name": "Alice Ng"}
    assert {"id": ID, "name": "bruno.costa@bugzilla.test", "email": "bruno.costa@bugzilla.test",
            "real_name": "Bruno Costa"} in normalize(bug["cc_detail"])
    assert [d["name"] for d in bug["cc_detail"]] == bug["cc"]
    assert normalize({"t": bug["creation_time"], "u": bug["last_change_time"]},
                     id_keys=set()) == {"t": bug["creation_time"], "u": bug["last_change_time"]}
    assert normalize({"creation_time": bug["creation_time"]}) == {"creation_time": DATETIME}


def test_get_by_id_matches_get_by_alias(api, seed):
    a = ok(api.get("/bug/seed-01"))
    b = ok(api.get(f"/bug/{seed['seed-01']}"))
    assert a == b


def test_get_include_fields_adds_faults_key(api, seed):
    body = ok(api.get("/bug/seed-01", params={"include_fields": "id,summary"}))
    assert normalize(body) == {"faults": [], "bugs": [{"id": ID, "summary": "CSV import drops rows with non-ASCII names"}]}


def test_get_include_fields_detail_is_implied(api, seed):
    bug = ok(api.get("/bug/seed-01", params={"include_fields": "id,cc,creator"}))["bugs"][0]
    assert set(bug) == {"id", "cc", "cc_detail", "creator", "creator_detail"}


def test_get_exclude_fields(api, seed):
    bug = ok(api.get("/bug/seed-01", params={"exclude_fields": "cc,cc_detail,creator_detail,assigned_to_detail"}))["bugs"][0]
    assert "cc" not in bug and "cc_detail" not in bug and "summary" in bug


def test_get_multiple_ids_ordered(api, seed):
    body = ok(api.get("/bug", params={"id": f"{seed['seed-01']},{seed['seed-05']}", "include_fields": "id,status,alias"}))
    assert body == {"bugs": [{"id": seed["seed-01"], "status": "CONFIRMED", "alias": ["seed-01"]},
                             {"id": seed["seed-05"], "status": "CONFIRMED", "alias": ["seed-05"]}]}


def test_get_resolved_duplicate(api, seed):
    bug = ok(api.get("/bug/seed-02", params={"include_fields": "status,resolution,dupe_of,is_open"}))["bugs"][0]
    assert bug == {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": seed["seed-01"], "is_open": False}


def test_get_nonexistent_id(api):
    expect_error(api.get("/bug/999999"), 404, 101, "Bug #999999 does not exist.")


def test_get_bad_alias(api):
    expect_error(api.get("/bug/not-an-id"), 400, 100, "'not-an-id' is not a valid bug number nor an alias to a bug.")


def test_get_history_nonexistent(api):
    expect_error(api.get("/bug/999999/history"), 404, 101)


def test_search_by_product(api, seed):
    body = ok(api.get("/bug", params={"product": "Orbit CRM", "include_fields": "id,product"}))
    assert len(body["bugs"]) >= 10
    assert {b["product"] for b in body["bugs"]} == {"Orbit CRM"}
    ids = [b["id"] for b in body["bugs"]]
    assert ids == sorted(ids)


def test_search_two_products(api, seed):
    one = ok(api.get("/bug", params={"product": "Orbit CRM", "include_fields": "id"}))["bugs"]
    two = ok(api.get("/bug", params={"product": ["Orbit CRM", "Lighthouse Billing"], "include_fields": "id"}))["bugs"]
    assert len(two) >= len(one) + 10


def test_search_by_alias(api, seed):
    body = ok(api.get("/bug", params={"alias": "seed-05", "include_fields": "id,alias,status"}))
    assert body == {"bugs": [{"id": seed["seed-05"], "alias": ["seed-05"], "status": "CONFIRMED"}]}


def test_search_by_status_and_resolution(api, seed):
    body = ok(api.get("/bug", params={"status": "RESOLVED", "resolution": "WONTFIX",
                                      "include_fields": "alias,resolution"}))
    assert {"alias": ["seed-10"], "resolution": "WONTFIX"} in body["bugs"]
    assert all(b["resolution"] == "WONTFIX" for b in body["bugs"])


def test_search_summary_substring(api, seed):
    body = ok(api.get("/bug", params={"summary": "invoice", "product": "Lighthouse Billing",
                                      "include_fields": "summary"}))
    assert len(body["bugs"]) == 5
    assert all("invoice" in b["summary"].lower() for b in body["bugs"])


def test_search_assigned_to(api, seed):
    body = ok(api.get("/bug", params={"assigned_to": "bruno.costa@bugzilla.test", "include_fields": "assigned_to"}))
    assert len(body["bugs"]) >= 6
    assert {b["assigned_to"] for b in body["bugs"]} == {"bruno.costa@bugzilla.test"}


def test_search_limit_offset(api, seed):
    full = ok(api.get("/bug", params={"product": "Orbit CRM", "include_fields": "id"}))["bugs"]
    page = ok(api.get("/bug", params={"product": "Orbit CRM", "limit": 2, "offset": 2, "include_fields": "id"}))["bugs"]
    assert page == full[2:4]


def test_search_unknown_product_is_empty(api):
    assert ok(api.get("/bug", params={"product": "No Such Product", "include_fields": "id"})) == {"bugs": []}


def test_search_unknown_id_is_empty_not_error(api):
    assert ok(api.get("/bug", params={"id": 999999, "include_fields": "id"})) == {"bugs": []}


def test_search_non_numeric_limit_is_ignored(api, seed):
    a = ok(api.get("/bug", params={"product": "Orbit CRM", "include_fields": "id"}))
    b = ok(api.get("/bug", params={"product": "Orbit CRM", "limit": "abc", "include_fields": "id"}))
    assert a == b


def test_search_default_fields_shape(api, seed):
    bug = ok(api.get("/bug", params={"alias": "seed-01"}))["bugs"][0]
    single = ok(api.get("/bug/seed-01"))["bugs"][0]
    assert shape(bug) == shape(single)


def test_bug_fields_types(api, seed):
    bug = ok(api.get("/bug/seed-01"))["bugs"][0]
    assert isinstance(bug["id"], int)
    assert isinstance(bug["is_open"], bool)
    assert isinstance(bug["creator_detail"]["id"], int)
    assert bug["dupe_of"] is None
