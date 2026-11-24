"""Read-only view of Bugzilla's data/params.json (the Perl app owns it)."""
import json
import os
import time

from app.core.config import settings

_cache: dict = {"mtime": None, "data": {}, "checked": 0.0}

DEFAULTS = {
    "maintainer": "", "requirelogin": "0", "insidergroup": "", "comment_taggers_group": "",
    "timetrackinggroup": "", "defaultpriority": "---", "defaultseverity": "normal",
    "defaultplatform": "", "defaultopsys": "", "useqacontact": "0", "usetargetmilestone": "0",
    "usestatuswhiteboard": "0", "letsubmitterchoosepriority": "1", "maxattachmentsize": "1000",
    "urlbase": "", "utf8": "1", "emailsuffix": "", "strict_isolation": "0",
}


def params() -> dict:
    now = time.time()
    if now - _cache["checked"] > 2:
        _cache["checked"] = now
        try:
            mtime = os.stat(settings.params_path).st_mtime
            if mtime != _cache["mtime"]:
                with open(settings.params_path) as fh:
                    _cache["data"] = {**DEFAULTS, **json.load(fh)}
                _cache["mtime"] = mtime
        except FileNotFoundError:
            _cache["data"] = dict(DEFAULTS)
    return _cache["data"]


def param(name: str, default=None):
    return params().get(name, default)
