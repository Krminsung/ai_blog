"""Celery worker configuration; domain tasks are registered by later stages."""

from celery import Celery

from blogops.core.config import get_settings

settings = get_settings()
celery_app = Celery("blogops", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    result_expires=86_400,
    imports=(
        "blogops.domain.generation.tasks",
        "blogops.domain.knowledge.tasks",
        "blogops.domain.keywords.tasks",
        "blogops.domain.research.tasks",
    ),
)
