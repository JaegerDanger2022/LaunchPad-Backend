"""
Conversation API — text-based dream-gathering chat

POST /start   – create a LangGraph thread, invoke the conversation graph for
                the greeting, and return session_id + ai_message.
POST /turn    – send one user message; streams the AI reply back as SSE.
                When extraction fires, kicks off the full roadmap workflow via
                POST /api/dreams/create (same call the voice endpoint uses).

SSE event format (POST /turn):
    event: chunk
    data: {"text": "<incremental AI text>"}

    event: done
    data: {"session_id": "…", "conversation_complete": bool, "enriched_context": … | null}
"""

import json
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from core.database import get_db

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Environment – same vars voice_v2.py already uses
# ---------------------------------------------------------------------------

LANGGRAPH_AGENT_URL = os.getenv("LANGGRAPH_AGENT_URL")
LANGGRAPH_API_KEY = os.getenv("LANGGRAPH_API_KEY")
LANGGRAPH_CONVERSATION_ASSISTANT_ID = os.getenv("LANGGRAPH_CONVERSATION_ASSISTANT_ID")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8001")


def _lg_headers() -> dict:
    return {"x-api-key": LANGGRAPH_API_KEY}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ConversationStartRequest(BaseModel):
    user_id: str


class ConversationStartResponse(BaseModel):
    session_id: str
    ai_message: str
    conversation_complete: bool = False


class ConversationTurnRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


# ---------------------------------------------------------------------------
# POST /start
# ---------------------------------------------------------------------------

@router.post("/start", response_model=ConversationStartResponse)
async def conversation_start(req: ConversationStartRequest):
    """
    1. Verify user exists in MongoDB.
    2. Create a LangGraph thread (same helper pattern as voice_v2).
    3. Invoke the "conversation" graph with user_message=None → greeting.
    4. Return session_id + greeting text.
    """
    if not LANGGRAPH_AGENT_URL or not LANGGRAPH_API_KEY or not LANGGRAPH_CONVERSATION_ASSISTANT_ID:
        raise HTTPException(status_code=500, detail="LANGGRAPH_AGENT_URL, LANGGRAPH_API_KEY, or LANGGRAPH_CONVERSATION_ASSISTANT_ID not configured")

    # --- verify user ---
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    user = await db.users.find_one({"user_id": req.user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # --- create thread ---
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            thread_resp = await client.post(
                f"{LANGGRAPH_AGENT_URL}/threads",
                headers=_lg_headers(),
                json={"metadata": {"user_id": req.user_id, "type": "conversation"}},
            )
            thread_resp.raise_for_status()
            thread_id: str = thread_resp.json()["thread_id"]
    except Exception as exc:
        logger.error("[CONVERSATION /start] thread creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to create conversation thread")

    # --- invoke conversation graph (first turn → greeting) ---
    invoke_payload = {
        "assistant_id": LANGGRAPH_CONVERSATION_ASSISTANT_ID,
        "input": {
            "user_id": req.user_id,
            "user_message": None,       # None triggers the greeting branch
            "messages": [],
            "enriched_context": None,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            invoke_resp = await client.post(
                f"{LANGGRAPH_AGENT_URL}/threads/{thread_id}/runs/wait",
                headers=_lg_headers(),
                json=invoke_payload,
            )
            invoke_resp.raise_for_status()
            output = invoke_resp.json().get("output", invoke_resp.json())
    except Exception as exc:
        logger.error("[CONVERSATION /start] invoke failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to start conversation")

    ai_message = _extract_last_ai_text(output)
    logger.info("[CONVERSATION /start] session=%s greeting=%s…", thread_id, ai_message[:60])

    return ConversationStartResponse(
        session_id=thread_id,
        ai_message=ai_message,
    )


# ---------------------------------------------------------------------------
# POST /turn  (SSE streaming)
# ---------------------------------------------------------------------------

@router.post("/turn")
async def conversation_turn(req: ConversationTurnRequest):
    """Stream the AI reply as SSE, then emit a done event."""
    return StreamingResponse(_generate(req), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------

async def _generate(req: ConversationTurnRequest):
    """
    Proxy the LangGraph /conversation/stream SSE back to the frontend.

    LangGraph emits events like:
        event: metadata   – bookkeeping, ignored
        event: updates    – node output; contains the new AI message
        event: end        – stream finished

    We forward AI text as "chunk" events and finish with a single "done" event.
    """
    stream_payload = {
        "assistant_id": LANGGRAPH_CONVERSATION_ASSISTANT_ID,
        "input": {
            "user_id": req.user_id,
            "user_message": req.message,
            "messages": [],              # thread persistence fills this in
            "enriched_context": None,
        },
    }

    enriched_context: Optional[dict] = None

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{LANGGRAPH_AGENT_URL}/threads/{req.session_id}/runs/stream",
                headers=_lg_headers(),
                json=stream_payload,
            ) as resp:
                resp.raise_for_status()

                # Parse the upstream SSE line-by-line
                current_event: Optional[str] = None
                current_data: Optional[str] = None

                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        current_data = line[len("data:"):].strip()
                    elif line == "":
                        # Blank line = end of one SSE event block
                        if current_event and current_data:
                            # Forward chunk events to the frontend
                            for frame in _forward_chunk(current_event, current_data):
                                yield frame

                            # Keep track of the latest updates so we can
                            # check enriched_context after the stream closes
                            if current_event == "updates":
                                enriched_context = _maybe_extract_context(
                                    current_data, enriched_context
                                )

                        current_event = None
                        current_data = None

    except Exception as exc:
        logger.error("[CONVERSATION /turn] stream error: %s", exc, exc_info=True)
        yield _sse("done", json.dumps({
            "session_id": req.session_id,
            "conversation_complete": False,
            "enriched_context": None,
            "error": str(exc),
        }))
        return

    # --- post-stream: if extraction fired, kick off the full roadmap workflow ---
    conversation_complete = enriched_context is not None

    if conversation_complete:
        await _trigger_roadmap_workflow(req.user_id, enriched_context)

    yield _sse("done", json.dumps({
        "session_id": req.session_id,
        "conversation_complete": conversation_complete,
        "enriched_context": enriched_context,
    }))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _forward_chunk(event_name: str, data_str: str):
    """
    If this is an 'updates' event containing an AI message, yield a chunk frame.
    """
    if event_name != "updates":
        return

    try:
        parsed = json.loads(data_str)
        node_output = parsed[-1] if isinstance(parsed, list) else parsed
        conv_out = node_output.get("conversation", node_output)
        msgs = conv_out.get("messages", [])
        if msgs:
            last = msgs[-1]
            content = (
                last.get("content", "")
                if isinstance(last, dict)
                else getattr(last, "content", "")
            )
            if content:
                yield _sse("chunk", json.dumps({"text": content}))
    except (json.JSONDecodeError, KeyError, TypeError):
        pass


def _maybe_extract_context(data_str: str, current: Optional[dict]) -> Optional[dict]:
    """Pull enriched_context out of an updates payload if present."""
    try:
        parsed = json.loads(data_str)
        node_output = parsed[-1] if isinstance(parsed, list) else parsed
        conv_out = node_output.get("conversation", node_output)
        if conv_out.get("enriched_context"):
            return conv_out["enriched_context"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return current


def _sse(event: str, data: str) -> str:
    """Format a single SSE frame (SSE spec requires trailing blank line)."""
    return f"event: {event}\ndata: {data}\n\n"


def _extract_last_ai_text(output: dict) -> str:
    """Pull the text of the last AI message from a LangGraph invoke response."""
    messages = output.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("type", msg.get("role", ""))
            if role in ("ai", "assistant"):
                return msg.get("content", "")
        else:
            if getattr(msg, "type", "") == "ai":
                return getattr(msg, "content", "")
    return ""


async def _trigger_roadmap_workflow(user_id: str, enriched_context: dict):
    """
    Fire off the full roadmap pipeline via POST /api/dreams/create — the exact
    same internal call the voice WebSocket endpoint makes (voice_v2.py:366).
    """
    # Pull real user profile so architect can personalise the roadmap
    db = get_db()
    user = await db.users.find_one({"user_id": user_id}) if db else None
    user_profile = {
        "traits": (user or {}).get("traits", {}),
        "preferences": (user or {}).get("preferences", {}),
    }

    payload = {
        "user_id": user_id,
        "user_request": enriched_context.get("user_dream", ""),
        "user_profile": user_profile,
        "research_data": {},
        "messages": [],
        "roadmap": {},
        "tracks": [],
        "status": "planning",
        "enriched_context": enriched_context,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/dreams/create",
                json=payload,
            )
            resp.raise_for_status()
            logger.info("[CONVERSATION /turn] roadmap workflow triggered for user %s", user_id)
    except Exception as exc:
        # Non-fatal — conversation already completed successfully for the user.
        logger.error("[CONVERSATION /turn] roadmap trigger failed: %s", exc, exc_info=True)
