# Savo SiteScout: Chennai expansion intelligence (M1 Area Intelligence + M2 Property Scouting)

A BD Manager picks any part of Chennai (locality name, pincode, or grid cells on a map), runs a **virtual
analysis**, and gets a saved, timestamped **Area Fitness Report**: what the area is like, a 0-100 fit rating,
the reasoning behind it, and the 5 places to scout first. Built mobile-first (360 px and up) in the Savomart
brand colours (`#782B90` purple, `#FFF200` yellow).

> Status: **Milestones 1 and 2 complete.** M3 (catchment surveys) is next. See Milestone 2 below.
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
python scripts/seed_demo.py                # demo scouting assignments from M1 hotspots (--reset wipes M2 tables)
python -m pytest -q                        # unit tests; integration tests use the real DB and take minutes

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


---

# Milestone 2: Property scouting and evaluation

Flow: **BD Manager** assigns an M1 hotspot to a **BD Executive** -> the executive captures a property on a phone
(GPS pin, details, photos) -> the system runs a **deterministic, versioned evaluation** -> the manager sees a
30-second review screen and either **rejects** the property or **requests a catchment study** (M3).

Roles (switcher, top right): Asha (BD Manager), Ravi and Divya (BD Executives). An `X-Persona` header carries the
choice; the API enforces who may do what (state machine + role checks). This is seeded-persona access control, not
real authentication.

## Demo path
1. As **Asha**: open a report (e.g. Velachery) -> **Assign to executive** on a hotspot -> pick Divya, add a note.
2. Switch to **Divya**: *Assignments* -> open it -> **Add property** (7-step stepper):
   Location (**Use current location**, drag the pin, or tap the map) -> Rent -> Building -> Access & parking ->
   Competitors seen -> Photos (camera) -> Review & submit. Missing required fields and photos are listed with the
   exact reason; a nearby existing property triggers a duplicate warning.
3. After submit the evaluation runs in the background. Switch back to **Asha**: *Properties* -> open it. Reject with
   a reason, or **Request catchment study**. Every change is in the pipeline history.

**Geolocation needs HTTPS or localhost.** On a phone over the plain LAN URL the browser blocks it (the UI says so and
falls back to tapping the map). For phone demos use an HTTPS tunnel or an HTTPS dev server. On a laptop the location
can be off by hundreds of metres; that is why the pin is draggable and the accuracy circle is shown.

## Data model (new tables; M1 tables are untouched)
| table | purpose |
|---|---|
| `properties` | one row per building: exact `POINT` (SRID 4326, GiST index), rent, areas, access, parking, `pipeline_stage`, duplicate flags. **No cell or report foreign key**: reports are snapshots, so the point is the source of truth and cells/reports are found by spatial query |
| `property_photos` | `storage_key` only (never a URL or binary); photo type; size |
| `property_field_competitors` | competitors the executive saw (kept separate from OpenStreetMap competitors) |
| `property_evaluations` | **append-only, versioned**: score, confidence, breakdown, insights, risks, recommendation, explanation, data sources, flags, M1 context, trigger |
| `property_status_history` | every stage change: from, to, who, when, reason |
| `scouting_assignments` | manager -> executive task tied to an area and a copied hotspot (cell id + coordinates) |

## Pipeline
`ASSIGNED` (editable in-field draft) -> `SUBMITTED` -> `UNDER_REVIEW` -> `CATCHMENT_REQUESTED` ->
`CATCHMENT_IN_PROGRESS` -> `CATCHMENT_COMPLETED` -> `FINAL_REVIEW` -> `APPROVED` / `REJECTED`, plus a manager
"send back for changes" (back to `ASSIGNED`).
* **No reject before the catchment study.** `REJECTED` is only reachable from `FINAL_REVIEW`, after the ground survey.
  Before that the manager can only start review, send back for changes, or request the study.
* **Requesting a catchment study needs no typed text.** One click; the history records "Catchment study requested".
  What gets recorded on the ground is defined by the survey executive's capture forms. Send back for changes, approve and
  reject each require a written reason.
* **Drafts are private to the executive.** The manager's Properties list only shows submitted properties. A property
  the manager sent back stays visible, labelled "Sent back to executive".
The transitions after `CATCHMENT_REQUESTED` are in the state machine (`services/pipeline.py`) for M3 to drive.

## Final review (after the catchment study)
`CATCHMENT_COMPLETED` -> `FINAL_REVIEW` -> `APPROVED` or `REJECTED`. The catchment stages are driven by Milestone 3 (the
survey side / system), not by the BD manager or executive.
* **Entering final review creates a new evaluation version** (trigger `catchment_completed`). The original M2 evaluation
  is never edited, so the review screen shows the original and the updated score side by side, with the change.
* **The manager decides on a finished evaluation.** Approve and reject are blocked while the updated evaluation is still
  running. A **reason is mandatory for both**.
* **Audit trail:** the decision is stored in `property_status_history` with who, when, the reason and the
  `evaluation_id` the manager was looking at ("Decided on evaluation v2"). Approved and rejected are terminal.
* **No invented survey data:** until M3 exists there are no catchment insights, and the screen says so. The final-review
  evaluation is a real re-run on the same public and field data.
* **Testing without M3:** `python scripts/advance_property.py <property id>` moves a property from
  `CATCHMENT_REQUESTED` to `FINAL_REVIEW` through the same state machine as the `system` actor. It is a dev helper only;
  nothing in the app UI can skip the survey stages.

## Evaluation (deterministic; the LLM never calculates)
Eight factors with configurable weights (`core/property_scoring_constants.py`, env-overridable): rent affordability 15,
accessibility/visibility 15, demand generators 15, demographic fit 10, competition vs demand 15, parking 10, space 10,
M1 area fitness 10. **All weights, caps and bands are documented assumptions, not validated Savomart rules.**
* **Missing data is never invented.** Factors that cannot be scored show "Insufficient data", the remaining weights
  are rescaled, and a **confidence** figure (share of weight actually assessed) is shown beside the score.
* **Rent:** rent per sq ft is always shown. `rent / expected revenue` is only computed if a manager supplies an
  expected monthly revenue (labelled with its source) and is compared with a configurable 3-5% band, an initial
  assumption. With no revenue the factor is unscored: not zero, not estimated.
* **Traffic signal** is a modifier, not a bonus: it helps only with easy entry/exit and counts against the site with
  difficult entry/exit.
* **Demand:** exact school / college / hospital counts within 250 m and 500 m of the pin (from cached or live
  OpenStreetMap data). Treated as trip-generator signals, not guaranteed customers. Real OSM data only: synthetic
  fallback data is never scored.
* **Competition:** organised competitors (supermarkets, plus convenience stores matching a configurable brand list)
  vs other grocery; field observations are shown separately and never added to public counts (they may be the same
  store). Zero competition is scored together with demand: with weak demand it is neutral, not "high opportunity".
* **Demographics:** M1's estimated locality data (flagged, lowers confidence). Income fit is not assessed: there is
  no income data and no Savomart target range.
* **Space:** sales/storage ratios only when measured; no 80:20 rule assumed.
* **M1 context:** latest completed report for the area, the grid cell containing the pin (`ST_Contains`), hotspot
  status, and the nearest Savomart (spatial query).
* **Risks** are rule-generated and the **recommendation** is a deterministic band on score and confidence, with
  high-severity risks (e.g. cannibalisation, heavy organised competition) preventing an automatic "proceed".
* **Re-evaluation:** automatic on submit and via *Re-evaluate*; each run adds a new version (v1 stays available), and
  M3 will add a version when a catchment study completes.
* **LLM (explanation only):** same grounded pattern as M1 (facts in, JSON out, every number checked against the
  facts, one retry, then a fixed template). No LLM key is configured here, so the template is used.

## Validation and duplicates
* Drafts accept partial data; **submit** requires location, address, locality, pincode, rent, areas, frontage,
  floors, type, access/parking answers and the required photos (front, road, interior). Errors are per field.
  Implausible values (negative area, ground floor larger than total, sales + storage larger than total, pin outside
  Chennai) are rejected at any save; unusual values (rent per sq ft, weak GPS, far from the hotspot) are warnings.
* **Duplicates:** the same building is detected by `ST_DWithin` within 20 m and by identical normalised address +
  pincode. It warns ("Possible duplicate property found 12 m away."); it never merges, upserts or overwrites, and the
  manager sees the flag. Two executives submitting the same building create two rows for a human to reconcile.

## Photo storage
Photos are stored outside Postgres. With `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
`R2_BUCKET` set, they go to a **private Cloudflare R2 bucket** and are shown through short-lived presigned URLs
(the raw object URL is not public). Uploads go browser -> API -> R2, so no bucket CORS is needed; phones compress
photos to about 1600 px first. Without those variables the app falls back to a gitignored local `backend/uploads/`
folder. Check the connection with `python scripts/check_r2.py` (it never prints secrets).

## M2 trade-offs and known issues
* Personas are seeded, not authenticated. Anyone who can call the API can set `X-Persona`.
* Demographics are estimates and OSM competitor/brand detection is heuristic (the brand list is configurable).
* Weights and bands are assumptions to calibrate against real store performance.
* The catchment request is recorded as a stage plus reason; M3 attaches the actual study.
* Evaluation runs as a FastAPI background task (fine for one node; a queue would be next).
* The managed database is remote (about 0.3 s per query), so the integration tests take minutes.
* Not built: offline capture with sync, PDF decision pack, reverse-geocode caching.
