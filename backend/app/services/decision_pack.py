"""Decision Pack: a leadership-ready PDF for an APPROVED property, built on the fly from existing M1, M2 and M3 data.

Nothing is stored or duplicated: the caller passes the property detail (routes.properties.detail_json), and this module adds the
M1 area report, the Savomart stores and the photos, then lays them out with reportlab. Missing optional data reads "Not
available" and is never invented. Internal ids, JSON and flag codes are turned into plain sentences.
"""
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

from app.models.db_models import AreaReport
from app.models.property_models import PropertyPhoto
from app.models.survey_models import SurveyCapturePhoto
from app.services import savomart_client, storage
from app.services.m1_lookup import latest_completed_report

PURPLE, PURPLE_DARK, YELLOW = colors.HexColor("#782B90"), colors.HexColor("#4F1A5F"), colors.HexColor("#FFF200")
TINT, LINE, INK, MUTED = colors.HexColor("#F1E6F5"), colors.HexColor("#E6DCEA"), colors.HexColor("#221A26"), colors.HexColor("#5A4D60")
NA = "Not available"
IST = timezone(timedelta(hours=5, minutes=30))

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.2, leading=12.4, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.4, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.6, leading=11)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=PURPLE_DARK, spaceBefore=10, spaceAfter=5, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=PURPLE, spaceBefore=7, spaceAfter=3, keepWithNext=1)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=PURPLE_DARK)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=11, bulletIndent=2)


# ------------------------------------------------------------------ small helpers
def _t(v) -> str:
    """Text safe for the built-in PDF font (no rupee sign / exotic characters) and for reportlab's mini-markup."""
    s = NA if v is None or v == "" else str(v)
    s = s.replace("₹", "Rs ").replace("→", "to").replace("≤", "<=").replace("≥", ">=")
    return escape(s.encode("cp1252", "replace").decode("cp1252"))


def _p(v, style=BODY) -> Paragraph:
    return Paragraph(_t(v), style)


def _dt(iso: str | None, with_time: bool = False) -> str:
    if not iso:
        return NA
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        d = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(IST)
        return d.strftime("%d %b %Y, %H:%M IST" if with_time else "%d %b %Y")
    except ValueError:
        return NA


def _n(v, unit: str = "", digits: int = 1) -> str:
    if v is None:
        return NA
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        txt = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.{digits}f}"
        return f"{txt} {unit}".strip()
    return f"{v} {unit}".strip()


def _rs(v, digits: int = 0) -> str:
    return NA if v is None else f"Rs {v:,.{digits}f}"


def _words(code) -> str:
    return NA if not code else str(code).replace("_", " ").capitalize()


def _kv(rows: list[tuple[str, object]], widths=(52 * mm, 118 * mm)) -> Table:
    t = Table([[_p(k, CELL), _p(v, CELL)] for k, v in rows], colWidths=widths)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("BACKGROUND", (0, 0), (0, -1), TINT), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _grid(header: list[str], rows: list[list], widths) -> Table:
    data = [[_p(h, ParagraphStyle("th", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)) for h in header]]
    data += [[c if hasattr(c, "wrap") else _p(c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), PURPLE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TINT]), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _bullets(items: list[str], empty: str = NA) -> list:
    return [Paragraph(_t(i), BULLET, bulletText="•") for i in items] or [_p(empty, SMALL)]


def _summary_line(d: dict | None, labels: dict[str, str]) -> str:
    """One readable sentence from a survey summary dict: 'Independent houses: 26; activity: low (2)'."""
    if not d:
        return "Insufficient data"
    if not d.get("sufficient", True) and d.get("status"):
        return f"{d['status']} ({d.get('observations', 0)} observation(s))"
    parts = []
    for key, label in labels.items():
        v = d.get(key)
        if v in (None, {}, [], ""):
            continue
        if isinstance(v, dict):
            v = ", ".join(f"{_words(k).lower()} ({n})" for k, n in v.items())
        elif isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        parts.append(f"{label}: {v}")
    body = "; ".join(parts) or "nothing recorded"
    return body + ("" if d.get("sufficient", True) else "  (limited data: fewer than 2 observations)")


# ------------------------------------------------------------------ photos and map
def _photo_flow(blobs: list[tuple[str, bytes]]) -> list:
    """Up to four photos in a 2 x 2 grid, shrunk to about 800 px so the PDF stays small. Unreadable photos are skipped."""
    cells = []
    for caption, raw in blobs:
        try:
            im = PILImage.open(BytesIO(raw)).convert("RGB")
            im.thumbnail((800, 800))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=78)
            buf.seek(0)
            w, h = im.size
            width = 82 * mm
            height = min(width * h / w, 62 * mm)
            cells.append([Image(buf, width=height * w / h if height < width * h / w else width, height=height), _p(caption, SMALL)])
        except Exception:  # noqa: BLE001  (a bad file must never break the pack)
            continue
    if not cells:
        return [_p("No photos available.", SMALL)]
    rows = []
    for i in range(0, len(cells), 2):
        pair = cells[i:i + 2] + [None] * (2 - len(cells[i:i + 2]))
        rows.append([[c[0], c[1]] if c else "" for c in pair])
    t = Table(rows, colWidths=[85 * mm, 85 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return [t]


def _meters(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    return (lon - lon0) * 111320 * math.cos(math.radians(lat0)), (lat - lat0) * 110540


def _map(lat: float, lon: float, stores: list[dict]) -> Drawing:
    """Schematic location map: the property with its nearest Savomart stores. Drawn as vector shapes (no map tiles)."""
    W, H = 170 * mm, 72 * mm
    near = sorted(((math.hypot(*_meters(lat, lon, s["latitude"], s["longitude"])), s) for s in stores), key=lambda t: t[0])
    radius = max(2000.0, min((near[0][0] * 1.35) if near else 3000.0, 7000.0))
    scale = (H / 2 - 6 * mm) / radius
    cx, cy = W / 2, H / 2
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, fillColor=colors.HexColor("#F7F3F9"), strokeColor=LINE, strokeWidth=0.8))
    for r_m in ([1000, 2000] if radius <= 3500 else [2000, 4000, 6000]):
        if r_m < radius:
            d.add(Circle(cx, cy, r_m * scale, fillColor=None, strokeColor=colors.HexColor("#C9B8D2"), strokeWidth=0.6, strokeDashArray=[2, 2]))
            d.add(String(cx + 1, cy + r_m * scale + 1.5, f"{r_m // 1000} km", fontSize=6, fontName="Helvetica", fillColor=MUTED))
    shown = 0
    for dist, s in near:
        if dist > radius * 1.02 or shown >= 6:
            continue
        dx, dy = _meters(lat, lon, s["latitude"], s["longitude"])
        x, y = cx + dx * scale, cy + dy * scale
        d.add(Line(cx, cy, x, y, strokeColor=colors.HexColor("#B9A3C4"), strokeWidth=0.5))
        d.add(Circle(x, y, 3.2, fillColor=PURPLE, strokeColor=YELLOW, strokeWidth=1.2))
        d.add(String(min(max(x + 5, 3), W - 60), y - 2, f"{s['name'][:22]} ({dist / 1000:.1f} km)", fontSize=6.5, fontName="Helvetica", fillColor=INK))
        shown += 1
    d.add(Circle(cx, cy, 5.5, fillColor=YELLOW, strokeColor=PURPLE, strokeWidth=2))
    d.add(String(cx + 8, cy - 2, "This property", fontSize=7.5, fontName="Helvetica-Bold", fillColor=PURPLE_DARK))
    d.add(String(W - 10, H - 10, "N", fontSize=8, fontName="Helvetica-Bold", fillColor=MUTED))
    d.add(Line(W - 7, H - 24, W - 7, H - 13, strokeColor=MUTED, strokeWidth=1))
    if not near or near[0][0] > radius:
        d.add(String(6, 6, "No Savomart store inside the map area", fontSize=6.5, fontName="Helvetica", fillColor=MUTED))
    return d


# ------------------------------------------------------------------ page furniture
def _on_page(canvas, doc):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(PURPLE)
    canvas.rect(0, h - 13 * mm, w, 13 * mm, stroke=0, fill=1)
    canvas.setFillColor(YELLOW)
    canvas.rect(0, h - 14.2 * mm, w, 1.2 * mm, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(20 * mm, h - 8.6 * mm, "Savo SiteScout")
    canvas.setFont("Helvetica", 8.5)
    canvas.drawRightString(w - 20 * mm, h - 8.6 * mm, "Decision Pack  |  Confidential: for leadership review")
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(20 * mm, 10 * mm, f"Generated {doc.generated}  |  Property #{doc.pid}")
    canvas.drawRightString(w - 20 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


# ------------------------------------------------------------------ the pack
def build_pack(db, prop, d: dict) -> bytes:
    """`d` is routes.properties.detail_json for this (APPROVED) property; everything else is read from existing tables."""
    now = datetime.now(IST)
    generated = now.strftime("%d %b %Y, %H:%M IST")
    ev = d.get("evaluation") or {}
    fd = d.get("final_decision") or {}
    cat = d.get("catchment") or {}
    ins = cat.get("insights") or {}
    m1ctx = ev.get("m1_context") or {}
    rep: AreaReport | None = db.get(AreaReport, m1ctx["area_report_id"]) if m1ctx.get("area_report_id") else latest_completed_report(db, prop.area_id)
    prof = (rep.area_profile or {}) if rep else {}
    try:
        stores, store_meta = savomart_client.get_stores(db)
    except Exception:  # noqa: BLE001
        stores, store_meta = [], {}
    title = d.get("address") or f"Property #{d['property_id']}"

    # photos, fetched together (property photos and survey evidence)
    jobs: list[tuple[str, str]] = [(f"{_words(p.photo_type)}", p.storage_key) for p in prop.photos[:4]]
    ev_jobs: list[tuple[str, str]] = []
    for e in (cat.get("evidence_photos") or [])[:4]:
        row = db.get(SurveyCapturePhoto, e["photo_id"])
        if row is not None:
            ev_jobs.append((f"{_words(e.get('capture_type'))} observation ({_dt(e.get('captured_at'))})", row.storage_key))

    def fetch(job):
        try:
            return job[0], storage.read(job[1])
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        prop_blobs = [r for r in pool.map(fetch, jobs) if r]
        ev_blobs = [r for r in pool.map(fetch, ev_jobs) if r]

    story: list = [Spacer(1, 3 * mm), _p(title, TITLE), Spacer(1, 2 * mm)]
    sub = [f"Property #{d['property_id']}", d.get("locality") or d.get("area_name")]
    story.append(_p("  |  ".join(str(x) for x in sub if x), SMALL))
    band = Table([[_p("APPROVED", ParagraphStyle("st", parent=BODY, fontName="Helvetica-Bold", fontSize=11, textColor=PURPLE_DARK)),
                   _p(f"Property score {_n(ev.get('overall_score'))} / 100", CELL),
                   _p(f"Area score {_n(m1ctx.get('area_score') if m1ctx else None)} / 100", CELL),
                   _p(f"Ground survey coverage {_n(ins.get('coverage_percentage'), '%', 0)}" if ins else "No ground survey", CELL)]],
                 colWidths=[30 * mm, 42 * mm, 40 * mm, 58 * mm])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), YELLOW), ("BACKGROUND", (1, 0), (-1, 0), TINT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [Spacer(1, 3 * mm), band]

    # 1 ------------------------------------------------------------ property overview
    story.append(_p("1. Property overview", H1))
    story.append(_kv([
        ("Property", f"#{d['property_id']}"), ("Address", ", ".join(x for x in [d.get("address"), d.get("locality"), d.get("pincode")] if x) or NA),
        ("Area analysed", d.get("area_name")), ("Location", f"{d['lat']:.5f}, {d['lon']:.5f}"),
        ("Property type", _words(d.get("property_type"))), ("Total area", _n(d.get("total_area_sqft"), "sq ft")),
        ("Ground-floor area", _n(d.get("ground_floor_area_sqft"), "sq ft")), ("Sales area", _n(d.get("sales_area_sqft"), "sq ft")),
        ("Storage area", _n(d.get("storage_area_sqft"), "sq ft")), ("Floors", f"{_n(d.get('floor'))} (of {_n(d.get('number_of_floors'))})"),
        ("Monthly rent", _rs(d.get("monthly_rent"))), ("Rent per sq ft", _rs(d.get("rent_per_sqft"), 1)),
        ("Security deposit", _rs(d.get("security_deposit"))), ("Lease term", _n(d.get("lease_duration_months"), "months")),
    ]))
    story += [Spacer(1, 3 * mm), _p("Location relative to Savomart stores", H2), _map(d["lat"], d["lon"], stores),
              _p("Schematic map (not a street map): shows the property and its nearest Savomart stores.", SMALL),
              KeepTogether([_p("Property photos", H2)] + _photo_flow(prop_blobs))]

    # 2 ------------------------------------------------------------ M1
    story.append(_p("2. Area intelligence", H1))
    if rep is None:
        story.append(_p("No completed area report is available for this location.", BODY))
    else:
        story.append(_kv([
            ("Area / locality", f"{d.get('area_name')}  (property cell locality: {m1ctx.get('cell_locality') or NA})"),
            ("Area fitness score", f"{_n(rep.overall_score)} / 100  ({rep.rating})"), ("Report date", _dt(rep.created_at.isoformat())),
            ("Property's grid cell", f"score {_n(m1ctx.get('cell_score'))}" + (f"; a top scouting hotspot (rank {m1ctx['hotspot_rank']})" if m1ctx.get("is_hotspot") else "; not a top hotspot")),
            ("Area size", _n(prof.get("area_km2"), "km2")),
            ("Population (estimate)", _n(prof.get("estimated_population"), digits=0)), ("Households (estimate)", _n(prof.get("estimated_households"), digits=0)),
            ("Amenities in the area", f"{_n(prof.get('schools'))} schools, {_n(prof.get('colleges'))} colleges, {_n(prof.get('hospitals'))} hospitals, {_n(prof.get('clinics'))} clinics, {_n(prof.get('transit_stops'))} transit stops"),
            ("Competition in the area", f"{_n(prof.get('supermarkets'))} supermarkets, {_n(prof.get('convenience_stores'))} convenience stores; {_n(prof.get('shops_offices_banks'))} shops, offices and banks"),
            ("Nearest Savomart to this property", f"{m1ctx.get('nearest_savomart') or NA}, {_n(m1ctx.get('nearest_savomart_m'), 'm', 0)} away; {_n(m1ctx.get('savomart_within_1km'), digits=0)} within 1 km"),
            ("Savomart stores in the area", f"{_n(prof.get('savomart_stores_inside'), digits=0)} inside; {_n(prof.get('savomart_stores_within_3km'), digits=0)} within 3 km of the area"),
        ]))
        hs = (prof.get("hotspots") or [])[:3]
        if hs:
            story += [_p("Top scouting hotspots in this area", H2)] + _bullets([f"#{h.get('rank')} {h.get('locality')}: score {_n(h.get('score'))}" for h in hs])
        if prof.get("population_is_estimate"):
            story.append(_p("Population and household figures are estimates from a locality table, not official Census data.", SMALL))

    # 3 ------------------------------------------------------------ M2
    story.append(_p("3. Property evaluation", H1))
    if not ev:
        story.append(_p("No completed evaluation is available.", BODY))
    else:
        orig, upd = d.get("original_evaluation"), d.get("updated_evaluation")
        head = [("Property score", f"{_n(ev.get('overall_score'))} / 100 (evaluation v{ev.get('version')}, {_dt(ev.get('created_at'))})"),
                ("Confidence", f"{_n((ev.get('confidence') or 0) * 100, '%', 0)} of the score weight could be assessed" if ev.get("confidence") is not None else NA),
                ("Recommendation", _words(ev.get("recommendation")))]
        if orig and upd and orig.get("overall_score") is not None and upd.get("overall_score") is not None:
            head.append(("Before / after the ground survey", f"{_n(orig['overall_score'])} to {_n(upd['overall_score'])} (change {_n(d.get('score_change'))})"))
        story.append(_kv(head))
        rows = [[b["label"], (f"{b['points']:.1f} / {b['effective_weight']:.1f}" if b.get("scored") else "Insufficient data"), b.get("explanation") or ""]
                for b in ev.get("score_breakdown") or []]
        story += [_p("Score breakdown", H2), _grid(["Factor", "Points", "Why"], rows, [42 * mm, 26 * mm, 102 * mm])]
        met = ev.get("metrics") or {}
        poi = (met.get("poi") or {})
        story += [_p("Key property facts", H2), _kv([
            ("Rent", f"{_rs(d.get('monthly_rent'))} a month, {_rs(d.get('rent_per_sqft'), 1)} per sq ft" + ("; negotiable" if d.get("rent_negotiable") else "")),
            ("Accessibility", f"{'main-road frontage' if d.get('is_main_road_frontage') else 'not on the main road'}; {'corner property' if d.get('is_corner_property') else 'not a corner property'}; road width {_n(d.get('road_width_ft'), 'ft')}; entry {_words(d.get('entry_access')).lower()}, exit {_words(d.get('exit_access')).lower()}; visibility {_n(d.get('visibility_score'), '/ 5', 0)}"),
            ("Road frontage", _n(d.get("frontage_ft"), "ft")),
            ("Parking", f"two-wheeler {_n(d.get('two_wheeler_parking'))}; four-wheeler {_n(d.get('four_wheeler_parking'))}; capacity {_n(d.get('parking_capacity'), digits=0)}; {_words(d.get('parking_type')).lower()}"),
            ("Competition nearby", (f"organised competitors within 500 m: {(poi.get('organised') or {}).get('500', NA)}; within 1 km: {(poi.get('organised') or {}).get('1000', NA)}" if poi.get("available") else "map data not available")
             + (f"; field notes: {', '.join(c['name'] for c in d.get('field_competitors') or [])}" if d.get("field_competitors") else "")),
        ])]
        story += [_p("Risks", H2)] + _bullets([f"{_words(r.get('severity'))}: {r.get('text')}" for r in ev.get("risks") or []], "No risks flagged.")
        story += [_p("Evaluation insights", H2)] + _bullets([i.get("text") for i in ev.get("insights") or []], NA)

    # 4 ------------------------------------------------------------ M3
    story.append(_p("4. Ground catchment survey", H1))
    if not cat:
        story.append(_p("No catchment survey was recorded for this property.", BODY))
    else:
        story.append(_kv([
            ("Status", _words(cat.get("status")) + (" (an existing recent survey was reused)" if cat.get("reused") else "")),
            ("Requested / completed", f"{_dt(cat.get('requested_at'))} / {_dt(cat.get('completed_at'))}"),
            ("Survey coverage", f"{_n(ins.get('coverage_percentage'), '%', 0)} of the planned observations were recorded" if ins else NA),
            ("Ground indicator", _n(ins.get("ground_fit_score"), "/ 100") if ins.get("ground_fit_score") is not None else "Insufficient data (needs at least three well-covered categories)"),
            ("Residential", _summary_line(ins.get("residential"), {"independent_houses": "independent houses", "apartment_complexes": "apartment complexes", "activity_level": "activity", "occupancy": "occupancy"})),
            ("Commercial", _summary_line(ins.get("commercial"), {"businesses_total": "businesses", "businesses_by_kind": "kinds", "activity_level": "activity"})),
            ("Ground-observed competition", _summary_line(ins.get("competition"), {"competitors": "competitors", "by_type": "types", "by_size": "sizes", "customer_activity": "customer activity"})),
            ("Traffic and footfall", _summary_line(ins.get("traffic"), {"pedestrian": "pedestrian", "vehicle": "vehicle"})),
            ("Accessibility", _summary_line(ins.get("accessibility"), {"road_condition": "road condition", "entry_exit": "entry/exit", "parking": "parking", "median_road_width_ft": "road width (ft)"})),
            ("Demand generators", _summary_line(ins.get("demand_generators"), {"by_kind": "found", "named": "named"})),
        ]))
        story += [_p("Key findings", H2)] + _bullets(ins.get("key_findings") or [], NA)
        story += [_p("Survey risks", H2)] + _bullets([f"{_words(r.get('severity'))}: {r.get('text')}" for r in ins.get("risks") or []], "No survey risks flagged.")
        story.append(KeepTogether([_p("Survey evidence photos", H2)] + (_photo_flow(ev_blobs) if ev_blobs else [_p("No survey photos were recorded.", SMALL)])))

    # 5 ------------------------------------------------------------ decision
    story.append(_p("5. Final decision", H1))
    based = fd.get("based_on_evaluation") or {}
    story.append(_kv([
        ("Status", "APPROVED"), ("Approved by", fd.get("decided_by_name") or NA), ("Approval date", _dt(fd.get("decided_at"), True)),
        ("Decision notes", fd.get("reason") or "No notes were recorded."),
        ("Decided on", f"evaluation v{based.get('version')}, score {_n(based.get('overall_score'))}" if based else NA)]))
    tl = [[_dt(h.get("changed_at")), f"{_words(h.get('from_stage'))} to {_words(h.get('to_stage'))}", h.get("changed_by_name"), h.get("reason") or ""]
          for h in d.get("history") or []]
    if tl:
        story += [_p("Decision trail", H2), _grid(["Date", "Step", "By", "Note"], tl, [24 * mm, 56 * mm, 34 * mm, 56 * mm])]

    # 6 ------------------------------------------------------------ sources
    story.append(_p("6. Data sources and dates", H1))
    src, seen = [], set()

    def add(name, date, note=""):
        if name and name not in seen:
            seen.add(name)
            src.append([name, _dt(date), note])
    if rep:
        add(f"Area Fitness Report ({d.get('area_name')})", rep.created_at.isoformat(), "Area analysis")
        for s in rep.data_sources or []:
            add(s.get("source"), s.get("fetched_at"), "estimate, not official" if s.get("mocked") else "")
    add(f"Property evaluation v{ev.get('version')}", ev.get("created_at"), "Deterministic scoring") if ev else None
    for s in ev.get("data_sources") or []:
        add(s.get("source"), s.get("fetched_at"), "estimate, not official" if s.get("mocked") else "")
    if cat:
        add("Ground catchment survey", (cat.get("completed_at") or cat.get("requested_at")), f"insight version {ins.get('version', NA)}" if ins else "")
    add(store_meta.get("source", "Savomart stores list"), store_meta.get("fetched_at"), "sample list, not the live store list" if store_meta.get("mocked") else "")
    story.append(_grid(["Source", "Date", "Note"], src, [88 * mm, 26 * mm, 56 * mm]))
    story += [Spacer(1, 3 * mm), _p("Map data (c) OpenStreetMap contributors. Population figures are estimates unless stated. Scores are decision "
                                     "aids based on documented assumptions, not guarantees. Generated by Savo SiteScout on " + generated + ".", SMALL)]

    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=18 * mm,
                          title=f"Decision Pack - {title}", author="Savo SiteScout", subject="Property decision pack")
    doc.generated, doc.pid = generated, d["property_id"]
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=_on_page)])
    doc.build(story)
    return buf.getvalue()
