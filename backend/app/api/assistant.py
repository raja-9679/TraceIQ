"""In-app assistant API (WP14a, E20, decision W1).

    GET    /api/assistant/status?workspace_id=           enabled? (and why not)
    POST   /api/assistant/conversations                  {workspace_id, project_id?, title?}
    GET    /api/assistant/conversations?workspace_id=    the caller's own threads
    GET    /api/assistant/conversations/{id}             one thread with its messages
    DELETE /api/assistant/conversations/{id}
    POST   /api/assistant/conversations/{id}/messages    {text, workspace_id, project_id?}

Conversations belong to one user in one workspace and are invisible to
everyone else (404, not 403, so ids are no oracle). The scope a question may
read is recomputed on every message with the same rule as the project pages
(`accessible_projects`: project access per project; an API key is narrowed to
its own workspace and project). Answers are re-filtered on read: one that used
a project the reader can no longer access comes back hidden.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.reports import _workspace, accessible_projects
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.limiter import limiter
from app.models import AssistantConversation, AssistantMessage
from app.services import assistant as svc

router = APIRouter()

MAX_CONVERSATIONS = 50


class ConversationCreate(BaseModel):
    workspace_id: int
    project_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=80)


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=svc.MAX_QUESTION_CHARS)
    workspace_id: int
    project_id: Optional[int] = None


async def scope_for(session: AsyncSession, principal: AuthPrincipal,
                    workspace_id: int) -> Dict[int, str]:
    """{project_id: name} the caller may read in this workspace."""
    if principal.api_key is not None and principal.api_key.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="API key belongs to another workspace")
    await _workspace(session, principal, workspace_id)
    return {p.id: p.name for p in await accessible_projects(session, principal, workspace_id)}


def _focus(scope: Dict[int, str], project_id: Optional[int]) -> Optional[int]:
    if project_id is not None and project_id not in scope:
        raise HTTPException(status_code=403, detail="Access denied to this project")
    return project_id


async def _own(session: AsyncSession, principal: AuthPrincipal,
               conversation_id: int) -> AssistantConversation:
    conv = await session.get(AssistantConversation, conversation_id)
    if conv is None or conv.user_id != principal.user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if principal.api_key is not None and principal.api_key.workspace_id != conv.workspace_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


def _conv_out(c: AssistantConversation) -> Dict[str, Any]:
    return {"id": c.id, "workspace_id": c.workspace_id, "project_id": c.project_id,
            "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at}


@router.get("/assistant/status")
async def assistant_status(workspace_id: int = Query(...),
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    await scope_for(session, principal, workspace_id)
    ok, reason = await svc.assistant_allowed(session, workspace_id)
    return {"enabled": ok, "reason": reason,
            "message": None if ok else svc.TEXT.get(reason or "disabled"),
            "disclaimer": svc.DISCLAIMER, "max_tool_steps": svc.MAX_TOOL_STEPS,
            "retention_days": svc.RETENTION_DAYS}


@router.post("/assistant/conversations", status_code=201)
async def create_conversation(body: ConversationCreate,
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    scope = await scope_for(session, principal, body.workspace_id)
    conv = AssistantConversation(user_id=principal.user.id, workspace_id=body.workspace_id,
                                 project_id=_focus(scope, body.project_id),
                                 title=(body.title or "").strip() or "New conversation")
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return _conv_out(conv)


@router.get("/assistant/conversations")
async def list_conversations(workspace_id: int = Query(...),
                             session: AsyncSession = Depends(get_session),
                             principal: AuthPrincipal = Depends(get_current_principal)) -> List[Dict[str, Any]]:
    await scope_for(session, principal, workspace_id)
    rows = (await session.exec(
        select(AssistantConversation)
        .where(AssistantConversation.user_id == principal.user.id,
               AssistantConversation.workspace_id == workspace_id,
               AssistantConversation.updated_at >= svc.retention_cutoff())
        .order_by(AssistantConversation.updated_at.desc())
        .limit(MAX_CONVERSATIONS))).all()
    return [_conv_out(c) for c in rows]


@router.get("/assistant/conversations/{conversation_id}")
async def get_conversation(conversation_id: int,
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    conv = await _own(session, principal, conversation_id)
    scope = await scope_for(session, principal, conv.workspace_id)
    msgs = (await session.exec(
        select(AssistantMessage)
        .where(AssistantMessage.conversation_id == conv.id,
               AssistantMessage.user_id == principal.user.id,
               AssistantMessage.created_at >= svc.retention_cutoff())
        .order_by(AssistantMessage.id))).all()
    return {**_conv_out(conv), "disclaimer": svc.DISCLAIMER,
            "messages": [svc.present_message(m, list(scope)) for m in msgs]}


@router.delete("/assistant/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int,
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    conv = await _own(session, principal, conversation_id)
    await session.exec(sa_delete(AssistantMessage).where(
        AssistantMessage.conversation_id == conv.id))
    await session.delete(conv)
    await session.commit()
    return {"status": "deleted", "id": conversation_id}


@router.post("/assistant/conversations/{conversation_id}/messages")
@limiter.limit("20/minute")
async def post_message(request: Request, conversation_id: int, body: MessageCreate,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)) -> Dict[str, Any]:
    conv = await _own(session, principal, conversation_id)
    if body.workspace_id != conv.workspace_id:
        raise HTTPException(status_code=422, detail="workspace_id does not match the conversation")
    scope = await scope_for(session, principal, conv.workspace_id)
    focus = _focus(scope, body.project_id if body.project_id is not None else
                   (conv.project_id if conv.project_id in scope else None))
    answer = await svc.ask(session, user_id=principal.user.id, conversation=conv,
                           question=body.text, allowed_projects=scope, focus_project_id=focus)
    return {"conversation": _conv_out(conv), "disclaimer": svc.DISCLAIMER,
            "message": svc.present_message(answer, list(scope))}
