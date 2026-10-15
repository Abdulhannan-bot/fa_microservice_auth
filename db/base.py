from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase
from core.config import get_settings

settings = get_settings()
schema = settings.DB.SCHEMA

metadata = MetaData(schema=schema) if schema else MetaData()

class Base(DeclarativeBase):
    metadata = metadata