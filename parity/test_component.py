"""Component.create (POST /component).

Bugzilla 5.2 exposes a single Component REST resource: ``POST /component`` (Bugzilla::WebService::Component::create,
success code 201). There is no Component.get/update, so every other verb/path on /component is a 32614.
Side effects are verified black-box through Bug.legal_values / Bug.fields (component list, is_active) and Bug.create
(default assignee / default CC are copied onto new bugs).
"""
import pytest

from parity.conftest import SEED_COMPONENT, SEED_PRODUCT, expect_error, ok, unique
from parity.normalize import ID, normalize

# Components are created in Bugzilla's stock product so the seeded "Orbit CRM" component list asserted by
# test_bug_fields stays untouched (there is no Component delete in the REST API).
PRODUCT = "TestProduct"
COMPONENT = "TestComponent"
VERSION = "unspecified"
ASSIGNEE = "alice.ng@bugzilla.test"
QA = "bruno.costa@bugzilla.test"
CC = ["chen.wei@bugzilla.test", "dana.okafor@bugzilla.test"]


@pytest.fixture(scope="session")
def comp_product_id(api):
    """Numeric id of PRODUCT, discovered through Bug.legal_values (the Product module is a later slice)."""
    for pid in range(1, 50):
        r = api.get(f"/field/bug/component/{pid}/values")
        if r.status_code == 200 and COMPONENT in r.json()["values"]:
            return pid
    pytest.fail(f"could not determine the id of product {PRODUCT!r}")


def body(**over):
    b = {"product": PRODUCT, "name": unique("comp"), "description": "created by the parity suite",
         "default_assignee": ASSIGNEE}
    b.update(over)
    return b


def component_values(api, comp_product_id):
    return ok(api.get(f"/field/bug/component/{comp_product_id}/values"))["values"]


def component_flags(api, token):
    f = ok(api.get("/field/bug/component", token=token))["fields"][0]
    return {v["name"]: v["is_active"] for v in f["values"]}


def bug_in(api, admin_token, component, **over):
    b = {"product": PRODUCT, "component": component, "version": VERSION, "summary": unique("bug"),
         "description": "bug in a component created by the parity suite", "op_sys": "All", "platform": "All",
         "priority": "Normal", "severity": "normal"}
    b.update(over)
    return api.post("/bug", token=admin_token, json=b)


# ----------------------------------------------------------------------------- happy paths

def test_create_minimal_returns_201_with_id(api, admin_token, comp_product_id):
    b = body()
    r = api.post("/component", token=admin_token, json=b)
    assert normalize(ok(r, 201)) == {"id": ID}
    assert b["name"] in component_values(api, comp_product_id)
    assert component_flags(api, admin_token)[b["name"]] is True
    # default assignee is copied onto bugs filed in the component, no default CC
    bug_id = ok(bug_in(api, admin_token, b["name"]))["id"]
    bug = ok(api.get(f"/bug/{bug_id}", token=admin_token, params={"include_fields": "assigned_to,cc,component"}))["bugs"][0]
    assert (bug["assigned_to"], bug["cc"], bug["component"]) == (ASSIGNEE, [], b["name"])


def test_create_full_trims_name_and_description_and_sets_cc(api, admin_token, comp_product_id):
    name = unique("full")
    b = body(name=f"  {name}  ", description="  padded  ", default_assignee=QA, default_qa_contact=ASSIGNEE,
             default_cc=CC + [CC[0]], is_open=True)
    ok(api.post("/component", token=admin_token, json=b), 201)
    assert name in component_values(api, comp_product_id)
    # the trimmed name is what got stored: an exact-name duplicate is rejected
    expect_error(api.post("/component", token=admin_token, json=body(name=name)), 400, 1200,
                 f"The {PRODUCT} product already has a component named {name}.")
    bug_id = ok(bug_in(api, admin_token, name))["id"]
    bug = ok(api.get(f"/bug/{bug_id}", token=admin_token, params={"include_fields": "assigned_to,cc"}))["bugs"][0]
    assert bug["assigned_to"] == QA
    assert sorted(bug["cc"]) == sorted(CC)  # duplicates in default_cc collapse


def test_create_is_open_false_makes_component_inactive(api, admin_token, comp_product_id):
    b = body(is_open=False)
    ok(api.post("/component", token=admin_token, json=b), 201)
    assert b["name"] in component_values(api, comp_product_id)
    assert component_flags(api, admin_token)[b["name"]] is False


@pytest.mark.parametrize("value,active", [("0", False), (None, False), ("false", True), (1, True), ("", False)])
def test_create_is_open_uses_perl_truthiness(api, admin_token, value, active):
    # Perl: "0", "", undef are false; any other string (even "false") is true
    b = body(is_open=value)
    ok(api.post("/component", token=admin_token, json=b), 201)
    assert component_flags(api, admin_token)[b["name"]] is active


def test_create_accepts_raw_perl_field_names(api, admin_token):
    # translate() maps default_assignee->initialowner etc. but passes unmapped keys through, so the DB
    # column names are accepted as well
    b = body(); del b["default_assignee"]
    b["initialowner"] = QA
    b["initial_cc"] = [CC[1]]
    b["isactive"] = 1
    ok(api.post("/component", token=admin_token, json=b), 201)
    bug_id = ok(bug_in(api, admin_token, b["name"]))["id"]
    bug = ok(api.get(f"/bug/{bug_id}", token=admin_token, params={"include_fields": "assigned_to,cc"}))["bugs"][0]
    assert (bug["assigned_to"], bug["cc"]) == (QA, [CC[1]])


def test_create_with_query_string_params_only(api, admin_token):
    b = body()
    r = api.post("/component", token=admin_token, params=b)
    assert normalize(ok(r, 201)) == {"id": ID}


def test_create_matches_users_and_product_case_insensitively(api, admin_token):
    b = body(product=PRODUCT.lower(), default_assignee=ASSIGNEE.upper(), default_cc=[CC[0].upper()])
    ok(api.post("/component", token=admin_token, json=b), 201)
    bug_id = ok(bug_in(api, admin_token, b["name"]))["id"]
    bug = ok(api.get(f"/bug/{bug_id}", token=admin_token, params={"include_fields": "assigned_to,cc"}))["bugs"][0]
    assert (bug["assigned_to"], bug["cc"]) == (ASSIGNEE, [CC[0]])


def test_create_qa_contact_ignored_when_useqacontact_off(api, admin_token):
    # useqacontact is off in this installation: the value is not even validated
    ok(api.post("/component", token=admin_token, json=body(default_qa_contact="nobody@bugzilla.test")), 201)


def test_create_with_create_series_flag(api, admin_token):
    # undocumented Bugzilla::Component validator reachable through translate(): creates two charting series
    ok(api.post("/component", token=admin_token, json=body(create_series=1)), 201)


def test_create_with_empty_default_cc(api, admin_token):
    ok(api.post("/component", token=admin_token, json=body(default_cc=[])), 201)


def test_create_with_login_password_in_query(api):
    r = api.post("/component", params={"login": "admin@bugzilla.test", "password": "password01!"}, json=body())
    assert normalize(ok(r, 201)) == {"id": ID}


# ----------------------------------------------------------------------------- auth

def test_create_requires_login(api):
    expect_error(api.post("/component", json=body()), 401, 410, "You must log in before using this part of Bugzilla.")


def test_create_requires_editcomponents(api, user_token):
    expect_error(api.post("/component", token=user_token, json=body()), 401, 304,
                 "Sorry, you aren't a member of the 'editcomponents' group, and so you are not authorized to add, "
                 "modify or delete components.")


def test_create_with_unknown_token(api):
    expect_error(api.post("/component", token="1-abcdefghij", json=body()), 400, 32000,
                 "The cookies or token provide were not valid or have expired.")


def test_create_with_bad_password(api):
    expect_error(api.post("/component", params={"login": "admin@bugzilla.test", "password": "wrong"}, json=body()),
                 401, 300, "The login or password you entered is not valid.")


# ----------------------------------------------------------------------------- product

def test_create_missing_product(api, admin_token):
    b = body(); del b["product"]
    expect_error(api.post("/component", token=admin_token, json=b), 400, 50, "You must select/enter a product.")


def test_create_blank_product(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(product="  ")), 400, 50,
                 "You must select/enter a product.")


def test_create_unknown_product(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(product="No Such Product")), 401, 106,
                 "Either the product 'No Such Product' does not exist or you don't have access to it.")


# ----------------------------------------------------------------------------- name

def test_create_duplicate_name_is_case_insensitive_and_reports_existing_name(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(name=COMPONENT.upper())), 400, 1200,
                 f"The {PRODUCT} product already has a component named {COMPONENT}.")


@pytest.mark.parametrize("name", ["", "   ", None, "missing"])
def test_create_blank_name(api, admin_token, name):
    b = body(name=name)
    if name == "missing":
        del b["name"]
    expect_error(api.post("/component", token=admin_token, json=b), 400, 32000,
                 "You must enter a name for this new component.")


def test_create_name_too_long(api, admin_token):
    name = "x" * 65
    expect_error(api.post("/component", token=admin_token, json=body(name=name)), 400, 105,
                 f"The name of a component is limited to 64 characters. '{name}' is too long (65 characters).")


def test_create_name_of_64_chars_is_accepted(api, admin_token):
    ok(api.post("/component", token=admin_token, json=body(name=unique("len").ljust(64, "z"))), 201)


# ----------------------------------------------------------------------------- description / assignee / cc

@pytest.mark.parametrize("description", ["", "  ", None, "missing"])
def test_create_blank_description(api, admin_token, description):
    b = body(description=description)
    if description == "missing":
        del b["description"]
    expect_error(api.post("/component", token=admin_token, json=b), 400, 32000,
                 "You must enter a non-blank description for this component.")


@pytest.mark.parametrize("assignee", ["", None, "missing"])
def test_create_missing_default_assignee(api, admin_token, assignee):
    b = body(default_assignee=assignee)
    if assignee == "missing":
        del b["default_assignee"]
    expect_error(api.post("/component", token=admin_token, json=b), 400, 32000,
                 "A default assignee is required for this component.")


def test_create_whitespace_default_assignee(api, admin_token):
    # "  " is Perl-true so it passes component_need_initialowner, then User->check trims it to ''
    expect_error(api.post("/component", token=admin_token, json=body(default_assignee="  ")), 400, 50,
                 "You must select/enter a user.")


def test_create_unknown_default_assignee(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(default_assignee="nobody@bugzilla.test")), 404, 51,
                 "There is no user named 'nobody@bugzilla.test'. Either you mis-typed the name or that user has not "
                 "yet registered for a Bugzilla account.")


def test_create_unknown_default_cc(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(default_cc=[CC[0], "nobody@bugzilla.test"])),
                 401, 504, "The name nobody@bugzilla.test is not a valid username. Either you misspelled it, or the "
                 "person has not registered for a Bugzilla account.")


def test_create_empty_string_in_default_cc(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(default_cc=[""])), 401, 504,
                 "is not a valid username.")


def test_create_default_cc_must_be_a_list(api, admin_token):
    # Perl dereferences default_cc as an array; a scalar dies inside the validator *after* the REST layer already
    # chose the 201 success code, so the error envelope is returned with HTTP 201 and the generic code 100500.
    r = api.post("/component", token=admin_token, json=body(default_cc=CC[0]))
    assert r.status_code == 201, r.text
    err = r.json()
    assert set(err) == {"error", "code", "message", "documentation"} and err["error"] is True
    assert err["code"] == 100500
    assert err["message"].startswith(f'Can\'t use string ("{CC[0]}") as an ARRAY ref')


# ----------------------------------------------------------------------------- unknown params

def test_create_rejects_unknown_param(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(foo=1)), 400, 53,
                 "foo is not a valid parameter for the Bugzilla::Component::create function.")


def test_create_rejects_include_fields(api, admin_token):
    # Component.create does not filter its result, so include_fields is just an unknown column
    expect_error(api.post("/component", token=admin_token, params={"include_fields": "id"}, json=body()), 400, 53,
                 "include_fields is not a valid parameter for the Bugzilla::Component::create function.")


# ----------------------------------------------------------------------------- validation order

def test_product_is_checked_before_everything_else(api, admin_token):
    expect_error(api.post("/component", token=admin_token,
                          json=body(product="Nope", name="", description="", default_assignee="nobody@x")), 401, 106)


def test_description_is_validated_before_cc_assignee_and_name(api, admin_token):
    expect_error(api.post("/component", token=admin_token,
                          json=body(name="", description="", default_assignee="nobody@x", default_cc=["nobody@y"])),
                 400, 32000, "You must enter a non-blank description for this component.")


def test_cc_is_validated_before_assignee_and_name(api, admin_token):
    expect_error(api.post("/component", token=admin_token,
                          json=body(name="", default_assignee="nobody@x", default_cc=["nobody@y"])), 401, 504)


def test_assignee_is_validated_before_name(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(name=COMPONENT, default_assignee="nobody@x")),
                 404, 51)


def test_duplicate_name_is_reported_before_unknown_params(api, admin_token):
    expect_error(api.post("/component", token=admin_token, json=body(name=COMPONENT, foo=1)), 400, 1200)


# ----------------------------------------------------------------------------- no other Component resources in 5.2

@pytest.mark.parametrize("method,path", [("GET", "/component"), ("GET", "/component/1"), ("PUT", "/component/1"),
                                         ("PUT", f"/component/{SEED_PRODUCT}/{SEED_COMPONENT}"),
                                         ("DELETE", "/component/1")])
def test_only_post_component_exists(api, admin_token, method, path):
    r = api.request(method, path, token=admin_token, json={"name": "x"} if method != "GET" else None)
    expect_error(r, 404, 32614, f"A REST API resource was not found for '{method} {path}'.")
