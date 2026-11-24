"""Bug behaviour found while porting the module: each case here was added
before the Python code that satisfies it (see .agents/skills/port-bugzilla-module)."""
import os

import pytest

from conftest import ALICE, Api, assert_error, bug_fields, login
from normalize import normalize

ALICE_PASSWORD = os.environ.get("BZ_DEMO_USER_PASSWORD", "Demo-passw0rd!")
NOT_INSIDER = "Sorry, but you are not allowed to (un)mark comments or attachments as private."


@pytest.fixture(scope="module")
def alice(http):
    """A user outside the insider group."""
    return Api(http, login(http, ALICE, ALICE_PASSWORD)["token"])


def test_summary_is_trimmed_but_inner_spaces_are_kept(admin, make_bug, run_tag):
    bug_id = make_bug("spaced   out  ")
    bug = admin.get(f"/bug/{bug_id}", include_fields="summary").json()["bugs"][0]
    assert bug == {"summary": f"{run_tag} spaced   out"}


def test_create_with_blocks_and_alias(admin, anon, make_bug, run_tag):
    blocked = make_bug("blocked by new bug")
    alias = f"{run_tag}-alias"
    blocker = make_bug("blocker", blocks=[blocked], alias=alias)
    bugs = {blocked: "A", blocker: "B"}
    resp = anon.get(f"/bug/{alias}", include_fields="id,alias,blocks,depends_on")
    assert normalize(resp.json(), bugs) == {"faults": [], "bugs": [
        {"id": "<BUG:B>", "alias": [alias], "blocks": ["<BUG:A>"], "depends_on": []}]}
    history = normalize(anon.get(f"/bug/{blocked}/history").json(), bugs)["bugs"][0]["history"]
    assert [h["changes"] for h in history] == [[{"added": "<BUG:B>", "field_name": "depends_on", "removed": ""}]]
    resp = admin.post("/bug", bug_fields(f"{run_tag} alias taken", alias=alias))
    assert (resp.status_code, resp.json()["code"]) == (400, 103)


def test_duplicate_and_reopen(admin, anon, bug, make_bug):
    target = make_bug("duplicate target")
    bugs = {bug: "A", target: "B"}
    resp = admin.put(f"/bug/{bug}", {"dupe_of": target, "comment": {"body": "Same problem."}})
    assert resp.status_code == 200
    # dupe_of is reported as a change, but is not written to the history.
    assert resp.json()["bugs"][0]["changes"] == {
        "dupe_of": {"added": str(target), "removed": ""},
        "resolution": {"added": "DUPLICATE", "removed": ""},
        "status": {"added": "RESOLVED", "removed": "CONFIRMED"}}
    fetched = anon.get(f"/bug/{bug}", include_fields="status,resolution,dupe_of").json()["bugs"][0]
    assert fetched == {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": target}
    texts = lambda b: [c["text"] for c in anon.get(f"/bug/{b}/comment").json()["bugs"][str(b)]["comments"]]
    assert texts(bug)[1] == f"Same problem.\n\n*** This bug has been marked as a duplicate of bug {target} ***"
    assert texts(target)[1] == f"*** Bug {bug} has been marked as a duplicate of this bug. ***"
    fields = {c["field_name"] for h in anon.get(f"/bug/{bug}/history").json()["bugs"][0]["history"]
              for c in h["changes"]}
    assert fields == {"status", "resolution"}

    resp = admin.put(f"/bug/{bug}", {"status": "CONFIRMED"})
    assert resp.json()["bugs"][0]["changes"] == {
        "dupe_of": {"added": "", "removed": str(target)},
        "resolution": {"added": "", "removed": "DUPLICATE"},
        "status": {"added": "CONFIRMED", "removed": "RESOLVED"}}
    assert anon.get(f"/bug/{bug}", include_fields="dupe_of").json()["bugs"][0] == {"dupe_of": None}


def test_update_illegal_status_transition(admin, bug):
    assert_error(admin.put(f"/bug/{bug}", {"status": "VERIFIED", "resolution": "FIXED"}), 400, 123,
                 "You are not allowed to change the bug status from CONFIRMED to VERIFIED.")


def test_update_resolution_on_open_bug(admin, bug):
    assert_error(admin.put(f"/bug/{bug}", {"resolution": "FIXED"}), 400, 122,
                 "You cannot set a resolution for open bugs.")


def test_update_ids_parameter_replaces_the_path_id(admin, bug, make_bug):
    other = make_bug("ids target")
    resp = admin.put(f"/bug/{bug}", {"ids": [other], "severity": "major"})
    assert [b["id"] for b in resp.json()["bugs"]] == [other]
    assert admin.get(f"/bug/{bug}", include_fields="severity").json()["bugs"][0] == {"severity": "minor"}


def test_update_query_parameter_wins_over_body(admin, bug):
    resp = admin.put(f"/bug/{bug}", {"priority": "Normal"}, priority="High")
    assert resp.json()["bugs"][0]["changes"] == {"priority": {"added": "High", "removed": "Low"}}


def test_private_comment_needs_insider(alice, anon, bug):
    assert_error(alice.post(f"/bug/{bug}/comment", {"comment": "secret", "is_private": True}), 401, 113, NOT_INSIDER)
    assert len(anon.get(f"/bug/{bug}/comment").json()["bugs"][str(bug)]["comments"]) == 1


def test_private_attachment_is_hidden_from_anonymous(admin, anon, bug):
    resp = admin.post(f"/bug/{bug}/attachment", {"data": "aGk=", "file_name": "dir/p.diff", "summary": "Fix",
                                                 "is_patch": True, "is_private": True, "comment": "See file."})
    assert resp.status_code == 201
    listed = admin.get(f"/bug/{bug}/attachment", include_fields="file_name,content_type,is_patch,is_private")
    assert listed.json()["bugs"][str(bug)] == [
        {"file_name": "p.diff", "content_type": "text/plain", "is_patch": 1, "is_private": 1}]
    assert anon.get(f"/bug/{bug}/attachment").json()["bugs"][str(bug)] == []
    texts = lambda api: [c["text"] for c in api.get(f"/bug/{bug}/comment").json()["bugs"][str(bug)]["comments"]]
    assert texts(admin)[1].endswith("\nFix\n\nSee file.")
    assert len(texts(anon)) == 1


def test_comment_id_must_be_numeric(anon):
    assert_error(anon.get("/bug/comment/abc"), 400, 52,
                 "Invalid parameter passed to Bugzilla::Comment::new_from_list: It must be numeric.")


def test_search_offset_requires_limit(anon):
    assert_error(anon.get("/bug", offset=1, summary="x"), 400, 50,
                 "The function Bug.search() requires a limit argument, and that argument was not set.")


@pytest.mark.parametrize("method, path", [("GET", "/bug/1/nosuch"), ("DELETE", "/bug/1"), ("POST", "/bug/1")])
def test_unknown_bug_route(anon, method, path):
    assert_error(anon.request(method, path), 404, 32614, f"A REST API resource was not found for '{method} {path}'.")


def test_update_alias(admin, anon, bug, run_tag):
    # Not ported to Python yet: the Python service hands these to Perl.
    alias = f"{run_tag}-later"
    resp = admin.put(f"/bug/{bug}", {"alias": {"add": [alias]}})
    assert resp.status_code == 200
    assert resp.json()["bugs"][0]["changes"] == {"alias": {"added": alias, "removed": ""}}
    found = anon.get("/bug", alias=alias, include_fields="id")
    assert (found.status_code, found.json()) == (200, {"bugs": [{"id": bug}]})
