# Parity harness

Black-box contract tests for the Bugzilla REST API (pytest + httpx). The same tests run against the
Perl app and, later, the Python port; Perl is the reference.

```
docker compose up -d --build
make seed                      # demo users and products the tests rely on
make parity-deps
make parity TARGET=perl        # BASE_URL defaults to http://127.0.0.1:8080/rest.cgi
make parity TARGET=python      # BASE_URL defaults to http://127.0.0.1:8000/rest.cgi
```

`make parity-report` runs both targets and prints the `endpoint | Perl | Python` table.

Apache proxies the REST modules that are already ported (`/rest.cgi/bug*`) to the Python service.
`TARGET=perl` sends `X-Bugzilla-Backend: perl`, which Apache does not proxy, so the Perl baseline
of a ported module stays testable. `TARGET=python` talks to the Python service on :8000, which
forwards the routes it does not own yet to Perl.

`BASE_URL=... make parity` overrides the URL. `BZ_ADMIN_EMAIL` and `BZ_ADMIN_PASSWORD` override the
admin login.

- Each test creates the bugs it needs, tagged with a per-run id, so the suite can be rerun on the
  same database and does not depend on bug ids.
- `normalize.py` is the only place that replaces volatile values (timestamps, tokens, generated
  ids). Tests compare the normalised JSON with literal expected JSON, so shape and values are both
  checked.
- Error cases assert the HTTP status, the Bugzilla error `code` and the message.
