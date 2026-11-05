# Migration status — REST WebService modules (Perl → Python)

Branch: `migration/python`. Every module below is served by the FastAPI service (`python/`) behind the Apache
strangler proxy (`docker/python-proxy-routes.d/*.txt`); anything not ported falls back to Perl automatically.

Full parity suite on the merged branch (`make parity-report`, 2026-11-05): **Perl 541/541 · Python 541/541**
(119 tests before the fan-out → 541 after). The suite also passes against the mixed stack (`:8080` with the proxy on).

| module | child session | PR | parity tests (Perl / Python) | LOC Perl → Python¹ | ACUs | status |
|---|---|---|---|---|---|---|
| Bug | coordinator (Phase 3) — [session](https://devin-gtm.devinenterprise.com/sessions/17a5d0f11b0a45f8adad667adea0c91a) | [#8](https://github.com/COG-GTM/bugzilla/pull/8) | 88 / 88 | 4 871 → 2 002 | — (coordinator) | merged |
| Bugzilla | [d98dac57](https://devin-gtm.devinenterprise.com/sessions/d98dac572f954baf80a636c3f4581153) | [#10](https://github.com/COG-GTM/bugzilla/pull/10) | 94 / 94 | 576 → 270 | 12.4 | merged |
| User | [a141b448](https://devin-gtm.devinenterprise.com/sessions/a141b448dbc2461db070b1704d5d5371) (login/logout/valid_login by coordinator) | [#18](https://github.com/COG-GTM/bugzilla/pull/18) | 87 / 87 | 1 175 → 876 | 20.4 | merged |
| Product | [2817cc94](https://devin-gtm.devinenterprise.com/sessions/2817cc94e20a4a61b03a6afd8d65c423) | [#15](https://github.com/COG-GTM/bugzilla/pull/15) | 36 / 36 | 1 035 → 616 | 22.4 | merged |
| Component | [6caca3f3](https://devin-gtm.devinenterprise.com/sessions/6caca3f3eedc43c2a1ad0f62d2396e02) | [#13](https://github.com/COG-GTM/bugzilla/pull/13) | 57 / 57 | 196 → 349 | 19.0 | merged |
| Classification | [f623e359](https://devin-gtm.devinenterprise.com/sessions/f623e359a5c948a0a15cc27047d98002) | [#11](https://github.com/COG-GTM/bugzilla/pull/11) | 32 / 32 | 275 → 207 | 13.5 | merged |
| Group | [2e42cb24](https://devin-gtm.devinenterprise.com/sessions/2e42cb2445954625b96e9dbcb68c694c) | [#12](https://github.com/COG-GTM/bugzilla/pull/12) | 38 / 38 | 670 → 560 | 23.8 | merged |
| FlagType | [b6fff3c0](https://devin-gtm.devinenterprise.com/sessions/b6fff3c0f0f3483eb90981b4d6f5ef24) | [#14](https://github.com/COG-GTM/bugzilla/pull/14) | 72 / 72 | 915 → 637 | 22.8 | merged |
| BugUserLastVisit | [9a190e70](https://devin-gtm.devinenterprise.com/sessions/9a190e70dbce473e8992b0728d3ebc54) | [#17](https://github.com/COG-GTM/bugzilla/pull/17) | 37 / 37 | 259 → 146 | 14.9 | merged |
| **total** | 8 children + coordinator | 9 PRs | **541 / 541** | **9 972 → 5 663** (+ 722 shared `app/core`, 3 625 test lines) | **149.2** (children) | all merged |

¹ `wc -l` of `Bugzilla/WebService/<Module>.pm` + `Server/REST/Resources/<Module>.pm` vs `python/app/modules/<module>/*.py`.
The domain modules behind them (`Bugzilla/Bug.pm`, `Search.pm`, `Product.pm`, `User.pm`, …, ~10 k LOC for Bug alone)
were ported only along the REST-reachable paths and stay alive in Perl for the HTML CGIs.

## Merge order and shared-core changes made by the coordinator

Merged in dependency order: Classification → Bugzilla → Product → Component → FlagType → BugUserLastVisit → Group → User,
rerunning the full suite on both backends after each merge. Children only touched their own folders; shared files were
changed by the coordinator on request:

- `parity/report.py`: one report group per module.
- `python/app/core/rest.py`: Perl's JSONP `callback` regex and message; query string overrides the JSON body.
- `python/app/core/errors.py`: Perl `params_required` text.
- `python/app/core/db.py`: `READ COMMITTED` isolation (pooled connections served stale snapshots → intermittent failures).
- `python/app/main.py`: unhandled exceptions → Bugzilla envelope code 100500; unported `/rest.cgi` resources forwarded to
  the proxy-free Perl listener `:8081` (`BZ_PERL_UPSTREAM`).
- `python/app/modules/bug/writes.py`: `value_inactive` for inactive components; `bug_user_last_visit` sync on `Bug.update`.
- `docker-compose.yml`: `extensions/` mounted into the Python container (`/extensions` parity).
- Cross-suite test isolation: classification product order (Perl sorts case-insensitively), User group lists tolerate the
  `parity_group_*` regexp groups created by the Group suite, BugUserLastVisit admin test files its own bug.

## Still on Perl (REST)

`Bug.update_attachment`, `Bug.search_comment_tags` / `update_comment_tags`, `Bug.update_see_also`, flags on bugs/attachments,
custom fields, `User.offer_account_by_email` (JSON-RPC only), e-mail (BugMail), `audit_log` writes. Everything that is not
REST — the HTML CGIs, Template Toolkit UI, whine/jobqueue, checksetup — is untouched.
