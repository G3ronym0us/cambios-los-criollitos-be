from celery import Celery
from celery.schedules import crontab
from app.core.config import settings


celery_app = Celery(
    "tasas-project",
    broker=settings.celery_broker_url_computed,
    backend=settings.celery_result_backend_computed,
    include=[
        "app.tasks.scraping_tasks",
        "app.tasks.bcv_tasks",
        "app.tasks.bank_email_tasks",
        "app.tasks.analysis_digest_tasks",
    ],
)

celery_app.conf.timezone = "UTC"

celery_app.conf.beat_schedule = {
    "scrape-rates-every-1-hour": {
        "task": "app.tasks.scraping_tasks.scrape_exchange_rates",
        "schedule": 3600.0,
    },
    "refresh-bcv-every-5-minutes": {
        "task": "app.tasks.bcv_tasks.refresh_bcv_rate",
        "schedule": 300.0,
    },
    "poll-bank-emails-every-minute": {
        "task": "app.tasks.bank_email_tasks.poll_bank_emails",
        "schedule": 60.0,
    },
    # Una vez al día, 13:00 UTC ≈ 9 de la mañana en Venezuela: el operador lo lee al
    # arrancar el día, no de madrugada. La zona del scheduler es UTC (arriba).
    "analysis-digest-daily": {
        "task": "app.tasks.analysis_digest_tasks.send_analysis_digest",
        "schedule": crontab(hour=13, minute=0),
    },
}


# Compatibilidad con `celery -A app.celery_app worker/beat`
app = celery_app
