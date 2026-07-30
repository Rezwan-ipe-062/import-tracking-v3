"""
pipeline_db.py — Database schema and connection management for the Import Tracker pipeline.

Phase 5: Full threshold profile management with versioning, approval workflow,
and audit trail.
"""

import os
import sqlite3


def get_default_db_path():
    data_dir = os.environ.get("IMPORT_TRACKER_DATA_DIR")
    if data_dir:
        return os.path.join(data_dir, "import_tracker.db")
    return os.path.join(os.path.dirname(__file__), "..", "import_tracker.db")


def get_connection(db_path=None):
    if db_path is None:
        db_path = get_default_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_database(conn=None):
    if conn is None:
        conn = get_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS upload_runs (
            run_id TEXT PRIMARY KEY,
            run_status TEXT NOT NULL DEFAULT 'Uploaded',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            pipeline_version TEXT NOT NULL,
            threshold_config_version TEXT,
            threshold_profile_id INTEGER,
            threshold_profile_version INTEGER,
            rejected_duplicate INTEGER DEFAULT 0,
            duplicate_of_run_id TEXT,
            run_notes TEXT
        );

        CREATE TABLE IF NOT EXISTS source_file_uploads (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_type TEXT NOT NULL CHECK(source_type IN ('open_po','bd_tracker','eagle_eye')),
            original_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            upload_timestamp TEXT NOT NULL,
            raw_row_count INTEGER,
            valid_row_count INTEGER,
            excluded_row_count INTEGER,
            ghost_row_count INTEGER,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS master_detail_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Standardised_PO_Number TEXT,
            Standardised_Material_AGI TEXT,
            Standardised_Material_AGI_Stripped TEXT,
            Partial_Shipment_Reference TEXT,
            Product_Name TEXT,
            Supplier_Plant_ID TEXT,
            Supplier_Plant_Name TEXT,
            Open_Quantity TEXT,
            Unit_of_Measure TEXT,
            PO_Line_Count TEXT,
            PO_Line_Status TEXT,
            Scope_Status TEXT,
            Quantity_Quality_Flag TEXT,
            Open_PO_Data_Quality_Flag TEXT,
            Overall_Import_Status TEXT,
            BD_Tracker_Data_Quality_Flag TEXT,
            LC_Date TEXT,
            SI_Shared_Date TEXT,
            RDD TEXT,
            BD_Tracker_ETD TEXT,
            BD_Tracker_ETA TEXT,
            OBL_EBL_Received_Date TEXT,
            Final_Documents_Received_Date TEXT,
            Origin_Code TEXT,
            Container_Count TEXT,
            Container_Numbers TEXT,
            Earliest_EE_ETA TEXT,
            Latest_EE_ETA TEXT,
            Eagle_Eye_Data_Quality_Flag TEXT,
            Has_BD_Tracker_Match INTEGER,
            Has_Eagle_Eye_Match INTEGER,
            Merge_Method TEXT,
            Days_Remaining_to_RDD TEXT,
            Overall_Risk_Category TEXT,
            Risk_Calculation_Status TEXT,
            Data_Quality_Severity TEXT,
            Data_Quality_Reasons TEXT,
            Next_Required_Milestone TEXT,
            As_Of_Date TEXT,
            Source_Filename TEXT,
            Run_Timestamp TEXT,
            Quantity_Distribution_Note TEXT,
            Source_Row_ID TEXT,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS po_summary_records (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Standardised_PO_Number TEXT,
            Open_PO_Quantity TEXT,
            Total_Detail_Rows TEXT,
            Material_Count TEXT,
            Partial_Shipment_Count TEXT,
            BD_Matched_Rows TEXT,
            EE_Matched_Rows TEXT,
            Merge_Method_Summary TEXT,
            Earliest_RDD TEXT,
            Latest_RDD TEXT,
            Earliest_ETA TEXT,
            Latest_ETA TEXT,
            Highest_Data_Quality_Severity TEXT,
            Risk_Calculation_Status TEXT,
            Next_Required_Milestone TEXT,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS unmatched_bd_records (
            unmatched_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Source_Row_ID TEXT,
            Standardised_PO_Number TEXT,
            Standardised_Material_AGI_Stripped TEXT,
            Partial_Shipment_Reference TEXT,
            Overall_Import_Status TEXT,
            LC_Date TEXT,
            SI_Shared_Date TEXT,
            RDD TEXT,
            BD_Tracker_ETD TEXT,
            BD_Tracker_ETA TEXT,
            OBL_EBL_Received_Date TEXT,
            Final_Documents_Received_Date TEXT,
            Note TEXT,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS unmatched_ee_records (
            unmatched_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Source_Row_ID TEXT,
            Origin_Code TEXT,
            Standardised_PO_Number TEXT,
            Standardised_Material_AGI_Stripped TEXT,
            Container_Number TEXT,
            Eagle_Eye_Status TEXT,
            Eagle_Eye_ETA TEXT,
            Note TEXT,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS ambiguous_match_records (
            ambiguous_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Standardised_PO_Number TEXT,
            Standardised_Material_AGI_Stripped TEXT,
            Number_Of_BD_Matches INTEGER,
            BD_Source_Row_IDs TEXT,
            Quantity_Distribution_Note TEXT,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS data_quality_exceptions (
            exception_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            Standardised_PO_Number TEXT,
            Standardised_Material_AGI_Stripped TEXT,
            Data_Quality_Severity TEXT,
            Data_Quality_Reasons TEXT,
            Merge_Method TEXT,
            Has_BD_Tracker_Match INTEGER,
            Has_Eagle_Eye_Match INTEGER,
            FOREIGN KEY (run_id) REFERENCES upload_runs(run_id)
        );

        -- Phase 5: Threshold profiles with full lifecycle
        CREATE TABLE IF NOT EXISTS threshold_profiles (
            profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_name TEXT NOT NULL,
            country_code TEXT NOT NULL DEFAULT 'BD',
            description TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Draft'
                CHECK(status IN ('Draft','Pending_Approval','Approved','Active','Inactive','Retired','Expired')),
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'admin',
            approved_by TEXT,
            approved_at TEXT,
            effective_from TEXT,
            effective_to TEXT,
            original_profile_id INTEGER,
            reason_for_change TEXT,
            FOREIGN KEY (original_profile_id) REFERENCES threshold_profiles(profile_id)
        );

        -- Phase 5: Rules within a profile
        CREATE TABLE IF NOT EXISTS threshold_profile_rules (
            rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            milestone_name TEXT NOT NULL,
            reference_date_used TEXT NOT NULL,
            missing_incomplete_condition TEXT,
            watchlist_days INTEGER NOT NULL,
            critical_days INTEGER NOT NULL,
            emergency_days INTEGER NOT NULL,
            action_owner TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES threshold_profiles(profile_id)
        );

        -- Phase 5: Audit trail for all profile changes
        CREATE TABLE IF NOT EXISTS threshold_profile_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            action TEXT NOT NULL,
            old_values TEXT,
            new_values TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            reason TEXT,
            FOREIGN KEY (profile_id) REFERENCES threshold_profiles(profile_id)
        );
    """)
    conn.commit()

    # Phase 5 migration: ensure threshold_profile_version column exists
    try:
        conn.execute("ALTER TABLE upload_runs ADD COLUMN threshold_profile_version INTEGER")
    except Exception:
        pass  # Column already exists

    # Phase 5 migration: remove stale FK referencing old threshold_rule_profiles table
    fks = conn.execute("PRAGMA foreign_key_list(upload_runs)").fetchall()
    has_stale_fk = any(row[2] == "threshold_rule_profiles" for row in fks)
    if has_stale_fk:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript("""
            CREATE TABLE upload_runs_new (
                run_id TEXT PRIMARY KEY,
                run_status TEXT NOT NULL DEFAULT 'Uploaded',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                pipeline_version TEXT NOT NULL,
                threshold_config_version TEXT,
                threshold_profile_id INTEGER,
                threshold_profile_version INTEGER,
                rejected_duplicate INTEGER DEFAULT 0,
                duplicate_of_run_id TEXT,
                run_notes TEXT
            );
            INSERT INTO upload_runs_new SELECT * FROM upload_runs;
            DROP TABLE upload_runs;
            ALTER TABLE upload_runs_new RENAME TO upload_runs;
        """)
        conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


class ColumnMapper:
    """Maps DataFrame column names to database column names for each output table type."""

    TABLES = {
        "master_detail": {
            "table": "master_detail_records",
            "id_column": "record_id",
            "columns": [
                "Standardised_PO_Number", "Standardised_Material_AGI",
                "Standardised_Material_AGI_Stripped", "Partial_Shipment_Reference",
                "Product_Name", "Supplier_Plant_ID", "Supplier_Plant_Name",
                "Open_Quantity", "Unit_of_Measure", "PO_Line_Count", "PO_Line_Status",
                "Scope_Status", "Quantity_Quality_Flag", "Open_PO_Data_Quality_Flag",
                "Overall_Import_Status", "BD_Tracker_Data_Quality_Flag",
                "LC_Date", "SI_Shared_Date", "RDD", "BD_Tracker_ETD", "BD_Tracker_ETA",
                "OBL_EBL_Received_Date", "Final_Documents_Received_Date", "Origin_Code",
                "Container_Count", "Container_Numbers", "Earliest_EE_ETA", "Latest_EE_ETA",
                "Eagle_Eye_Data_Quality_Flag", "Has_BD_Tracker_Match", "Has_Eagle_Eye_Match",
                "Merge_Method", "Days_Remaining_to_RDD", "Overall_Risk_Category",
                "Risk_Calculation_Status", "Data_Quality_Severity", "Data_Quality_Reasons",
                "Next_Required_Milestone", "As_Of_Date", "Source_Filename", "Run_Timestamp",
                "Quantity_Distribution_Note", "Source_Row_ID",
            ],
            "rename_map": {},
        },
        "po_summary": {
            "table": "po_summary_records",
            "id_column": "summary_id",
            "columns": [
                "Standardised_PO_Number", "Open_PO_Quantity", "Total_Detail_Rows",
                "Material_Count", "Partial_Shipment_Count", "BD_Matched_Rows",
                "EE_Matched_Rows", "Earliest_RDD", "Latest_RDD", "Earliest_ETA",
                "Latest_ETA", "Highest_Data_Quality_Severity", "Risk_Calculation_Status",
                "Next_Required_Milestone",
            ],
            "rename_map": {"Merge_Method": "Merge_Method_Summary"},
        },
        "unmatched_bd": {
            "table": "unmatched_bd_records",
            "id_column": "unmatched_id",
            "columns": [
                "Source_Row_ID", "Standardised_PO_Number",
                "Standardised_Material_AGI_Stripped", "Partial_Shipment_Reference",
                "Overall_Import_Status", "LC_Date", "SI_Shared_Date", "RDD",
                "BD_Tracker_ETD", "BD_Tracker_ETA", "OBL_EBL_Received_Date",
                "Final_Documents_Received_Date", "Note",
            ],
            "rename_map": {"Unmatched_Note": "Note"},
        },
        "unmatched_ee": {
            "table": "unmatched_ee_records",
            "id_column": "unmatched_id",
            "columns": [
                "Source_Row_ID", "Origin_Code", "Standardised_PO_Number",
                "Standardised_Material_AGI_Stripped", "Container_Number",
                "Eagle_Eye_Status", "Eagle_Eye_ETA", "Note",
            ],
            "rename_map": {
                "Unmatched_Note": "Note",
                "Eagle_Eye_Status": "Eagle_Eye_Status",
            },
        },
        "ambiguous_matches": {
            "table": "ambiguous_match_records",
            "id_column": "ambiguous_id",
            "columns": [
                "Standardised_PO_Number", "Standardised_Material_AGI_Stripped",
                "Number_Of_BD_Matches", "BD_Source_Row_IDs", "Quantity_Distribution_Note",
            ],
            "rename_map": {
                "BD_Match_Count": "Number_Of_BD_Matches",
                "BD_Source_Row_IDs": "BD_Source_Row_IDs",
            },
        },
        "dq_exceptions": {
            "table": "data_quality_exceptions",
            "id_column": "exception_id",
            "columns": [
                "Standardised_PO_Number", "Standardised_Material_AGI_Stripped",
                "Data_Quality_Severity", "Data_Quality_Reasons", "Merge_Method",
                "Has_BD_Tracker_Match", "Has_Eagle_Eye_Match",
            ],
            "rename_map": {},
        },
    }

    @classmethod
    def get_table_info(cls, table_type):
        return cls.TABLES.get(table_type)

    @classmethod
    def get_table_name(cls, table_type):
        info = cls.TABLES.get(table_type)
        return info["table"] if info else None

    @classmethod
    def get_columns(cls, table_type):
        info = cls.TABLES.get(table_type)
        return info["columns"] if info else []

    @classmethod
    def get_rename_map(cls, table_type):
        info = cls.TABLES.get(table_type)
        return info["rename_map"] if info else {}

    @classmethod
    def get_table_types(cls):
        return list(cls.TABLES.keys())
