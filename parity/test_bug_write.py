"""Bugzilla::WebService::Bug write endpoints: create, update."""
import pytest

from conftest import ALICE, BOB, PRODUCT, USER, assert_error, bug_fields
from normalize import normalize

LOGIN_REQUIRED = "You must log in before using this part of Bugzilla."


def no_user(name):
    return (f"There is no user named '{name}'. Either you mis-typed the name or that user "
            "has not yet registered for a Bugzilla account.")


# ---------------------------------------------------------------- create

def test_create_bug(admin, run_tag):
    summary = f"{run_tag} create"
    resp = admin.post("/bug", bug_fields(summary, assigned_to=BOB, cc=[ALICE], priority="High",
                                         severity="major", component="Accounts"))
    assert resp.status_code == 200
    bug_id = resp.json()["id"]
    assert normalize(resp.json()) == {"id": "<ID>"}

    fetched = admin.get(f"/bug/{bug_id}", include_fields=(
        "id,summary,product,component,version,op_sys,platform,priority,severity,status,resolution,"
        "assigned_to,cc,creator,is_open,is_confirmed,creation_time,last_change_time"))
    assert fetched.json()["bugs"][0]["creation_time"] == fetched.json()["bugs"][0]["last_change_time"]
    assert normalize(fetched.json(), {bug_id: "NEW"}) == {"faults": [], "bugs": [{
        "id": "<BUG:NEW>", "summary": summary, "product": PRODUCT, "component": "Accounts",
        "version": "1.0", "op_sys": "All", "platform": "All", "priority": "High", "severity": "major",
        "status": "CONFIRMED", "resolution": "", "assigned_to": BOB, "cc": [ALICE],
        # Asking for a user field also returns its "_detail" companion.
        "assigned_to_detail": USER["bob"], "cc_detail": [USER["alice"]], "creator_detail": USER["admin"],
        "creator": USER["admin"]["email"], "is_open": True, "is_confirmed": True,
        "creation_time": "<TIMESTAMP>", "last_change_time": "<TIMESTAMP>",
    }]}


def test_create_bug_description_becomes_comment_zero(admin, make_bug):
    bug_id = make_bug("create description", description="First comment text.")
    comments = normalize(admin.get(f"/bug/{bug_id}/comment").json(), {bug_id: "NEW"})
    assert comments == {"comments": {}, "bugs": {"<BUG:NEW>": {"comments": [{
        "attachment_id": None, "bug_id": "<BUG:NEW>", "count": 0, "creation_time": "<TIMESTAMP>",
        "creator": USER["admin"]["email"], "id": "<ID>", "is_private": False, "tags": [],
        "text": "First comment text.", "time": "<TIMESTAMP>",
    }]}}}


def test_create_bug_defaults_assignee_to_component_owner(admin, make_bug):
    bug_id = make_bug("create default assignee")
    bug = admin.get(f"/bug/{bug_id}", include_fields="assigned_to,cc,status").json()["bugs"][0]
    assert bug["cc"] == [] and bug["status"] == "CONFIRMED"
    assert bug["assigned_to"].endswith("@bugzilla.test")


def test_create_bug_without_severity_uses_default(admin, run_tag):
    fields = bug_fields(f"{run_tag} create no severity")
    del fields["severity"]
    resp = admin.post("/bug", fields)
    assert resp.status_code == 200
    bug = admin.get(f"/bug/{resp.json()['id']}", include_fields="severity").json()["bugs"][0]
    assert bug == {"severity": "normal"}


def test_create_bug_requires_login(anon, run_tag):
    assert_error(anon.post("/bug", bug_fields(f"{run_tag} noauth")), 401, 410, LOGIN_REQUIRED)


def test_create_bug_bad_token(anon, run_tag):
    assert_error(anon.post("/bug", bug_fields(f"{run_tag} bad token"), token="1-abcdefghij"), 400, 32000,
                 "The cookies or token provide were not valid or have expired. "
                 "You may login again to get new cookies or a new token.")


@pytest.mark.parametrize("field, status, code, message", [
    ("product", 400, 50, "You must select/enter a product."),
    ("component", 400, 105, "To file this bug, you must first choose a component. If necessary, just guess."),
    ("summary", 400, 107, "You must enter a summary for this bug."),
    ("version", 400, 50, "You must select/enter a version."),
    ("op_sys", 400, 50, "You must select/enter a OS."),
    # The default priority parameter is not a legal value in this install.
    ("priority", 404, 51, "There is no Priority named '--'."),
])
def test_create_bug_missing_field(admin, run_tag, field, status, code, message):
    fields = bug_fields(f"{run_tag} missing {field}")
    del fields[field]
    assert_error(admin.post("/bug", fields), status, code, message)


@pytest.mark.parametrize("field, value, status, code, message", [
    ("product", "NoSuch", 401, 106,
     "Sorry, either the product NoSuch does not exist or you aren't authorized to enter a bug into it."),
    ("component", "NoSuch", 404, 51, "There is no component named 'NoSuch' in the 'WebStore' product."),
    ("version", "9.9", 404, 51, "There is no version named '9.9' in the 'WebStore' product."),
    ("priority", "Bogus", 404, 51, "There is no Priority named 'Bogus'."),
    ("severity", "Bogus", 404, 51, "There is no Severity named 'Bogus'."),
    ("op_sys", "Bogus", 404, 51, "There is no OS named 'Bogus'."),
    ("platform", "Bogus", 404, 51, "There is no Hardware named 'Bogus'."),
    ("status", "BOGUS", 404, 51, "There is no status named 'BOGUS'."),
    ("target_milestone", "nope", 404, 51, "There is no milestone named 'nope' in the 'WebStore' product."),
    ("assigned_to", "nobody@x.test", 404, 51, no_user("nobody@x.test")),
    ("cc", ["nobody@x.test"], 401, 504,
     "The name nobody@x.test is not a valid username. Either you misspelled it, "
     "or the person has not registered for a Bugzilla account."),
    ("summary", "", 400, 107, "You must enter a summary for this bug."),
    ("depends_on", [999999], 404, 101, "Bug #999999 does not exist."),
])
def test_create_bug_invalid_field_value(admin, run_tag, field, value, status, code, message):
    fields = bug_fields(f"{run_tag} invalid {field}")
    fields[field] = value
    assert_error(admin.post("/bug", fields), status, code, message)


def test_create_bug_numeric_alias(admin, run_tag):
    resp = admin.post("/bug", bug_fields(f"{run_tag} numeric alias", alias="1"))
    assert resp.status_code == 400
    assert resp.json()["code"] == 103
    assert resp.json()["message"].startswith("You tried to give this bug the alias 1, but aliases cannot be merely numbers")


def test_create_bug_invalid_values_create_nothing(admin, anon, run_tag):
    summary = f"{run_tag} must-not-exist"
    admin.post("/bug", bug_fields(summary, priority="Bogus"))
    anon.post("/bug", bug_fields(summary))
    assert anon.get("/bug", summary=summary).json() == {"bugs": []}


# ---------------------------------------------------------------- update

def updated(resp, bugs):
    assert resp.status_code == 200, resp.text
    return normalize(resp.json(), bugs)


def changes(**changed):
    return {"bugs": [{"alias": [], "id": "<BUG:A>", "last_change_time": "<TIMESTAMP>", "changes": changed}]}


def test_update_bug_fields(admin, bug, run_tag):
    resp = admin.put(f"/bug/{bug}", {"summary": f"{run_tag} renamed", "priority": "High"})
    assert updated(resp, {bug: "A"}) == changes(
        priority={"added": "High", "removed": "Low"},
        summary={"added": f"{run_tag} renamed", "removed": f"{run_tag} scratch"},
    )
    fetched = admin.get(f"/bug/{bug}", include_fields="summary,priority").json()["bugs"][0]
    assert fetched == {"summary": f"{run_tag} renamed", "priority": "High"}


def test_update_bug_without_changes(admin, bug):
    assert updated(admin.put(f"/bug/{bug}", {"priority": "Low"}), {bug: "A"}) == changes()


def test_update_bug_unknown_field_is_ignored(admin, bug):
    assert updated(admin.put(f"/bug/{bug}", {"nosuchfield": "x"}), {bug: "A"}) == changes()


def test_update_bug_cc(admin, bug):
    resp = admin.put(f"/bug/{bug}", {"cc": {"add": [BOB, ALICE]}})
    assert updated(resp, {bug: "A"}) == changes(cc={"added": f"{ALICE}, {BOB}", "removed": ""})
    resp = admin.put(f"/bug/{bug}", {"cc": {"remove": [BOB]}})
    assert updated(resp, {bug: "A"}) == changes(cc={"added": "", "removed": BOB})
    assert admin.get(f"/bug/{bug}", include_fields="cc").json()["bugs"][0]["cc"] == [ALICE]


def test_update_bug_dependency(admin, bug, make_bug):
    other = make_bug("dependency target")
    bugs = {bug: "A", other: "B"}
    resp = admin.put(f"/bug/{bug}", {"depends_on": {"add": [other]}})
    assert updated(resp, bugs) == changes(depends_on={"added": "<BUG:B>", "removed": ""})
    fetched = admin.get("/bug", id=f"{bug},{other}", include_fields="id,depends_on,blocks")
    assert normalize(fetched.json(), bugs) == {"bugs": [
        {"id": "<BUG:A>", "depends_on": ["<BUG:B>"], "blocks": []},
        {"id": "<BUG:B>", "depends_on": [], "blocks": ["<BUG:A>"]},
    ]}


def test_update_bug_status_workflow(admin, bug):
    bugs = {bug: "A"}
    resp = admin.put(f"/bug/{bug}", {"status": "IN_PROGRESS"})
    assert updated(resp, bugs) == changes(status={"added": "IN_PROGRESS", "removed": "CONFIRMED"})

    resp = admin.put(f"/bug/{bug}", {"status": "RESOLVED", "resolution": "FIXED", "comment": {"body": "Fixed."}})
    assert updated(resp, bugs) == changes(
        resolution={"added": "FIXED", "removed": ""},
        status={"added": "RESOLVED", "removed": "IN_PROGRESS"},
    )
    fetched = admin.get(f"/bug/{bug}", include_fields="status,resolution,is_open").json()["bugs"][0]
    assert fetched == {"status": "RESOLVED", "resolution": "FIXED", "is_open": False}
    texts = [c["text"] for c in admin.get(f"/bug/{bug}/comment").json()["bugs"][str(bug)]["comments"]]
    assert texts == ["Created by the parity suite.", "Fixed."]

    resp = admin.put(f"/bug/{bug}", {"status": "UNCONFIRMED"})
    assert updated(resp, bugs) == changes(
        is_confirmed={"added": "0", "removed": "1"},
        resolution={"added": "", "removed": "FIXED"},
        status={"added": "UNCONFIRMED", "removed": "RESOLVED"},
    )


def test_update_bug_requires_login(anon, admin, bug, run_tag):
    assert_error(anon.put(f"/bug/{bug}", {"summary": "changed anonymously"}), 401, 410, LOGIN_REQUIRED)
    assert admin.get(f"/bug/{bug}", include_fields="summary").json()["bugs"][0] == {"summary": f"{run_tag} scratch"}


def test_update_bug_not_found(admin):
    assert_error(admin.put("/bug/999999", {"summary": "x"}), 404, 101, "Bug #999999 does not exist.")


def test_update_bug_invalid_id(admin):
    assert_error(admin.put("/bug/notabug", {"summary": "x"}), 400, 100,
                 "'notabug' is not a valid bug number nor an alias to a bug.")


@pytest.mark.parametrize("body, status, code, message", [
    ({"status": "BOGUS"}, 404, 51, "There is no status named 'BOGUS'."),
    ({"status": "RESOLVED"}, 400, 121, "A valid resolution is required to mark bugs as RESOLVED."),
    ({"priority": "Bogus"}, 404, 51, "There is no Priority named 'Bogus'."),
    ({"severity": "Bogus"}, 404, 51, "There is no Severity named 'Bogus'."),
    ({"resolution": "BOGUS"}, 404, 51, "There is no Resolution named 'BOGUS'."),
    ({"component": "NoSuch"}, 404, 51, "There is no component named 'NoSuch' in the 'WebStore' product."),
    ({"cc": {"add": ["nobody@x.test"]}}, 404, 51, no_user("nobody@x.test")),
    ({"assigned_to": "nobody@x.test"}, 404, 51, no_user("nobody@x.test")),
    ({"summary": ""}, 400, 107, "You must enter a summary for this bug."),
    ({"depends_on": {"add": [999999]}}, 404, 101, "Bug #999999 does not exist."),
    ({"dupe_of": 999999}, 404, 101, "Bug #999999 does not exist."),
], ids=lambda v: "-".join(v) if isinstance(v, dict) else None)
def test_update_bug_invalid_field_value(admin, bug, body, status, code, message):
    assert_error(admin.put(f"/bug/{bug}", body), status, code, message)
    assert admin.get(f"/bug/{bug}/history").json()["bugs"][0]["history"] == []


def test_update_bug_cannot_depend_on_itself(admin, bug):
    assert_error(admin.put(f"/bug/{bug}", {"depends_on": {"add": [bug]}}), 400, 116,
                 "You can't make a bug block itself or depend on itself.")
