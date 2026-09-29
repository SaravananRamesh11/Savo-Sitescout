"""DEV/TEST helper: move a property through the catchment stages that Milestone 3 will drive automatically.

Milestone 3 (survey operations) is not built yet, so nothing in the app can move a property past
CATCHMENT_REQUESTED. This script does it as the 'system' actor, through the same state machine and history table
the app uses (nothing is faked in the evaluation: the final-review evaluation is a real re-run, and no catchment
data is invented).

    python scripts/advance_property.py 21                 # CATCHMENT_REQUESTED -> ... -> FINAL_REVIEW
    python scripts/advance_property.py 21 CATCHMENT_COMPLETED
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.core.db import get_session
from app.models.property_models import Property
from app.services import pipeline, property_eval

PATH = ["CATCHMENT_IN_PROGRESS", "CATCHMENT_COMPLETED", "FINAL_REVIEW"]
pid = int(sys.argv[1])
target = sys.argv[2] if len(sys.argv) > 2 else "FINAL_REVIEW"
if target not in PATH:
    sys.exit(f"target must be one of {PATH}")

db = get_session()
p = db.get(Property, pid)
if p is None:
    sys.exit("property not found")
print("start:", p.pipeline_stage)
for stage in PATH:
    if p.pipeline_stage == target:
        break
    if p.pipeline_stage in PATH and PATH.index(stage) <= PATH.index(p.pipeline_stage):
        continue
    if stage not in pipeline.TRANSITIONS.get(p.pipeline_stage, {}):
        sys.exit(f"cannot go from {p.pipeline_stage} to {stage}")
    pipeline.apply_transition(db, p, stage, "system:m3-stub", reason="Advanced by dev script (M3 not built yet)")
    if stage == "FINAL_REVIEW":
        ev = property_eval.new_evaluation_row(db, p.id, "catchment_completed", "system:m3-stub")
        db.commit()
        property_eval.run_evaluation(ev.id)
    db.commit()
    print("->", stage)
print("now:", p.pipeline_stage)
