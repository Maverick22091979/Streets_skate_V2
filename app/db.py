from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import env_int, env_value


def _database_url() -> str:
    direct = env_value("DATABASE_URL", "")
    if direct:
        return direct
    user = env_value("POSTGRES_USER", "street_skate")
    password = env_value("POSTGRES_PASSWORD", "street_skate")
    host = env_value("DB_HOST", "db")
    port = env_value("DB_PORT", "5432")
    name = env_value("POSTGRES_DB", "street_skate")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


DB_URL = _database_url()

_engine_options = {"future": True, "pool_pre_ping": True}
if not DB_URL.startswith("sqlite"):
    _engine_options.update(
        pool_size=max(1, env_int("DATABASE_POOL_SIZE", 5)),
        max_overflow=max(0, env_int("DATABASE_MAX_OVERFLOW", 3)),
        pool_timeout=max(1, env_int("DATABASE_POOL_TIMEOUT_SEC", 10)),
        pool_recycle=max(60, env_int("DATABASE_POOL_RECYCLE_SEC", 1800)),
    )
engine = create_engine(DB_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
