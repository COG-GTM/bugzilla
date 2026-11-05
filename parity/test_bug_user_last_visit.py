"""BugUserLastVisit.get / BugUserLastVisit.update  (GET|POST /bug_user_last_visit/{bug_id}).

Perl quirks pinned here (Bugzilla/WebService/BugUserLastVisit.pm on the 5.2 branch):
* ``$user->login(LOGIN_REQUIRED)`` calls Bugzilla::User::login (the login *name* accessor), so the
  login requirement is never enforced: anonymous callers get ``user_not_involved`` (32000), not 410.
* ``get`` calls ``->id`` on the plain bug id scalar (line 78) and dies: every GET answers HTTP 200
  with the ``ERROR_UNKNOWN_FATAL`` envelope (code 100500), whatever the credentials or the id.
* ``Bugzilla::Bug->check({id => ...})`` reports an unknown numeric id as ``improper_bug_id_field_value``
  (100, "You must enter a valid bug number!"), not as ``bug_id_does_not_exist`` (101).
* Only ``/bug_user_last_visit/<digits>`` is a resource; the collection path is 32614.
"""
import pytest

from parity.conftest import expect_error, ok, unique
from parity.normalize import DATETIME, DATETIME_RE, ID, normalize

OTHER_USER = "chen.wei@bugzilla.test"
USER = "alice.ng@bugzilla.test"
PERL_GET_CRASH = ('Can\'t locate object method "id" via package "{id}" (perhaps you forgot to load "{id}"?) '
                  'at /var/www/html/Bugzilla/WebService/BugUserLastVisit.pm line 78.\n')


def visits(resp, status=200):
    """Assert the ``[{id, last_visit_ts}]`` shape and return it with the timestamp normalised."""
    body = ok(resp, status)
    assert isinstance(body, list), body
    out = []
    for row in body:
        if "last_visit_ts" in row:
            assert isinstance(row["last_visit_ts"], str) and DATETIME_RE.match(row["last_visit_ts"]), row
            row = dict(row, last_visit_ts=DATETIME)
        out.append(normalize(row))
    return out


@pytest.fixture
def involved_bug(new_bug):
    """Reporter = admin, assignee = alice (component default): both callers are involved."""
    return new_bug()


@pytest.fixture
def uninvolved_bug(new_bug):
    """Reporter = admin, assignee = chen, no cc: alice is not involved, admin still is."""
    return new_bug(assigned_to=OTHER_USER)


# ---------------------------------------------------------------- update (POST)
def test_update_marks_bug_for_assignee(api, user_token, involved_bug):
    assert visits(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token)) == \
        [{"id": ID, "last_visit_ts": DATETIME}]


def test_update_returns_the_bug_id_and_a_utc_timestamp(api, user_token, involved_bug):
    body = ok(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token))
    assert body[0]["id"] == involved_bug
    assert set(body[0]) == {"id", "last_visit_ts"}


def test_update_twice_moves_the_timestamp_forward(api, user_token, involved_bug):
    first = ok(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token))[0]["last_visit_ts"]
    second = ok(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token))[0]["last_visit_ts"]
    assert second >= first


def test_update_marks_bug_for_reporter(api, admin_token, uninvolved_bug):
    body = ok(api.post(f"/bug_user_last_visit/{uninvolved_bug}", token=admin_token))
    assert [r["id"] for r in body] == [uninvolved_bug]


def test_update_marks_bug_for_cc_member(api, user_token, new_bug):
    bug_id = new_bug(assigned_to=OTHER_USER, cc=[USER])
    assert [r["id"] for r in ok(api.post(f"/bug_user_last_visit/{bug_id}", token=user_token))] == [bug_id]


def test_update_body_ids_override_the_path_id(api, user_token, new_bug):
    a, b = new_bug(), new_bug()
    body = ok(api.post(f"/bug_user_last_visit/{a}", token=user_token, json={"ids": [b, a]}))
    assert [r["id"] for r in body] == [b, a]
    assert body[0]["last_visit_ts"] == body[1]["last_visit_ts"]  # one SELECT NOW() per request


def test_update_body_ids_accepts_a_scalar(api, user_token, new_bug):
    a, b = new_bug(), new_bug()
    assert [r["id"] for r in ok(api.post(f"/bug_user_last_visit/{a}", token=user_token, json={"ids": str(b)}))] == [b]


def test_update_body_ids_accepts_aliases(api, user_token, new_bug):
    alias = unique("bulv").replace(" ", "-")
    bug_id = new_bug(alias=[alias])
    other = new_bug()
    body = ok(api.post(f"/bug_user_last_visit/{other}", token=user_token, json={"ids": [alias]}))
    assert [r["id"] for r in body] == [bug_id]


def test_update_query_string_ids_override_the_body(api, user_token, new_bug):
    a, b = new_bug(), new_bug()
    body = ok(api.post(f"/bug_user_last_visit/{a}", token=user_token, params={"ids": b}, json={"ids": [a]}))
    assert [r["id"] for r in body] == [b]


def test_update_repeated_ids_are_reported_twice(api, user_token, involved_bug):
    body = visits(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                           json={"ids": [involved_bug, involved_bug]}))
    assert body == [{"id": ID, "last_visit_ts": DATETIME}] * 2


def test_update_include_fields(api, user_token, involved_bug):
    assert visits(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                           params={"include_fields": "id"})) == [{"id": ID}]


def test_update_exclude_fields(api, user_token, involved_bug):
    assert visits(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                           params={"exclude_fields": "id"})) == [{"last_visit_ts": DATETIME}]


def test_update_empty_ids_is_param_required(api, user_token, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token, json={"ids": []}),
                 400, 50, "The function requires a ids argument, and that argument was not set.")


def test_update_nested_ids_is_scalar_array_required(api, user_token, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token, json={"ids": [[involved_bug]]}),
                 400, 58, "The ids parameter must be an array of scalars (integers and/or strings).")


def test_update_not_involved_user(api, user_token, uninvolved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{uninvolved_bug}", token=user_token),
                 400, 32000, f"Sorry, but you are not involved with Bug {uninvolved_bug}.")


def test_update_admin_is_not_special(api, admin_token, seed):
    # seed-01: reporter alice, assignee chen, cc bruno
    expect_error(api.post(f"/bug_user_last_visit/{seed['seed-01']}", token=admin_token),
                 400, 32000, f"Sorry, but you are not involved with Bug {seed['seed-01']}.")


def test_update_anonymous_is_not_involved_rather_than_login_required(api, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}"),
                 400, 32000, f"Sorry, but you are not involved with Bug {involved_bug}.")


def test_update_not_involved_message_uses_the_numeric_id_for_aliases(api, user_token, new_bug):
    alias = unique("bulv").replace(" ", "-")
    bug_id = new_bug(alias=[alias], assigned_to=OTHER_USER)
    expect_error(api.post(f"/bug_user_last_visit/{bug_id}", token=user_token, json={"ids": [alias]}),
                 400, 32000, f"Sorry, but you are not involved with Bug {bug_id}.")


def test_update_unknown_bug_id(api, user_token):
    expect_error(api.post("/bug_user_last_visit/99999999", token=user_token),
                 400, 100, "You must enter a valid bug number!")


def test_update_bug_id_zero(api, user_token):
    expect_error(api.post("/bug_user_last_visit/0", token=user_token), 400, 100, "You must enter a valid bug number!")


def test_update_unknown_alias_in_body(api, user_token, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token, json={"ids": ["no-such-alias"]}),
                 400, 100, "'no-such-alias' is not a valid bug number nor an alias to a bug.")


def test_update_unknown_bug_wins_over_later_ids(api, user_token, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                          json={"ids": [99999999, involved_bug]}), 400, 100)


def test_update_not_involved_wins_over_later_ids(api, user_token, involved_bug, uninvolved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                          json={"ids": [uninvolved_bug, involved_bug]}), 400, 32000,
                 f"Sorry, but you are not involved with Bug {uninvolved_bug}.")


def test_update_anonymous_unknown_bug_is_still_checked(api):
    expect_error(api.post("/bug_user_last_visit/99999999"), 400, 100, "You must enter a valid bug number!")


def test_update_bogus_token(api, involved_bug):
    expect_error(api.post(f"/bug_user_last_visit/{involved_bug}", token="1-bogus"), 400, 32000,
                 "The cookies or token provide were not valid or have expired")


def test_update_non_numeric_path_is_not_a_resource(api, user_token):
    expect_error(api.post("/bug_user_last_visit/abc", token=user_token), 404, 32614,
                 "A REST API resource was not found for 'POST /bug_user_last_visit/abc'.")


def test_update_collection_path_is_not_a_resource(api, user_token, involved_bug):
    expect_error(api.post("/bug_user_last_visit", token=user_token, json={"ids": [involved_bug]}), 404, 32614,
                 "A REST API resource was not found for 'POST /bug_user_last_visit'.")


def test_put_and_delete_are_not_resources(api, user_token, involved_bug):
    expect_error(api.put(f"/bug_user_last_visit/{involved_bug}", token=user_token, json={}), 404, 32614,
                 f"A REST API resource was not found for 'PUT /bug_user_last_visit/{involved_bug}'.")
    expect_error(api.request("DELETE", f"/bug_user_last_visit/{involved_bug}", token=user_token), 404, 32614,
                 f"A REST API resource was not found for 'DELETE /bug_user_last_visit/{involved_bug}'.")


# ---------------------------------------------------------------- get (GET): dies in Perl 5.2
def test_get_single_bug_reproduces_the_perl_crash(api, user_token, involved_bug):
    ok(api.post(f"/bug_user_last_visit/{involved_bug}", token=user_token))
    body = expect_error(api.get(f"/bug_user_last_visit/{involved_bug}", token=user_token), 200, 100500)
    assert body["message"] == PERL_GET_CRASH.format(id=involved_bug)


def test_get_anonymous_crashes_the_same_way(api, involved_bug):
    body = expect_error(api.get(f"/bug_user_last_visit/{involved_bug}"), 200, 100500)
    assert body["message"] == PERL_GET_CRASH.format(id=involved_bug)


def test_get_unknown_bug_crashes_before_any_lookup(api, user_token):
    body = expect_error(api.get("/bug_user_last_visit/99999999", token=user_token), 200, 100500)
    assert body["message"] == PERL_GET_CRASH.format(id=99999999)


def test_get_extra_query_ids_do_not_change_the_crash(api, user_token, involved_bug, uninvolved_bug):
    body = expect_error(api.get(f"/bug_user_last_visit/{involved_bug}", token=user_token,
                                params={"ids": uninvolved_bug}), 200, 100500)
    assert body["message"] == PERL_GET_CRASH.format(id=involved_bug)


def test_get_bogus_token_is_rejected_before_the_crash(api, involved_bug):
    expect_error(api.get(f"/bug_user_last_visit/{involved_bug}", token="1-bogus"), 400, 32000)


def test_get_collection_path_is_not_a_resource(api, user_token):
    expect_error(api.get("/bug_user_last_visit", token=user_token), 404, 32614,
                 "A REST API resource was not found for 'GET /bug_user_last_visit'.")


def test_get_non_numeric_path_is_not_a_resource(api, user_token):
    expect_error(api.get("/bug_user_last_visit/seed-01", token=user_token), 404, 32614,
                 "A REST API resource was not found for 'GET /bug_user_last_visit/seed-01'.")
