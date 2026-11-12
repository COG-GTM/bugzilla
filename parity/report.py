"""Side-by-side parity report: endpoint group | Perl | Python.

    python3 parity/report.py [--perl URL] [--python URL] [--markdown out.md]

Runs the suite once per target (JUnit XML), then aggregates pass counts per test module
(= endpoint group) and per endpoint prefix taken from the test name.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

GROUPS = OrderedDict([
    ("test_bugzilla", "Bugzilla: /version /time /timezone /extensions /last_audit_time /parameters"),
    ("test_auth", "User: /login /logout /valid_login"),
    ("test_bug_read", "Bug.get / Bug.search  (GET /bug, GET /bug/{id})"),
    ("test_bug_write", "Bug.create / Bug.update  (POST /bug, PUT /bug/{id})"),
    ("test_bug_comments_history", "Bug.comments / add_comment / history"),
    ("test_bug_attachments", "Bug.attachments / add_attachment (metadata)"),
    ("test_bug_fields", "Bug.fields / legal_values  (GET /field/bug...)"),
])


def run(base_url: str, label: str) -> dict:
    out = Path(tempfile.gettempdir()) / f"parity-{label}-{os.getpid()}.xml"
    out.unlink(missing_ok=True)  # never aggregate a stale report
    env = dict(os.environ, BASE_URL=base_url)
    proc = subprocess.run([sys.executable, "-m", "pytest", "parity", "-q", "-p", "no:cacheprovider",
                           f"--junitxml={out}"], env=env, cwd=Path(__file__).resolve().parent.parent,
                          capture_output=True, text=True)
    if not out.exists():  # pytest died before writing JUnit XML (collection error, crash, bad BASE_URL)
        sys.exit(f"parity run against {label} ({base_url}) produced no JUnit XML (exit {proc.returncode}):\n"
                 f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    results = {}
    for case in ET.parse(out).getroot().iter("testcase"):
        module = case.get("classname", "").split(".")[-1]
        ok = not any(child.tag in ("failure", "error") for child in case)
        skipped = any(child.tag == "skipped" for child in case)
        results[(module, case.get("name"))] = "skip" if skipped else ("pass" if ok else "fail")
    return results


def table(perl: dict, python: dict) -> str:
    rows = ["| endpoint group | Perl | Python |", "|---|---|---|"]
    total_p = total_y = total_n = 0
    groups = OrderedDict(GROUPS)
    for module in sorted({m for (m, _) in set(perl) | set(python)}):
        groups.setdefault(module, module)  # a new test_<module>.py shows up even before it is labelled here
    for module, label in groups.items():
        names = sorted({n for (m, n) in set(perl) | set(python) if m == module})
        if not names:
            continue
        p_ok = sum(perl.get((module, n)) == "pass" for n in names)
        y_ok = sum(python.get((module, n)) == "pass" for n in names)
        total_p += p_ok
        total_y += y_ok
        total_n += len(names)
        rows.append(f"| {label} | {p_ok}/{len(names)} | {y_ok}/{len(names)} |")
    rows.append(f"| **total** | **{total_p}/{total_n}** | **{total_y}/{total_n}** |")
    diffs = [(m, n) for (m, n) in sorted(set(perl) | set(python)) if perl.get((m, n)) != python.get((m, n))]
    if diffs:
        rows.append("")
        rows.append("Differences:")
        rows += [f"- `{m}::{n}`: Perl={perl.get((m, n))} Python={python.get((m, n))}" for m, n in diffs]
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perl", default=os.environ.get("PERL_BASE_URL", "http://127.0.0.1:8080/rest.cgi"))
    ap.add_argument("--python", default=os.environ.get("PYTHON_BASE_URL", "http://127.0.0.1:8000/rest.cgi"))
    ap.add_argument("--markdown", help="also write the table to this file")
    args = ap.parse_args()
    perl = run(args.perl, "perl")
    python = run(args.python, "python")
    md = table(perl, python)
    print(md)
    if args.markdown:
        Path(args.markdown).write_text(md + "\n")


if __name__ == "__main__":
    main()
