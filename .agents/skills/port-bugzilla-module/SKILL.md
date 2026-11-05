---
name: port-bugzilla-module
description: Port one Bugzilla WebService module (Bugzilla/WebService/<Module>.pm + its REST resource file) from Perl to the FastAPI service in python/, parity-first. Use for any "migrate/port <Module> to Python" task on COG-GTM/bugzilla (Product, Component, Classification, Group, User, FlagType, BugUserLastVisit, Bugzilla, ...).
---

# Port a Bugzilla WebService module to Python

The Bug module (`python/app/modules/bug/`, PR into `migration/python`) is the reference
implementation; copy its shape. Perl stays the source of truth: a module is done when
`make parity` is green against **both** backends with the same pass count.

## 0. Ground rules

- Branch from `migration/python`. Only edit:
  - `parity/test_<module>.py` (new file, one per module)
  - `python/app/modules/<module>/{router.py,service.py,schemas.py,__init__.py}` (+ `writes.py` if large)
  - one line in `docker/python-proxy-routes.txt`
- Never edit `python/app/core/*`, `parity/conftest.py`, `parity/normalize.py` or another module's
  folder. If you need a shared helper, message the coordinator with the exact diff you want.
- Commit messages must contain `feature` or `bug`; PR description in Portuguese (repo rule) and
  ending with `Devin-Org: engineering`.

## 1. Run both backends

```sh
docker compose up -d --build            # Perl :8080, MariaDB, Python :8000 (code is bind-mounted)
docker compose logs bugzilla5.web | grep "Checksetup completed"
make seed                                # idempotent demo data (scripts/seed_demo_data.py)
docker compose restart bugzilla5.python  # after every Python edit (no --reload in the image)
```

Admin `admin@bugzilla.test / password01!`; seeded non-admin `alice.ng@bugzilla.test / Seed-pass-2024!`.
Perl errors/paths: `Bugzilla/WebService/<Module>.pm`, routes in
`Bugzilla/WebService/Server/REST/Resources/<Module>.pm` (`_rest_resources`), error codes in
`Bugzilla/WebService/Constants.pm`, messages in `template/en/default/global/{user,code}-error.html.tmpl`.

## 2. Parity tests first (against Perl)

1. Read `_rest_resources` for the module: every regex -> method, param mapping, `success_code`.
   Watch for Perl quirks (`POST /bug` uses `status_code` instead of `success_code` and answers 200).
2. Probe Perl with curl for each route: happy path, bad id, no auth, invalid value. Copy the
   **exact** `code`, HTTP status and message prefix into the tests. Do not guess codes.
3. Write `parity/test_<module>.py` using the fixtures in `parity/conftest.py`
   (`api`, `admin_token`, `user_token`, `seed`, `new_bug`, `seed_product_id`, `expect_error`, `ok`, `unique`).
   - Write tests create their own objects (unique names) and never mutate seeded `seed-NN` bugs.
   - Never depend on another module's endpoint; if you must (e.g. a product id), add a fixture
     with a fallback like `seed_product_id`.
4. `make parity TARGET=perl PYTEST_ARGS="-k <module>"` must be green, then the full suite.

## 3. Implement `python/app/modules/<module>/`

Layout (auto-discovered at startup, no registry to edit; `app/main.py` imports every
`app.modules.<pkg>.router:router` and mounts it under `/rest.cgi`):

```
router.py   FastAPI routes = the Perl _rest_resources table, in the same order; each handler is
            `def f(..., ctx: Ctx = Depends(context)): return json_response(ctx.request, service.x(ctx, ...), status)`
service.py  read side: lookups, *_to_hash builders, search; raise BugzillaError(name, message)
writes.py   create/update/... (optional split)
schemas.py  Pydantic v2 models for OpenAPI (response_model / openapi_extra); handlers return dicts
```

Shared helpers in `app/core/`:
- `rest.Ctx`: `ctx.params` = query string + JSON body merged with Perl's credential renames,
  `ctx.user` (`User.require_login()`, `is_insider`, `is_timetracker`, `in_group`, `group_ids`),
  `ctx.filter` (`include_fields`/`exclude_fields`: `filter.wants(name, types, prefix)` / `filter.apply(dict)`),
  `ctx.param_list("ids")` (scalar, csv or list -> list).
- `errors.BugzillaError(name, message, code=None)`: code from `WS_ERROR_CODE[name]` (else 32000),
  HTTP status from `REST_STATUS_CODE_MAP` (51/101/32614 -> 404, auth-ish codes -> 401, else 400).
  Helpers: `param_required`, `params_required`, `login_required`.
- `params.param(name)` reads Perl's `data/params.json` (mounted read-only).
- `util.iso_z` for `dateTime`, `auth.authenticate/issue_login_token/logout_all`.
- Reuse `app.modules.bug.service` helpers (`find_bug`, `check_bug`, `user_hash`, `object_does_not_exist`)
  by import; do not copy them.

Translate Perl `type()`s: `int` -> int, `boolean` -> bool, `string`/`email` -> str, `dateTime` -> `iso_z`,
`double` -> `num()` (integral doubles print as `0`, like JSON::XS), `base64` -> base64 str.
Perl `undef` -> `None`. Keep key names and nesting identical; `filter.apply` decides what is emitted.

Write paths must reproduce Perl's side effects on the shared schema: `bugs_activity` rows
(`fielddefs.id` by column name), `delta_ts`/`lastdiffed`, `longdescs` comment types,
`bugs_fulltext` sync, audit_log where Perl writes it (`Bugzilla::Object` create/update for
products, components, groups, ... insert `audit_log` rows; bugs do not).

## 4. Make it green against Python, then both

```sh
make parity TARGET=python PYTEST_ARGS="-k <module>"
make parity TARGET=python && make parity TARGET=perl     # same count on both
make parity-report                                       # endpoint | Perl | Python table for the PR
```

## 5. Cut the routes over

Add the module's path prefix(es) to `docker/python-proxy-routes.txt` (one regex alternative per
line, e.g. `product`, `field/bug`). `docker/startup.sh` turns them into `ProxyPassMatch` rules
when `BZ_PYTHON_UPSTREAM=http://bugzilla5.python:8000` is set:

```sh
BZ_PYTHON_UPSTREAM=http://bugzilla5.python:8000 docker compose up -d bugzilla5.web
curl -si http://127.0.0.1:8080/rest.cgi/<route> | grep -i x-bugzilla-backend   # -> python
```

Then check the classic UI still renders objects created through Python (e.g. buglist.cgi,
show_bug.cgi, editproducts.cgi).

## 6. PR into `migration/python`

Description (Portuguese): what was ported, the `make parity-report` table, Perl quirks the port
reproduces, anything intentionally not ported (say so explicitly), test plan with the commands above.

## Normalisation rules (parity/normalize.py)

Only three things are volatile and only `normalize()` may hide them: timestamps
(`YYYY-MM-DDTHH:MM:SSZ` -> `<datetime>`), login tokens (`<userid>-<cookie>` -> `<token>`),
auto-increment ids and numeric dict keys (`<id>`). A value that should be a timestamp/int but is not
raises `NormalizeError` (so a Python port returning `"2026-11-05 12:00:00"` fails). Everything
else is compared literally; never add fields to `DATETIME_KEYS`/`ID_KEYS` to make a test pass -
fix the port instead. Prefer asserting exact dicts after `normalize()` over `in`/`len` checks.

## Gotchas found while porting Bug

- Perl merges query string and JSON body into one param hash; `login`/`password` in the query
  string of **any** call authenticate it (only when both are present), `token` -> `Bugzilla_token`.
- A token without the `<digits>-` prefix is silently anonymous; with the prefix but unknown -> 400/32000.
  `/logout` deletes **all** of the user's logincookies.
- `GET /bug/{id}` returns `faults: []` next to `bugs`; `GET /bug` (search) does not.
- `include_fields=cc` implies `cc_detail` (same for `creator`, `assigned_to`, `qa_contact`);
  `exclude_fields` cannot remove the implied `*_detail` of an included parent.
- `/parameters`: anonymous -> 2 params, logged in -> ~31, `tweakparams` -> all (strings).
- Default values come from `params.json` (`defaultpriority` is `--`, which is **not** a legal
  priority, hence "bug create needs explicit priority"). Missing `op_sys`/`platform` -> code 50
  "You must select/enter a OS." because the defaults are empty strings.
- Validation order matters for which error wins: `Bugzilla::Object::_sort_by_dep` = fields without
  dependencies alphabetically, then dependent ones (component after product, assigned_to after component...).
- Error type names come from `field-descs.none.tmpl`: `Priority`, `Severity`, `status` (lowercase),
  `OS`, `Hardware`, `Resolution`; component/version messages add `in the 'X' product`.
- `bug_status` field values include a pseudo status with `name: null` (new-bug transitions).
- Marking a duplicate: self gets a type-1 comment (`"\n{body}\n*** This bug has been marked as a duplicate of bug N ***"`),
  the original gets a type-2 comment and the duplicate's reporter on CC; `dupe_of` is **not**
  written to `bugs_activity`.
- Attachment comments are type 5 with `extra_data` = attach id; text is `Created attachment N\n<description>`
  (+ `\n\n<comment>`); `is_patch` forces `text/plain`; unknown attachment id -> 200 with empty maps.
- `Comment.count` is the index among **all** comments of the bug (including private ones the caller can't see).
- Comment timestamps all share the DB second; Perl orders by `bug_when, comment_id`.
- Apache/mod_perl drops idle keep-alive connections: the parity client sends `Connection: close`.
- The Docker image had no `tzdata` and no `sendmail`; `TZ=Etc/UTC` and `mail_delivery_method=None`
  are set in the image. Bug.create/update commit before mail is sent, so a mailer error produces
  an error envelope **after** the row exists.
