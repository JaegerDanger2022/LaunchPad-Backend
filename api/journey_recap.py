"""
Journey Recap API endpoints - Share completed dream journeys
"""

import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateJourneyRecapRequest(BaseModel):
    """Request schema for creating a journey recap"""
    dreamId: str = Field(..., description="Thread ID of the completed dream")
    journeyStory: str = Field(..., min_length=10, max_length=500, description="User's reflection on the journey")
    keyMoment: str | None = Field(None, max_length=200, description="Most memorable moment (optional)")
    isAnonymous: bool = Field(False, description="Whether to share anonymously")


class GivePermissionRequest(BaseModel):
    """Request schema for giving permission to a journey recap"""
    permissionType: int = Field(..., ge=1, le=8, description="Permission type (1-8)")


@router.post("", status_code=201, tags=["journey-recap"])
async def create_journey_recap(
    journey_data: CreateJourneyRecapRequest,
    user_id: str = Query(..., description="ID of the user creating the journey recap")
):
    """
    Create a journey recap for a completed dream.

    Args:
        journey_data: Journey recap details (story, key moment, etc.)
        user_id: The user ID (passed as query param)

    Returns:
        dict: Created journey recap with ID and courage points awarded

    Raises:
        400: Invalid data or dream not complete
        404: Dream not found
        409: Journey recap already exists for this dream
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"Creating journey recap for dream {journey_data.dreamId} by user {user_id}")

        # 1. Verify dream exists in the dreams collection
        dream = await db.dreams.find_one(
            {"thread_id": journey_data.dreamId, "user_id": user_id}
        )

        if not dream:
            raise HTTPException(status_code=404, detail="Dream not found")

        # 3. Verify dream is completed
        if not dream.get("isComplete", False):
            raise HTTPException(
                status_code=400,
                detail="Cannot create journey recap for incomplete dream"
            )

        # 4. Check if journey recap already exists
        existing_recap = await db.journey_recaps.find_one({
            "userId": user_id,
            "dreamId": journey_data.dreamId
        })

        if existing_recap:
            raise HTTPException(
                status_code=409,
                detail="Journey recap already exists for this dream"
            )

        # 5. Calculate journey stats
        milestones = dream.get("roadmap", {}).get("milestones", [])
        total_milestones = len(milestones)

        # Get completion dates
        completed_dates = [
            m.get("completedDate")
            for m in milestones
            if m.get("completedDate") is not None
        ]

        dream_start_date = min(completed_dates) if completed_dates else None
        dream_completed_date = dream.get("completed_at")

        # Calculate duration
        duration_days = 0
        if dream_start_date and dream_completed_date:
            start = datetime.fromisoformat(dream_start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(dream_completed_date.replace('Z', '+00:00'))
            duration_days = max(0, (end - start).days)

        # 6. Get user profile info
        user_display_name = "Anonymous"
        user_location = None
        user_age = None
        pref_timezone = None

        # Always fetch timezone for anonymous posts (but not name/location/age)
        user_doc = await db.users.find_one(
            {"user_id": user_id},
            {"firstname": 1, "communityProfile": 1, "pref_timezone": 1}
        )

        if user_doc:
            pref_timezone = user_doc.get("pref_timezone")

            if not journey_data.isAnonymous:
                user_display_name = user_doc.get("firstname", "User")
                community_profile = user_doc.get("communityProfile", {})
                user_location = community_profile.get("location")
                user_age = community_profile.get("age")

        # 7. Create journey recap document
        journey_recap = {
            "userId": user_id,
            "userDisplayName": user_display_name,
            "userLocation": user_location,
            "userAge": user_age,
            "prefTimezone": pref_timezone,
            "dreamId": journey_data.dreamId,
            "dreamTitle": dream.get("dream", ""),
            "dreamCategory": dream.get("category", "achievement_goals"),
            "journeyStory": journey_data.journeyStory,
            "totalMilestones": total_milestones,
            "durationDays": duration_days,
            "keyMoment": journey_data.keyMoment,
            "completedDate": dream_completed_date or datetime.now(timezone.utc).isoformat(),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "courageBoosts": 0,
            "permissionsCount": 0,
            "meTooCount": 0,
            "isAnonymous": journey_data.isAnonymous
        }

        # 8. Insert into database
        result = await db.journey_recaps.insert_one(journey_recap)
        journey_recap_id = str(result.inserted_id)

        # 9. Award courage points (+10 for journey vs +5 for victory)
        courage_points_awarded = 10
        await db.users.update_one(
            {"user_id": user_id},
            {"$inc": {"couragePoints": courage_points_awarded}}
        )

        logger.info(f"Journey recap created: {journey_recap_id}, awarded {courage_points_awarded} courage points")

        return {
            "success": True,
            "journeyRecapId": journey_recap_id,
            "couragePointsAwarded": courage_points_awarded
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating journey recap: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create journey recap: {str(e)}")


@router.post("/{journey_recap_id}/boost", status_code=200, tags=["journey-recap"])
async def boost_journey_recap(
    journey_recap_id: str,
    giver_user_id: str = Query(..., description="ID of user giving the boost")
):
    """
    Give a courage boost to a journey recap.

    Args:
        journey_recap_id: The ID of the journey recap to boost
        giver_user_id: ID of user giving the boost

    Returns:
        dict: Success status, new boost count, and courage points awarded

    Raises:
        400: User already boosted or invalid ID
        404: Journey recap not found
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"User {giver_user_id} boosting journey recap {journey_recap_id}")

        # 1. Check if journey recap exists
        try:
            journey = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid journey recap ID format")

        if not journey:
            raise HTTPException(status_code=404, detail="Journey recap not found")

        # 2. Check for duplicate boost
        existing_boost = await db.courage_boosts.find_one({
            "itemId": journey_recap_id,
            "itemType": "journey_recap",
            "giverId": giver_user_id
        })

        if existing_boost:
            raise HTTPException(status_code=400, detail="You already boosted this journey")

        # 3. Create boost record
        boost = {
            "itemId": journey_recap_id,
            "itemType": "journey_recap",
            "giverId": giver_user_id,
            "receiverId": journey["userId"],
            "createdAt": datetime.now(timezone.utc).isoformat()
        }
        await db.courage_boosts.insert_one(boost)

        # 4. Increment boost count on journey recap
        await db.journey_recaps.update_one(
            {"_id": ObjectId(journey_recap_id)},
            {"$inc": {"courageBoosts": 1}}
        )

        # 5. Award courage point to receiver
        await db.users.update_one(
            {"user_id": journey["userId"]},
            {"$inc": {"couragePoints": 1}}
        )

        # 6. Get new count
        updated = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})

        logger.info(f"Boost successful. New count: {updated['courageBoosts']}")

        return {
            "success": True,
            "newBoostCount": updated["courageBoosts"],
            "couragePointsAwarded": 1
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error boosting journey recap: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to boost journey recap: {str(e)}")


@router.post("/{journey_recap_id}/permission", status_code=200, tags=["journey-recap"])
async def give_permission_to_journey_recap(
    journey_recap_id: str,
    permission_data: GivePermissionRequest,
    giver_user_id: str = Query(..., description="ID of user giving permission")
):
    """
    Grant a permission slip to a journey recap.

    Args:
        journey_recap_id: The ID of the journey recap
        permission_data: Permission type data
        giver_user_id: ID of user giving permission

    Returns:
        dict: Success status, permission ID, and permission text

    Raises:
        400: User already gave permission or invalid ID
        404: Journey recap not found
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"User {giver_user_id} giving permission to journey recap {journey_recap_id}")

        # 1. Check if journey recap exists
        try:
            journey = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid journey recap ID format")

        if not journey:
            raise HTTPException(status_code=404, detail="Journey recap not found")

        # 2. Prevent self-permissions
        if giver_user_id == journey["userId"]:
            raise HTTPException(status_code=400, detail="Cannot give permission to your own journey recap")

        # 3. Check for duplicate permission
        existing_permission = await db.permission_slips.find_one({
            "itemId": journey_recap_id,
            "itemType": "journey_recap",
            "giverId": giver_user_id
        })

        if existing_permission:
            raise HTTPException(status_code=400, detail="You already granted permission to this journey")

        # 4. Get permission text
        permission_texts = {
            1: "Permission to feel proud",
            2: "Permission to rest",
            3: "Permission to ask for help",
            4: "Permission to keep showing up",
            5: "Permission to start messy",
            6: "Permission to change your mind",
            7: "Permission to celebrate small wins",
            8: "Permission to be a beginner"
        }
        permission_text = permission_texts.get(permission_data.permissionType, "Permission granted")

        # 5. Create permission record
        permission = {
            "itemId": journey_recap_id,
            "itemType": "journey_recap",
            "giverId": giver_user_id,
            "receiverId": journey["userId"],
            "permissionType": permission_data.permissionType,
            "permissionText": permission_text,
            "createdAt": datetime.now(timezone.utc).isoformat()
        }
        result = await db.permission_slips.insert_one(permission)
        permission_id = str(result.inserted_id)

        # 6. Increment permission count on journey recap
        await db.journey_recaps.update_one(
            {"_id": ObjectId(journey_recap_id)},
            {"$inc": {"permissionsCount": 1}}
        )

        # 7. Award courage points to receiver (+5 for permission)
        await db.users.update_one(
            {"user_id": journey["userId"]},
            {"$inc": {"couragePoints": 5}}
        )

        # 8. Update community stats
        await db.users.update_one(
            {"user_id": giver_user_id},
            {"$inc": {"communityStats.permissionsGiven": 1}}
        )
        await db.users.update_one(
            {"user_id": journey["userId"]},
            {"$inc": {"communityStats.permissionsReceived": 1}}
        )

        logger.info(f"Permission granted successfully. ID: {permission_id}")

        return {
            "success": True,
            "permissionId": permission_id,
            "permissionText": permission_text
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error giving permission to journey recap: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to give permission: {str(e)}")


@router.post("/{journey_recap_id}/metoo", status_code=200, tags=["journey-recap"])
async def toggle_metoo_on_journey_recap(
    journey_recap_id: str,
    user_id: str = Query(..., description="ID of user toggling Me Too")
):
    """
    Toggle Me Too on a journey recap (add to inspirations list).

    Args:
        journey_recap_id: The ID of the journey recap
        user_id: ID of user toggling Me Too

    Returns:
        dict: Success status, new Me Too count, and whether it was added or removed

    Raises:
        400: Invalid ID
        404: Journey recap not found
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"User {user_id} toggling Me Too on journey recap {journey_recap_id}")

        # 1. Check if journey recap exists
        try:
            journey = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid journey recap ID format")

        if not journey:
            raise HTTPException(status_code=404, detail="Journey recap not found")

        # 2. Check if Me Too already exists
        existing_metoo = await db.me_toos.find_one({
            "itemId": journey_recap_id,
            "itemType": "journey_recap",
            "userId": user_id
        })

        if existing_metoo:
            # Remove Me Too
            await db.me_toos.delete_one({"_id": existing_metoo["_id"]})
            await db.journey_recaps.update_one(
                {"_id": ObjectId(journey_recap_id)},
                {"$inc": {"meTooCount": -1}}
            )
            added = False
            logger.info(f"Me Too removed from journey recap {journey_recap_id}")
        else:
            # Add Me Too
            metoo = {
                "itemId": journey_recap_id,
                "itemType": "journey_recap",
                "userId": user_id,
                "createdAt": datetime.now(timezone.utc).isoformat()
            }
            await db.me_toos.insert_one(metoo)
            await db.journey_recaps.update_one(
                {"_id": ObjectId(journey_recap_id)},
                {"$inc": {"meTooCount": 1}}
            )
            added = True
            logger.info(f"Me Too added to journey recap {journey_recap_id}")

        # 3. Get new count
        updated = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})

        return {
            "success": True,
            "newMeTooCount": updated["meTooCount"],
            "added": added
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling Me Too on journey recap: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to toggle Me Too: {str(e)}")


@router.get("/{journey_recap_id}/permissions", status_code=200, tags=["journey-recap"])
async def get_journey_recap_permissions(journey_recap_id: str):
    """
    Fetch all permissions granted to a journey recap.

    Args:
        journey_recap_id: The ID of the journey recap

    Returns:
        dict: List of permissions with giver info

    Raises:
        400: Invalid ID
        404: Journey recap not found
        500: Database error
    """
    try:
        db = get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database connection not available")

        logger.info(f"Fetching permissions for journey recap {journey_recap_id}")

        # 1. Check if journey recap exists
        try:
            journey = await db.journey_recaps.find_one({"_id": ObjectId(journey_recap_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid journey recap ID format")

        if not journey:
            raise HTTPException(status_code=404, detail="Journey recap not found")

        # 2. Fetch permissions
        permissions_cursor = db.permission_slips.find({
            "itemId": journey_recap_id,
            "itemType": "journey_recap"
        }).sort("createdAt", -1)
        permissions_list = await permissions_cursor.to_list(length=None)

        # 3. Enrich with giver display names
        permissions = []
        for perm in permissions_list:
            giver = await db.users.find_one({"user_id": perm["giverId"]})
            giver_name = giver.get("firstname", "Anonymous") if giver else "Anonymous"

            permissions.append({
                "id": str(perm["_id"]),
                "giverUserId": perm["giverId"],
                "giverDisplayName": giver_name,
                "permissionType": perm["permissionType"],
                "permissionText": perm["permissionText"],
                "createdAt": perm["createdAt"]
            })

        logger.info(f"Found {len(permissions)} permissions for journey recap {journey_recap_id}")

        return {"permissions": permissions}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching permissions for journey recap: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch permissions: {str(e)}")
