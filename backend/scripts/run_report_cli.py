"""Dev helper: resolve an area and run the whole agent synchronously, printing the result."""
import json, sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.core.db import get_session
from app.models.db_models import Area, AreaReport
from app.agent.graph import run_report, initial_steps
from app.services import geocoding
from geoalchemy2.shape import from_shape

kind, value = sys.argv[1], sys.argv[2]
db = get_session()
r = geocoding.resolve_pincode(value) if kind == "pincode" else geocoding.resolve_name(value)
km2 = geocoding.validate_size(r["polygon"])
a = db.query(Area).filter_by(input_type=kind, raw_input=value.lower()).first()
if not a:
    a = Area(input_type=kind, raw_input=value.lower(), resolved_name=r["name"], area_km2=km2,
             geometry=from_shape(r["polygon"], srid=4326), boundary_quality=r["quality"]); db.add(a); db.commit()
rep = AreaReport(area_id=a.id, status="queued", steps=initial_steps()); db.add(rep); db.commit()
t = time.time(); run_report(rep.id); db.expire_all(); rep = db.get(AreaReport, rep.id)
print("status", rep.status, "in", round(time.time() - t, 1), "s", "| failed:", rep.failed_node, rep.error)
for s in rep.steps: print(f"  {s['status']:8} {s['node']:20} {s['note'][:110]}")
print("score", rep.overall_score, rep.rating, "| flags", rep.data_quality_flags)
if rep.status == "completed":
    print(json.dumps(rep.llm_explanation, indent=1)[:1500])
    for h in rep.area_profile["hotspots"]: print(h["rank"], h["cell_id"], h["score"], h["locality"], "|", h["nearest_named_road"])
    print({k: v for k, v in rep.area_profile.items() if k not in ("hotspots", "facts")})
