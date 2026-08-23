from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import env_value


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

engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
