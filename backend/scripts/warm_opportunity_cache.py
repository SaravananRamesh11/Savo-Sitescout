"""Pre-fill the Opportunity Finder's tile cache with one city-wide run, so the first 'Find Opportunities' click in the app
is quick. Fetches the Chennai tiles from Overpass one at a time (about 40 tiles; 10-25 minutes the first time, seconds
afterwards) and saves a completed run. Safe to re-run: cached tiles are reused unless --refresh is given.

    python scripts/warm_opportunity_cache.py [--refresh]
"""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.db import get_session  # noqa: E402
from app.models.opportunity_models import OpportunityRun  # noqa: E402
from app.services import opportunity_finder as finder  # noqa: E402

refresh = "--refresh" in sys.argv
db = get_session()
run = finder.create_run(db, "bd_manager:asha")
run_id = run.id
db.close()
started = time.time()
worker = threading.Thread(target=finder.run_finder, args=(run_id, refresh), daemon=True)
worker.start()
last = None
while worker.is_alive():
    time.sleep(10)
    s = get_session()
    p = s.get(OpportunityRun, run_id).progress
    s.close()
    line = f"[{time.time() - started:6.0f}s] {p.get('message')} ({p.get('done')}/{p.get('total')})"
    if line.split("]")[1] != (last or ""):
        print(line, flush=True)
        last = line.split("]")[1]
worker.join()
s = get_session()
r = s.get(OpportunityRun, run_id)
print(f"\nrun {run_id}: {r.status} in {time.time() - started:.0f}s", flush=True)
if r.status == "completed":
    sm = r.summary
    print(f"cells {sm['cells_total']} (ranked {sm['cells_ranked']}, unranked {sm['unranked_by_reason']}); tiles {sm['tiles_total']}: "
          f"live {sm['tiles_live']}, cache {sm['tiles_from_cache']}, stale {sm['tiles_stale']}, missing {sm['tiles_missing']}")
    print("flags:", r.data_quality_flags)
    for t in r.top:
        print(f"  #{t['rank']} {t['locality']:<22} {t['opportunity_score']:5.1f} (M1 {t['m1_score']:.1f}, scouted {t['scouting']['coverage'] * 100:.0f}%)  {t['cell_id']}")
else:
    print("error:", r.error)
s.close()
