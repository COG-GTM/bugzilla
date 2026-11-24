from normalize import normalize


def test_timestamps_tokens_and_ids():
    assert normalize({
        "id": 7, "token": "7-AbCdEfGhIj", "update_token": "1795-x", "creation_time": "2026-11-24T13:38:08Z",
        "summary": "2026-11-24 is not a timestamp", "count": 3, "is_open": True, "dupe_of": None,
    }) == {
        "id": "<ID>", "token": "<TOKEN>", "update_token": "<TOKEN>", "creation_time": "<TIMESTAMP>",
        "summary": "2026-11-24 is not a timestamp", "count": 3, "is_open": True, "dupe_of": None,
    }


def test_named_bugs_keep_their_identity():
    bugs = {24: "A", 25: "B"}
    assert normalize({
        "bugs": {"24": [{"bug_id": 24, "attachment_id": None}]},
        "depends_on": [25, 99], "ids": [5],
        "changes": {"depends_on": {"added": "25, 99", "removed": ""}, "summary": {"added": "25", "removed": "x"}},
        "history": [{"field_name": "blocks", "added": "24", "removed": ""}],
    }, bugs) == {
        "bugs": {"<BUG:A>": [{"bug_id": "<BUG:A>", "attachment_id": None}]},
        "depends_on": ["<BUG:B>", "<ID>"], "ids": ["<ID>"],
        "changes": {"depends_on": {"added": "<BUG:B>, <ID>", "removed": ""}, "summary": {"added": "25", "removed": "x"}},
        "history": [{"field_name": "blocks", "added": "<BUG:A>", "removed": ""}],
    }


def test_history_changes_are_sorted_by_field_name():
    changes = [{"field_name": "status", "added": "x"}, {"field_name": "cc", "added": "y"}]
    assert normalize({"changes": changes}) == {"changes": changes[::-1]}


def test_attachment_id_in_comment_text():
    assert normalize({"text": "Created attachment 12\nlog"}) == {"text": "Created attachment <ID>\nlog"}
