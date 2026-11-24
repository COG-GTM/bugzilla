"""Bugzilla::WebService::Bug read endpoints: get, search."""
import pytest

from conftest import ALICE, BOB, PRODUCT, USER, assert_error
from normalize import normalize


@pytest.fixture(scope="module")
def pair(admin, make_bug):
    """Bug A (assigned to Bob, CC Alice) depends on bug B."""
    b = make_bug("read B", component="Checkout", priority="High", severity="major")
    a = make_bug("read A", assigned_to=BOB, cc=[ALICE], depends_on=[b])
    return {a: "A", b: "B"}


def ids(pair):
    by_name = {name: bug_id for bug_id, name in pair.items()}
    return by_name["A"], by_name["B"]


def expected_bug_a(run_tag):
    return {
        "alias": [], "assigned_to": BOB, "assigned_to_detail": USER["bob"],
        "blocks": [], "cc": [ALICE], "cc_detail": [USER["alice"]],
        "classification": "Unclassified", "component": "Catalog", "creation_time": "<TIMESTAMP>",
        "creator": USER["admin"]["email"], "creator_detail": USER["admin"],
        "deadline": None, "depends_on": ["<BUG:B>"], "dupe_of": None, "flags": [], "groups": [],
        "id": "<BUG:A>", "is_cc_accessible": True, "is_confirmed": True, "is_creator_accessible": True,
        "is_open": True, "keywords": [], "last_change_time": "<TIMESTAMP>", "op_sys": "All",
        "platform": "All", "priority": "Low", "product": PRODUCT, "qa_contact": "", "resolution": "",
        "see_also": [], "severity": "minor", "status": "CONFIRMED", "summary": f"{run_tag} read A",
        "target_milestone": "---", "url": "", "version": "1.0", "whiteboard": "",
    }


def test_get_bug_anonymous(anon, pair, run_tag):
    a, _ = ids(pair)
    resp = anon.get(f"/bug/{a}")
    assert resp.status_code == 200
    assert normalize(resp.json(), pair) == {"bugs": [expected_bug_a(run_tag)], "faults": []}


def test_get_bug_authenticated_adds_time_tracking_and_update_token(admin, pair, run_tag):
    a, _ = ids(pair)
    resp = admin.get(f"/bug/{a}")
    assert resp.status_code == 200
    expected = dict(expected_bug_a(run_tag), actual_time=0, estimated_time=0, remaining_time=0,
                    update_token="<TOKEN>")
    assert normalize(resp.json(), pair) == {"bugs": [expected], "faults": []}


def test_get_bug_dependency_is_visible_from_both_sides(anon, pair):
    a, b = ids(pair)
    resp = anon.get(f"/bug/{b}", include_fields="id,blocks,depends_on,component,priority,severity")
    assert normalize(resp.json(), pair) == {"faults": [], "bugs": [{
        "id": "<BUG:B>", "blocks": ["<BUG:A>"], "depends_on": [],
        "component": "Checkout", "priority": "High", "severity": "major",
    }]}


def test_get_bug_include_fields(anon, pair, run_tag):
    a, _ = ids(pair)
    resp = anon.get(f"/bug/{a}", include_fields="id,summary,status")
    assert normalize(resp.json(), pair) == {"faults": [], "bugs": [
        {"id": "<BUG:A>", "status": "CONFIRMED", "summary": f"{run_tag} read A"}]}


def test_get_bug_exclude_fields(anon, pair, run_tag):
    a, _ = ids(pair)
    resp = anon.get(f"/bug/{a}", exclude_fields="cc,cc_detail,flags")
    expected = {k: v for k, v in expected_bug_a(run_tag).items() if k not in ("cc", "cc_detail", "flags")}
    assert normalize(resp.json(), pair) == {"bugs": [expected], "faults": []}


def test_get_several_bugs_by_id_list(anon, pair, run_tag):
    a, b = ids(pair)
    resp = anon.get("/bug", id=f"{a},{b}", include_fields="id,summary")
    assert resp.status_code == 200
    assert normalize(resp.json(), pair) == {"bugs": [
        {"id": "<BUG:B>", "summary": f"{run_tag} read B"},
        {"id": "<BUG:A>", "summary": f"{run_tag} read A"},
    ]}


def test_get_bug_not_found(anon):
    assert_error(anon.get("/bug/999999"), 404, 101, "Bug #999999 does not exist.")


def test_get_bug_zero(anon):
    assert_error(anon.get("/bug/0"), 404, 101, "Bug #0 does not exist.")


@pytest.mark.parametrize("bad_id", ["notabug", "-1", "1,2"])
def test_get_bug_invalid_id(anon, bad_id):
    assert_error(anon.get(f"/bug/{bad_id}"), 400, 100,
                 f"'{bad_id}' is not a valid bug number nor an alias to a bug.")


def test_get_bug_bad_token(anon, pair):
    a, _ = ids(pair)
    assert_error(anon.get(f"/bug/{a}", token="1-abcdefghij"), 400, 32000,
                 "The cookies or token provide were not valid or have expired. "
                 "You may login again to get new cookies or a new token.")


def test_get_bug_invalid_api_key(anon, pair):
    a, _ = ids(pair)
    assert_error(anon.get(f"/bug/{a}", api_key="x" * 40), 400, 306,
                 "The API key you specified is invalid. Please check that you typed it correctly.")


def test_search_by_summary(anon, pair, run_tag):
    resp = anon.get("/bug", summary=run_tag, include_fields="id,summary,status,product,component")
    assert resp.status_code == 200
    found = [b for b in normalize(resp.json(), pair)["bugs"] if b["id"].startswith("<BUG:")]
    assert found == [
        {"id": "<BUG:B>", "summary": f"{run_tag} read B", "status": "CONFIRMED",
         "product": PRODUCT, "component": "Checkout"},
        {"id": "<BUG:A>", "summary": f"{run_tag} read A", "status": "CONFIRMED",
         "product": PRODUCT, "component": "Catalog"},
    ]


def test_search_full_bug_shape_matches_get(anon, pair, run_tag):
    resp = anon.get("/bug", summary=f"{run_tag} read", component="Catalog")
    assert normalize(resp.json(), pair) == {"bugs": [expected_bug_a(run_tag)]}


def test_search_combines_filters(anon, pair, run_tag):
    resp = anon.get("/bug", summary=f"{run_tag} read", component="Checkout", priority="High",
                    assigned_to=ALICE, include_fields="id")
    assert normalize(resp.json(), pair) == {"bugs": [{"id": "<BUG:B>"}]}


def test_search_limit_and_offset(anon, pair, run_tag):
    params = {"summary": f"{run_tag} read", "include_fields": "id"}
    assert normalize(anon.get("/bug", limit=1, **params).json(), pair) == {"bugs": [{"id": "<BUG:B>"}]}
    assert normalize(anon.get("/bug", limit=1, offset=1, **params).json(), pair) == {"bugs": [{"id": "<BUG:A>"}]}


def test_search_no_match(anon):
    resp = anon.get("/bug", summary="zzzz-no-such-summary-zzzz")
    assert (resp.status_code, resp.json()) == (200, {"bugs": []})


def test_search_unknown_product_matches_nothing(anon):
    resp = anon.get("/bug", product="NoSuchProduct")
    assert (resp.status_code, resp.json()) == (200, {"bugs": []})


def test_search_unknown_status_value_is_ignored(anon, pair, run_tag):
    # Perl drops an unknown status instead of rejecting it.
    resp = anon.get("/bug", summary=f"{run_tag} read", status="BOGUS", include_fields="id")
    assert resp.status_code == 200
    assert normalize(resp.json(), pair) == {"bugs": [{"id": "<BUG:B>"}, {"id": "<BUG:A>"}]}


def test_search_unknown_parameter_is_ignored(anon, pair, run_tag):
    resp = anon.get("/bug", summary=f"{run_tag} read", nosuchfield="1", include_fields="id")
    assert resp.status_code == 200
    assert normalize(resp.json(), pair) == {"bugs": [{"id": "<BUG:B>"}, {"id": "<BUG:A>"}]}


@pytest.mark.parametrize("field", ["creation_time", "last_change_time"])
def test_search_invalid_date(anon, field):
    assert_error(anon.get("/bug", **{field: "notadate"}), 400, 56, "'notadate' is not a legal date.")


def test_search_bad_token(anon):
    assert_error(anon.get("/bug", summary="x", token="1-abcdefghij"), 400, 32000,
                 "The cookies or token provide were not valid or have expired. "
                 "You may login again to get new cookies or a new token.")
