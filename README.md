# Savo SiteScout: Chennai expansion intelligence (M1 Area Intelligence, M2 Property Scouting, M3 Ground Catchment Survey)

A BD Manager picks any part of Chennai (locality name, pincode, or grid cells on a map), runs a **virtual
analysis**, and gets a saved, timestamped **Area Fitness Report**: what the area is like, a 0-100 fit rating,
the reasoning behind it, and the 5 places to scout first. Built mobile-first (360 px and up) in the Savomart
brand colours (`#782B90` purple, `#FFF200` yellow).

> Status: **Milestones 1, 2 and 3 complete.** See the Milestone 2 and Milestone 3 sections below, and `m3_tweaks.md`
> for every deviation from the M3 spec.
> The `areas` table is the anchor M2 and M3 point to.

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
python scripts/seed_demo.py                # demo scouting assignments from M1 hotspots (--reset wipes M2 + M3 tables)
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
Ollama). **No key is stored in this repo** (keys live in `.env` locally and in the Render dashboard); without a key the
app runs on the template path, and the LLM path is covered by unit tests using a stubbed client.

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
The transitions after `CATCHMENT_REQUESTED` are driven by the survey workflow (Milestone 3).

## Final review (after the catchment study)
`CATCHMENT_COMPLETED` -> `FINAL_REVIEW` -> `APPROVED` or `REJECTED`. The catchment stages are driven by the Milestone 3
survey workflow, not by the BD manager or executive.
* **Entering final review creates a new evaluation version** (trigger `catchment_completed`). The original M2 evaluation
  is never edited, so the review screen shows the original and the updated score side by side, with the change.
* **The manager decides on a finished evaluation.** Approve and reject are blocked while the updated evaluation is still
  running. A **reason is mandatory for both**.
* **Audit trail:** the decision is stored in `property_status_history` with who, when, the reason and the
  `evaluation_id` the manager was looking at ("Decided on evaluation v2"). Approved and rejected are terminal.
* **No invented survey data:** if a property reaches final review without a completed survey, the screen says so. With a
  survey, the findings are shown next to the evaluation (see Milestone 3). The M2 score is never changed by M3.

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


---

# Milestone 3: Ground catchment survey

M1 gives virtual data, M2 evaluates the property with public and field data, **M3 checks the ground around the property
with people**: Survey Executives walk the catchment and record what is really there. The BD Manager then decides
(`FINAL_REVIEW`) with the M2 evaluation **and** the M3 findings side by side.

```
BD Manager: Request catchment study (one click)         property -> CATCHMENT_REQUESTED
Survey Manager: split into work units, assign            study REQUESTED
Survey Executives: capture on the ground                 units ASSIGNED -> IN_PROGRESS -> COMPLETED
Survey Manager: review insights, complete and send       study COMPLETED, property -> CATCHMENT_COMPLETED
BD Manager: FINAL_REVIEW -> APPROVED / REJECTED (reason required)
```

Roles: **Meena** (Survey Manager), **Karthik** and **Lakshmi** (Survey Executives), plus the BD roles from M2.
Visibility is enforced by the API: the BD Manager sees only catchment-level status (requested / in progress /
completed), never work units; a Survey Executive sees only their own units; the Survey Manager sees everything.

## Demo path
1. As **Asha**: submit-and-review a property as in M2, then **Request catchment study** (or request one for a whole
   analysed area from an M1 report). If a recent, good survey already covers it, it is reused (see below).
2. As **Meena**: *Studies* -> open the study -> **Preview the split** (map with coloured units, workload, lanes, suggested
   executive per unit; add or remove units) -> **Confirm and assign**.
3. As **Karthik** or **Lakshmi** (phone): *My units* -> open a unit -> **Start** -> **Add observation** -> pick a type
   (homes, shops, competitor, footfall, access, demand, condition) -> fill the short form -> **Use current location**
   (or tap the map; it must be inside the purple unit outline) -> optional photo -> save. **Mark this unit complete**.
4. As **Meena**, when all units are complete: **Review the insights** (preview, nothing saved) -> **Complete and send to
   BD Manager**.
5. As **Asha**: the property review now has a **Ground catchment survey** panel next to the unchanged M2 evaluation.
   **Start final review** -> **Approve** or **Reject** with a reason.

## Data model (five new tables; M1 and M2 tables untouched)
| table | purpose |
|---|---|
| `catchment_studies` | one request, for **either** an M2 `property` **or** an M1 `area` (DB CHECK: exactly one). `study_geometry` polygon, status `REQUESTED / IN_PROGRESS / COMPLETED`, `reused_from_study_id` + `reuse_reason`, quality flags |
| `survey_work_units` | the non-overlapping pieces: MULTIPOLYGON, assignee, status `ASSIGNED / IN_PROGRESS / COMPLETED`, priority, estimated lane distance, target and completed capture counts, workload breakdown |
| `survey_captures` | one observation: POINT (+GPS accuracy), type, `data` JSON, who and when |
| `survey_capture_photos` | optional evidence photo on one capture: `storage_key` only (R2 or local fallback), optional location |
| `catchment_insights` | **append-only, versioned** aggregation of the completed survey |

Foreign keys: `catchment_studies -> properties` (RESTRICT), `-> areas` (RESTRICT), `-> catchment_studies` (RESTRICT,
reuse); `survey_work_units -> catchment_studies`, `survey_captures -> survey_work_units`,
`survey_capture_photos -> survey_captures`, `catchment_insights -> catchment_studies` (all CASCADE). GiST indexes on
every geometry, indexes on every FK, CHECK constraints on statuses, types, priority and coverage. There is no offline
mode, no `client_capture_id`, no sync fields and no catchment-level photos.

## How work is split (fair, non-overlapping, balanced)
`services/catchment_split.py`, deterministic, no optimiser:
1. The catchment (a 500 m circle around a property, or the area polygon) is partitioned **exactly** into 125 m blocks.
2. Each block gets a workload: lane length (1 point per 100 m) + 0.5 per shop/office/bank + 0.5 per school, clinic,
   hospital or transit stop + estimated households / 50. Lane count alone is never the signal. Households come from
   M1's estimated demographics and are labelled as estimates; OSM buildings are not fetched, so building density is not
   claimed.
3. Units = `round(total points / 45)`, clamped to 1-12 (the manager can add or remove units in the preview).
4. Blocks are ordered in a serpentine sweep and cut where cumulative workload crosses `k x total / K`.
5. A unit is the union of its blocks, so units **cannot overlap and leave no gaps** (tested). Each unit shows its
   estimated lane distance, named lanes and target observations before anything is assigned.

If OpenStreetMap is unreachable the split falls back to equal-area blocks and says so (`split_by_area_only`); mock data
is never used to balance work. Suggested assignees even out people who already have open work.

## What is captured
Closed-list forms, validated on the server (`services/survey_schema.py`): **homes** (houses, apartments, occupancy,
construction, activity), **shops** (kind, name, count, activity), **competitor** (name, type, size, customer activity),
**footfall** (pedestrian and vehicle LOW/MEDIUM/HIGH, observation period; levels only, no invented counts),
**access** (road condition, width, entry/exit, parking, obstruction, construction, closure, median, difficult turns),
**demand generator** (school, college, hospital, apartments, offices, market, transit) and **local condition**
(construction, vacant land, waterlogging, blocked road, restrictions, barriers). The location must be inside the
executive's own unit (small GPS tolerance) or the server refuses it. Photos are optional on any observation.

## Insights (real data only)
`services/catchment_insights.py` aggregates the captures: counts and distributions of what was recorded, competitors
de-duplicated (same name and type within 25 m), distance of the nearest observed competitor to the property. A rated
category (homes, shops, footfall, access) needs at least 2 observations, otherwise it says **"Insufficient data"**.
Competition and demand generators are only reported when enough of the catchment was surveyed, because "no competitors
recorded" proves nothing at 5% coverage. Key findings and risks are rule-generated sentences that quote only computed
values. `overall_ground_fit_score` is an independent 0-100 indicator over the categories that have enough data (NULL if
fewer than three), shown as "ground indicator", and it **never changes the M2 score**. Insights are versioned: the Survey
Manager can generate a new version and older ones stay.

## Reuse of an existing study
When a study is requested, the app looks for a **completed** study whose geometry covers at least **80%** of the new
catchment, finished within **90 days**, with ground-data coverage of at least **70%** and no `insufficient_data` flag
(`core/survey_constants.py`). The best match (highest coverage, then newest) is reused: a **new** `catchment_studies` row
is created with `reused_from_study_id` and a plain-language `reuse_reason`, status `COMPLETED`; **no captures are copied**
and the old study stays intact. The property moves through the catchment stages with the reason recorded. Because an area
study's geometry is the whole area, a property scouted inside an area that already has a study reuses it. The BD Manager
can tick "Run a new survey even if a recent one nearby could be reused" to survey again.

## Final review
The final-review evaluation (M2 mechanism) now carries the survey facts and the explanation can cite them (through the
same number-grounding check), but **the M2 score and factors are unchanged**: the manager weighs both. The decision,
reason and evaluation version are stored in the audit history as in M2.

## M3 trade-offs and known issues
* Personas are seeded, not authenticated (no User table exists); see `m3_tweaks.md` for every deviation from the spec.
* Work units are groups of 125 m blocks, not polygons cut along individual lanes; the lanes inside each unit are listed,
  and non-overlap is guaranteed by construction. A finer, lane-following geometry is the obvious next step.
* Workload weights, the 45-point unit size and the reuse thresholds are assumptions to calibrate with real surveys.
* No offline capture (out of scope by design): observations are saved when submitted.
* The capture list supports deleting and re-adding an observation; an edit screen is not built (the API supports edits).
* Integration tests use the real remote database and take several minutes.

---

# Bonus: Conversational analyst (BD Manager)

An **Ask** tab where the BD Manager asks questions in plain English about existing areas (M1), properties (M2) and
catchment surveys (M3): "Compare Velachery and Mylapore", "Show properties in Velachery", "Which property has the highest
M2 score?", "What did the ground survey find around property 80?", "Which areas have completed catchment studies?".
It is **read-only**: it never starts an analysis, an evaluation or a study.

**How it stays honest** (`services/analyst.py`, `services/analyst_tools.py`, `api/routes/analyst.py`):
1. **Route:** the LLM only picks up to 3 of thirteen fixed tools and their arguments as JSON. Nine read the saved data
   (`search_areas`, `get_area_report`, `compare_areas`, `search_properties`, `get_property`, `get_property_evaluation`,
   `get_catchment`, `search_catchments`, `compare_properties`); four reach outside it (`list_chennai_areas`,
   `lookup_place`, `place_demographics`, `nearby_amenities`, see *External data* below). Names, argument types, enums and list sizes are validated; anything
   unknown is refused.
2. **Query:** the backend runs the tools read-only, reusing the existing M1/M2/M3 functions and tables. There is no
   analyst database and no copied data. Comparisons, rankings, gaps and "highest score" are computed in Python, not by
   the model. Anything missing returns "Insufficient data."
3. **Explain:** the LLM writes the answer from those verified results only. The **same grounding check as M1/M2** rejects
   any number that is not in the results; after one retry the answer is built by a fixed template from the same data.
4. **Permissions:** the endpoint is BD Manager only (`403` for every other role). Tools apply the manager's view of the
   data (drafts still being captured are hidden; catchment results carry no work-unit detail, which is Survey Manager
   only). The answer lists **sources** (area report, property, catchment study) that open the matching page.

**External data** (`services/analyst_external.py`) lets it answer what the saved data cannot ("what areas are in Chennai?",
"where is Perungudi?", "how many supermarkets are near Adyar?", "population around Tambaram?"):

| Source | Used for | Notes |
|---|---|---|
| OGD India pincode boundaries | Chennai Corporation zones, suburb areas, pincodes | bundled file, no network |
| Nominatim (OpenStreetMap) | where a place is; whether it is inside an analysed area | live, Chennai-bounded, 1 request/s policy |
| Overpass (OpenStreetMap) | counts of supermarkets, schools, hospitals, clinics, banks, bus stops within 300-2000 m | small count query, cached 6 h; counts are a minimum because OSM can be incomplete |
| Bundled locality table | population density, households, growth | **an ESTIMATE, always labelled "not Census"** |

Comparing an analysed area with one that has no report gives a *partial* comparison (report facts for one, estimate for the
other, nothing ranked, and a note to analyse the missing area). **Not connected:** Census of India ward tables, the Tamil Nadu OGD
portal and Bhuvan expose no open API this app can call reliably, so they are not faked; the assistant says so. Every external
answer carries a source chip that opens the source in a new tab.

No conversation is stored: the browser sends the last few turns with each question. Requests are limited to 20 a minute
per persona to protect a free LLM key.

**Setting it up (Gemini free tier example)** in `backend/.env` locally and in the Render dashboard (values are never
committed):
```
LLM_PROVIDER=openai
LLM_API_KEY=<your key>
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=<a current Flash model from AI Studio, e.g. gemini-3.5-flash>
LLM_FALLBACK_MODELS=<optional, comma separated, tried in order when the first is rate-limited or overloaded, e.g. gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-3.5-flash>
```
Free tiers limit requests **per model per day**, so a fallback list stretches the quota. Model names change: list what your
key can use in AI Studio. For the Gemini endpoint the client sends `reasoning_effort: none` (Gemini's hidden reasoning
otherwise eats the output budget); override with `LLM_REASONING_EFFORT`. When the provider is rate-limiting, the chat
says so ("busy, try again in about a minute") instead of failing.

**Known limits:** one routing round (a tool's result cannot feed another tool), so "compare the top two properties" needs
ids or a two-step conversation; area names are matched by text, so an ambiguous name returns candidates to choose from.

---

# Bonus: Opportunity Finder (BD Manager)

A separate **Opportunity Finder** page (bottom-nav "Finder", route `#/opportunities`) with its own map. It answers *"where should the
BD team scout next?"* without any area input: it scores every 500 m square of Chennai and lists the best places that have had little
or no scouting. The Analyse Area flow, M1 scoring, M2 and M3 are unchanged. It is deterministic backend code: **no LLM, no agent**.

**Flow:** `Find Opportunities` starts a background run (progress bar, polled like a report) -> Chennai is cut into 6 km tiles ->
per-tile map data (cached) -> per-cell features -> Savomart distances -> scouting coverage -> opportunity score -> ranking ->
top 10 with details. Tap a square or a list row for the breakdown; **Analyse this pocket** (with a confirm step) runs the normal
Analyse Area report on the 3 x 3 cells around it. Nothing else is created: no property, assignment or catchment study.

**What is reused from M1 (nothing duplicated):** the 500 m UTM-44N grid (`grid.py`), `features.compute_cell_features` and
`aggregate_area`, `scoring.score_features` with its ten factors and weights untouched, `hotspots.pick_hotspots` (spread top
results) / `why_bullets` / `nearest_locality`, `overpass_client`, `savomart_client` and its fallbacks, `demographics`, and the
same data-quality flag names.

**Opportunity score** (`services/opportunity_score.py`): `opportunity = (1 - W) x M1_score + W x 100 x (1 - scouting_coverage)`.
The ten M1 factors are rescaled to `(1 - W)` of the total and one new factor, *unscouted opportunity*, is worth `W`; weights still sum
to 100. `W` defaults to **0.20** (`OPP_W_UNSCOUTED`); it is an assumption to calibrate, not a business fact, and `W = 0` gives exactly
the M1 score. Every run stores the assumptions it used.

**Scouting coverage** (`services/opportunity_coverage.py`, 0 to 1, all constants in `core/opportunity_constants.py`): evidence from
existing data, read in three queries: a property in the cell (1.0 unit, 0.5 if it has no completed evaluation), an open scouting
assignment on that hotspot cell (0.5), a catchment study covering the cell (3.0 completed, 1.5 requested or in progress). Signals fade
with age (full for 90 days, zero at 365). Point signals count fully in their own cell and 50% in the 8 neighbours; a study counts in
every cell it covers. `coverage = min(1, units / 3)`. So a lone property lowers a square's priority a little (it is **not** excluded)
and a completed catchment study nearly fills it.

**Data and freshness:** Overpass in 6 km tiles (one padded request per tile, never per cell; 42 tiles for the default city bounds);
the cache (`opportunity_tiles`) keeps the derived per-cell features, place names, source and fetched time for 7 days
(`OPPORTUNITY_CACHE_HOURS`), not the raw road geometry. If Overpass fails a stale cache is used and flagged, otherwise the tile is
reported missing and its cells are **not ranked**; nothing is mocked. A cell is only ranked if it has mapped roads or places (so
water and open land are never scored on an estimate) and lies within 3 km of a locality in the demographic table. Savomart distances
are recomputed every run from the current stores. Locality names come from OSM place nodes (no Nominatim for bulk work). OSRM (public
demo server) gives road distance and time to the nearest store for the **top 10 only**; scoring keeps M1's straight-line measure.
**Population is an estimate** (bundled locality table, Census-2011 order of magnitude), never presented as current Census data;
there is no Census API in this project.

**Speed:** the first city-wide run reads about 40 tiles from Overpass one at a time (about 8 to 25 minutes depending on Overpass).
Later runs reuse the cache (about a minute, mostly database round trips). Pre-fill it once, before a demo:
`python backend/scripts/warm_opportunity_cache.py` (the cache lives in the shared database, so this also warms the deployed app).
Re-running retries any tile that failed.

**API** (BD Manager only): `POST /api/opportunities/runs` (202; a second click joins the running one), `GET /api/opportunities/runs/latest`,
`GET /api/opportunities/runs/{id}` (+ `/status`, `/cells/{cell_id}`). **New tables** (additive, created on startup): `opportunity_tiles`,
`opportunity_runs` (latest 3 runs kept).

**Known limits:** population is estimated; OSM can be incomplete; the coverage and `W` defaults are starting assumptions; a tile can
fail on a busy Overpass (re-run to retry); the local Savomart store list is a sample snapshot unless `SAVOMART_CRON_TOKEN` is set.

---

# Deploying (Vercel frontend + Render backend)

The frontend is static, so it goes on **Vercel**. The backend must run on an always-on server, because analyses and
property evaluations keep working after the HTTP reply. Vercel's serverless functions would cut them off, so the backend
goes on **Render**. The database (PostGIS) and photo storage (Cloudflare R2) are hosted services already.

**Live URLs:** Frontend: https://savo-sitescout.vercel.app · Backend health check:
https://savo-sitescout-api-ix6x.onrender.com/api/health (the free Render instance sleeps when idle, so open the site once
and wait a minute before a demo).

## 1. Backend on Render
1. Push the repo to GitHub. On render.com choose **New → Blueprint** and pick the repo (it reads `render.yaml`), or
   **New → Web Service** with root directory `backend`, build `pip install -r requirements.txt`, start
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/api/health`.
2. Enter these environment variables in the Render dashboard (never in a file): `db_url`, `R2_ACCOUNT_ID`,
   `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `SAVOMART_CRON_TOKEN`, and optionally `LLM_PROVIDER` /
   `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_FALLBACK_MODELS` (needed for the conversational analyst).
3. Let Render reach the database (allowed-IP settings on your database provider).
4. Open `https://<service>.onrender.com/api/health`: it should say `"database": true`.

## 2. Frontend on Vercel
1. `npm i -g vercel`, then `vercel login`.
2. In `frontend/` run `vercel` once to link the project (accept the Vite defaults; `vercel.json` sets the build).
3. Set the backend address: `vercel env add VITE_API_BASE production` and enter the Render URL without a trailing slash.
   Vite bakes it in at build time, so changing it needs a redeploy.
4. `vercel --prod` prints the production URL.

## 3. Connect them
Set `CORS_ORIGINS` on Render to the exact Vercel production URL (for example `https://your-project.vercel.app`, no
trailing slash) and redeploy the backend. Use the production address, not a per-deployment preview URL.

## Notes
* **Free tier:** Render's free web service sleeps when idle, so the first request after a pause can take about a minute.
  Open the site once before a demo.
* **HTTPS:** Vercel serves HTTPS, which browsers require for "Use current location" on a phone.
* In development nothing changes: leave `VITE_API_BASE` empty and the Vite dev server proxies `/api` to the backend.
* Deployment settings contain only variable **names** (`render.yaml`, `frontend/.env.example`); no secret is stored in the repo.
