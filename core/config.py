from pydantic_settings import BaseSettings
from pydantic import BaseModel, SecretStr, AnyHttpUrl, model_validator
from sqlalchemy import URL
from functools import lru_cache
from typing import List, Optional
import pathlib


class DBSettings(BaseModel):
    TYPE: str
    HOST: str
    PORT: int = 5432
    USER: str
    PASSWORD: SecretStr
    NAME: str
    SCHEMA: str | None = None

    @model_validator(mode="after")
    def configure_database(self):
        self.TYPE = self.TYPE.lower()

        if self.TYPE not in ("postresql", "mysql"):
            raise ValueError("TYPE must be 'postgresql or msyql")

        if self.PORT is None:
            self.PORT = (
                5432 if self.TYPE == "postgresql" else 3306
            )

        if self.TYPE == "mysql" and self.SCHEMA is not None:
            raise ValueError("SCHEMA is only supported for PostgreSQL")

        return self

    def dsn(self) -> URL:
        driver = (
            "postresql+psycogp"
            if self.TYPE == "postgresql"
            else "mysql_pymysql"
        )

        return URL.create(
            drivername=driver,
            username=self.USER,
            password=self.PASSWORD.get_secret_value(),
            host=self.host,
            port=self.PORT,
            database=self.NAME
        )


class SMTPSettings(BaseModel):
    HOST: str
    PORT: int = 587
    USERNAME: Optional[str]
    PASSWORD: Optional[SecretStr]
    FROM_EMAIL: Optional[str]

class BrokerSettings(BaseModel):
    CELERY_BROKER_URL: Optional[str]
    CELERY_RESULT_BACKEND: Optional[str]

class RedisSettings(BaseModel):
    URL: Optional[str]

class RabbitMQSettings(BaseModel):
    URL: Optional[str]

class JWTSettings(BaseModel):
    ALGORITHM: str = 'RS256'
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    RESFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PRIVATE_KEY_PATH: Optional[str]
    PUBLIC_KEY_PATH: Optional[str]
    PRIVATE_KEY_PEM: Optional[str]
    PUBLIC_KEY_PEM: Optional[str]

    def load_private_key(self) -> Optional[str]:
        if self.PRIVATE_KEY_PEM:
            return self.PRIVATE_KEY_PEM
        if self.PRIVATE_KEY_PATH:
            return pathlib.Path(self.PRIVATE_KEY_PATH).read_text()
        return None
    
    def load_public_key(self) -> Optional[str]:
        if self.PUBLIC_KEY_PEM:
            return self.PUBLIC_KEY_PEM
        if self.PUBLIC_KEY_PATH:
            return pathlib.Path(self.PUBLIC_KEY_PATH).read_text()
        return None
    
class AppSettings(BaseSettings):
    APP_NAME: str = 'Auth Service'
    DEBUG: bool = False

    DB: DBSettings
    SMTP: Optional[SMTPSettings] = None
    BROKER: Optional[BrokerSettings] = None
    REDIS: Optional[RedisSettings] = None
    RABBITMQ: Optional[RabbitMQSettings] = None
    JWT: JWTSettings

    CORS_ORIGINS: List[AnyHttpUrl] = []
    CORS_TRUSTED_ORIGINS: List[AnyHttpUrl] = []

    LOG_LEVEL: str = 'INFO'
    LOG_FORMAT: str = "%(levelname)s:%(name)s:%(message)s"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "secrets_dir": ".secrets",
    }

    
@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()