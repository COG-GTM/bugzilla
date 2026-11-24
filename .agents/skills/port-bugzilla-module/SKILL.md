---
name: port-bugzilla-module
description: Port one Bugzilla WebService module (Product, Component, Classification, Group, User, FlagType, BugUserLastVisit, Bugzilla) from Perl to the python/ FastAPI service, parity tests first, following the Bug module template. Use when asked to port, migrate or re-implement a Bugzilla REST module in Python.
---

# Port a Bugzilla WebService module to Python

Perl is the source of truth. A module is done when `make parity` gives the same pass count on
`TARGET=perl` and `TARGET=python` and Apache routes the module's paths to Python.
The Bug module (`python/app/modules/bug/`, `parity/test_bug_*.py`) is the template.

## Steps

1. **Start the stack**: `docker compose up -d --build && make seed && make parity-deps`.
   Perl UI and REST on :8080, Python on :8000, both on the same MariaDB.
2. **Read the Perl**, in this order:
   - `Bugzilla/WebService/Server/REST/Resources/<Module>.pm`: the exact paths, methods and
     success status codes (`success_code`), and which path captures become which parameters.
   - `Bugzilla/WebService/<Module>.pm`: parameters, output keys, types (`$self->type(...)`).
   - The `Bugzilla/<Object>.pm` validators and `update()`/`create()` it calls: side effects.
   - `Bugzilla/WebService/Constants.pm` (`WS_ERROR_CODE`) and
     `template/en/default/global/user-error.html.tmpl` / `code-error.html.tmpl`: codes and messages.
   - `Bugzilla/DB/Schema.pm` for the tables.
3. **Extend the parity tests first** (next section) and get them green on `TARGET=perl`.
4. **Add the module** under `python/app/modules/<module>/` (layout below). Nothing outside that
   directory changes, except the Apache rule in step 6.
5. **Iterate**: `docker compose up -d --build bugzilla5.api && make parity TARGET=python`
   until the count equals Perl's. Then probe beyond the suite: run the same request sequence
   against both backends, diff the normalised responses, and turn every difference you fix into
   a parity test.
6. **Route it**: add one anchored `ProxyPassMatch` line to `docker/000-default.conf`, rebuild the
   web image, and run `make parity TARGET=python PYTHON_BASE_URL=http://127.0.0.1:8080/rest.cgi`.
7. **Check the classic UI** still shows data written through Python (the `.cgi` page that lists
   and the one that shows the object).
8. **Open a PR into `migration/python-v2`** with the `make parity-report` table in the description.

## File layout

```
python/app/
  main.py                 # do not edit: discovers app/modules/*/router.py at startup
  core/                   # shared; change only in a separate PR, other modules depend on it
    auth.py               # current_user (token + api_key), require_login, issue_hash_token
    errors.py             # BugzillaError(code, message), status map, shared errors
    fallback.py           # forward_to_perl, NotPorted
    params.py             # request_params (query wins over body), FieldFilter, dates
    db.py                 # engine, get_session, reflected tables `t.<table>`, db_now
    config.py             # DB env, Bugzilla params (data/params.json), site_wide_secret
  modules/<module>/
    __init__.py
    router.py             # `router = APIRouter()`; paths without the /rest.cgi prefix
    service.py            # logic and SQL; raises BugzillaError; commits once per write
    schemas.py            # Pydantic v2 request (lenient) and response (OpenAPI) models
parity/test_<module>*.py  # contract tests, run against both backends
```

- A router is mounted because the file exists. There is no list to edit, so two modules never
  touch the same file.
- Routes take `params: dict = Depends(request_params)`, `user: User = Depends(current_user)`,
  `session: Session = Depends(get_session)` and return `JSONResponse(service.fn(...))`.
- Tables are reflected on first use: `from app.core.db import t`, then `t.products`. Do not add
  model classes to a shared file.
- Request models have every field optional and ignore unknown fields. Bugzilla's validators run
  in the service, because a Pydantic 422 can never match a Bugzilla error code and message.
- Module-specific errors live in the module's `service.py`; only errors used by several modules
  go to `core/errors.py`.

## Extend the parity tests first

- Probe Perl with curl before writing an assertion. Do not trust the docs or the Perl source for
  messages; copy what the server returns.
- One file per area: `parity/test_<module>_<area>.py`. Use the `anon`, `admin`, `make_bug`,
  `run_tag` fixtures from `parity/conftest.py`; add fixtures for your module's objects there only
  if another module needs them, otherwise in your test file.
- For every endpoint cover: success shape (full literal JSON), `include_fields`/`exclude_fields`,
  no auth (410), bad token (32000), bad id / unknown name, each invalid field value, and one
  "failed write changed nothing" check.
- Assert errors with `assert_error(resp, http_status, code, message)`: all three are compared.
- Create your own data with names containing `run_tag`, so reruns on the same database pass and
  no test depends on ids or on another test.
- Green on Perl first: `make parity TARGET=perl PYTEST_ARGS="parity/test_<module>*.py"`.
  A test that Perl fails is a wrong test, not a Perl bug to fix.
- While a Python module is proxied, `TARGET=perl` still reaches Perl: the suite sends
  `X-Bugzilla-Backend: perl`, which Apache does not proxy.

## Normalisation rules

`parity/normalize.py` is the only place volatile values are replaced. Tests compare
`normalize(resp.json(), {bug_id: "A"})` with literal JSON.

| Value | Becomes |
|---|---|
| ISO timestamps | `<TIMESTAMP>` |
| `token`, `update_token` | `<TOKEN>` |
| Generated ids (`id`, `bug_id`, `attachment_id`, `dupe_of`, `ids`) | `<ID>` |
| Bug ids the test names (`{id: "A"}`), also in `depends_on`/`blocks`, object keys and change strings | `<BUG:A>` |
| "Created attachment N" in comment text | `Created attachment <ID>` |
| Changes inside one history entry | sorted by `field_name` |

- Never normalise in a test file. A new volatile field means a new rule in `normalize.py` plus a
  case in `parity/test_normalize.py`.
- Do not normalise more than needed: error messages, names, statuses and counts are compared
  exactly. Where an id must match another response, compare the raw values before normalising.
- A placeholder hides the value. If the value matters across backends (a token Perl must accept),
  add a direct check; for `update_token`, request it from both backends in the same second and
  compare.

## Gotchas hit while porting Bug

Routing and requests
- `/rest.cgi/bug*` as a prefix also matches `/rest.cgi/bug_user_last_visit`. Anchor the rule:
  `^/rest(?:\.cgi)?/(bug(?:/.*)?)$`.
- Query parameters override JSON body values on POST/PUT, and a body `ids` replaces the id in the
  path (`PUT /bug/1` with `{"ids": [2]}` updates only bug 2).
- Success status differs per route: `POST /bug` is 200, `POST /bug/{id}/comment` and
  `/attachment` are 201.
- Unknown paths and methods are 404 with code 32614 and the message
  `A REST API resource was not found for 'METHOD /path'.` (path without the prefix).
- `/bug/comment/{id}` and `/bug/attachment/{id}` must be declared before `/bug/{id}`.
- The Python service forwards every path no router declares to Perl (`PERL_REST_URL`), with
  `X-Bugzilla-Backend: perl` so Apache does not proxy it back. Declaring a route is what takes
  it away from Perl.

Auth
- Login tokens live in `logincookies` (`<user_id>-<cookie>`), not in `tokens`. A token that is
  not of that form is ignored; a well-formed wrong one is error 32000. A bad `api_key` is 306.
- `update_token` is an HMAC with Perl's `site_wide_secret`; `docker/startup.sh` exports it to
  `data/python_site_wide_secret` for the Python container.

Errors
- HTTP status comes from the code: 404 for 51, 101 and 32614; 401 for the auth/permission codes
  (`UNAUTHORIZED` in `core/errors.py`); 400 otherwise.
- The same mistake has different errors per call: an unknown CC user is 504 on create and 51 on
  update; an unknown product on create is 106, not 51.
- `param_required` names the function only where Perl passes one
  (`The function Bug.search() requires a limit argument...` vs `The function requires a comment argument...`).
- Message text uses `field_descs`, not column names: `OS`, `Hardware`, `Priority`, lowercase
  `status`.
- Defaults come from parameters, and they can be invalid: omitting `priority` fails with
  `There is no Priority named '--'.` in this install; omitting `severity` gives `normal`.
- Search is forgiving where get is strict: an unknown status value or parameter is ignored, an
  unknown product matches nothing, and an unknown attachment id returns 200 with empty objects.
- Invalid JSON bodies return code 32000, but the message contains Perl parser text that Python
  cannot reproduce. This is the one accepted deviation.

Data and side effects
- Use the database clock (`db_now`) for every timestamp, one value per request, and render
  times as UTC `...Z`.
- Reproduce every write Perl makes, not only the main row. For Bug: `longdescs` comment 0,
  `bugs_fulltext`, `bugs_activity` on both bugs of a dependency, `delta_ts` on every touched bug,
  the reporter added to the CC of a duplicate's target. Check with the classic UI and with
  `GET .../history`.
- Some response fields are not history: `dupe_of` appears in `changes` of an update but has no
  `bugs_activity` row.
- Activity strings keep Perl's formats: ids and logins joined with `", "`, sorted; the old
  value of a decimal as `0.00` but the new one as the client sent it.
- Text cleaning differs per field: a summary is trimmed and control characters become a space,
  inner spaces are kept; a comment only loses trailing whitespace.
- Generated comment text depends on `longdescs.type` (duplicate, has-duplicate, attachment
  created); the stored `thetext` is not what the API returns.
- `count` of a comment is its position among all comments of the bug, private ones included.
- Privacy needs the `insidergroup`: setting `is_private` without it is 401/113, and private
  comments and attachments are left out for everyone else.
- Bug mail is not sent by Python. `lastdiffed` is left untouched, so Perl still sees the changes
  as unmailed. Do not advance it.
- Perl closes the connection after each response; the harness disables keep-alive. Perl takes
  about 0.2 s per request, so a full run is about 30 s on Perl and 5 s on Python.

## Partial ports

A module does not have to be complete to be routed. Whatever it does not handle is answered by
Perl through `app/core/fallback.py`:

- A path or method no router declares is forwarded to Perl as is (for Bug: comment tags,
  `PUT /bug/attachment/{id}`, `GET /bug/`).
- A request the service recognises but cannot serve yet raises `NotPorted`, before any write, and
  is forwarded the same way (for Bug: `PUT /bug/{id}` with `product`, `groups`, `keywords`,
  `see_also`, `flags`, `alias`, `reset_assigned_to`, `reset_qa_contact`, `comment_is_private` or
  `work_time`).
- Never ignore a parameter Perl acts on, and never invent an error for it. Forward it.
- List what is forwarded in the PR description, and add a parity test for at least one forwarded
  case so the hand-off itself is covered.
