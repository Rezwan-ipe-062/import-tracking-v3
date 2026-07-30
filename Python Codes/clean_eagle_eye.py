"""
clean_eagle_eye.py — Eagle Eye cleaning module (Import Tracker pipeline Step 3)

Reproduces the approved Power Query M-code cleaning logic faithfully.
Output: single-sheet workbook with the clean Eagle Eye container-detail layer.

Usage:
    python clean_eagle_eye.py --input "Eagle eye.xlsx" --output "q_Eagle_Eye_Clean.xlsx"

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
    "From",
    "DDPO",
    "AGI Code",
    "Container No.",
    "Tracking",
    "Status",
    "ETA",
]

COLUMN_RENAME_MAP = {
    "From": "Origin_Code",
    "DDPO": "Raw_Eagle_Eye_DDPO",
    "AGI Code": "Raw_Eagle_Eye_AGI",
    "Container No.": "Raw_Container_Number",
    "Tracking": "Tracking_Information",
    "Status": "Eagle_Eye_Status",
    "ETA": "Raw_Eagle_Eye_ETA",
}

CLEAN_OUTPUT_COLUMNS = [
    "Source_Row_ID",
    "Origin_Code",
    "Origin_Code_Quality_Flag",
    "Standardised_PO_Number",
    "Standardised_Material_AGI_Stripped",
    "Container_Number",
    "Tracking_Information",
    "Eagle_Eye_Status",
    "Eagle_Eye_ETA",
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
# Step 1: Read source
# ---------------------------------------------------------------------------

def read_eagle_eye(filepath):
    try:
        xls = pd.ExcelFile(filepath)
        if "Sheet1" not in xls.sheet_names:
            print(
                "ERROR: Required sheet 'Sheet1' not found in workbook.",
                file=sys.stderr,
            )
            print(f"Available sheets: {xls.sheet_names}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not open workbook: {e}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(
        filepath,
        sheet_name="Sheet1",
        dtype=str,
        keep_default_na=False,
    )
    print(f"Read {len(df)} total rows from 'Sheet1'")
    return df


# ---------------------------------------------------------------------------
# Step 2: Validate columns
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
# Step 3: Remove ghost rows (blank DDPO)
# ---------------------------------------------------------------------------

def remove_ghost_rows(df):
    before = len(df)
    blank_ddpo = (df["DDPO"] == "").sum()
    df = df[df["DDPO"] != ""].copy()
    print(f"Rows with blank DDPO removed: {before - len(df)}")
    print(f"Valid Eagle Eye records: {len(df)}")
    return df


# ---------------------------------------------------------------------------
# Step 4: Select and rename
# ---------------------------------------------------------------------------

def select_and_rename(df):
    selected = df[REQUIRED_SOURCE_COLUMNS].copy()
    selected.rename(columns=COLUMN_RENAME_MAP, inplace=True)
    return selected


# ---------------------------------------------------------------------------
# Step 5: Clean text and uppercase origin
# ---------------------------------------------------------------------------

def clean_text_fields(df):
    for col in [
        "Origin_Code",
        "Raw_Eagle_Eye_DDPO",
        "Raw_Eagle_Eye_AGI",
        "Raw_Container_Number",
        "Tracking_Information",
        "Eagle_Eye_Status",
        "Raw_Eagle_Eye_ETA",
    ]:
        df[col] = df[col].apply(clean_text)
    # Uppercase origin code
    df["Origin_Code"] = df["Origin_Code"].apply(
        lambda x: x.upper() if not _is_blank(x) else None
    )
    return df


# ---------------------------------------------------------------------------
# Step 6: Add Source_Row_ID
# ---------------------------------------------------------------------------

def add_row_id(df):
    df.insert(0, "Source_Row_ID", range(1, len(df) + 1))
    return df


# ---------------------------------------------------------------------------
# Step 7: Standardise PO number (remove leading F/G)
# ---------------------------------------------------------------------------

def standardise_po(df):
    pos = []
    for val in df["Raw_Eagle_Eye_DDPO"]:
        if _is_blank(val):
            pos.append(None)
            continue
        po = clean_text(val)
        if po is None:
            pos.append(None)
            continue
        first = po[0].upper()
        if first in ("F", "G"):
            pos.append(po[1:].strip())
        else:
            pos.append(po)
    df["Standardised_PO_Number"] = pos
    return df


# ---------------------------------------------------------------------------
# Step 8: Standardise AGI — strip leading zeros for cross-source merge key
# ---------------------------------------------------------------------------

def standardise_agi(df):
    df["Standardised_Material_AGI_Stripped"] = df["Raw_Eagle_Eye_AGI"].apply(
        lambda x: x.lstrip("0") if x is not None else None
    )
    return df


# ---------------------------------------------------------------------------
# Step 9: Clean container number
# ---------------------------------------------------------------------------

def clean_container(df):
    containers = []
    for val in df["Raw_Container_Number"]:
        if _is_blank(val):
            containers.append(None)
            continue
        c = clean_text(val)
        if c is None or c == "-":
            containers.append(None)
        else:
            containers.append(c)
    df["Container_Number"] = containers
    return df


# ---------------------------------------------------------------------------
# Step 10: Clean Eagle_Eye_ETA
# ---------------------------------------------------------------------------

def _parse_date(val):
    if _is_blank(val):
        return None
    s = str(val).strip()
    if s == "-":
        return None
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
    ]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s).date()
    except (ValueError, TypeError):
        return None


def clean_eta(df):
    df["Eagle_Eye_ETA"] = df["Raw_Eagle_Eye_ETA"].apply(_parse_date)
    return df


# ---------------------------------------------------------------------------
# Step 11: Origin_Code_Quality_Flag
# ---------------------------------------------------------------------------

def flag_origin_quality(df):
    flags = []
    for val in df["Origin_Code"]:
        if _is_blank(val):
            flags.append("Missing Origin Code")
        else:
            s = str(val).strip().upper()
            if len(s) != 2:
                flags.append("Unexpected Origin Code - Manual review required.")
            else:
                flags.append("OK")
    df["Origin_Code_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 12: Date_Quality_Flag
# ---------------------------------------------------------------------------

def flag_date_quality(df):
    flags = []
    for _, row in df.iterrows():
        raw = row.get("Raw_Eagle_Eye_ETA")
        clean = row.get("Eagle_Eye_ETA")
        if _is_blank(raw) or str(raw).strip() == "-":
            flags.append("OK")
        elif clean is None:
            flags.append("Invalid or unreadable date: Eagle_Eye_ETA")
        else:
            flags.append("OK")
    df["Date_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 13: Data_Quality_Flag (ordered priority)
# ---------------------------------------------------------------------------

def flag_data_quality(df):
    flags = []
    for _, row in df.iterrows():
        po = row.get("Standardised_PO_Number")
        origin_flag = row.get("Origin_Code_Quality_Flag")
        date_flag = row.get("Date_Quality_Flag")
        status = row.get("Eagle_Eye_Status")

        if _is_blank(po):
            flags.append("Missing DDPO / PO")
        elif origin_flag != "OK":
            flags.append(origin_flag)
        elif date_flag != "OK":
            flags.append(date_flag)
        elif _is_blank(status):
            flags.append("Missing Eagle Eye Status")
        else:
            flags.append("OK")
    df["Data_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 14: Build clean output (removes Raw_Eagle_Eye_DDPO,
#           Raw_Container_Number, Raw_Eagle_Eye_ETA per M code)
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
        clean_df.to_excel(writer, sheet_name="q_Eagle_Eye_Clean", index=False)
    print(f"Workbook written: {output_path}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def clean_eagle_eye(input_path, output_path):
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")

    # 1. Read
    raw = read_eagle_eye(input_path)

    # 2. Validate
    validate_columns(raw)

    # 3. Remove ghost rows (blank DDPO)
    data = remove_ghost_rows(raw)

    # 4. Select and rename
    audit = select_and_rename(data)

    # 5. Clean text + uppercase origin
    audit = clean_text_fields(audit)

    # 6. Add Source_Row_ID
    audit = add_row_id(audit)

    # 7. Standardise PO
    audit = standardise_po(audit)

    # 8. Standardise AGI (strip leading zeros)
    audit = standardise_agi(audit)

    # 9. Clean container number
    audit = clean_container(audit)

    # 10. Clean ETA
    audit = clean_eta(audit)

    # 11. Origin quality flag
    audit = flag_origin_quality(audit)

    # 12. Date quality flag
    audit = flag_date_quality(audit)

    # 13. Data quality flag
    audit = flag_data_quality(audit)

    # 14. Build clean output (remove raw fields per M code)
    clean_out = build_clean_output(audit)

    # Add run tracking columns
    clean_out["Source_Filename"] = os.path.basename(input_path)
    clean_out["Run_Timestamp"] = datetime.now().isoformat()

    # 15. Export
    export_workbook(clean_out, output_path)

    # Reconciliation
    print("\n--- Reconciliation ---")
    print(f"Source rows:                          {len(raw)}")
    print(f"Blank DDPO rows removed:              {len(raw) - len(audit)}")
    print(f"Valid Eagle Eye records:              {len(audit)}")
    print(f"Unique standardised POs:              {audit['Standardised_PO_Number'].nunique()}")
    print(f"Unique AGI values (stripped):         {audit['Standardised_Material_AGI_Stripped'].nunique()}")
    print(f"Rows with F-prefix DDPO:              {(audit['Raw_Eagle_Eye_DDPO'].str.upper().str.startswith('F').fillna(False)).sum()}")
    print(f"Rows with G-prefix DDPO:              {(audit['Raw_Eagle_Eye_DDPO'].str.upper().str.startswith('G').fillna(False)).sum()}")
    print(f"Non-null Container_Number:            {audit['Container_Number'].notna().sum()}")
    print(f"\nOrigin Code distribution:")
    print(audit["Origin_Code"].value_counts().to_string())
    print(f"\nStatus distribution:")
    print(audit["Eagle_Eye_Status"].value_counts().to_string())
    print(f"\nData Quality Flag distribution:")
    print(audit["Data_Quality_Flag"].value_counts().to_string()) if len(audit["Data_Quality_Flag"].value_counts()) > 0 else None
    print(f"\nOrigin Code Quality Flag distribution:")
    print(audit["Origin_Code_Quality_Flag"].value_counts().to_string())
    print(f"\nDate Quality Flag distribution:")
    print(audit["Date_Quality_Flag"].value_counts().to_string())

    return audit, clean_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clean Eagle Eye file for Import Tracker pipeline."
    )
    parser.add_argument(
        "--input", "-i", required=True,
        help="Path to the Eagle Eye Excel workbook",
    )
    parser.add_argument(
        "--output", "-o",
        default="q_Eagle_Eye_Clean.xlsx",
        help="Output path (default: q_Eagle_Eye_Clean.xlsx)",
    )
    args = parser.parse_args()
    clean_eagle_eye(args.input, args.output)


if __name__ == "__main__":
    main()
