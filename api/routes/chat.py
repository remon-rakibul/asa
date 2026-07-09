"""Chat endpoints: drive the LangGraph agent over HTTP, SSE, and WebSocket."""

from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from agent.runner import run_turn, stream_turn_tokens

from ..deps import resolve_channel_clinic
from ..schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])

# Session IDs must be alphanumeric + hyphens/underscores, 8–128 chars.
# This prevents clients from using arbitrary strings to hijack other sessions.
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,128}$")


def _validate_session(session_id: str) -> str:
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(
            status_code=400,
            detail="session_id must be 8–128 alphanumeric characters (hyphens/underscores allowed). "
                   "Use POST /chat/session to generate a valid ID.",
        )
    return session_id


async def _clinic_for(req: ChatRequest) -> int:
    return await resolve_channel_clinic("web", req.clinic_slug or "default")


@router.post("/session")
async def new_session() -> dict:
    """Generate a server-side session ID.

    Clients SHOULD use this rather than self-generating IDs. Using predictable
    or sequential session IDs allows one user to read or inject into another
    user's conversation state.
    """
    return {"session_id": uuid.uuid4().hex}


@router.post("/start", response_model=ChatResponse)
async def start(req: ChatRequest, request: Request) -> ChatResponse:
    """Open a new call: returns the agent's greeting."""
    _validate_session(req.session_id)
    clinic_id = await _clinic_for(req)
    result = await run_turn(
        request.app.state.graph, req.session_id, req.message or "", clinic_id
    )
    return ChatResponse(**result)


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    """Send one patient message; get the agent's Bangla reply."""
    _validate_session(req.session_id)
    clinic_id = await _clinic_for(req)
    result = await run_turn(request.app.state.graph, req.session_id, req.message, clinic_id)
    return ChatResponse(**result)


@router.post("/stream")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """SSE streaming chat — streams token events as the LLM generates them.

    Event format:
      data: {"type": "token", "text": "..."}
      data: {"type": "end",   "phase": "...", "done": true/false, ...}
    """
    _validate_session(req.session_id)
    graph = request.app.state.graph
    clinic_id = await _clinic_for(req)

    async def event_generator():
        async for event in stream_turn_tokens(graph, req.session_id, req.message, clinic_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket) -> None:
    """Streaming chat over WebSocket.

    Client sends {session_id, message}; server streams token events then a
    final {type: 'end', ...}.
    """
    await websocket.accept()
    graph = websocket.app.state.graph
    try:
        while True:
            data = await websocket.receive_json()
            session_id = data.get("session_id", "")
            if not _SESSION_ID_RE.match(session_id):
                await websocket.send_json(
                    {"type": "error", "detail": "Invalid session_id format"}
                )
                continue
            message = data.get("message", "")
            clinic_id = await resolve_channel_clinic("web", data.get("clinic_slug") or "default")
            async for event in stream_turn_tokens(graph, session_id, message, clinic_id):
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return
