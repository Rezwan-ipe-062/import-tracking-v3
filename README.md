# Syngenta Bangladesh Import Tracker

Import tracking and supply-chain visibility tool for Syngenta Bangladesh — merges Open PO, BD Tracker, and Eagle Eye data into a single dashboard with milestone-based risk scoring.

## Architecture

```
User Browser  ──►  Flask App (web_app.py)
                        │
                        ├── pipeline_service.py   (business logic, merge, threshold rules)
                        ├── pipeline_db.py         (SQLite schema, migrations)
                        ├── templates/             (Jinja2 HTML)
                        └── static/                (CSS, PWA assets)
```

Three source Excel files are uploaded via the web UI:

1. **Open PO** — current purchase orders from the ERP
2. **BD Tracker** — Bangladesh logistics status (1M+ rows, 99.9% ghost rows filtered)
3. **Eagle Eye** — international shipment tracking container-level data

The merge engine combines these at the `(PO, AGI, Partial_Shipment_Reference)` grain, producing:

- **Master_Detail** — row-level merged data with risk scores
- **PO_Summary** — one row per purchase order
- **Unmatched_BD / Unmatched_EE** — rows from each source that did not match Open PO
- **Ambiguous_Matches** — POs with multiple conflicting AGI assignments
- **DQ_Exceptions** — data-quality flags
- **Dashboard** — filterable, paginated view with risk cards and search

Threshold profiles (admin-managed) define milestone-based risk rules (Watchlist → Critical → Emergency) per country code.

## Local Setup

### Prerequisites

- Python 3.13+
- Git

### Install

```bash
git clone https://github.com/Rezwan-ipe-062/import-tracking-v3.git
cd import-tracking-v3
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

### Configuration

Copy and edit:

```bash
cp .env.example .env
```

Key variables (see `.env.example` for full list):

| Variable | Purpose | Default |
|---|---|---|
| `FLASK_SECRET_KEY` | Flask session signing | Random dev key |
| `IMPORT_TRACKER_ADMIN_SECRET` | Admin login token | Random UUID (shown at startup) |
| `IMPORT_TRACKER_ARCHIVE` | Archived file storage path | `%LOCALAPPDATA%/import_tracker/archive` |
| `DATABASE_URL` | PostgreSQL URL (prod only) | SQLite (local dev) |

### Run Tests

```bash
cd "Python Codes"
python -m pytest test_phase2.py -v
python -m pytest test_phase3.py -v
python -m pytest test_phase4.py -v
python -m pytest test_phase5.py -v
```

All four test suites should pass (253 assertions total).

### Launch Locally

```bash
cd "Python Codes"
python web_app.py
```

Open http://127.0.0.1:5000 in a browser.

## Deployment Guidance

### MVP Hosting (Simple Demo)

Use a single VM or container with:

- **Gunicorn** (Linux) or **Waitress** (Windows) WSGI server
- **SQLite** (as-is, single-user / low concurrency)
- **Local filesystem** for archives
- **Nginx/Apache** reverse-proxy for TLS termination

### Production Hosting

For multi-user, business-critical deployment:

| Component | Local Dev | Production |
|---|---|---|
| Database | SQLite | PostgreSQL |
| File Archive | Local folder | S3-compatible object storage |
| WSGI Server | Flask dev server | Gunicorn behind Nginx |
| Auth | Admin token | Company SSO / OIDC |
| Session Store | Signed cookies | Redis / DB-backed sessions |

See **recommended-hosting-architecture.md** for the full migration plan from SQLite to PostgreSQL and from local file storage to object storage.

## Version

Current: 1.0.0
