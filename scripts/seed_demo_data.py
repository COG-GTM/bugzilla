#!/usr/bin/env python3
"""Seed a local Bugzilla with demo data through the REST API (rest.cgi).

Creates 2 products with 3 components each, 5 users and 20 bugs with comments,
CC lists, dependencies and status changes. Safe to re-run: anything that
already exists (matched by name, login or summary) is left alone.

    python3 scripts/seed_demo_data.py
    BASE_URL=http://127.0.0.1:8080/rest.cgi python3 scripts/seed_demo_data.py

Uses only the standard library.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080/rest.cgi").rstrip("/")
ADMIN_LOGIN = os.environ.get("BZ_ADMIN_EMAIL", "admin@bugzilla.test")
ADMIN_PASSWORD = os.environ.get("BZ_ADMIN_PASSWORD", "password01!")
USER_PASSWORD = os.environ.get("BZ_DEMO_USER_PASSWORD", "Demo-passw0rd!")

USERS = [
    ("alice.nguyen@bugzilla.test", "Alice Nguyen"),
    ("bob.martins@bugzilla.test", "Bob Martins"),
    ("carol.okafor@bugzilla.test", "Carol Okafor"),
    ("dave.fischer@bugzilla.test", "Dave Fischer"),
    ("erin.kowalski@bugzilla.test", "Erin Kowalski"),
]
ALICE, BOB, CAROL, DAVE, ERIN = (u[0] for u in USERS)

PRODUCTS = {
    "WebStore": {
        "description": "Customer-facing online store",
        "components": [
            ("Checkout", "Cart, payment and order confirmation", ALICE),
            ("Catalog", "Product listing, search and filters", BOB),
            ("Accounts", "Sign-up, login and profile pages", CAROL),
        ],
    },
    "Fulfilment": {
        "description": "Warehouse and shipping back office",
        "components": [
            ("Inventory", "Stock levels and reservations", DAVE),
            ("Shipping", "Carrier integration and labels", ERIN),
            ("Returns", "Return requests and refunds", ALICE),
        ],
    },
}

# key, product, component, summary, severity, priority, assignee, cc, comments
BUGS = [
    ("b1", "WebStore", "Checkout", "Order total is wrong when a discount code and gift card are combined", "major", "Highest", ALICE, [BOB, DAVE],
     ["Reproduced with code SPRING10 and a 20.00 gift card: total is off by the discount amount.", "The discount is applied before and after the gift card. Fix in review."]),
    ("b2", "WebStore", "Checkout", "Payment page times out for carts with more than 50 items", "critical", "High", ALICE, [ERIN],
     ["Gateway logs show the tax call taking 28s for large carts."]),
    ("b3", "WebStore", "Checkout", "Add Apple Pay as a payment option", "enhancement", "Normal", BOB, [ALICE, CAROL],
     ["Product asked for this for the Q3 mobile push."]),
    ("b4", "WebStore", "Checkout", "Order confirmation email shows the billing address as the shipping address", "normal", "Normal", CAROL, [ALICE],
     ["Only happens when the two addresses differ.", "Template uses the wrong variable."]),
    ("b5", "WebStore", "Catalog", "Search returns no results for queries with accented characters", "major", "High", BOB, [CAROL],
     ["'cafe' matches but 'café' does not.", "The search index is built without unicode normalisation."]),
    ("b6", "WebStore", "Catalog", "Price filter ignores the upper bound", "normal", "Normal", BOB, [],
     ["Filtering 10-50 also returns items above 50."]),
    ("b7", "WebStore", "Catalog", "Product images load slowly on category pages", "minor", "Low", DAVE, [BOB],
     ["Full-size images are served and resized in the browser."]),
    ("b8", "WebStore", "Catalog", "Out-of-stock products are still shown as available", "major", "High", BOB, [DAVE, ERIN],
     ["Depends on the stock feed from Fulfilment being correct."]),
    ("b9", "WebStore", "Accounts", "Password reset link expires after 5 minutes instead of 24 hours", "normal", "High", CAROL, [ALICE],
     ["Token lifetime is read in minutes but configured in hours."]),
    ("b10", "WebStore", "Accounts", "Users can register twice with the same email in different case", "major", "Normal", CAROL, [],
     ["Foo@example.com and foo@example.com create two accounts."]),
    ("b11", "WebStore", "Accounts", "Profile page crashes when the phone number is empty", "critical", "Highest", CAROL, [BOB],
     ["Stack trace points at the phone formatter.", "Fixed with a null check; verified on staging."]),
    ("b12", "Fulfilment", "Inventory", "Stock feed publishes negative quantities after a cancelled reservation", "critical", "Highest", DAVE, [BOB, ERIN],
     ["Cancelling a reservation twice subtracts the quantity twice.", "Made the cancel handler idempotent."]),
    ("b13", "Fulfilment", "Inventory", "Nightly stock reconciliation takes over 4 hours", "normal", "Normal", DAVE, [],
     ["The job does one query per SKU."]),
    ("b14", "Fulfilment", "Inventory", "Support multiple warehouses per SKU", "enhancement", "Low", DAVE, [ERIN, ALICE],
     ["Needed before the second warehouse opens."]),
    ("b15", "Fulfilment", "Shipping", "Shipping labels are printed with the wrong carrier barcode format", "major", "High", ERIN, [DAVE],
     ["Carrier rejects about 3% of parcels at the depot."]),
    ("b16", "Fulfilment", "Shipping", "Tracking numbers are not sent to customers for split shipments", "normal", "Normal", ERIN, [ALICE],
     ["Only the first parcel's tracking number is emailed."]),
    ("b17", "Fulfilment", "Shipping", "Carrier rate lookup fails for postcodes with a space", "normal", "High", ERIN, [],
     ["'SW1A 1AA' fails, 'SW1A1AA' works.", "Postcode is now normalised before the call."]),
    ("b18", "Fulfilment", "Returns", "Refund is issued twice when a return is approved from two browser tabs", "critical", "Highest", ALICE, [DAVE, ERIN],
     ["No lock on the return row while the refund is in flight."]),
    ("b19", "Fulfilment", "Returns", "Return label PDF is blank for international orders", "major", "Normal", ALICE, [ERIN],
     ["Depends on the carrier barcode fix."]),
    ("b20", "Fulfilment", "Returns", "Returned items are not added back to stock", "major", "High", DAVE, [ALICE],
     ["The returns service never calls the inventory API."]),
]

# bug -> bugs it depends on
DEPENDS_ON = {
    "b8": ["b12"],
    "b3": ["b2"],
    "b19": ["b15"],
    "b20": ["b12", "b18"],
    "b14": ["b13"],
}

# bug -> list of updates applied in order
STATUS_CHANGES = {
    "b1": [{"status": "IN_PROGRESS"}],
    "b5": [{"status": "IN_PROGRESS"}],
    "b15": [{"status": "IN_PROGRESS"}],
    "b4": [{"status": "IN_PROGRESS"}, {"status": "RESOLVED", "resolution": "FIXED"}],
    "b17": [{"status": "IN_PROGRESS"}, {"status": "RESOLVED", "resolution": "FIXED"}],
    "b11": [{"status": "RESOLVED", "resolution": "FIXED"}, {"status": "VERIFIED"}],
    "b12": [{"status": "IN_PROGRESS"}, {"status": "RESOLVED", "resolution": "FIXED"}, {"status": "VERIFIED"}],
    "b7": [{"status": "RESOLVED", "resolution": "WONTFIX"}],
    "b6": [{"status": "RESOLVED", "resolution": "WORKSFORME"}, {"status": "CONFIRMED"}],
}


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Client:
    def __init__(self, base_url):
        self.base_url = base_url
        self.token = None

    def call(self, method, path, params=None, body=None):
        query = dict(params or {})
        if self.token:
            query["token"] = self.token
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            raw = err.read().decode(errors="replace")
            try:
                raise ApiError(err.code, json.loads(raw)) from None
            except json.JSONDecodeError:
                raise ApiError(err.code, raw) from None
        if isinstance(payload, dict) and payload.get("error"):
            raise ApiError(200, payload)
        return payload

    def login(self, login, password):
        self.token = self.call("GET", "/login", {"login": login, "password": password})["token"]


def ensure_users(api):
    created = 0
    for email, name in USERS:
        if api.call("GET", "/user", {"match": email})["users"]:
            continue
        api.call("POST", "/user", body={"email": email, "full_name": name, "password": USER_PASSWORD})
        created += 1
    return created


def ensure_products(api):
    created = [0, 0]
    for name, spec in PRODUCTS.items():
        found = api.call("GET", "/product", {"names": name})["products"]
        if not found:
            api.call("POST", "/product", body={
                "name": name, "description": spec["description"], "version": "1.0",
                "is_open": True, "has_unconfirmed": True,
            })
            created[0] += 1
        existing = {c["name"] for p in found for c in p["components"]}
        for comp, desc, owner in spec["components"]:
            if comp in existing:
                continue
            api.call("POST", "/component", body={
                "product": name, "name": comp, "description": desc, "default_assignee": owner,
            })
            created[1] += 1
    return created


def ensure_bugs(api):
    ids, new = {}, set()
    for key, product, component, summary, severity, priority, assignee, cc, comments in BUGS:
        found = api.call("GET", "/bug", {"summary": summary, "product": product, "include_fields": "id,summary"})["bugs"]
        found = [b for b in found if b["summary"] == summary]
        if found:
            ids[key] = found[0]["id"]
            continue
        ids[key] = api.call("POST", "/bug", body={
            "product": product, "component": component, "summary": summary,
            "version": "1.0", "op_sys": "All", "platform": "All",
            # The install default for both is "--", which bug create rejects.
            "priority": priority, "severity": severity,
            "assigned_to": assignee, "cc": cc,
            "description": comments[0],
        })["id"]
        new.add(key)
        for text in comments[1:]:
            api.call("POST", f"/bug/{ids[key]}/comment", body={"comment": text})

    # Only decorate bugs created in this run, so a re-run changes nothing.
    for key, blockers in DEPENDS_ON.items():
        if key in new:
            api.call("PUT", f"/bug/{ids[key]}", body={"depends_on": {"add": [ids[b] for b in blockers]}})
    for key, updates in STATUS_CHANGES.items():
        if key in new:
            for update in updates:
                api.call("PUT", f"/bug/{ids[key]}", body=update)
    return ids, new


def main():
    api = Client(BASE_URL)
    api.login(ADMIN_LOGIN, ADMIN_PASSWORD)
    users = ensure_users(api)
    products, components = ensure_products(api)
    ids, new = ensure_bugs(api)
    print(f"users created: {users} of {len(USERS)}")
    print(f"products created: {products} of {len(PRODUCTS)}, components created: {components} of 6")
    print(f"bugs created: {len(new)} of {len(BUGS)} (ids {min(ids.values())}-{max(ids.values())})")


if __name__ == "__main__":
    try:
        main()
    except ApiError as err:
        sys.exit(f"seed failed: {err}")
