"""Bugzilla::WebService::Bug: comments, add_comment, history."""
import pytest

from conftest import ALICE, USER, assert_error
from normalize import normalize

ADMIN = USER["admin"]["email"]


def comment(count, text, **extra):
    return dict({
        "attachment_id": None, "bug_id": "<BUG:A>", "count": count, "creation_time": "<TIMESTAMP>",
        "creator": ADMIN, "id": "<ID>", "is_private": False, "tags": [], "text": text, "time": "<TIMESTAMP>",
    }, **extra)


# ---------------------------------------------------------------- comments

def test_add_and_list_comments(admin, anon, bug):
    resp = admin.post(f"/bug/{bug}/comment", {"comment": "Second comment."})
    assert resp.status_code == 201
    comment_id = resp.json()["id"]
    assert normalize(resp.json()) == {"id": "<ID>"}

    listed = anon.get(f"/bug/{bug}/comment")
    assert listed.status_code == 200
    assert normalize(listed.json(), {bug: "A"}) == {"comments": {}, "bugs": {"<BUG:A>": {"comments": [
        comment(0, "Created by the parity suite."), comment(1, "Second comment."),
    ]}}}
    assert listed.json()["bugs"][str(bug)]["comments"][1]["id"] == comment_id

    one = anon.get(f"/bug/comment/{comment_id}")
    assert one.status_code == 200
    assert list(one.json()["comments"]) == [str(comment_id)]
    assert normalize(one.json(), {bug: "A"}) == {"bugs": {}, "comments": {"<ID>": comment(1, "Second comment.")}}


def test_comments_include_fields(anon, bug):
    resp = anon.get(f"/bug/{bug}/comment", include_fields="count,text")
    assert normalize(resp.json(), {bug: "A"}) == {"comments": {}, "bugs": {"<BUG:A>": {"comments": [
        {"count": 0, "text": "Created by the parity suite."}]}}}


def test_comments_new_since_in_the_future(anon, bug):
    past = anon.get(f"/bug/{bug}/comment", new_since="2000-01-01T00:00:00Z")
    assert len(past.json()["bugs"][str(bug)]["comments"]) == 1
    resp = anon.get(f"/bug/{bug}/comment", new_since="2099-01-01T00:00:00Z")
    assert normalize(resp.json(), {bug: "A"}) == {"comments": {}, "bugs": {"<BUG:A>": {"comments": []}}}


def test_private_comment_is_hidden_from_anonymous(admin, anon, bug):
    resp = admin.post(f"/bug/{bug}/comment", {"comment": "Insiders only.", "is_private": True})
    assert resp.status_code == 201
    texts = lambda api: [c["text"] for c in api.get(f"/bug/{bug}/comment").json()["bugs"][str(bug)]["comments"]]
    assert texts(admin) == ["Created by the parity suite.", "Insiders only."]
    assert texts(anon) == ["Created by the parity suite."]


def test_comments_bug_not_found(anon):
    assert_error(anon.get("/bug/999999/comment"), 404, 101, "Bug #999999 does not exist.")


def test_comments_invalid_bug_id(anon):
    assert_error(anon.get("/bug/notabug/comment"), 400, 100,
                 "'notabug' is not a valid bug number nor an alias to a bug.")


def test_comment_id_not_found(anon):
    assert_error(anon.get("/bug/comment/999999"), 400, 111, "999999 is not a valid comment id.")


def test_comments_invalid_new_since(anon, bug):
    assert_error(anon.get(f"/bug/{bug}/comment", new_since="zzz"), 400, 56, "'zzz' is not a legal date.")


def test_add_comment_requires_login(anon, bug):
    assert_error(anon.post(f"/bug/{bug}/comment", {"comment": "anonymous"}), 401, 410,
                 "You must log in before using this part of Bugzilla.")
    assert len(anon.get(f"/bug/{bug}/comment").json()["bugs"][str(bug)]["comments"]) == 1


def test_add_comment_bug_not_found(admin):
    assert_error(admin.post("/bug/999999/comment", {"comment": "x"}), 404, 101, "Bug #999999 does not exist.")


@pytest.mark.parametrize("body", [{}, {"comment": ""}], ids=["missing", "empty"])
def test_add_comment_without_text(admin, bug, body):
    assert_error(admin.post(f"/bug/{bug}/comment", body), 400, 50,
                 "The function requires a comment argument, and that argument was not set.")


def test_add_comment_too_long(admin, bug):
    assert_error(admin.post(f"/bug/{bug}/comment", {"comment": "x" * 65536}), 400, 114,
                 "Comments cannot be longer than 65535 characters.")


# ---------------------------------------------------------------- history

def test_history_of_new_bug_is_empty(anon, bug):
    resp = anon.get(f"/bug/{bug}/history")
    assert resp.status_code == 200
    assert normalize(resp.json(), {bug: "A"}) == {"bugs": [{"alias": [], "history": [], "id": "<BUG:A>"}]}


def test_history_records_field_changes(admin, anon, bug, run_tag):
    admin.put(f"/bug/{bug}", {"summary": f"{run_tag} renamed", "priority": "High", "cc": {"add": [ALICE]},
                              "status": "RESOLVED", "resolution": "FIXED"})
    resp = anon.get(f"/bug/{bug}/history")
    assert normalize(resp.json(), {bug: "A"}) == {"bugs": [{"alias": [], "id": "<BUG:A>", "history": [{
        "when": "<TIMESTAMP>", "who": ADMIN, "changes": [
            {"added": ALICE, "field_name": "cc", "removed": ""},
            {"added": "High", "field_name": "priority", "removed": "Low"},
            {"added": "FIXED", "field_name": "resolution", "removed": ""},
            {"added": "RESOLVED", "field_name": "status", "removed": "CONFIRMED"},
            {"added": f"{run_tag} renamed", "field_name": "summary", "removed": f"{run_tag} scratch"},
        ]}]}]}


def test_history_records_dependencies_on_both_bugs(admin, anon, bug, make_bug):
    other = make_bug("history dependency")
    bugs = {bug: "A", other: "B"}
    admin.put(f"/bug/{bug}", {"depends_on": {"add": [other]}})
    entry = lambda field, added: [{"when": "<TIMESTAMP>", "who": ADMIN, "changes": [
        {"added": added, "field_name": field, "removed": ""}]}]
    assert normalize(anon.get(f"/bug/{bug}/history").json(), bugs) == {"bugs": [
        {"alias": [], "id": "<BUG:A>", "history": entry("depends_on", "<BUG:B>")}]}
    assert normalize(anon.get(f"/bug/{other}/history").json(), bugs) == {"bugs": [
        {"alias": [], "id": "<BUG:B>", "history": entry("blocks", "<BUG:A>")}]}


def test_history_new_since_in_the_future(admin, anon, bug):
    assert admin.put(f"/bug/{bug}", {"priority": "High"}).status_code == 200
    past = anon.get(f"/bug/{bug}/history", new_since="2000-01-01T00:00:00Z")
    assert len(past.json()["bugs"][0]["history"]) == 1
    resp = anon.get(f"/bug/{bug}/history", new_since="2099-01-01T00:00:00Z")
    assert normalize(resp.json(), {bug: "A"}) == {"bugs": [{"alias": [], "history": [], "id": "<BUG:A>"}]}


def test_history_bug_not_found(anon):
    assert_error(anon.get("/bug/999999/history"), 404, 101, "Bug #999999 does not exist.")


def test_history_invalid_bug_id(anon):
    assert_error(anon.get("/bug/notabug/history"), 400, 100,
                 "'notabug' is not a valid bug number nor an alias to a bug.")


def test_history_invalid_new_since(anon, bug):
    assert_error(anon.get(f"/bug/{bug}/history", new_since="zzz"), 400, 56, "'zzz' is not a legal date.")
