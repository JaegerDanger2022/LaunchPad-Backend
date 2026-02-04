"""
Conversation API — text-based dream-gathering chat

POST /start   – create a LangGraph thread, invoke the conversation graph for
                the greeting, and return session_id + ai_message.
POST /turn    – send one user message; streams the AI reply back as SSE.
                When extraction fires, kicks off the full roadmap workflow via
                POST /api/dreams/create.

SSE event format (POST /turn):
    event: chunk
    data: {"text": "<incremental AI text>"}

    event: done
    data: {"session_id": "…", "conversation_complete": bool, "enriched_context": … | null}
"""

import asyncio
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
from api.dreams import create_dream, CreateDreamRequest

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

LANGGRAPH_AGENT_URL = os.getenv("LANGGRAPH_AGENT_URL")
LANGGRAPH_API_KEY = os.getenv("LANGGRAPH_API_KEY")
LANGGRAPH_CONVERSATION_ASSISTANT_ID = os.getenv("LANGGRAPH_CONVERSATION_ASSISTANT_ID")


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
    return StreamingResponse(
        _generate(req),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------

async def _generate(req: ConversationTurnRequest):
    """
    Proxy the LangGraph /conversation/stream SSE back to the frontend.

    LangGraph emits events like:
        event: metadata   – bookkeeping, ignored
        event: values     – full graph state snapshot; contains messages array
        event: end        – stream finished (not always present)

    We forward new AI text as "chunk" events and finish with a single "done" event.
    When the conversation is complete the roadmap workflow is fired via
    asyncio.create_task so it runs on the event loop independently of the
    response lifecycle.
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
    # LangGraph stream order is always: metadata → values (existing state) →
    # values (new AI message appended).  The first values after metadata is
    # the thread state *before* this run's AI node fires — seed seen_ai_ids
    # from it so we never re-forward old messages.
    seen_ai_ids: set = set()
    first_values_seen = False

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{LANGGRAPH_AGENT_URL}/threads/{req.session_id}/runs/stream",
                headers=_lg_headers(),
                json=stream_payload,
            ) as resp:
                resp.raise_for_status()

                current_event: Optional[str] = None
                current_data: Optional[str] = None

                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        current_data = line[len("data:"):].strip()
                    elif line == "":
                        if current_event and current_data:
                            if current_event == "values":
                                if not first_values_seen:
                                    # First values = state before AI responds.
                                    # Record every AI id so we skip them later.
                                    _seed_seen_ids(current_data, seen_ai_ids)
                                    first_values_seen = True
                                else:
                                    # Subsequent values: forward only new AI msgs
                                    for frame in _forward_chunk(current_event, current_data, seen_ai_ids):
                                        yield frame
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

    # --- fire roadmap before the final yield ---
    # asyncio.create_task schedules the coroutine on the running event loop;
    # it will execute regardless of when the client disconnects or the
    # StreamingResponse generator is garbage-collected.
    conversation_complete = enriched_context is not None
    if conversation_complete:
        asyncio.create_task(_trigger_roadmap_workflow(req.user_id, enriched_context))

    yield _sse("done", json.dumps({
        "session_id": req.session_id,
        "conversation_complete": conversation_complete,
        "enriched_context": enriched_context,
    }))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_seen_ids(data_str: str, seen_ai_ids: set):
    """Collect all existing AI message IDs from a values snapshot so we don't
    re-forward them later."""
    try:
        state = json.loads(data_str)
        for msg in state.get("messages", []):
            if isinstance(msg, dict) and msg.get("type") == "ai" and msg.get("id"):
                seen_ai_ids.add(msg["id"])
    except (json.JSONDecodeError, KeyError, TypeError):
        pass


def _forward_chunk(event_name: str, data_str: str, seen_ai_ids: set):
    """
    LangGraph emits `event: values` with the full graph state each time a node
    completes.  The state is a flat dict:
        { "messages": [...], "enriched_context": ..., ... }
    We find the last AI message and forward it only if its ID is new.
    """
    if event_name != "values":
        return

    try:
        state = json.loads(data_str)
        msgs = state.get("messages", [])
        for msg in reversed(msgs):
            if isinstance(msg, dict) and msg.get("type") == "ai":
                msg_id = msg.get("id", "")
                if msg_id in seen_ai_ids:
                    break  # already forwarded (or pre-existing)
                if msg_id:
                    seen_ai_ids.add(msg_id)
                content = msg.get("content", "")
                if content:
                    yield _sse("chunk", json.dumps({"text": content}))
                break
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("[CONVERSATION _forward_chunk] parse error: %s | raw: %s", exc, data_str)


def _maybe_extract_context(data_str: str, current: Optional[dict]) -> Optional[dict]:
    """Pull enriched_context out of a values payload if present."""
    try:
        state = json.loads(data_str)
        if state.get("enriched_context"):
            return state["enriched_context"]
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
    Call create_dream directly (same process) to kick off the roadmap pipeline.
    """
    try:
        db = get_db()
        user = await db.users.find_one({"user_id": user_id}) if db is not None else None
        user_profile = {
            "traits": (user or {}).get("traits", {}),
            "preferences": (user or {}).get("preferences", {}),
        }

        dream_data = CreateDreamRequest(
            user_id=user_id,
            user_request=enriched_context.get("user_dream", ""),
            user_profile=user_profile,
            enriched_context=enriched_context,
        )

        await create_dream(dream_data)
        logger.info("[CONVERSATION /turn] roadmap workflow triggered for user %s", user_id)
    except Exception as exc:
        # Non-fatal — conversation already completed successfully for the user.
        logger.error("[CONVERSATION /turn] roadmap trigger failed: %s", exc, exc_info=True)
