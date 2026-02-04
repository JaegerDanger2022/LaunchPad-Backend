"""
Voice Assistant WebSocket Endpoint v2.0

Uses LangGraph + OpenAI Whisper + Eleven Labs for conversational voice interface.

Flow:
1. Client connects via WebSocket
2. Call LangGraph voice conversation node for AI greeting
3. Convert greeting to speech (Eleven Labs) and send to client
4. Receive user audio → transcribe (Whisper) → send to LangGraph
5. Get AI response → convert to speech → send to client
6. Repeat until context extracted (2-3 exchanges)
7. Trigger existing /api/dreams/create endpoint with enriched_context
8. Return thread_id to client
"""

import asyncio
import logging
import json
import base64
import os
import io
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Optional
import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from elevenlabs import AsyncElevenLabs, VoiceSettings

from core.database import get_db

load_dotenv()

logger = logging.getLogger(__name__)
router = APIRouter()

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
LANGGRAPH_AGENT_URL = os.getenv("LANGGRAPH_AGENT_URL")
LANGGRAPH_API_KEY = os.getenv("LANGGRAPH_API_KEY")

# Initialize clients
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
elevenlabs_client = AsyncElevenLabs(api_key=ELEVENLABS_API_KEY) if ELEVENLABS_API_KEY else None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def transcribe_audio(audio_bytes: bytes) -> str:
    """
    Transcribe audio using OpenAI Whisper.

    Args:
        audio_bytes: WAV audio data (16kHz, 16-bit, mono)

    Returns:
        Transcribed text
    """
    if not openai_client:
        raise ValueError("OpenAI client not initialized - check OPENAI_API_KEY")

    try:
        # Create file-like object
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.wav"

        # Transcribe
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en"
        )

        return response.text

    except Exception as e:
        logger.error(f"Whisper transcription error: {e}", exc_info=True)
        raise


async def text_to_speech(text: str) -> bytes:
    """
    Convert text to speech using Eleven Labs.

    Args:
        text: Text to convert

    Returns:
        MP3 audio bytes
    """
    if not elevenlabs_client:
        raise ValueError("Eleven Labs client not initialized - check ELEVENLABS_API_KEY")

    try:
        # Generate audio with streaming
        audio_generator = elevenlabs_client.generate(
            text=text,
            voice=ELEVENLABS_VOICE_ID,
            model="eleven_multilingual_v2",
            stream=True
        )

        # Collect chunks
        audio_bytes = b""
        async for chunk in audio_generator:
            if chunk:
                audio_bytes += chunk

        return audio_bytes

    except Exception as e:
        logger.error(f"Eleven Labs TTS error: {e}", exc_info=True)
        raise


async def call_langgraph_voice_node(
    thread_id: str,
    user_message: Optional[str] = None,
    user_id: Optional[str] = None
) -> dict:
    """
    Call LangGraph voice_conversation graph for one turn of conversation.

    The voice_conversation graph is a standalone graph (separate from main workflow)
    that processes one conversation turn and returns immediately.

    Args:
        thread_id: LangGraph thread ID
        user_message: User's message (None for initial greeting)
        user_id: User ID for thread metadata

    Returns:
        {
            "ai_response": "AI's text response",
            "context_extracted": bool,
            "enriched_context": dict or None
        }
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Prepare payload
        payload = {
            "input": {
                "user_id": user_id,
                "user_message": user_message,  # None for greeting, string for subsequent turns
                "messages": [],
                "enriched_context": None,
                "turn_count": 0
            },
            "config": {
                "configurable": {
                    "thread_id": thread_id
                }
            }
        }

        # Call voice_conversation graph
        response = await client.post(
            f"{LANGGRAPH_AGENT_URL}/voice_conversation/invoke",
            headers={"x-api-key": LANGGRAPH_API_KEY},
            json=payload
        )
        response.raise_for_status()

        result = response.json()
        output = result.get("output", {})

        # Extract AI response from messages
        messages = output.get("messages", [])
        ai_response = ""
        if messages:
            # Get last message (AI response)
            last_message = messages[-1]
            if isinstance(last_message, dict):
                ai_response = last_message.get("content", "")
            else:
                # Handle serialized message object as string
                ai_response = str(last_message)

        # Check if context extracted
        enriched_context = output.get("enriched_context")

        return {
            "ai_response": ai_response,
            "context_extracted": enriched_context is not None,
            "enriched_context": enriched_context
        }


async def create_langgraph_thread(user_id: str) -> str:
    """Create LangGraph thread for voice conversation."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{LANGGRAPH_AGENT_URL}/threads",
            headers={"x-api-key": LANGGRAPH_API_KEY},
            json={"metadata": {"user_id": user_id, "type": "voice"}}
        )
        response.raise_for_status()
        return response.json()["thread_id"]


# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@router.websocket("/ws/{user_id}")
async def voice_assistant_websocket(websocket: WebSocket, user_id: str):
    """
    WebSocket endpoint for LangGraph voice conversation.

    Flow:
    1. Client connects
    2. Fetch user from database
    3. Create LangGraph thread
    4. Get AI greeting → TTS → send to client
    5. Receive user audio → STT → send to LangGraph
    6. Get AI response → TTS → send to client
    7. Repeat until context extracted
    8. Trigger /api/dreams/create with enriched_context
    9. Return thread_id to client
    """
    await websocket.accept()
    logger.info(f"🎤 Voice session started: {user_id}")

    # Verify services are configured
    if not openai_client:
        await websocket.send_json({
            "type": "error",
            "message": "Speech-to-text service not configured"
        })
        await websocket.close()
        return

    if not elevenlabs_client:
        await websocket.send_json({
            "type": "error",
            "message": "Text-to-speech service not configured"
        })
        await websocket.close()
        return

    try:
        # ========================================================================
        # STEP 1: Fetch user from database
        # ========================================================================

        db = get_db()
        if db is None:
            await websocket.send_json({
                "type": "error",
                "message": "Database not connected"
            })
            await websocket.close()
            return

        user = await db.users.find_one({"user_id": user_id})
        if not user:
            await websocket.send_json({
                "type": "error",
                "message": "User not found"
            })
            await websocket.close()
            return

        logger.info(f"✅ User verified: {user.get('firstname', 'Unknown')}")

        # Extract user profile
        user_profile = {
            "traits": user.get("traits", {}),
            "preferences": user.get("preferences", {})
        }

        # ========================================================================
        # STEP 2: Create LangGraph thread and get AI greeting
        # ========================================================================

        thread_id = await create_langgraph_thread(user_id)
        logger.info(f"📝 Thread created: {thread_id}")

        # Get AI greeting
        greeting_result = await call_langgraph_voice_node(
            thread_id=thread_id,
            user_message=None,  # None triggers greeting
            user_id=user_id
        )
        ai_greeting = greeting_result["ai_response"]
        logger.info(f"🤖 AI greeting: {ai_greeting[:50]}...")

        # Convert to speech
        greeting_audio = await text_to_speech(ai_greeting)
        greeting_base64 = base64.b64encode(greeting_audio).decode('utf-8')

        # Send greeting
        await websocket.send_json({"type": "audio", "data": greeting_base64})
        await websocket.send_json({"type": "text", "text": ai_greeting})
        await websocket.send_json({"type": "turn_complete"})
        logger.info("🔊 Sent AI greeting")

        # ========================================================================
        # STEP 3: Conversation loop
        # ========================================================================

        while True:
            # Wait for user audio
            message = await websocket.receive_json()

            if message.get("type") != "audio":
                logger.warning(f"Unexpected message type: {message.get('type')}")
                continue

            # Decode and transcribe
            audio_data = base64.b64decode(message.get("data", ""))

            try:
                user_text = await transcribe_audio(audio_data)
                logger.info(f"📝 User said: {user_text}")
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": "Could not understand audio. Please try again."
                })
                continue

            # Call LangGraph
            try:
                response_result = await call_langgraph_voice_node(
                    thread_id=thread_id,
                    user_message=user_text,
                    user_id=user_id
                )
            except Exception as e:
                logger.error(f"LangGraph error: {e}", exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": "AI is having trouble. Please try again."
                })
                continue

            # Check if context extracted
            if response_result["context_extracted"]:
                enriched_context = response_result["enriched_context"]
                user_request = enriched_context["user_dream"]

                logger.info(f"✅ Context extracted: {user_request}")

                # ================================================================
                # STEP 4: Trigger roadmap workflow via /api/dreams/create
                # ================================================================

                # Notify client
                await websocket.send_json({
                    "type": "workflow_starting",
                    "thread_id": f"dream_{user_id}",
                    "user_request": user_request
                })

                # Call internal dreams/create endpoint
                async with httpx.AsyncClient(timeout=120.0) as client:
                    try:
                        # Get base URL from environment or construct from LANGGRAPH_AGENT_URL
                        backend_url = os.getenv("BACKEND_URL", "http://localhost:8001")

                        create_response = await client.post(
                            f"{backend_url}/api/dreams/create",
                            json={
                                "user_id": user_id,
                                "user_request": user_request,
                                "user_profile": user_profile,
                                "research_data": {},
                                "messages": [],
                                "roadmap": {},
                                "tracks": [],
                                "status": "planning",
                                # Pass enriched_context for enhanced roadmap generation
                                "enriched_context": enriched_context
                            }
                        )
                        create_response.raise_for_status()
                        dream_result = create_response.json()

                        # Send completion
                        await websocket.send_json({
                            "type": "workflow_complete",
                            "thread_id": dream_result.get("thread_id"),
                            "user_request": user_request,
                            "enriched_context": enriched_context
                        })

                        logger.info(f"🚀 Workflow complete: {dream_result.get('thread_id')}")

                    except Exception as e:
                        logger.error(f"Failed to trigger roadmap: {e}", exc_info=True)
                        await websocket.send_json({
                            "type": "error",
                            "message": "Failed to create roadmap. Please try again."
                        })

                # End conversation
                break

            # Get AI response
            ai_response = response_result["ai_response"]
            logger.info(f"🤖 AI response: {ai_response[:50]}...")

            # Convert to speech
            try:
                response_audio = await text_to_speech(ai_response)
                response_base64 = base64.b64encode(response_audio).decode('utf-8')

                # Send response
                await websocket.send_json({"type": "audio", "data": response_base64})
                await websocket.send_json({"type": "text", "text": ai_response})
                await websocket.send_json({"type": "turn_complete"})
                logger.info("🔊 Sent AI response")

            except Exception as e:
                logger.error(f"TTS error: {e}", exc_info=True)
                # Fallback: send text only
                await websocket.send_json({"type": "text", "text": ai_response})
                await websocket.send_json({"type": "turn_complete"})

    except WebSocketDisconnect:
        logger.info(f"🔌 Client disconnected: {user_id}")
    except Exception as e:
        logger.error(f"❌ Voice session error: {e}", exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass
        logger.info(f"🔒 Voice session closed: {user_id}")


# ============================================================================
# HEALTH CHECK ENDPOINT
# ============================================================================

@router.get("/health")
async def voice_health_check():
    """Check if all voice services are configured."""

    openai_ok = bool(OPENAI_API_KEY)
    elevenlabs_ok = bool(ELEVENLABS_API_KEY)
    langgraph_ok = bool(LANGGRAPH_AGENT_URL and LANGGRAPH_API_KEY)

    status = "healthy" if (openai_ok and elevenlabs_ok and langgraph_ok) else "degraded"

    return {
        "status": status,
        "services": {
            "whisper_stt": openai_ok,
            "elevenlabs_tts": elevenlabs_ok,
            "langgraph": langgraph_ok
        },
        "message": "Voice services ready" if status == "healthy" else "Missing API keys - check OPENAI_API_KEY, ELEVENLABS_API_KEY, LANGGRAPH_AGENT_URL"
    }
