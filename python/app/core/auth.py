"""Authentication exactly as Bugzilla::WebService::Server::REST + Bugzilla::Auth do it.

Credentials (query string or JSON body) are normalised by Bugzilla::WebService::Util:
login/password -> Bugzilla_login/Bugzilla_password, api_key -> Bugzilla_api_key, token -> Bugzilla_token.
Order: api_key, then token ("<userid>-<logincookie>", a token without that shape is ignored),
then login/password. A token with the right shape that is not in logincookies -> invalid_cookies_or_token.
"""
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import BugzillaError, login_required
from app.core.params import param
from app.core.util import bz_crypt, random_token


@dataclass
class User:
    id: int = 0
    login: str = ""
    realname: str = ""
    is_enabled: bool = True
    groups: set[str] = field(default_factory=set)
    group_ids: set[int] = field(default_factory=set)
    login_token: str | None = None  # the logincookie used for this request, if any

    @property
    def is_logged_in(self) -> bool:
        return self.id > 0

    def in_group(self, name: str) -> bool:
        return bool(name) and name in self.groups

    @property
    def is_insider(self) -> bool:
        return self.in_group(param("insidergroup"))

    @property
    def is_timetracker(self) -> bool:
        return self.in_group(param("timetrackinggroup"))

    def require_login(self):
        if not self.is_logged_in:
            raise login_required()


ANONYMOUS = User()


def load_user(db: Session, user_id: int) -> User | None:
    row = db.execute(text("SELECT userid, login_name, realname, is_enabled FROM profiles WHERE userid = :id"),
                     {"id": user_id}).first()
    if not row:
        return None
    if not row.is_enabled:
        raise BugzillaError("account_disabled", f"The user account {row.login_name} is disabled.")
    group_ids = user_group_ids(db, user_id)
    names = set()
    if group_ids:
        names = {r[0] for r in db.execute(text(f"SELECT name FROM groups WHERE id IN ({','.join(map(str, group_ids))})"))}
    return User(id=row.userid, login=row.login_name, realname=row.realname or "",
                is_enabled=bool(row.is_enabled), groups=names, group_ids=group_ids)


def user_group_ids(db: Session, user_id: int) -> set[int]:
    """Direct memberships plus groups inherited through group_group_map (grant_type 0 = member)."""
    direct = {r[0] for r in db.execute(text(
        "SELECT group_id FROM user_group_map WHERE user_id = :uid AND isbless = 0"), {"uid": user_id})}
    inherits: dict[int, set[int]] = {}
    for member, grantor in db.execute(text("SELECT member_id, grantor_id FROM group_group_map WHERE grant_type = 0")):
        inherits.setdefault(member, set()).add(grantor)
    seen, todo = set(), list(direct)
    while todo:
        g = todo.pop()
        if g in seen:
            continue
        seen.add(g)
        todo.extend(inherits.get(g, ()))
    return seen


def user_by_login(db: Session, login: str) -> User | None:
    row = db.execute(text("SELECT userid FROM profiles WHERE login_name = :l"), {"l": login}).first()
    return load_user(db, row.userid) if row else None


def authenticate(db: Session, params: dict) -> User:
    api_key = (params.get("Bugzilla_api_key") or "").strip()
    if api_key:
        row = db.execute(text("SELECT user_id, revoked FROM user_api_keys WHERE api_key = :k"), {"k": api_key}).first()
        if not row:
            raise BugzillaError("api_key_not_valid", "The API key you specified is invalid. Please check that you "
                                "typed it correctly.")
        if row.revoked:
            raise BugzillaError("api_key_revoked", "The API key you specified has been revoked. Please check that "
                                "you typed it correctly.")
        db.execute(text("UPDATE user_api_keys SET last_used = NOW() WHERE api_key = :k"), {"k": api_key})
        return load_user(db, row.user_id) or ANONYMOUS

    token = (params.get("Bugzilla_token") or "").strip()
    if token:
        user_id, _, cookie = token.partition("-")
        if user_id.isdigit() and cookie:
            row = db.execute(text("SELECT cookie FROM logincookies WHERE cookie = :c AND userid = :u "
                                  "AND (ipaddr = :ip OR ipaddr IS NULL)"),
                             {"c": cookie, "u": int(user_id), "ip": params.get("_remote_ip")}).first()
            if not row or row.cookie != cookie:
                raise BugzillaError("invalid_cookies_or_token",
                                    "The cookies or token provide were not valid or have expired. "
                                    "You may login again to get new cookies or a new token.")
            db.execute(text("UPDATE logincookies SET lastused = NOW() WHERE cookie = :c"), {"c": cookie})
            user = load_user(db, int(user_id)) or ANONYMOUS
            user.login_token = cookie
            return user

    login = params.get("Bugzilla_login")
    password = params.get("Bugzilla_password")
    if login is not None and password is not None:
        row = db.execute(text("SELECT userid, cryptpassword FROM profiles WHERE login_name = :l"),
                         {"l": str(login)}).first()
        if not row or not bz_crypt(str(password), row.cryptpassword or ""):
            raise BugzillaError("invalid_login_or_password", "The login or password you entered is not valid.")
        return load_user(db, row.userid) or ANONYMOUS
    return ANONYMOUS


def issue_login_token(db: Session, user: User, remote_ip: str | None = None, restrict: bool = False) -> str:
    """Bugzilla::Auth::Persist::Cookie::persist_login for web services: insert a logincookie."""
    while True:
        cookie = random_token(10)
        if not db.execute(text("SELECT 1 FROM logincookies WHERE cookie = :c"), {"c": cookie}).first():
            break
    db.execute(text("INSERT INTO logincookies (cookie, userid, ipaddr, lastused) VALUES (:c, :u, :ip, NOW())"),
               {"c": cookie, "u": user.id, "ip": remote_ip if restrict else None})
    db.execute(text("DELETE FROM logincookies WHERE lastused < NOW() - INTERVAL 30 DAY"))
    return f"{user.id}-{cookie}"


def logout_all(db: Session, user: User):
    if user.is_logged_in:
        db.execute(text("DELETE FROM logincookies WHERE userid = :u"), {"u": user.id})
