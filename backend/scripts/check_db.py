"""Connects using the env var and creates PostGIS. Never prints the URL."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.db import engine

if engine is None:
    sys.exit("db_url not set")
with engine.begin() as c:
    print("connected:", c.execute(text("select 1")).scalar() == 1)
    try:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        print("postgis:", c.execute(text("select postgis_version()")).scalar())
    except Exception as e:  # noqa: BLE001
        print("postgis FAILED:", type(e).__name__, str(e).splitlines()[0])
