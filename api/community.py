"""
Community API endpoints for user stats and profile
"""

import logging
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from core.database import get_db
from models.community import (
    CommunityStatsResponse,
    UpdateCommunityProfileRequest,
    UpdateCommunityProfileResponse,
    CommunityProfile,
    InspirationsResponse,
    InspirationItem,
    VictoriesListResponse,
    VictoryCardResponse,
    PaginationInfo,
    TogglePinResponse,
    PinnedInspirationDB,
    generate_pin_id,
    get_current_iso_timestamp,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ====================
# COMMUNITY STATS ENDPOINTS
# ====================

@router.get("/users/{userId}/community-stats", response_model=CommunityStatsResponse, tags=["community"])
async def get_community_stats(userId: str):
    """
    Get user's community engagement statistics

    Calculation Logic:
    - victoriesShared: Count of VictoryCard documents where userId matches
    - boostsReceived: Count of CourageBoost documents where receiverId matches
    - boostsGiven: Count of CourageBoost documents where giverId matches
    - couragePoints: Value from user document's couragePoints field
    """
    try:
        db = get_db()

        # Validate user exists
        user = await db.users.find_one({"user_id": userId})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Count victories shared
        victories_shared = await db.victory_cards.count_documents({"userId": userId})

        # Count boosts received
        boosts_received = await db.courage_boosts.count_documents({"receiverId": userId})

        # Count boosts given
        boosts_given = await db.courage_boosts.count_documents({"giverId": userId})

        # Count permissions received
        permissions_received = await db.permission_slips.count_documents({"receiverId": userId})

        # Count permissions given
        permissions_given = await db.permission_slips.count_documents({"giverId": userId})

        # Get courage points from user document
        courage_points = user.get("couragePoints", 0)

        logger.info(f"Community stats fetched for user {userId}")

        return CommunityStatsResponse(
            victoriesShared=victories_shared,
            boostsReceived=boosts_received,
            boostsGiven=boosts_given,
            permissionsReceived=permissions_received,
            permissionsGiven=permissions_given,
            couragePoints=courage_points
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching community stats for {userId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================
# COMMUNITY PROFILE ENDPOINTS
# ====================

@router.put("/users/{userId}/community-profile", response_model=UpdateCommunityProfileResponse, tags=["community"])
async def update_community_profile(userId: str, profile_data: UpdateCommunityProfileRequest):
    """
    Update user's community profile settings

    Request Fields (all optional):
    - location: User's location
    - age: User's age (positive integer)
    - shareAnonymousByDefault: Default sharing preference
    """
    try:
        db = get_db()

        # Validate user exists
        user = await db.users.find_one({"user_id": userId})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Build update document
        update_data = {}

        if profile_data.location is not None:
            update_data["communityProfile.location"] = profile_data.location

        if profile_data.age is not None:
            # Pydantic validation already ensures age is between 1 and 120
            update_data["communityProfile.age"] = profile_data.age

        if profile_data.shareAnonymousByDefault is not None:
            update_data["communityProfile.shareAnonymousByDefault"] = profile_data.shareAnonymousByDefault

        # If no fields to update, return current profile
        if not update_data:
            current_profile = user.get("communityProfile", {})
            return UpdateCommunityProfileResponse(
                success=True,
                message="No changes made",
                communityProfile=CommunityProfile(
                    location=current_profile.get("location"),
                    age=current_profile.get("age"),
                    shareAnonymousByDefault=current_profile.get("shareAnonymousByDefault", False)
                )
            )

        # Update user document
        await db.users.update_one(
            {"user_id": userId},
            {"$set": update_data}
        )

        # Fetch updated user
        updated_user = await db.users.find_one({"user_id": userId})
        updated_profile = updated_user.get("communityProfile", {})

        logger.info(f"Community profile updated for user {userId}")

        return UpdateCommunityProfileResponse(
            success=True,
            message="Community profile updated",
            communityProfile=CommunityProfile(
                location=updated_profile.get("location"),
                age=updated_profile.get("age"),
                shareAnonymousByDefault=updated_profile.get("shareAnonymousByDefault", False)
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating community profile for {userId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ====================
# INSPIRATIONS ENDPOINTS
# ====================

@router.get("/users/{userId}/inspirations", response_model=InspirationsResponse, tags=["community"])
async def get_user_inspirations(
    userId: str,
    limit: int = Query(20, ge=1, le=100, description="Number of inspirations to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """
    Get list of victories the user has clicked "Me Too" on

    Business Logic:
    1. Query me_toos collection for user's entries
    2. Join with victory_cards to get full victory details
    3. Sort by meTooDate descending (most recent first)
    4. Apply pagination
    5. Return list with total count
    """
    try:
        db = get_db()

        # Validate user exists
        user = await db.users.find_one({"user_id": userId})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get total count
        total = await db.me_toos.count_documents({"userId": userId})

        # Query Me Toos with pagination, sorted by newest first
        metoos_cursor = db.me_toos.find(
            {"userId": userId}
        ).sort("createdAt", -1).skip(offset).limit(limit)

        metoos_list = await metoos_cursor.to_list(length=limit)

        # Fetch corresponding victory cards
        inspirations = []

        for metoo in metoos_list:
            victory = await db.victory_cards.find_one({"id": metoo["victoryCardId"]})

            if victory:
                inspiration = InspirationItem(
                    id=victory["id"],
                    milestoneTitle=victory.get("milestoneTitle", "Untitled"),
                    dreamCategory=victory.get("dreamCategory", "achievement_goals"),
                    userDisplayName=victory.get("userDisplayName", "Anonymous"),
                    createdAt=victory.get("createdAt", ""),
                    meTooDate=metoo.get("createdAt", "")
                )
                inspirations.append(inspiration)

        logger.info(f"Retrieved {len(inspirations)} inspirations for user {userId}")

        return InspirationsResponse(
            inspirations=inspirations,
            total=total
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching inspirations for user {userId}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/users/{user_id}/inspiration", response_model=VictoriesListResponse, tags=["community"])
async def get_user_inspiration(
    user_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    limit: int = Query(10, ge=1, le=50, description="Number of items per page")
):
    """
    Retrieves all victory cards that the user has pinned to inspiration.

    This endpoint returns full victory card objects with hasUserBoosted and
    hasUserMeTooed fields calculated for the requesting user.

    Business Logic:
    1. Query pinned_inspirations collection for user's entries
    2. Join with victory_cards to get full victory details
    3. Calculate hasUserBoosted and hasUserMeTooed
    4. Sort by pin createdAt DESC (most recently pinned first)
    5. Apply pagination
    """
    try:
        db = get_db()

        # Validate user exists
        user = await db.users.find_one({"user_id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Calculate pagination
        skip = (page - 1) * limit

        # Get total count from pinned_inspirations
        total_count = await db.pinned_inspirations.count_documents({"userId": user_id})
        total_pages = max(1, (total_count + limit - 1) // limit)

        # Query pinned inspirations with pagination, sorted by newest first
        pins_cursor = db.pinned_inspirations.find(
            {"userId": user_id}
        ).sort("createdAt", -1).skip(skip).limit(limit)

        pins_list = await pins_cursor.to_list(length=limit)

        # Fetch corresponding victory cards with all details
        victories = []

        for pin in pins_list:
            victory_doc = await db.victory_cards.find_one({"id": pin["victoryCardId"]})

            if not victory_doc:
                continue

            # Check if user has boosted this victory
            boost_exists = await db.courage_boosts.find_one({
                "victoryCardId": victory_doc["id"],
                "giverId": user_id
            })
            has_user_boosted = boost_exists is not None

            # Check if user has Me Too'd this victory
            metoo_exists = await db.me_toos.find_one({
                "victoryCardId": victory_doc["id"],
                "userId": user_id
            })
            has_user_metooed = metoo_exists is not None

            # Build VictoryCardResponse
            victory_response = VictoryCardResponse(
                id=victory_doc["id"],
                userId=victory_doc["userId"],
                userDisplayName=victory_doc.get("userDisplayName", "Anonymous"),
                userLocation=victory_doc.get("userLocation"),
                userAge=victory_doc.get("userAge"),
                milestoneId=victory_doc["milestoneId"],
                milestoneTitle=victory_doc.get("milestoneTitle", "Untitled"),
                challengeType=victory_doc.get("challengeType"),
                dreamId=victory_doc["dreamId"],
                dreamCategory=victory_doc.get("dreamCategory", "achievement_goals"),
                evidenceSnippet=victory_doc.get("evidenceSnippet", ""),
                confidenceBoost=victory_doc.get("confidenceBoost", 0),
                impactLevel=victory_doc.get("impactLevel", "medium"),
                completedDate=victory_doc.get("completedDate", ""),
                createdAt=victory_doc.get("createdAt", ""),
                courageBoosts=victory_doc.get("courageBoosts", 0),
                hasUserBoosted=has_user_boosted,
                permissionsCount=victory_doc.get("permissionsCount", 0),
                meTooCount=victory_doc.get("meTooCount", 0),
                hasUserMeTooed=has_user_metooed,
                isAnonymous=victory_doc.get("isAnonymous", False)
            )

            victories.append(victory_response)

        logger.info(f"Retrieved {len(victories)} pinned inspiration victories for user {user_id} (page {page})")

        # Fetch ALL pinned item IDs (both victory cards and journey recaps)
        all_pins_cursor = db.pinned_inspirations.find(
            {"userId": user_id}, {"victoryCardId": 1, "_id": 0}
        )
        all_pins = await all_pins_cursor.to_list(length=None)
        pinned_item_ids = [pin["victoryCardId"] for pin in all_pins]

        # Build pagination info
        pagination = PaginationInfo(
            page=page,
            limit=limit,
            totalPages=total_pages,
            totalCount=total_count
        )

        return VictoriesListResponse(
            victories=victories,
            pagination=pagination,
            pinnedItemIds=pinned_item_ids
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching inspiration for user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/users/{user_id}/inspiration/{item_id}", response_model=TogglePinResponse, tags=["community"])
async def toggle_pin_inspiration(user_id: str, item_id: str):
    """
    Toggle pin/save a victory card to the user's inspiration list.

    This is separate from Me Too (resonance). Pinning saves the card
    to the user's inspiration tab without affecting the resonance count.

    Business Logic:
    1. Validate user and victory card exist
    2. Check if already pinned in pinned_inspirations collection
       - If pinned: remove pin
       - If not pinned: add pin
    3. Return success and new pinned state
    """
    try:
        db = get_db()

        # Validate user exists
        user = await db.users.find_one({"user_id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Validate item exists (victory card OR journey recap)
        victory = await db.victory_cards.find_one({"id": item_id})
        if not victory:
            try:
                journey_recap = await db.journey_recaps.find_one({"_id": ObjectId(item_id)})
            except Exception:
                journey_recap = None
            if not journey_recap:
                raise HTTPException(status_code=404, detail="Item not found")

        # Check if already pinned
        existing_pin = await db.pinned_inspirations.find_one({
            "victoryCardId": item_id,
            "userId": user_id,
        })

        if existing_pin:
            # Unpin (toggle off)
            await db.pinned_inspirations.delete_one({
                "victoryCardId": item_id,
                "userId": user_id,
            })
            logger.info(f"Unpinned inspiration: user {user_id} from victory {item_id}")
            return TogglePinResponse(success=True, pinned=False)
        else:
            # Pin (toggle on)
            pin = PinnedInspirationDB(
                id=generate_pin_id(),
                victoryCardId=item_id,
                userId=user_id,
                createdAt=get_current_iso_timestamp(),
            )
            await db.pinned_inspirations.insert_one(pin.model_dump())
            logger.info(f"Pinned inspiration: user {user_id} to victory {item_id}")
            return TogglePinResponse(success=True, pinned=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling pin for user {user_id}, victory {item_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
