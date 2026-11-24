"""Bugzilla::WebService::Bug: attachment metadata (attachments, add_attachment)."""
import pytest

from conftest import USER, assert_error
from normalize import normalize

ADMIN = USER["admin"]["email"]
FILE = {"data": "aGVsbG8=", "file_name": "notes.txt", "summary": "Parity notes", "content_type": "text/plain"}
METADATA = {
    "bug_id": "<BUG:A>", "content_type": "text/plain", "creation_time": "<TIMESTAMP>", "creator": ADMIN,
    "file_name": "notes.txt", "flags": [], "id": "<ID>", "is_obsolete": 0, "is_patch": 0, "is_private": 0,
    "last_change_time": "<TIMESTAMP>", "size": 5, "summary": "Parity notes",
}


@pytest.fixture(scope="module")
def attached(admin, make_bug):
    bug_id = make_bug("attachments")
    resp = admin.post(f"/bug/{bug_id}/attachment", FILE)
    assert resp.status_code == 201, resp.text
    assert normalize(resp.json()) == {"ids": ["<ID>"]}
    return bug_id, resp.json()["ids"][0]


def test_bug_without_attachments(anon, bug):
    resp = anon.get(f"/bug/{bug}/attachment")
    assert resp.status_code == 200
    assert normalize(resp.json(), {bug: "A"}) == {"attachments": {}, "bugs": {"<BUG:A>": []}}


def test_attachments_of_bug(anon, attached):
    bug_id, attachment_id = attached
    resp = anon.get(f"/bug/{bug_id}/attachment")
    assert resp.status_code == 200
    assert resp.json()["bugs"][str(bug_id)][0]["id"] == attachment_id
    assert normalize(resp.json(), {bug_id: "A"}) == {
        "attachments": {}, "bugs": {"<BUG:A>": [dict(METADATA, data="aGVsbG8=")]}}


def test_attachments_metadata_only(anon, attached):
    bug_id, _ = attached
    resp = anon.get(f"/bug/{bug_id}/attachment", exclude_fields="data")
    assert normalize(resp.json(), {bug_id: "A"}) == {"attachments": {}, "bugs": {"<BUG:A>": [METADATA]}}


def test_attachment_by_id(anon, attached):
    bug_id, attachment_id = attached
    resp = anon.get(f"/bug/attachment/{attachment_id}", exclude_fields="data")
    assert resp.status_code == 200
    assert list(resp.json()["attachments"]) == [str(attachment_id)]
    assert normalize(resp.json(), {bug_id: "A"}) == {"bugs": {}, "attachments": {"<ID>": METADATA}}


def test_attachment_include_fields(anon, attached):
    bug_id, attachment_id = attached
    resp = anon.get(f"/bug/attachment/{attachment_id}", include_fields="file_name,size,content_type")
    assert normalize(resp.json()) == {"bugs": {}, "attachments": {"<ID>": {
        "file_name": "notes.txt", "size": 5, "content_type": "text/plain"}}}


def test_attachment_adds_a_comment(anon, attached):
    bug_id, attachment_id = attached
    comments = anon.get(f"/bug/{bug_id}/comment").json()["bugs"][str(bug_id)]["comments"]
    assert comments[1]["attachment_id"] == attachment_id
    assert comments[1]["text"] == f"Created attachment {attachment_id}\nParity notes"
    assert normalize(comments[1], {bug_id: "A"}) == {
        "attachment_id": "<ID>", "bug_id": "<BUG:A>", "count": 1, "creation_time": "<TIMESTAMP>",
        "creator": ADMIN, "id": "<ID>", "is_private": False, "tags": [],
        "text": "Created attachment <ID>\nParity notes", "time": "<TIMESTAMP>",
    }


def test_attachments_bug_not_found(anon):
    assert_error(anon.get("/bug/999999/attachment"), 404, 101, "Bug #999999 does not exist.")


def test_attachments_invalid_bug_id(anon):
    assert_error(anon.get("/bug/notabug/attachment"), 400, 100,
                 "'notabug' is not a valid bug number nor an alias to a bug.")


def test_attachment_id_not_found_returns_empty_result(anon):
    # Perl answers 200 with nothing instead of an error for an unknown attachment id.
    resp = anon.get("/bug/attachment/999999")
    assert (resp.status_code, resp.json()) == (200, {"attachments": {}, "bugs": {}})


def test_add_attachment_requires_login(anon, bug):
    assert_error(anon.post(f"/bug/{bug}/attachment", FILE), 401, 410,
                 "You must log in before using this part of Bugzilla.")
    assert anon.get(f"/bug/{bug}/attachment").json()["bugs"][str(bug)] == []


def test_add_attachment_bug_not_found(admin):
    assert_error(admin.post("/bug/999999/attachment", FILE), 404, 101, "Bug #999999 does not exist.")


def test_add_attachment_missing_data(admin, bug):
    body = {k: v for k, v in FILE.items() if k != "data"}
    assert_error(admin.post(f"/bug/{bug}/attachment", body), 400, 50,
                 "The function requires a data argument, and that argument was not set.")


def test_add_attachment_missing_summary(admin, bug):
    body = {k: v for k, v in FILE.items() if k != "summary"}
    assert_error(admin.post(f"/bug/{bug}/attachment", body), 400, 604,
                 "You must enter a description for the attachment.")


@pytest.mark.parametrize("content_type", [None, "notatype"])
def test_add_attachment_invalid_content_type(admin, bug, content_type):
    body = {k: v for k, v in FILE.items() if k != "content_type"}
    if content_type:
        body["content_type"] = content_type
    resp = admin.post(f"/bug/{bug}/attachment", body)
    assert (resp.status_code, resp.json()["code"]) == (400, 601)
    assert resp.json()["message"].startswith(
        f"The content type {content_type} is invalid." if content_type else "The content type is invalid.")
