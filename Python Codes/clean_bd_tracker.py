"""
clean_bd_tracker.py — BD Tracker cleaning module (Import Tracker pipeline Step 2)

Reproduces the approved Power Query M-code cleaning logic faithfully.
Output: single-sheet workbook with the clean BD Tracker shipment-detail layer.

Usage:
    python clean_bd_tracker.py --input "BD TRACKER - 2026 v1.xlsx" --output "q_BD_Tracker_Clean.xlsx"

Dependencies: Python 3, pandas, openpyxl
"""

import argparse
import os
import re
import sys
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REQUIRED_SOURCE_COLUMNS = [
    "Overall Status",
    " PO",
    "AGI",
    "LC  Date",
    "SI shared date",
    "RDD",
    "ETD",
    "ETA",
    "OBL/EBL rcvd Date",
    "Final docs rcvd Date",
]

COLUMN_RENAME_MAP = {
    "Overall Status": "Overall_Import_Status",
    " PO": "Raw_BD_Tracker_PO",
    "AGI": "Raw_BD_Tracker_AGI",
    "LC  Date": "Raw_LC_Date",
    "SI shared date": "Raw_SI_Shared_Date",
    "RDD": "Raw_RDD",
    "ETD": "Raw_BD_Tracker_ETD",
    "ETA": "Raw_BD_Tracker_ETA",
    "OBL/EBL rcvd Date": "Raw_OBL_EBL_Received_Date",
    "Final docs rcvd Date": "Raw_Final_Documents_Received_Date",
}

DATE_FIELD_PAIRS = [
    ("Raw_LC_Date", "LC_Date"),
    ("Raw_SI_Shared_Date", "SI_Shared_Date"),
    ("Raw_RDD", "RDD"),
    ("Raw_BD_Tracker_ETD", "BD_Tracker_ETD"),
    ("Raw_BD_Tracker_ETA", "BD_Tracker_ETA"),
    ("Raw_OBL_EBL_Received_Date", "OBL_EBL_Received_Date"),
    ("Raw_Final_Documents_Received_Date", "Final_Documents_Received_Date"),
]

APPROVED_STATUSES = [
    "Completed",
    "SI shared - Waiting for schedule & draft",
    "Schedule received - Waiting for Draft",
    "Draft received - waiting for OBL",
    "OBL Received - waiting for other docs",
    "Hard copy pending",
    "Full set received",
    "Docs created, under Prasanna validation",
    "HSBC Discrepancy / Approval pending",
    "LC yet to receive",
]

# Order for the clean output (matching M-code final output)
CLEAN_OUTPUT_COLUMNS = [
    "Source_Row_ID",
    "Standardised_PO_Number",
    "Standardised_Material_AGI_Stripped",
    "Partial_Shipment_Reference",
    "Overall_Import_Status",
    "Overall_Status_Quality_Flag",
    "LC_Date",
    "SI_Shared_Date",
    "RDD",
    "BD_Tracker_ETD",
    "BD_Tracker_ETA",
    "OBL_EBL_Received_Date",
    "Final_Documents_Received_Date",
    "Date_Quality_Flag",
    "Data_Quality_Flag",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_blank(val):
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


def clean_text(val):
    if _is_blank(val):
        return None
    s = str(val).strip()
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = s.strip()
    return s if s else None


# ---------------------------------------------------------------------------
# Step 1: Read source data
# ---------------------------------------------------------------------------

def read_bd_tracker(filepath):
    """
    Read the BD Tracker workbook — Tracker File sheet only.
    Only loads the 9 required columns to avoid loading 1M ghost rows into memory
    unnecessarily.
    """
    try:
        xls = pd.ExcelFile(filepath)
        if "Tracker File" not in xls.sheet_names:
            print(
                "ERROR: Required sheet 'Tracker File' not found in workbook.",
                file=sys.stderr,
            )
            print(f"Available sheets: {xls.sheet_names}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not open workbook: {e}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(
        filepath,
        sheet_name="Tracker File",
        usecols=REQUIRED_SOURCE_COLUMNS,
        dtype=str,
        keep_default_na=False,
    )
    print(f"Read {len(df)} total rows from 'Tracker File'")
    return df


# ---------------------------------------------------------------------------
# Step 2: Validate required columns
# ---------------------------------------------------------------------------

def validate_columns(df):
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in df.columns]
    if missing:
        print(
            f"ERROR: Missing required source columns: {missing}",
            file=sys.stderr,
        )
        print(f"Available columns: {list(df.columns)}", file=sys.stderr)
        sys.exit(1)
    print("All required source columns present.")


# ---------------------------------------------------------------------------
# Step 3: Remove ghost rows
# ---------------------------------------------------------------------------

def remove_ghost_rows(df):
    """
    Remove:
    1. Completely blank rows.
    2. Rows where Overall Status is blank/null.
    """
    before = len(df)

    # Remove completely blank rows
    blank_mask = (df == "").all(axis=1)
    df = df[~blank_mask].copy()
    after_blank = len(df)

    # Remove rows with blank Overall Status
    df = df[df["Overall Status"] != ""].copy()
    after_status = len(df)

    print(f"Completely blank rows removed: {before - after_blank}")
    print(f"Rows with blank status removed: {after_blank - after_status}")
    print(f"Valid BD Tracker records: {len(df)}")
    return df


# ---------------------------------------------------------------------------
# Step 4: Select and rename
# ---------------------------------------------------------------------------

def select_and_rename(df):
    selected = df[REQUIRED_SOURCE_COLUMNS].copy()
    selected.rename(columns=COLUMN_RENAME_MAP, inplace=True)
    return selected


# ---------------------------------------------------------------------------
# Step 5: Clean text fields
# ---------------------------------------------------------------------------

def clean_text_fields(df):
    for col in ["Raw_BD_Tracker_PO", "Raw_BD_Tracker_AGI", "Overall_Import_Status"]:
        df[col] = df[col].apply(clean_text)
    return df


# ---------------------------------------------------------------------------
# Step 6: Add Source_Row_ID
# ---------------------------------------------------------------------------

def add_row_id(df):
    df.insert(0, "Source_Row_ID", range(1, len(df) + 1))
    return df


# ---------------------------------------------------------------------------
# Step 7: Standardise PO and extract partial-shipment reference
# ---------------------------------------------------------------------------

def parse_po_number(raw_po):
    """
    Parse a raw BD Tracker PO value.

    If it matches "<PO> - <number>", return (parent_po, number).
    Otherwise return (cleaned_po, None).
    """
    if _is_blank(raw_po):
        return None, None

    po = clean_text(raw_po)
    if po is None:
        return None, None

    parts = po.split(" - ")
    if len(parts) == 2:
        suffix = parts[1].strip()
        try:
            float(suffix)
            return parts[0].strip(), suffix
        except (ValueError, TypeError):
            pass

    return po, None


def standardise_po(df):
    pos = []
    refs = []
    for val in df["Raw_BD_Tracker_PO"]:
        po, ref = parse_po_number(val)
        pos.append(po)
        refs.append(ref)
    df["Standardised_PO_Number"] = pos
    df["Partial_Shipment_Reference"] = refs
    return df


# ---------------------------------------------------------------------------
# Step 8: Standardise AGI — strip leading zeros for cross-source merge key
# ---------------------------------------------------------------------------

def standardise_agi(df):
    df["Standardised_Material_AGI_Stripped"] = df["Raw_BD_Tracker_AGI"].apply(
        lambda x: x.lstrip("0") if x is not None else None
    )
    return df


# ---------------------------------------------------------------------------
# Step 9: Create clean date fields
# ---------------------------------------------------------------------------

def _parse_date(val):
    """Try to parse a date value from string, returning None on failure."""
    if _is_blank(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d.%m.%y",
    ]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Fallback: let pandas parse it
    try:
        return pd.to_datetime(s).date()
    except (ValueError, TypeError):
        return None


def create_clean_dates(df):
    for raw_col, clean_col in DATE_FIELD_PAIRS:
        df[clean_col] = df[raw_col].apply(_parse_date)
    return df


# ---------------------------------------------------------------------------
# Step 10: Date quality flag
# ---------------------------------------------------------------------------

def flag_date_quality(df):
    """
    Flag non-blank raw date values that could not be parsed as valid dates.
    """
    invalid_fields = []
    for raw_col, clean_col in DATE_FIELD_PAIRS:
        for idx in df.index:
            raw_val = df.at[idx, raw_col]
            clean_val = df.at[idx, clean_col]
            if not _is_blank(raw_val) and clean_val is None:
                invalid_fields.append((idx, clean_col))

    flags = {}
    for idx, field in invalid_fields:
        flags.setdefault(idx, []).append(field)

    flag_series = []
    for idx in df.index:
        if idx in flags:
            field_names = sorted(set(flags[idx]))
            flag_series.append(
                "Invalid or unreadable date: " + ", ".join(field_names)
            )
        else:
            flag_series.append("OK")
    df["Date_Quality_Flag"] = flag_series
    return df


# ---------------------------------------------------------------------------
# Step 11: Overall status quality flag
# ---------------------------------------------------------------------------

def flag_status_quality(df):
    flags = []
    for val in df["Overall_Import_Status"]:
        s = clean_text(val) if not _is_blank(val) else None
        if s is not None and s in APPROVED_STATUSES:
            flags.append("OK")
        else:
            flags.append(
                "Non-standard Overall Status value - Manual review required."
            )
    df["Overall_Status_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 12: Data quality flag (ordered priority)
# ---------------------------------------------------------------------------

def flag_data_quality(df):
    flags = []
    for _, row in df.iterrows():
        po = row.get("Standardised_PO_Number")
        status_flag = row.get("Overall_Status_Quality_Flag")
        date_flag = row.get("Date_Quality_Flag")
        rdd = row.get("RDD")

        if _is_blank(po):
            flags.append("Missing PO")
        elif status_flag != "OK":
            flags.append(status_flag)
        elif date_flag != "OK":
            flags.append(date_flag)
        elif rdd is None:
            flags.append("RDD Missing - Risk Cannot Be Calculated")
        else:
            flags.append("OK")
    df["Data_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 13: Build clean output (remove raw date fields and raw PO)
# ---------------------------------------------------------------------------

def build_clean_output(df):
    out = df.copy()
    for col in CLEAN_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[CLEAN_OUTPUT_COLUMNS]
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_workbook(clean_df, output_path):
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        clean_df.to_excel(writer, sheet_name="q_BD_Tracker_Clean", index=False)
    print(f"Workbook written: {output_path}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def clean_bd_tracker(input_path, output_path):
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")

    # 1. Read
    raw = read_bd_tracker(input_path)

    # 2. Validate
    validate_columns(raw)

    # 3. Remove ghost rows
    data = remove_ghost_rows(raw)

    # 4. Select and rename
    audit = select_and_rename(data)

    # 5. Clean text
    audit = clean_text_fields(audit)

    # 6. Add Source_Row_ID
    audit = add_row_id(audit)

    # 7. Standardise PO and partial shipment
    audit = standardise_po(audit)

    # 8. Standardise AGI (strip leading zeros)
    audit = standardise_agi(audit)

    # 9. Create clean dates
    audit = create_clean_dates(audit)

    # 10. Date quality flag
    audit = flag_date_quality(audit)

    # 11. Status quality flag
    audit = flag_status_quality(audit)

    # 12. Data quality flag
    audit = flag_data_quality(audit)

    # 13. Build clean output (removes raw dates and raw PO per M code)
    clean_out = build_clean_output(audit)

    # Add run tracking columns
    clean_out["Source_Filename"] = os.path.basename(input_path)
    clean_out["Run_Timestamp"] = datetime.now().isoformat()

    # 15. Export
    export_workbook(clean_out, output_path)

    # Reconciliation
    print("\n--- Reconciliation ---")
    print(f"Source rows (total in Tracker File):     {len(raw)}")
    print(f"Ghost rows removed:                       {len(raw) - len(audit)}")
    print(f"Valid BD Tracker records:                 {len(audit)}")
    print(f"Rows with partial-shipment suffix:        {audit['Partial_Shipment_Reference'].notna().sum()}")
    print(f"Unique standardised POs:                  {audit['Standardised_PO_Number'].nunique()}")
    print(f"Unique AGI values (stripped):             {audit['Standardised_Material_AGI_Stripped'].nunique()}")
    print(f"\nOverall Status distribution:")
    print(audit["Overall_Import_Status"].value_counts().to_string())
    print(f"\nData Quality Flag distribution:")
    print(audit["Data_Quality_Flag"].value_counts().to_string())
    print(f"\nDate Quality Flag distribution:")
    print(audit["Date_Quality_Flag"].value_counts().to_string())
    print(f"\nOverall Status Quality Flag distribution:")
    print(audit["Overall_Status_Quality_Flag"].value_counts().to_string())

    return audit, clean_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clean BD Tracker file for Import Tracker pipeline."
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Path to the BD Tracker Excel workbook"
    )
    parser.add_argument(
        "--output", "-o",
        default="q_BD_Tracker_Clean.xlsx",
        help="Output path (default: q_BD_Tracker_Clean.xlsx)",
    )
    args = parser.parse_args()
    clean_bd_tracker(args.input, args.output)


if __name__ == "__main__":
    main()
