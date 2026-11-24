"""One sub-package per Bugzilla WebService module (bug, user, product, ...).

Each sub-package exposes ``router`` (a fastapi.APIRouter) in ``router.py`` and
is discovered automatically by ``app.main`` -- there is no central list to edit.
"""
