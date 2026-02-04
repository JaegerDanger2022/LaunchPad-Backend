"""
Dreams API endpoints - Create and manage dreams in MongoDB
"""

import logging
import os
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any
import httpx
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class UserTraits(BaseModel):
    """User personality and work style traits"""
    work_style: Optional[str] = Field(None, description="How the user prefers to work")
    completion_style: Optional[str] = Field(None, description="User's completion style")


class UserPreferences(BaseModel):
    """User preferences and settings"""
    preferred_time: Optional[str] = Field(None, description="User's preferred time of day")


class UserProfile(BaseModel):
    """User profile information"""
    traits: Optional[UserTraits] = Field(default_factory=UserTraits, description="User traits")
    preferences: Optional[UserPreferences] = Field(default_factory=UserPreferences, description="User preferences")


class CreateDreamRequest(BaseModel):
    """Request schema for creating a new dream"""
    user_id: str = Field(..., description="Unique user identifier")
    user_request: str = Field(..., description="The dream or goal the user wants to achieve")
    user_profile: UserProfile = Field(..., description="User profile with traits and preferences")
    research_data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Research data related to the dream")
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="Message history")
    roadmap: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Dream roadmap/plan")
    tracks: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Dream tracks/milestones")
    status: Optional[str] = Field(default="", description="Current status of the dream")
    enriched_context: Optional[Dict[str, Any]] = Field(None, description="Voice conversation context (timeline, motivation, etc.)")


@router.post("/create", status_code=201, tags=["dreams"])
async def create_dream(dream_data: CreateDreamRequest):
    """
    Send dream to langgraph agent for processing.

    Args:
        dream_data: Dream information including user request and profile

    Returns:
        dict: Dream with generated thread_id

    Raises:
        500: Langgraph agent error
    """
    try:
        logger.info(f"Processing dream for user: {dream_data.user_id}")

        # --- Dream limit gate ---
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection failed")

        user = await db.users.find_one({"user_id": dream_data.user_id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        plan = user.get("plan", "free")

        if plan == "free":
            # Free: hard cap of 2 dreams total (active + completed)
            total_dreams = await db.dreams.count_documents({"user_id": dream_data.user_id})
            if total_dreams >= 2:
                logger.warning(f"Free user {dream_data.user_id} hit dream limit ({total_dreams} total)")
                raise HTTPException(
                    status_code=403,
                    detail="Dream limit reached. Upgrade to Pro to create more dreams."
                )
        else:
            # Pro: max 3 active dreams (completed don't count)
            active_dreams = await db.dreams.count_documents({"user_id": dream_data.user_id, "status": "active"})
            if active_dreams >= 3:
                logger.warning(f"Pro user {dream_data.user_id} hit active dream limit ({active_dreams} active)")
                raise HTTPException(
                    status_code=403,
                    detail="Active dream limit reached. Complete or delete a dream to create a new one."
                )
        # --- End limit gate ---

        # Get environment variables
        langgraph_url = os.getenv("LANGGRAPH_AGENT_URL")
        if not langgraph_url:
            logger.error("LANGGRAPH_AGENT_URL environment variable not set")
            raise HTTPException(
                status_code=500,
                detail="LANGGRAPH_AGENT_URL not configured"
            )

        api_key = os.getenv("LANGGRAPH_API_KEY")
        if not api_key:
            logger.error("LANGGRAPH_API_KEY environment variable not set")
            raise HTTPException(
                status_code=500,
                detail="LANGGRAPH_API_KEY not configured"
            )

        assistant_id = os.getenv("LANGGRAPH_ASSISTANT_ID")
        if not assistant_id:
            logger.error("LANGGRAPH_ASSISTANT_ID environment variable not set")
            raise HTTPException(
                status_code=500,
                detail="LANGGRAPH_ASSISTANT_ID not configured"
            )

        # Build headers with API key
        headers = {"x-api-key": api_key}

        try:
            async with httpx.AsyncClient() as client:
                # Step 1: Create a thread
                create_thread_url = f"{langgraph_url}/threads"
                logger.info(f"Creating thread at: {create_thread_url}")

                thread_response = await client.post(
                    create_thread_url,
                    json={},
                    headers=headers,
                    timeout=30.0
                )
                logger.info(f"Thread creation response status: {thread_response.status_code}")
                thread_response.raise_for_status()
                thread_data = thread_response.json()
                thread_id = thread_data.get("thread_id")
                logger.info(f"Created thread_id: {thread_id}")

                if not thread_id:
                    logger.error("No thread_id in response from /threads endpoint")
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to create thread"
                    )

                # Step 2: Send run to the thread with correct payload structure
                run_endpoint = f"{langgraph_url}/threads/{thread_id}/runs/wait"
                logger.info(f"Sending run to: {run_endpoint}")

                # Build payload with input wrapper - add thread_id to dream data
                dream_input = dream_data.model_dump()
                dream_input["thread_id"] = thread_id

                run_payload = {
                    "assistant_id": assistant_id,
                    "input": dream_input
                }

                logger.info(f"Run payload structure: assistant_id + input with keys: {list(run_payload['input'].keys())}")

                run_response = await client.post(
                    run_endpoint,
                    json=run_payload,
                    headers=headers,
                    timeout=30.0
                )
                logger.info(f"Run response status code: {run_response.status_code}")
                run_response.raise_for_status()
                agent_response = run_response.json()
                logger.info(f"Agent response received, keys: {list(agent_response.keys()) if isinstance(agent_response, dict) else 'not a dict'}")

                logger.info(f"Successfully processed dream for thread: {thread_id}")

                # Add thread_id to response
                if isinstance(agent_response, dict):
                    agent_response["thread_id"] = thread_id
                else:
                    # If response is not a dict, wrap it
                    agent_response = {
                        "thread_id": thread_id,
                        "response": agent_response
                    }

                return agent_response

        except httpx.HTTPError as e:
            logger.error(f"HTTP Error in langgraph call: {type(e).__name__}: {e}", exc_info=True)
            if hasattr(e, 'response'):
                logger.error(f"Response status: {e.response.status_code}")
                try:
                    logger.error(f"Response body: {e.response.text}")
                except:
                    pass
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process dream: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error processing dream: {type(e).__name__}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process dream: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing dream for user {dream_data.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while processing dream"
        )
