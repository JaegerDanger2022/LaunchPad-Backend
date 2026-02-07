"""
Dreams CRUD API endpoints for the dreams collection.
Handles fetching, updating, and deleting dreams from the dedicated dreams collection.
"""

import logging
import base64
import uuid
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class UpdateDreamRequest(BaseModel):
    """Request schema for updating a dream"""
    status: Optional[str] = None
    isComplete: Optional[bool] = None
    roadmap: Optional[dict] = None


class AddCustomMilestoneRequest(BaseModel):
    """Request schema for adding a user-created milestone"""
    title: str = Field(..., min_length=1, max_length=60)
    challenge_type: str = Field(...)
    description: Optional[str] = Field(None, max_length=200)


@router.get("", tags=["dreams-crud"])
async def get_user_dreams(
    user_id: str = Query(..., description="User ID to fetch dreams for"),
    status: Optional[str] = Query(None, description="Filter by status (active, completed)"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    summary: bool = Query(False, description="Return summary without full roadmaps")
):
    """
    Get all dreams for a user from the dreams collection.

    Args:
        user_id: Firebase UID
        status: Optional status filter
        page: Page number for pagination
        limit: Number of items per page
        summary: If true, exclude full roadmaps

    Returns:
        List of dreams with pagination info

    Raises:
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Fetching dreams for user: {user_id} (status: {status}, page: {page}, limit: {limit}, summary: {summary})")

        # Build query
        query = {"user_id": user_id}
        if status:
            query["status"] = status

        # Get total count
        total = await db.dreams.count_documents(query)

        # Calculate skip
        skip = (page - 1) * limit

        # Projection for summary mode
        projection = None
        if summary:
            projection = {
                "_id": 1,
                "user_id": 1,
                "thread_id": 1,
                "dream": 1,
                "status": 1,
                "created_at": 1,
                "updated_at": 1,
                "category": 1,
                "isComplete": 1,
                "dream_image_bytes": 1,
                "dream_card_bg": 1,
            }

        # Fetch dreams
        cursor = db.dreams.find(query, projection).sort("updated_at", -1).skip(skip).limit(limit)
        dreams = await cursor.to_list(length=limit)

        # Convert ObjectId and binary image to base64
        for dream in dreams:
            if "_id" in dream:
                dream["_id"] = str(dream["_id"])

            if "dream_image_bytes" in dream and isinstance(dream["dream_image_bytes"], bytes):
                dream["dream_image_bytes"] = base64.b64encode(dream["dream_image_bytes"]).decode('utf-8')

            # Calculate milestone counts for summary
            if summary and "roadmap" not in dream:
                # Fetch just milestone count
                full_dream = await db.dreams.find_one(
                    {"thread_id": dream["thread_id"]},
                    {"roadmap.milestones": 1}
                )
                if full_dream and "roadmap" in full_dream:
                    milestones = full_dream["roadmap"].get("milestones", [])
                    dream["milestones_count"] = len(milestones)
                    dream["completed_milestones_count"] = sum(
                        1 for m in milestones if m.get("status") == "completed"
                    )
                    if len(milestones) > 0:
                        dream["completion_percentage"] = round(
                            (dream["completed_milestones_count"] / len(milestones)) * 100, 1
                        )
                    else:
                        dream["completion_percentage"] = 0

        logger.info(f"Successfully retrieved {len(dreams)} dreams for user: {user_id}")

        return {
            "dreams": dreams,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "totalPages": (total + limit - 1) // limit
            }
        }

    except Exception as e:
        logger.error(f"Error fetching dreams for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{thread_id}", tags=["dreams-crud"])
async def get_dream_by_id(thread_id: str):
    """
    Get a single dream by thread_id.

    Args:
        thread_id: Unique thread identifier

    Returns:
        Complete dream document with full roadmap

    Raises:
        404: Dream not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Fetching dream: {thread_id}")

        dream = await db.dreams.find_one({"thread_id": thread_id})

        if dream is None:
            logger.warning(f"Dream not found: {thread_id}")
            raise HTTPException(
                status_code=404,
                detail=f"Dream with thread_id '{thread_id}' not found"
            )

        # Convert ObjectId and binary image
        if "_id" in dream:
            dream["_id"] = str(dream["_id"])

        if "dream_image_bytes" in dream and isinstance(dream["dream_image_bytes"], bytes):
            dream["dream_image_bytes"] = base64.b64encode(dream["dream_image_bytes"]).decode('utf-8')

        # Backfill score/total_xp into metadata if not yet written by the milestone
        # endpoint.  Note: metadata may already exist with roadmap-generation fields
        # (version, structure, …) but lack score — so check for score specifically.
        if "score" not in dream.get("metadata", {}):
            milestones = dream.get("roadmap", {}).get("milestones", [])
            total_xp = sum(m.get("xp_points", 0) for m in milestones)
            score = sum(m.get("xp_points", 0) for m in milestones if m.get("status") == "completed")

            if "metadata" not in dream:
                dream["metadata"] = {}
            dream["metadata"]["score"] = score
            dream["metadata"]["total_xp"] = total_xp

            # Persist so subsequent fetches don't recompute
            await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$set": {"metadata.score": score, "metadata.total_xp": total_xp}}
            )
            logger.info(f"Backfilled metadata for dream {thread_id}: score={score}, total_xp={total_xp}")

        logger.info(f"Successfully retrieved dream: {thread_id}")
        return dream

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dream {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/{thread_id}", tags=["dreams-crud"])
async def update_dream(thread_id: str, update_data: UpdateDreamRequest):
    """
    Update dream fields.

    Args:
        thread_id: Unique thread identifier
        update_data: Fields to update

    Returns:
        Success status and updated dream

    Raises:
        404: Dream not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Updating dream: {thread_id}")

        # Check if dream exists
        dream = await db.dreams.find_one({"thread_id": thread_id})
        if dream is None:
            logger.warning(f"Dream not found: {thread_id}")
            raise HTTPException(status_code=404, detail="Dream not found")

        # Build update document
        update_doc = {"updated_at": datetime.now(timezone.utc)}

        if update_data.status is not None:
            update_doc["status"] = update_data.status

        if update_data.isComplete is not None:
            update_doc["isComplete"] = update_data.isComplete

        if update_data.roadmap is not None:
            # If roadmap contains milestones, update them specifically
            # to preserve other roadmap fields
            if "milestones" in update_data.roadmap:
                update_doc["roadmap.milestones"] = update_data.roadmap["milestones"]
            else:
                # If full roadmap replacement is intended
                update_doc["roadmap"] = update_data.roadmap

        # Update dreams collection
        result = await db.dreams.update_one(
            {"thread_id": thread_id},
            {"$set": update_doc}
        )

        # Update dreams_summary in user document (if exists)
        if update_data.status or update_data.isComplete is not None:
            summary_update = {
                "dreams_summary.$.updated_at": datetime.now(timezone.utc).isoformat()
            }

            if update_data.status:
                summary_update["dreams_summary.$.status"] = update_data.status

            if update_data.isComplete is not None:
                summary_update["dreams_summary.$.isComplete"] = update_data.isComplete

            await db.users.update_one(
                {"user_id": dream["user_id"], "dreams_summary.thread_id": thread_id},
                {"$set": summary_update}
            )

        # Sync status change to dreams_metadata on user doc
        if update_data.status:
            metadata_update = {"dreams_metadata.$.status": update_data.status}
            if update_data.status == "completed":
                metadata_update["dreams_metadata.$.completed_at"] = datetime.now(timezone.utc).isoformat()

            await db.users.update_one(
                {"user_id": dream["user_id"], "dreams_metadata.thread_id": thread_id},
                {"$set": metadata_update}
            )

        logger.info(f"Successfully updated dream: {thread_id}")

        return {
            "success": True,
            "message": "Dream updated successfully",
            "dream": {
                "thread_id": thread_id,
                "updated_at": update_doc["updated_at"].isoformat()
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating dream {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{thread_id}", tags=["dreams-crud"])
async def delete_dream(thread_id: str):
    """
    Delete a dream.

    Removes dream from:
    1. dreams collection
    2. User's dreams_summary array

    Args:
        thread_id: Unique thread identifier

    Returns:
        Success status

    Raises:
        404: Dream not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Deleting dream: {thread_id}")

        # Get dream to find user_id
        dream = await db.dreams.find_one({"thread_id": thread_id})

        if dream is None:
            logger.warning(f"Dream not found: {thread_id}")
            raise HTTPException(status_code=404, detail="Dream not found")

        user_id = dream["user_id"]

        # Delete from dreams collection
        await db.dreams.delete_one({"thread_id": thread_id})

        # Remove from user's dreams_summary and dreams_metadata
        await db.users.update_one(
            {"user_id": user_id},
            {"$pull": {
                "dreams_summary": {"thread_id": thread_id},
                "dreams_metadata": {"thread_id": thread_id},
            }}
        )

        logger.info(f"Successfully deleted dream: {thread_id}")

        return {
            "success": True,
            "message": "Dream deleted successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting dream {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{thread_id}/milestones", tags=["dreams-crud"])
async def get_dream_milestones(thread_id: str):
    """
    Get all milestones for a dream.

    Args:
        thread_id: Unique thread identifier

    Returns:
        List of milestones

    Raises:
        404: Dream not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Fetching milestones for dream: {thread_id}")

        dream = await db.dreams.find_one(
            {"thread_id": thread_id},
            {"roadmap.milestones": 1}
        )

        if dream is None:
            logger.warning(f"Dream not found: {thread_id}")
            raise HTTPException(status_code=404, detail="Dream not found")

        milestones = dream.get("roadmap", {}).get("milestones", [])

        logger.info(f"Successfully retrieved {len(milestones)} milestones for dream: {thread_id}")

        return {
            "thread_id": thread_id,
            "milestones": milestones
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching milestones for dream {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{thread_id}/milestones", status_code=201, tags=["dreams-crud"])
async def add_custom_milestone(thread_id: str, data: AddCustomMilestoneRequest):
    """
    Add a user-created milestone to a dream.

    The milestone is inserted directly into ``roadmap.milestones`` array,
    positioned BEFORE the last milestone (typically the celebration_moment).
    The last milestone's dependencies are updated to include this new milestone
    as a prerequisite, ensuring the dream cannot be completed until all custom
    milestones are done. ``metadata.total_xp`` is bumped accordingly.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        dream = await db.dreams.find_one({"thread_id": thread_id})
        if dream is None:
            raise HTTPException(status_code=404, detail="Dream not found")

        if dream.get("status") == "completed":
            raise HTTPException(status_code=409, detail="Cannot add milestones to a completed dream")

        # Build the new milestone document
        milestone_id = f"custom_{uuid.uuid4().hex[:12]}"
        xp_points = 10  # default for user-created milestones
        new_milestone = {
            "id": milestone_id,
            "title": data.title,
            "challenge_type": data.challenge_type,
            "status": "not_started",
            "xp_points": xp_points,
            "time_estimate": "30 mins",
            "description": data.description or "Custom milestone",
            "motivation_hook": "",
            "streak_eligible": False,
            "is_custom": True,
        }

        # Get current roadmap milestones
        roadmap_milestones = dream.get("roadmap", {}).get("milestones", [])

        if not roadmap_milestones:
            # No existing milestones - just append
            await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$push": {"roadmap.milestones": new_milestone}}
            )
        else:
            # Insert BEFORE the last milestone
            # We need to: 1) pop the last milestone, 2) push new one, 3) push last one back
            # OR we can fetch, modify array in Python, and replace entire array

            # Fetch full milestones, insert before last, update entire array
            updated_milestones = roadmap_milestones.copy()
            last_milestone = updated_milestones.pop()  # Remove last
            updated_milestones.append(new_milestone)    # Add custom milestone
            updated_milestones.append(last_milestone)   # Add last back

            # Update the entire milestones array
            await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$set": {"roadmap.milestones": updated_milestones}}
            )

            # Update the last milestone's dependencies to include this new custom milestone
            last_index = len(updated_milestones) - 1
            dep_path = f"roadmap.milestones.{last_index}.dependencies"

            # Get current dependencies of the last milestone
            current_deps = last_milestone.get("dependencies", [])
            if milestone_id not in current_deps:
                current_deps.append(milestone_id)
                await db.dreams.update_one(
                    {"thread_id": thread_id},
                    {"$set": {dep_path: current_deps}}
                )
                logger.info(f"Added dependency {milestone_id} to last milestone (index {last_index})")

        # Bump metadata.total_xp so score == total_xp check stays correct
        current_total_xp = dream.get("metadata", {}).get("total_xp", 0)
        # If total_xp was never set, compute it from all existing roadmap milestones first
        if current_total_xp == 0:
            current_total_xp = sum(
                m.get("xp_points", 0) for m in roadmap_milestones
            )
        new_total_xp = current_total_xp + xp_points
        await db.dreams.update_one(
            {"thread_id": thread_id},
            {"$set": {"metadata.total_xp": new_total_xp, "updated_at": datetime.now(timezone.utc)}}
        )

        logger.info(f"Added custom milestone {milestone_id} to dream {thread_id}. total_xp now {new_total_xp}")

        # Return the updated milestones array from roadmap
        updated_dream = await db.dreams.find_one(
            {"thread_id": thread_id},
            {"roadmap.milestones": 1}
        )

        return {
            "success": True,
            "milestone": new_milestone,
            "milestones": updated_dream.get("roadmap", {}).get("milestones", [])
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding custom milestone to dream {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
