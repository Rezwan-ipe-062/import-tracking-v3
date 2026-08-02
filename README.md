# Anchor

A small local dashboard for the Country Planning Team import-visibility pipeline.
It presents the open-import-PO view with urgency classification, risk and
data-quality views, and wraps the existing, **unchanged** Phase-3 scripts
(`scripts/`). No data ever leaves the laptop.

---

## What it does

Anchor accepts the data pipeline in two upload modes and renders **six pages**.

### Nav (six pages)

- **Action Centre** — open POs prioritised by severity (`Critical → Urgent →
  Data Review → Monitor`, then RDD ascending). Seven KPI cards (Active Open POs,
  Critical, Urgent, Data Review, Monitor, No BD record, No Eagle Eye evidence).
  Open requirement is shown **separately by unit (KG and L)** — the two are never
  combined. Follow-up and owner are derived from Primary Reason and are always
  marked *suggested*.
- **PO Journey** — one PO at a time: the milestone trail (LC → SI → ETD → ETA →
  OBL → Final docs), partial shipments, container linkage (with a caveat if the
  container↔PO link is not confirmed), follow-up, and a device-local note.
- **Shipment Visibility** — one row per container / evidence record. Open
  quantity is never summed here (PO level only).
- **Risk & Exposure** — distinct-PO counts by Import country and supplier, a
  risk×country matrix, product exposure, and an RDD exposure horizon. Quantity is
  never cross-unit combined.
- **Data Quality** — reconciliation & quality KPI cards plus the cleaner's
  control sheets (Exceptions, Unmatched BD, Unmatched EE, Cleaning Log). Data-gap
  rows use the slate *Data Review* label, never red.
- **Thresholds & Refresh** — per-route urgency windows (read-only), source-file
  freshness, upload, export and clear controls.

### Upload modes

- **Mode A — full source refresh.** Open PO, BD Tracker, Eagle Eye and (optional)
  Country Thresholds from the same refresh cycle. Runs the cleaner.
- **Mode B — pre-generated Master workbook.** A pre-built Import Visibility
  Master workbook is read as-is (no cleaning) and validated on generate.

### Controlled exports

Four controlled exports (action list, Priority journey detail, Quality /
Reconciliation, and shipment) — every export carries an Anchor metadata block
(version, timestamp, refresh, source-freshness, filter description). There is
never a silent "export everything".

### Freshness

If the saved view is older than **3 days** Anchor warns you so you never act on
stale facts. The exact window is shown on the Thresholds & Refresh page.

## Running it

### A. Local (nothing shared)

```bash
pip install -r requirements.txt
python -m streamlit run app.py
```

Runs entirely on your machine. `.anchor/` persists between refreshes on that
machine and a hard **Clear Local Data &amp; Start Fresh** button wipes it when you
next want to upload a fresh set.

### B. Hosted on Streamlit Community Cloud (open a URL, no install)

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with the
   GitHub account that owns (or can connect to) the repo.
3. **Create app** → pick the repo → set **Main file** to `app.py`.
4. **Deploy.** You get a public URL (`something.streamlit.app`) — anyone with the
   link opens the dashboard in a browser and uploads their Excels with **no local
   install**.

> **Persistence caveat on the free hosted tier:** the app's working directory is
> on the Cloud side, so `.anchor/` persists *within an active session* (refreshes
> survive) but **not across idle/sleep gaps** — cold restarts can reset it. For
> multi-day/month rollout you'd pin it on a persistent host. The hard **Clear**
> button works the same either way.

### First run

- No local view yet → a **Welcome** screen invites you to **Upload Latest Files**.
- A local view already exists → **Restore or Refresh**: restore the last
  dashboard, upload a new full set, or clear local data.

## How it is wired

```
anchor/
  app.py            # Streamlit entry point (six pages, two upload modes, exports)
  pipeline.py       # bridge -> clean_open_po / clean_bd_tracker /
                    #            clean_eagle_eye / clean_merge / rule_engine
  store.py          # local persistence under .anchor/ (gitignored)
  logic.py          # data-confidence, suggested follow-up/owner, risk buckets
  theme.py          # brand + severity tokens and injected CSS
  ui/components.py  # pills, KPI cards, global search, export-metadata helper
  .streamlit/config.toml  # branded theme + upload cap (used by the host)
  tests/acceptance.py     # run: python -m tests.acceptance
requirements.txt
```

### Source files

| File                | Required | Role |
|---------------------|----------|------|
| Open PO             | yes      | Defines the active population of POs |
| BD Tracker          | yes      | LC, SI, RDD, ETA/ETD, OBL, final docs |
| Eagle Eye           | yes      | Container & shipment visibility |
| Country Thresholds  | no       | Per-route timing rules (built-in defaults if absent) |

### Persistence

Processed outputs, control sheets, manager notes and the last page are kept
under `.anchor/` (gitignored). Raw uploaded Excel bytes are never stored — they
are staged in a transient temp dir and cleared after every run.

### Secrets / data

**No Excel or PO data, and no credentials, may be committed to source control.**
`.gitignore` is aggressive about `.xlsx/.xlsm/.xlsb/.xls` and generated masters.

## Guardrails

- `scripts/` is **untouched** — the pipeline never re-implements cleaning.
- Severity counts are only over **Active** population-status rows; local tolling
  POs are dropped at source and are out of scope.
- KG and L open quantities are shown separately and **never combined**.
- Suggested follow-up and owner are recommendations, never assignments.