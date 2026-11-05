"""Runtime configuration: environment (same names as docker-compose.yml) plus Bugzilla's params.json."""

from __future__ import annotations

import json
import os
import pathlib
from functools import lru_cache
from typing import Any

PARAM_DEFAULTS: dict[str, Any] = {
    "maintainer": "",
    "requirelogin": "0",
    "defaultpriority": "--",
    "defaultseverity": "normal",
    "defaultopsys": "",
    "defaultplatform": "",
    "useclassification": "1",
    "usetargetmilestone": "1",
    "useqacontact": "0",
    "usestatuswhiteboard": "1",
    "insidergroup": "",
    "timetrackinggroup": "",
    "comment_taggers_group": "",
    "urlbase": "",
    "maxattachmentsize": "1000",
    "maxlocalattachment": "0",
    "duplicate_or_move_bug_status": "RESOLVED",
}


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.db_host = env.get("BZ_DB_HOST", "127.0.0.1")
        self.db_port = int(env.get("BZ_DB_PORT", "3306"))
        self.db_user = env.get("BZ_DB_USER", "bugs")
        self.db_pass = env.get("BZ_DB_PASS", "bugzilla")
        self.db_name = env.get("BZ_DB_NAME", "bugs")
        self.params_file = pathlib.Path(env.get("BZ_PARAMS_FILE", "/var/www/html/data/params.json"))
        self.urlbase = env.get("BZ_URLBASE", "")
        # Perl answers HTTP 200 for internal errors (code 100500); keep that while proving parity.
        self.compat_die_200 = env.get("BZ_COMPAT_DIE_200", "1") == "1"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"
            "?charset=utf8mb4"
        )

    def params(self) -> dict[str, Any]:
        """Bugzilla parameters, read from the shared data/params.json when available."""
        values = dict(PARAM_DEFAULTS)
        try:
            values.update(json.loads(self.params_file.read_text()))
        except (OSError, ValueError):
            pass
        if self.urlbase:
            values["urlbase"] = self.urlbase
        return values


@lru_cache
def settings() -> Settings:
    return Settings()
