"""Celery application configuration and beat schedule."""

from datetime import timedelta

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "automate",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.pipeline",
        "app.tasks.analytics_tasks",
    ],
)

celery_app.conf.update(
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Task execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Prevent task redelivery during long-running tasks
    broker_transport_options={
        "visibility_timeout": 3600,  # 1 hour
    },
    # Result expiry
    result_expires=86400,  # 24 hours
    # Beat schedule — IMPORTANT: Use timedelta, NOT crontab for true intervals
    # crontab(minute='*/55') would run at :00 and :55, not every 55 minutes
    beat_schedule={
        "content-pipeline-every-55-min": {
            "task": "app.tasks.pipeline.run_content_pipeline",
            "schedule": timedelta(minutes=settings.posting_interval_minutes),
            "options": {"queue": "pipeline"},
        },
        "collect-engagement-metrics-every-6h": {
            "task": "app.tasks.analytics_tasks.collect_engagement_metrics",
            "schedule": timedelta(hours=6),
            "options": {"queue": "analytics"},
        },
        "update-topic-weights-daily": {
            "task": "app.tasks.analytics_tasks.update_topic_weights",
            "schedule": timedelta(hours=24),
            "options": {"queue": "analytics"},
        },
    },
    # Task routing
    task_routes={
        "app.tasks.pipeline.*": {"queue": "pipeline"},
        "app.tasks.analytics_tasks.*": {"queue": "analytics"},
    },
    # Default queue
    task_default_queue="default",
)
