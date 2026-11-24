import base64
import hashlib
import secrets
import string
from datetime import datetime, timezone

ALNUM = string.ascii_letters + string.digits


def iso_z(value) -> str | None:
    """Bugzilla dateTime: YYYY-MM-DDTHH:MM:SSZ. DB datetimes are stored in UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def random_token(length: int = 10) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(length))


def bz_crypt(password: str, crypted: str) -> bool:
    """Verify a password against profiles.cryptpassword (Bugzilla::Util::bz_crypt).

    Format: ``<salt>,<base64 digest without padding>{<ALGORITHM>}``.
    """
    if not crypted or "{" not in crypted:
        return False
    algorithm = crypted[crypted.rindex("{") + 1:-1]
    salt = crypted.split(",", 1)[0]
    try:
        hasher = hashlib.new(algorithm.replace("-", "").lower())
    except ValueError:
        return False
    hasher.update(password.encode("utf-8") + salt.encode("utf-8"))
    digest = base64.b64encode(hasher.digest()).decode().rstrip("=")
    return f"{salt},{digest}{{{algorithm}}}" == crypted


def as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
