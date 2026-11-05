"""Bug.fields and Bug.legal_values (GET /field/bug...)."""
from parity.conftest import expect_error, ok
from parity.normalize import ID, normalize


def test_all_fields_have_names_and_ids(api):
    fields = ok(api.get("/field/bug"))["fields"]
    names = {f["name"] for f in fields}
    assert {"bug_id", "short_desc", "product", "component", "bug_status", "resolution",
            "priority", "bug_severity", "assigned_to", "reporter", "cc"} <= names
    for f in fields:
        assert set(f) >= {"id", "name", "display_name", "type", "is_custom", "is_mandatory",
                          "is_on_bug_entry", "visibility_field", "visibility_values"}, f["name"]
        assert isinstance(f["id"], int) and isinstance(f["type"], int)


def test_priority_field_and_values(api):
    body = ok(api.get("/field/bug/priority"))
    assert len(body["fields"]) == 1
    f = body["fields"][0]
    assert normalize({k: f[k] for k in ("id", "name", "display_name", "type", "is_custom", "is_mandatory",
                                        "is_on_bug_entry", "value_field", "visibility_field", "visibility_values")}) == {
        "id": ID, "name": "priority", "display_name": "Priority", "type": 2, "is_custom": False,
        "is_mandatory": False, "is_on_bug_entry": False, "value_field": None,
        "visibility_field": None, "visibility_values": [],
    }
    assert [v["name"] for v in f["values"]] == ["Highest", "High", "Normal", "Low", "Lowest", "---"]
    assert [v["sort_key"] for v in f["values"]] == [100, 200, 300, 400, 500, 600]
    assert all(set(v) == {"name", "sort_key", "sortkey", "visibility_values"} for v in f["values"])


def test_field_by_id_equals_field_by_name(api):
    by_name = ok(api.get("/field/bug/priority"))["fields"][0]
    by_id = ok(api.get(f"/field/bug/{by_name['id']}"))["fields"][0]
    assert by_id == by_name


def test_status_field_values_carry_transitions(api):
    f = ok(api.get("/field/bug/bug_status"))["fields"][0]
    names = {v["name"]: v for v in f["values"]}
    # Perl emits an extra pseudo-value with name null describing the transitions allowed for a new bug
    assert set(names) == {None, "UNCONFIRMED", "CONFIRMED", "IN_PROGRESS", "RESOLVED", "VERIFIED"}
    assert {t["name"] for t in names[None]["can_change_to"]} >= {"CONFIRMED", "IN_PROGRESS"}
    assert names["RESOLVED"]["is_open"] is False and names["CONFIRMED"]["is_open"] is True
    assert {"name": "RESOLVED", "comment_required": False} in names["IN_PROGRESS"]["can_change_to"] or \
        any(t["name"] == "RESOLVED" for t in names["IN_PROGRESS"]["can_change_to"])


def test_unknown_field(api):
    expect_error(api.get("/field/bug/nosuchfield"), 404, 51, "There is no field named 'nosuchfield'.")


def test_legal_values_priority(api):
    assert ok(api.get("/field/bug/priority/values")) == {"values": ["Highest", "High", "Normal", "Low", "Lowest", "---"]}


def test_legal_values_severity(api):
    assert ok(api.get("/field/bug/bug_severity/values")) == {
        "values": ["blocker", "critical", "major", "normal", "minor", "trivial", "enhancement"]}


def test_legal_values_status(api):
    assert ok(api.get("/field/bug/bug_status/values")) == {
        "values": ["UNCONFIRMED", "CONFIRMED", "IN_PROGRESS", "RESOLVED", "VERIFIED"]}


def test_legal_values_resolution(api):
    values = ok(api.get("/field/bug/resolution/values"))["values"]
    assert values[0] == "" and {"FIXED", "INVALID", "WONTFIX", "DUPLICATE", "WORKSFORME"} <= set(values)


def test_legal_values_unknown_field(api):
    expect_error(api.get("/field/bug/nosuchfield/values"), 400, 108, 'Can\'t use "nosuchfield" as a field name.')


def test_legal_values_product_specific_requires_product_id(api, seed_product_id):
    pid = seed_product_id
    assert ok(api.get(f"/field/bug/component/{pid}/values")) == {"values": ["Contacts", "Mobile App", "Pipeline"]}
    assert ok(api.get(f"/field/bug/version/{pid}/values")) == {"values": ["2.4"]}


def test_legal_values_unknown_product(api):
    expect_error(api.get("/field/bug/component/999999/values"), 401, 106)
