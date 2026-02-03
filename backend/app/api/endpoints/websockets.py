from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.redis import RedisClient
import asyncio
import json

router = APIRouter()

@router.websocket("/ws/runs/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: int):
    await websocket.accept()
    redis = RedisClient.get_instance()
    pubsub = redis.pubsub()
    channel = f"run:{run_id}"
    await pubsub.subscribe(channel)
    
    try:
        # Check initial messages or just wait for updates
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        # Client disconnect
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
