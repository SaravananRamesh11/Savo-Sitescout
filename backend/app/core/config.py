"""Central configuration. Everything comes from environment variables (never hard-coded).

`.env` is loaded by python-dotenv for local runs; real values are never committed
(see /.env.example for the variable names).
"""
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))
load_dotenv()  # also honour a backend/.env or real environment


def _db_url() -> str:
    url = os.getenv("db_url") or os.getenv("DATABASE_URL") or ""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _db_url()

# --- Savomart internal Stores API (token only via env) ---
SAVOMART_STORES_URL = os.getenv(
    "SAVOMART_STORES_URL", "https://internal-service.savomart.in/bridge/api/store/list"
)
SAVOMART_CRON_TOKEN = os.getenv("SAVOMART_CRON_TOKEN", "")
STORES_TTL_HOURS = int(os.getenv("STORES_TTL_HOURS", "12"))

# --- Overpass / Nominatim ---
OVERPASS_ENDPOINTS = [
    e.strip()
    for e in os.getenv(
        "OVERPASS_ENDPOINTS",
        "https://overpass-api.de/api/interpreter,https://overpass.private.coffee/api/interpreter",
    ).split(",")
    if e.strip()
]
OVERPASS_TIMEOUT_S = int(os.getenv("OVERPASS_TIMEOUT_S", "90"))
OVERPASS_CACHE_HOURS = int(os.getenv("OVERPASS_CACHE_HOURS", "24"))
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
USER_AGENT = os.getenv("HTTP_USER_AGENT", "SavoSiteScout-hackathon/0.1 (saravananramesh102002@gmail.com)")

# --- LLM (provider is swappable via config) ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()  # anthropic | openai | none
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # for OpenAI-compatible providers (Groq, Gemini, Ollama...)

CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o]

# --- Grid / analysis limits ---
CELL_SIZE_M = 500
MAX_GRID_CELLS = 100
MAX_AREA_KM2 = float(os.getenv("MAX_AREA_KM2", "60"))
UTM_EPSG = 32644  # UTM zone 44N covers Chennai (80.2E)
HOTSPOT_COUNT = 5

# --- Cloudflare R2 photo storage (private bucket + presigned URLs). All values come from the environment. ---
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "")
R2_SIGNED_URL_TTL_S = int(os.getenv("R2_SIGNED_URL_TTL_S", "3600"))
MAX_PHOTO_BYTES = int(os.getenv("MAX_PHOTO_BYTES", str(8 * 1024 * 1024)))
