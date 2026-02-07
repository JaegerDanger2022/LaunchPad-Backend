"""
Dreams API endpoints - Create and manage dreams in MongoDB
"""

import logging
import os
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List, Any, Literal
import httpx
from datetime import datetime, timezone
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


class CustomMilestone(BaseModel):
    """Request schema for a custom milestone"""
    title: str = Field(..., min_length=1, max_length=100, description="Milestone title")
    description: str = Field(default="", max_length=300, description="Milestone description (optional)")
    challenge_type: Literal[
        "power_move",
        "knowledge_quest",
        "prep_ritual",
        "courage_check",
        "skill_flex",
        "decision_point",
        "celebration_moment"
    ] = Field(..., description="Challenge type")
    order: int = Field(..., ge=1, description="Milestone order in sequence")


class CreateCustomDreamRequest(BaseModel):
    """Request schema for creating a custom DIY dream"""
    user_id: str = Field(..., description="Unique user identifier")
    dream_title: str = Field(..., min_length=1, max_length=100, description="The dream title")
    milestones: List[CustomMilestone] = Field(..., min_items=1, description="List of custom milestones")


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
            async with httpx.AsyncClient(timeout=300.0) as client:
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
                    timeout=300.0
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


@router.post("/create-custom", status_code=201, tags=["dreams"])
async def create_custom_dream(dream_data: CreateCustomDreamRequest):
    """
    Create a custom DIY dream with user-defined milestones.

    This endpoint allows users to create dreams manually without AI assistance.
    Milestones are created with sequential dependencies (each depends on the previous one).

    Args:
        dream_data: Dream information including title and custom milestones

    Returns:
        dict: Created dream with thread_id

    Raises:
        400: Invalid request (no milestones)
        403: Dream limit reached
        404: User not found
        500: Database error
    """
    try:
        logger.info(f"Creating custom dream for user: {dream_data.user_id}")
        logger.info(f"Dream title: {dream_data.dream_title}")
        logger.info(f"Number of milestones: {len(dream_data.milestones)}")

        # Validation
        if not dream_data.milestones:
            raise HTTPException(status_code=400, detail="At least one milestone is required")

        # --- Dream limit gate (same as regular create) ---
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

        # Generate thread_id
        thread_id = str(uuid.uuid4())
        logger.info(f"Generated thread_id: {thread_id}")

        # Create milestone documents with sequential dependencies
        milestone_docs = []
        prev_milestone_id = None
        total_xp = 0

        for milestone_data in sorted(dream_data.milestones, key=lambda x: x.order):
            milestone_id = str(uuid.uuid4())
            xp_points = 10  # Default XP for custom milestones
            total_xp += xp_points

            milestone_doc = {
                "id": milestone_id,
                "title": milestone_data.title,
                "description": milestone_data.description or "",
                "challenge_type": milestone_data.challenge_type,
                "status": "pending",
                "order": milestone_data.order,
                "time_estimate": "30 min",
                "xp_points": xp_points,
                "streak_eligible": True,
                "dependencies": [prev_milestone_id] if prev_milestone_id else [],
                "is_custom": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            milestone_docs.append(milestone_doc)
            prev_milestone_id = milestone_id

        logger.info(f"Created {len(milestone_docs)} milestone documents")

        # Create dream document
        dream_doc = {
            "thread_id": thread_id,
            "user_id": dream_data.user_id,
            "dream": dream_data.dream_title,
            "status": "active",
            "category": "custom",
            "is_custom": True,
            "isComplete": False,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "roadmap": {
                "status": "active",
                "milestones": milestone_docs
            },
            "metadata": {
                "score": 0,
                "total_xp": total_xp,
                "version": "1.0",
                "structure": "custom"
            }
        }

        # Insert dream document
        await db.dreams.insert_one(dream_doc)
        logger.info(f"Inserted dream document for thread_id: {thread_id}")

        # Create dream metadata entry for user document
        dream_metadata_entry = {
            "thread_id": thread_id,
            "dream": dream_data.dream_title,
            "status": "active",
            "category": "custom",
            "milestones_count": len(milestone_docs),
            "completed_milestones_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Update user document
        await db.users.update_one(
            {"user_id": dream_data.user_id},
            {
                "$push": {"dreams_metadata": dream_metadata_entry},
                "$inc": {"dreams_count": 1}
            }
        )
        logger.info(f"Updated user document with dream metadata")

        # Update up_next if user doesn't have one
        if not user.get("up_next"):
            first_milestone = milestone_docs[0]
            up_next = {
                "milestone_id": first_milestone["id"],
                "milestone_title": first_milestone["title"],
                "dream_thread_id": thread_id,
                "dream_title": dream_data.dream_title,
                "time_estimate": first_milestone["time_estimate"],
                "xp_points": first_milestone["xp_points"],
                "challenge_type": first_milestone["challenge_type"],
                "streak_eligible": first_milestone["streak_eligible"],
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.users.update_one(
                {"user_id": dream_data.user_id},
                {"$set": {"up_next": up_next}}
            )
            logger.info(f"Set first milestone as up_next for user")

        logger.info(f"Successfully created custom dream: {thread_id}")

        return {"thread_id": thread_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating custom dream for user {dream_data.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while creating custom dream"
        )
