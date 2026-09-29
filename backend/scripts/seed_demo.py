"""Demo data: (optionally) clears the M2 and M3 tables, then creates scouting assignments from the latest completed M1
reports' top hotspots (one for Ravi, one for Divya). Safe to re-run.

    python scripts/seed_demo.py            # add assignments only if none exist
    python scripts/seed_demo.py --reset    # wipe M2 + M3 tables (properties, photos, evaluations, studies, survey data) first
"""
import shutil
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from app.core.db import get_session
from app.models.db_models import AreaReport
from app.models.property_models import ScoutingAssignment
from app.services import storage

db = get_session()
if "--reset" in sys.argv:
    # M3 first: catchment studies reference properties/areas with ON DELETE RESTRICT
    for t in ("catchment_insights", "survey_capture_photos", "survey_captures", "survey_work_units"):
        db.execute(text(f"delete from {t}"))
    db.execute(text("delete from catchment_studies where reused_from_study_id is not null"))
    db.execute(text("delete from catchment_studies"))
    for t in ("property_evaluations", "property_status_history", "property_photos", "property_field_competitors",
              "properties", "scouting_assignments"):
        db.execute(text(f"delete from {t}"))
    db.commit()
    shutil.rmtree(storage.LOCAL_DIR, ignore_errors=True)
    print("M2 and M3 tables cleared (M1 data untouched; photos already in R2 are not deleted)")

if db.query(ScoutingAssignment).count() == 0:
    reports = (db.query(AreaReport).filter(AreaReport.status == "completed")
               .order_by(AreaReport.created_at.desc()).all())
    seen, made = set(), 0
    for r in reports:
        if r.area_id in seen or not (r.area_profile or {}).get("hotspots"):
            continue
        seen.add(r.area_id)
        h = r.area_profile["hotspots"][0]
        who = "bd_executive:ravi" if made % 2 == 0 else "bd_executive:divya"
        db.add(ScoutingAssignment(
            area_id=r.area_id, source_report_id=r.id, hotspot_cell_id=h["cell_id"], hotspot_lat=h["lat"],
            hotspot_lon=h["lon"], hotspot_label=f"{h['locality']} (hotspot #{h['rank']})", executive_id=who,
            assigned_by="bd_manager:asha", notes="Look for corner units with 1,500+ sq ft and good road frontage.",
            status="OPEN"))
        made += 1
        if made == 2:
            break
    db.commit()
    print(f"created {made} demo assignment(s) from M1 hotspots")
else:
    print("assignments already exist; nothing added")
