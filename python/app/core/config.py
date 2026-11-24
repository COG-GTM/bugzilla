"""Settings. Database credentials come from the environment; Bugzilla's own
parameters and the token-signing secret are read from the Perl data directory,
which the container mounts read-only."""
import json
import os
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(os.environ.get("BZ_DATA_DIR", "/var/www/html/data"))
DOCS_URL = "https://bugzilla.readthedocs.org/en/5.0/api/"


def database_url():
    env = os.environ
    return "mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4".format(
        user=env.get("BZ_DB_USER", "bugs"), password=env.get("BZ_DB_PASS", "bugzilla"),
        host=env.get("BZ_DB_HOST", "127.0.0.1"), port=env.get("BZ_DB_PORT", "3306"),
        name=env.get("BZ_DB_NAME", "bugs"),
    )


@lru_cache
def _params_file(mtime):
    return json.loads((DATA_DIR / "params.json").read_text())


def param(name, default=""):
    """A Bugzilla parameter (data/params.json), reloaded when the file changes."""
    path = DATA_DIR / "params.json"
    try:
        value = _params_file(path.stat().st_mtime).get(name)
    except OSError:
        return default
    return default if value is None else value


def site_wide_secret():
    """Perl's localconfig secret, exported by docker/startup.sh. Needed so that
    update tokens issued here are accepted by process_bug.cgi."""
    if os.environ.get("BZ_SITE_WIDE_SECRET"):
        return os.environ["BZ_SITE_WIDE_SECRET"]
    return (DATA_DIR / "python_site_wide_secret").read_text().strip()
