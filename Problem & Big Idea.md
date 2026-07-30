# Import Tracker System — Complete Implementation Specification

## 1. Document Purpose

Build a secure, local-first web application called **Import Tracker System** for import-planning visibility in Syngenta Bangladesh.

This document is the complete functional specification for the first MVP. It is written for an AI development agent or software developer.

The MVP must prove these core capabilities before any workflow automation, email notification, predictive AI, or agentic AI is introduced:

1. Correctly merge Open PO, BD Tracker, and Eagle Eye workbooks.
2. Correctly identify the next required incomplete import milestone.
3. Preserve and display partial-shipment and container-level detail.
4. Apply supplier/plant-specific, milestone-specific risk thresholds.
5. Clearly surface data, matching, configuration, and status exceptions.

---

## 2. Business Context

Syngenta Bangladesh imports crop-protection products in bulk and locally fills and packs them into finished goods. Availability of imported bulk material directly affects factory production scheduling and product availability.

Each purchase order (PO) has an **RDD — Required Date of Delivery**. For this system, RDD means:

> **The date by which imported bulk material must reach the factory.**

To meet RDD, activities such as LC completion, SI sharing, shipment scheduling, shipment departure, bill-of-lading receipt, final-document receipt, port clearance, and transport must be completed in sequence.

For example, even if a container reaches Chittagong port, bulk material may not be released if LC or documents are incomplete. This can disrupt filling and packing, create stock-out risk, and add logistics cost.

The Planning Manager currently reconciles three Excel workbooks manually. This is repetitive, depends on individual knowledge, and does not create a prioritised action view.

---

## 3. MVP Scope and Explicit Exclusions

### 3.1 Included in MVP

- Local upload and processing of three Excel workbooks.
- Excel validation, standardisation, filtering, merging, and export.
- Supplier/plant mapping and centrally stored threshold configurations.
- Milestone-specific risk calculations.
- PO-level and shipment/container-level visibility.
- Dashboard, daily priority list, master-data export, and exception queue.
- Authenticated access with Admin and Standard User roles.

### 3.2 Explicitly excluded from MVP

Do **not** build the following in this phase:

- Automated email, reminder, or escalation workflows.
- Predictive AI, generative AI, or agentic AI recommendations.
- Automatic supplier-country/location inference based only on supplier-name text.
- Manager override or risk-suppression capability.
- Threshold-rule simulation before saving a rule.
- RDD history and RDD-change tracking.
- Quantity, revenue, margin, inventory, or customer-impact priority scoring.

The first MVP is a local-first data-consolidation and rule-based risk-alert application. It does not yet include automated notifications or email escalation. Automation to notify responsible people when a milestone is overdue may be considered only after data structure, ownership mapping, matching logic, and business rules are proven reliable.

---

## 4. Privacy, Data Storage, and Deployment Requirements

### 4.1 Local operational-data processing

The app source code may be hosted publicly, for example on GitHub Pages. However, operational Excel data must be processed entirely on the user’s local device/browser.

Operational data includes, but is not limited to:

- PO numbers
- Material / AGI
- Product names
- Quantities
- RDD
- LC, shipment, ETA, OBL/EBL, and document dates
- Container numbers
- Tracking values
- Source statuses
- Calculated master data and risk outputs

The application must:

- Never upload the Excel files to an application server.
- Never send operational Excel data to Supabase.
- Never save operational data in GitHub, cloud storage, analytics logs, or a database.
- Keep operational data only in browser memory/current session.
- Clear local data when the session ends or the page is reloaded.
- Display this notice clearly:

> **Your files are processed locally and are not uploaded or stored.**

### 4.2 Centrally stored configuration data

The following non-operational configuration data may be stored in Supabase:

- Supplier/Plant ID
- Supplier/Plant Name
- Location/source group
- Assigned threshold profile
- Milestone thresholds
- Action Owner
- Active/pending/inactive status
- Effective date
- Rule audit information

Configuration data is internal and must be protected, even though it does not contain PO-level operational data.

---

## 5. Authentication and Roles

The app may be reachable through a shared link, but users must log in before access.

Use Supabase Auth or an equivalent secure authentication mechanism. Do **not** embed or use a shared hard-coded password in frontend source code.

### 5.1 Roles

| Role | Permissions |
|---|---|
| **Country Manager / Admin** | Upload/process local files; view/download outputs; create/edit supplier mappings, threshold profiles, milestone rules, Action Owners, and day-calculation setting; review pending configurations; review change history. |
| **Standard User** | Upload/process local files; view/download outputs; cannot create or change centrally stored configuration. |

### 5.2 Security requirements

- Require authenticated access for Supabase configuration tables.
- Use Supabase Row-Level Security (RLS), or equivalent, to protect data.
- Allow only Admin users to write/update rule configuration.
- Retain audit information: updated by, updated at, and changed values where feasible.
- Do not expose unrestricted configuration write access from the browser.

---

## 6. Source Workbooks and Required Columns

The user uploads these three workbooks:

1. **Open PO**
2. **BD Tracker** — data is in the `Tracker File` sheet
3. **Eagle Eye**

The upload screen must validate expected files, sheets, and required columns before processing.

### 6.1 Open PO workbook

Open PO is the **base population** for the Import Master Data File.

| Source column | Output/master field | Required transformation or business rule |
|---|---|---|
| `Material` | Material / AGI | This is called AGI in other sheets. Retain as material identifier and reporting field. |
| `Short text` | Product Name | Product name/description. |
| `Purchasing Document` | Raw PO Number and Standardised PO Number | Primary PO number. Exclude records with a Purchasing Document beginning with `62`; these are interplant movements within Bangladesh and outside import-tracker scope. |
| `Supplier/Supplying Plant` | Raw Supplier/Plant; Supplier/Plant ID; Supplier/Plant Name | Split into leading ID and remaining name. Use ID as stable configuration key. |
| `Still to be delivered (qty)` | Open Quantity | Quantity remaining to be delivered. |
| `Order unit` | Unit of Measure | Unit for the open quantity. |

### 6.2 BD Tracker workbook — `Tracker File` sheet

BD Tracker provides import-process status and milestone dates. It may enrich only POs that remain in the filtered Open PO base population.

| Source column | Output/master field | Required transformation or business rule |
|---|---|---|
| `Overall status` | Overall Import Status | Retain and validate against the standard status list in Section 8. |
| `PO` | Raw BD Tracker PO; Standardised PO Number; Partial Shipment Reference | Use for matching. Remove partial-shipment suffix from matching key, but preserve it as detail. Example: `6590028256 - 2` becomes standard PO `6590028256` and partial-shipment reference `2`. |
| `LC Date` | LC Date | Blank/null normally means LC is not yet complete. |
| `SI Shared Date` | SI Shared Date | Blank/null normally means SI is not yet shared. |
| `RDD` | RDD | Required date by which bulk material must reach factory. Main risk-calculation date. |
| `ETD` | BD Tracker ETD | Shipment departure milestone. |
| `ETA` | BD Tracker ETA | Retain separately from Eagle Eye ETA. |
| `OBL/EBL rcvd date` | OBL/EBL Received Date | Blank/null normally means OBL/EBL not yet received. |
| `Final docs rcvd date` | Final Documents Received Date | Blank/null normally means final documents not yet received. |

### 6.3 Eagle Eye workbook

Eagle Eye provides shipment and container tracking visibility. It may enrich only POs that remain in the filtered Open PO base population.

| Source column | Output/master field | Required transformation or business rule |
|---|---|---|
| `DDPO Number` | Raw Eagle Eye DDPO; Standardised PO Number | Remove one leading `F` or `G` prefix before matching. Example: `F6590028423` and `G6590028423` both become `6590028423`. |
| `Container no.` | Container Number | Preserve for container-level detail. |
| `Tracking` | Tracking Information | Preserve shipment tracking reference/detail. |
| `Status` | Eagle Eye Status | Preserve current shipment/tracking status. |
| `ETA` | Eagle Eye ETA | Retain separately from BD Tracker ETA. |

---

## 7. Data Preparation, Filtering, and Lookup Logic

### 7.1 Mandatory master-data lookup flow

The merge direction is mandatory and must not be reversed:

1. Read Open PO `Purchasing Document`.
2. Filter out all records whose Purchasing Document begins with `62`.
3. The remaining Open PO POs become the approved **base lookup population**.
4. Standardise those Open PO PO numbers.
5. Standardise BD Tracker `PO` values by removing partial-shipment suffixes such as ` - 2` for matching.
6. Standardise Eagle Eye `DDPO Number` values by removing a leading `F` or `G` prefix for matching.
7. Look up only the filtered Open PO PO numbers in BD Tracker and Eagle Eye.
8. Append matching BD Tracker and Eagle Eye information to the corresponding Open PO records.
9. Do **not** create new master-data rows from PO records that exist only in BD Tracker or Eagle Eye.

Example:

```text
Open PO Purchasing Document values:
6290063584
6590026079
6590027103

After filtering POs beginning with 62:
6590026079
6590027103

Only these two POs are looked up in cleaned BD Tracker and Eagle Eye data.
```

### 7.2 Standardisation controls

Before matching:

- Convert PO identifiers to text.
- Trim leading/trailing spaces.
- Normalise repeated internal spaces where required.
- Preserve raw source values in detail/output fields for audit.
- Convert date fields to valid standard date values.
- Flag invalid or unreadable dates rather than silently converting them incorrectly.

### 7.3 Matching and duplicates

- Match primarily on Standardised PO Number.
- Retain Material/AGI as a validation and reporting field.
- One PO may contain multiple materials, partial shipments, tracker records, or containers.
- The system must never silently select one matching record and discard the rest.
- Preserve detailed shipment/container-level records and calculate an aggregated PO-level summary.

### 7.4 Unmatched records

Show these as exceptions, not silently excluded:

- `Open PO with no BD Tracker match`
- `Open PO with no Eagle Eye match`
- Multiple BD Tracker records for one PO
- Multiple Eagle Eye/container records for one PO

---

## 8. BD Tracker Overall Status Values

The `Overall status` column in BD Tracker must retain and validate these approved standard values:

1. `Completed`
2. `SI shared - Waiting for schedule & draft`
3. `Schedule received - Waiting for Draft`
4. `Draft received - waiting for OBL`
5. `OBL Received - waiting for other docs`
6. `Hard copy pending`
7. `Full set received`
8. `Docs created, under Prasanna validation`
9. `HSBC Discrepancy / Approval pending`
10. `LC yet to receive`

If status is blank, unexpected, or spelling-variant, create this exception:

> `Non-standard Overall Status value — Manual review required.`

Do not silently map non-standard text to an approved status.

---

## 9. Supplier/Plant Parsing and Configuration

The Open PO `Supplier/Supplying Plant` field contains a combined ID and name, for example:

| Raw value | Supplier/Plant ID | Supplier/Plant Name |
|---|---|---|
| `70000559 Syngenta Production France SAS` | `70000559` | Syngenta Production France SAS |
| `70002218 Syngenta Korea Limited - 4408` | `70002218` | Syngenta Korea Limited - 4408 |
| `70000367 Syngenta Nantong Crop Protection Co` | `70000367` | Syngenta Nantong Crop Protection Co |

Rules:

- Parse the leading identifier as `Supplier/Plant ID`.
- Store the remainder as `Supplier/Plant Name`.
- Use Supplier/Plant ID—not supplier name—as the stable configuration key.
- Do not infer country/location/threshold rules solely from supplier-name text.

Country/location is useful, but thresholds may also depend on route, shipment mode, supplier reliability, LC process, banking process, and documentation process. Therefore, rule assignment is explicit and controlled by the Country Manager.

---

## 10. Persistent Rule Configuration in Supabase

Store only configuration data in Supabase. Retrieve it when the user processes local Excel workbooks.

Use two configuration layers:

1. **Supplier/Plant Mapping** — maps each Supplier/Plant ID to a location/source group and reusable threshold profile.
2. **Threshold Profile Rules** — contains milestone-specific thresholds and Action Owners.

### 10.1 Suggested table: `supplier_plant_mappings`

| Field | Requirement |
|---|---|
| `id` | Primary key. |
| `supplier_plant_id` | Unique stable supplier/plant key from Open PO. |
| `supplier_plant_name` | Display name. |
| `location_source_group` | Admin-assigned group such as China, France, Korea, India, or a route-specific group. |
| `threshold_profile_id` | Assigned reusable profile. |
| `status` | `active`, `inactive`, or `pending_configuration`. |
| `effective_from` | Date mapping becomes valid. |
| `notes` | Optional explanation, route logic, or exception context. |
| `created_at`, `created_by` | Audit fields. |
| `updated_at`, `updated_by` | Audit fields. |

### 10.2 Suggested table: `threshold_profiles`

| Field | Requirement |
|---|---|
| `id` | Primary key. |
| `profile_name` | Readable name, e.g. `China Standard Import`, `Europe Standard Import`, or `Supplier-Specific Exception`. |
| `description` | Optional profile rationale. |
| `active` | Profile usability. |
| `effective_from` | Date profile becomes valid. |
| `created_at`, `created_by`, `updated_at`, `updated_by` | Audit fields. |

### 10.3 Suggested table: `threshold_profile_milestones`

| Field | Requirement |
|---|---|
| `id` | Primary key. |
| `threshold_profile_id` | Parent threshold profile. |
| `milestone_code` | One of `LC_DATE`, `SI_SHARED_DATE`, `ETD`, `OBL_EBL_RECEIVED`, `FINAL_DOCS_RECEIVED`. |
| `watchlist_days` | Remaining days at/below which monitoring starts if milestone is incomplete. |
| `critical_days` | Remaining days at/below which urgent follow-up is required if milestone is incomplete. |
| `emergency_days` | Remaining days at/below which immediate intervention is required if milestone is incomplete. |
| `action_owner` | Role/team/person responsible for next action. |
| `active` | Rule usability. |
| `created_at`, `created_by`, `updated_at`, `updated_by` | Audit fields. |

A profile may be reused by multiple Supplier/Plant IDs. A supplier with a unique process may be assigned an exception profile.

### 10.4 New supplier/plant detection

After Open PO upload:

1. Extract unique Supplier/Plant IDs and names.
2. Compare IDs against `supplier_plant_mappings`.
3. For known active IDs with an assigned active profile, apply rules automatically.
4. For an unknown ID:
   - create a pending mapping row in Supabase, pre-filled with ID and name;
   - set status to `pending_configuration`;
   - show it in an Admin configuration queue;
   - show this message in user output:

> `Threshold Profile Not Configured: New Supplier/Supplying Plant detected — [ID] [Name].`

5. Until an Admin assigns an active profile, do not classify affected POs with a guessed/default threshold.

---

## 11. Risk Calculation

### 11.1 RDD and remaining days

Calculate:

```text
Remaining Days = RDD − Today’s Date
```

RDD is the date material must arrive at the factory.

### 11.2 Day calculation setting

- Default: **Calendar Days**.
- Admin setting: Country Manager can select either `Calendar Days` or `Business Days`.
- Display the active setting on dashboard and exports, e.g.:

> `Risk calculation method: Calendar Days`

Business Days requires an agreed Bangladesh working-day/public-holiday calendar. Until that is configured and maintained, Calendar Days remains the recommended default.

### 11.3 Milestone-specific thresholds

Thresholds must vary by:

- Supplier/Plant ID via assigned threshold profile
- Milestone

A single universal threshold is invalid because LC, SI, shipment departure, OBL/EBL, and final documents have different timing requirements.

Initial milestones:

1. LC Date
2. SI Shared Date
3. ETD
4. OBL/EBL Received Date
5. Final Documents Received Date

For every incomplete required milestone, compare Remaining Days to the threshold values in the assigned profile.

Risk precedence:

```text
Emergency > Critical > Watchlist > Normal
```

The PO-level risk is the highest active risk across related shipment/container details.

### 11.4 Next-required-milestone logic

Do **not** flag all blank future milestones at the same time.

The system must assess the workflow sequence and flag the next required incomplete milestone. Illustrative sequence:

1. LC Date
2. SI Shared Date
3. Schedule / Draft process (using Overall Status where relevant)
4. ETD
5. OBL/EBL Received Date
6. Final Documents Received Date

Example: if LC Date is blank, assess LC risk only. Do not also flag missing OBL/EBL and final documents, because those may not yet be due.

Exact milestone sequence and applicability conditions must be designed so they can be adjusted later after business validation. Use `Overall status` and available dates to determine workflow state. Do not assume a blank field is always overdue; it can be pending or not yet applicable.

### 11.5 Risk reason

Each risk row must explain why it is flagged, including milestone, Remaining Days, triggered rule, and Action Owner.

Examples:

- `Critical: LC Date is blank; 50 calendar days remaining to RDD; China Standard Import LC Critical threshold is 60 days; Action Owner: Finance / Treasury.`
- `Emergency: Final documents not received; 12 calendar days remaining to RDD; assigned profile threshold reached; Action Owner: Documentation Coordination.`
- `Threshold Profile Not Configured: Supplier/Plant ID 7000XXXX requires configuration.`

### 11.6 Action Owner

Each milestone rule includes `action_owner`, configured in Supabase by an Admin.

Illustrative examples only:

| Milestone | Illustrative Action Owner |
|---|---|
| LC Date | Finance / Treasury |
| SI Shared Date | Procurement / Supplier Coordination |
| ETD | Supplier Coordination / Logistics |
| OBL/EBL Received Date | Procurement / Documentation Coordination |
| Final Documents Received Date | Finance, Documentation Coordination, or C&F depending on agreed process |

These are configurable values, not hard-coded requirements.

---

## 12. Partial Shipment and Container-Level Detail

A PO may have multiple partial shipments and containers. Therefore, a PO may be partly safe and partly at risk.

The system must provide two linked levels:

### 12.1 PO-level summary

One summary line per PO containing at minimum:

- PO Number
- Material / AGI
- Product Name
- Supplier/Plant ID and Name
- Open Quantity and Unit
- RDD
- Remaining Days
- Overall highest risk
- Primary risk reason
- Number of linked partial shipments/containers
- Action Owner

### 12.2 Shipment/container detail

Provide an expandable table/view and detailed export with:

- Standardised PO Number
- Raw BD Tracker PO
- Partial Shipment Reference
- Container Number
- Tracking Information
- Eagle Eye Status
- BD Tracker ETA
- Eagle Eye ETA
- milestone dates
- line-level risk and reason

For `6590028256 - 2`, use `6590028256` to match Open PO but preserve `2` as a partial shipment reference. Do not discard it.

If related shipment/container lines have different risks, PO-level risk equals the highest active risk among them.

---

## 13. Required Exception Handling

Use grey for exceptions and configuration/data issues. Grey is not a low-priority status; it means manual review is required before relying on the calculated status.

### 13.1 ETA conflict

Retain both `BD Tracker ETA` and `Eagle Eye ETA`.

If both exist and differ after date normalisation, create:

> `ETA Conflict: BD Tracker ETA and Eagle Eye ETA differ. Manual review required.`

Do not silently choose an authoritative source. If downstream logic requires an ETA, clearly display which one was used. A preferred operational ETA rule may be agreed later by Planning and Logistics.

### 13.2 Missing RDD

If RDD is blank, invalid, or unreadable:

> `RDD Missing — Risk Cannot Be Calculated`

Rules:

- Add to Exception Queue.
- Do not assign Normal, Watchlist, Critical, or Emergency using a guessed RDD.
- Include in full master-data export and Daily Action List.

### 13.3 Completed status versus open quantity mismatch

When:

```text
Overall Status = Completed
AND
Still to be delivered (qty) > 0
```

create:

> `Status Mismatch: BD Tracker is Completed, but Open PO shows quantity still open. Manual review required.`

Rules:

- Treat as data-consistency exception, not automatic completion.
- Do not close, remove, downgrade, or hide the PO.
- Initial review owner: `Planning`.
- Preserve partial-shipment detail because one shipment may be completed while other quantity remains open.

### 13.4 Other required exceptions

- `Open PO with no BD Tracker match`
- `Open PO with no Eagle Eye match`
- `Threshold Profile Not Configured`
- `Invalid or unreadable date`
- `Non-standard Overall Status value — Manual review required.`
- `Multiple BD Tracker records matched to one PO`
- `Multiple Eagle Eye/container records matched to one PO`

---

## 14. User Interface Requirements

### 14.1 Upload page

User flow:

1. Login.
2. See local-processing privacy notice.
3. Upload Open PO, BD Tracker, and Eagle Eye workbooks.
4. See pre-processing validation summary.
5. Process files locally.
6. Review dashboard, master data, exceptions, and detailed shipments.
7. Download outputs.

Before processing, show for each workbook:

- File name
- Required sheet detected (especially `Tracker File` for BD Tracker)
- Rows detected
- Required columns found/missing
- Any validation errors
- Number of valid import POs after excluding Open PO POs beginning with `62`

### 14.2 Dashboard

Show at minimum:

- Total open import POs
- Emergency POs
- Critical POs
- Watchlist POs
- Normal POs
- Threshold Profile Not Configured count
- LC pending count
- Document pending count
- Missing RDD count
- ETA Conflict count
- Completed Status vs Open Quantity Mismatch count
- Analysis timestamp (Nice to Have)
- Active day-calculation method

Use visual categories:

| Colour | Meaning |
|---|---|
| Red | Emergency |
| Amber | Critical |
| Yellow | Watchlist |
| Green | Normal |
| Grey | Exception, unmatched record, invalid data, or configuration missing |

### 14.3 Today’s Priority List

Provide a one-click filter/list containing only:

- Emergency POs
- Critical POs
- Watchlist POs
- POs with Threshold Profile Not Configured
- Open PO records with no BD Tracker match
- Open PO records with no Eagle Eye match
- Invalid/unreadable dates
- Missing RDD
- ETA conflicts
- Completed status vs open quantity mismatch

Each row should be understandable without opening details. Example:

> `PO 6590026079 | China Standard Import | 48 days to factory RDD | LC Date missing | Critical | Action Owner: Finance / Treasury`

### 14.4 Exception Queue

Include separate filters/tabs for:

- No BD Tracker match
- No Eagle Eye match
- New Supplier/Plant IDs requiring configuration
- Invalid/missing/unreadable dates
- Non-standard Overall Status values
- Multiple matches per PO
- Partial shipment/container review
- ETA conflict
- Missing RDD
- Completed status versus open quantity mismatch

### 14.5 Admin panel

Authenticated Admin users must be able to:

- View/add/edit/activate/deactivate Supplier/Plant mappings.
- Assign location/source group and threshold profile to each Supplier/Plant ID.
- View a queue of newly detected Supplier/Plant IDs.
- Create/edit/activate/deactivate threshold profiles.
- Configure Watchlist, Critical, and Emergency thresholds for each milestone.
- Set/update Action Owner for each milestone rule.
- Select Calendar Days or Business Days.
- View basic configuration audit history.

Standard Users must not be able to modify these central configurations.

---

## 15. Exports

Support two Excel downloads generated locally:

### 15.1 Full Import Master Data File

Include all consolidated PO and detailed shipment/container fields, raw/standardised matching values where practical, milestone dates, risk category, risk reason, Action Owner, and exception flags.

### 15.2 Daily Action List

Include only records requiring monitoring/review:

- Emergency
- Critical
- Watchlist
- Unconfigured threshold profile
- Unmatched source records
- Invalid/missing data exceptions
- ETA conflicts
- Completed/open quantity mismatch

Include PO, product, supplier/plant, RDD, Remaining Days, next required milestone, risk/exception reason, and Action Owner.

---

## 16. Acceptance Criteria

The MVP is acceptable only when all of the following are true:

### Data privacy and security

- Excel operational data is processed locally in the browser.
- No uploaded Excel data or generated PO-level results are sent to Supabase.
- Supabase contains configuration data only.
- Users authenticate before accessing the application.
- Only Admin users can update configuration data.

### Data processing

- Open PO is the base population.
- Open PO POs beginning with `62` are excluded before lookup.
- BD Tracker partial-shipment suffixes are cleaned for matching but retained for detail.
- Eagle Eye leading `F`/`G` prefixes are cleaned for matching.
- Only filtered Open PO POs are enriched with BD Tracker and Eagle Eye data.
- Source-only records do not create new master rows.
- Multiple matches and unmatched records are visible as exceptions.

### Configuration and rules

- Supplier/Plant ID is used as the stable configuration key.
- Existing mappings and profiles are automatically applied on future uploads.
- New supplier/plant IDs are detected and made pending for Admin configuration.
- Unconfigured suppliers receive no guessed/default risk classification.
- LC, SI, ETD, OBL/EBL, and final-document thresholds are separately configurable by threshold profile.
- Each milestone rule has a configurable Action Owner.
- Calendar Days is the default, with Admin option to select Business Days.

### Risk and workflow

- RDD is interpreted as date bulk material must reach factory.
- System identifies the next required incomplete milestone rather than flagging all later blank fields.
- PO-level risk is the highest risk among related shipment/container details.
- Every risk state has a plain-language reason and Action Owner.
- Partial shipments and containers are visible in detail.

### Exceptions and outputs

- ETA differences between BD Tracker and Eagle Eye are preserved and flagged.
- Missing/invalid RDD prevents date-based risk calculation and is flagged.
- `Completed` in BD Tracker with positive Open PO quantity is flagged for review and not auto-closed.
- Dashboard, Today’s Priority List, Exception Queue, full master-data export, and Daily Action List are available.

---

## 17. Development Priorities

Implement in this order:

1. Authentication and role separation.
2. Local Excel upload, validation, extraction, standardisation, and Open PO base filtering.
3. Correct matching and detail-preserving merge logic.
4. Supabase configuration schema, Admin panel, supplier detection, and profile assignment.
5. Next-required-milestone calculation and supplier/plant-specific threshold rules.
6. Exception logic, PO-level rollup, container/partial-shipment detail.
7. Dashboard, priority list, filters, and local Excel exports.
8. UI polish and Nice-to-Have analysis timestamp.

Do not add automated email, workflow automation, AI, or agentic functionality until the above processing and decision rules are validated with real business files and the Country Planning Manager.
