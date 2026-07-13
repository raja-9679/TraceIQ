from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
from app.core.redis import RedisClient
from app.core.database import async_session_factory
from app.core.auth import _user_from_jwt
from app.models import TestRun
from app.services.access_service import access_service

router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    # Browsers can't set headers on a WebSocket, so the JWT arrives as a
    # query param. Authenticate and authorize BEFORE accepting: only users
    # with access to the run's project may watch its live progress.
    if not token:
        await websocket.close(code=4401)
        return
    async with async_session_factory() as session:
        user = await _user_from_jwt(token, session)
        if not user:
            await websocket.close(code=4401)
            return
        run = await session.get(TestRun, run_id)
        if not run:
            await websocket.close(code=4404)
            return
        if not await access_service.has_project_access(user.id, run.project_id, session):
            await websocket.close(code=4403)
            return

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
