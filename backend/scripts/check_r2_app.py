"""Proves photo uploads go to R2 THROUGH THE APP (running API on :8000), not just through the storage module.

Creates a throw-away draft property, uploads a real JPEG via POST /api/properties/{id}/photos, then checks that the
object exists in the bucket, that the signed URL returned by the API loads, and that the raw URL is private.
Cleans up the object and the test rows. Never prints credentials.
"""
import sys, pathlib, struct, zlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import httpx
from sqlalchemy import text
from app.core import config
from app.core.db import get_session
from app.services import storage

B = "http://localhost:8000/api"
H = {"X-Persona": "bd_executive:ravi"}


def png_bytes() -> bytes:  # a real, valid 8x8 PNG so no image library is needed
    raw = b"".join(b"\x00" + b"\x77\x2b\x90" * 8 for _ in range(8))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


print("storage backend seen by this script:", storage.backend_name())
c = httpx.Client(timeout=90)
h = c.get(f"{B}/health").json()
print("api health:", h["status"])
p = c.post(f"{B}/properties", headers=H, json={"lat": 12.98632, "lon": 80.22776, "area_id": 1,
                                                 "address": "R2 check (temporary)", "locality": "Velachery"})
p.raise_for_status()
pid = p.json()["property_id"]
db = get_session()
key = None
try:
    up = c.post(f"{B}/properties/{pid}/photos", headers=H, data={"photo_type": "front_view"},
                files={"file": ("check.png", png_bytes(), "image/png")})
    print("upload via API:", up.status_code)
    up.raise_for_status()
    url = up.json()["url"]
    print("API returned a", "signed R2 URL" if "X-Amz-Signature" in url else "non-R2 URL (local path)")
    key = db.execute(text("select storage_key from property_photos where property_id=:p"), {"p": pid}).scalar()
    print("db stores only the key:", key)
    if storage.r2_configured():
        head = storage._r2().head_object(Bucket=config.R2_BUCKET, Key=key)
        print("object exists in the bucket:", head["ContentLength"], "bytes,", head["ContentType"])
    r = httpx.get(url, timeout=30)
    print("signed URL loads:", r.status_code, r.headers.get("content-type"), len(r.content), "bytes")
    raw = httpx.get(url.split("?")[0], timeout=30)
    print("raw object URL without signature:", raw.status_code, "(400/401/403 = private)")
finally:
    if key:
        storage.delete(key)
        if storage.r2_configured():
            try:
                storage._r2().head_object(Bucket=config.R2_BUCKET, Key=key)
                print("cleanup: OBJECT STILL PRESENT")
            except Exception:
                print("cleanup: test object removed from the bucket")
    db.execute(text("delete from property_photos where property_id=:p"), {"p": pid})
    db.execute(text("delete from property_status_history where property_id=:p"), {"p": pid})
    db.execute(text("delete from properties where id=:p"), {"p": pid})
    db.commit()
    db.close()
    print("cleanup: test property removed")
