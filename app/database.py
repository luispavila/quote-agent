from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


def _normalize_url(url: str) -> str:
    # Render dá "postgresql://..."; SQLAlchemy precisa do driver psycopg3.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


def _engine_options(url: str) -> dict:
    return {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}


settings = get_settings()
_db_url = _normalize_url(settings.database_url)
engine = create_engine(_db_url, pool_pre_ping=True, **_engine_options(_db_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_schema() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
