from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import areas, assignments, geo, properties, reports
from app.core.personas import PERSONAS
from app.core import config
from app.core.db import engine, warm_pool
from app.models.db_models import Base
import app.models.property_models  # noqa: F401
from app.models.property_models import ensure_m2_schema

app = FastAPI(title="Savo SiteScout API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])  # includes X-Persona

@app.on_event("startup")
def startup() -> None:
    if engine is not None:
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        Base.metadata.create_all(engine)
        ensure_m2_schema(engine)
        warm_pool()


@app.get("/api/health")
def health():
    db_ok = False
    if engine is not None:
        try:
            with engine.connect() as c:
                db_ok = c.execute(text("select 1")).scalar() == 1
        except Exception:  # noqa: BLE001
            db_ok = False
    from app.services import llm
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "llm_configured": llm.is_configured(),
            "llm_provider": config.LLM_PROVIDER}


@app.get("/api/personas")
def personas():
    return PERSONAS


app.include_router(areas.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(assignments.router, prefix="/api")
app.include_router(properties.router, prefix="/api")
app.include_router(geo.router, prefix="/api")
