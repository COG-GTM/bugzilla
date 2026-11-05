"""Bug.create and Bug.update (POST /bug, PUT /bug/{id})."""
from parity.conftest import SEED_COMPONENT, SEED_PRODUCT, SEED_VERSION, expect_error, ok, unique
from parity.normalize import DATETIME, ID, normalize

VALID = {
    "product": SEED_PRODUCT, "component": SEED_COMPONENT, "version": SEED_VERSION,
    "description": "desc", "op_sys": "All", "platform": "All", "priority": "Normal", "severity": "normal",
}


def body(**over):
    b = dict(VALID, summary=unique("create"))
    b.update(over)
    return b


def test_create_returns_200_with_id(api, admin_token):
    # Perl's REST resource spells the key `status_code` (not `success_code`), so POST /bug answers 200, not 201.
    r = api.post("/bug", token=admin_token, json=body(summary="parity create roundtrip"))
    assert normalize(ok(r, 200)) == {"id": ID}
    bug = ok(api.get(f"/bug/{r.json()['id']}"))["bugs"][0]
    assert {k: bug[k] for k in ("summary", "product", "component", "version", "op_sys", "platform",
                                 "priority", "severity", "status", "resolution", "creator", "assigned_to",
                                 "is_open", "alias", "cc")} == {
        "summary": "parity create roundtrip", "product": SEED_PRODUCT, "component": SEED_COMPONENT,
        "version": SEED_VERSION, "op_sys": "All", "platform": "All", "priority": "Normal", "severity": "normal",
        "status": "CONFIRMED", "resolution": "", "creator": "admin@bugzilla.test",
        "assigned_to": "alice.ng@bugzilla.test", "is_open": True, "alias": [], "cc": [],
    }
    comments = ok(api.get(f"/bug/{bug['id']}/comment"))["bugs"][str(bug["id"])]["comments"]
    assert len(comments) == 1 and comments[0]["text"] == "desc" and comments[0]["count"] == 0


def test_create_with_alias_cc_and_assignee(api, admin_token, new_bug):
    alias = unique("alias").replace(" ", "-")
    bug_id = new_bug(alias=[alias], cc=["bruno.costa@bugzilla.test", "chen.wei@bugzilla.test"],
                     assigned_to="dana.okafor@bugzilla.test", priority="Highest", severity="blocker")
    bug = ok(api.get(f"/bug/{alias}", params={"include_fields": "id,alias,cc,assigned_to,priority,severity"}))["bugs"][0]
    assert set(bug) == {"id", "alias", "cc", "cc_detail", "assigned_to", "assigned_to_detail", "priority", "severity"}
    del bug["cc_detail"], bug["assigned_to_detail"]
    assert bug == {"id": bug_id, "alias": [alias], "cc": ["bruno.costa@bugzilla.test", "chen.wei@bugzilla.test"],
                   "assigned_to": "dana.okafor@bugzilla.test", "priority": "Highest", "severity": "blocker"}


def test_create_as_non_admin_user(api, user_token):
    r = api.post("/bug", token=user_token, json=body())
    bug_id = ok(r)["id"]
    assert ok(api.get(f"/bug/{bug_id}", params={"include_fields": "creator"}))["bugs"][0]["creator"] == "alice.ng@bugzilla.test"


def test_create_requires_login(api):
    expect_error(api.post("/bug", json=body()), 401, 410)


def test_create_missing_summary(api, admin_token):
    b = body(); del b["summary"]
    expect_error(api.post("/bug", token=admin_token, json=b), 400, 107, "You must enter a summary for this bug.")


def test_create_missing_op_sys(api, admin_token):
    b = body(); del b["op_sys"]
    expect_error(api.post("/bug", token=admin_token, json=b), 400, 50, "You must select/enter a OS.")


def test_create_missing_platform(api, admin_token):
    b = body(); del b["platform"]
    expect_error(api.post("/bug", token=admin_token, json=b), 400, 50, "You must select/enter a Hardware.")


def test_create_invalid_priority(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(priority="Bogus")), 404, 51,
                 "There is no Priority named 'Bogus'.")


def test_create_invalid_severity(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(severity="bogus")), 404, 51,
                 "There is no Severity named 'bogus'.")


def test_create_unknown_component(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(component="Nope")), 404, 51,
                 f"There is no component named 'Nope' in the '{SEED_PRODUCT}' product.")


def test_create_unknown_product(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(product="Nope")), 401, 106,
                 "either the product Nope does not exist or you aren't authorized")


def test_create_unknown_version(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(version="9.9")), 404, 51,
                 f"There is no version named '9.9' in the '{SEED_PRODUCT}' product.")


def test_create_unknown_assignee(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(assigned_to="nobody@example.test")), 404, 51,
                 "There is no user named 'nobody@example.test'.")


def test_create_duplicate_alias(api, admin_token):
    expect_error(api.post("/bug", token=admin_token, json=body(alias=["seed-01"])), 400, 103)


def test_update_status_with_comment(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "IN_PROGRESS", "comment": {"body": "taking"}}))
    assert normalize(r) == {"bugs": [{"id": ID, "alias": [], "last_change_time": DATETIME,
                                      "changes": {"status": {"removed": "CONFIRMED", "added": "IN_PROGRESS"}}}]}
    bug = ok(api.get(f"/bug/{bug_id}", params={"include_fields": "status,is_open"}))["bugs"][0]
    assert bug == {"status": "IN_PROGRESS", "is_open": True}
    comments = ok(api.get(f"/bug/{bug_id}/comment"))["bugs"][str(bug_id)]["comments"]
    assert comments[-1]["text"] == "taking"


def test_update_multiple_fields(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"summary": "renamed", "priority": "High", "whiteboard": "wb"}))
    assert r["bugs"][0]["changes"] == {
        "summary": {"removed": r["bugs"][0]["changes"]["summary"]["removed"], "added": "renamed"},
        "priority": {"removed": "Normal", "added": "High"},
        "whiteboard": {"removed": "", "added": "wb"},
    }


def test_update_noop_reports_no_changes(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"priority": "Normal"}))
    assert normalize(r) == {"bugs": [{"id": ID, "alias": [], "last_change_time": DATETIME, "changes": {}}]}


def test_update_resolve_and_reopen(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "RESOLVED", "resolution": "FIXED"}))
    assert r["bugs"][0]["changes"] == {"status": {"removed": "CONFIRMED", "added": "RESOLVED"},
                                       "resolution": {"removed": "", "added": "FIXED"}}
    assert ok(api.get(f"/bug/{bug_id}", params={"include_fields": "is_open"}))["bugs"][0] == {"is_open": False}
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "CONFIRMED"}))
    assert r["bugs"][0]["changes"] == {"status": {"removed": "RESOLVED", "added": "CONFIRMED"},
                                       "resolution": {"removed": "FIXED", "added": ""}}


def test_update_cc_add_remove(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"cc": {"add": ["bruno.costa@bugzilla.test"]}}))
    assert r["bugs"][0]["changes"] == {"cc": {"removed": "", "added": "bruno.costa@bugzilla.test"}}
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"cc": {"remove": ["bruno.costa@bugzilla.test"]}}))
    assert r["bugs"][0]["changes"] == {"cc": {"removed": "bruno.costa@bugzilla.test", "added": ""}}


def test_update_dependencies(api, admin_token, new_bug, seed):
    bug_id = new_bug()
    r = ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"depends_on": {"add": [seed["seed-05"]]}}))
    assert r["bugs"][0]["changes"] == {"depends_on": {"removed": "", "added": str(seed["seed-05"])}}
    other = ok(api.get("/bug/seed-05", params={"include_fields": "blocks"}))["bugs"][0]
    assert bug_id in other["blocks"]


def test_update_mark_duplicate(api, admin_token, user_token, new_bug):
    original = new_bug()
    dupe = ok(api.post("/bug", token=user_token, json=body()))["id"]
    r = ok(api.put(f"/bug/{dupe}", token=admin_token, json={"dupe_of": original}))
    assert r["bugs"][0]["changes"] == {"status": {"removed": "CONFIRMED", "added": "RESOLVED"},
                                       "resolution": {"removed": "", "added": "DUPLICATE"},
                                       "dupe_of": {"removed": "", "added": str(original)}}
    # the duplicate's reporter is CC'd on the original
    orig = ok(api.get(f"/bug/{original}", params={"include_fields": "cc,dupe_of"}))["bugs"][0]
    assert orig["cc"] == ["alice.ng@bugzilla.test"] and orig["dupe_of"] is None
    assert ok(api.get(f"/bug/{dupe}", params={"include_fields": "dupe_of"}))["bugs"][0]["dupe_of"] == original


def test_update_requires_login(api, seed):
    expect_error(api.put("/bug/seed-01", json={"status": "IN_PROGRESS"}), 401, 410)


def test_update_nonexistent(api, admin_token):
    expect_error(api.put("/bug/999999", token=admin_token, json={"status": "IN_PROGRESS"}), 404, 101)


def test_update_invalid_status(api, admin_token, new_bug):
    expect_error(api.put(f"/bug/{new_bug()}", token=admin_token, json={"status": "BOGUS"}), 404, 51,
                 "There is no status named 'BOGUS'.")


def test_update_invalid_priority(api, admin_token, new_bug):
    expect_error(api.put(f"/bug/{new_bug()}", token=admin_token, json={"priority": "Bogus"}), 404, 51,
                 "There is no Priority named 'Bogus'.")


def test_update_resolved_requires_resolution(api, admin_token, new_bug):
    expect_error(api.put(f"/bug/{new_bug()}", token=admin_token, json={"status": "RESOLVED"}), 400, 121,
                 "A valid resolution is required to mark bugs as RESOLVED.")


def test_update_unknown_cc_user(api, admin_token, new_bug):
    expect_error(api.put(f"/bug/{new_bug()}", token=admin_token, json={"cc": {"add": ["nobody@example.test"]}}),
                 404, 51, "There is no user named 'nobody@example.test'.")


def test_update_unknown_dependency(api, admin_token, new_bug):
    expect_error(api.put(f"/bug/{new_bug()}", token=admin_token, json={"depends_on": {"add": [999999]}}), 404, 101)


def test_update_illegal_transition(api, admin_token, new_bug):
    bug_id = new_bug()
    ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "RESOLVED", "resolution": "FIXED"}))
    ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "VERIFIED"}))
    expect_error(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "RESOLVED", "resolution": "FIXED"}),
                 400, 123, "You are not allowed to change the bug status from VERIFIED to RESOLVED.")
