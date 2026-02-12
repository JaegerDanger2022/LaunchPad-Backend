"""
Users API endpoints - Retrieve and create user data from MongoDB
"""

import logging
from enum import Enum
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from pymongo.errors import DuplicateKeyError
from core.database import get_db
from core.firebase_admin import delete_firebase_user
from services.notification_service import send_welcome_notification

logger = logging.getLogger(__name__)

router = APIRouter()


class UserFieldsLevel(str, Enum):
    """Field selection levels for user data"""
    minimal = "minimal"      # Only basic user info
    essential = "essential"  # Basic + up_next + streak + recents (DEFAULT)


class CreateUserRequest(BaseModel):
    """Request schema for creating a new user"""
    user_id: str = Field(..., description="Unique user identifier")
    email: str = Field(..., description="User email address")
    firstname: str = Field(..., description="User first name")
    lastname: str = Field(default="", description="User last name (optional)")
    pref_timezone: Optional[str] = Field(default=None, description="User's preferred timezone (IANA timezone identifier)")
    pref_notification_time: Optional[str] = Field(default=None, description="User's preferred notification time in 24-hour format (HH:MM), null if disabled")


class UpdateRecentsRequest(BaseModel):
    """Request schema for updating user's recents array"""
    thread_id: str = Field(..., description="Dream thread ID to add to recents")


class UpNextData(BaseModel):
    """Schema for up_next milestone data"""
    milestone_id: str = Field(..., description="Unique milestone identifier")
    milestone_title: str = Field(..., description="Title of the milestone")
    dream_thread_id: str = Field(..., description="Thread ID of the dream containing the milestone")
    dream_title: str = Field(..., description="Title of the dream")
    time_estimate: str = Field(..., description="Estimated time to complete (e.g., '20 mins', '1 hour')")
    xp_points: int = Field(..., description="XP points awarded for completing the milestone", ge=0)
    challenge_type: str = Field(..., description="Type of challenge (e.g., 'power_move', 'knowledge_quest')")
    streak_eligible: bool = Field(..., description="Whether this milestone is eligible for streak")
    updated_at: str = Field(..., description="ISO 8601 timestamp when up_next was updated")


class UpdateUpNextRequest(BaseModel):
    """Request schema for updating user's up_next field"""
    up_next: UpNextData | None = Field(..., description="Next milestone data or null to clear")


class UpdateStreakRequest(BaseModel):
    """Request schema for updating user's streak after milestone completion"""
    milestone_id: str = Field(..., description="The ID of the milestone being completed")
    completion_date: str = Field(..., description="ISO 8601 timestamp of milestone completion")
    is_streak_eligible: bool = Field(..., description="Whether this milestone counts toward streak")


@router.get("/{user_id}", tags=["users"])
async def get_user(user_id: str, fields: Optional[UserFieldsLevel] = UserFieldsLevel.essential):
    """
    Get a user by user_id from the users collection with optional field filtering.

    Args:
        user_id: The user's unique identifier (string)
        fields: Field selection level (minimal, essential). Defaults to 'essential'.

    Returns:
        dict: The user document (filtered based on 'fields' parameter)

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Fetching user: {user_id} (fields: {fields})")

        user = await db.users.find_one({"user_id": user_id}, {"dreams": 0})

        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=404,
                detail=f"User with id '{user_id}' not found"
            )

        # Convert ObjectId to string for JSON serialization
        if "_id" in user:
            user["_id"] = str(user["_id"])

        user["dreams_count"] = await db.dreams.count_documents({"user_id": user_id})

        logger.info(f"Successfully retrieved user: {user_id} (fields: {fields})")
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching user"
        )


@router.post("/register", status_code=201, tags=["users"])
async def register_user(user_data: CreateUserRequest):
    """
    Create a new user in the users collection.

    Args:
        user_data: User information (user_id, email, firstname, lastname)

    Returns:
        dict: Created user document with _id and created_at

    Raises:
        400: Invalid input or user_id already exists
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"Creating user: {user_data.user_id}")

        # Check if user already exists by user_id
        existing_user = await db.users.find_one({"user_id": user_data.user_id})
        if existing_user:
            logger.warning(f"User already exists: {user_data.user_id}")
            raise HTTPException(
                status_code=400,
                detail=f"User with id '{user_data.user_id}' already exists"
            )

        # Check if email is already taken
        existing_email = await db.users.find_one({"email": user_data.email})
        if existing_email:
            logger.warning(f"Email already registered: {user_data.email}")
            raise HTTPException(
                status_code=409,
                detail=f"Email '{user_data.email}' is already registered"
            )

        # Create user document
        user_doc = {
            "user_id": user_data.user_id,
            "email": user_data.email,
            "firstname": user_data.firstname,
            "lastname": user_data.lastname if user_data.lastname else "",
            "created_at": datetime.now(timezone.utc),
            "couragePoints": 0,
            "plan": "free",
            "dreams_metadata": [],
            "pref_timezone": user_data.pref_timezone,
            "pref_notification_time": user_data.pref_notification_time,
            "last_activity": None,
            "communityProfile": {
                "location": None,
                "age": None,
                "shareAnonymousByDefault": False
            },
            "communityStats": {
                "permissionsGiven": 0,
                "permissionsReceived": 0
            }
        }

        # Insert into database
        result = await db.users.insert_one(user_doc)

        # Get the created document
        created_user = await db.users.find_one({"_id": result.inserted_id})

        # Convert ObjectId to string for JSON serialization
        if "_id" in created_user:
            created_user["_id"] = str(created_user["_id"])
        if "created_at" in created_user:
            created_user["created_at"] = created_user["created_at"].isoformat()

        logger.info(f"Successfully created user: {user_data.user_id}")
        return created_user

    except HTTPException:
        raise
    except DuplicateKeyError as e:
        logger.warning(f"Duplicate key error creating user {user_data.user_id}: {e}")
        raise HTTPException(
            status_code=409,
            detail="A user with this email or id already exists"
        )
    except Exception as e:
        logger.error(f"Error creating user {user_data.user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while creating user"
        )


@router.put("/{user_id}/recents", status_code=200, tags=["users"])
async def update_recents(user_id: str, update_data: UpdateRecentsRequest):
    """
    Update the user's recents array with a newly accessed dream.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Request body containing thread_id of the dream

    Returns:
        dict: Success status, message, and updated recents array

    Raises:
        400: Dream is not active
        404: User or dream not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        thread_id = update_data.thread_id

        logger.info(f"Updating recents for user {user_id} with thread_id: {thread_id}")

        # Validate input
        if not thread_id:
            logger.warning(f"Missing thread_id for user {user_id}")
            raise HTTPException(
                status_code=400,
                detail="thread_id is required"
            )

        # Find user
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Verify dream exists and is active
        dream = await db.dreams.find_one({"thread_id": thread_id, "user_id": user_id})
        if dream is None:
            logger.warning(f"Dream not found: {thread_id} for user {user_id}")
            raise HTTPException(
                status_code=404,
                detail="Dream not found"
            )

        if dream.get("status") != "active":
            logger.warning(f"Dream is not active: {thread_id} (status: {dream.get('status')})")
            raise HTTPException(
                status_code=400,
                detail="Cannot add inactive dream to recents"
            )

        # Update recents array
        recents = user.get("recents", [])
        if not isinstance(recents, list):
            recents = []

        # Remove thread_id if it already exists
        recents = [id for id in recents if id != thread_id]

        # Add thread_id to the front
        recents.insert(0, thread_id)

        # Keep only first 3 items
        recents = recents[:3]

        # Update user document with new recents array and updated_at timestamp
        await db.users.find_one_and_update(
            {"user_id": user_id},
            {
                "$set": {
                    "recents": recents,
                    "updated_at": datetime.now(timezone.utc)
                }
            },
            return_document=True
        )

        logger.info(f"Successfully updated recents for user {user_id}: {recents}")

        return {
            "success": True,
            "message": "Recents updated successfully",
            "recents": recents
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating recents for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while updating recents"
        )


@router.put("/{user_id}/up_next", status_code=200, tags=["users"])
async def update_up_next(user_id: str, update_data: UpdateUpNextRequest):
    """
    Update the user's up_next field with the next incomplete milestone.

    The frontend calculates which milestone should be "next up" and sends it to
    the backend for persistence. This endpoint stores the milestone data as-is.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Request body containing up_next milestone data or null

    Returns:
        dict: Success status, message, and the up_next data that was stored

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        up_next_value = update_data.up_next

        if up_next_value is None:
            logger.info(f"Clearing up_next for user {user_id} (no incomplete milestones)")
        else:
            logger.info(f"Updating up_next for user {user_id} to milestone {up_next_value.milestone_id}")

        # Find user
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Prepare up_next data for storage
        if up_next_value is None:
            up_next_to_store = None
        else:
            # Convert the up_next data to a dictionary for storage
            up_next_to_store = up_next_value.model_dump()

        # Update user document with new up_next
        await db.users.find_one_and_update(
            {"user_id": user_id},
            {
                "$set": {
                    "up_next": up_next_to_store
                }
            },
            return_document=True
        )

        # Prepare response
        if up_next_value is None:
            logger.warning(f"User {user_id} updated up_next to null (no incomplete milestones)")
            return {
                "success": True,
                "message": "Up next cleared - no incomplete milestones found",
                "up_next": None
            }
        else:
            logger.info(f"Successfully updated up_next for user {user_id} to milestone {up_next_value.milestone_id}")
            return {
                "success": True,
                "message": "Up next updated successfully",
                "up_next": up_next_to_store
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating up_next for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while updating up_next"
        )


@router.put("/{user_id}/streak/update", status_code=200, tags=["users"])
async def update_streak(user_id: str, update_data: UpdateStreakRequest):
    """
    Update the user's streak data after a milestone completion.

    Calculates streak continuation, checks for achievement milestones (3-day, 7-day, 30-day),
    and returns updated streak information for frontend notifications and modals.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Milestone completion data including completion_date

    Returns:
        dict: Success status, streak data, and achievement information

    Raises:
        400: Invalid request body or future completion_date
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        # Validate required fields
        if not update_data.milestone_id:
            raise HTTPException(
                status_code=400,
                detail="milestone_id is required"
            )
        if not update_data.completion_date:
            raise HTTPException(
                status_code=400,
                detail="completion_date is required"
            )

        # Parse completion_date
        try:
            # Handle ISO 8601 format by replacing Z with +00:00 for fromisoformat compatibility
            iso_string = update_data.completion_date.replace('Z', '+00:00')
            completion_dt = datetime.fromisoformat(iso_string)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid ISO 8601 timestamp format for completion_date"
            )

        # Validate completion_date is not in the future
        now_utc = datetime.now(timezone.utc)
        if completion_dt > now_utc:
            logger.warning(f"User {user_id} submitted future completion_date: {completion_dt}")
            raise HTTPException(
                status_code=400,
                detail="completion_date cannot be in the future"
            )

        logger.info(f"Updating streak for user {user_id} with milestone {update_data.milestone_id}")

        # Find user
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Initialize streak if missing
        streak = user.get("streak")
        if not streak:
            streak = {
                "current_streak": 0,
                "longest_streak": 0,
                "last_completion_date": None,
                "total_completions": 0,
                "streak_freeze_available": False,
                "milestone_achievements": {
                    "three_day_count": 0,
                    "seven_day_count": 0,
                    "thirty_day_count": 0
                }
            }

        # Calculate days since last completion
        last_completion = streak.get("last_completion_date")

        if last_completion is None:
            days_diff = float('inf')  # First completion ever
        else:
            # Parse last_completion if it's a string
            if isinstance(last_completion, str):
                iso_string = last_completion.replace('Z', '+00:00')
                last_completion_dt = datetime.fromisoformat(iso_string)
            else:
                last_completion_dt = last_completion

            today = completion_dt.date()
            last_date = last_completion_dt.date()
            days_diff = (today - last_date).days

        streak_increased = False
        streak_broken = False
        milestone_achieved = None

        if days_diff == 0:
            # Same day - increment completion counter but don't change streak
            streak["total_completions"] += 1
            streak["last_completion_date"] = completion_dt.isoformat()

        elif days_diff == 1 or days_diff == float('inf'):
            # Next day or first completion - extend streak
            streak["current_streak"] += 1
            streak["total_completions"] += 1
            streak["last_completion_date"] = completion_dt.isoformat()

            # Update longest streak if current exceeds it
            if streak["current_streak"] > streak["longest_streak"]:
                streak["longest_streak"] = streak["current_streak"]

            streak_increased = True

            # Check for achievement milestones
            if streak["current_streak"] == 3:
                streak["milestone_achievements"]["three_day_count"] += 1
                milestone_achieved = "3_day"
            elif streak["current_streak"] == 7:
                streak["milestone_achievements"]["seven_day_count"] += 1
                milestone_achieved = "7_day"
            elif streak["current_streak"] == 30:
                streak["milestone_achievements"]["thirty_day_count"] += 1
                milestone_achieved = "30_day"

        elif days_diff > 1:
            # Missed one or more days
            if streak.get("streak_freeze_available", False):
                # Use streak freeze power-up
                streak["streak_freeze_available"] = False
                streak["total_completions"] += 1
                streak["last_completion_date"] = completion_dt.isoformat()
                logger.info(f"User {user_id} used streak freeze to continue {streak['current_streak']} day streak")
            else:
                # Streak broken - reset to 1
                streak["current_streak"] = 1
                streak["total_completions"] += 1
                streak["last_completion_date"] = completion_dt.isoformat()
                streak_broken = True
                logger.info(f"User {user_id} streak broken (missed {days_diff} days)")

        else:
            # Past date (shouldn't happen, but handle gracefully)
            streak["total_completions"] += 1
            streak["last_completion_date"] = completion_dt.isoformat()

        # Update user document with new streak
        await db.users.find_one_and_update(
            {"user_id": user_id},
            {
                "$set": {
                    "streak": streak
                }
            },
            return_document=True
        )

        # Build response message
        if streak_broken:
            message = "Streak broken! Starting over at day 1"
        elif milestone_achieved:
            achievement_names = {
                "3_day": "On Fire!",
                "7_day": "Week Warrior!",
                "30_day": "Legend!"
            }
            message = f"Streak updated to {streak['current_streak']} days! Achievement unlocked: {achievement_names[milestone_achieved]}!"
        elif streak_increased:
            message = f"Streak updated to {streak['current_streak']} days"
        else:
            message = f"Milestone completed (streak continued at {streak['current_streak']} days)"

        if milestone_achieved:
            logger.info(f"User {user_id} milestone achievement unlocked: {milestone_achieved}")
        elif streak_increased:
            logger.info(f"User {user_id} streak updated: {streak['current_streak']} days")

        return {
            "success": True,
            "message": message,
            "streak_data": streak,
            "streak_increased": streak_increased,
            "streak_broken": streak_broken,
            "milestone_achieved": milestone_achieved
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating streak for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while updating streak"
        )


@router.get("/{user_id}/streak", status_code=200, tags=["users"])
async def get_streak(user_id: str):
    """
    Fetch the current streak data for a user with automatic recalculation.

    Checks if the user's streak needs to be recalculated (e.g., if a day has passed
    since the last completion) and updates it in the database if necessary.

    Args:
        user_id: The user's unique identifier (Firebase UID)

    Returns:
        dict: Success status, streak data, and recalculation flags

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        logger.error("Database not connected")
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[getStreak] Fetching streak for user {user_id}")

        # Find user
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Initialize streak if missing
        streak = user.get("streak")
        if not streak:
            streak = {
                "current_streak": 0,
                "longest_streak": 0,
                "last_completion_date": None,
                "total_completions": 0,
                "streak_freeze_available": False,
                "milestone_achievements": {
                    "three_day_count": 0,
                    "seven_day_count": 0,
                    "thirty_day_count": 0
                }
            }

        # Track recalculation state
        recalculated = False
        streak_broken = False
        current_streak_before = streak.get("current_streak", 0)

        # Check if recalculation is needed
        last_completion = streak.get("last_completion_date")

        if last_completion is not None:
            # Parse last_completion if it's a string
            if isinstance(last_completion, str):
                iso_string = last_completion.replace('Z', '+00:00')
                last_completion_dt = datetime.fromisoformat(iso_string)
            else:
                last_completion_dt = last_completion

            # Get current time in UTC
            now_utc = datetime.now(timezone.utc)

            # Calculate days difference
            today = now_utc.date()
            last_date = last_completion_dt.date()
            days_diff = (today - last_date).days

            logger.info(f"[getStreak] User {user_id}: days_diff={days_diff}, current_streak_before={current_streak_before}")

            # Handle missed days (days_diff > 1)
            if days_diff > 1:
                logger.info(f"[getStreak] User {user_id} missed {days_diff} days")

                if streak.get("streak_freeze_available", False):
                    # Use streak freeze power-up
                    streak["streak_freeze_available"] = False
                    streak["last_completion_date"] = now_utc.isoformat()
                    logger.info(f"[getStreak] User {user_id} used streak freeze to prevent reset")
                    recalculated = True
                else:
                    # Streak broken - reset to 0
                    streak["current_streak"] = 0
                    streak["last_completion_date"] = None
                    streak_broken = True
                    recalculated = True
                    logger.info(f"[getStreak] User {user_id} streak broken - reset to 0")

        # Save updated streak if recalculation happened
        if recalculated:
            await db.users.find_one_and_update(
                {"user_id": user_id},
                {
                    "$set": {
                        "streak": streak
                    }
                },
                return_document=True
            )

        current_streak_after = streak.get("current_streak", 0)
        logger.info(f"[getStreak] User {user_id}, Current streak after: {current_streak_after}, Recalculated: {recalculated}, Broken: {streak_broken}")

        return {
            "success": True,
            "message": "Streak fetched successfully",
            "streak_data": streak,
            "streak_broken": streak_broken,
            "recalculated": recalculated
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching streak for user {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal server error while fetching streak"
        )


class UpdatePlanRequest(BaseModel):
    """Request schema for updating a user's plan"""
    plan: str = Field(..., description="Plan tier ('free' or 'pro')")


@router.patch("/{user_id}/plan", status_code=200, tags=["users"])
async def update_plan(user_id: str, update_data: UpdatePlanRequest):
    """
    Update a user's subscription plan.

    Called by the frontend after a successful RevenueCat purchase or restore.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Request body containing the new plan tier

    Returns:
        dict: Success status and updated plan

    Raises:
        400: Invalid plan value
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    valid_plans = ["free", "pro"]
    if update_data.plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {valid_plans}")

    try:
        result = await db.users.find_one_and_update(
            {"user_id": user_id},
            {"$set": {"plan": update_data.plan}},
            return_document=True
        )

        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"Updated plan for user {user_id} to: {update_data.plan}")

        return {
            "success": True,
            "message": f"Plan updated to {update_data.plan}",
            "plan": update_data.plan
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating plan for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating plan")


class UpdateTimezoneRequest(BaseModel):
    """Request schema for updating user's timezone"""
    pref_timezone: str = Field(..., description="User's preferred timezone (IANA timezone identifier)")


@router.patch("/{user_id}/timezone", status_code=200, tags=["users"])
async def update_timezone(user_id: str, update_data: UpdateTimezoneRequest):
    """
    Update a user's preferred timezone.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Request body containing the new timezone

    Returns:
        dict: Success status and updated timezone

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        result = await db.users.find_one_and_update(
            {"user_id": user_id},
            {"$set": {"pref_timezone": update_data.pref_timezone, "updated_at": datetime.now(timezone.utc)}},
            return_document=True
        )

        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"Updated timezone for user {user_id} to: {update_data.pref_timezone}")

        return {
            "success": True,
            "message": f"Timezone updated to {update_data.pref_timezone}",
            "pref_timezone": update_data.pref_timezone
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating timezone for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating timezone")


class UpdateNotificationPreferencesRequest(BaseModel):
    """Request schema for updating notification preferences"""
    pref_notification_time: Optional[str] = Field(..., description="Preferred notification time in HH:MM format (24-hour), or null to disable")


@router.patch("/{user_id}/notification-preferences", status_code=200, tags=["users"])
async def update_notification_preferences(user_id: str, update_data: UpdateNotificationPreferencesRequest):
    """
    Update a user's notification preferences (daily reminder time).

    Args:
        user_id: The user's unique identifier (Firebase UID)
        update_data: Request body containing the notification time preference (HH:MM or null)

    Returns:
        dict: Success status and updated notification time

    Raises:
        400: Invalid time format
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        # Validate time format if provided (HH:MM in 24-hour format)
        if update_data.pref_notification_time is not None:
            time_parts = update_data.pref_notification_time.split(":")
            if len(time_parts) != 2:
                raise HTTPException(status_code=400, detail="Invalid time format. Expected HH:MM")
            try:
                hour, minute = int(time_parts[0]), int(time_parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid time format. Hour must be 0-23, minute must be 0-59"
                )

        result = await db.users.find_one_and_update(
            {"user_id": user_id},
            {"$set": {
                "pref_notification_time": update_data.pref_notification_time,
                "updated_at": datetime.now(timezone.utc)
            }},
            return_document=True
        )

        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        status_msg = "disabled" if update_data.pref_notification_time is None else f"set to {update_data.pref_notification_time}"
        logger.info(f"Updated notification preferences for user {user_id}: {status_msg}")

        return {
            "success": True,
            "message": f"Notification preferences {status_msg}",
            "pref_notification_time": update_data.pref_notification_time
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating notification preferences for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while updating notification preferences")


class UpdateActivityRequest(BaseModel):
    last_activity: str


@router.put("/{user_id}/activity", status_code=200, tags=["users"])
async def update_activity(user_id: str, update_data: UpdateActivityRequest):
    """Record the user's last activity timestamp."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        result = await db.users.find_one_and_update(
            {"user_id": user_id},
            {"$set": {"last_activity": update_data.last_activity}},
            return_document=True
        )

        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        logger.info(f"Updated last_activity for user {user_id}")
        return {"success": True, "last_activity": update_data.last_activity}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating activity for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


class SavePushTokenRequest(BaseModel):
    """Request schema for saving a push token"""
    pushToken: str = Field(..., description="Expo push token (e.g., ExponentPushToken[xxx])")


@router.post("/{user_id}/push-token", status_code=200, tags=["users"])
async def save_push_token(user_id: str, data: SavePushTokenRequest):
    """
    Save or update a user's Expo push token for notifications.

    Stores the token in push_tokens array (supports multiple devices).
    Deduplicates — won't add the same token twice.

    Args:
        user_id: The user's unique identifier
        data: Request body containing the Expo push token

    Returns:
        dict: Success status

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        # Validate token format
        if not data.pushToken.startswith("ExponentPushToken["):
            raise HTTPException(status_code=400, detail="Invalid Expo push token format")

        # Use $addToSet to avoid duplicates
        result = await db.users.find_one_and_update(
            {"user_id": user_id},
            {
                "$addToSet": {"push_tokens": data.pushToken},
                "$set": {"updated_at": datetime.now(timezone.utc)}
            },
            return_document=True
        )

        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        token_count = len(result.get("push_tokens", []))
        logger.info(f"Saved push token for user {user_id} (total tokens: {token_count})")

        return {"success": True, "message": "Push token saved", "token_count": token_count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving push token for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{user_id}/welcome-notification", status_code=200, tags=["users"])
async def send_welcome(user_id: str):
    """
    Send a welcome push notification to a newly registered user.

    Called by the frontend after the push token has been saved during signup.

    Args:
        user_id: The user's unique identifier

    Returns:
        dict: Success status and send count

    Raises:
        404: User not found
        500: Server error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        result = await send_welcome_notification(user_id)

        logger.info(f"Welcome notification result for user {user_id}: {result}")
        return {
            "success": result["sent"] > 0,
            "message": f"Welcome notification sent ({result['sent']} delivered)",
            "sent": result["sent"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending welcome notification for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ============================================================================
# DATA MANAGEMENT ENDPOINTS (Your Data screen)
# ============================================================================


@router.get("/{user_id}/personal-data", status_code=200, tags=["users"])
async def get_personal_data(user_id: str):
    """
    Return all personal data we hold about a user.

    Used by the "Your Data" screen so the user can view everything stored about them.

    Args:
        user_id: The user's unique identifier (Firebase UID)

    Returns:
        dict: Comprehensive personal data summary

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[getPersonalData] Fetching personal data for user {user_id}")

        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Count dreams
        dreams_count = await db.dreams.count_documents({"user_id": user_id})

        # Count milestones (total and completed) across all dreams
        pipeline = [
            {"$match": {"user_id": user_id}},
            {"$project": {
                "milestones": "$roadmap.milestones",
            }},
            {"$unwind": "$milestones"},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "completed": {
                    "$sum": {"$cond": [{"$eq": ["$milestones.status", "completed"]}, 1, 0]}
                },
            }},
        ]
        milestone_stats = await db.dreams.aggregate(pipeline).to_list(length=1)
        total_milestones = milestone_stats[0]["total"] if milestone_stats else 0
        completed_milestones = milestone_stats[0]["completed"] if milestone_stats else 0

        # Count community posts (victory cards + journey recaps)
        victory_count = await db.victory_cards.count_documents({"userId": user_id})
        recap_count = await db.journey_recaps.count_documents({"userId": user_id})
        community_posts_count = victory_count + recap_count

        # Check push token registration
        push_tokens = user.get("push_tokens", [])
        push_token_registered = len(push_tokens) > 0

        # Serialize created_at
        created_at = user.get("created_at")
        if created_at is not None and not isinstance(created_at, str):
            created_at = created_at.isoformat()

        # Serialize streak dates
        streak = user.get("streak")
        if streak and isinstance(streak.get("last_completion_date"), datetime):
            streak["last_completion_date"] = streak["last_completion_date"].isoformat()

        # Serialize last_activity
        last_activity = user.get("last_activity")
        if last_activity is not None and not isinstance(last_activity, str):
            last_activity = last_activity.isoformat()

        response = {
            "user_id": user.get("user_id"),
            "firstname": user.get("firstname", ""),
            "lastname": user.get("lastname", ""),
            "email": user.get("email", ""),
            "created_at": created_at,
            "pref_timezone": user.get("pref_timezone"),
            "pref_notification_time": user.get("pref_notification_time"),
            "streak": streak,
            "dreams_count": dreams_count,
            "completed_milestones_count": completed_milestones,
            "total_milestones_count": total_milestones,
            "community_posts_count": community_posts_count,
            "last_activity": last_activity,
            "push_token_registered": push_token_registered,
        }

        logger.info(f"[getPersonalData] Successfully retrieved personal data for user {user_id}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[getPersonalData] Error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching personal data")


class DataExportRequest(BaseModel):
    """Request schema for data export"""
    format: str = Field(default="json", description="Export format (json or csv)")


@router.post("/{user_id}/data-export", status_code=200, tags=["users"])
async def export_user_data(user_id: str, request_data: DataExportRequest):
    """
    Export all user data in a portable format.

    Gathers data from all collections related to the user and returns it as a
    single JSON document. Used by the "Your Data" screen for GDPR-style data portability.

    Args:
        user_id: The user's unique identifier (Firebase UID)
        request_data: Export options (format)

    Returns:
        dict: Complete user data export

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[exportUserData] Exporting data for user {user_id}")

        user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Serialize datetime fields in user doc
        for key in ["created_at", "updated_at", "last_activity"]:
            val = user.get(key)
            if val is not None and not isinstance(val, str):
                user[key] = val.isoformat()

        streak = user.get("streak")
        if streak and isinstance(streak.get("last_completion_date"), datetime):
            streak["last_completion_date"] = streak["last_completion_date"].isoformat()

        # Fetch all dreams with full roadmaps
        dreams_cursor = db.dreams.find({"user_id": user_id}, {"_id": 0})
        dreams = await dreams_cursor.to_list(length=None)
        for dream in dreams:
            for key in ["created_at", "updated_at"]:
                val = dream.get(key)
                if val is not None and not isinstance(val, str):
                    dream[key] = val.isoformat()

        # Fetch victory cards
        victories_cursor = db.victory_cards.find({"userId": user_id}, {"_id": 0})
        victories = await victories_cursor.to_list(length=None)
        for v in victories:
            for key in ["createdAt", "completedDate"]:
                val = v.get(key)
                if val is not None and not isinstance(val, str):
                    v[key] = val.isoformat()

        # Fetch journey recaps
        recaps_cursor = db.journey_recaps.find({"userId": user_id}, {"_id": 0})
        recaps = await recaps_cursor.to_list(length=None)
        for r in recaps:
            for key in ["createdAt"]:
                val = r.get(key)
                if val is not None and not isinstance(val, str):
                    r[key] = val.isoformat()

        # Fetch courage boosts given by user
        boosts_cursor = db.courage_boosts.find({"giverId": user_id}, {"_id": 0})
        boosts_given = await boosts_cursor.to_list(length=None)
        for b in boosts_given:
            if isinstance(b.get("createdAt"), datetime):
                b["createdAt"] = b["createdAt"].isoformat()

        # Fetch permission slips given by user
        permissions_cursor = db.permission_slips.find({"giverId": user_id}, {"_id": 0})
        permissions_given = await permissions_cursor.to_list(length=None)
        for p in permissions_given:
            if isinstance(p.get("createdAt"), datetime):
                p["createdAt"] = p["createdAt"].isoformat()

        export_data = {
            "account": {
                "user_id": user.get("user_id"),
                "email": user.get("email"),
                "firstname": user.get("firstname"),
                "lastname": user.get("lastname"),
                "created_at": user.get("created_at"),
                "plan": user.get("plan"),
                "pref_timezone": user.get("pref_timezone"),
                "pref_notification_time": user.get("pref_notification_time"),
                "couragePoints": user.get("couragePoints", 0),
            },
            "streak": user.get("streak"),
            "dreams": dreams,
            "victory_cards": victories,
            "journey_recaps": recaps,
            "courage_boosts_given": boosts_given,
            "permission_slips_given": permissions_given,
            "community_profile": user.get("communityProfile"),
            "community_stats": user.get("communityStats"),
        }

        logger.info(f"[exportUserData] Successfully exported data for user {user_id} "
                     f"({len(dreams)} dreams, {len(victories)} victories, {len(recaps)} recaps)")

        return {
            "success": True,
            "message": "Data exported successfully",
            "data": export_data,
            "export_date": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[exportUserData] Error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while exporting data")


@router.get("/{user_id}/export-dreams", status_code=200, tags=["users"])
async def export_dreams(user_id: str):
    """
    Export user's dreams, milestones, and progress data.

    Returns a structured export of all dreams with their roadmaps (milestones)
    and the user's streak/progress data. Used by the "Your Data" screen.

    Args:
        user_id: The user's unique identifier (Firebase UID)

    Returns:
        dict: Dreams and progress export

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[exportDreams] Exporting dreams for user {user_id}")

        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Fetch all dreams with full roadmaps
        dreams_cursor = db.dreams.find({"user_id": user_id}, {"_id": 0})
        raw_dreams = await dreams_cursor.to_list(length=None)

        dreams_export = []
        for dream in raw_dreams:
            milestones = dream.get("roadmap", {}).get("milestones", [])
            milestones_export = []
            for m in milestones:
                milestone_data = {
                    "title": m.get("title", ""),
                    "status": m.get("status", "pending"),
                    "challenge_type": m.get("challenge_type", ""),
                }
                updated = m.get("updated_at")
                if updated is not None:
                    if not isinstance(updated, str):
                        updated = updated.isoformat()
                    if m.get("status") == "completed":
                        milestone_data["completed_at"] = updated
                milestones_export.append(milestone_data)

            created_at = dream.get("created_at")
            if created_at is not None and not isinstance(created_at, str):
                created_at = created_at.isoformat()

            dream_data = {
                "dream_title": dream.get("dream", ""),
                "status": dream.get("status", "active"),
                "created_at": created_at,
                "milestones": milestones_export,
            }

            # Add completed_at if dream is complete
            if dream.get("isComplete"):
                updated_at = dream.get("updated_at")
                if updated_at is not None and not isinstance(updated_at, str):
                    updated_at = updated_at.isoformat()
                dream_data["completed_at"] = updated_at

            dreams_export.append(dream_data)

        # Get streak data
        streak = user.get("streak")
        if streak and isinstance(streak.get("last_completion_date"), datetime):
            streak["last_completion_date"] = streak["last_completion_date"].isoformat()

        logger.info(f"[exportDreams] Successfully exported {len(dreams_export)} dreams for user {user_id}")

        return {
            "success": True,
            "dreams": dreams_export,
            "streak_data": streak,
            "export_date": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[exportDreams] Error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while exporting dreams")


@router.delete("/{user_id}", status_code=200, tags=["users"])
async def delete_account(user_id: str):
    """
    Permanently delete a user's account and all associated data.

    Removes the user document and all related data across every collection:
    dreams, victory cards, journey recaps, courage boosts, permission slips,
    me-too reactions, and streaks.

    Args:
        user_id: The user's unique identifier (Firebase UID)

    Returns:
        dict: Success status with deletion summary

    Raises:
        404: User not found
        500: Database error
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[deleteAccount] Starting account deletion for user {user_id}")

        # Verify user exists
        user = await db.users.find_one({"user_id": user_id})
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Delete Firebase Authentication user first
        try:
            firebase_deleted = await delete_firebase_user(user_id)
            if firebase_deleted:
                logger.info(f"[deleteAccount] Firebase auth user deleted for {user_id}")
            else:
                logger.warning(f"[deleteAccount] Firebase auth deletion skipped for {user_id} (SDK not configured)")
        except Exception as e:
            logger.error(f"[deleteAccount] Firebase auth deletion failed for {user_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Failed to delete authentication account. Please try again or contact support."
            )

        # Delete from all collections
        deleted_counts = {}

        # 1. Delete all dreams
        result = await db.dreams.delete_many({"user_id": user_id})
        deleted_counts["dreams"] = result.deleted_count
        logger.info(f"[deleteAccount] Deleted {result.deleted_count} dreams for user {user_id}")

        # 2. Delete victory cards
        result = await db.victory_cards.delete_many({"userId": user_id})
        deleted_counts["victory_cards"] = result.deleted_count

        # 3. Delete journey recaps
        result = await db.journey_recaps.delete_many({"userId": user_id})
        deleted_counts["journey_recaps"] = result.deleted_count

        # 4. Delete courage boosts (given and received)
        result = await db.courage_boosts.delete_many(
            {"$or": [{"giverId": user_id}, {"receiverId": user_id}]}
        )
        deleted_counts["courage_boosts"] = result.deleted_count

        # 5. Delete permission slips (given and received)
        result = await db.permission_slips.delete_many(
            {"$or": [{"giverId": user_id}, {"receiverId": user_id}]}
        )
        deleted_counts["permission_slips"] = result.deleted_count

        # 6. Delete me-too reactions
        result = await db.me_toos.delete_many({"userId": user_id})
        deleted_counts["me_toos"] = result.deleted_count

        # 7. Delete streak data
        result = await db.streaks.delete_many({"user_id": user_id})
        deleted_counts["streaks"] = result.deleted_count

        # 8. Delete the user document itself (last)
        result = await db.users.delete_one({"user_id": user_id})
        deleted_counts["user"] = result.deleted_count

        logger.info(f"[deleteAccount] Account deletion complete for user {user_id}: {deleted_counts}")

        return {
            "success": True,
            "message": "Account and all associated data have been permanently deleted",
            "deleted": deleted_counts,
            "deletion_scheduled": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[deleteAccount] Error for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while deleting account")

