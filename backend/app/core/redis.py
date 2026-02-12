from redis.asyncio import Redis
from app.core.config import settings

class RedisClient:
    _client: Redis = None

    @classmethod
    def get_instance(cls) -> Redis:
        if cls._client is None:
            # Create connection (lazy)
            cls._client = Redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
        return cls._client

    @classmethod
    async def close(cls):
        if cls._client:
            await cls._client.close()
