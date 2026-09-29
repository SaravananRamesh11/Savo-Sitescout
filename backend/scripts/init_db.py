"""Create PostGIS + all tables. Safe to re-run."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.core.db import engine
from app.models.db_models import Base
import app.models.property_models  # noqa: F401  registers the M2 tables on the same Base

with engine.begin() as c:
    c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
Base.metadata.create_all(engine)
from app.models.property_models import ensure_m2_schema
ensure_m2_schema(engine)
from app.models.survey_models import ensure_m3_schema
ensure_m3_schema(engine)
print("tables:", sorted(Base.metadata.tables))
