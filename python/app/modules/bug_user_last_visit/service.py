"""BugUserLastVisit.update / BugUserLastVisit.get (Bugzilla/WebService/BugUserLastVisit.pm).

Behaviour is pinned to the running Perl 5.2 app, quirks included:
* ``$user->login(LOGIN_REQUIRED)`` resolves to the Bugzilla::User::login *accessor*, so no login is enforced;
  anonymous callers fall through to ``user_not_involved``.
* ``Bugzilla::Bug->check({id => ..., cache => 1})`` loses the id on a miss, so unknown numeric ids come back
  as ``improper_bug_id_field_value`` with the "You must enter a valid bug number!" wording, not as 101.
* ``get`` dies calling ``->id`` on the plain bug id (line 78) and the REST layer answers HTTP 200 with code 100500.
"""
from sqlalchemy import text

from app.core.errors import BugzillaError, param_required
from app.core.params import param
from app.core.rest import Ctx
from app.core.util import iso_z
from app.modules.bug.service import check_bug
from app.modules.bug.writes import db_now, truthy

PERL_GET_CRASH = ('Can\'t locate object method "id" via package "{id}" (perhaps you forgot to load "{id}"?) '
                  'at /var/www/html/Bugzilla/WebService/BugUserLastVisit.pm line 78.\n')
ERROR_UNKNOWN_FATAL_REST = 100500


def invalid_bug_number() -> BugzillaError:
    return BugzillaError("improper_bug_id_field_value", "You must enter a valid bug number!")


def ids_param(ctx: Ctx, path_id: str) -> list:
    """``validate($params, 'ids')`` after the REST merge: query string > JSON body > path id (never csv-split)."""
    if "ids" not in ctx.params:
        return [path_id]
    ids = ctx.params["ids"]
    if not isinstance(ids, list):
        ids = [ids]
    if any(isinstance(v, (list, dict)) for v in ids):
        raise BugzillaError("param_scalar_array_required",
                            "The ids parameter must be an array of scalars (integers and/or strings).")
    return ids


def check_bug_by_id_param(ctx: Ctx, value):
    """``Bugzilla::Bug->check({id => $value, cache => 1})``: same lookup/visibility as Bug->check($value),
    except that undef/empty/unknown-numeric ids are reported without a bug number."""
    shown = None if value is None else str(value).strip()
    if not shown:
        raise invalid_bug_number()
    try:
        return check_bug(ctx, shown)
    except BugzillaError as err:
        if err.name == "bug_id_does_not_exist":
            raise invalid_bug_number() from None
        raise


def is_involved_in_bug(ctx: Ctx, row) -> bool:
    """Bugzilla::User::is_involved_in_bug: assignee, reporter, QA contact (if enabled) or on the CC list."""
    user = ctx.user
    if not user.is_logged_in:
        return False
    if row["assigned_to"] == user.id or row["reporter"] == user.id:
        return True
    if truthy(param("useqacontact")) and row["qa_contact"] == user.id:
        return True
    return ctx.db.execute(text("SELECT 1 FROM cc WHERE bug_id = :b AND who = :u"),
                          {"b": row["bug_id"], "u": user.id}).first() is not None


def update_user_last_visit(ctx: Ctx, bug_id: int, last_visit_ts) -> None:
    """Bugzilla::Bug::update_user_last_visit: update the (user, bug) row or create it."""
    binds = {"b": bug_id, "u": ctx.user.id, "ts": last_visit_ts}
    updated = ctx.db.execute(text("UPDATE bug_user_last_visit SET last_visit_ts = :ts "
                                  "WHERE bug_id = :b AND user_id = :u"), binds).rowcount
    if not updated:
        ctx.db.execute(text("INSERT INTO bug_user_last_visit (user_id, bug_id, last_visit_ts) "
                            "VALUES (:u, :b, :ts)"), binds)


def to_hash(ctx: Ctx, bug_id: int, last_visit_ts) -> dict:
    return ctx.filter.apply({"id": bug_id, "last_visit_ts": iso_z(last_visit_ts)})


def update(ctx: Ctx, path_id: str) -> list[dict]:
    ids = ids_param(ctx, path_id)
    if not ids:
        raise param_required("ids")
    last_visit_ts = db_now(ctx.db)  # one SELECT NOW() per request, shared by every row
    out = []
    for value in ids:
        row = check_bug_by_id_param(ctx, value)
        if not is_involved_in_bug(ctx, row):
            raise BugzillaError("user_not_involved", f"Sorry, but you are not involved with Bug {row['bug_id']}.")
        update_user_last_visit(ctx, row["bug_id"], last_visit_ts)
        out.append(to_hash(ctx, row["bug_id"], last_visit_ts))
    return out


def get(ctx: Ctx, path_id: str) -> dict:
    """Reproduces the Perl fatal error so the strangler proxy is transparent (see module docstring)."""
    return BugzillaError("unknown_fatal", PERL_GET_CRASH.format(id=path_id), code=ERROR_UNKNOWN_FATAL_REST).envelope()
