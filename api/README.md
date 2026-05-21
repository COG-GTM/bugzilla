# Bugzilla REST API — FastAPI Port

A modernised Python/FastAPI port of the Bugzilla WebService User API, reading
from the existing Bugzilla MySQL database.

## Quick Start

```bash
cd api/

# Install dependencies
pip install -e ".[dev]"

# Configure database connection (matches docker-compose.yml defaults)
export BZ_DB_HOST=127.0.0.1
export BZ_DB_PORT=3306
export BZ_DB_USER=bugs
export BZ_DB_PASS=bugzilla
export BZ_DB_NAME=bugs

# Run the server
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

## Endpoints (Ticket 1 — Foundation & Auth)

| Method | Path            | Description                         | Perl source                   |
|--------|-----------------|-------------------------------------|-------------------------------|
| GET    | `/login`        | Authenticate, returns `{id, token}` | `User.pm` `login()` L56-73   |
| GET    | `/logout`       | Invalidate session token            | `User.pm` `logout()` L75-78  |
| GET    | `/valid_login`  | Check if token is valid for login   | `User.pm` `valid_login()` L80-89 |

## Error Format

All errors follow the Bugzilla REST API envelope:

```json
{"error": true, "message": "...", "code": 300}
```

Error codes and HTTP status mapping are ported from
`Bugzilla/WebService/Constants.pm`.

## Project Structure

```
api/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── core/
│   │   ├── config.py         # Settings from env vars
│   │   ├── database.py       # SQLAlchemy engine & session
│   │   ├── errors.py         # Error codes & exception handler
│   │   └── field_filter.py   # include_fields / exclude_fields utility
│   ├── deps/
│   │   └── auth.py           # Auth dependencies (login, token validation)
│   ├── models/
│   │   └── user.py           # SQLAlchemy models (profiles, groups, etc.)
│   ├── routers/
│   │   └── user.py           # /login, /logout, /valid_login routes
│   └── schemas/
│       └── user.py           # Pydantic request/response models
├── pyproject.toml
└── README.md
```
