"""
Admin migration endpoints - REMOVE AFTER USE
These endpoints should only be used once to fix data, then deleted for security.
"""

import logging
from fastapi import APIRouter, HTTPException
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/fix-victory-categories", tags=["admin-migrations"])
async def fix_victory_categories():
    """
    FIX EXISTING DATA: Update all victory cards to use correct category from roadmap.category

    ⚠️ This endpoint should be called ONCE after deploying the category fix, then removed.
    """
    try:
        db = get_db()

        # Get all victory cards
        victories = await db.victory_cards.find({}).to_list(length=None)

        fixed_count = 0
        skipped_count = 0
        errors = []

        logger.info(f"Found {len(victories)} victory cards to process")

        for victory in victories:
            try:
                dream_id = victory.get("dreamId")

                if not dream_id:
                    logger.warning(f"Victory {victory.get('id')} has no dreamId, skipping")
                    skipped_count += 1
                    continue

                # Find the associated dream
                dream = await db.dreams.find_one({"thread_id": dream_id})

                if not dream:
                    logger.warning(f"Dream {dream_id} not found for victory {victory.get('id')}, skipping")
                    skipped_count += 1
                    continue

                # Get category from roadmap.category
                correct_category = dream.get("roadmap", {}).get("category", "achievement_goals")
                current_category = victory.get("dreamCategory", "achievement_goals")

                # Update if different
                if correct_category != current_category:
                    await db.victory_cards.update_one(
                        {"_id": victory["_id"]},
                        {"$set": {"dreamCategory": correct_category}}
                    )
                    logger.info(f"Updated victory {victory.get('id')}: {current_category} -> {correct_category}")
                    fixed_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                error_msg = f"Error processing victory {victory.get('id')}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        logger.info(f"Victory cards migration complete: {fixed_count} fixed, {skipped_count} skipped, {len(errors)} errors")

        return {
            "success": True,
            "message": "Victory categories migration complete",
            "fixed": fixed_count,
            "skipped": skipped_count,
            "errors": errors
        }

    except Exception as e:
        logger.error(f"Error in fix_victory_categories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


@router.post("/fix-journey-recap-categories", tags=["admin-migrations"])
async def fix_journey_recap_categories():
    """
    FIX EXISTING DATA: Update all journey recaps to use correct category from roadmap.category

    ⚠️ This endpoint should be called ONCE after deploying the category fix, then removed.
    """
    try:
        db = get_db()

        # Get all journey recaps
        recaps = await db.journey_recaps.find({}).to_list(length=None)

        fixed_count = 0
        skipped_count = 0
        errors = []

        logger.info(f"Found {len(recaps)} journey recaps to process")

        for recap in recaps:
            try:
                dream_id = recap.get("dreamId")

                if not dream_id:
                    logger.warning(f"Journey recap {recap.get('_id')} has no dreamId, skipping")
                    skipped_count += 1
                    continue

                # Find the associated dream
                dream = await db.dreams.find_one({"thread_id": dream_id})

                if not dream:
                    logger.warning(f"Dream {dream_id} not found for recap {recap.get('_id')}, skipping")
                    skipped_count += 1
                    continue

                # Get category from roadmap.category
                correct_category = dream.get("roadmap", {}).get("category", "achievement_goals")
                current_category = recap.get("dreamCategory", "achievement_goals")

                # Update if different
                if correct_category != current_category:
                    await db.journey_recaps.update_one(
                        {"_id": recap["_id"]},
                        {"$set": {"dreamCategory": correct_category}}
                    )
                    logger.info(f"Updated recap {recap.get('_id')}: {current_category} -> {correct_category}")
                    fixed_count += 1
                else:
                    skipped_count += 1

            except Exception as e:
                error_msg = f"Error processing recap {recap.get('_id')}: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        logger.info(f"Journey recaps migration complete: {fixed_count} fixed, {skipped_count} skipped, {len(errors)} errors")

        return {
            "success": True,
            "message": "Journey recap categories migration complete",
            "fixed": fixed_count,
            "skipped": skipped_count,
            "errors": errors
        }

    except Exception as e:
        logger.error(f"Error in fix_journey_recap_categories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")
