from __future__ import annotations

from celery import Celery
from core.config import get_settings

settings= get_settings()

broker_url = (
    settings.BROKER.CELERY_BROKER_URL
    or settings.RABBITMQ.URL
    or "amqp://quest:quest@localhost:5672//"
)

result_backend = (
    settings.BROKER.CELERY_RESULT_BACKEND
    or settings.REDIS.URL
    or "redis://localhost:6379/0"
)

celery = Celery(
    "auth_service",
    broker=broker_url,
    backend=result_backend
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True
)