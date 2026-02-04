"""
Dreams CRUD API endpoints for the dreams collection.
Handles fetching, updating, and deleting dreams from the dedicated dreams collection.
"""

import logging
import base64
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
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
                # Exclude roadmap
                "roadmap": 0
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

        # Backfill metadata from milestones if not yet present (covers dreams completed
        # before the milestone endpoint started writing metadata to this collection)
        if "metadata" not in dream:
            milestones = dream.get("roadmap", {}).get("milestones", [])
            total_xp = sum(m.get("xp_points", 0) for m in milestones)
            score = sum(m.get("xp_points", 0) for m in milestones if m.get("status") == "completed")
            dream["metadata"] = {"score": score, "total_xp": total_xp}

            # Persist so subsequent fetches don't recompute
            await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$set": {"metadata": dream["metadata"]}}
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
