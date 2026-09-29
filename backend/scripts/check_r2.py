"""Checks R2 connectivity: put -> presigned GET -> anonymous GET is denied -> delete. Never prints secrets."""
import sys, pathlib, uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import httpx
from app.services import storage

print("backend:", storage.backend_name())
if not storage.r2_configured():
    sys.exit("R2 variables are not all set (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET)")
key = f"healthcheck/{uuid.uuid4().hex}.txt"
storage.put(key, b"savo-sitescout r2 check", "text/plain")
print("put: ok")
url = storage.url_for(key)
r = httpx.get(url, timeout=20)
print("presigned GET:", r.status_code, "(expect 200)", "| body ok:", r.content == b"savo-sitescout r2 check")
raw = url.split("?")[0]
r2 = httpx.get(raw, timeout=20)
print("anonymous GET of raw object URL:", r2.status_code, "(expect 400/401/403 = private)")
storage.delete(key)
print("delete: ok")
