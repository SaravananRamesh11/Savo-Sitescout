from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import DB_URL

# The managed Postgres is remote: opening a connection costs seconds, so keep a warm pool.
engine = (
    create_engine(DB_URL, pool_pre_ping=True, pool_size=10, max_overflow=10, pool_recycle=300,
                  connect_args={"connect_timeout": 30})
    if DB_URL else None
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False) if engine is not None else None


def get_session():
    if SessionLocal is None:
        raise RuntimeError("Database URL not configured: set db_url (or DATABASE_URL) in the environment")
    return SessionLocal()


def get_db():
    s = get_session()
    try:
        yield s
    finally:
        s.close()


def warm_pool(n: int = 6) -> None:
    """Open n connections in parallel at startup so the first requests don't pay the connect cost."""
    if engine is None:
        return
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    def _one(_):
        with engine.connect() as c:
            c.execute(text("select 1"))
            import time
            time.sleep(0.5)  # hold so each thread really uses a distinct connection

    with ThreadPoolExecutor(n) as ex:
        list(ex.map(_one, range(n)))
