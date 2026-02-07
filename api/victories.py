"""
Victory Cards API endpoints for community features
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from core.database import get_db
from models.community import (
    CreateVictoryRequest,
    CreateVictoryResponse,
    VictoryCardResponse,
    VictoriesListResponse,
    VictoryCardDB,
    CourageBoostDB,
    BoostVictoryResponse,
    PaginationInfo,
    PermissionSlipDB,
    GivePermissionRequest,
    GivePermissionResponse,
    PermissionSlipResponse,
    VictoryPermissionsResponse,
    MeTooDB,
    ToggleMeTooResponse,
    generate_victory_id,
    generate_boost_id,
    generate_permission_id,
    generate_metoo_id,
    get_current_iso_timestamp,
    get_permission_text,
    DREAM_CATEGORIES,
    IMPACT_LEVELS
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ====================
# HELPER FUNCTIONS
# ====================

def calculate_timeframe_filter(timeframe: Optional[str]) -> dict:
    """Calculate date filter based on timeframe parameter"""
    if not timeframe or timeframe == "all":
        return {}

    now = datetime.now(timezone.utc)

    if timeframe == "week":
        start_date = now - timedelta(days=7)
    elif timeframe == "month":
        start_date = now - timedelta(days=30)
    else:
        return {}

    return {"createdAt": {"$gte": start_date.isoformat()}}


def parse_categories_filter(categories: Optional[str]) -> dict:
    """Parse comma-separated categories into MongoDB filter"""
    if not categories:
        return {}

    category_list = [cat.strip() for cat in categories.split(",")]
    # Validate categories
    valid_categories = [cat for cat in category_list if cat in DREAM_CATEGORIES]

    if not valid_categories:
        return {}

    return {"dreamCategory": {"$in": valid_categories}}


# ====================
# VICTORY CARD ENDPOINTS
# ====================

@router.get("", tags=["victories"])
async def get_victories(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    categories: Optional[str] = Query(None, description="Comma-separated list of dream categories"),
    timeframe: Optional[str] = Query(None, description="Filter by time - 'week', 'month', or omit for 'all'"),
    user_id: Optional[str] = Query(None, description="User ID for personalized hasUserBoosted/hasUserMeTooed flags")
):
    """
    Fetch combined feed of victory cards and journey recaps with filtering and pagination

    Query Parameters:
    - page: Page number (default: 1)
    - limit: Items per page (default: 20, max: 100)
    - categories: Comma-separated dream categories
    - timeframe: 'week', 'month', or omit for 'all'
    - user_id: Optional user ID for personalized flags
    """
    try:
        db = get_db()

        # Build query filter
        query_filter = {}

        # Add category filter
        category_filter = parse_categories_filter(categories)
        if category_filter:
            query_filter.update(category_filter)

        # Add timeframe filter
        timeframe_filter = calculate_timeframe_filter(timeframe)
        if timeframe_filter:
            query_filter.update(timeframe_filter)

        # 1. Fetch victory cards
        victories_cursor = db.victory_cards.find(query_filter)
        victories_list = await victories_cursor.to_list(length=None)

        # 2. Fetch journey recaps
        journeys_cursor = db.journey_recaps.find(query_filter)
        journeys_list = await journeys_cursor.to_list(length=None)

        # 3. Transform to unified format with type field
        victory_items = []
        for victory_doc in victories_list:
            item = {
                "type": "victory_card",
                "id": victory_doc.get("id", str(victory_doc.get("_id"))),
                "userId": victory_doc["userId"],
                "userDisplayName": victory_doc.get("userDisplayName", "Anonymous"),
                "userLocation": victory_doc.get("userLocation"),
                "userAge": victory_doc.get("userAge"),
                "prefTimezone": victory_doc.get("prefTimezone"),
                "milestoneId": victory_doc["milestoneId"],
                "milestoneTitle": victory_doc.get("milestoneTitle", "Untitled"),
                "challengeType": victory_doc.get("challengeType"),  # Include challenge type from milestone
                "dreamId": victory_doc["dreamId"],
                "dreamCategory": victory_doc.get("dreamCategory", "achievement_goals"),
                "evidenceSnippet": victory_doc.get("evidenceSnippet"),  # Can be None
                "confidenceBoost": victory_doc.get("confidenceBoost", 0),
                "impactLevel": victory_doc.get("impactLevel", "medium"),
                "completedDate": victory_doc.get("completedDate", ""),
                "createdAt": victory_doc.get("createdAt", ""),
                "courageBoosts": victory_doc.get("courageBoosts", 0),
                "hasUserBoosted": None,
                "permissionsCount": victory_doc.get("permissionsCount", 0),
                "meTooCount": victory_doc.get("meTooCount", 0),
                "hasUserMeTooed": None,
                "isAnonymous": victory_doc.get("isAnonymous", False)
            }
            victory_items.append(item)

        journey_items = []
        for journey_doc in journeys_list:
            item = {
                "type": "journey_recap",
                "id": str(journey_doc.get("_id")),
                "userId": journey_doc["userId"],
                "userDisplayName": journey_doc.get("userDisplayName", "Anonymous"),
                "userLocation": journey_doc.get("userLocation"),
                "userAge": journey_doc.get("userAge"),
                "prefTimezone": journey_doc.get("prefTimezone"),
                "dreamId": journey_doc["dreamId"],
                "dreamTitle": journey_doc.get("dreamTitle", "Untitled Dream"),
                "dreamCategory": journey_doc.get("dreamCategory", "achievement_goals"),
                "journeyStory": journey_doc.get("journeyStory", ""),
                "totalMilestones": journey_doc.get("totalMilestones", 0),
                "durationDays": journey_doc.get("durationDays", 0),
                "keyMoment": journey_doc.get("keyMoment"),
                "completedDate": journey_doc.get("completedDate", ""),
                "createdAt": journey_doc.get("createdAt", ""),
                "courageBoosts": journey_doc.get("courageBoosts", 0),
                "hasUserBoosted": None,
                "permissionsCount": journey_doc.get("permissionsCount", 0),
                "meTooCount": journey_doc.get("meTooCount", 0),
                "hasUserMeTooed": None,
                "isAnonymous": journey_doc.get("isAnonymous", False)
            }
            journey_items.append(item)

        # 4. Merge and sort by createdAt (newest first)
        combined_feed = victory_items + journey_items
        combined_feed.sort(key=lambda x: x.get("createdAt", ""), reverse=True)

        # 5. Apply pagination
        total_count = len(combined_feed)
        total_pages = (total_count + limit - 1) // limit
        start_index = (page - 1) * limit
        end_index = start_index + limit
        paginated_feed = combined_feed[start_index:end_index]

        pagination = {
            "page": page,
            "limit": limit,
            "totalPages": total_pages,
            "totalCount": total_count
        }

        return {"feed": paginated_feed, "pagination": pagination}

    except Exception as e:
        logger.error(f"Error fetching victories feed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("", response_model=CreateVictoryResponse, status_code=201, tags=["victories"])
async def create_victory(victory_data: CreateVictoryRequest):
    """
    Create a new victory card from a completed milestone

    Business Logic:
    1. Validate milestone exists and is completed
    2. Pull milestone data (title, XP, impact, status)
    3. Pull dream data (title, category)
    4. Pull user data (name, age, location) unless isAnonymous is true
    5. Create VictoryCard document in database
    6. Award +5 courage points to user
    7. Update user's couragePoints balance
    """
    try:
        db = get_db()

        # 1. Find the dream containing the milestone
        dream = await db.dreams.find_one({
            "roadmap.milestones.id": victory_data.milestoneId
        })

        if not dream:
            raise HTTPException(status_code=404, detail="Milestone not found")

        # 2. Extract milestone from the dream
        milestone = next(
            (m for m in dream.get("roadmap", {}).get("milestones", [])
             if m.get("id") == victory_data.milestoneId),
            None
        )

        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found in dream")

        # 3. Validate milestone is completed
        if milestone.get("status") != "completed":
            raise HTTPException(status_code=400, detail="Milestone must be completed to create a victory")

        # 4. Check if victory already exists for this milestone within this dream
        existing_victory = await db.victory_cards.find_one({"milestoneId": victory_data.milestoneId, "dreamId": dream["thread_id"]})
        if existing_victory:
            raise HTTPException(status_code=409, detail="Victory already exists for this milestone")

        # 5. Extract user data
        user_id = dream["user_id"]
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

            if not victory_data.isAnonymous:
                user_display_name = user_doc.get("firstname", "Anonymous")
                community_profile = user_doc.get("communityProfile", {})
                user_location = community_profile.get("location")
                user_age = community_profile.get("age")

        # 6. Determine impact level
        impact_level = victory_data.impact or milestone.get("impact", "high")
        if impact_level not in IMPACT_LEVELS:
            impact_level = "high"

        # 7. Create victory card document
        victory_id = generate_victory_id()
        victory_card = VictoryCardDB(
            id=victory_id,
            userId=user_id,
            userDisplayName=user_display_name,
            userLocation=user_location,
            userAge=user_age,
            prefTimezone=pref_timezone,
            milestoneId=victory_data.milestoneId,
            milestoneTitle=milestone.get("title", "Untitled Milestone"),
            challengeType=milestone.get("challenge_type"),  # Include challenge type from milestone
            dreamId=dream.get("thread_id", ""),
            dreamCategory=dream.get("roadmap", {}).get("category") or "achievement_goals",
            evidenceSnippet=victory_data.evidenceSnippet if victory_data.evidenceSnippet else None,
            confidenceBoost=milestone.get("xp_points", 0),
            impactLevel=impact_level,
            completedDate=milestone.get("completedDate", get_current_iso_timestamp()),
            createdAt=get_current_iso_timestamp(),
            courageBoosts=0,
            hasUserBoosted={},
            isAnonymous=victory_data.isAnonymous
        )

        # 8. Insert into database
        await db.victory_cards.insert_one(victory_card.model_dump())

        # 9. Award +5 courage points to user ($inc initializes to 0 if missing)
        courage_points_awarded = 5
        await db.users.update_one(
            {"user_id": user_id},
            {"$inc": {"couragePoints": courage_points_awarded}}
        )

        logger.info(f"Victory card created: {victory_id} for user {user_id}")

        return CreateVictoryResponse(
            success=True,
            victoryId=victory_id,
            couragePointsAwarded=courage_points_awarded
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating victory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{victoryId}", response_model=VictoryCardResponse, tags=["victories"])
async def get_victory(victoryId: str):
    """
    Fetch a single victory card
    """
    try:
        db = get_db()

        victory_doc = await db.victory_cards.find_one({"id": victoryId})

        if not victory_doc:
            raise HTTPException(status_code=404, detail="Victory card not found")

        return VictoryCardResponse(
            id=victory_doc["id"],
            userId=victory_doc["userId"],
            userDisplayName=victory_doc.get("userDisplayName", "Anonymous"),
            userLocation=victory_doc.get("userLocation"),
            userAge=victory_doc.get("userAge"),
            prefTimezone=victory_doc.get("prefTimezone"),
            milestoneId=victory_doc["milestoneId"],
            milestoneTitle=victory_doc.get("milestoneTitle", "Untitled"),
            challengeType=victory_doc.get("challengeType"),  # Include challenge type
            dreamId=victory_doc["dreamId"],
            dreamCategory=victory_doc.get("dreamCategory", "achievement_goals"),
            evidenceSnippet=victory_doc.get("evidenceSnippet"),  # Can be None
            confidenceBoost=victory_doc.get("confidenceBoost", 0),
            impactLevel=victory_doc.get("impactLevel", "medium"),
            completedDate=victory_doc.get("completedDate", ""),
            createdAt=victory_doc.get("createdAt", ""),
            courageBoosts=victory_doc.get("courageBoosts", 0),
            hasUserBoosted=None,  # Not calculated for single view (no user context)
            permissionsCount=victory_doc.get("permissionsCount", 0),
            meTooCount=victory_doc.get("meTooCount", 0),
            hasUserMeTooed=None,  # Not calculated for single view (no user context)
            isAnonymous=victory_doc.get("isAnonymous", False)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching victory {victoryId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================
# COURAGE BOOST ENDPOINTS
# ====================

@router.post("/{victoryId}/boost", response_model=BoostVictoryResponse, tags=["victories"])
async def boost_victory(victoryId: str, giver_user_id: str = Query(..., description="User ID of the person giving the boost")):
    """
    Give a courage boost to a victory card

    Note: In production, giver_user_id should be derived from authentication context (Bearer token)
    For now, it's passed as a query parameter for testing purposes

    Business Logic:
    1. Get authenticated user ID from token (currently from query param)
    2. Check if user has already boosted this victory
    3. If yes, return error (HTTP 409)
    4. Create CourageBoost document
    5. Increment victory card's courageBoosts count
    6. Award +1 courage point to victory card creator
    7. Return new boost count
    """
    try:
        db = get_db()

        # 1. Validate victory card exists
        victory = await db.victory_cards.find_one({"id": victoryId})

        if not victory:
            raise HTTPException(status_code=404, detail="Victory card not found")

        # 2. Check if user has already boosted this victory
        existing_boost = await db.courage_boosts.find_one({
            "victoryCardId": victoryId,
            "giverId": giver_user_id
        })

        if existing_boost:
            raise HTTPException(status_code=409, detail="User has already boosted this victory")

        # 3. Prevent users from boosting their own victories
        if victory["userId"] == giver_user_id:
            raise HTTPException(status_code=400, detail="Cannot boost your own victory")

        # 4. Create courage boost document
        boost_id = generate_boost_id()
        courage_boost = CourageBoostDB(
            id=boost_id,
            victoryCardId=victoryId,
            giverId=giver_user_id,
            receiverId=victory["userId"],
            createdAt=get_current_iso_timestamp()
        )

        # 5. Insert boost into database
        await db.courage_boosts.insert_one(courage_boost.model_dump())

        # 6. Increment victory card's boost count and update hasUserBoosted
        result = await db.victory_cards.find_one_and_update(
            {"id": victoryId},
            {
                "$inc": {"courageBoosts": 1},
                "$set": {f"hasUserBoosted.{giver_user_id}": True}
            },
            return_document=True
        )

        new_boost_count = result["courageBoosts"]

        # 7. Award +1 courage point to victory card creator
        courage_points_awarded = 1

        # Initialize couragePoints if it doesn't exist
        receiver = await db.users.find_one({"user_id": victory["userId"]})
        if receiver and "couragePoints" not in receiver:
            await db.users.update_one(
                {"user_id": victory["userId"]},
                {"$set": {"couragePoints": 0}}
            )

        # Update receiver's courage points
        await db.users.update_one(
            {"user_id": victory["userId"]},
            {"$inc": {"couragePoints": courage_points_awarded}}
        )

        logger.info(f"Courage boost given: {boost_id} from {giver_user_id} to victory {victoryId}")

        return BoostVictoryResponse(
            success=True,
            newBoostCount=new_boost_count,
            couragePointsAwarded=courage_points_awarded
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error boosting victory {victoryId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================
# PERMISSION SLIP ENDPOINTS
# ====================

@router.post("/{victoryId}/permission", response_model=GivePermissionResponse, tags=["victories"])
async def give_permission(
    victoryId: str,
    permission_data: GivePermissionRequest,
    giver_user_id: str = Query(..., description="User ID of the person giving the permission")
):
    """
    Give a permission slip to a victory card

    Note: In production, giver_user_id should be derived from authentication context (Bearer token)
    For now, it's passed as a query parameter for testing purposes

    Business Logic:
    1. Validate permissionType is 1, 2, 3, or 4
    2. Validate victory card exists
    3. Check user hasn't already given a permission to this victory
    4. Get victory card's dream category
    5. Generate permissionText based on type and category
    6. Create permission slip document
    7. Increment permissionsCount on victory card (+1)
    8. Award +5 courage points to receiver
    9. Increment permissionsGiven stat for giver
    10. Increment permissionsReceived stat for receiver
    """
    try:
        db = get_db()

        # 1. Validate victory card exists
        victory = await db.victory_cards.find_one({"id": victoryId})

        if not victory:
            raise HTTPException(status_code=404, detail="Victory card not found")

        # 2. Prevent users from giving permissions to their own victories
        if victory["userId"] == giver_user_id:
            raise HTTPException(status_code=400, detail="Cannot give permission to your own victory")

        # 3. Check if user has already given a permission to this victory
        existing_permission = await db.permission_slips.find_one({
            "victoryCardId": victoryId,
            "giverId": giver_user_id
        })

        if existing_permission:
            raise HTTPException(status_code=409, detail="User has already given a permission to this victory")

        # 4. Get giver's display name
        giver = await db.users.find_one({"user_id": giver_user_id})
        giver_display_name = "Anonymous"

        if giver:
            community_profile = giver.get("communityProfile", {})
            share_anonymous = community_profile.get("shareAnonymousByDefault", False)

            if not share_anonymous:
                giver_display_name = giver.get("firstname", "Anonymous")

        # 5. Generate permission text based on type and category
        dream_category = victory.get("dreamCategory", "achievement_goals")
        permission_text = get_permission_text(permission_data.permissionType, dream_category)

        # 6. Create permission slip document
        permission_id = generate_permission_id()
        permission_slip = PermissionSlipDB(
            id=permission_id,
            victoryCardId=victoryId,
            giverId=giver_user_id,
            giverDisplayName=giver_display_name,
            receiverId=victory["userId"],
            permissionType=permission_data.permissionType,
            permissionText=permission_text,
            createdAt=get_current_iso_timestamp()
        )

        # 7. Insert permission into database
        await db.permission_slips.insert_one(permission_slip.model_dump())

        # 8. Increment victory card's permission count
        await db.victory_cards.update_one(
            {"id": victoryId},
            {"$inc": {"permissionsCount": 1}}
        )

        # 9. Award +5 courage points to receiver
        courage_points_awarded = 5

        # Initialize couragePoints if it doesn't exist
        receiver = await db.users.find_one({"user_id": victory["userId"]})
        if receiver and "couragePoints" not in receiver:
            await db.users.update_one(
                {"user_id": victory["userId"]},
                {"$set": {"couragePoints": 0}}
            )

        # Update receiver's courage points
        await db.users.update_one(
            {"user_id": victory["userId"]},
            {"$inc": {"couragePoints": courage_points_awarded}}
        )

        # 10. Update giver's permissionsGiven stat
        await db.users.update_one(
            {"user_id": giver_user_id},
            {"$inc": {"communityStats.permissionsGiven": 1}}
        )

        # 11. Update receiver's permissionsReceived stat
        await db.users.update_one(
            {"user_id": victory["userId"]},
            {"$inc": {"communityStats.permissionsReceived": 1}}
        )

        logger.info(f"Permission slip given: {permission_id} from {giver_user_id} to victory {victoryId}")

        return GivePermissionResponse(
            success=True,
            permissionText=permission_text,
            couragePointsAwarded=courage_points_awarded
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error giving permission to victory {victoryId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{victoryId}/permissions", response_model=VictoryPermissionsResponse, tags=["victories"])
async def get_victory_permissions(
    victoryId: str,
    limit: int = Query(50, ge=1, le=100, description="Number of permissions to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """
    Retrieve all permission slips for a victory card

    Business Logic:
    1. Validate victory card exists
    2. Query permission slips by victoryCardId
    3. Sort by createdAt descending (newest first)
    4. Apply pagination (limit & offset)
    5. Return list with total count
    """
    try:
        db = get_db()

        # 1. Validate victory card exists
        victory = await db.victory_cards.find_one({"id": victoryId})

        if not victory:
            raise HTTPException(status_code=404, detail="Victory card not found")

        # 2. Get total count
        total = await db.permission_slips.count_documents({"victoryCardId": victoryId})

        # 3. Query permissions with pagination, sorted by newest first
        permissions_cursor = db.permission_slips.find(
            {"victoryCardId": victoryId}
        ).sort("createdAt", -1).skip(offset).limit(limit)

        permissions_list = await permissions_cursor.to_list(length=limit)

        # 4. Convert to response models
        permissions = [
            PermissionSlipResponse(
                id=perm["id"],
                giverDisplayName=perm["giverDisplayName"],
                permissionText=perm["permissionText"],
                createdAt=perm["createdAt"]
            )
            for perm in permissions_list
        ]

        logger.info(f"Retrieved {len(permissions)} permissions for victory {victoryId}")

        return VictoryPermissionsResponse(
            permissions=permissions,
            count=len(permissions),
            total=total
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching permissions for victory {victoryId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================
# ME TOO ENDPOINTS
# ====================

@router.post("/{victoryId}/metoo", response_model=ToggleMeTooResponse, tags=["victories"])
async def toggle_metoo(
    victoryId: str,
    user_id: str = Query(..., description="User ID clicking Me Too")
):
    """
    Toggle Me Too for a victory card (add if not exists, remove if exists)

    Note: In production, user_id should be derived from authentication context (Bearer token)
    For now, it's passed as a query parameter for testing purposes

    Business Logic:
    1. Validate victory card exists
    2. Check if user already has Me Too for this victory
       - If exists: Remove Me Too document & decrement count
       - If not exists: Create Me Too document & increment count
    3. Return updated count and whether it was added or removed
    4. NO notification sent (intentional - low-pressure feature)

    Important Notes:
    - This is a toggle endpoint - calling it twice removes the Me Too
    - Users CAN give Me Too to their own victories (unlike permissions)
    - No courage points awarded (unlike boosts/permissions)
    """
    try:
        db = get_db()

        # 1. Validate victory card exists
        victory = await db.victory_cards.find_one({"id": victoryId})

        if not victory:
            raise HTTPException(status_code=404, detail="Victory card not found")

        # 2. Check if user already has Me Too for this victory
        existing_metoo = await db.me_toos.find_one({
            "victoryCardId": victoryId,
            "userId": user_id
        })

        if existing_metoo:
            # Remove Me Too (toggle off)
            await db.me_toos.delete_one({
                "victoryCardId": victoryId,
                "userId": user_id
            })

            # Decrement victory card's Me Too count
            result = await db.victory_cards.find_one_and_update(
                {"id": victoryId},
                {"$inc": {"meTooCount": -1}},
                return_document=True
            )

            new_metoo_count = max(0, result.get("meTooCount", 0))  # Ensure non-negative

            logger.info(f"Me Too removed: user {user_id} from victory {victoryId}")

            return ToggleMeTooResponse(
                success=True,
                newMeTooCount=new_metoo_count,
                added=False
            )

        else:
            # Add Me Too (toggle on)
            metoo_id = generate_metoo_id()
            metoo = MeTooDB(
                id=metoo_id,
                victoryCardId=victoryId,
                userId=user_id,
                createdAt=get_current_iso_timestamp()
            )

            # Insert Me Too into database
            await db.me_toos.insert_one(metoo.model_dump())

            # Increment victory card's Me Too count
            result = await db.victory_cards.find_one_and_update(
                {"id": victoryId},
                {"$inc": {"meTooCount": 1}},
                return_document=True
            )

            new_metoo_count = result.get("meTooCount", 1)

            logger.info(f"Me Too added: user {user_id} to victory {victoryId}")

            return ToggleMeTooResponse(
                success=True,
                newMeTooCount=new_metoo_count,
                added=True
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling Me Too for victory {victoryId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
