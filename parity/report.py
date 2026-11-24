"""Side-by-side parity table: endpoint | Perl | Python.

    python3 parity/report.py            # runs the suite against both targets

Each test is attributed to the endpoint it exercises by its name; counts are
passed/total per target."""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# First match wins. (test-name pattern, endpoint, served by the Python port?)
RULES = [
    (r"test_normalize", "(harness self-test, no HTTP)", None),
    (r"test_bugzilla.*version", "GET /version", False),
    (r"test_bugzilla.*time", "GET /time", False),
    (r"test_bugzilla", "GET /<unknown>, other Bugzilla routes", False),
    (r"valid_login", "GET /valid_login", False),
    (r"logout", "GET /logout", False),
    (r"test_session", "GET /login", False),
    (r"unknown_bug_route", "unknown /bug routes (error 32614)", True),
    (r"comment_id|by_comment", "GET /bug/comment/{comment_id}", True),
    (r"test_add_comment|test_add_and_list|private_comment", "POST /bug/{id}/comment", True),
    (r"test_comments", "GET /bug/{id}/comment", True),
    (r"test_history", "GET /bug/{id}/history", True),
    (r"attachment_by_id|attachment_include|attachment_id_not_found", "GET /bug/attachment/{attachment_id}", True),
    (r"test_add_attachment|private_attachment", "POST /bug/{id}/attachment", True),
    (r"attachment", "GET /bug/{id}/attachment", True),
    (r"test_search|several_bugs", "GET /bug (search)", True),
    (r"test_create|summary_is_trimmed", "POST /bug", True),
    (r"test_update|duplicate", "PUT /bug/{id}", True),
    (r"test_get_bug", "GET /bug/{id}", True),
]


def run(target):
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "junit.xml"
        subprocess.run(["make", "-s", "parity", f"TARGET={target}",
                        f"PYTEST_ARGS=-q -p no:cacheprovider --junitxml={xml}"],
                       cwd=ROOT, stdout=subprocess.DEVNULL)
        results = {}
        for case in ET.parse(xml).getroot().iter("testcase"):
            failed = any(child.tag in ("failure", "error", "skipped") for child in case)
            results[f"{case.get('classname')}::{case.get('name')}"] = not failed
        return results


def main():
    perl, python = run("perl"), run("python")
    rows = OrderedDict((endpoint, [0, 0, 0, served]) for _, endpoint, served in RULES)
    for name in perl:
        endpoint = next(e for pattern, e, _ in RULES if re.search(pattern, name))
        rows[endpoint][0] += 1
        rows[endpoint][1] += perl[name]
        rows[endpoint][2] += python.get(name, False)
    print("| endpoint | Perl | Python | served by |")
    print("|---|---|---|---|")
    for endpoint, (total, p, y, served) in rows.items():
        if total:
            by = {True: "Python", False: "Perl (forwarded)", None: "n/a"}[served]
            print(f"| `{endpoint}` | {p}/{total} | {y}/{total} | {by} |")
    total = len(perl)
    print(f"| **total** | **{sum(perl.values())}/{total}** | **{sum(python.values())}/{total}** | |")
    return 0 if all(perl.values()) and all(python.values()) and len(python) == total else 1


if __name__ == "__main__":
    sys.exit(main())
