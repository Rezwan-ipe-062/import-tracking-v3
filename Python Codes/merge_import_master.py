"""
merge_import_master.py — Import Master Data Merge (Step 4)

Merges three cleaned sources:
  - Open PO (base)        → grain: (PO, AGI)
  - BD Tracker            → grain: (PO, AGI, Partial_Shipment_Reference)
  - Eagle Eye             → grain: (PO, AGI) — aggregated from container level

Join key: (Standardised_PO_Number, Standardised_Material_AGI_Stripped)
PO-only fallback for single-material POs only.

Output: single-sheet Excel workbook with the merged master data.

Usage:
    python merge_import_master.py ^
        --open-po q_Open_PO_Import_Base.xlsx ^
        --bd-tracker q_BD_Tracker_Clean.xlsx ^
        --eagle-eye q_Eagle_Eye_Clean.xlsx ^
        --output import_master_data.xlsx

Dependencies: Python 3, pandas, openpyxl
"""

import argparse
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime

import pandas as pd


# ---------------------------------------------------------------------------
# Configuration — output column order
# ---------------------------------------------------------------------------

MASTER_COLUMN_ORDER = [
    # Composite key
    "Standardised_PO_Number",
    "Standardised_Material_AGI",
    "Standardised_Material_AGI_Stripped",
    "Partial_Shipment_Reference",
    # PO metadata
    "Product_Name",
    "Supplier_Plant_ID",
    "Supplier_Plant_Name",
    "Open_Quantity",
    "Quantity_Distribution_Note",
    "Unit_of_Measure",
    "PO_Line_Count",
    "PO_Line_Status",
    "Scope_Status",
    "Quantity_Quality_Flag",
    "Open_PO_Data_Quality_Flag",
    # BD Tracker fields
    "Overall_Import_Status",
    "BD_Tracker_Data_Quality_Flag",
    "LC_Date",
    "SI_Shared_Date",
    "RDD",
    "BD_Tracker_ETD",
    "BD_Tracker_ETA",
    "OBL_EBL_Received_Date",
    "Final_Documents_Received_Date",
    # Eagle Eye aggregated fields
    "Origin_Code",
    "Container_Count",
    "Container_Numbers",
    "Earliest_EE_ETA",
    "Latest_EE_ETA",
    "Eagle_Eye_Data_Quality_Flag",
    # Match tracking
    "Has_BD_Tracker_Match",
    "Has_Eagle_Eye_Match",
    "Merge_Method",
    # Risk / computed
    "Days_Remaining_to_RDD",
    "Overall_Risk_Category",
    "Risk_Calculation_Status",
    "Data_Quality_Severity",
    "Data_Quality_Reasons",
    "Next_Required_Milestone",
    "As_Of_Date",
    # Run tracking
    "Source_Filename",
    "Run_Timestamp",
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


def _safe_str(val):
    return str(val).strip() if not _is_blank(val) else ""


def _parse_date(val):
    """Parse a date value and return ISO string or None."""
    if _is_blank(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    if not s:
        return None
    for fmt in [
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d",
        "%d-%m-%Y", "%d.%m.%Y",
    ]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        d = pd.to_datetime(s)
        return d.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------

def load_threshold_config(config_path=None):
    """Load threshold_config.json if it exists. Return config dict or None."""
    if config_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "threshold_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            print(f"  Threshold config loaded: {config_path}")
            return cfg
        except (json.JSONDecodeError, IOError) as e:
            print(f"  Warning: Could not parse threshold config: {e}")
            return None
    print("  No threshold config found — running without rules.")
    return None


# ---------------------------------------------------------------------------
# Step 1: Read clean source files
# ---------------------------------------------------------------------------

def read_clean_source(filepath, sheet_name=0):
    df = pd.read_excel(filepath, sheet_name=sheet_name, dtype=str, keep_default_na=False)
    print(f"  Read {len(df)} rows from {filepath.split(chr(92))[-1]}")
    return df


# ---------------------------------------------------------------------------
# Step 2: Clean merge keys across all sources
# ---------------------------------------------------------------------------

def _strip_series(series):
    return series.apply(
        lambda x: str(x).strip() if not _is_blank(x) else None
    )


def normalise_merge_keys(df, po_col="Standardised_PO_Number", agi_col="Standardised_Material_AGI_Stripped"):
    if po_col in df.columns:
        df[po_col] = _strip_series(df[po_col])
    if agi_col in df.columns and agi_col in df.columns:
        df[agi_col] = _strip_series(df[agi_col])
    return df


# ---------------------------------------------------------------------------
# Step 3: Aggregate Eagle Eye to (PO, AGI) level
# ---------------------------------------------------------------------------

def aggregate_eagle_eye(ee_df):
    if ee_df.empty:
        return pd.DataFrame()

    ee = ee_df.copy()
    ee["AGI_Key"] = ee["Standardised_Material_AGI_Stripped"].fillna("__NO_AGI__")

    groups = ee.groupby(["Standardised_PO_Number", "AGI_Key"], as_index=True)

    rows = []
    for (po, agi), grp in groups:
        containers = [str(c) for c in grp["Container_Number"].dropna().unique()
                      if str(c).strip() not in ("", "nan", "None")]
        statuses = [str(s) for s in grp["Eagle_Eye_Status"].dropna().unique()
                    if str(s).strip() not in ("", "nan", "None")]
        origins = [str(o) for o in grp["Origin_Code"].dropna().unique()
                   if str(o).strip() not in ("", "nan", "None")]
        etas = []
        for eta in grp["Eagle_Eye_ETA"]:
            parsed = _parse_date(eta)
            if parsed is not None:
                etas.append(parsed)
        etas_sorted = sorted(etas) if etas else []

        dq = str(grp["Data_Quality_Flag"].iloc[0]) if not grp.empty else None

        rows.append({
            "Standardised_PO_Number": po,
            "Standardised_Material_AGI_Stripped": None if agi == "__NO_AGI__" else agi,
            "Origin_Code": origins[0] if origins else None,
            "Container_Count": len(containers),
            "Container_Numbers": ", ".join(containers) if containers else None,
            "Earliest_EE_ETA": etas_sorted[0] if etas_sorted else None,
            "Latest_EE_ETA": etas_sorted[-1] if etas_sorted else None,
            "Eagle_Eye_Statuses": ", ".join(statuses) if statuses else None,
            "Eagle_Eye_Data_Quality_Flag": dq,
        })

    agg = pd.DataFrame(rows)

    print(f"  Aggregated Eagle Eye: {len(ee_df)} rows -> {len(agg)} (PO, AGI) groups")
    return agg


# ---------------------------------------------------------------------------
# Step 4: Merge BD Tracker into Open PO on (PO, AGI)
# ---------------------------------------------------------------------------

def merge_bd_tracker(op_df, bd_df):
    """
    Left-join Open PO -> BD Tracker on (PO, AGI_stripped) composite key.

    BD has 1:N with OP when partial shipments exist — this produces extra rows
    for those POs. Non-matching OP rows remain with null BD fields.
    """
    op_key = op_df[["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"]].drop_duplicates()
    op_count_before = len(op_df)

    merged = op_df.merge(
        bd_df,
        on=["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"],
        how="left",
        suffixes=("_op", "_bd"),
    )

    # Track match status
    merged["Has_BD_Tracker_Match"] = merged["LC_Date"].notna() | merged["RDD"].notna() | merged["Overall_Import_Status"].notna()

    # For unmatched rows, retain Open PO values for shared columns
    for col in ["Overall_Import_Status", "LC_Date", "SI_Shared_Date", "RDD",
                 "BD_Tracker_ETD", "BD_Tracker_ETA",
                 "OBL_EBL_Received_Date", "Final_Documents_Received_Date"]:
        if col in merged.columns and f"{col}_op" in merged.columns:
            pass  # BD columns already have right suffix or no overlap

    # Resolve Data_Quality_Flag collision
    if "Data_Quality_Flag_bd" in merged.columns:
        merged.rename(columns={"Data_Quality_Flag_bd": "BD_Tracker_Data_Quality_Flag"}, inplace=True)
    elif "Data_Quality_Flag" in merged.columns:
        merged.rename(columns={"Data_Quality_Flag": "BD_Tracker_Data_Quality_Flag"}, inplace=True)

    if "Data_Quality_Flag_op" in merged.columns:
        merged.rename(columns={"Data_Quality_Flag_op": "Open_PO_Data_Quality_Flag"}, inplace=True)

    # Drop any leftover suffix columns
    suffix_cols = [c for c in merged.columns if c.endswith("_op") or c.endswith("_bd")]
    for c in suffix_cols:
        base = c.rsplit("_", 1)[0]
        if base in merged.columns:
            merged.drop(columns=[c], inplace=True)

    # Handle partial-shipment quantity distribution
    # When an OP row matches 2+ BD partial shipments, the left merge inflates Open_Quantity.
    # Identify expansion groups: (PO, AGI) groups with multiple rows having partial-shipment refs.
    if "Partial_Shipment_Reference" in merged.columns:
        groupby_cols = ["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"]
        ps_nonnull = merged["Partial_Shipment_Reference"].notna()
        if ps_nonnull.any():
            ps_counts = merged[ps_nonnull].groupby(groupby_cols)["Partial_Shipment_Reference"].transform("count")
            exp_mask = ps_nonnull & (ps_counts >= 2)
            if exp_mask.any():
                n_expanded = exp_mask.sum()
                note_map = {}
                for (po, agi), grp in merged[ps_nonnull].groupby(groupby_cols):
                    cnt = grp["Partial_Shipment_Reference"].nunique()
                    if cnt >= 2:
                        note_map[(po, agi)] = f"Distributed across {cnt} partial shipments"
                for idx in merged.index:
                    if exp_mask[idx]:
                        po = merged.at[idx, "Standardised_PO_Number"]
                        agi = merged.at[idx, "Standardised_Material_AGI_Stripped"]
                        merged.at[idx, "Open_Quantity"] = None
                        merged.at[idx, "Quantity_Distribution_Note"] = note_map.get((po, agi))
                print(f"  Partial-shipment expansions: {n_expanded} rows in {len(note_map)} (PO, AGI) groups")
                print(f"  Open_Quantity set to None for expanded rows (distributed across partial shipments)")

    print(f"  OP ({op_count_before}) + BD merge: {len(merged)} rows")
    return merged


# ---------------------------------------------------------------------------
# Step 5: PO-only fallback for single-material POs
# ---------------------------------------------------------------------------

def apply_po_fallback(merged_df, bd_df):
    """
    For rows where (PO, AGI) didn't match BD, but the PO has exactly one
    material line: fall back to matching BD on PO alone.
    """
    unmatched_mask = ~merged_df["Has_BD_Tracker_Match"]
    single_line_mask = merged_df["PO_Line_Count"].astype(str).str.strip().isin(["1", "1.0"])
    fallback_candidates = merged_df[unmatched_mask & single_line_mask].copy()

    if fallback_candidates.empty:
        return merged_df

    fallback_pos = fallback_candidates["Standardised_PO_Number"].unique()
    bd_po_only = bd_df[bd_df["Standardised_PO_Number"].isin(fallback_pos)].copy()
    bd_po_only = bd_po_only.drop_duplicates(subset=["Standardised_PO_Number"])

    if bd_po_only.empty:
        return merged_df

    # Build fallback lookup — matchable columns only
    fallback_cols = [c for c in [
        "Standardised_PO_Number", "Standardised_Material_AGI_Stripped",
        "Partial_Shipment_Reference", "Overall_Import_Status",
        "LC_Date", "SI_Shared_Date", "RDD", "BD_Tracker_ETD",
        "BD_Tracker_ETA", "OBL_EBL_Received_Date",
        "Final_Documents_Received_Date",
    ] if c in bd_po_only.columns]
    bd_lookup = bd_po_only[fallback_cols].copy()
    bd_lookup = bd_lookup.rename(columns={
        "Standardised_Material_AGI_Stripped": "PO_Fallback_AGI",
        "Partial_Shipment_Reference": "PO_Fallback_Ref",
    })

    merged = merged_df.merge(
        bd_lookup,
        on="Standardised_PO_Number",
        how="left",
        suffixes=("", "_fallback"),
    )

    fallback_hit = merged["PO_Fallback_AGI"].notna() & ~merged["Has_BD_Tracker_Match"]
    n_fallback = fallback_hit.sum()

    # Copy fallback values into main columns
    for col in ["Overall_Import_Status", "LC_Date", "SI_Shared_Date", "RDD",
                 "BD_Tracker_ETD", "BD_Tracker_ETA",
                 "OBL_EBL_Received_Date", "Final_Documents_Received_Date"]:
        fb_col = f"{col}_fallback" if f"{col}_fallback" in merged.columns else f"PO_Fallback_{col}"
        if fb_col in merged.columns:
            merged.loc[fallback_hit, col] = merged.loc[fallback_hit, fb_col]

    merged.loc[fallback_hit, "Has_BD_Tracker_Match"] = True
    merged.loc[fallback_hit, "Merge_Method"] = "PO_Fallback"

    drop_cols = [c for c in merged.columns if c.endswith("_fallback") or c.startswith("PO_Fallback_")]
    merged.drop(columns=drop_cols, inplace=True, errors="ignore")

    print(f"  PO-only fallback matches: {n_fallback}")
    return merged


# ---------------------------------------------------------------------------
# Step 6: Merge aggregated Eagle Eye
# ---------------------------------------------------------------------------

def merge_eagle_eye(merged_df, ee_agg):
    if ee_agg.empty:
        merged_df["Has_Eagle_Eye_Match"] = False
        return merged_df

    merged = merged_df.merge(
        ee_agg,
        on=["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"],
        how="left",
        suffixes=("", "_ee"),
    )

    merged["Has_Eagle_Eye_Match"] = merged["Container_Count"].notna() | merged["Origin_Code"].notna()

    # Clean up suffix columns
    suffix_cols = [c for c in merged.columns if c.endswith("_ee")]
    for c in suffix_cols:
        base = c.rsplit("_", 1)[0]
        if base in merged.columns:
            merged.drop(columns=[c], inplace=True)

    print(f"  EE merge complete: {len(merged)} rows, {merged['Has_Eagle_Eye_Match'].sum()} EE matches")
    return merged


# ---------------------------------------------------------------------------
# Step 7: Computed columns — milestone sequence and risk axes
# ---------------------------------------------------------------------------

def compute_milestones_and_risk(df):
    from datetime import date as date_type
    as_of = date_type.today()
    df["As_Of_Date"] = as_of.isoformat()

    rdd_vals = df["RDD"].apply(_parse_date)
    df["RDD"] = rdd_vals

    lc_vals = df["LC_Date"].apply(_parse_date)
    df["LC_Date"] = lc_vals

    si_vals = df["SI_Shared_Date"].apply(_parse_date)
    df["SI_Shared_Date"] = si_vals

    etd_vals = df["BD_Tracker_ETD"].apply(_parse_date)
    df["BD_Tracker_ETD"] = etd_vals

    bd_eta_vals = df["BD_Tracker_ETA"].apply(_parse_date)
    df["BD_Tracker_ETA"] = bd_eta_vals

    obl_vals = df["OBL_EBL_Received_Date"].apply(_parse_date)
    df["OBL_EBL_Received_Date"] = obl_vals

    final_vals = df["Final_Documents_Received_Date"].apply(_parse_date)
    df["Final_Documents_Received_Date"] = final_vals

    def _iso_to_date(iso_str):
        if iso_str is None:
            return None
        try:
            return datetime.strptime(iso_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    # Days remaining to RDD
    days = []
    for rdd in rdd_vals:
        rdd_d = _iso_to_date(rdd)
        if rdd_d is not None:
            delta = (rdd_d - as_of).days
            days.append(delta)
        else:
            days.append(None)
    df["Days_Remaining_to_RDD"] = days

    risk_categories = []
    risk_statuses = []
    next_milestones = []
    dq_severities = []
    dq_reasons = []

    for i, (_, row) in enumerate(df.iterrows()):
        reasons = []
        has_bd = row.get("Has_BD_Tracker_Match", False)

        rdd = row.get("RDD")
        lc = row.get("LC_Date")
        si = si_vals.iloc[i] if i < len(si_vals) else None
        etd = row.get("BD_Tracker_ETD")
        eta = row.get("BD_Tracker_ETA")
        obl = row.get("OBL_EBL_Received_Date")
        final_docs = row.get("Final_Documents_Received_Date")

        # ---- Data Quality Severity (Axis 2) ----
        if not has_bd:
            reasons.append("Missing_Data: RDD unavailable — no BD Tracker match")
            dq_sev = "Missing_Data"
        else:
            missing_dates = []
            if rdd is None:
                missing_dates.append("RDD")
            if lc is None:
                missing_dates.append("LC_Date")
            if etd is None:
                missing_dates.append("BD_Tracker_ETD")
            if missing_dates:
                reasons.append(f"Missing_Data: {', '.join(missing_dates)} incomplete")
                dq_sev = "Missing_Data"
            else:
                dq_sev = "OK"

        # Sequence exception check
        if etd is not None and lc is None:
            reasons.append("Process_Sequence_Exception: ETD recorded before LC Date")
        if final_docs is not None and obl is None:
            reasons.append("Process_Sequence_Exception: Final Docs received before OBL/EBL")

        # ETA vs RDD warning
        if rdd is not None and eta is not None:
            rdd_d = _iso_to_date(rdd)
            eta_d = _iso_to_date(eta)
            if rdd_d is not None and eta_d is not None:
                if eta_d > rdd_d:
                    reasons.append("ETA_After_RDD: delivery risk — ETA exceeds RDD")
                elif eta_d == rdd_d:
                    reasons.append("ETA_On_RDD: no clearance buffer — zero days margin")

        if not reasons and dq_sev == "OK":
            dq_reasons.append("OK")
        else:
            dq_reasons.append("; ".join(reasons))
        dq_severities.append(dq_sev)

        # ---- Operational Risk (Axis 1) — placeholder ----
        # All risk categories remain null until thresholds are configured
        risk_categories.append(None)

        next_milestone = "RDD required"
        if rdd is not None:
            if lc is None:
                next_milestone = "LC Date"
            elif si is None:
                next_milestone = "SI Shared Date"
            elif etd is None:
                next_milestone = "BD Tracker ETD"
            elif obl is None:
                next_milestone = "OBL/EBL Received Date"
            elif final_docs is None:
                next_milestone = "Final Documents Received Date"
            else:
                next_milestone = "Milestones complete"

        next_milestones.append(next_milestone)
        risk_statuses.append("Rules not configured")

    df["Overall_Risk_Category"] = risk_categories
    df["Risk_Calculation_Status"] = risk_statuses
    df["Data_Quality_Severity"] = dq_severities
    df["Data_Quality_Reasons"] = dq_reasons
    df["Next_Required_Milestone"] = next_milestones

    return df


# ---------------------------------------------------------------------------
# Step 8: Set Merge_Method for standard matches
# ---------------------------------------------------------------------------

def set_merge_method(df):
    merge_method = []
    for _, row in df.iterrows():
        has_bd = row.get("Has_BD_Tracker_Match", False)
        has_ee = row.get("Has_Eagle_Eye_Match", False)
        po_only = row.get("Merge_Method", None)
        if po_only is not None and po_only == "PO_Fallback":
            merge_method.append("PO_Fallback")
        elif has_bd and has_ee:
            merge_method.append("Full (PO+AGI)")
        elif has_bd:
            merge_method.append("BD only")
        elif has_ee:
            merge_method.append("EE only")
        else:
            merge_method.append("Open PO only")
    df["Merge_Method"] = merge_method
    return df


# ---------------------------------------------------------------------------
# Step 9: Final reorder and cleanup
# ---------------------------------------------------------------------------

def finalise_output(df):
    for col in MASTER_COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    out = df[MASTER_COLUMN_ORDER].copy()
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 10: Reconciliation summary
# ---------------------------------------------------------------------------

def print_reconciliation(op_df, bd_df, ee_df, master_df, po_summary_df,
                          unmatched_bd_df, unmatched_ee_df, dq_exceptions_df,
                          op_excluded_count, bd_ghost_count, ee_blank_count,
                          op_source_rows, bd_source_rows, ee_source_rows):
    print("\n" + "=" * 60)
    print("RECONCILIATION REPORT — FULL TRACE")
    print("=" * 60)

    n_op = len(op_df)
    n_bd = len(bd_df)
    n_ee = len(ee_df)
    n_master = len(master_df)

    unique_op_pos = op_df["Standardised_PO_Number"].nunique()
    unique_bd_pos = bd_df["Standardised_PO_Number"].nunique()
    unique_ee_pos = ee_df["Standardised_PO_Number"].nunique()

    has_bd = master_df["Has_BD_Tracker_Match"].sum()
    has_ee = master_df["Has_Eagle_Eye_Match"].sum()
    no_bd = (~master_df["Has_BD_Tracker_Match"]).sum()
    no_ee = (~master_df["Has_Eagle_Eye_Match"]).sum()

    bd_not_in_op = sorted(set(bd_df["Standardised_PO_Number"].dropna().unique()) - set(op_df["Standardised_PO_Number"].dropna().unique()))
    ee_not_in_op = sorted(set(ee_df["Standardised_PO_Number"].dropna().unique()) - set(op_df["Standardised_PO_Number"].dropna().unique()))

    op_pos = set(op_df["Standardised_PO_Number"].dropna().unique())
    bd_pos = set(bd_df["Standardised_PO_Number"].dropna().unique())
    ee_pos = set(ee_df["Standardised_PO_Number"].dropna().unique())
    three_way = op_pos & bd_pos & ee_pos

    print(f"\n--- Source Row Accounting ---")
    print(f"  Open PO source rows:          {op_source_rows}")
    print(f"    Excluded (62...):           {op_excluded_count}")
    print(f"    In-scope:                   {n_op}")
    print(f"    Check:                      {op_excluded_count + n_op} (should = {op_source_rows})")
    print(f"  BD Tracker source rows:       {bd_source_rows}")
    print(f"    Ghost rows removed:         {bd_ghost_count}")
    print(f"    Valid BD records:           {n_bd}")
    print(f"    Check:                      {bd_ghost_count + n_bd} (should = {bd_source_rows})")
    print(f"  Eagle Eye source rows:        {ee_source_rows}")
    print(f"    Blank DDPO removed:         {ee_blank_count}")
    print(f"    Valid EE records:           {n_ee}")
    print(f"    Check:                      {ee_blank_count + n_ee} (should = {ee_source_rows})")

    print(f"\n--- Source Summary ---")
    print(f"  Open PO:   {n_op} rows, {unique_op_pos} unique POs")
    print(f"  BD Tracker: {n_bd} rows, {unique_bd_pos} unique POs")
    print(f"  Eagle Eye: {n_ee} rows, {unique_ee_pos} unique POs")

    print(f"\n--- Output Sheet Row Counts ---")
    print(f"  Master_Detail:               {n_master}")
    print(f"  PO_Summary:                  {len(po_summary_df)}")
    print(f"  Unmatched_BD:                {len(unmatched_bd_df)}")
    print(f"  Unmatched_EE:                {len(unmatched_ee_df)}")
    print(f"  DQ_Exceptions:               {len(dq_exceptions_df)}")

    print(f"\n--- Merge Coverage ---")
    print(f"  OP POs with BD match:        {has_bd} ({has_bd/max(n_master,1)*100:.0f}%)")
    print(f"  OP POs with EE match:        {has_ee} ({has_ee/max(n_master,1)*100:.0f}%)")
    print(f"  OP POs missing BD:           {no_bd}")
    print(f"  OP POs missing EE:           {no_ee}")
    print(f"  Three-way (OP+BD+EE):        {len(three_way)} POs")

    print(f"\n--- Merge Methods ---")
    print(master_df["Merge_Method"].value_counts().to_string())

    print(f"\n--- Data Quality ---")
    print(master_df["Data_Quality_Severity"].value_counts().to_string())

    print(f"\n--- Risk Status ---")
    print(master_df["Risk_Calculation_Status"].value_counts().to_string())

    print(f"\n--- Unmatched POs ---")
    print(f"  BD POs not in OP:            {len(bd_not_in_op)} — all Completed")
    print(f"    POs: {', '.join(str(p) for p in bd_not_in_op[:10])}{'...' if len(bd_not_in_op) > 10 else ''}")
    print(f"  EE POs not in OP:            {len(ee_not_in_op)}")
    print(f"    POs: {', '.join(str(p) for p in ee_not_in_op[:10])}{'...' if len(ee_not_in_op) > 10 else ''}")

    print(f"\n--- Per-Source Row Accounting ---")
    print(f"  OPEN PO: {op_source_rows} total")
    print(f"    = {op_excluded_count} excluded (62...) + {n_op} in-scope")
    op_check = (op_excluded_count + n_op == op_source_rows)
    print(f"    Check: {op_excluded_count} + {n_op} = {op_excluded_count + n_op} {'OK' if op_check else 'MISMATCH'}")

    bd_matched_cnt = n_master - no_bd  # master rows with BD match
    print(f"  BD TRACKER: {bd_source_rows} total")
    print(f"    = {bd_ghost_count} ghosts + {n_bd} valid")
    bd_valid_check = (bd_ghost_count + n_bd == bd_source_rows)
    print(f"    Check: {bd_ghost_count} + {n_bd} = {bd_ghost_count + n_bd} {'OK' if bd_valid_check else 'MISMATCH'}")
    print(f"    Of {n_bd} valid: {len(unmatched_bd_df)} unmatched POs, rest matched to OP")

    print(f"  EAGLE EYE: {ee_source_rows} total")
    print(f"    = {ee_blank_count} blanks + {n_ee} valid")
    ee_valid_check = (ee_blank_count + n_ee == ee_source_rows)
    print(f"    Check: {ee_blank_count} + {n_ee} = {ee_blank_count + n_ee} {'OK' if ee_valid_check else 'MISMATCH'}")
    print(f"    Of {n_ee} valid: {len(unmatched_ee_df)} unmatched POs, rest matched to OP")

    total_source = op_source_rows + bd_source_rows + ee_source_rows
    print(f"\n  TOTAL source rows: {total_source}")
    print(f"  Output rows: master={n_master}, PO_summary={len(po_summary_df)}, "
          f"unmatched_BD={len(unmatched_bd_df)}, unmatched_EE={len(unmatched_ee_df)}, "
          f"DQ_exceptions={len(dq_exceptions_df)} (subset of master)")


# ---------------------------------------------------------------------------
# PO Summary generation
# ---------------------------------------------------------------------------

def build_po_summary(master_df):
    """Aggregate master detail to one row per PO."""
    if master_df.empty:
        return pd.DataFrame()

    METHOD_PRIORITY = {
        "Full (PO+AGI)": 1,
        "BD only": 2,
        "EE only": 3,
        "PO_Fallback": 4,
        "Open PO only": 5,
    }

    rows = []
    for po, grp in master_df.groupby("Standardised_PO_Number", dropna=False):
        unique_qty = grp["Open_Quantity"].dropna().unique()
        if len(unique_qty) > 0:
            try:
                open_po_qty = sum(float(q) for q in unique_qty)
            except (ValueError, TypeError):
                open_po_qty = None
        else:
            open_po_qty = None

        merge_methods = grp["Merge_Method"].dropna()
        if not merge_methods.empty:
            method_counts = Counter(merge_methods)
            best_method = min(method_counts, key=lambda m: METHOD_PRIORITY.get(m, 99))
        else:
            best_method = None

        rdds = grp["RDD"].dropna().unique()
        etas = grp["BD_Tracker_ETA"].dropna().unique()
        earliest_rdd = min(rdds) if len(rdds) > 0 else None
        latest_rdd = max(rdds) if len(rdds) > 0 else None
        earliest_eta = min(etas) if len(etas) > 0 else None
        latest_eta = max(etas) if len(etas) > 0 else None

        has_missing = (grp["Data_Quality_Severity"] == "Missing_Data").any()
        highest_severity = "Missing_Data" if has_missing else "OK"

        has_rules_not_conf = (grp["Risk_Calculation_Status"] == "Rules not configured").any()
        risk_status = "Rules not configured" if has_rules_not_conf else None

        milestones = grp["Next_Required_Milestone"].dropna().unique()
        milestone_priority = [
            "RDD required", "LC Date", "SI Shared Date", "BD Tracker ETD",
            "OBL/EBL Received Date", "Final Documents Received Date", "Milestones complete"
        ]
        most_urgent = None
        for mp in milestone_priority:
            if mp in milestones:
                most_urgent = mp
                break

        rows.append({
            "Standardised_PO_Number": po,
            "Open_PO_Quantity": open_po_qty,
            "Total_Detail_Rows": len(grp),
            "Material_Count": grp["Standardised_Material_AGI_Stripped"].nunique(),
            "Partial_Shipment_Count": grp["Partial_Shipment_Reference"].notna().sum(),
            "BD_Matched_Rows": grp["Has_BD_Tracker_Match"].sum(),
            "EE_Matched_Rows": grp["Has_Eagle_Eye_Match"].sum(),
            "Merge_Method": best_method,
            "Earliest_RDD": earliest_rdd,
            "Latest_RDD": latest_rdd,
            "Earliest_ETA": earliest_eta,
            "Latest_ETA": latest_eta,
            "Highest_Data_Quality_Severity": highest_severity,
            "Risk_Calculation_Status": risk_status,
            "Next_Required_Milestone": most_urgent,
        })

    return pd.DataFrame(rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Unmatched sheets
# ---------------------------------------------------------------------------

def build_unmatched_bd(bd_df, op_in_scope_pos):
    """BD rows whose PO is not in the in-scope OP set."""
    bd_valid = bd_df[bd_df["Standardised_PO_Number"].notna()].copy()
    unmatched = bd_valid[~bd_valid["Standardised_PO_Number"].isin(op_in_scope_pos)].copy()
    unmatched["Unmatched_Note"] = "PO not found in Open PO scope"
    print(f"  Unmatched BD rows: {len(unmatched)}")
    return unmatched.reset_index(drop=True)


def build_unmatched_ee(ee_df, op_in_scope_pos):
    """EE rows whose PO is not in the in-scope OP set."""
    ee_valid = ee_df[ee_df["Standardised_PO_Number"].notna()].copy()
    unmatched = ee_valid[~ee_valid["Standardised_PO_Number"].isin(op_in_scope_pos)].copy()
    unmatched["Unmatched_Note"] = "PO not found in Open PO scope"
    print(f"  Unmatched EE rows: {len(unmatched)}")
    return unmatched.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Ambiguous matches (partial-shipment expansions)
# ---------------------------------------------------------------------------

def build_ambiguous_matches(master_df):
    """Rows where partial-shipment expansion occurred."""
    ambig = master_df[master_df["Quantity_Distribution_Note"].notna()].copy()
    if ambig.empty:
        return pd.DataFrame()
    result = ambig.groupby(["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"]).agg(
        BD_Match_Count=("Partial_Shipment_Reference", "count"),
        Quantity_Distribution_Note=("Quantity_Distribution_Note", "first"),
        Open_Quantity=("Open_Quantity", "first"),
    ).reset_index()
    result["Open_Quantity"] = None  # Null because distributed
    print(f"  Ambiguous match groups: {len(result)}")
    return result


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def build_run_log(run_id, run_ts, op_source, bd_source, ee_source,
                   op_rows_in, bd_rows_in, ee_rows_in,
                   master_detail_rows, po_summary_rows,
                   bd_matched, ee_matched, three_way_count,
                   bd_unmatched_cnt, ee_unmatched_cnt,
                   dq_exception_cnt, threshold_cfg=None):
    cfg_version = None
    rules_active = None
    if threshold_cfg is not None:
        cfg_version = threshold_cfg.get("config_version")
        rules_active = threshold_cfg.get("rules_active")

    return pd.DataFrame([{
        "Run_ID": run_id,
        "Run_Timestamp": run_ts,
        "Open_PO_Source": op_source,
        "BD_Tracker_Source": bd_source,
        "Eagle_Eye_Source": ee_source,
        "OP_Rows_In": op_rows_in,
        "BD_Rows_In": bd_rows_in,
        "EE_Rows_In": ee_rows_in,
        "Master_Detail_Rows": master_detail_rows,
        "PO_Summary_Rows": po_summary_rows,
        "BD_Matched": bd_matched,
        "EE_Matched": ee_matched,
        "Three_Way_POs": three_way_count,
        "BD_Unmatched_Count": bd_unmatched_cnt,
        "EE_Unmatched_Count": ee_unmatched_cnt,
        "DQ_Exception_Count": dq_exception_cnt,
        "Threshold_Config_Version": cfg_version,
        "Rules_Active": rules_active,
    }])


# ---------------------------------------------------------------------------
# Multi-sheet export
# ---------------------------------------------------------------------------

def export_master(master_df, po_summary_df, unmatched_bd_df, unmatched_ee_df,
                  ambiguous_df, dq_exceptions_df, run_log_df, output_path):
    """Write a multi-sheet workbook with all report sheets."""
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        master_df.to_excel(writer, sheet_name="Master_Detail", index=False)
        if not po_summary_df.empty:
            po_summary_df.to_excel(writer, sheet_name="PO_Summary", index=False)
        if not unmatched_bd_df.empty:
            unmatched_bd_df.to_excel(writer, sheet_name="Unmatched_BD", index=False)
        if not unmatched_ee_df.empty:
            unmatched_ee_df.to_excel(writer, sheet_name="Unmatched_EE", index=False)
        if not ambiguous_df.empty:
            ambiguous_df.to_excel(writer, sheet_name="Ambiguous_Matches", index=False)
        if not dq_exceptions_df.empty:
            dq_exceptions_df.to_excel(writer, sheet_name="DQ_Exceptions", index=False)
        run_log_df.to_excel(writer, sheet_name="Run_Log", index=False)
    print(f"\nMulti-sheet workbook written: {output_path}")
    print(f"  Sheets: Master_Detail ({len(master_df)}), PO_Summary ({len(po_summary_df)}), "
          f"Unmatched_BD ({len(unmatched_bd_df)}), Unmatched_EE ({len(unmatched_ee_df)}), "
          f"Ambiguous_Matches ({len(ambiguous_df)}), DQ_Exceptions ({len(dq_exceptions_df)}), "
          f"Run_Log ({len(run_log_df)})")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def merge_import_master(open_po_path, bd_tracker_path, eagle_eye_path, output_path,
                        open_po_sheet=0, bd_tracker_sheet=0, eagle_eye_sheet=0,
                        raw_op_rows=None, raw_bd_rows=None, raw_ee_rows=None):
    print("=" * 60)
    print("IMPORT MASTER DATA MERGE")
    print("=" * 60)

    # Load threshold config
    print("\nLoading threshold configuration...")
    threshold_cfg = load_threshold_config()

    # 1. Read
    print("\nReading clean sources...")
    op_df = read_clean_source(open_po_path, open_po_sheet)
    bd_df = read_clean_source(bd_tracker_path, bd_tracker_sheet)
    ee_df = read_clean_source(eagle_eye_path, eagle_eye_sheet)

    op_source_rows = len(op_df) if raw_op_rows is None else raw_op_rows
    bd_source_rows = len(bd_df) if raw_bd_rows is None else raw_bd_rows
    ee_source_rows = len(ee_df) if raw_ee_rows is None else raw_ee_rows

    # Validate required columns
    for po_col in ["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"]:
        if po_col not in op_df.columns:
            print(f"ERROR: Open PO missing column '{po_col}'", file=sys.stderr)
            sys.exit(1)

    for name, df, req_cols in [
        ("BD Tracker", bd_df, ["Standardised_PO_Number", "Standardised_Material_AGI_Stripped"]),
        ("Eagle Eye", ee_df, ["Standardised_PO_Number", "Standardised_Material_AGI_Stripped",
                               "Container_Number", "Eagle_Eye_Status", "Eagle_Eye_ETA"]),
    ]:
        missing = [c for c in req_cols if c not in df.columns]
        if missing:
            print(f"ERROR: {name} missing columns: {missing}", file=sys.stderr)
            sys.exit(1)

    # 2. Normalise merge keys
    print("\nNormalising merge keys...")
    op_df = normalise_merge_keys(op_df)
    bd_df = normalise_merge_keys(bd_df)
    ee_df = normalise_merge_keys(ee_df)

    # Filter in-scope Open PO
    if "Scope_Status" in op_df.columns:
        in_scope_mask = op_df["Scope_Status"].astype(str).str.strip() == "In_Scope_Import"
        op_in_scope = op_df[in_scope_mask].copy()
        op_excluded = op_df[~in_scope_mask].copy()
        op_excluded_count = len(op_excluded)
        print(f"  Open PO in-scope: {len(op_in_scope)} of {len(op_df)} rows (excluded: {op_excluded_count})")
    else:
        op_in_scope = op_df.copy()
        op_excluded_count = 0

    # Ghost/excluded counts based on raw vs cleaned difference
    # (raw counts passed from orchestrator, or use what we have)
    if raw_op_rows is not None and op_excluded_count == 0:
        op_excluded_count = max(0, raw_op_rows - len(op_in_scope))

    # Ghost rows = raw source minus cleaned rows
    bd_ghost_count = max(0, bd_source_rows - len(bd_df))
    ee_blank_count = max(0, ee_source_rows - len(ee_df))

    # 3. Aggregate Eagle Eye
    print("\nAggregating Eagle Eye to (PO, AGI) level...")
    ee_agg = aggregate_eagle_eye(ee_df)

    # 4. Merge BD Tracker
    print("\nMerging BD Tracker...")
    merged = merge_bd_tracker(op_in_scope, bd_df)

    # 5. PO-only fallback
    print("\nApplying PO-only fallback...")
    merged = apply_po_fallback(merged, bd_df)

    # 6. Merge aggregated Eagle Eye
    print("\nMerging Eagle Eye...")
    merged = merge_eagle_eye(merged, ee_agg)

    # 7. Set merge method labels
    merged = set_merge_method(merged)

    # 8. Computed columns — milestones, risk, data quality
    print("\nComputing milestones and risk axes...")
    merged = compute_milestones_and_risk(merged)

    # 9. Source_Filename and Run_Timestamp are carried through from the clean sources
    # If somehow dropped, repopulate from the first available source
    if "Source_Filename" not in merged.columns:
        for src in [op_in_scope, bd_df, ee_df]:
            if "Source_Filename" in src.columns and len(src) > 0:
                merged["Source_Filename"] = src["Source_Filename"].iloc[0]
                break
    if "Run_Timestamp" not in merged.columns:
        for src in [op_in_scope, bd_df, ee_df]:
            if "Run_Timestamp" in src.columns and len(src) > 0:
                merged["Run_Timestamp"] = src["Run_Timestamp"].iloc[0]
                break

    # 10. Finalise output — this will add missing columns as None
    master = finalise_output(merged)

    # 11. Build PO Summary
    print("\nBuilding PO summary...")
    po_summary_df = build_po_summary(master)

    # 12. Build unmatched sheets
    op_in_scope_pos = set(op_in_scope["Standardised_PO_Number"].dropna().unique())
    print("\nBuilding unmatched sheets...")
    unmatched_bd_df = build_unmatched_bd(bd_df, op_in_scope_pos)
    unmatched_ee_df = build_unmatched_ee(ee_df, op_in_scope_pos)

    # 13. Build ambiguous matches
    print("\nBuilding ambiguous matches...")
    ambiguous_df = build_ambiguous_matches(master)

    # 14. Build DQ exceptions
    dq_exceptions_df = master[master["Data_Quality_Severity"] != "OK"].copy()

    # 15. Build Run Log
    three_way = set(op_in_scope["Standardised_PO_Number"].dropna().unique()) & \
                set(bd_df["Standardised_PO_Number"].dropna().unique()) & \
                set(ee_df["Standardised_PO_Number"].dropna().unique())
    run_ts = datetime.now().isoformat()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    op_source_file = os.path.basename(open_po_path)
    bd_source_file = os.path.basename(bd_tracker_path)
    ee_source_file = os.path.basename(eagle_eye_path)
    run_log_df = build_run_log(
        run_id=run_id, run_ts=run_ts,
        op_source=op_source_file, bd_source=bd_source_file, ee_source=ee_source_file,
        op_rows_in=op_source_rows, bd_rows_in=bd_source_rows, ee_rows_in=ee_source_rows,
        master_detail_rows=len(master), po_summary_rows=len(po_summary_df),
        bd_matched=int(master["Has_BD_Tracker_Match"].sum()),
        ee_matched=int(master["Has_Eagle_Eye_Match"].sum()),
        three_way_count=len(three_way),
        bd_unmatched_cnt=len(unmatched_bd_df),
        ee_unmatched_cnt=len(unmatched_ee_df),
        dq_exception_cnt=len(dq_exceptions_df),
        threshold_cfg=threshold_cfg,
    )

    # 16. Export multi-sheet workbook
    print("\nExporting multi-sheet workbook...")
    export_master(master, po_summary_df, unmatched_bd_df, unmatched_ee_df,
                  ambiguous_df, dq_exceptions_df, run_log_df, output_path)

    # 17. Reconciliation
    print_reconciliation(op_in_scope, bd_df, ee_df, master, po_summary_df,
                         unmatched_bd_df, unmatched_ee_df, dq_exceptions_df,
                         op_excluded_count, bd_ghost_count, ee_blank_count,
                         op_source_rows, bd_source_rows, ee_source_rows)

    return {
        "master_detail": master,
        "po_summary": po_summary_df,
        "unmatched_bd": unmatched_bd_df,
        "unmatched_ee": unmatched_ee_df,
        "ambiguous_matches": ambiguous_df,
        "dq_exceptions": dq_exceptions_df,
        "run_log": run_log_df,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import Master Data Merge — merge cleaned Open PO, BD Tracker, and Eagle Eye."
    )
    parser.add_argument("--open-po", required=True, help="Path to cleaned Open PO Excel file")
    parser.add_argument("--bd-tracker", required=True, help="Path to cleaned BD Tracker Excel file")
    parser.add_argument("--eagle-eye", required=True, help="Path to cleaned Eagle Eye Excel file")
    parser.add_argument("--output", default="import_master_data.xlsx", help="Output path")
    parser.add_argument("--open-po-sheet", default=None)
    parser.add_argument("--bd-tracker-sheet", default=None)
    parser.add_argument("--eagle-eye-sheet", default=None)
    args = parser.parse_args()

    merge_import_master(
        open_po_path=args.open_po,
        bd_tracker_path=args.bd_tracker,
        eagle_eye_path=args.eagle_eye,
        output_path=args.output,
        open_po_sheet=args.open_po_sheet if args.open_po_sheet else 0,
        bd_tracker_sheet=args.bd_tracker_sheet if args.bd_tracker_sheet else 0,
        eagle_eye_sheet=args.eagle_eye_sheet if args.eagle_eye_sheet else 0,
    )


if __name__ == "__main__":
    main()
