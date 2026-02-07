"""
Migration script to fix dreamCategory field in existing victory cards and journey recaps.

This script updates all victory cards and journey recaps to read the category from
the dream's roadmap.category field instead of the flat category field.

Run this ONCE after deploying the API fix to correct existing data.
"""

import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "packslight")


async def fix_victory_categories():
    """Fix dreamCategory for all victory cards"""
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DB_NAME]

    try:
        # Get all victory cards
        victories = await db.victory_cards.find({}).to_list(length=None)

        fixed_count = 0
        skipped_count = 0
        error_count = 0

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
                    logger.debug(f"Victory {victory.get('id')} already has correct category: {correct_category}")
                    skipped_count += 1

            except Exception as e:
                logger.error(f"Error processing victory {victory.get('id')}: {e}")
                error_count += 1

        logger.info(f"Victory cards migration complete: {fixed_count} fixed, {skipped_count} skipped, {error_count} errors")
        return fixed_count, skipped_count, error_count

    finally:
        client.close()


async def fix_journey_recap_categories():
    """Fix dreamCategory for all journey recaps"""
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DB_NAME]

    try:
        # Get all journey recaps
        recaps = await db.journey_recaps.find({}).to_list(length=None)

        fixed_count = 0
        skipped_count = 0
        error_count = 0

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
                    logger.debug(f"Recap {recap.get('_id')} already has correct category: {correct_category}")
                    skipped_count += 1

            except Exception as e:
                logger.error(f"Error processing recap {recap.get('_id')}: {e}")
                error_count += 1

        logger.info(f"Journey recaps migration complete: {fixed_count} fixed, {skipped_count} skipped, {error_count} errors")
        return fixed_count, skipped_count, error_count

    finally:
        client.close()


async def main():
    """Run all migrations"""
    logger.info("Starting category migration...")

    # Fix victory cards
    v_fixed, v_skipped, v_errors = await fix_victory_categories()

    # Fix journey recaps
    j_fixed, j_skipped, j_errors = await fix_journey_recap_categories()

    logger.info("\n" + "="*60)
    logger.info("MIGRATION SUMMARY")
    logger.info("="*60)
    logger.info(f"Victory Cards:   {v_fixed} fixed, {v_skipped} skipped, {v_errors} errors")
    logger.info(f"Journey Recaps:  {j_fixed} fixed, {j_skipped} skipped, {j_errors} errors")
    logger.info(f"TOTAL:           {v_fixed + j_fixed} fixed, {v_skipped + j_skipped} skipped, {v_errors + j_errors} errors")
    logger.info("="*60)


if __name__ == "__main__":
    asyncio.run(main())
