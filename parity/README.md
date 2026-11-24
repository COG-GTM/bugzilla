# REST parity harness

Black-box contract tests for the Bugzilla REST API (`rest.cgi`). The same suite
runs against the Perl app and, later, the FastAPI port; a migrated module is
"done" only when it is green against both.

```sh
make parity                      # Perl, http://127.0.0.1:8080/rest.cgi
make parity TARGET=python        # FastAPI, http://127.0.0.1:8000/rest.cgi
make parity BASE_URL=http://host/rest.cgi PYTEST_ARGS="-k attachments"
make seed                        # load the demo data the suite expects (scripts/seed_demo_data.py)
```

Environment: `BASE_URL`, `BZ_ADMIN_LOGIN`/`BZ_ADMIN_PASSWORD`,
`BZ_USER_LOGIN`/`BZ_USER_PASSWORD` (defaults match docker-compose + the seed).

Layout:

| file | covers |
|---|---|
| `conftest.py` | `Api` httpx client, admin/user tokens, `seed` ids, `new_bug` factory, `expect_error(resp, http, code)` |
| `normalize.py` | the one place volatile values are replaced: timestamps -> `<datetime>`, login tokens -> `<token>`, auto ids / numeric dict keys -> `<id>` |
| `test_bugzilla.py` | `/version /extensions /timezone /time /last_audit_time /parameters`, 404 for unknown resources, JSONP, CORS |
| `test_auth.py` | `/login /logout /valid_login`, token rules (codes 50, 300, 410, 32000) |
| `test_bug_read.py` | `GET /bug/{id\|alias}`, `GET /bug` search, include/exclude_fields (codes 100, 101) |
| `test_bug_write.py` | `POST /bug`, `PUT /bug/{id}` incl. cc/deps/dupes and every validation code (50, 51, 103, 106, 107, 121, 123, 410) |
| `test_bug_comments_history.py` | `/bug/{id}/comment`, `/bug/comment/{id}`, `/bug/{id}/history` (codes 110, 111) |
| `test_bug_attachments.py` | attachment metadata, `exclude_fields=data`, privacy (codes 50, 304, 601, 603, 604) |
| `test_bug_fields.py` | `/field/bug...` and legal values (codes 51, 106, 108) |

Write tests create their own bugs (as admin or the seeded `alice.ng` user) and
never mutate the seeded `seed-NN` bugs, so the suite is safe to re-run.
