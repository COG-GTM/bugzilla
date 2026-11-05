---
name: port-bugzilla-module
description: Port one Bugzilla WebService module (Product, Component, Classification, Group, User, FlagType, BugUserLastVisit, ...) from Perl to the Python FastAPI service in python/, prove parity with the parity/ harness, and route it through the Apache strangler proxy. Use when asked to migrate, port or add a Bugzilla REST module to Python on the migration/python branch.
---

# Port a Bugzilla WebService module to Python

The Bug slice (`python/app/modules/bug/`, PR into `migration/python`) is the template.
Perl stays the source of truth: a module is "done" when `make parity TARGET=python`
has the same pass count as `make parity TARGET=perl` and Apache routes it to Python.

## Steps

1. **Branch** from `migration/python`: `git checkout -b devin/<ts>-python-<module>`.
2. **Extend the parity tests first** (see below) and record goldens from Perl:
   `docker compose up -d --build && make seed && make parity-update-golden && make parity`
   (must be green on Perl before any Python is written).
3. **Read the Perl**, in this order, and keep notes of every error name + message:
   - `Bugzilla/WebService/<Module>.pm` – method list, `_*_to_hash` shapes, `validate()` keys,
     `filter_wants` types (`_default`/`_extra`/`_custom`).
   - `Bugzilla/WebService/Server/REST/Resources/<Module>.pm` – URL regexes, which path
     parameter becomes which WebService param, `success_code` (201) vs `status_code` (ignored → 200).
   - `Bugzilla/<Module>.pm` – `VALIDATORS`, `VALIDATOR_DEPENDENCIES`, `UPDATE_COLUMNS`,
     `_check_*` subs; `Bugzilla/Object.pm::run_create_validators` / `_sort_by_dep` for order.
   - `Bugzilla/WebService/Constants.pm` (`WS_ERROR_CODE`, `REST_STATUS_CODE_MAP`) and
     `template/en/default/global/user-error.html.tmpl` / `code-error.html.tmpl` for exact text.
4. **Create `python/app/modules/<module>/`** with `router.py`, `service.py`, `schemas.py`
   (add `model.py`/`validators.py`/`search.py` helpers if the module is big, as Bug does).
   Routers are auto-discovered: no central list to edit, no merge conflicts between modules.
5. **Run it**: `make parity TARGET=python` (fixtures for not-yet-ported resources come from
   Perl via `FIXTURE_BASE_URL`, set by the Makefile). Iterate until the pass count matches Perl.
6. **Route it**: add the resource prefix to the two `ProxyPassMatch` lines in
   `docker/000-default.conf`, rebuild (`docker compose up -d --build`), run `make parity`
   (TARGET=perl now goes through Apache → Python for your routes) and click through the
   classic UI pages that read the tables you write.
7. **PR into `migration/python`** with the endpoint table (`endpoint | Perl | Python`) and the
   pass counts of both targets. Commit messages must contain `feature` or `bug`.

## File layout

```
python/app/
  main.py                     FastAPI app, router auto-discovery, error handlers, OPTIONS, 32614 catch-all
  core/config.py              env (BZ_DB_*, BZ_PARAMS_FILE) + Bugzilla params.json (shared data volume)
  core/db.py                  SQLAlchemy 2.x engine, per-request session (commit/rollback)
  core/models.py              tables reflected from the live schema (checksetup.pl owns the DDL)
  core/errors.py              BugzillaError, WS_ERROR_CODE, REST_STATUS_CODE_MAP, MESSAGES (byte-exact)
  core/auth.py                api_key / token ("<uid>-<logincookie>") / login+password, bz_crypt, groups
  core/request.py             Ctx: query+JSON params merged, credentials stripped, user resolved
  core/fields.py              include_fields / exclude_fields (filter_wants)
  core/responses.py           JSON/JSONP envelope, CORS, ETag + 304, {"result": scalar} wrapping
  modules/<module>/router.py  one handler per REST regex in Resources/<Module>.pm
  modules/<module>/service.py the WebService methods (same names as the Perl subs)
  modules/<module>/schemas.py Pydantic shapes (documentation; dicts are built per include_fields)
```

Handlers call `service.<method>(ctx)`; the path parameter is injected exactly like Perl does
(`/bug/{id}` → `ids=[id]`, `/bug/comment/{id}` → `comment_ids=[id]`).

## Extending the parity tests first

- One file per module: `parity/test_<module>.py`; fixtures shared in `parity/conftest.py`.
- For every endpoint cover: happy path, `include_fields`/`exclude_fields`, no auth (410),
  bad ids (404/51 vs 400/100), invalid field values (assert `code` *and* HTTP status).
- Build fixtures idempotently through the API (`product`, `user` fixtures show the pattern) and
  make them unique per run with `run_id`.
- Use `snapshot(resp[, label])` for any body you want pinned; `make parity-update-golden`
  re-records from Perl only. Never hand-edit goldens.
- Anything that calls a not-yet-ported resource inside your test must use `fixture_api`
  (Perl), not `api`; swap it to `api` when that module lands.

## Normalisation rules (`parity/normalize.py`)

- IDs: `id`, `*_id`, `ids`, numeric map keys (`"bugs": {"17": ...}` → `<id:1>`, `<id:2>` in
  ascending order), numeric strings in `added`/`removed` **only** for id-bearing change fields
  (`depends_on`, `blocks`, `dupe_of`), `Bug #N` / `'N'` in error messages → `<n>`.
- Times: any ISO/SQL timestamp or `*_time`/`when`/`bug_when` key → `<datetime>`.
- Secrets: `token`, `update_token`, `api_key`, `etag` → `<token>`.
- Run data: the per-run `run_id` → `<run>`; configured admin email/realname → `<admin-email>`
  / `<admin-name>` (whole-token match).
- Order: `changes` lists and unordered scalar lists (`cc`, `groups`, `keywords`, `alias`,
  `depends_on`, `blocks`, `ids`) are sorted; everything else keeps server order.
- Add a rule only when a value is volatile *on Perl itself* between runs; add a unit test in
  `parity/test_normalize.py`.

## Gotchas hit while porting Bug

- **Validation order matters for which error you get.** Perl validates no-dependency fields
  alphabetically, then dependent ones (`component` after `product`, `bug_status` after
  `comment`/`target_milestone`...). `validators.sort_by_dep` reproduces it; reuse it.
- **Same error, two codes.** `Bugzilla::User->check` → 51 (404) but `login_to_id(..., THROW_ERROR)`
  → 504 (401). Bug.create's `cc` uses the latter, Bug.update's `cc` the former.
- **HTTP status comes from the code**, via `REST_STATUS_CODE_MAP`; unknown error names are
  32000/400 (that is why a bad token is 32000, not 307).
- **`POST /bug` answers 200**: the resource says `status_code` instead of `success_code`.
  `POST .../comment` and `.../attachment` are 201.
- **Scalars are wrapped**: `logout` → `{"result": null}`, `valid_login` → `{"result": false}`.
- **Doubles print as ints** when integral (`estimated_time: 0`), and attachment flags
  (`is_patch`...) are ints while bug flags (`is_open`...) are booleans.
- **Comment `count` is positional** in `Bug.comments` by bug (0 = description) but computed
  with `COUNT(bug_when <= ts) - 1` when fetched by `comment_ids`.
- **`dup_id` is returned in `changes` but never logged** to `bugs_activity` (no fielddefs row);
  `dupe_of` gets a CMT_DUPE_OF comment on the dupe and CMT_HAS_DUPE on the original.
- **Group membership is inherited** through `group_group_map` (admin is in every group only
  that way) and regexp grants are materialised in `user_group_map` (grant_type 2).
- **Insider / timetracker are params** (`insidergroup`, `timetrackinggroup`), read from the
  Perl `data/params.json` on the shared volume; the Python container mounts it read-only.
- **Keep `bugs_fulltext`, `delta_ts`, `lastdiffed` semantics** or the classic UI (buglist,
  show_bug, quicksearch) diverges even though the REST contract is green.
- **Apache/mod_perl drops keep-alive** (`Apache2::SizeLimit`); the parity client sends
  `Connection: close`. Tokens are stored without IP (`logincookies.ipaddr NULL`) unless
  `restrict_login`, so a Perl-issued token works on Python and vice-versa.
- **Search is a subset** (`modules/bug/search.py` docstring lists it); boolean charts
  (`f1/o1/v1`) and saved searches are out of scope until the Search unit is ported.
