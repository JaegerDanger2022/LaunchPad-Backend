"""
Milestone API endpoints - Update and manage milestones in roadmaps
"""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class UpdateMilestoneRequest(BaseModel):
    """Request schema for updating a milestone"""
    status: str = Field(..., description="New status for the milestone (e.g., 'completed', 'pending', 'in_progress')")
    evidence: str | None = Field(None, max_length=200, description="Evidence/proof text, max 200 characters")
    impact: str | None = Field(None, description="Impact level - 'critical', 'high', 'medium', or 'low'")


@router.put("/update-status/{user_id}/{thread_id}/{milestone_id}", status_code=200, tags=["milestone"])
async def update_milestone_status(
    user_id: str,
    thread_id: str,
    milestone_id: str,
    update_data: UpdateMilestoneRequest
):
    """
    Update a milestone's status in a dream.

    Args:
        user_id: The user ID who owns the dream
        thread_id: The thread ID of the dream containing the milestone
        milestone_id: The ID of the milestone to update
        update_data: Request body containing the new status

    Returns:
        dict: Updated milestone data with the new status

    Raises:
        404: User, dream, or milestone not found
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            logger.error("Database connection not available")
            raise HTTPException(
                status_code=500,
                detail="Database connection not available"
            )

        logger.info(f"Updating milestone {milestone_id} in dream {thread_id} for user {user_id} to status: {update_data.status}")

        # Verify dream exists in the dreams collection
        dream_doc = await db.dreams.find_one(
            {"thread_id": thread_id, "user_id": user_id}
        )

        if not dream_doc:
            logger.warning(f"Dream {thread_id} not found for user {user_id}")
            raise HTTPException(
                status_code=404,
                detail="User or dream not found"
            )

        # ---------------------------------------------------------------------------
        # Locate milestone — check roadmap.milestones first, then customMilestones.
        # Track which array it lives in so the $set path is correct.
        # ---------------------------------------------------------------------------
        roadmap_milestones = dream_doc.get("roadmap", {}).get("milestones", [])
        custom_milestones  = dream_doc.get("customMilestones", [])

        in_roadmap = any(m.get("id") == milestone_id for m in roadmap_milestones)
        in_custom  = any(m.get("id") == milestone_id for m in custom_milestones)

        if not in_roadmap and not in_custom:
            logger.warning(f"Milestone {milestone_id} not found in dream {thread_id}")
            raise HTTPException(status_code=404, detail="Milestone not found")

        # The array path prefix used in $set / array_filters
        array_path = "roadmap.milestones" if in_roadmap else "customMilestones"
        logger.info(f"Milestone {milestone_id} lives in {array_path}")

        # Check if roadmap status is "started", if not set it
        roadmap_status = dream_doc.get("roadmap", {}).get("status")
        if roadmap_status != "started":
            logger.info(f"Roadmap status is '{roadmap_status}', setting to 'started'")
            await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$set": {"roadmap.status": "started"}}
            )

        # Prepare milestone update fields using the resolved array path
        update_fields = {
            f"{array_path}.$[m].status": update_data.status
        }

        if update_data.status == "completed":
            update_fields[f"{array_path}.$[m].completedDate"] = datetime.now(timezone.utc).isoformat()

        if update_data.evidence is not None:
            update_fields[f"{array_path}.$[m].evidence"] = update_data.evidence

        if update_data.impact is not None:
            valid_impacts = ["critical", "high", "medium", "low"]
            if update_data.impact in valid_impacts:
                update_fields[f"{array_path}.$[m].impact"] = update_data.impact
            else:
                logger.warning(f"Invalid impact level: {update_data.impact}. Using default.")

        # Update the milestone in the dreams collection
        updated_dream = await db.dreams.find_one_and_update(
            {"thread_id": thread_id},
            {"$set": update_fields},
            array_filters=[{"m.id": milestone_id}],
            return_document=True
        )

        if not updated_dream:
            logger.error(f"Failed to update milestone despite verification passing")
            raise HTTPException(status_code=500, detail="Failed to update milestone")

        # ---------------------------------------------------------------------------
        # Find the updated milestone from the returned doc (search both arrays)
        # ---------------------------------------------------------------------------
        all_roadmap  = updated_dream.get("roadmap", {}).get("milestones", [])
        all_custom   = updated_dream.get("customMilestones", [])
        all_milestones = all_roadmap + all_custom

        updated_milestone = next(
            (m for m in all_milestones if m.get("id") == milestone_id), None
        )

        # ---------------------------------------------------------------------------
        # Recalculate score / total_xp across BOTH arrays and check completion
        # ---------------------------------------------------------------------------
        xp_points = updated_milestone.get("xp_points", 0) if updated_milestone else 0

        # Always recompute from the ground truth returned by find_one_and_update
        new_score  = sum(m.get("xp_points", 0) for m in all_milestones if m.get("status") == "completed")
        total_xp   = sum(m.get("xp_points", 0) for m in all_milestones)

        is_complete = (new_score == total_xp) and total_xp > 0
        logger.info(f"Dream completion check: score={new_score}, total_xp={total_xp}, isComplete={is_complete}")

        dream_update_fields = {
            "metadata.score": new_score,
            "metadata.total_xp": total_xp,
        }
        if is_complete:
            dream_update_fields["isComplete"] = True
            dream_update_fields["completed_at"] = datetime.now(timezone.utc).isoformat()
            dream_update_fields["status"] = "completed"
            logger.info(f"Dream {thread_id} is now complete! Setting status to completed.")

        await db.dreams.update_one(
            {"thread_id": thread_id},
            {"$set": dream_update_fields}
        )
        logger.info(f"Updated dream metadata: score={new_score}, total_xp={total_xp}, isComplete={is_complete}")

        # Sync completion to dreams_metadata on the user doc
        if is_complete:
            await db.users.update_one(
                {"user_id": user_id, "dreams_metadata.thread_id": thread_id},
                {"$set": {
                    "dreams_metadata.$.status": "completed",
                    "dreams_metadata.$.completed_at": dream_update_fields["completed_at"],
                }}
            )

        logger.info(f"Successfully updated milestone {milestone_id} to status: {update_data.status}")

        # ---------------------------------------------------------------------------
        # Build response
        # ---------------------------------------------------------------------------
        milestone_response = None
        if updated_milestone:
            milestone_response = {
                "id": updated_milestone.get("id"),
                "title": updated_milestone.get("title"),
                "status": updated_milestone.get("status"),
                "evidence": updated_milestone.get("evidence"),
                "impact": updated_milestone.get("impact"),
                "completedDate": updated_milestone.get("completedDate"),
                "xp_points": updated_milestone.get("xp_points"),
                "challenge_type": updated_milestone.get("challenge_type"),
                "streak_eligible": updated_milestone.get("streak_eligible"),
                "is_custom": updated_milestone.get("is_custom", False),
            }

        response = {
            "success": True,
            "message": "Milestone updated",
            "milestone": milestone_response,
            "isComplete": is_complete,
            "dreamCompleted": is_complete,
        }

        # Dream stats for Journey Recap — only included when dream just completed
        if is_complete:
            completed_dates = [
                m.get("completedDate") for m in all_milestones
                if m.get("completedDate") is not None
            ]
            dream_start_date    = min(completed_dates) if completed_dates else None
            dream_completed_date = dream_update_fields.get("completed_at")

            duration_days = 0
            if dream_start_date and dream_completed_date:
                try:
                    start = datetime.fromisoformat(dream_start_date.replace('Z', '+00:00'))
                    end   = datetime.fromisoformat(dream_completed_date.replace('Z', '+00:00'))
                    duration_days = max(0, (end - start).days)
                except Exception as e:
                    logger.warning(f"Failed to calculate duration: {e}")

            response["dreamStats"] = {
                "totalMilestones": len(all_milestones),
                "completedMilestones": sum(1 for m in all_milestones if m.get("status") == "completed"),
                "completionPercentage": 100,
                "dreamStartDate": dream_start_date,
                "dreamCompletedDate": dream_completed_date,
                "durationDays": duration_days,
            }
            logger.info(f"Dream complete! Stats: {response['dreamStats']}")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating milestone {milestone_id}: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update milestone: {str(e)}"
        )
