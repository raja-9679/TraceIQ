from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
from app.core.redis import RedisClient
from app.core.database import async_session_factory
from app.core.auth import _user_from_jwt
from app.models import TestRun
from app.services.access_service import access_service

router = APIRouter()


#: Subprotocol the browser offers alongside the JWT: `new WebSocket(url,
#: [BEARER_SUBPROTOCOL, token])`. The server must echo it back on accept.
BEARER_SUBPROTOCOL = "traceiq.bearer"


def token_from_subprotocols(header: Optional[str]) -> Optional[str]:
    """The JWT from a `Sec-WebSocket-Protocol: traceiq.bearer, <jwt>` header.

    Browsers can't set an Authorization header on a WebSocket. A query param
    works but lands in every access log (uvicorn's, nginx's, any proxy's) —
    a live credential in plain text. The subprotocol header is the standard
    way to carry it instead; nothing logs it.
    """
    if not header:
        return None
    parts = [p.strip() for p in header.split(",") if p.strip()]
    if len(parts) >= 2 and parts[0] == BEARER_SUBPROTOCOL:
        return parts[1]
    return None


@router.websocket("/ws/runs/{run_id}")
async def websocket_endpoint(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    # Authenticate and authorize BEFORE accepting: only users with access to
    # the run's project may watch its live progress. The JWT comes in the
    # subprotocol header; `?token=` is still accepted from older clients (its
    # value is redacted from our logs, see core/telemetry.py).
    header_token = token_from_subprotocols(websocket.headers.get("sec-websocket-protocol"))
    subprotocol = BEARER_SUBPROTOCOL if header_token else None
    token = header_token or token
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

    await websocket.accept(subprotocol=subprotocol)
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
