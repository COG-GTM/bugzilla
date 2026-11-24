"""Bug.comments, Bug.add_comment and Bug.history."""
from parity.conftest import expect_error, ok
from parity.normalize import DATETIME, ID, normalize

SEED01_COMMENTS = [
    ("alice.ng@bugzilla.test", "Importing contacts.csv with accented characters silently skips those rows. Encoding is UTF-8 with BOM."),
    ("chen.wei@bugzilla.test", "Reproduced with a UTF-8 file containing 'José'. The parser opens the file in latin-1."),
]


def test_comments_for_bug(api, seed):
    body = ok(api.get("/bug/seed-01/comment"))
    assert set(body) == {"bugs", "comments"} and body["comments"] == {}
    bid = str(seed["seed-01"])
    assert set(body["bugs"]) == {bid}
    comments = body["bugs"][bid]["comments"]
    assert len(comments) >= 3
    for n, c in enumerate(comments):
        assert c["count"] == n
        assert normalize(c) == {
            "id": ID, "bug_id": ID, "attachment_id": None, "count": n, "tags": [],
            "is_private": False, "creator": c["creator"], "text": c["text"],
            "time": DATETIME, "creation_time": DATETIME,
        }
    assert [(c["creator"], c["text"]) for c in comments[:2]] == SEED01_COMMENTS


def test_comments_by_comment_id(api, seed):
    first = ok(api.get("/bug/seed-01/comment"))["bugs"][str(seed["seed-01"])]["comments"][0]
    body = ok(api.get(f"/bug/comment/{first['id']}"))
    assert body == {"bugs": {}, "comments": {str(first["id"]): first}}


def test_comments_bad_comment_id(api):
    expect_error(api.get("/bug/comment/999999"), 400, 111, "999999 is not a valid comment id.")


def test_comments_nonexistent_bug(api):
    expect_error(api.get("/bug/999999/comment"), 404, 101)


def test_add_comment(api, admin_token, new_bug):
    bug_id = new_bug()
    body = ok(api.post(f"/bug/{bug_id}/comment", token=admin_token, json={"comment": "hello from parity"}), 201)
    assert normalize(body) == {"id": ID}
    comments = ok(api.get(f"/bug/{bug_id}/comment"))["bugs"][str(bug_id)]["comments"]
    assert [c["text"] for c in comments] == ["created by the parity suite", "hello from parity"]
    assert comments[1]["id"] == body["id"] and comments[1]["count"] == 1
    assert comments[1]["creator"] == "admin@bugzilla.test"


def test_add_comment_requires_login(api, seed):
    expect_error(api.post("/bug/seed-01/comment", json={"comment": "x"}), 401, 410)


def test_add_comment_requires_comment(api, admin_token, seed):
    expect_error(api.post("/bug/seed-01/comment", token=admin_token, json={}), 400, 50,
                 "requires a comment argument")


def test_add_comment_nonexistent_bug(api, admin_token):
    expect_error(api.post("/bug/999999/comment", token=admin_token, json={"comment": "x"}), 404, 101)


def test_private_comment_hidden_from_non_insiders(api, admin_token, user_token, new_bug):
    bug_id = new_bug()
    cid = ok(api.post(f"/bug/{bug_id}/comment", token=admin_token,
                      json={"comment": "insider only", "is_private": True}), 201)["id"]
    as_admin = ok(api.get(f"/bug/{bug_id}/comment", token=admin_token))["bugs"][str(bug_id)]["comments"]
    assert [c["is_private"] for c in as_admin] == [False, True]
    anon = ok(api.get(f"/bug/{bug_id}/comment"))["bugs"][str(bug_id)]["comments"]
    assert [c["id"] for c in anon] == [as_admin[0]["id"]]
    as_user = ok(api.get(f"/bug/{bug_id}/comment", token=user_token))["bugs"][str(bug_id)]["comments"]
    assert [c["id"] for c in as_user] == [as_admin[0]["id"]]
    expect_error(api.get(f"/bug/comment/{cid}"), 401, 110)


def test_history_seed_bug(api, seed):
    body = ok(api.get("/bug/seed-01/history"))
    assert set(body) == {"bugs"}
    entry = body["bugs"][0]
    assert entry["id"] == seed["seed-01"] and entry["alias"] == ["seed-01"]
    changes = [c for h in entry["history"] for c in h["changes"]]
    assert {"field_name": "blocks", "removed": "", "added": str(seed["seed-03"])} in changes
    for h in entry["history"]:
        assert set(h) == {"who", "when", "changes"}
        assert normalize({"when": h["when"]}) == {"when": DATETIME}


def test_history_records_updates(api, admin_token, new_bug):
    bug_id = new_bug()
    assert ok(api.get(f"/bug/{bug_id}/history")) == {"bugs": [{"id": bug_id, "alias": [], "history": []}]}
    ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"status": "IN_PROGRESS", "priority": "High"}))
    hist = ok(api.get(f"/bug/{bug_id}/history"))["bugs"][0]["history"]
    assert len(hist) == 1 and hist[0]["who"] == "admin@bugzilla.test"
    assert sorted(hist[0]["changes"], key=lambda c: c["field_name"]) == [
        {"field_name": "priority", "removed": "Normal", "added": "High"},
        {"field_name": "status", "removed": "CONFIRMED", "added": "IN_PROGRESS"},
    ]


def test_history_new_since_filter(api, admin_token, new_bug):
    bug_id = new_bug()
    ok(api.put(f"/bug/{bug_id}", token=admin_token, json={"priority": "Low"}))
    body = ok(api.get(f"/bug/{bug_id}/history", params={"new_since": "2099-01-01"}))
    assert body == {"bugs": [{"id": bug_id, "alias": [], "history": []}]}
