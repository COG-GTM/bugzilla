# REST parity suite

Black-box contract tests for the Bugzilla REST API (`/rest.cgi`). The suite only
speaks HTTP to `BASE_URL`, so the same tests run unchanged against the Perl
application and against the Python port; identical results are the definition of
parity for a migrated module.

```sh
make parity-deps                   # pytest + httpx
make seed                          # optional demo data (scripts/seed_demo_data.py)
make parity                        # TARGET=perl, BASE_URL=http://127.0.0.1:8080/rest.cgi
make parity TARGET=python          # BASE_URL=http://127.0.0.1:8000/rest.cgi (fixtures via FIXTURE_BASE_URL=Perl)
make parity PYTEST_ARGS="-k comment -x"
```

Environment: `BASE_URL`, `BZ_ADMIN_EMAIL`, `BZ_ADMIN_PASSWORD` (defaults match
`docker-compose.yml`). The suite creates its own fixtures (product `Parity Product`,
component `Core`, user `parity.user@bugzilla.test`, bugs with per-run summaries), so it
does not depend on the demo seed and can be run repeatedly.

## Layout

| Path | Purpose |
| --- | --- |
| `conftest.py` | `Api` httpx wrapper, fixtures (`admin`, `user`, `product`, `make_bug`, `bug_payload`), `snapshot`, `assert_error` |
| `normalize.py` | the **only** place volatile data is blurred: timestamps, tokens, auto-increment ids, per-run names |
| `golden/<module>/<test>.json` | recorded contract (HTTP status + normalised body) from the Perl app |
| `test_bugzilla.py` | `Bugzilla.*` endpoints, dispatch errors, CORS/ETag/JSONP, `/rest/` alias |
| `test_auth.py` | `User.login/logout/valid_login`, token/api_key/inline credentials, permission errors |
| `test_bug.py` | `Bug.*`: fields, create, get, search, update, comments, history, attachments |

## Golden snapshots

`snapshot(resp)` normalises the response and compares it with the committed golden
file; a mismatch prints a unified diff between the Perl contract and the actual
response. Record or refresh goldens **only from the Perl app**:

```sh
make parity-update-golden
```

Normalisation rules live in `normalize.py`: ISO/SQL timestamps -> `<datetime>`,
login tokens/api keys/ETags -> `<token>`, integer ids under id-like keys and digit-only
map keys -> `<id>`, `added`/`removed` id lists -> `<id>`, digits in error `message`
-> `<n>`, the per-run marker -> `<run>`, and unordered scalar lists (`cc`, `groups`,
`alias`, ...) are sorted.

## Perl behaviours the contract pins down (candidates for `DEVIATIONS.md`)

* `POST /bug` answers **200**, not 201 (the resource declares `status_code` instead of
  `success_code`); `POST /bug/{id}/attachment`, `/product`, `/component`, `/user` answer 201.
* Unknown search parameters and unknown field values in `GET /bug` are ignored rather than
  rejected (`?status=NOPE` returns every bug); unknown fields in `PUT /bug` are ignored.
* `GET /bug/attachment/{unknown}` returns `200 {"attachments":{},"bugs":{}}`.
* An invalid login token yields code `32000` (generic) rather than a 3xx auth code.
* Attachment `is_patch`/`is_private`/`is_obsolete` are integers `0/1`, not booleans.
