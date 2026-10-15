from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy import Session, sessionmaker

from core.config import get_settings
from db.base import Base


settings = get_settings()

engine = create_engine(settings.DB.dsn, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db() -> None:
    Base.metadata.create_all(bind=engine)