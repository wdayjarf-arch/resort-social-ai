from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "resort_social",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.default_timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


celery_app.conf.beat_schedule = {
    "requeue-pending-meta-webhooks-every-minute": {
        "task": "app.meta.requeue_pending_webhooks",
        "schedule": 60.0,
    },
    "publish-due-scheduled-posts-every-minute": {
        "task": "app.scheduler.publish_due_posts",
        "schedule": 60.0,
    },
    "requeue-approved-replies-every-minute": {
        "task": "app.inbox.requeue_approved_replies",
        "schedule": 60.0,
    },
    "sync-meta-insights-hourly": {
        "task": "app.analytics.sync_meta_insights",
        "schedule": 3600.0,
    },
    "summarize-customer-profiles-every-six-hours": {
        "task": "app.crm.summarize_customer_profiles",
        "schedule": 21600.0,
    },
    "recover-stuck-publications-every-five-minutes": {
        "task": "app.scheduler.recover_stuck_publications",
        "schedule": 300.0,
    },
}
