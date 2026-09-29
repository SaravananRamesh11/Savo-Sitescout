"""Create PostGIS + all tables. Safe to re-run."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.db import engine
from app.models.db_models import Base

with engine.begin() as c:
    c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
Base.metadata.create_all(engine)
print("tables:", sorted(Base.metadata.tables))
