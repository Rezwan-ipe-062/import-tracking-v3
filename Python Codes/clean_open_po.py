"""
clean_open_po.py — Open PO cleaning script (Step 1 of Import Tracker pipeline)

Reproduces the approved Power Query M-code cleaning logic faithfully.

Output: single-sheet Excel workbook with the import base (q_Open_PO_Import_Base).

Usage:
    python clean_open_po.py --input "Open PO.xlsx" --output "q_Open_PO_Import_Base.xlsx"

Dependencies: Python 3, pandas, openpyxl
"""

import argparse
import os
import re
import sys
from datetime import datetime

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration — change these to match your source workbook
# ---------------------------------------------------------------------------
DEFAULT_INPUT_SHEET = "Data"
DEFAULT_OUTPUT = "q_Open_PO_Import_Base.xlsx"

REQUIRED_SOURCE_COLUMNS = [
    "Material",
    "Short Text",
    "Purchasing Document",
    "Supplier/Supplying Plant",
    "Still to be delivered (qty)",
    "Order Unit",
]

COLUMN_RENAME_MAP = {
    "Material": "Material_AGI",
    "Short Text": "Product_Name",
    "Purchasing Document": "Raw_PO_Number",
    "Supplier/Supplying Plant": "Raw_Supplier_Plant",
    "Still to be delivered (qty)": "Open_Quantity",
    "Order Unit": "Unit_of_Measure",
}

IMPORT_BASE_COLUMN_ORDER = [
    "Source_Row_ID",
    "Standardised_PO_Number",
    "Standardised_Material_AGI",
    "Standardised_Material_AGI_Stripped",
    "Product_Name",
    "Supplier_Plant_ID",
    "Supplier_Plant_Name",
    "Open_Quantity",
    "Unit_of_Measure",
    "Scope_Status",
    "Quantity_Quality_Flag",
    "Data_Quality_Flag",
    "PO_Line_Count",
    "PO_Line_Status",
]


def _is_blank(val):
    """Return True if value is None, NaN, or empty/whitespace string."""
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


# ---------------------------------------------------------------------------
# Step 1: Read source data
# ---------------------------------------------------------------------------

def read_open_po(filepath, sheet_name=None, table_name=None):
    """
    Read the raw Open PO workbook.

    Tries: Excel table first, then sheet, then first available sheet.
    Returns a pandas DataFrame with original column names.
    """
    if sheet_name is None:
        sheet_name = DEFAULT_INPUT_SHEET

    # Attempt 1: read the named sheet
    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name, dtype=str, keep_default_na=False)
        print(f"Read {len(df)} rows from sheet '{sheet_name}'")
        return df
    except (ValueError, KeyError) as exc:
        print(f"Sheet '{sheet_name}' not found: {exc}")

    # Attempt 2: read all sheets and try to find the table
    xls = pd.ExcelFile(filepath, dtype=str, keep_default_na=False)
    for s in xls.sheet_names:
        df = pd.read_excel(filepath, sheet_name=s, dtype=str, keep_default_na=False)
        # Check if it has the required columns
        missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in df.columns]
        if not missing:
            print(f"Found required columns in sheet '{s}' — reading from there.")
            return df

    # Attempt 3: use first sheet
    first_sheet = xls.sheet_names[0]
    df = pd.read_excel(filepath, sheet_name=first_sheet, dtype=str, keep_default_na=False)
    print(f"Reading from first available sheet '{first_sheet}'")
    return df


# ---------------------------------------------------------------------------
# Step 2: Validate required columns
# ---------------------------------------------------------------------------

def validate_columns(df):
    """Check that all REQUIRED_SOURCE_COLUMNS exist. Exit with error if not."""
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
# Step 3: Clean text fields
# ---------------------------------------------------------------------------

def clean_text(val):
    """
    Trim whitespace and remove non-printable characters.
    Preserves None/NaN values.
    """
    if _is_blank(val):
        return None
    s = str(val).strip()
    # Remove non-printable characters (ASCII control chars except common whitespace)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    s = s.strip()
    if s == "":
        return None
    return s


def clean_text_series(series):
    """Apply clean_text() to every element of a pandas Series."""
    return series.apply(clean_text)


# ---------------------------------------------------------------------------
# Step 4: Select and rename columns, add Source_Row_ID
# ---------------------------------------------------------------------------

def select_and_rename(df):
    """
    Keep only required columns, rename to canonical names, add Source_Row_ID.
    """
    selected = df[REQUIRED_SOURCE_COLUMNS].copy()
    selected.rename(columns=COLUMN_RENAME_MAP, inplace=True)
    # Add Source_Row_ID (1-based index, matching PQ behaviour)
    selected.insert(0, "Source_Row_ID", range(1, len(selected) + 1))
    return selected


# ---------------------------------------------------------------------------
# Step 5: Create standardised fields
# ---------------------------------------------------------------------------

def create_standardised_fields(df):
    """
    Create Standardised_PO_Number and Standardised_Material_AGI
    from cleaned raw fields.
    """
    df["Standardised_PO_Number"] = df["Raw_PO_Number"].apply(
        lambda x: clean_text(x) if not _is_blank(x) else None
    )
    df["Standardised_Material_AGI"] = df["Material_AGI"].apply(
        lambda x: clean_text(x) if not _is_blank(x) else None
    )
    df["Standardised_Material_AGI_Stripped"] = df["Standardised_Material_AGI"].apply(
        lambda x: x.lstrip("0") if x is not None else None
    )
    return df


# ---------------------------------------------------------------------------
# Step 6: Parse supplier/plant
# ---------------------------------------------------------------------------

def parse_supplier_plant(df):
    """
    Split Raw_Supplier_Plant at first space into Supplier_Plant_ID and
    Supplier_Plant_Name. Retain the untouched Raw_Supplier_Plant for audit.
    """
    id_list = []
    name_list = []

    for val in df["Raw_Supplier_Plant"]:
        if _is_blank(val):
            id_list.append(None)
            name_list.append(None)
        else:
            s = clean_text(val)
            parts = s.split(" ", 1)
            if len(parts) == 2:
                sid = clean_text(parts[0])
                sname = clean_text(parts[1])
                id_list.append(sid)
                name_list.append(sname)
            else:
                # Single token only — can't split
                id_list.append(clean_text(parts[0]))
                name_list.append(None)

    df["Supplier_Plant_ID"] = id_list
    df["Supplier_Plant_Name"] = name_list
    # Apply trim + clean to ID and name (matching PQ steps)
    df["Supplier_Plant_ID"] = clean_text_series(df["Supplier_Plant_ID"])
    df["Supplier_Plant_Name"] = clean_text_series(df["Supplier_Plant_Name"])
    return df


# ---------------------------------------------------------------------------
# Step 7: Quantity quality flag
# ---------------------------------------------------------------------------

def flag_quantity_quality(df):
    """
    Create Quantity_Quality_Flag with priority:
    Missing Quantity -> Invalid Quantity -> Negative Quantity -> OK
    """
    flags = []
    for val in df["Open_Quantity"]:
        # Check missing
        if _is_blank(val):
            flags.append("Missing Quantity")
            continue

        # Try numeric conversion
        try:
            qty = float(str(val).replace(",", "."))
        except (ValueError, TypeError):
            flags.append("Invalid Quantity")
            continue

        if qty < 0:
            flags.append("Negative Quantity")
        else:
            flags.append("OK")

    df["Quantity_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 8: Scope status
# ---------------------------------------------------------------------------

def flag_scope_status(df):
    """
    Create Scope_Status with priority:
    Missing_PO -> Excluded_Local_Interplant -> In_Scope_Import
    """
    flags = []
    for val in df["Standardised_PO_Number"]:
        if _is_blank(val):
            flags.append("Missing_PO")
        elif str(val).strip().startswith("62"):
            flags.append("Excluded_Local_Interplant")
        else:
            flags.append("In_Scope_Import")
    df["Scope_Status"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 9: Data quality flag (ordered priority)
# ---------------------------------------------------------------------------

def flag_data_quality(df):
    """
    Create Data_Quality_Flag with this priority order:
    1. Missing PO
    2. Missing Material
    3. Missing Product Name
    4. Supplier Parsing Review
    5. Missing or Invalid Quantity
    6. Negative Quantity
    7. OK
    """
    flags = []
    for _, row in df.iterrows():
        po = row.get("Standardised_PO_Number", None)
        material = row.get("Standardised_Material_AGI", None)
        product = row.get("Product_Name", None)
        supplier_id = row.get("Supplier_Plant_ID", None)
        raw_qty = row.get("Open_Quantity", None)

        if _is_blank(po):
            flags.append("Missing PO")
            continue

        if _is_blank(material):
            flags.append("Missing Material")
            continue

        if _is_blank(product):
            flags.append("Missing Product Name")
            continue

        if _is_blank(supplier_id):
            flags.append("Supplier Parsing Review")
            continue

        # Quantity check
        if _is_blank(raw_qty):
            flags.append("Missing or Invalid Quantity")
            continue

        try:
            qty = float(str(raw_qty).replace(",", "."))
        except (ValueError, TypeError):
            flags.append("Missing or Invalid Quantity")
            continue

        if qty < 0:
            flags.append("Negative Quantity")
            continue

        flags.append("OK")

    df["Data_Quality_Flag"] = flags
    return df


# ---------------------------------------------------------------------------
# Step 10: Build PO line counts
# ---------------------------------------------------------------------------

def build_po_counts(df):
    """
    Group by Standardised_PO_Number to create PO_Line_Count.
    Returns a Series with PO -> count mapping.
    """
    counts = df.groupby("Standardised_PO_Number").size()
    counts.name = "PO_Line_Count"
    return counts


# ---------------------------------------------------------------------------
# Step 11: Build import base (the only output sheet)
# ---------------------------------------------------------------------------

def build_import_base(df, counts_series):
    """
    Build the in-scope import base:
    - Left-merge counts
    - Create PO_Line_Status
    - Filter In_Scope_Import
    - Drop Material_AGI and Raw_PO_Number (matching PQ behaviour)
    - Reorder columns
    """
    base = df.copy()

    # Merge counts
    counts_df = counts_series.reset_index()
    counts_df.columns = ["Standardised_PO_Number", "PO_Line_Count"]
    base = base.merge(counts_df, on="Standardised_PO_Number", how="left")

    # Create PO_Line_Status
    base["PO_Line_Status"] = base["PO_Line_Count"].apply(
        lambda x: "Multiple_Material_or_PO_Lines" if x is not None and x > 1
        else "Single_PO_Line"
    )

    # Filter to in-scope
    base = base[base["Scope_Status"] == "In_Scope_Import"].copy()

    # Add missing columns
    for col in IMPORT_BASE_COLUMN_ORDER:
        if col not in base.columns:
            base[col] = None

    # Reorder
    base = base[IMPORT_BASE_COLUMN_ORDER]

    # Drop Material_AGI and Raw_PO_Number (matching PQ final step)
    base = base.drop(columns=["Raw_PO_Number"], errors="ignore")
    # Material_AGI is not in IMPORT_BASE_COLUMN_ORDER so it's already excluded

    return base.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 12: Build audit summary (printed to console only)
# ---------------------------------------------------------------------------

def build_audit_summary(input_filename, source_row_count, df_audit, counts_series):
    """
    Build a small summary DataFrame with key counts.
    """
    excluded_count = len(df_audit[df_audit["Scope_Status"] == "Excluded_Local_Interplant"])
    in_scope_count = len(df_audit[df_audit["Scope_Status"] == "In_Scope_Import"])

    # Unique in-scope POs
    in_scope_pos = df_audit[df_audit["Scope_Status"] == "In_Scope_Import"]["Standardised_PO_Number"]
    unique_in_scope_pos = in_scope_pos.nunique()

    # Repeated PO rows (all scope statuses, count of rows with PO_Line_Count > 1)
    po_line_counts = df_audit.merge(
        counts_series.reset_index().rename(columns={0: "PO_Line_Count"}),
        on="Standardised_PO_Number",
        how="left"
    )
    repeated_row_count = len(po_line_counts[po_line_counts["PO_Line_Count"] > 1])

    # Data quality flag distribution
    dq_counts = df_audit["Data_Quality_Flag"].value_counts().to_dict()

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = [
        ("Input file name", input_filename),
        ("Source row count", source_row_count),
        ("Scope_Status = Excluded_Local_Interplant", excluded_count),
        ("Scope_Status = In_Scope_Import", in_scope_count),
        ("Unique in-scope PO count", unique_in_scope_pos),
        ("Repeated-PO row count", repeated_row_count),
    ]

    # Add DQ flag breakdown
    for flag in ["Missing PO", "Missing Material", "Missing Product Name",
                  "Supplier Parsing Review", "Missing or Invalid Quantity",
                  "Negative Quantity", "OK"]:
        cnt = dq_counts.get(flag, 0)
        rows.append((f"DQ: {flag}", cnt))

    rows.append(("Script run timestamp", now_str))

    summary_df = pd.DataFrame(rows, columns=["Metric", "Value"])
    return summary_df


# ---------------------------------------------------------------------------
# Step 13: Export workbook
# ---------------------------------------------------------------------------

def export_workbook(import_base_df, output_path):
    """Write the single import-base sheet to an Excel workbook."""
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        import_base_df.to_excel(writer, sheet_name="q_Open_PO_Import_Base", index=False)
    print(f"Workbook written: {output_path}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def clean_open_po(input_path, output_path, sheet_name=None, table_name=None):
    """
    Run the full Open PO cleaning pipeline.
    """
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")

    # 1. Read
    raw = read_open_po(input_path, sheet_name=sheet_name, table_name=table_name)
    source_row_count = len(raw)
    print(f"Source row count: {source_row_count}")

    # 2. Validate columns
    validate_columns(raw)

    # 3. Select, rename, add Source_Row_ID
    audit = select_and_rename(raw)

    # 4. Clean text fields
    for col in ["Material_AGI", "Product_Name", "Raw_PO_Number",
                 "Raw_Supplier_Plant", "Open_Quantity", "Unit_of_Measure"]:
        audit[col] = clean_text_series(audit[col])

    # 5. Create standardised fields
    audit = create_standardised_fields(audit)

    # 6. Parse supplier/plant
    audit = parse_supplier_plant(audit)

    # 7. Quantity quality flag
    audit = flag_quantity_quality(audit)

    # 8. Scope status
    audit = flag_scope_status(audit)

    # 9. Data quality flag
    audit = flag_data_quality(audit)

    # 10. Build PO counts
    counts = build_po_counts(audit)

    # 11. Build import base
    import_base_out = build_import_base(audit, counts)

    # Add run tracking columns
    import_base_out["Source_Filename"] = os.path.basename(input_path)
    import_base_out["Run_Timestamp"] = datetime.now().isoformat()

    # 12. Export (single sheet)
    export_workbook(import_base_out, output_path)

    # 13. Print reconciliation
    print("\n--- Reconciliation ---")
    print(f"Source rows:                 {source_row_count}")
    excluded = len(audit[audit["Scope_Status"] == "Excluded_Local_Interplant"])
    in_scope = len(audit[audit["Scope_Status"] == "In_Scope_Import"])
    print(f"Excluded (62...) rows:       {excluded}")
    print(f"In-scope rows:               {in_scope}")
    unique_po = audit[audit["Scope_Status"] == "In_Scope_Import"]["Standardised_PO_Number"].nunique()
    print(f"Unique in-scope POs:         {unique_po}")
    repeated_rows = len(audit.merge(
        counts.reset_index().rename(columns={0: "PO_Line_Count"}),
        on="Standardised_PO_Number", how="left"
    ).query("PO_Line_Count > 1"))
    print(f"Repeated PO rows:            {repeated_rows}")
    print(f"\nData Quality Flag distribution:")
    print(audit["Data_Quality_Flag"].value_counts().to_string())

    return import_base_out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Clean Open PO file for Import Tracker pipeline."
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the Open PO Excel workbook",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--sheet",
        default=DEFAULT_INPUT_SHEET,
        help=f"Source sheet name (default: '{DEFAULT_INPUT_SHEET}')",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Source Excel table name (optional, if different from sheet)",
    )
    args = parser.parse_args()

    clean_open_po(
        input_path=args.input,
        output_path=args.output,
        sheet_name=args.sheet,
        table_name=args.table,
    )


if __name__ == "__main__":
    main()
