"""Bug.attachments / Bug.add_attachment (metadata only; data is base64)."""
import base64

from parity.conftest import expect_error, ok
from parity.normalize import DATETIME, ID, normalize

DATA = base64.b64encode(b"hello world").decode()


def test_no_attachments(api, new_bug):
    bug_id = new_bug()
    assert ok(api.get(f"/bug/{bug_id}/attachment")) == {"attachments": {}, "bugs": {str(bug_id): []}}


def test_add_and_read_attachment_metadata(api, admin_token, new_bug):
    bug_id = new_bug()
    r = ok(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                    json={"ids": [bug_id], "data": DATA, "file_name": "hello.txt",
                          "summary": "Hello file", "content_type": "text/plain"}), 201)
    assert normalize(r) == {"ids": [ID]}
    att_id = r["ids"][0]

    by_bug = ok(api.get(f"/bug/{bug_id}/attachment"))
    assert set(by_bug) == {"attachments", "bugs"} and by_bug["attachments"] == {}
    atts = by_bug["bugs"][str(bug_id)]
    assert len(atts) == 1
    assert normalize(atts[0]) == {
        "id": ID, "bug_id": ID, "data": DATA, "size": 11, "file_name": "hello.txt",
        "summary": "Hello file", "content_type": "text/plain", "creator": "admin@bugzilla.test",
        "is_private": 0, "is_obsolete": 0, "is_patch": 0, "flags": [],
        "creation_time": DATETIME, "last_change_time": DATETIME,
    }
    assert atts[0]["id"] == att_id and atts[0]["bug_id"] == bug_id

    by_id = ok(api.get(f"/bug/attachment/{att_id}", params={"exclude_fields": "data"}))
    assert set(by_id) == {"attachments", "bugs"} and by_id["bugs"] == {}
    expected = dict(atts[0]); del expected["data"]
    assert by_id["attachments"] == {str(att_id): expected}



def test_attachment_metadata_include_fields(api, admin_token, new_bug):
    bug_id = new_bug()
    att_id = ok(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                         json={"ids": [bug_id], "data": DATA, "file_name": "a.bin",
                               "summary": "bin", "content_type": "application/octet-stream"}), 201)["ids"][0]
    body = ok(api.get(f"/bug/attachment/{att_id}", params={"include_fields": "file_name,size,content_type"}))
    assert body == {"bugs": {}, "attachments": {str(att_id): {
        "file_name": "a.bin", "size": 11, "content_type": "application/octet-stream"}}}


def test_patch_attachment_forces_text_plain(api, admin_token, new_bug):
    bug_id = new_bug()
    att_id = ok(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                         json={"ids": [bug_id], "data": DATA, "file_name": "fix.patch", "summary": "patch",
                               "content_type": "application/octet-stream", "is_patch": True}), 201)["ids"][0]
    att = ok(api.get(f"/bug/attachment/{att_id}", params={"include_fields": "is_patch,content_type"}))["attachments"][str(att_id)]
    assert att == {"is_patch": 1, "content_type": "text/plain"}


def test_private_attachment_hidden_from_non_insiders(api, admin_token, user_token, new_bug):
    bug_id = new_bug()
    att_id = ok(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                         json={"ids": [bug_id], "data": DATA, "file_name": "secret.txt", "summary": "s",
                               "content_type": "text/plain", "is_private": True}), 201)["ids"][0]
    assert ok(api.get(f"/bug/{bug_id}/attachment", token=admin_token))["bugs"][str(bug_id)][0]["is_private"] == 1
    assert ok(api.get(f"/bug/{bug_id}/attachment"))["bugs"][str(bug_id)] == []
    assert ok(api.get(f"/bug/{bug_id}/attachment", token=user_token))["bugs"][str(bug_id)] == []
    expect_error(api.get(f"/bug/attachment/{att_id}"), 401, 304)


def test_unknown_attachment_id_is_empty(api):
    assert ok(api.get("/bug/attachment/999999")) == {"attachments": {}, "bugs": {}}


def test_attachments_nonexistent_bug(api):
    expect_error(api.get("/bug/999999/attachment"), 404, 101)


def test_add_attachment_requires_login(api, seed):
    expect_error(api.post("/bug/seed-01/attachment", json={"ids": [1], "data": DATA, "file_name": "a",
                                                            "summary": "s", "content_type": "text/plain"}), 401, 410)


def test_add_attachment_requires_data(api, admin_token, new_bug):
    bug_id = new_bug()
    expect_error(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                          json={"ids": [bug_id], "file_name": "a", "summary": "s", "content_type": "text/plain"}),
                 400, 50, "requires a data argument")


def test_add_attachment_requires_file_name(api, admin_token, new_bug):
    bug_id = new_bug()
    expect_error(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                          json={"ids": [bug_id], "data": DATA, "summary": "s", "content_type": "text/plain"}),
                 400, 603, "You did not specify a file to attach.")


def test_add_attachment_requires_summary(api, admin_token, new_bug):
    bug_id = new_bug()
    expect_error(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                          json={"ids": [bug_id], "data": DATA, "file_name": "a", "content_type": "text/plain"}),
                 400, 604, "You must enter a description for the attachment.")


def test_add_attachment_invalid_content_type(api, admin_token, new_bug):
    bug_id = new_bug()
    expect_error(api.post(f"/bug/{bug_id}/attachment", token=admin_token,
                          json={"ids": [bug_id], "data": DATA, "file_name": "a", "summary": "s",
                                "content_type": "not a mime type"}), 400, 601)


def test_add_attachment_nonexistent_bug(api, admin_token):
    expect_error(api.post("/bug/999999/attachment", token=admin_token,
                          json={"ids": [999999], "data": DATA, "file_name": "a", "summary": "s",
                                "content_type": "text/plain"}), 404, 101)
