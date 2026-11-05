#!/usr/bin/env python3
"""Seed a Bugzilla instance with realistic demo data through the REST API.

Idempotent: every object is looked up before it is created, and bugs carry a
stable alias (``seed-NN``) so re-running the script never duplicates them.

Usage:
    python3 scripts/seed_demo_data.py [--base-url http://127.0.0.1:8080/rest.cgi]
                                      [--admin-login admin@bugzilla.test]
                                      [--admin-password 'password01!']

Only the standard library is used so the script runs anywhere Python 3.8+ does.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

USER_PASSWORD = "Seed-pass-2024!"

PRODUCTS = [
    {
        "name": "Orbit CRM",
        "description": "Customer relationship management suite (web + mobile).",
        "version": "2.4",
        "components": [
            ("Contacts", "Contact and account records, import/export."),
            ("Pipeline", "Opportunity pipeline, forecasting and reports."),
            ("Mobile App", "iOS/Android client and offline sync."),
        ],
    },
    {
        "name": "Lighthouse Billing",
        "description": "Subscription billing, invoicing and payment collection.",
        "version": "1.9",
        "components": [
            ("Invoicing", "Invoice generation, PDF rendering, numbering."),
            ("Payments", "Card/ACH processing and payment gateway adapters."),
            ("Tax Engine", "Tax jurisdiction lookup and rate calculation."),
        ],
    },
]

USERS = [
    ("alice.ng@bugzilla.test", "Alice Ng"),
    ("bruno.costa@bugzilla.test", "Bruno Costa"),
    ("chen.wei@bugzilla.test", "Chen Wei"),
    ("dana.okafor@bugzilla.test", "Dana Okafor"),
    ("eli.marsh@bugzilla.test", "Eli Marsh"),
]

# (product, component, summary, severity, priority, reporter_idx, assignee_idx, cc_idxs, description)
BUGS = [
    ("Orbit CRM", "Contacts", "CSV import drops rows with non-ASCII names", "major", "High", 0, 2, [1],
     "Importing contacts.csv with accented characters silently skips those rows. Encoding is UTF-8 with BOM."),
    ("Orbit CRM", "Contacts", "Duplicate detection ignores trailing whitespace in email", "normal", "Normal", 1, 2, [],
     "'a@b.com ' and 'a@b.com' are treated as different contacts."),
    ("Orbit CRM", "Contacts", "Account merge loses custom field values", "critical", "Highest", 3, 0, [1, 2],
     "After merging two accounts the custom 'Region' field on the surviving record is blank."),
    ("Orbit CRM", "Pipeline", "Forecast report off by one month in Q4", "major", "High", 2, 1, [0],
     "Opportunities closing on Dec 31 are counted in January of the next year."),
    ("Orbit CRM", "Pipeline", "Kanban drag-and-drop broken in Firefox 128", "normal", "Normal", 4, 1, [],
     "Cards snap back to the original column. Console shows a DataTransfer error."),
    ("Orbit CRM", "Pipeline", "Stage probability not recalculated after bulk edit", "minor", "Low", 0, 1, [3],
     "Bulk changing stage leaves the old probability percentage."),
    ("Orbit CRM", "Pipeline", "Export to XLSX times out above 50k opportunities", "major", "High", 3, 4, [1],
     "Request exceeds the 60s gateway timeout; should stream or run async."),
    ("Orbit CRM", "Mobile App", "Offline sync conflict overwrites newer server data", "blocker", "Highest", 1, 4, [0, 2],
     "Device edit made before going offline wins over a later server edit."),
    ("Orbit CRM", "Mobile App", "Push notifications stop after app update on Android 14", "major", "High", 2, 4, [],
     "FCM token is not refreshed after update; reinstall fixes it."),
    ("Orbit CRM", "Mobile App", "Dark mode: unreadable text in contact detail", "trivial", "Lowest", 4, 4, [],
     "Grey-on-grey labels in the detail header."),
    ("Lighthouse Billing", "Invoicing", "Invoice PDF renders wrong currency symbol for CHF", "normal", "Normal", 3, 0, [],
     "Shows 'Fr.' instead of 'CHF' as configured in the tenant settings."),
    ("Lighthouse Billing", "Invoicing", "Invoice numbering gap after failed send", "major", "High", 0, 2, [3],
     "A failed email send consumes the sequence number, creating gaps auditors flag."),
    ("Lighthouse Billing", "Invoicing", "Credit note cannot reference a voided invoice", "normal", "Normal", 1, 2, [],
     "Validation blocks selecting voided invoices even though finance needs this."),
    ("Lighthouse Billing", "Invoicing", "Bulk invoice run sends duplicate emails", "critical", "Highest", 2, 0, [1, 4],
     "Retry logic re-sends already-sent invoices when a later batch item fails."),
    ("Lighthouse Billing", "Payments", "ACH return code R01 not mapped to 'insufficient funds'", "normal", "Normal", 4, 3, [],
     "Shows generic 'payment failed' message; dunning flow picks the wrong template."),
    ("Lighthouse Billing", "Payments", "Stripe webhook signature check fails behind proxy", "blocker", "Highest", 0, 3, [2],
     "Body is re-encoded by the reverse proxy so the HMAC never matches."),
    ("Lighthouse Billing", "Payments", "Refund amount allows more than the captured total", "critical", "Highest", 1, 3, [0, 4],
     "Partial refunds can sum above the original charge."),
    ("Lighthouse Billing", "Tax Engine", "VAT reverse charge not applied for EU B2B", "major", "High", 2, 1, [],
     "Customers with a valid VAT ID are still charged VAT."),
    ("Lighthouse Billing", "Tax Engine", "Rate lookup uses billing instead of shipping address", "normal", "Normal", 3, 1, [2],
     "US sales tax should be destination based for physical goods."),
    ("Lighthouse Billing", "Tax Engine", "Tax rounding differs between invoice and ledger", "minor", "Low", 4, 1, [],
     "Line-level vs total-level rounding produce a 1 cent difference."),
]

# Extra comments: (bug_idx, commenter_idx, text)
COMMENTS = [
    (0, 2, "Reproduced with a UTF-8 file containing 'José'. The parser opens the file in latin-1."),
    (0, 0, "Can we add a regression test with the sample file attached to the ticket?"),
    (2, 0, "Confirmed on staging. Custom fields are not included in the merge payload."),
    (3, 1, "Root cause: date bucketed with month+1 without wrapping the year."),
    (3, 0, "Fix looks good; waiting on QA for the December data set."),
    (7, 4, "Last-write-wins is applied on the device clock, which may be behind."),
    (7, 2, "We should use server-side vector clocks or at least compare updated_at from the server."),
    (11, 2, "Sequence should be reserved only after the SMTP 250 response."),
    (13, 0, "Hotfix deployed: idempotency key per invoice send."),
    (15, 3, "Proxy was normalising JSON whitespace. Switched to raw body passthrough."),
    (16, 3, "Added a guard comparing cumulative refunds to captured amount."),
    (17, 1, "Need the VIES validation result cached on the customer record first."),
]

# Dependencies: (blocked_idx, depends_on_idx)
DEPENDENCIES = [
    (2, 0),    # account merge depends on import fix
    (6, 3),    # export depends on forecast fix
    (13, 11),  # duplicate emails depends on numbering fix
    (17, 18),  # reverse charge depends on address fix
    (16, 15),  # refund guard depends on webhook fix
]

# Status changes: (bug_idx, actor_idx, status, resolution, comment)
STATUS_CHANGES = [
    (3, 1, "IN_PROGRESS", None, "Taking this one."),
    (3, 1, "RESOLVED", "FIXED", "Fixed in commit 4f2a9c1; year now wraps correctly."),
    (9, 4, "RESOLVED", "WONTFIX", "Dark mode is being redesigned in 3.0; not worth patching 2.x."),
    (13, 0, "IN_PROGRESS", None, "Working on idempotency keys."),
    (13, 0, "RESOLVED", "FIXED", "Idempotency key added; verified on staging."),
    (13, 2, "VERIFIED", "FIXED", "Verified against the bulk-run fixture."),
    (15, 3, "RESOLVED", "FIXED", "Raw body passthrough configured on the proxy."),
    (1, 2, "RESOLVED", "DUPLICATE", None),  # dupe_of handled below
    (10, 0, "IN_PROGRESS", None, "Looking at the currency table."),
]
DUPLICATE_OF = {1: 0}  # bug_idx -> bug_idx
STATUS_RANK = {"UNCONFIRMED": 0, "CONFIRMED": 0, "IN_PROGRESS": 1, "RESOLVED": 2, "VERIFIED": 3}


class ApiError(RuntimeError):
    def __init__(self, status, payload, message):
        super().__init__(message)
        self.status = status
        self.code = payload.get("code") if isinstance(payload, dict) else None

    @property
    def not_found(self):
        # 51 object_does_not_exist, 100 invalid_bug_id_or_alias, 101 bug_id_does_not_exist
        return self.status == 404 or self.code in (51, 100, 101)


class Bugzilla:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def call(self, method, path, params=None, body=None, token=None):
        params = dict(params or {})
        if token:
            params["token"] = token
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode(errors="replace")
            try:
                payload = json.loads(payload)
            except ValueError:
                pass
            raise ApiError(exc.code, payload, f"{method} {path} -> HTTP {exc.code}: {payload}") from None

    def login(self, login, password):
        return self.call("GET", "/login", {"login": login, "password": password})["token"]


def log(msg):
    print(msg, flush=True)


def ensure_product(bz, token, spec):
    found = bz.call("GET", "/product", {"names": spec["name"], "include_fields": "id,name"}, token=token)
    if found.get("products"):
        log(f"  product exists: {spec['name']}")
    else:
        bz.call("POST", "/product", body={
            "name": spec["name"],
            "description": spec["description"],
            "version": spec["version"],
            "has_unconfirmed": True,
            "is_open": True,
        }, token=token)
        log(f"  created product: {spec['name']}")
    product = bz.call("GET", "/product", {"names": spec["name"], "include_fields": "id,name,components"},
                      token=token)["products"][0]
    existing = {c["name"] for c in product.get("components", [])}
    for i, (comp_name, comp_desc) in enumerate(spec["components"]):
        if comp_name in existing:
            log(f"    component exists: {comp_name}")
            continue
        bz.call("POST", "/component", body={
            "name": comp_name,
            "product": spec["name"],
            "description": comp_desc,
            "default_assignee": USERS[i % len(USERS)][0],
        }, token=token)
        log(f"    created component: {comp_name}")


def ensure_user(bz, token, email, full_name):
    try:
        bz.call("GET", "/user", {"names": email, "include_fields": "id,name"}, token=token)
        log(f"  user exists: {email}")
        return
    except ApiError as exc:
        if not exc.not_found:
            raise
    bz.call("POST", "/user", body={"email": email, "full_name": full_name, "password": USER_PASSWORD}, token=token)
    log(f"  created user: {email}")


def bug_alias(idx):
    return f"seed-{idx + 1:02d}"


def get_bug(bz, token, alias):
    try:
        return bz.call("GET", f"/bug/{alias}", {"include_fields": "id,alias,status,resolution,depends_on,cc,dupe_of"},
                       token=token)["bugs"][0]
    except ApiError as exc:
        if exc.not_found:
            return None
        raise


def ensure_bugs(bz, tokens):
    ids = {}
    for idx, (product, component, summary, severity, priority, reporter, assignee, cc, desc) in enumerate(BUGS):
        alias = bug_alias(idx)
        existing = get_bug(bz, tokens["admin"], alias)
        if existing:
            ids[idx] = existing["id"]
            log(f"  bug exists: {alias} (#{existing['id']}) {summary}")
            continue
        version = next(p["version"] for p in PRODUCTS if p["name"] == product)
        body = {
            "product": product,
            "component": component,
            "summary": summary,
            "version": version,
            "description": desc,
            "op_sys": "All",
            "platform": "All",
            "priority": priority,
            "severity": severity,
            "alias": [alias],
            "assigned_to": USERS[assignee][0],
            "cc": [USERS[i][0] for i in cc],
        }
        result = bz.call("POST", "/bug", body=body, token=tokens[USERS[reporter][0]])
        if "id" not in result:
            # Bug.create commits before sending mail; a mailer failure is reported as an
            # error envelope with HTTP 200 even though the bug exists.
            created = get_bug(bz, tokens["admin"], alias)
            if not created:
                raise RuntimeError(f"POST /bug returned no id: {result}")
            result = created
        ids[idx] = result["id"]
        log(f"  created bug: {alias} (#{result['id']}) {summary}")
    return ids


def ensure_comments(bz, tokens, ids):
    for bug_idx, commenter, text in COMMENTS:
        bug_id = ids[bug_idx]
        comments = bz.call("GET", f"/bug/{bug_id}/comment", {"include_fields": "text"}, token=tokens["admin"])
        existing = [c["text"] for c in comments["bugs"][str(bug_id)]["comments"]]
        if text in existing:
            continue
        bz.call("POST", f"/bug/{bug_id}/comment", body={"comment": text}, token=tokens[USERS[commenter][0]])
        log(f"  comment on #{bug_id} by {USERS[commenter][1]}")


def ensure_dependencies(bz, tokens, ids):
    for blocked_idx, dep_idx in DEPENDENCIES:
        blocked, dep = ids[blocked_idx], ids[dep_idx]
        bug = get_bug(bz, tokens["admin"], blocked)
        if dep in bug["depends_on"]:
            continue
        bz.call("PUT", f"/bug/{blocked}", body={"ids": [blocked], "depends_on": {"add": [dep]}},
                token=tokens["admin"])
        log(f"  #{blocked} now depends on #{dep}")


def ensure_status_changes(bz, tokens, ids):
    for bug_idx, actor, status, resolution, comment in STATUS_CHANGES:
        bug_id = ids[bug_idx]
        current = get_bug(bz, tokens["admin"], bug_id)
        if STATUS_RANK.get(current["status"], 0) >= STATUS_RANK[status]:
            continue  # already there, or moved past this state on a previous run
        body = {"ids": [bug_id], "status": status}
        if resolution:
            body["resolution"] = resolution
        if resolution == "DUPLICATE":
            body["dupe_of"] = ids[DUPLICATE_OF[bug_idx]]
            del body["resolution"]
            del body["status"]
        if comment:
            body["comment"] = {"body": comment}
        bz.call("PUT", f"/bug/{bug_id}", body=body, token=tokens[USERS[actor][0]])
        log(f"  #{bug_id} -> {status}{'/' + resolution if resolution else ''} by {USERS[actor][1]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/rest.cgi")
    parser.add_argument("--admin-login", default="admin@bugzilla.test")
    parser.add_argument("--admin-password", default="password01!")
    args = parser.parse_args()

    bz = Bugzilla(args.base_url)
    log(f"Bugzilla {bz.call('GET', '/version')['version']} at {args.base_url}")
    tokens = {"admin": bz.login(args.admin_login, args.admin_password)}

    log("Users")
    for email, full_name in USERS:
        ensure_user(bz, tokens["admin"], email, full_name)
    for email, _ in USERS:
        tokens[email] = bz.login(email, USER_PASSWORD)

    log("Products and components")
    for spec in PRODUCTS:
        ensure_product(bz, tokens["admin"], spec)

    log("Bugs")
    ids = ensure_bugs(bz, tokens)
    log("Comments")
    ensure_comments(bz, tokens, ids)
    log("Dependencies")
    ensure_dependencies(bz, tokens, ids)
    log("Status changes")
    ensure_status_changes(bz, tokens, ids)

    summary = bz.call("GET", "/bug", {"alias": [bug_alias(i) for i in range(len(BUGS))],
                                      "include_fields": "id,status,resolution"}, token=tokens["admin"])
    by_status = {}
    for b in summary["bugs"]:
        key = b["status"] + (f"/{b['resolution']}" if b["resolution"] else "")
        by_status[key] = by_status.get(key, 0) + 1
    log(f"Done: {len(summary['bugs'])} seed bugs -> {json.dumps(by_status, sort_keys=True)}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
