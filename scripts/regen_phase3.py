"""Regenerate the real Import Visibility Master deliverable (Phase 3) from
the byte-identical raw copies, mirroring clean_merge.main() without dialogs.

The Country Thresholds file is optional (system argument or known path); when
it is missing the built-in defaults are used.
"""
import datetime
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rule_engine
import clean_open_po
import clean_bd_tracker
import clean_eagle_eye
import clean_merge

TMP = Path(os.environ.get("TEMP", r"C:\Users\s1394428\AppData\Local\Temp"))
OPCODE = TMP / "opencode"
BASE = Path(r"C:\Users\s1394428\OneDrive - Syngenta\Projects\Planning Team Product Build V3")
DST = BASE / "excel files" / "Import Visibility Master_merged.xlsx"
THRESHOLDS = BASE / "excel files" / "Country Thresholds.xlsx"

SRC1 = OPCODE / "openpo_input.xlsx"
SRC2 = OPCODE / "bdtracker_input.xlsx"
SRC3 = OPCODE / "eagleeye_input.xlsx"

thresholds = None
if len(sys.argv) > 1:
    THRESHOLDS = Path(sys.argv[1])
if THRESHOLDS.exists():
    thresholds = rule_engine.load_country_thresholds(THRESHOLDS)
    print("Country thresholds loaded from: %s" % THRESHOLDS)
else:
    print("Country thresholds not found at %s - using built-in defaults."
          % THRESHOLDS)

print("Cleaning Open PO ...")
_, openpo_rows, op_info = clean_open_po.clean_to_rows(SRC1)
print("Cleaning BD Tracker ...")
_, bd_rows, bd_info = clean_bd_tracker.clean_to_rows(SRC2)
print("Cleaning Eagle Eye ...")
_, ee_rows, ee_info = clean_eagle_eye.clean_to_rows(SRC3)

merged, summary = clean_merge.merge_rows(openpo_rows, bd_rows, ee_rows,
                                         op_info, bd_info, ee_info,
                                         thresholds=thresholds)
control = clean_merge.build_control_sheets(op_info, bd_info, ee_info,
                                           openpo_rows, bd_rows, ee_rows,
                                           [SRC1, SRC2, SRC3])
refresh = datetime.datetime.now().replace(microsecond=0)
threshold_src = (str(THRESHOLDS) if thresholds is not None
                 else "built-in defaults")
sheets = {"Import Visibility Master": (clean_merge.MERGE_COLUMNS, merged,
                                       clean_merge.MERGE_DATE_COLS)}
sheets["Release"] = clean_merge.build_release_sheet(summary, refresh,
                                                   thresholds, threshold_src)
sheets["Reconciliation"] = clean_merge.build_reconciliation(merged, summary)
sheets.update(control)
clean_merge.write_workbook(DST, sheets)

print("=" * 60)
print("Import Visibility Master - merge summary (Phase 3)")
print("=" * 60)
print("Master rows      : %d (%d POs)" % (summary["rows"], summary["po_count"]))
print("Not in BD        : %d   Not in EE: %d   Neither: %d"
      % (summary["not_bd"], summary["not_ee"], summary["not_both"]))
sev = summary["severity"]
print("Urgency (Active) : Critical %d | Urgent %d | Monitor %d | Data Review %d"
      % (sev.get("Critical", 0), sev.get("Urgent", 0),
         sev.get("Monitor", 0), sev.get("Data Review", 0)))
pop = summary.get("population", {})
print("Population       : Active %d | Quantity Review %d"
      % (pop.get("Active", 0), pop.get("Quantity Review", 0)))
print("Output           : %s" % DST)
print("=" * 60)
