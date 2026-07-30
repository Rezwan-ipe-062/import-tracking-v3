# Deployment Options Analysis — Import Tracker

> **Icon confirmation**: The old-computer.png image is used consistently across all icon assets — `favicon.ico` (multi-size), `favicon-16x16.png`, `favicon-32x32.png`, `apple-touch-icon.png` (180×180), `icon-192.png`, `icon-512.png`, and `icon-512-maskable.png` (with safe padding). The PWA `manifest.webmanifest` references all three PNG sizes with correct `purpose` values (`any` / `maskable`).

---

## Option 1: Company-Approved Internal Hosting (Preferred)

### Example: Azure App Service (Syngenta corporate Azure tenant)

| Criterion | Detail |
|---|---|
| **Cost** | Covered under Syngenta's enterprise Azure agreement. Free-tier App Service (F1) supports 1 GB RAM, 1 GB storage, 60 minutes/day compute — sufficient for an internal MVP. Paid tier (B1 ~$55/mo) for production. |
| **Access protection** | Azure App Service supports **Azure AD / Entra ID authentication** out of the box (Easy Auth). Country managers and admin users authenticate with their corporate credentials. No shared tokens, no separate login. |
| **Database** | PostgreSQL via Azure Database for PostgreSQL Flexible Server. Free tier (1 vCore, 2 GB RAM, 32 GB storage) available for 12 months. Migration from SQLite (see `RECOMMENDED-HOSTING-ARCHITECTURE.md`). |
| **File/archive storage** | Azure Blob Storage (hot tier) for archived source files and generated workbooks. Hot tier: ~$0.018/GB/month. Cool tier for old archives: ~$0.01/GB/month. |
| **Backups** | Azure Database for PostgreSQL: automated geo-redundant backups with 7–35 day retention. Blob Storage: soft-delete enabled by default. App Service: deployment slots for zero-downtime updates. |
| **Data confidentiality** | All data stays within Syngenta's Azure tenant. No third-party infrastructure. Azure complies with ISO 27001, SOC 2, GDPR. Data residency can be pinned to the company's preferred region (e.g., UK South, West Europe). |
| **Ease of use** | Country manager navigates to a single URL, authenticates with corporate SSO, uploads files, views dashboard. No local installs, no VPN required (if app is published to the internal network or internet with Entra ID auth). |
| **Constraints** | Requires **IT/Cloud team approval** to provision App Service, PostgreSQL, and Blob Storage in the corporate Azure tenant. May take 1–4 weeks depending on internal process. If Syngenta does not use Azure, equivalent options exist on AWS (Elastic Beanstalk + RDS + S3) or GCP (Cloud Run + Cloud SQL + Cloud Storage). |

### Alternative: SharePoint-Integrated Platform

If Syngenta uses SharePoint Online / Power Platform:

- **SharePoint Embedded** can host the archive files (Excel workbooks replaced by a SharePoint document library)
- **Power Apps** could rebuild the dashboard front-end
- **Power Automate** could replace the upload/process trigger
- **SQLite → Dataverse** for the relational data

This is a **re-architecture** not a migration. Only relevant if the team is already a Power Platform shop.

---

## Option 2: Free-Tier External Hosting — Technical Demo Only

### Comparison of external free tiers

| Provider | Free tier | Database | File storage | Sleep/cold start | Private access | Verdict |
|---|---|---|---|---|---|---|
| **PythonAnywhere** | 512 MB RAM, 1 web app, 1 always-on | MySQL (free, 200 MB) | Limited to 512 MB total | No sleep on paid; free tier may idle | Password-protected (shared login, no SSO) | **Demo only** — no private access, no durable file storage |
| **Render** | 512 MB RAM, 750 hours/mo | PostgreSQL free (1 GB, expires after 90 days) | Ephemeral disk (deleted on deploy) | Spins down after 15 mins idle | Can add Basic Auth at proxy layer | **Demo only** — cold starts, ephemeral storage, no data persistence guarantee |
| **Railway** | $5 credit, ~500 hours/mo | PostgreSQL included in credit | Ephemeral | Spins down after inactivity | No private network; URL is public | **Demo only** — public by default, data at risk |
| **Fly.io** | 3 shared VMs, ~256 MB RAM each | PostgreSQL free tier (1 GB) | 3 GB persistent volume | Sleep after inactivity (can be configured) | Public URL by default; WireGuard tunnel available but complex | **Demo only** — requires custom auth layer, not turnkey |
| **Azure App Service (F1 — external)** | 1 GB RAM, 1 GB storage, no custom domain | No free PostgreSQL (requires separate Azure DB) | Ephemeral for app; Blob Storage free (5 GB) | Sleeps after 20 mins | Easy Auth (Azure AD) **if using corporate tenant** — otherwise no auth | **Only viable if linked to corporate Azure tenant**; standalone free tier has no private access |

### Verdict on free external hosting

**None of the standalone external free tiers are suitable for real PO/import data.** They all share fundamental problems:

1. **No private access** — the application URL is public by default. Anyone who discovers the URL can upload files, view dashboard data, and download workbooks.
2. **Ephemeral file storage** — uploaded files and generated workbooks disappear on redeploy or after inactivity sleep cycles.
3. **No data residency control** — you cannot guarantee the data stays in a specific legal jurisdiction.
4. **Company-data policy** — storing Syngenta operational data (PO numbers, supplier names, shipment records) on an external platform likely violates internal data-protection policies.

**If used at all:** populate with synthetic/test data only (no real PO records). Use for a 1–2 hour executive demo, then delete the deployment.

---

## Option 3: Local / Private Deployment on Country Manager's Laptop or Internal PC

| Criterion | Detail |
|---|---|
| **Cost** | Zero — runs on existing hardware. |
| **Access protection** | Accessible only on that machine (`127.0.0.1:5000`). For network access, the country manager can start with `host="0.0.0.0"` and rely on the internal network firewall to restrict access. No authentication needed locally — physical possession of the laptop is the access control. |
| **Database** | SQLite — works as-is. No migration needed. The DB file lives on the local drive. |
| **File/archive storage** | Local folder (`%LOCALAPPDATA%/import_tracker/archive/`). Works as-is. |
| **Backups** | Manual — the country manager or IT copies the SQLite database and archive folder periodically (e.g., weekly to a shared drive or OneDrive). Can be scripted with a simple batch file. |
| **Data confidentiality** | Data stays on the local machine / internal network. No external transmission. Compliant with the most restrictive data policies by default. |
| **Ease of use** | Requires the country manager to: (1) have Python 3.13+ installed, (2) run `pip install -r requirements.txt`, (3) run `python web_app.py` from a terminal. For non-technical users, package as a single-click launcher (see below). |
| **Constraints** | Single-user (unless the country manager opens the network port, which adds risk). Data is lost if the hard drive fails without backups. No concurrent access. Requires IT to approve Python installation on a corporate laptop (many orgs restrict this). |

### Making it practical for a non-technical user

Use **PyInstaller** to package the app into a standalone `import_tracker.exe` that the country manager double-clicks:

```bash
pip install pyinstaller
pyinstaller --onefile --add-data "templates;templates" --add-data "static;static" web_app.py
```

The resulting `.exe` (~30 MB) includes Python and all dependencies. The country manager runs it, a terminal window opens with the URL, and they open that URL in their browser. No Python installation needed.

---

## Recommendation Summary

### ✅ Recommended for internal proof of concept: **Local deployment (Option 3)**

- **Why**: Zero cost, zero external data exposure, zero IT approvals (or minimal — just Python/executable approval). Uses SQLite and local storage as-is. The country manager can start using it immediately.
- **Steps**:
  1. Package with PyInstaller → single `.exe` file
  2. Place on the country manager's laptop with a `run.bat` shortcut
  3. Educate on manual backup: copy `import_tracker.db` and `%LOCALAPPDATA%/import_tracker/archive/` to a shared drive weekly
  4. Access at `http://localhost:5000` — no network exposure
- **Risk**: Single point of failure (hard drive loss). Mitigate with regular backups.

### ✅ Recommended for operational use: **Azure App Service + Azure AD (Option 1)**

- **Why**: Company data stays in company infrastructure. SSO with corporate credentials. Managed database with automated backups. Durable blob storage. Scales to 5–50+ users.
- **Steps requiring IT/cloud team**:
  1. Provision Azure App Service (B1 minimum), Azure Database for PostgreSQL (flexible server), Azure Blob Storage container
  2. Run the PostgreSQL migration (see `RECOMMENDED-HOSTING-ARCHITECTURE.md`)
  3. Enable Easy Auth → Azure AD / Entra ID
  4. Replace `get_connection()` with pg8000/psycopg2 connection pool
  5. Replace `archive_source_files()` with `S3Storage` backend (blob storage)
  6. Set environment variables in App Service Configuration
  7. Configure a custom domain (if needed) and TLS certificate (automatic with App Service)
  8. Test with a small user group, then roll out to country managers

### ❌ Not recommended for production: **Free external hosting**

External free tiers cannot guarantee private access, durable storage, or data residency. They are suitable only for a short technical demo using synthetic/test data, after which the deployment must be torn down.
