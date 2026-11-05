"""Tables of the unchanged Bugzilla schema (see Bugzilla/DB/Schema.pm).

They are *reflected* from the live database at startup rather than redeclared: the
schema is owned by checksetup.pl, so reflection cannot drift from it. Access them as
``T.bugs``, ``T.longdescs`` ... (SQLAlchemy Core ``Table`` objects).
"""

from __future__ import annotations

from sqlalchemy import MetaData, Table

from app.core.db import engine

BUG_SLICE_TABLES = [
    "bugs", "bugs_aliases", "bugs_activity", "bugs_fulltext", "longdescs", "longdescs_tags",
    "attachments", "attach_data", "cc", "dependencies", "duplicates", "keywords", "keyworddefs",
    "bug_see_also", "bug_group_map", "group_control_map", "groups", "user_group_map",
    "fielddefs", "profiles", "products", "components", "versions", "milestones", "classifications",
    "bug_status", "status_workflow", "priority", "bug_severity", "resolution", "op_sys", "rep_platform",
    "logincookies", "user_api_keys", "flags", "flagtypes", "audit_log", "setting", "profile_setting",
    "component_cc", "bug_user_last_visit",
]

metadata = MetaData()


class _Tables:
    def __getattr__(self, name: str) -> Table:
        try:
            return metadata.tables[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


T = _Tables()


def reflect(names: list[str] | None = None) -> None:
    """Load table definitions. Modules may call this with their own table list at import time."""
    wanted = [n for n in (names or BUG_SLICE_TABLES) if n not in metadata.tables]
    if wanted:
        metadata.reflect(bind=engine(), only=wanted)
