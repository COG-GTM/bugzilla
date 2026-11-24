"""Authentication against the tables the Perl app owns, so a token issued by
Perl works here and the reverse (Bugzilla/Auth/Login/Cookie.pm, APIKey.pm).

- ``token``  (also Bugzilla_token): ``<user_id>-<cookie>`` checked in logincookies.
- ``api_key`` (also Bugzilla_api_key): checked in user_api_keys.
"""
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .config import param, site_wide_secret
from .db import db_now, get_session, t
from .errors import invalid_api_key, invalid_token, login_required
from .params import request_params


@dataclass
class User:
    id: int = 0
    login: str = ""
    real_name: str = ""
    groups: set = field(default_factory=set)

    def in_group(self, name):
        return bool(name) and name in self.groups

    @property
    def is_insider(self):
        return self.in_group(param("insidergroup"))

    @property
    def is_timetracker(self):
        return self.in_group(param("timetrackinggroup"))


ANONYMOUS = User()


def _group_names(session, user_id):
    """Direct memberships plus groups inherited through group_group_map (type 0 = member)."""
    ugm, ggm, groups = t.user_group_map, t.group_group_map, t.groups
    ids = set(session.scalars(select(ugm.c.group_id).where(ugm.c.user_id == user_id, ugm.c.isbless == 0)))
    edges = session.execute(select(ggm.c.member_id, ggm.c.grantor_id).where(ggm.c.grant_type == 0)).all()
    grew = True
    while grew:
        new = {grantor for member, grantor in edges if member in ids} - ids
        ids |= new
        grew = bool(new)
    return set(session.scalars(select(groups.c.name).where(groups.c.id.in_(ids)))) if ids else set()


def _load_user(session, user_id):
    p = t.profiles
    row = session.execute(select(p.c.userid, p.c.login_name, p.c.realname, p.c.is_enabled)
                          .where(p.c.userid == user_id)).first()
    if row is None or not row.is_enabled:
        return None
    return User(row.userid, row.login_name, row.realname, _group_names(session, row.userid))


def _from_token(session, token, client_ip):
    user_id, _, cookie = str(token).partition("-")
    # Perl ignores a token that is not "<user_id>-<cookie>" instead of rejecting it.
    if not (user_id.isdigit() and cookie):
        return ANONYMOUS
    lc = t.logincookies
    found = session.execute(select(lc.c.cookie).where(
        lc.c.cookie == cookie, lc.c.userid == int(user_id),
        or_(lc.c.ipaddr == client_ip, lc.c.ipaddr.is_(None)))).first()
    user = _load_user(session, int(user_id)) if found else None
    if user is None:
        raise invalid_token()
    session.execute(update(lc).where(lc.c.cookie == cookie).values(lastused=db_now(session)))
    session.commit()
    return user


def _from_api_key(session, api_key):
    keys = t.user_api_keys
    row = session.execute(select(keys.c.id, keys.c.user_id).where(
        keys.c.api_key == str(api_key), keys.c.revoked == 0)).first()
    user = _load_user(session, row.user_id) if row else None
    if user is None:
        raise invalid_api_key()
    session.execute(update(keys).where(keys.c.id == row.id).values(last_used=db_now(session)))
    session.commit()
    return user


async def current_user(request: Request, params: dict = Depends(request_params),
                       session: Session = Depends(get_session)) -> User:
    """The logged-in user, or ANONYMOUS. Invalid credentials are an error."""
    api_key = params.get("Bugzilla_api_key") or params.get("api_key")
    if api_key:
        return _from_api_key(session, api_key)
    token = params.get("Bugzilla_token") or params.get("token")
    if token:
        return _from_token(session, token, request.client.host if request.client else "")
    return ANONYMOUS


def require_login(user: User):
    if not user.id:
        raise login_required()
    return user


def issue_hash_token(user: User, data, client_ip="", now=None):
    """Bugzilla::Token::issue_hash_token, signed with Perl's site_wide_secret."""
    now = int(now or time.time())
    message = "*".join(str(part) for part in [now, user.id or client_ip, *data])
    digest = hmac.new(site_wide_secret().encode(), message.encode(), hashlib.sha256).digest()
    encoded = base64.b64encode(digest).decode().rstrip("=").replace("+", "-").replace("/", "_")
    return f"{now}-{encoded}"
