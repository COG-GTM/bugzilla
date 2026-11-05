# Bugzilla REST API — Python port

FastAPI + SQLAlchemy 2.x service that re-implements Bugzilla's REST WebService module by
module on the **unchanged** MariaDB schema. Perl remains the source of truth; Apache
(`docker/000-default.conf`) proxies only the migrated resources here.

```
docker compose up -d --build          # bugzilla5.web (Perl, :8080) + bugzilla5.api (this, :8000) + MariaDB
make parity TARGET=python             # contract suite straight against :8000
make parity                           # against :8080 (Apache → Python for /rest.cgi/bug*, Perl for the rest)
```

Local development without the container:

```
python -m venv .venv && . .venv/bin/activate && pip install -e python  # or pip install -r pyproject deps
BZ_DB_HOST=<mariadb host> BZ_PARAMS_FILE=/path/to/data/params.json \
  uvicorn app.main:app --app-dir python --reload --port 8000
```

Migrated modules: `app/modules/bug` (Bug), `app/modules/bugzilla` (Bugzilla), `app/modules/user`
(login/logout/valid_login only). Add a module by creating `app/modules/<name>/router.py`; it is
discovered at startup. See `.agents/skills/port-bugzilla-module/SKILL.md` for the full recipe.
