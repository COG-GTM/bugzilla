"""Contract tests for the ``Bug`` WebService module over REST."""

import base64
import datetime as dt
import time

import pytest

from conftest import ADMIN_EMAIL, Api, assert_error, assert_keys

BUG_KEYS = {
    "id", "alias", "summary", "status", "resolution", "product", "component", "version",
    "platform", "op_sys", "priority", "severity", "classification", "target_milestone",
    "creator", "creator_detail", "assigned_to", "assigned_to_detail", "qa_contact", "cc",
    "cc_detail", "keywords", "groups", "flags", "blocks", "depends_on", "dupe_of", "see_also",
    "url", "whiteboard", "deadline", "is_open", "is_confirmed", "is_cc_accessible",
    "is_creator_accessible", "creation_time", "last_change_time",
}
COMMENT_KEYS = {"id", "bug_id", "count", "creator", "time", "creation_time", "text", "is_private",
                "attachment_id", "tags"}
ATTACHMENT_META_KEYS = {"id", "bug_id", "file_name", "summary", "content_type", "size", "is_patch",
                        "is_private", "is_obsolete", "creator", "creation_time", "last_change_time", "flags"}
HELLO_B64 = base64.b64encode(b"hello").decode()


def get_bug(api: Api, bug_id, token=None, **params):
    resp = api.get(f"/bug/{bug_id}", params=params or None, token=token)
    assert resp.status == 200, resp.body
    return resp.body["bugs"][0]


def comments_of(api: Api, bug_id, token=None, **params):
    resp = api.get(f"/bug/{bug_id}/comment", params=params or None, token=token)
    assert resp.status == 200, resp.body
    return resp.body["bugs"][str(bug_id)]["comments"]


def attach(api: Api, bug_id, token, **overrides):
    body = {"data": HELLO_B64, "file_name": "hello.txt", "summary": "parity attachment",
            "content_type": "text/plain"}
    body.update(overrides)
    return api.post(f"/bug/{bug_id}/attachment", {k: v for k, v in body.items() if v is not None}, token=token)


# --------------------------------------------------------------------------- fields

def test_fields_lists_every_bug_field(api: Api):
    resp = api.get("/field/bug")
    assert resp.status == 200
    names = {f["name"] for f in resp.body["fields"]}
    assert {"priority", "bug_severity", "bug_status", "resolution", "product", "component",
            "version", "short_desc", "assigned_to"} <= names
    for field in resp.body["fields"]:
        assert_keys(field, "id", "name", "display_name", "type", "is_custom", "is_mandatory",
                    "is_on_bug_entry", "visibility_field", "visibility_values")


def test_field_by_name(api: Api, snapshot):
    resp = api.get("/field/bug/priority")
    assert resp.status == 200
    (field,) = resp.body["fields"]
    assert field["name"] == "priority" and field["type"] == 2
    assert [v["name"] for v in field["values"]] == ["Highest", "High", "Normal", "Low", "Lowest", "---"]
    snapshot(resp)


def test_field_unknown_name(api: Api, snapshot):
    resp = api.get("/field/bug/no_such_field")
    assert_error(resp, 404, 51)
    snapshot(resp)


def test_legal_values_priority(api: Api, snapshot):
    resp = api.get("/field/bug/priority/values")
    assert resp.status == 200
    assert resp.body == {"values": ["Highest", "High", "Normal", "Low", "Lowest", "---"]}
    snapshot(resp)


def test_legal_values_unknown_field(api: Api, snapshot):
    resp = api.get("/field/bug/no_such_field/values")
    assert_error(resp, 400, 108)
    snapshot(resp)


def test_legal_values_version_requires_product(api: Api, snapshot):
    resp = api.get("/field/bug/version/values")
    assert_error(resp, 400, 50)
    snapshot(resp)


def test_legal_values_version_for_product(api: Api, fixture_api: Api, admin: str, product: dict, snapshot):
    found = fixture_api.get("/product", params={"names": product["product"], "include_fields": "id"}, token=admin)
    product_id = found.body["products"][0]["id"]
    resp = api.get(f"/field/bug/version/{product_id}/values")
    assert resp.status == 200
    assert resp.body == {"values": [product["version"]]}
    snapshot(resp)


# --------------------------------------------------------------------------- create

def test_create_minimal(api: Api, admin: str, bug_payload, run_id: str, snapshot):
    resp = api.post("/bug", bug_payload(summary=f"Minimal bug {run_id}"), token=admin)
    assert resp.status == 200, resp.body  # Perl quirk: resource declares status_code, not success_code
    assert set(resp.body) == {"id"} and isinstance(resp.body["id"], int)
    snapshot(resp, "created")

    bug_resp = api.get(f"/bug/{resp.body['id']}")
    bug = bug_resp.body["bugs"][0]
    assert set(bug) == BUG_KEYS
    assert bug["summary"] == f"Minimal bug {run_id}"
    assert bug["status"] == "CONFIRMED" and bug["resolution"] == ""
    assert bug["is_open"] is True and bug["is_confirmed"] is True
    assert bug["creator"] == ADMIN_EMAIL and bug["assigned_to"] == ADMIN_EMAIL
    assert bug["priority"] == "Normal" and bug["severity"] == "normal"
    assert bug["alias"] == [] and bug["cc"] == [] and bug["depends_on"] == [] and bug["blocks"] == []
    assert bug["dupe_of"] is None and bug["deadline"] is None
    assert bug["creation_time"] == bug["last_change_time"]
    snapshot(bug_resp, "get")


def test_create_full(api: Api, admin: str, user: dict, make_bug, bug_payload, run_id: str, snapshot):
    blocker = make_bug(summary=f"Blocker {run_id}")
    blocked = make_bug(summary=f"Blocked {run_id}")
    alias = f"{run_id}-full"
    resp = api.post("/bug", bug_payload(
        summary=f"Full bug {run_id}", alias=alias, assigned_to=user["email"], cc=[user["email"]],
        priority="High", severity="major", depends_on=[blocker], blocks=[blocked],
        whiteboard="parity", url="https://example.test/parity", target_milestone="---",
        description="Full description.",
    ), token=admin)
    assert resp.status == 200 and not resp.error, resp.body
    bug_id = resp.body["id"]

    bug_resp = api.get(f"/bug/{alias}")
    bug = bug_resp.body["bugs"][0]
    assert bug["id"] == bug_id and bug["alias"] == [alias]
    assert bug["assigned_to"] == user["email"] and bug["assigned_to_detail"]["real_name"] == user["name"]
    assert bug["cc"] == [user["email"]] and bug["cc_detail"][0]["email"] == user["email"]
    assert bug["depends_on"] == [blocker] and bug["blocks"] == [blocked]
    assert bug["priority"] == "High" and bug["severity"] == "major"
    assert bug["whiteboard"] == "parity" and bug["url"] == "https://example.test/parity"
    snapshot(bug_resp, "get")

    assert get_bug(api, blocker)["blocks"] == [bug_id]
    assert get_bug(api, blocked)["depends_on"] == [bug_id]
    assert comments_of(api, bug_id)[0]["text"] == "Full description."


def test_create_as_plain_user(api: Api, user: dict, bug_payload, run_id: str, snapshot):
    resp = api.post("/bug", bug_payload(summary=f"User bug {run_id}"), token=user["token"])
    assert resp.status == 200 and not resp.error, resp.body
    bug_resp = api.get(f"/bug/{resp.body['id']}", token=user["token"])
    bug = bug_resp.body["bugs"][0]
    assert bug["creator"] == user["email"]
    assert isinstance(bug["is_confirmed"], bool)
    snapshot(bug_resp)


def test_create_requires_login(api: Api, bug_payload, snapshot):
    resp = api.post("/bug", bug_payload())
    assert_error(resp, 401, 410)
    snapshot(resp)


@pytest.mark.parametrize(
    ("label", "overrides", "http_status", "code"),
    [
        ("missing_product", {"product": None}, 400, 50),
        ("unknown_product", {"product": "No Such Product"}, 401, 106),
        ("unknown_component", {"component": "No Such Component"}, 404, 51),
        ("missing_summary", {"summary": None}, 400, 107),
        ("empty_summary", {"summary": ""}, 400, 107),
        ("unknown_version", {"version": "99.99"}, 404, 51),
        ("unknown_priority", {"priority": "Bogus"}, 404, 51),
        ("unknown_severity", {"severity": "bogus"}, 404, 51),
        ("unknown_op_sys", {"op_sys": "Bogus OS"}, 404, 51),
        ("missing_op_sys", {"op_sys": None}, 400, 50),
        ("missing_platform", {"platform": None}, 400, 50),
        ("unknown_assignee", {"assigned_to": "nobody@bugzilla.test"}, 404, 51),
        ("unknown_cc", {"cc": ["nobody@bugzilla.test"]}, 401, 504),
        ("illegal_initial_status", {"status": "VERIFIED"}, 400, 123),
        ("numeric_alias", {"alias": "12345"}, 400, 103),
        ("unknown_dependency", {"depends_on": [99999999]}, 404, 101),
        ("unknown_milestone", {"target_milestone": "no-such-milestone"}, 404, 51),
    ],
)
def test_create_invalid(api: Api, admin: str, bug_payload, snapshot, label, overrides, http_status, code):
    resp = api.post("/bug", bug_payload(**overrides), token=admin)
    assert_error(resp, http_status, code)
    snapshot(resp)


def test_create_alias_already_taken(api: Api, admin: str, make_bug, bug_payload, run_id: str, snapshot):
    alias = f"{run_id}-taken"
    make_bug(alias=alias)
    resp = api.post("/bug", bug_payload(alias=alias), token=admin)
    assert_error(resp, 400, 103)
    snapshot(resp)


# --------------------------------------------------------------------------- get

def test_get_by_alias(api: Api, make_bug, run_id: str):
    alias = f"{run_id}-getalias"
    bug_id = make_bug(alias=alias)
    assert get_bug(api, alias)["id"] == bug_id


def test_get_include_fields(api: Api, make_bug, run_id: str, snapshot):
    bug_id = make_bug(summary=f"Include fields {run_id}")
    resp = api.get(f"/bug/{bug_id}", params={"include_fields": "id,summary,status"})
    assert resp.status == 200
    assert resp.body["faults"] == []
    assert resp.body["bugs"] == [{"id": bug_id, "summary": f"Include fields {run_id}", "status": "CONFIRMED"}]
    snapshot(resp)


def test_get_exclude_fields(api: Api, make_bug):
    bug_id = make_bug()
    bug = get_bug(api, bug_id, exclude_fields="cc,cc_detail,flags,groups,see_also")
    assert set(bug) == BUG_KEYS - {"cc", "cc_detail", "flags", "groups", "see_also"}


def test_get_exclude_wins_over_include(api: Api, make_bug, run_id: str, snapshot):
    bug_id = make_bug(summary=f"Exclude wins {run_id}")
    resp = api.get(f"/bug/{bug_id}", params={"include_fields": "id,cc,summary", "exclude_fields": "cc"})
    assert resp.status == 200
    assert resp.body["bugs"] == [{"id": bug_id, "summary": f"Exclude wins {run_id}"}]
    snapshot(resp)


def test_get_unknown_include_field_is_ignored(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.get(f"/bug/{bug_id}", params={"include_fields": "id,no_such_field"})
    assert resp.status == 200
    assert resp.body == {"faults": [], "bugs": [{"id": bug_id}]}
    snapshot(resp)


def test_get_default_field_group(api: Api, make_bug):
    bug_id = make_bug()
    assert set(get_bug(api, bug_id, include_fields="_default")) == BUG_KEYS


def test_get_unknown_id(api: Api, snapshot):
    resp = api.get("/bug/99999999")
    assert_error(resp, 404, 101)
    snapshot(resp)


def test_get_unknown_alias(api: Api, snapshot):
    resp = api.get("/bug/no-such-alias")
    assert_error(resp, 400, 100)
    snapshot(resp)


def test_get_alias_with_space(api: Api, snapshot):
    resp = api.get("/bug/not%20an%20alias")
    assert_error(resp, 400, 100)
    snapshot(resp)


# --------------------------------------------------------------------------- search

def test_search_by_summary(api: Api, make_bug, run_id: str, snapshot):
    token = f"needle-{run_id}"
    first = make_bug(summary=f"Search {token} one")
    second = make_bug(summary=f"Search {token} two")
    resp = api.get("/bug", params={"summary": token, "include_fields": "id,summary"})
    assert resp.status == 200
    assert sorted(b["id"] for b in resp.body["bugs"]) == sorted([first, second])
    snapshot(resp)


def test_search_by_alias(api: Api, make_bug, run_id: str):
    alias = f"{run_id}-searchalias"
    bug_id = make_bug(alias=alias)
    resp = api.get("/bug", params={"alias": alias, "include_fields": "id,alias"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id, "alias": [alias]}]}


def test_search_by_id(api: Api, make_bug):
    bug_id = make_bug()
    resp = api.get("/bug", params={"id": bug_id, "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id}]}


def test_search_quicksearch_alias(api: Api, make_bug, run_id: str):
    alias = f"{run_id}-quick"
    bug_id = make_bug(alias=alias)
    resp = api.get("/bug", params={"quicksearch": f"ALIAS:{alias}", "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id}]}


def test_search_by_product_and_status(api: Api, make_bug, product: dict):
    bug_id = make_bug()
    resp = api.get("/bug", params={"product": product["product"], "status": "CONFIRMED",
                                   "include_fields": "id,product,status"})
    assert resp.status == 200
    assert bug_id in {b["id"] for b in resp.body["bugs"]}
    assert all(b["product"] == product["product"] and b["status"] == "CONFIRMED" for b in resp.body["bugs"])


def test_search_by_assigned_to(api: Api, make_bug, user: dict):
    bug_id = make_bug(assigned_to=user["email"])
    resp = api.get("/bug", params={"assigned_to": user["email"], "include_fields": "id,assigned_to"})
    assert resp.status == 200
    assert bug_id in {b["id"] for b in resp.body["bugs"]}
    assert all(b["assigned_to"] == user["email"] for b in resp.body["bugs"])


def test_search_by_creator(api: Api, make_bug, user: dict, run_id: str):
    bug_id = make_bug(token=user["token"], summary=f"Creator search {run_id}")
    resp = api.get("/bug", params={"creator": user["email"], "summary": run_id, "include_fields": "id,creator"})
    assert resp.status == 200
    assert {b["id"] for b in resp.body["bugs"]} >= {bug_id}
    assert all(b["creator"] == user["email"] for b in resp.body["bugs"])


def test_search_by_creation_time(api: Api, make_bug, run_id: str):
    token = f"created-{run_id}"
    bug_id = make_bug(summary=f"Search {token}")
    today = dt.datetime.now(dt.timezone.utc).date()
    resp = api.get("/bug", params={"summary": token, "creation_time": (today - dt.timedelta(days=1)).isoformat(),
                                   "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id}]}
    resp = api.get("/bug", params={"summary": token, "creation_time": (today + dt.timedelta(days=2)).isoformat(),
                                   "include_fields": "id"})
    assert resp.body == {"bugs": []}


def test_search_limit_and_offset(api: Api, make_bug, product: dict):
    for _ in range(3):
        make_bug()
    page = api.get("/bug", params={"product": product["product"], "limit": 2, "include_fields": "id"})
    assert page.status == 200 and len(page.body["bugs"]) == 2
    shifted = api.get("/bug", params={"product": product["product"], "limit": 1, "offset": 1, "include_fields": "id"})
    assert shifted.status == 200
    assert shifted.body["bugs"] == [page.body["bugs"][1]]


def test_search_include_fields_shapes_every_hit(api: Api, make_bug, product: dict):
    make_bug()
    resp = api.get("/bug", params={"product": product["product"], "include_fields": "id,alias"})
    assert resp.status == 200
    assert resp.body["bugs"] and all(set(b) == {"id", "alias"} for b in resp.body["bugs"])


def test_search_no_match(api: Api, snapshot):
    resp = api.get("/bug", params={"product": "No Such Product", "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": []}
    snapshot(resp)


def test_search_unknown_status_value_is_ignored(api: Api, make_bug, run_id: str):
    """Perl quirk: an invalid legal value is dropped from the search instead of rejected."""
    token = f"status-quirk-{run_id}"
    bug_id = make_bug(summary=f"Search {token}")
    resp = api.get("/bug", params={"summary": token, "status": "NO_SUCH_STATUS", "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id}]}


def test_search_non_numeric_limit_is_ignored(api: Api, make_bug, run_id: str):
    token = f"limit-quirk-{run_id}"
    bug_id = make_bug(summary=f"Search {token}")
    resp = api.get("/bug", params={"summary": token, "limit": "abc", "include_fields": "id"})
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id}]}


# --------------------------------------------------------------------------- update

def test_update_priority_and_severity(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"priority": "High", "severity": "major"}, token=admin)
    assert resp.status == 200, resp.body
    (change,) = resp.body["bugs"]
    assert set(change) == {"id", "alias", "last_change_time", "changes"}
    assert change["id"] == bug_id
    assert change["changes"] == {"priority": {"removed": "Normal", "added": "High"},
                                 "severity": {"removed": "normal", "added": "major"}}
    snapshot(resp)
    bug = get_bug(api, bug_id)
    assert (bug["priority"], bug["severity"]) == ("High", "major")
    assert bug["last_change_time"] == change["last_change_time"]


def test_update_noop_reports_no_changes(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"priority": "Normal"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {}
    snapshot(resp)


def test_update_unknown_field_is_ignored(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"no_such_field": "x"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {}
    snapshot(resp)


def test_update_summary(api: Api, admin: str, make_bug, run_id: str, snapshot):
    bug_id = make_bug(summary=f"Before {run_id}")
    resp = api.put(f"/bug/{bug_id}", {"summary": f"After {run_id}"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"summary": {"removed": f"Before {run_id}", "added": f"After {run_id}"}}
    snapshot(resp)


def test_update_assigned_to(api: Api, admin: str, user: dict, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"assigned_to": user["email"]}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"assigned_to": {"removed": ADMIN_EMAIL, "added": user["email"]}}
    snapshot(resp)
    assert get_bug(api, bug_id)["assigned_to"] == user["email"]


def test_update_set_alias(api: Api, admin: str, make_bug, run_id: str, snapshot):
    bug_id = make_bug()
    alias = f"{run_id}-setalias"
    resp = api.put(f"/bug/{bug_id}", {"alias": alias}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["alias"] == [alias]
    assert resp.body["bugs"][0]["changes"] == {"alias": {"removed": "", "added": alias}}
    snapshot(resp)
    assert get_bug(api, alias)["id"] == bug_id


def test_update_status_in_progress(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"status": "IN_PROGRESS"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"status": {"removed": "CONFIRMED", "added": "IN_PROGRESS"}}
    snapshot(resp)
    assert get_bug(api, bug_id)["is_open"] is True


def test_update_resolve_fixed_with_comment(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"status": "RESOLVED", "resolution": "FIXED",
                                      "comment": {"body": "Fixed in parity."}}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"status": {"removed": "CONFIRMED", "added": "RESOLVED"},
                                               "resolution": {"removed": "", "added": "FIXED"}}
    snapshot(resp)
    bug = get_bug(api, bug_id)
    assert bug["is_open"] is False and bug["resolution"] == "FIXED"
    assert comments_of(api, bug_id)[-1]["text"] == "Fixed in parity."


def test_update_reopen_clears_resolution(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    api.put(f"/bug/{bug_id}", {"status": "RESOLVED", "resolution": "FIXED"}, token=admin)
    resp = api.put(f"/bug/{bug_id}", {"status": "CONFIRMED"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"status": {"removed": "RESOLVED", "added": "CONFIRMED"},
                                               "resolution": {"removed": "FIXED", "added": ""}}
    snapshot(resp)


def test_update_verified_after_resolved(api: Api, admin: str, make_bug):
    bug_id = make_bug()
    api.put(f"/bug/{bug_id}", {"status": "RESOLVED", "resolution": "FIXED"}, token=admin)
    resp = api.put(f"/bug/{bug_id}", {"status": "VERIFIED"}, token=admin)
    assert resp.status == 200
    assert resp.body["bugs"][0]["changes"] == {"status": {"removed": "RESOLVED", "added": "VERIFIED"}}


def test_update_mark_duplicate(api: Api, admin: str, make_bug, snapshot):
    original = make_bug()
    dupe = make_bug()
    resp = api.put(f"/bug/{dupe}", {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": original}, token=admin)
    assert resp.status == 200, resp.body
    changes = resp.body["bugs"][0]["changes"]
    assert changes["status"] == {"removed": "CONFIRMED", "added": "RESOLVED"}
    assert changes["resolution"] == {"removed": "", "added": "DUPLICATE"}
    assert changes["dupe_of"] == {"removed": "", "added": str(original)}
    snapshot(resp)
    bug = get_bug(api, dupe)
    assert bug["dupe_of"] == original and bug["is_open"] is False
    assert comments_of(api, original)[-1]["text"].startswith("*** Bug ")


def test_update_cc_add_and_remove(api: Api, admin: str, user: dict, make_bug, snapshot):
    bug_id = make_bug()
    added = api.put(f"/bug/{bug_id}", {"cc": {"add": [user["email"]]}}, token=admin)
    assert added.status == 200
    assert added.body["bugs"][0]["changes"] == {"cc": {"removed": "", "added": user["email"]}}
    snapshot(added, "add")
    assert get_bug(api, bug_id)["cc"] == [user["email"]]

    removed = api.put(f"/bug/{bug_id}", {"cc": {"remove": [user["email"]]}}, token=admin)
    assert removed.body["bugs"][0]["changes"] == {"cc": {"removed": user["email"], "added": ""}}
    snapshot(removed, "remove")
    assert get_bug(api, bug_id)["cc"] == []


def test_update_depends_on_add(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    blocker = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"depends_on": {"add": [blocker]}}, token=admin)
    assert resp.status == 200, resp.body
    assert resp.body["bugs"][0]["changes"] == {"depends_on": {"removed": "", "added": str(blocker)}}
    snapshot(resp)
    assert get_bug(api, bug_id)["depends_on"] == [blocker]
    assert get_bug(api, blocker)["blocks"] == [bug_id]


def test_update_depends_on_set_and_remove(api: Api, admin: str, make_bug):
    bug_id = make_bug()
    a, b = make_bug(), make_bug()
    resp = api.put(f"/bug/{bug_id}", {"depends_on": {"set": [a, b]}}, token=admin)
    assert resp.status == 200
    assert sorted(get_bug(api, bug_id)["depends_on"]) == sorted([a, b])
    resp = api.put(f"/bug/{bug_id}", {"depends_on": {"remove": [a]}}, token=admin)
    assert resp.body["bugs"][0]["changes"] == {"depends_on": {"removed": str(a), "added": ""}}
    assert get_bug(api, bug_id)["depends_on"] == [b]


def test_update_depends_on_self(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"depends_on": {"add": [bug_id]}}, token=admin)
    assert_error(resp, 400, 116)
    snapshot(resp)


def test_update_dependency_loop(api: Api, admin: str, make_bug, snapshot):
    a, b = make_bug(), make_bug()
    assert api.put(f"/bug/{a}", {"depends_on": {"add": [b]}}, token=admin).status == 200
    resp = api.put(f"/bug/{b}", {"depends_on": {"add": [a]}}, token=admin)
    assert_error(resp, 400, 116)
    snapshot(resp)


@pytest.mark.parametrize(
    ("label", "body", "http_status", "code"),
    [
        ("resolved_without_resolution", {"status": "RESOLVED"}, 400, 121),
        ("illegal_transition", {"status": "VERIFIED"}, 400, 123),
        ("duplicate_without_dupe_of", {"status": "RESOLVED", "resolution": "DUPLICATE"}, 400, 119),
        ("empty_summary", {"summary": ""}, 400, 107),
        ("unknown_priority", {"priority": "Bogus"}, 404, 51),
        ("unknown_resolution", {"status": "RESOLVED", "resolution": "BOGUS"}, 404, 51),
        ("unknown_cc", {"cc": {"add": ["nobody@bugzilla.test"]}}, 404, 51),
        ("unknown_assignee", {"assigned_to": "nobody@bugzilla.test"}, 404, 51),
        ("unknown_dependency", {"depends_on": {"add": [99999999]}}, 404, 101),
    ],
)
def test_update_invalid(api: Api, admin: str, make_bug, snapshot, label, body, http_status, code):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", body, token=admin)
    assert_error(resp, http_status, code)
    snapshot(resp)


def test_update_requires_login(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"priority": "High"})
    assert_error(resp, 401, 410)
    snapshot(resp)


def test_update_unknown_bug(api: Api, admin: str, snapshot):
    resp = api.put("/bug/99999999", {"priority": "High"}, token=admin)
    assert_error(resp, 404, 101)
    snapshot(resp)


def test_update_as_plain_user(api: Api, user: dict, make_bug):
    bug_id = make_bug()
    resp = api.put(f"/bug/{bug_id}", {"priority": "Low"}, token=user["token"])
    assert resp.status == 200, resp.body
    assert resp.body["bugs"][0]["changes"] == {"priority": {"removed": "Normal", "added": "Low"}}


# --------------------------------------------------------------------------- comments

def test_comments_initial_description(api: Api, make_bug, run_id: str, snapshot):
    bug_id = make_bug(summary=f"Comments {run_id}", description="Initial description.")
    resp = api.get(f"/bug/{bug_id}/comment")
    assert resp.status == 200
    assert set(resp.body) == {"bugs", "comments"} and resp.body["comments"] == {}
    (comment,) = resp.body["bugs"][str(bug_id)]["comments"]
    assert set(comment) == COMMENT_KEYS
    assert comment["count"] == 0 and comment["text"] == "Initial description."
    assert comment["bug_id"] == bug_id and comment["creator"] == ADMIN_EMAIL
    assert comment["is_private"] is False and comment["attachment_id"] is None and comment["tags"] == []
    snapshot(resp)


def test_add_comment(api: Api, admin: str, user: dict, make_bug, run_id: str, snapshot):
    bug_id = make_bug(summary=f"Add comment {run_id}", description="Initial description.")
    resp = api.post(f"/bug/{bug_id}/comment", {"comment": "A public comment."}, token=user["token"])
    assert resp.status == 201, resp.body
    assert set(resp.body) == {"id"} and isinstance(resp.body["id"], int)
    snapshot(resp, "created")

    listing = api.get(f"/bug/{bug_id}/comment")
    comments = listing.body["bugs"][str(bug_id)]["comments"]
    assert [c["count"] for c in comments] == [0, 1]
    assert comments[1]["id"] == resp.body["id"]
    assert comments[1]["text"] == "A public comment." and comments[1]["creator"] == user["email"]
    snapshot(listing, "listing")
    assert get_bug(api, bug_id)["last_change_time"] == comments[1]["time"]


def test_comment_by_id(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    cid = api.post(f"/bug/{bug_id}/comment", {"comment": "Fetch me by id."}, token=admin).body["id"]
    resp = api.get(f"/bug/comment/{cid}")
    assert resp.status == 200
    assert resp.body["bugs"] == {}
    assert resp.body["comments"][str(cid)]["text"] == "Fetch me by id."
    assert resp.body["comments"][str(cid)]["bug_id"] == bug_id
    snapshot(resp)


def test_private_comment_visible_to_insiders_only(api: Api, admin: str, user: dict, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.post(f"/bug/{bug_id}/comment", {"comment": "Insider only.", "is_private": True}, token=admin)
    assert resp.status == 201, resp.body
    as_admin = api.get(f"/bug/{bug_id}/comment", token=admin)
    assert [c["is_private"] for c in as_admin.body["bugs"][str(bug_id)]["comments"]] == [False, True]
    snapshot(as_admin, "admin")
    assert [c["text"] for c in comments_of(api, bug_id)] == ["Created by the REST parity suite."]
    assert [c["text"] for c in comments_of(api, bug_id, token=user["token"])] == ["Created by the REST parity suite."]


def test_private_comment_denied_for_plain_user(api: Api, user: dict, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.post(f"/bug/{bug_id}/comment", {"comment": "Sneaky.", "is_private": True}, token=user["token"])
    assert_error(resp, 401, 113)
    snapshot(resp)


def test_comments_new_since(api: Api, admin: str, make_bug):
    bug_id = make_bug()
    assert comments_of(api, bug_id, new_since="2099-01-01T00:00:00Z") == []
    assert len(comments_of(api, bug_id, new_since="2000-01-01T00:00:00Z")) == 1


@pytest.mark.parametrize(
    ("label", "body", "http_status", "code"),
    [
        ("empty", {"comment": ""}, 400, 50),
        ("whitespace", {"comment": "   "}, 400, 50),
        ("missing", {}, 400, 50),
    ],
)
def test_add_comment_invalid(api: Api, admin: str, make_bug, snapshot, label, body, http_status, code):
    bug_id = make_bug()
    resp = api.post(f"/bug/{bug_id}/comment", body, token=admin)
    assert_error(resp, http_status, code)
    snapshot(resp)


def test_add_comment_requires_login(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.post(f"/bug/{bug_id}/comment", {"comment": "anonymous"})
    assert_error(resp, 401, 410)
    snapshot(resp)


def test_add_comment_unknown_bug(api: Api, admin: str, snapshot):
    resp = api.post("/bug/99999999/comment", {"comment": "x"}, token=admin)
    assert_error(resp, 404, 101)
    snapshot(resp)


def test_comments_unknown_bug(api: Api, snapshot):
    resp = api.get("/bug/99999999/comment")
    assert_error(resp, 404, 101)
    snapshot(resp)


def test_comment_unknown_id(api: Api, snapshot):
    resp = api.get("/bug/comment/99999999")
    assert_error(resp, 400, 111)
    snapshot(resp)


# --------------------------------------------------------------------------- history

def test_history_empty_for_new_bug(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.get(f"/bug/{bug_id}/history")
    assert resp.status == 200
    assert resp.body == {"bugs": [{"id": bug_id, "alias": [], "history": []}]}
    snapshot(resp)


def test_history_records_changes_in_order(api: Api, admin: str, user: dict, make_bug, snapshot):
    bug_id = make_bug()
    api.put(f"/bug/{bug_id}", {"priority": "High"}, token=admin)
    time.sleep(1.1)  # history entries are grouped by (when, who) at one-second resolution
    api.put(f"/bug/{bug_id}", {"status": "RESOLVED", "resolution": "FIXED"}, token=user["token"])
    resp = api.get(f"/bug/{bug_id}/history")
    assert resp.status == 200
    (entry,) = resp.body["bugs"]
    assert set(entry) == {"id", "alias", "history"} and entry["id"] == bug_id
    assert [h["who"] for h in entry["history"]] == [ADMIN_EMAIL, user["email"]]
    assert all(set(h) == {"when", "who", "changes"} for h in entry["history"])
    first, second = entry["history"]
    assert first["when"] < second["when"]
    assert first["changes"] == [{"field_name": "priority", "removed": "Normal", "added": "High"}]
    assert sorted(second["changes"], key=lambda c: c["field_name"]) == [
        {"field_name": "resolution", "removed": "", "added": "FIXED"},
        {"field_name": "status", "removed": "CONFIRMED", "added": "RESOLVED"},
    ]
    snapshot(resp)


def test_history_cc_change(api: Api, admin: str, user: dict, make_bug):
    bug_id = make_bug()
    api.put(f"/bug/{bug_id}", {"cc": {"add": [user["email"]]}}, token=admin)
    time.sleep(1.1)  # same-second cc changes are merged into one activity row
    api.put(f"/bug/{bug_id}", {"cc": {"remove": [user["email"]]}}, token=admin)
    history = api.get(f"/bug/{bug_id}/history").body["bugs"][0]["history"]
    changes = sorted((c for h in history for c in h["changes"]), key=lambda c: c["added"])
    assert changes == [
        {"field_name": "cc", "removed": user["email"], "added": ""},
        {"field_name": "cc", "removed": "", "added": user["email"]},
    ]


def test_history_new_since(api: Api, admin: str, make_bug):
    bug_id = make_bug()
    api.put(f"/bug/{bug_id}", {"priority": "High"}, token=admin)
    future = api.get(f"/bug/{bug_id}/history", params={"new_since": "2099-01-01T00:00:00Z"})
    assert future.body["bugs"][0]["history"] == []
    past = api.get(f"/bug/{bug_id}/history", params={"new_since": "2000-01-01T00:00:00Z"})
    assert len(past.body["bugs"][0]["history"]) == 1


def test_history_unknown_bug(api: Api, snapshot):
    resp = api.get("/bug/99999999/history")
    assert_error(resp, 404, 101)
    snapshot(resp)


def test_history_unknown_alias(api: Api, snapshot):
    resp = api.get("/bug/no-such-alias/history")
    assert_error(resp, 400, 100)
    snapshot(resp)


# --------------------------------------------------------------------------- attachments (metadata)

def test_add_attachment_and_list_metadata(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    resp = attach(api, bug_id, admin)
    assert resp.status == 201, resp.body
    assert set(resp.body) == {"ids"} and len(resp.body["ids"]) == 1
    attachment_id = resp.body["ids"][0]
    snapshot(resp, "created")

    listing = api.get(f"/bug/{bug_id}/attachment", params={"exclude_fields": "data"})
    assert listing.status == 200
    assert listing.body["attachments"] == {}
    (meta,) = listing.body["bugs"][str(bug_id)]
    assert set(meta) == ATTACHMENT_META_KEYS
    assert meta["id"] == attachment_id and meta["bug_id"] == bug_id
    assert meta["file_name"] == "hello.txt" and meta["summary"] == "parity attachment"
    assert meta["content_type"] == "text/plain" and meta["size"] == 5
    assert meta["creator"] == ADMIN_EMAIL and meta["flags"] == []
    assert meta["is_patch"] == 0 and meta["is_private"] == 0 and meta["is_obsolete"] == 0
    snapshot(listing, "listing")

    with_data = api.get(f"/bug/attachment/{attachment_id}")
    assert with_data.body["attachments"][str(attachment_id)]["data"] == HELLO_B64
    assert with_data.body["bugs"] == {}

    comments = comments_of(api, bug_id)
    assert comments[-1]["attachment_id"] == attachment_id


def test_attachment_by_id(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    attachment_id = attach(api, bug_id, admin, is_patch=True, file_name="fix.patch",
                           content_type="text/plain", summary="a patch").body["ids"][0]
    resp = api.get(f"/bug/attachment/{attachment_id}", params={"exclude_fields": "data"})
    assert resp.status == 200
    meta = resp.body["attachments"][str(attachment_id)]
    assert meta["is_patch"] == 1 and meta["file_name"] == "fix.patch"
    snapshot(resp)


def test_update_attachment_metadata(api: Api, admin: str, make_bug, snapshot):
    bug_id = make_bug()
    attachment_id = attach(api, bug_id, admin).body["ids"][0]
    resp = api.put(f"/bug/attachment/{attachment_id}", {"summary": "renamed", "is_obsolete": True}, token=admin)
    assert resp.status == 200, resp.body
    (change,) = resp.body["attachments"]
    assert change["id"] == attachment_id
    assert change["changes"] == {"summary": {"removed": "parity attachment", "added": "renamed"},
                                 "is_obsolete": {"removed": "0", "added": "1"}}
    snapshot(resp)
    meta = api.get(f"/bug/attachment/{attachment_id}", params={"exclude_fields": "data"}).body["attachments"][str(attachment_id)]
    assert meta["summary"] == "renamed" and meta["is_obsolete"] == 1


def test_attachments_empty_for_new_bug(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = api.get(f"/bug/{bug_id}/attachment")
    assert resp.status == 200
    assert resp.body == {"attachments": {}, "bugs": {str(bug_id): []}}
    snapshot(resp)


def test_attachment_unknown_id_is_empty_not_error(api: Api, snapshot):
    """Perl quirk: an unknown attachment id yields empty maps rather than a 404."""
    resp = api.get("/bug/attachment/99999999")
    assert resp.status == 200
    assert resp.body == {"attachments": {}, "bugs": {}}
    snapshot(resp)


def test_attachments_unknown_bug(api: Api, snapshot):
    resp = api.get("/bug/99999999/attachment")
    assert_error(resp, 404, 101)
    snapshot(resp)


@pytest.mark.parametrize(
    ("label", "overrides", "http_status", "code"),
    [
        ("missing_data", {"data": None}, 400, 50),
        ("empty_data", {"data": ""}, 400, 606),
        ("missing_summary", {"summary": None}, 400, 604),
        ("missing_file_name", {"file_name": None}, 400, 603),
        ("invalid_content_type", {"content_type": "bogus"}, 400, 601),
    ],
)
def test_add_attachment_invalid(api: Api, admin: str, make_bug, snapshot, label, overrides, http_status, code):
    bug_id = make_bug()
    resp = attach(api, bug_id, admin, **overrides)
    assert_error(resp, http_status, code)
    snapshot(resp)


def test_add_attachment_requires_login(api: Api, make_bug, snapshot):
    bug_id = make_bug()
    resp = attach(api, bug_id, None)
    assert_error(resp, 401, 410)
    snapshot(resp)


def test_add_attachment_unknown_bug(api: Api, admin: str, snapshot):
    resp = attach(api, 99999999, admin)
    assert_error(resp, 404, 101)
    snapshot(resp)
