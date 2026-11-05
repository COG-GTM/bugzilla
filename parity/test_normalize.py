"""Unit tests for the normaliser itself (no server needed)."""

from normalize import normalize


def test_numeric_map_keys_keep_every_record():
    out = normalize({"bugs": {"18": {"comments": ["two"]}, "17": {"comments": ["one"]}}})
    assert out == {"bugs": {"<id:1>": {"comments": ["one"]}, "<id:2>": {"comments": ["two"]}}}


def test_only_id_bearing_changes_are_blurred():
    changes = {"changes": {"depends_on": {"added": "17, 18", "removed": ""},
                           "is_obsolete": {"added": "1", "removed": "0"}}}
    out = normalize(changes)
    assert out["changes"]["depends_on"] == {"added": "<id>", "removed": ""}
    assert out["changes"]["is_obsolete"] == {"added": "1", "removed": "0"}
    history = [{"field_name": "dupe_of", "added": "42", "removed": ""},
               {"field_name": "estimated_time", "added": "2", "removed": "0"}]
    # lists under "changes" are sorted by their JSON form
    assert normalize(history, key="changes") == [
        {"added": "2", "field_name": "estimated_time", "removed": "0"},
        {"added": "<id>", "field_name": "dupe_of", "removed": ""},
    ]


def test_ids_times_tokens_and_identity():
    out = normalize(
        {"id": 7, "bug_id": 7, "time": "2026-01-01T00:00:00Z", "token": "7-abc", "update_token": "x",
         "creator": "admin@bugzilla.test", "message": "Bug #7 by run1", "cc": ["b", "a"]},
        run_id="run1", replacements={"admin@bugzilla.test": "<admin-email>"},
    )
    assert out == {"bug_id": "<id>", "cc": ["a", "b"], "creator": "<admin-email>", "id": "<id>",
                   "message": "Bug #<n> by <run>", "time": "<datetime>", "token": "<token>",
                   "update_token": "<token>"}


def test_identity_replacement_is_whole_token_only():
    out = normalize(
        {"creator": "parity.user@bugzilla.test", "assigned_to": "user@bugzilla.test",
         "cc": ["user@bugzilla.test.example"],
         "message": "There is no user named 'user@bugzilla.test'. Contact user@bugzilla.test."},
        replacements={"user@bugzilla.test": "<admin-email>"},
    )
    assert out == {"assigned_to": "<admin-email>", "cc": ["user@bugzilla.test.example"],
                   "creator": "parity.user@bugzilla.test",
                   "message": "There is no user named '<admin-email>'. Contact <admin-email>."}
