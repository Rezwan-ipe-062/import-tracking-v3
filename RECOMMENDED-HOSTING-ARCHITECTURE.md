# Recommended Hosting Architecture

## Current Design (Local Development)

- **Database**: SQLite (`import_tracker.db`) — file-based, single-writer, no concurrent user support
- **File Archive**: Local folder (`%LOCALAPPDATA%/import_tracker/archive/` or `IMPORT_TRACKER_ARCHIVE`)
- **WSGI**: Flask development server (`app.run(debug=True)`)
- **Auth**: Simple token-based admin login (Flask session cookie)
- **Session**: Client-side signed cookies

## Trade-offs: MVP vs Production

| Aspect | Simple MVP (single VM) | Durable Business Deployment |
|---|---|---|
| **Cost** | ~$10-20/mo (small VPS) | ~$50-100/mo (managed DB + storage) |
| **Setup time** | Hours | Days |
| **Concurrency** | 1-3 simultaneous users | 10-50+ users |
| **Data safety** | Manual backups | Automated backups, replication |
| **File durability** | Single disk | Redundant object storage |
| **Auth** | Admin token | SSO / OIDC integration |

## PostgreSQL Migration Plan

### Step 1: Extract schema to a migration script

The current `init_database()` in `pipeline_db.py` creates all tables programmatically. This must become:

- A standalone SQL migration file (`migrations/001_initial_schema.sql`) for PostgreSQL
- An `init_database()` that runs `CREATE TABLE IF NOT EXISTS` with PG-compatible DDL

### Step 2: DDL changes for PostgreSQL

| SQLite type | PostgreSQL type | Notes |
|---|---|---|
| `INTEGER` | `INTEGER` or `SERIAL` | Auto-increment becomes `SERIAL` |
| `TEXT` | `TEXT` or `VARCHAR(n)` | `TEXT` for most columns (no length limit needed) |
| `REAL` | `DOUBLE PRECISION` or `NUMERIC` | For numeric threshold values |
| `PRAGMA foreign_keys=ON` | `SET session_replication_role` | Enforcement is always ON unless explicitly disabled |
| `FOREIGN KEY ...` (in CREATE) | Same syntax | Mostly compatible |

### Step 3: Replace `get_connection()` with a connection-pool wrapper

```python
# Current (SQLite):
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# Future (PostgreSQL):
import pg8000  # or psycopg2
pool = None

def get_connection():
    global pool
    if pool is None:
        pool = ThreadedConnectionPool(1, 10, DATABASE_URL)
    return pool.getconn()
```

### Step 4: Remove WAL pragma and file-locking logic

- `PRAGMA journal_mode=WAL` is SQLite-specific — remove in PG mode
- `PRAGMA busy_timeout` is SQLite-specific — PG handles concurrent writes natively
- `try/except` around `ALTER TABLE ADD COLUMN` migrations can use PG's `DO $$ ... $$` anonymous blocks instead

### Step 5: Data export

```bash
# Dump SQLite data
sqlite3 import_tracker.db .dump > data_dump.sql

# Transform to PG-compatible SQL (adjust types, remove PRAGMA lines)
# Then load via psql into the PG database
psql $DATABASE_URL -f data_dump_pg.sql
```

### Schema Compatibility Table

| SQLite table | PG table | Migration strategy |
|---|---|---|
| `upload_runs` | `upload_runs` | Direct — use `SERIAL` for implicit PK if needed (TEXT PK works) |
| `source_file_uploads` | `source_file_uploads` | Direct |
| `master_detail_records` | `master_detail_records` | Direct, all TEXT columns |
| `po_summary_records` | `po_summary_records` | Direct |
| `unmatched_bd_records` | `unmatched_bd_records` | Direct |
| `unmatched_ee_records` | `unmatched_ee_records` | Direct |
| `ambiguous_match_records` | `ambiguous_match_records` | Direct |
| `data_quality_exceptions` | `data_quality_exceptions` | Direct |
| `threshold_profiles` | `threshold_profiles` | `SERIAL` primary key instead of `INTEGER` |
| `threshold_profile_rules` | `threshold_profile_rules` | Same FK references |
| `threshold_profile_audit` | `threshold_profile_audit` | Same FK references |
| `threshold_rules` | `threshold_rules` | Legacy — can be omitted |
| `threshold_rule_profiles` | (dropped) | No longer used |
| `threshold_rule_audit_log` | (dropped) | No longer used |

## Object Storage Migration (Local Archive → S3)

### Step 1: Abstract file operations behind a storage service

Create a `storage_service.py` module that provides:

```python
class StorageBackend(ABC):
    @abstractmethod
    def save(self, key: str, file_path: str) -> str: ...
    @abstractmethod
    def get(self, key: str) -> str: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def exists(self, key: str) -> bool: ...

class LocalStorage(StorageBackend):
    """Current file-system implementation."""

class S3Storage(StorageBackend):
    """boto3-backed S3 implementation."""
```

### Step 2: Configure via environment

```python
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
if STORAGE_BACKEND == "s3":
    archive = S3Storage(bucket="import-tracker-archives")
else:
    archive = LocalStorage(base_path=ARCHIVE_BASE)
```

### Step 3: Archive files now use the backend

```python
def archive_source_files(run_id, file_paths):
    for source_type, path in file_paths.items():
        key = f"archives/{run_id}/{source_type}/{os.path.basename(path)}"
        archive.save(key, path)
```

## Production WSGI Configuration

### Linux (recommended for production)

```bash
# Install
pip install gunicorn

# Start (4 workers, behind Nginx reverse-proxy)
gunicorn -w 4 -b 127.0.0.1:8000 "Python Codes.web_app:app"
```

### Windows (for internal team use on Windows Server)

Use **Waitress** instead of Gunicorn (Gunicorn is Unix-only):

```bash
pip install waitress
waitress-serve --port=8000 "Python Codes.web_app:app"
```

Or use IIS with wfastcgi (see `web.config` in repo root).

## Required Production Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `FLASK_SECRET_KEY` | Yes | Session signing (generate with `secrets.token_hex(32)`) |
| `IMPORT_TRACKER_ADMIN_SECRET` | Yes | Admin login token |
| `DATABASE_URL` | For PG | `postgresql://user:pass@host:5432/import_tracker` |
| `STORAGE_BACKEND` | For S3 | `s3` or `local` |
| `STORAGE_BUCKET` | For S3 | S3 bucket name |
| `AWS_ACCESS_KEY_ID` | For S3 | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | For S3 | AWS credentials |
| `AWS_REGION` | For S3 | e.g., `ap-southeast-1` |
| `ALLOWED_HOSTS` | Yes | Comma-separated CORS/CSRF hosts |
| `SESSION_COOKIE_SECURE` | Yes | `1` for HTTPS-only sessions |
| `MAX_CONTENT_LENGTH` | No | Upload size limit (default 100MB) |

## Health Check Endpoint

The application exposes `GET /health` returning:

```json
{
    "status": "ok",
    "version": "1.0.0",
    "database": "connected"
}
```

## Database Initialisation Strategy

### Fresh Deploy

```python
# Called once at app startup — safe to call repeatedly
from pipeline_db import init_database
init_database()
```

### Migrations

SQLite migrations use `ALTER TABLE ADD COLUMN` wrapped in `try/except` (idempotent).

PostgreSQL migrations should use a versioned migration table:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT NOW()
);
```

Each migration file is numbered (`001_initial_schema.sql`, `002_add_threshold_profiles.sql`, etc.) and applied in order by checking `schema_migrations`.
