import asyncio
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.dependencies import get_current_active_user, get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.repositories.rate_alert_repository import RateAlertRepository
from app.schemas.notification import RateAlertOut, RateAlertList
from app.core.redis_pubsub import RATE_ALERTS_CHANNEL

import redis.asyncio as aioredis
from app.core.config import settings

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _sse_generator(current_user: User) -> AsyncGenerator[str, None]:
    """Subscribes to Redis and streams rate divergence alerts as SSE."""
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(RATE_ALERTS_CHANNEL)

    # Send a connection acknowledgment event
    yield f"event: connected\ndata: {json.dumps({'user_id': current_user.id})}\n\n"

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30)
            if message and message["type"] == "message":
                yield f"data: {message['data']}\n\n"
            else:
                # Send a keepalive comment so the connection stays open
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(RATE_ALERTS_CHANNEL)
        await client.aclose()


@router.get("/stream")
async def notification_stream(
    current_user: User = Depends(get_moderator_user),
):
    """
    SSE endpoint — streams real-time rate divergence alerts to the frontend.
    Connect with EventSource; auth via cookie or ?token= query param.
    """
    return StreamingResponse(
        _sse_generator(current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables nginx buffering
        },
    )


@router.get("/alerts", response_model=RateAlertList)
def get_alerts(
    limit: int = 50,
    unacknowledged_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Return recent rate divergence alerts."""
    repo = RateAlertRepository(db)
    alerts = repo.get_recent(limit=limit, only_unacknowledged=unacknowledged_only)
    return RateAlertList(alerts=alerts, total=len(alerts))


@router.post("/alerts/{alert_uuid}/acknowledge", response_model=RateAlertOut)
def acknowledge_alert(
    alert_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Mark a rate alert as acknowledged."""
    repo = RateAlertRepository(db)
    alert = repo.acknowledge(alert_uuid)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert
