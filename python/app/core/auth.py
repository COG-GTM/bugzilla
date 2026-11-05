"""Authentication exactly as Bugzilla::Auth does it for WebService calls.

Credentials are read from (in Perl's order): ``Bugzilla_api_key``/``api_key``,
``Bugzilla_token``/``token`` (``"<userid>-<logincookie>"``), and
``Bugzilla_login``/``Bugzilla_password`` (or ``login``/``password``). Tokens are rows in
``logincookies``; API keys are rows in ``user_api_keys``. Passwords are ``bz_crypt``:
``salt,base64(sha256(password . salt)){SHA-256}``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
from dataclasses import dataclass, field

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import BugzillaError, param_required, user_error

CREDENTIAL_PARAMS = {
    "Bugzilla_login", "Bugzilla_password", "Bugzilla_token", "Bugzilla_api_key", "Bugzilla_restrictlogin",
    "login", "password", "token", "api_key", "restrict_login",
}
_PASSWORD_CHARS = string.ascii_letters + string.digits
MAX_LOGINCOOKIE_AGE_DAYS = 30


@dataclass
class User:
    id: int = 0
    login: str = ""
    realname: str = ""
    is_enabled: bool = True
    groups: set[str] = field(default_factory=set)
    login_token: str | None = None

    @property
    def is_logged_in(self) -> bool:
        return self.id > 0

    def in_group(self, name: str) -> bool:
        return name in self.groups

    @property
    def identity(self) -> str:
        return f"{self.realname} <{self.login}>" if self.realname else self.login


def bz_crypt(password: str, crypted: str) -> str:
    salt, _, rest = crypted.partition(",")
    algorithm = rest[rest.rfind("{") + 1 : -1] if rest.endswith("}") else ""
    if not algorithm:
        import crypt  # legacy DES/MD5 crypt passwords (pre-Bugzilla 4)

        return crypt.crypt(password, crypted)
    digest = hashlib.new(algorithm.replace("-", "").lower(), (password + salt).encode("utf-8")).digest()
    return f"{salt},{base64.b64encode(digest).decode().rstrip('=')}{{{algorithm}}}"


def generate_random_password(length: int = 10) -> str:
    return "".join(secrets.choice(_PASSWORD_CHARS) for _ in range(length))


def fix_credentials(params: dict) -> dict:
    """WebService::Util::fix_credentials: the short names win over the Bugzilla_* ones."""
    if "login" in params and "password" in params:
        params["Bugzilla_login"] = params.pop("login")
        params["Bugzilla_password"] = params.pop("password")
    if "api_key" in params:
        params["Bugzilla_api_key"] = params.pop("api_key")
    if "token" in params:
        params["Bugzilla_token"] = params.pop("token")
    if "restrict_login" in params:
        params["Bugzilla_restrictlogin"] = params.pop("restrict_login")
    return params


def remote_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def load_user(session: Session, user_id: int) -> User | None:
    row = session.execute(
        text("SELECT userid, login_name, realname, is_enabled FROM profiles WHERE userid = :id"), {"id": user_id}
    ).first()
    if row is None:
        return None
    # direct memberships, then expand through group_group_map (grant_type 0 = GROUP_MEMBERSHIP)
    ids = set(
        session.execute(
            text("SELECT group_id FROM user_group_map WHERE user_id = :id AND isbless = 0"), {"id": user_id}
        ).scalars()
    )
    frontier = set(ids)
    while frontier:
        inherited = set(
            session.execute(
                text(
                    "SELECT grantor_id FROM group_group_map WHERE grant_type = 0 AND member_id IN ("
                    + ",".join(str(int(i)) for i in frontier) + ")"
                )
            ).scalars()
        )
        frontier = inherited - ids
        ids |= inherited
    groups = set(
        session.execute(
            text("SELECT name FROM groups WHERE id IN (" + ",".join(str(int(i)) for i in ids) + ")")
        ).scalars()
    ) if ids else set()
    return User(id=row.userid, login=row.login_name, realname=row.realname or "", is_enabled=bool(row.is_enabled), groups=groups)


def user_by_login(session: Session, login: str) -> User | None:
    row = session.execute(text("SELECT userid FROM profiles WHERE login_name = :l"), {"l": login}).first()
    return load_user(session, row.userid) if row else None


def _login_with_api_key(session: Session, api_key: str) -> User:
    row = session.execute(
        text("SELECT id, user_id, api_key, revoked FROM user_api_keys WHERE api_key = :k"), {"k": api_key}
    ).first()
    if row is None or row.api_key != api_key:
        raise user_error("api_key_not_valid")
    if row.revoked:
        raise user_error("api_key_revoked")
    session.execute(text("UPDATE user_api_keys SET last_used = NOW() WHERE id = :id"), {"id": row.id})
    return load_user(session, row.user_id)


def _login_with_token(session: Session, token: str, ip: str) -> User:
    user_id_s, _, cookie = token.partition("-")
    user_id = int(user_id_s) if user_id_s.isdigit() else 0
    row = None
    if cookie and user_id:
        row = session.execute(
            text(
                "SELECT cookie FROM logincookies WHERE cookie = :c AND userid = :u "
                "AND (ipaddr = :ip OR ipaddr IS NULL)"
            ),
            {"c": cookie, "u": user_id, "ip": ip},
        ).first()
    if row is None or row.cookie != cookie:
        raise BugzillaError("invalid_cookies_or_token", user_error("invalid_token").message, code=32000)
    session.execute(text("UPDATE logincookies SET lastused = NOW() WHERE cookie = :c"), {"c": cookie})
    user = load_user(session, user_id)
    user.login_token = cookie
    return user


def _login_with_password(session: Session, login: str, password: str, ip: str, restrict: bool) -> User:
    row = session.execute(
        text("SELECT userid, cryptpassword, is_enabled FROM profiles WHERE login_name = :l"), {"l": login}
    ).first()
    if row is None or not row.cryptpassword or bz_crypt(password, row.cryptpassword) != row.cryptpassword:
        raise user_error("invalid_username_or_password")
    if not row.is_enabled:
        raise user_error("account_disabled", disabled_reason="")
    cookie = generate_random_password()
    session.execute(
        text("INSERT INTO logincookies (cookie, userid, ipaddr, lastused) VALUES (:c, :u, :ip, NOW())"),
        {"c": cookie, "u": row.userid, "ip": ip if restrict else None},
    )
    session.execute(
        text("DELETE FROM logincookies WHERE lastused < DATE_SUB(NOW(), INTERVAL :d DAY)"),
        {"d": MAX_LOGINCOOKIE_AGE_DAYS},
    )
    session.execute(text("UPDATE profiles SET last_seen_date = NOW() WHERE userid = :u"), {"u": row.userid})
    user = load_user(session, row.userid)
    user.login_token = cookie
    return user


def authenticate(session: Session, params: dict, request: Request) -> User:
    """Bugzilla->login(LOGIN_OPTIONAL) for a WebService request. Mutates ``params`` (strips credentials)."""
    fix_credentials(params)
    ip = remote_ip(request)
    api_key = params.pop("Bugzilla_api_key", None)
    token = params.pop("Bugzilla_token", None)
    login = params.pop("Bugzilla_login", None)
    password = params.pop("Bugzilla_password", None)
    restrict = str(params.pop("Bugzilla_restrictlogin", "0")) in ("1", "true", "True")
    if api_key:
        return _login_with_api_key(session, str(api_key))
    if token:
        return _login_with_token(session, str(token), ip)
    if login is not None and password is not None:
        return _login_with_password(session, str(login), str(password), ip, restrict)
    if login is not None and password is None:
        # User.login checks both names before authenticating.
        return User()
    return User()


def require_login(user: User) -> User:
    if not user.is_logged_in:
        raise user_error("login_required")
    return user


def login_endpoint(session: Session, params: dict, request: Request) -> dict:
    """User.login: requires login+password (unless already authenticated some other way)."""
    user = authenticate(session, dict(params), request)
    if user.is_logged_in and user.login_token is None and not (
        params.get("login") is not None or params.get("Bugzilla_login") is not None
    ):
        return {"id": user.id}
    for name in ("login", "password"):
        if params.get(name) is None and params.get("Bugzilla_" + name) is None:
            raise param_required(name)
    if not user.is_logged_in:
        user = authenticate(session, dict(params), request)
    out = {"id": user.id}
    if user.login_token:
        out["token"] = f"{user.id}-{user.login_token}"
    return out


def logout_endpoint(session: Session, user: User) -> None:
    if user.is_logged_in and user.login_token:
        session.execute(text("DELETE FROM logincookies WHERE cookie = :c AND userid = :u"), {"c": user.login_token, "u": user.id})


def valid_login_endpoint(session: Session, params: dict, user: User) -> bool:
    if params.get("login") is None:
        raise param_required("login")
    return bool(user.is_logged_in and user.login == params["login"])
