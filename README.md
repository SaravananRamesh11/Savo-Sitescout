# Savo SiteScout: Chennai expansion intelligence (Milestone 1: Area Intelligence)

A BD Manager picks any part of Chennai (locality name, pincode, or grid cells on a map), runs a **virtual
analysis**, and gets a saved, timestamped **Area Fitness Report**: what the area is like, a 0-100 fit rating,
the reasoning behind it, and the 5 places to scout first. Built mobile-first (360 px and up) in the Savomart
brand colours (`#782B90` purple, `#FFF200` yellow).

> Status: **Milestone 1 complete.** M2 (property scouting) and M3 (catchment surveys) are planned separately.
> The `areas` table is the anchor M2/M3 will point to.

## Run it locally

Prerequisites: Python 3.12+ (3.14 works, binary wheels only), Node 20+, a PostGIS-enabled Postgres.

```bash
cp .env.example .env            # fill db_url (and SAVOMART_CRON_TOKEN); see comments inside

# backend
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Windows; use .venv/bin on macOS/Linux
python scripts/init_db.py                  # PostGIS + tables (also runs at API startup)
python scripts/ingest_pincodes.py          # ~2.5 min, rebuilds data/chennai_pincodes.geojson (already committed)
python -m uvicorn app.main:app --port 8000
python -m pytest -q                        # 22 tests

# frontend (new terminal)
cd frontend && npm install && npm run dev  # http://localhost:5173 (proxies /api to :8000)
```

Open the app on a phone on the same Wi-Fi via the Vite "Network" URL.

**Roles:** a role switcher (top-right) has the four personas: BD Manager, BD Executive, Survey Manager, Survey
Executive. No login. In M1 only the **BD Manager** screens are built; the other three show what they will do in
M2/M3.

## Using it (demo path)
1. **Analyse** tab: choose *Locality* ("Velachery"), *Pincode* (600042), or *Map cells* (zoom in, tap cells that
   touch edge to edge, max 100). Savomart stores appear as yellow pins.
2. **Run virtual analysis.** A step-by-step progress screen shows each of the 9 pipeline stages. If one fails it
   shows which stage and why, with a retry that reuses already-fetched data.
3. The report: score + rating, "Why this rating", 5 hotspots (locality, nearest named road, coordinates, why),
   an expandable score breakdown, area facts, and the exact data sources with timestamps.
4. **Reports** tab: all saved reports; tick 2-4 to compare side by side.

## Architecture

```
React (Vite, Leaflet)  --/api-->  FastAPI  --BackgroundTask-->  LangGraph (9 sequential nodes)
                                     |                              |
                                 PostGIS  <-------------------------+
                                     ^        Overpass (OSM) | Nominatim | Savomart Stores API | LLM (optional)
```

Agent nodes (`backend/app/agent/nodes/pipeline.py`), each persisting progress to the report row:
`resolve_area -> get_osm_data -> get_demographics -> get_savomart -> calculate_features -> calculate_score ->
identify_hotspots -> generate_report -> save_report`.

* OSM is fetched with **one bulk Overpass query per area bounding box** (never per cell). Grid binning and
  distance maths then happen locally with shapely/STRtree in **UTM zone 44N metres** (never raw degrees).
* Generation is a background task; the UI polls `/reports/{id}/status` which returns per-step state,
  `failed_node` and `error`.
* Every external node degrades gracefully: fresh cache -> live -> stale cache -> **clearly labelled mock**, and
  records a `data_quality_flag` shown in the UI.

## Data model (PostGIS)
| table | purpose |
|---|---|
| `areas` | resolved area identity (type, input, polygon, km², boundary quality). Caches geocoding and is what M2/M3 will foreign-key to |
| `area_reports` | one row per analysis run: status, steps, score, rating, breakdown, profile, flags, data sources + timestamps, explanation |
| `grid_cells` | per-report 500 m cells: polygon, centroid, score, breakdown, features, hotspot rank, locality, nearest road |
| `savomart_stores` | normalised snapshot of the Stores API (reproducible reports, PostGIS distance queries, survives API outages) |
| `external_data_cache` | exactly one row per (area, source): last successful Overpass / population payload with expiry |

A report is a fresh snapshot every time (revisitable and comparable); only the resolved geometry is reused.

## How the score works (and why you can trust it)
Scoring is **deterministic backend code** (`services/scoring.py`, pure functions, unit-tested), never the LLM.
Weights sum to 100; each factor is `clip(raw / cap)` (or an inverse / distance ramp) times its weight.

| Group | Points | Signals (full-points value) |
|---|---|---|
| Residential demand | 35 | population density 15 (30,000/km²), household density 15 (7,500/km²), growth 5 (4 %/yr) |
| Accessibility | 20 | road density 8 (20 km/km²), major-road proximity 7 (≤200 m full, ≥2 km zero), transit stops within 500 m 5 (4) |
| Commercial activity | 10 | shops + offices + banks + markets per km² (500) |
| Amenity activity | 10 | weighted: school 1, college 1, hospital 2, clinic 0.5 per km² (25) |
| Competition opportunity | 15 | inverse: supermarkets + convenience stores within 1 km (0 pts at 15+) |
| Savomart opportunity | 10 | distance to nearest operational Savomart (<500 m = cannibalisation = 0; ≥3 km = full) |

Each cell is scored; the area score uses the same formula on coverage-weighted averages. The constants live in
`backend/app/core/scoring_constants.py` and are **initial product assumptions to calibrate**, not measured truths.
The UI shows every factor's raw value, cap and points. Ratings: ≥75 Excellent, ≥60 Good, ≥45 Moderate, else Weak.

**Hotspots:** the top cells by score, preferring non-adjacent cells so suggestions are spread out, ignoring
cells <40 % inside the area. Locality = nearest OSM `place` node; nearest named road comes from road geometry
already downloaded (no reverse-geocoding calls).

## Where AI is used, and how it is kept honest
The LLM is used **only** in `generate_report`, and only to phrase an explanation of numbers the backend already
computed:
1. The prompt contains a `FACTS` JSON of verified values and strict rules (no outside knowledge, no new numbers).
2. The model must return structured JSON referencing real factor keys and real hotspot cell ids.
3. **Grounding check** (`services/grounding.py`): every number in the text must appear in FACTS (allowing
   rounding and m<->km). Unknown factor/cell ids are rejected too.
4. One retry with the rejection reason, then a deterministic **template explanation** built from the same facts.
   The report records which one was used (`llm` or `template`), visible in the UI.

Provider is config-only (`LLM_PROVIDER=anthropic|openai|none`, any OpenAI-compatible base URL for Groq, Gemini,
Ollama). **No key is configured in this repo**, so the shipped demo runs on the template path; the LLM path
is covered by unit tests using a stubbed client.

## Data sources
* **OpenStreetMap** via the **Overpass API**: shops, offices, schools, colleges, hospitals, clinics, banks,
  markets, transit stops, roads, place names. © OpenStreetMap contributors, ODbL.
* **Nominatim** (locality -> boundary polygon; 1 request/s, custom User-Agent, results cached in `areas`).
* **Savomart Stores API** (operational stores). The token is read from `.env`. Note: the endpoint answers
  `GET`; the brief's `curl --data ''` makes it a `POST`, which returns 405.
* **Pincode boundaries:** the OGD boundary file needs a manual download, so `scripts/ingest_pincodes.py` asks
  Nominatim for each 6000xx pincode centroid and builds a **Voronoi partition** clipped to Chennai. These are
  **approximate boundaries** and are labelled as such. Drop in the real OGD GeoJSON (with a `pincode` property)
  at `backend/data/chennai_pincodes.geojson` to upgrade.
* **Demographics (MOCK / ESTIMATED):** ward-level Census data isn't available through a clean API, so
  `backend/app/mock_data/demographics_chennai.json` holds hand-calibrated locality estimates (Census-2011 order of
  magnitude), blended by inverse-distance weighting. Every report is flagged `population_mocked` and the UI says
  "estimate, not official". Growth rates are illustrative.
* Leaflet + react-leaflet, shapely, pyproj, FastAPI, SQLAlchemy/GeoAlchemy2, LangGraph, Vite/React (see the
  dependency files). Map tiles © OpenStreetMap contributors.
* `osm-mcp-server` was evaluated but not used in the scoring path (Overpass is called directly); scoring must not
  depend on a stdio subprocess.

## Design decisions and trade-offs
* **Deterministic scoring + LLM for wording only:** the rating must be defensible in front of a manager.
* **One Overpass query per area, local maths:** avoids rate limits and makes per-cell scoring cheap.
* **500 m fixed UTM lattice:** cell ids (`col_row`) are stable across reports, so M3 can split and reuse
  survey work per cell without overlap.
* **Postgres over an in-process store:** PostGIS geometry columns and spatial indexes suit the location workload
  and let M2/M3 do "is this property inside an already-studied area" queries.
* **Reports are snapshots, geometry is cached:** meets "revisit and compare" without stale scores.
* **Graceful degradation with visible labels** rather than failing a live demo.
* **Remote database latency:** the managed DB adds ~0.3 s per query, so the pool is pre-warmed and list/detail
  queries use eager loading.

## Known issues / what I'd do with more time
* Demographics are estimates; a real ward-level Census join (and real growth rates) is the biggest quality gap.
* Pincode polygons are Voronoi approximations, not surveyed boundaries.
* Scoring caps are assumptions; they should be calibrated against the performance of existing Savomart stores.
* Rent, footfall and traffic aren't in M1 (no public data).
* Hotspots are shown as a text list plus numbered pins on the report map, not a heat-map overlay.
* No authentication (role switcher only, per the brief). No offline mode yet.
* Overpass mirrors are occasionally slow; there is one retry and a labelled fallback, but a cold first analysis of
  a large area can take a minute.
* The `datetime`-based startup hook uses FastAPI's deprecated `on_event`; switch to lifespan.

## AI tools used
Built with **Claude Code (Claude Sonnet 5.5)** for planning, implementation, tests and UI verification with
headless-browser screenshots at phone and desktop widths. Chat exports go in `/ai-sessions` (see the note there).

## Demo video
_Add the Google Drive link here (3-5 minutes, "Anyone with the link can view")._
