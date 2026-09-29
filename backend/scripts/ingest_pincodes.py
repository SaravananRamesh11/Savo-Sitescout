"""Build backend/data/chennai_pincodes.geojson.

The OGD India pincode-boundary GeoJSON (data.gov.in) cannot be fetched without a
manual download, and OSM only has a coarse '600' postal_code relation for Chennai.
So we do this (documented in the README as an approximation):

1. For each pincode 600001..600130 ask Nominatim (1 req/s, per usage policy) for its
   centroid.
2. Build a Voronoi diagram of the centroids, clipped to the buffered hull of all
   centroids -> one polygon per pincode. Boundaries are APPROXIMATE.

If you have the real OGD boundary file, drop it at backend/data/chennai_pincodes.geojson
with a `pincode` property on every feature and skip this script.

Run:  python scripts/ingest_pincodes.py
"""
import json
import time
from pathlib import Path

import httpx
from pyproj import Transformer
from shapely.geometry import MultiPoint, Point, mapping
from shapely.ops import transform, voronoi_diagram

OUT = Path(__file__).resolve().parents[1] / "data" / "chennai_pincodes.geojson"
UA = "SavoSiteScout-hackathon/0.1 (saravananramesh102002@gmail.com)"
to_utm = Transformer.from_crs(4326, 32644, always_xy=True).transform
to_ll = Transformer.from_crs(32644, 4326, always_xy=True).transform


def fetch_centroids() -> dict[str, dict]:
    found: dict[str, dict] = {}
    with httpx.Client(headers={"User-Agent": UA}, timeout=30) as c:
        for pin in range(600001, 600131):
            try:
                r = c.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"postalcode": str(pin), "countrycodes": "in", "format": "jsonv2", "limit": 1},
                )
                rows = r.json()
            except Exception as exc:  # noqa: BLE001
                print(pin, "error", exc)
                rows = []
            if rows:
                lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
                # keep only points that are plausibly in Chennai
                if 12.8 <= lat <= 13.3 and 79.9 <= lon <= 80.4:
                    found[str(pin)] = {"lat": lat, "lon": lon, "label": rows[0].get("display_name", "")}
                    print(pin, lat, lon)
            time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
    return found


def main() -> None:
    cents = fetch_centroids()
    pts = {p: Point(*to_utm(v["lon"], v["lat"])) for p, v in cents.items()}
    hull = MultiPoint(list(pts.values())).convex_hull.buffer(2500)
    diagram = voronoi_diagram(MultiPoint(list(pts.values())), envelope=hull)
    feats = []
    for cell in diagram.geoms:
        cell = cell.intersection(hull)
        for pin, pt in pts.items():
            if cell.contains(pt):
                feats.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "pincode": pin,
                            "label": cents[pin]["label"],
                            "centroid": [cents[pin]["lon"], cents[pin]["lat"]],
                            "approximate": True,
                        },
                        "geometry": mapping(transform(to_ll, cell)),
                    }
                )
                break
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"wrote {len(feats)} pincodes -> {OUT}")


if __name__ == "__main__":
    main()
