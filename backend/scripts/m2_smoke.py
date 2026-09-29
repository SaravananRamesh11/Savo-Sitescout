"""End-to-end M2 API smoke test against a running server (localhost:8000). Creates data tagged for cleanup."""
import io, json, sys, time
import httpx

B = "http://localhost:8000/api"
R = {"X-Persona": "bd_executive:ravi"}
M = {"X-Persona": "bd_manager:asha"}
# minimal valid JPEG (1x1)
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9")

def show(label, r):
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    print(f"{label}: {r.status_code}", (json.dumps(body)[:260] if not isinstance(body, dict) or r.status_code >= 400 else ""))
    return body

c = httpx.Client(timeout=120)
draft = {"lat": 12.98632, "lon": 80.22776, "location_accuracy_m": 18, "area_id": 1,
         "address": "No. 12, Sony Nagar 2nd St", "locality": "Sony Nagar"}
p = show("create draft", c.post(f"{B}/properties", json=draft, headers=R))
pid = p["property_id"]
print("  stage", p["pipeline_stage"], "| area", p["area_name"])
show("submit incomplete", c.post(f"{B}/properties/{pid}/submit", json={}, headers=R))
full = {"pincode": "600042", "total_area_sqft": 1800, "ground_floor_area_sqft": 1200, "sales_area_sqft": 1300,
        "storage_area_sqft": 400, "frontage_ft": 24, "road_width_ft": 40, "floor": 0, "number_of_floors": 2,
        "property_type": "shop", "monthly_rent": 145000, "rent_negotiable": True, "is_corner_property": True,
        "is_main_road_frontage": True, "traffic_signal_nearby": True, "signal_distance_m": 60,
        "entry_access": "easy", "exit_access": "moderate", "visibility_score": 4, "two_wheeler_parking": True,
        "four_wheeler_parking": True, "parking_capacity": 4, "parking_type": "on_property"}
show("patch details", c.patch(f"{B}/properties/{pid}", json=full, headers=R))
show("bad patch (ground > total)", c.patch(f"{B}/properties/{pid}", json={"ground_floor_area_sqft": 9000}, headers=R))
r = show("submit w/o photos", c.post(f"{B}/properties/{pid}/submit", json={}, headers=R))
for t in ("front_view", "road_view", "interior_view"):
    show(f"upload {t}", c.post(f"{B}/properties/{pid}/photos", data={"photo_type": t},
                               files={"file": ("x.jpg", JPEG, "image/jpeg")}, headers=R))
show("upload bad file", c.post(f"{B}/properties/{pid}/photos", data={"photo_type": "side_view"},
                               files={"file": ("x.jpg", b"not an image", "image/jpeg")}, headers=R))
show("add competitor", c.post(f"{B}/properties/{pid}/competitors", json={"name": "Reliance Smart", "kind": "supermarket", "approx_distance_m": 350}, headers=R))
r = show("submit", c.post(f"{B}/properties/{pid}/submit", json={"acknowledge_warnings": True}, headers=R))
for i in range(40):
    s = c.get(f"{B}/properties/{pid}/evaluation-status", headers=M).json()
    if s["latest"] and s["latest"]["status"] != "running":
        break
    time.sleep(3)
print("eval:", s["latest"]["status"], "score", s["latest"]["overall_score"], "conf", s["latest"]["confidence"], s["latest"]["recommendation"])
d = c.get(f"{B}/properties/{pid}", headers=M).json()
ev = d["evaluation"]
print("stage:", d["pipeline_stage"], "| version", ev["version"], "| expl source", ev["explanation_source"], "| flags", ev["data_quality_flags"])
for f in ev["score_breakdown"]:
    print(f"   {f['label']:30} w={f['weight']:>4} eff={f['effective_weight']:>6} pts={f['points']} :: {f['explanation'][:90]}")
print("risks:", [(r['code'], r['severity']) for r in ev["risks"]])
print("metrics:", {k: v for k, v in ev["metrics"].items() if k in ("rent_per_sqft", "rent_to_revenue", "sales_ratio", "storage_to_sales")})
print("m1:", {k: ev["m1_context"][k] for k in ("area_score", "cell_id", "cell_score", "is_hotspot", "nearest_savomart", "nearest_savomart_m")})
# duplicate creation
dup = show("dup create", c.post(f"{B}/properties", json={"lat": 12.98638, "lon": 80.22781, "area_id": 1, "address": "Sony Nagar 2nd Street"}, headers={"X-Persona": "bd_executive:ravi"}))
print("  duplicates:", [x["message"] for x in dup.get("duplicates", [])])
# transitions
show("exec tries to approve", c.post(f"{B}/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=R))
show("manager under_review", c.post(f"{B}/properties/{pid}/transition", json={"to_stage": "UNDER_REVIEW"}, headers=M))
show("reject before catchment (refused)", c.post(f"{B}/properties/{pid}/transition", json={"to_stage": "REJECTED", "reason": "x"}, headers=M))
show("request catchment", c.post(f"{B}/properties/{pid}/transition", json={"to_stage": "CATCHMENT_REQUESTED", }, headers=M))
print("history:", [(h["from_stage"], h["to_stage"], h["changed_by"]) for h in c.get(f"{B}/properties/{pid}", headers=M).json()["history"]])
print("PIDs:", pid, dup.get("property_id"))
