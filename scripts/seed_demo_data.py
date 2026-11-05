#!/usr/bin/env python3
"""Seed a Bugzilla instance with realistic demo data through the REST API.

Creates 2 products x 3 components, 5 users and ~20 bugs with comments,
CC lists, dependencies and status changes. Idempotent: every object is
looked up by name/alias first, so re-running against a seeded instance
is a no-op.

Only the standard library is used so the script runs anywhere Python 3
does.

    python3 scripts/seed_demo_data.py \
        --base-url http://127.0.0.1:8080/rest.cgi \
        --login admin@bugzilla.test --password 'password01!'
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8080/rest.cgi"
USER_PASSWORD = "DemoPassw0rd!"

PRODUCTS = [
    {
        "name": "Orbit Web",
        "description": "Customer-facing web application and public API.",
        "version": "2.4",
        "default_milestone": "2.5",
        "components": [
            ("Frontend", "React single-page app, routing, state and rendering."),
            ("API", "Public REST API, auth and rate limiting."),
            ("Billing", "Invoicing, subscriptions and payment-provider integration."),
        ],
    },
    {
        "name": "Orbit Mobile",
        "description": "iOS and Android clients built on a shared core.",
        "version": "1.9",
        "default_milestone": "2.0",
        "components": [
            ("iOS", "Swift client, push notifications and App Store release."),
            ("Android", "Kotlin client, background sync and Play release."),
            ("Sync Engine", "Shared offline cache and conflict resolution."),
        ],
    },
]

USERS = [
    ("alice.dev@bugzilla.test", "Alice Nakamura"),
    ("bob.qa@bugzilla.test", "Bob Okafor"),
    ("carol.pm@bugzilla.test", "Carol Lindqvist"),
    ("dave.ops@bugzilla.test", "Dave Mbeki"),
    ("erin.mobile@bugzilla.test", "Erin Castellanos"),
]

ALICE, BOB, CAROL, DAVE, ERIN = (u[0] for u in USERS)

# alias, product, component, summary, severity, priority, reporter, assignee,
# cc, status flow, comments
BUGS = [
    dict(alias="seed-001", product="Orbit Web", component="Frontend",
         summary="Login page flashes unstyled content on slow connections",
         severity="normal", priority="High", reporter=BOB, assigned_to=ALICE,
         cc=[CAROL], status="IN_PROGRESS",
         comments=[(ALICE, "Reproduced on 3G throttling. The critical CSS is loaded async."),
                   (BOB, "Also visible on Safari 17 without throttling.")]),
    dict(alias="seed-002", product="Orbit Web", component="Frontend",
         summary="Dashboard chart tooltips overflow the viewport on narrow screens",
         severity="minor", priority="Normal", reporter=CAROL, assigned_to=ALICE,
         cc=[BOB], status="CONFIRMED",
         comments=[(ALICE, "Needs a boundary-aware tooltip placement; will reuse the popover util.")]),
    dict(alias="seed-003", product="Orbit Web", component="Frontend",
         summary="Keyboard focus is lost after closing the settings modal",
         severity="normal", priority="Normal", reporter=BOB, assigned_to=ALICE,
         cc=[], status="RESOLVED", resolution="FIXED",
         comments=[(ALICE, "Fixed by restoring focus to the trigger element on close."),
                   (BOB, "Verified on the staging build, thanks.")]),
    dict(alias="seed-004", product="Orbit Web", component="API",
         summary="GET /v1/orders returns 500 when the filter contains a unicode character",
         severity="major", priority="Highest", reporter=DAVE, assigned_to=ALICE,
         cc=[BOB, CAROL], status="IN_PROGRESS",
         comments=[(DAVE, "Stack trace points at the query builder encoding the filter as latin-1."),
                   (ALICE, "Confirmed, the parameter decoder bypasses the UTF-8 path. Patch in review.")]),
    dict(alias="seed-005", product="Orbit Web", component="API",
         summary="Rate limiter counts failed auth attempts against the wrong client",
         severity="critical", priority="Highest", reporter=DAVE, assigned_to=DAVE,
         cc=[ALICE], status="CONFIRMED",
         comments=[(DAVE, "Key is derived from X-Forwarded-For; behind the new LB that is always the LB IP.")]),
    dict(alias="seed-006", product="Orbit Web", component="API",
         summary="Pagination cursor leaks internal row ids",
         severity="normal", priority="Normal", reporter=ALICE, assigned_to=ALICE,
         cc=[DAVE], status="RESOLVED", resolution="WONTFIX",
         comments=[(ALICE, "Row ids are not sensitive and the cursor is opaque to clients by contract."),
                   (DAVE, "Agreed, closing as WONTFIX.")]),
    dict(alias="seed-007", product="Orbit Web", component="API",
         summary="Add ETag support to GET /v1/customers/{id}",
         severity="enhancement", priority="Low", reporter=CAROL, assigned_to=ALICE,
         cc=[], status="CONFIRMED", comments=[]),
    dict(alias="seed-008", product="Orbit Web", component="Billing",
         summary="Invoice PDF shows the wrong currency symbol for CHF accounts",
         severity="major", priority="High", reporter=CAROL, assigned_to=ALICE,
         cc=[BOB], status="RESOLVED", resolution="FIXED",
         comments=[(ALICE, "Locale fallback was picking de-DE instead of de-CH."),
                   (BOB, "Checked three CHF invoices on staging, all correct now.")]),
    dict(alias="seed-009", product="Orbit Web", component="Billing",
         summary="Proration is off by one day when a plan changes on the last day of the month",
         severity="major", priority="High", reporter=BOB, assigned_to=ALICE,
         cc=[CAROL, DAVE], status="IN_PROGRESS",
         comments=[(BOB, "Steps: subscribe on Jan 31, upgrade on Feb 28, see 29 days charged."),
                   (ALICE, "The month-end clamp happens before the timezone conversion.")]),
    dict(alias="seed-010", product="Orbit Web", component="Billing",
         summary="Webhook retries from the payment provider create duplicate payments",
         severity="blocker", priority="Highest", reporter=DAVE, assigned_to=DAVE,
         cc=[ALICE, CAROL], status="CONFIRMED",
         comments=[(DAVE, "We need an idempotency key on the webhook handler before the next release.")]),
    dict(alias="seed-011", product="Orbit Web", component="Billing",
         summary="Expose refunds in the customer billing history page",
         severity="enhancement", priority="Normal", reporter=CAROL, assigned_to=ALICE,
         cc=[], status="CONFIRMED", comments=[]),
    dict(alias="seed-012", product="Orbit Mobile", component="iOS",
         summary="App crashes on launch after upgrading from 1.7 with an empty cache",
         severity="blocker", priority="Highest", reporter=BOB, assigned_to=ERIN,
         cc=[CAROL, DAVE], status="IN_PROGRESS",
         comments=[(BOB, "Crash log attached in the internal tracker, 100% reproducible."),
                   (ERIN, "Migration assumes at least one cached workspace. Fix in progress.")]),
    dict(alias="seed-013", product="Orbit Mobile", component="iOS",
         summary="Push notification deep links open the wrong tab",
         severity="normal", priority="Normal", reporter=CAROL, assigned_to=ERIN,
         cc=[BOB], status="CONFIRMED",
         comments=[(ERIN, "Universal link routing ignores the tab query parameter.")]),
    dict(alias="seed-014", product="Orbit Mobile", component="iOS",
         summary="Dark mode: unreadable text in the invoice list",
         severity="minor", priority="Low", reporter=BOB, assigned_to=ERIN,
         cc=[], status="RESOLVED", resolution="FIXED",
         comments=[(ERIN, "Used a hard-coded colour instead of the semantic label colour.")]),
    dict(alias="seed-015", product="Orbit Mobile", component="Android",
         summary="Background sync drains battery when the device is offline",
         severity="major", priority="High", reporter=DAVE, assigned_to=ERIN,
         cc=[BOB], status="CONFIRMED",
         comments=[(DAVE, "WorkManager job retries every 30s without backoff while offline.")]),
    dict(alias="seed-016", product="Orbit Mobile", component="Android",
         summary="Biometric unlock prompt appears twice on Android 14",
         severity="normal", priority="Normal", reporter=BOB, assigned_to=ERIN,
         cc=[], status="VERIFIED", resolution="FIXED",
         comments=[(ERIN, "Activity recreation re-triggered the prompt. Guarded with saved state."),
                   (BOB, "Verified on Pixel 7 and Galaxy S23.")]),
    dict(alias="seed-017", product="Orbit Mobile", component="Android",
         summary="Support predictive back gesture",
         severity="enhancement", priority="Low", reporter=CAROL, assigned_to=ERIN,
         cc=[], status="CONFIRMED", comments=[]),
    dict(alias="seed-018", product="Orbit Mobile", component="Sync Engine",
         summary="Conflicting edits silently overwrite the server copy",
         severity="critical", priority="Highest", reporter=ALICE, assigned_to=ERIN,
         cc=[DAVE, CAROL], status="IN_PROGRESS",
         comments=[(ALICE, "Last-writer-wins is applied even when the base revision differs."),
                   (ERIN, "Working on a three-way merge with a conflict marker for the UI.")]),
    dict(alias="seed-019", product="Orbit Mobile", component="Sync Engine",
         summary="Sync fails with HTTP 500 when the order filter contains unicode",
         severity="major", priority="High", reporter=ERIN, assigned_to=ALICE,
         cc=[DAVE], status="CONFIRMED",
         comments=[(ERIN, "Same root cause as the API bug; blocked on that fix.")]),
    dict(alias="seed-020", product="Orbit Mobile", component="Sync Engine",
         summary="Initial sync downloads attachments that are never displayed",
         severity="normal", priority="Normal", reporter=DAVE, assigned_to=ERIN,
         cc=[ALICE], status="RESOLVED", resolution="DUPLICATE", dupe_of="seed-015",
         comments=[(DAVE, "Turns out this is the same battery/traffic problem as the Android sync bug.")]),
]

# (bug alias, depends on aliases)
DEPENDENCIES = [
    ("seed-019", ["seed-004"]),
    ("seed-012", ["seed-018"]),
    ("seed-010", ["seed-005"]),
    ("seed-009", ["seed-008"]),
]

STATUS_PATH = {
    "CONFIRMED": [],
    "IN_PROGRESS": [("IN_PROGRESS", None)],
    "RESOLVED": [("RESOLVED", "resolution")],
    "VERIFIED": [("RESOLVED", "resolution"), ("VERIFIED", None)],
}


class BugzillaError(Exception):
    def __init__(self, status, payload):
        super().__init__(f"HTTP {status}: {payload}")
        self.status = status
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None


class Client:
    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def call(self, method, path, params=None, body=None):
        params = dict(params or {})
        if self.token:
            params["token"] = self.token
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw.decode(errors="replace")
            raise BugzillaError(exc.code, payload) from None
        # Bugzilla reports some failures (e.g. mail delivery) as HTTP 200 + error body.
        if isinstance(payload, dict) and payload.get("error"):
            raise BugzillaError(200, payload)
        return payload

    def get(self, path, **params):
        return self.call("GET", path, params=params)

    def post(self, path, body):
        return self.call("POST", path, body=body)

    def put(self, path, body):
        return self.call("PUT", path, body=body)


def login(base_url, user, password):
    anon = Client(base_url)
    result = anon.get("/login", login=user, password=password)
    return Client(base_url, token=result["token"])


def log(msg):
    print(msg, flush=True)


def ensure_users(admin):
    created = 0
    for email, name in USERS:
        if user_exists(admin, email):
            continue
        admin.post("/user", {"email": email, "full_name": name, "password": USER_PASSWORD})
        created += 1
    log(f"users: {created} created, {len(USERS) - created} existing")


def user_exists(admin, email):
    try:
        return bool(admin.get("/user", names=email)["users"])
    except BugzillaError as exc:
        if exc.code in (51, 504):
            return False
        raise


def ensure_products(admin):
    created_p = created_c = 0
    for prod in PRODUCTS:
        existing = admin.get("/product", names=prod["name"],
                             include_fields="name,components")["products"]
        if not existing:
            admin.post("/product", {
                "name": prod["name"], "description": prod["description"],
                "version": prod["version"], "default_milestone": prod["default_milestone"],
                "has_unconfirmed": True, "is_open": True,
            })
            created_p += 1
            existing_components = set()
        else:
            existing_components = {c["name"] for c in existing[0].get("components", [])}
        for cname, cdesc in prod["components"]:
            if cname in existing_components:
                continue
            admin.post("/component", {
                "product": prod["name"], "name": cname, "description": cdesc,
                "default_assignee": ALICE if prod["name"] == "Orbit Web" else ERIN,
                "default_qa_contact": BOB, "default_cc": [CAROL],
            })
            created_c += 1
    log(f"products: {created_p} created; components: {created_c} created")


def find_bug(client, alias):
    try:
        bugs = client.get(f"/bug/{alias}", include_fields="id,alias,status,resolution,cc,depends_on")["bugs"]
    except BugzillaError as exc:
        if exc.code in (100, 101):
            return None
        raise
    return bugs[0] if bugs else None


def ensure_bugs(base_url, admin):
    sessions = {}

    def as_user(email):
        if email not in sessions:
            sessions[email] = login(base_url, email, USER_PASSWORD)
        return sessions[email]

    ids = {}
    created = 0
    for spec in BUGS:
        existing = find_bug(admin, spec["alias"])
        if existing:
            ids[spec["alias"]] = existing["id"]
            continue
        product = next(p for p in PRODUCTS if p["name"] == spec["product"])
        reporter_name = dict(USERS)[spec["reporter"]]
        result = as_user(spec["reporter"]).post("/bug", {
            "product": spec["product"], "component": spec["component"],
            "summary": spec["summary"], "version": product["version"],
            "description": (f"{spec['summary']}.\n\nReported by {reporter_name} during the "
                            f"{spec['product']} {product['version']} test cycle."),
            "op_sys": "All", "platform": "All",
            "priority": spec["priority"], "severity": spec["severity"],
            "alias": spec["alias"], "assigned_to": spec["assigned_to"],
            "cc": spec["cc"],
        })
        bug_id = result["id"]
        ids[spec["alias"]] = bug_id
        created += 1

        for author, text in spec["comments"]:
            as_user(author).post(f"/bug/{bug_id}/comment", {"comment": text})

        for status, needs_resolution in STATUS_PATH[spec["status"]]:
            change = {"status": status}
            if needs_resolution:
                change["resolution"] = spec["resolution"]
                if spec.get("dupe_of"):
                    change["dupe_of"] = ids[spec["dupe_of"]]
            as_user(spec["assigned_to"]).put(f"/bug/{bug_id}", change)

    log(f"bugs: {created} created, {len(BUGS) - created} existing")

    added = 0
    for alias, depends in DEPENDENCIES:
        current = find_bug(admin, alias)
        want = [ids[d] for d in depends if ids[d] not in current.get("depends_on", [])]
        if want:
            admin.put(f"/bug/{ids[alias]}", {"depends_on": {"add": want}})
            added += len(want)
    log(f"dependencies: {added} added")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=os.environ.get("BZ_BASE_URL", DEFAULT_BASE_URL),
                        help="REST base URL, e.g. http://127.0.0.1:8080/rest.cgi")
    parser.add_argument("--login", default=os.environ.get("BZ_ADMIN_EMAIL", "admin@bugzilla.test"))
    parser.add_argument("--password", default=os.environ.get("BZ_ADMIN_PASSWORD", "password01!"))
    args = parser.parse_args()

    log(f"seeding {args.base_url} as {args.login}")
    admin = login(args.base_url, args.login, args.password)
    ensure_users(admin)
    ensure_products(admin)
    ensure_bugs(args.base_url, admin)
    log("done")


if __name__ == "__main__":
    try:
        main()
    except BugzillaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
