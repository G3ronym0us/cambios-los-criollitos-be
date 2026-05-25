from celery import Celery
from app.core.config import settings


celery_app = Celery(
    "tasas-project",
    broker=settings.celery_broker_url_computed,
    backend=settings.celery_result_backend_computed,
    include=[
        "app.tasks.scraping_tasks",
        "app.tasks.bcv_tasks",
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
}


# Compatibilidad con `celery -A app.celery_app worker/beat`
app = celery_app
