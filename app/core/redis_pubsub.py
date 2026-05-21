import json
import redis.asyncio as aioredis
from app.core.config import settings

RATE_ALERTS_CHANNEL = "rate_alerts"


async def publish_alert(alert_data: dict) -> None:
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await client.publish(RATE_ALERTS_CHANNEL, json.dumps(alert_data, default=str))
    finally:
        await client.aclose()


async def subscribe_alerts():
    """Async generator that yields alert dicts from the Redis pub/sub channel."""
    client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(RATE_ALERTS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(RATE_ALERTS_CHANNEL)
        await client.aclose()
