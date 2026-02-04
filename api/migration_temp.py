"""
TEMPORARY Migration Endpoints - DELETE AFTER MIGRATION IS COMPLETE

These endpoints are ONLY for the one-time migration to dreams collection.
DO NOT use in production long-term.
"""

import logging
import base64
from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/migrate-user/{user_id}", tags=["migration-temp"])
async def migrate_single_user(user_id: str):
    """
    Migrate a single user's dreams from embedded array to dreams collection.

    TEMPORARY ENDPOINT - DELETE AFTER MIGRATION

    Args:
        user_id: User ID to migrate

    Returns:
        Migration result with statistics
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info(f"[MIGRATION] Starting migration for user: {user_id}")

        # Get user document
        user = await db.users.find_one({"user_id": user_id})

        if not user:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")

        dreams = user.get("dreams", [])

        if not dreams:
            return {
                "success": True,
                "message": "No dreams to migrate",
                "user_id": user_id,
                "dreams_migrated": 0,
                "already_migrated": 0
            }

        dreams_migrated = 0
        already_migrated = 0
        dreams_summary = []

        for dream in dreams:
            thread_id = dream.get("thread_id")

            if not thread_id:
                logger.warning(f"[MIGRATION] Skipping dream without thread_id for user {user_id}")
                continue

            # Check if dream already exists in dreams collection
            existing_dream = await db.dreams.find_one({"thread_id": thread_id})

            if existing_dream:
                logger.info(f"[MIGRATION] Dream {thread_id} already migrated, skipping")
                already_migrated += 1
            else:
                # Insert into dreams collection
                dream_doc = {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "dream": dream.get("dream", ""),
                    "status": dream.get("status", "active"),
                    "dream_image_bytes": dream.get("dream_image_bytes"),  # Keep as binary
                    "roadmap": dream.get("roadmap", {}),
                    "created_at": dream.get("created_at", datetime.now(timezone.utc)),
                    "updated_at": dream.get("updated_at", datetime.now(timezone.utc)),
                    "category": dream.get("category"),
                    "isComplete": dream.get("isComplete", False)
                }

                await db.dreams.insert_one(dream_doc)
                dreams_migrated += 1
                logger.info(f"[MIGRATION] Migrated dream {thread_id} to dreams collection")

            # Create summary for user document
            milestones = dream.get("roadmap", {}).get("milestones", [])
            total_milestones = len(milestones)
            completed_milestones = sum(
                1 for m in milestones
                if isinstance(m, dict) and m.get("status") == "completed"
            )

            # Convert image to base64 for summary
            image_base64 = None
            if "dream_image_bytes" in dream and isinstance(dream["dream_image_bytes"], bytes):
                image_base64 = base64.b64encode(dream["dream_image_bytes"]).decode('utf-8')

            summary = {
                "thread_id": thread_id,
                "dream": dream.get("dream", ""),
                "status": dream.get("status", "active"),
                "dream_image_bytes": image_base64,
                "dream_card_bg": dream.get("dream_card_bg"),
                "category": dream.get("category"),
                "created_at": dream.get("created_at"),
                "updated_at": dream.get("updated_at"),
                "milestones_count": total_milestones,
                "completed_milestones_count": completed_milestones,
                "isComplete": dream.get("isComplete", False)
            }

            dreams_summary.append(summary)

        # Update user document with dreams_summary
        await db.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "dreams_summary": dreams_summary,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )

        logger.info(f"[MIGRATION] Completed migration for user {user_id}: {dreams_migrated} new, {already_migrated} already migrated")

        return {
            "success": True,
            "message": "Migration completed successfully",
            "user_id": user_id,
            "dreams_migrated": dreams_migrated,
            "already_migrated": already_migrated,
            "total_dreams": len(dreams),
            "dreams_summary_created": len(dreams_summary)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[MIGRATION] Error migrating user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


@router.post("/migrate-all-users", tags=["migration-temp"])
async def migrate_all_users():
    """
    Migrate ALL users' dreams to dreams collection.

    TEMPORARY ENDPOINT - DELETE AFTER MIGRATION
    USE WITH CAUTION - This will process all users!

    Returns:
        Migration statistics for all users
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        logger.info("[MIGRATION] Starting migration for ALL users")

        # Get all users with dreams
        users = await db.users.find({"dreams": {"$exists": True, "$ne": []}}).to_list(None)

        total_users = len(users)
        users_migrated = 0
        users_skipped = 0
        total_dreams_migrated = 0
        errors = []

        for user in users:
            try:
                user_id = user["user_id"]
                dreams = user.get("dreams", [])

                if not dreams:
                    users_skipped += 1
                    continue

                logger.info(f"[MIGRATION] Processing user {user_id} ({users_migrated + 1}/{total_users})")

                dreams_migrated = 0
                dreams_summary = []

                for dream in dreams:
                    thread_id = dream.get("thread_id")

                    if not thread_id:
                        continue

                    # Check if already migrated
                    existing = await db.dreams.find_one({"thread_id": thread_id})

                    if not existing:
                        dream_doc = {
                            "user_id": user_id,
                            "thread_id": thread_id,
                            "dream": dream.get("dream", ""),
                            "status": dream.get("status", "active"),
                            "dream_image_bytes": dream.get("dream_image_bytes"),
                            "roadmap": dream.get("roadmap", {}),
                            "created_at": dream.get("created_at", datetime.now(timezone.utc)),
                            "updated_at": dream.get("updated_at", datetime.now(timezone.utc)),
                            "category": dream.get("category"),
                            "isComplete": dream.get("isComplete", False)
                        }

                        await db.dreams.insert_one(dream_doc)
                        dreams_migrated += 1

                    # Create summary
                    milestones = dream.get("roadmap", {}).get("milestones", [])
                    total_milestones = len(milestones)
                    completed_milestones = sum(
                        1 for m in milestones
                        if isinstance(m, dict) and m.get("status") == "completed"
                    )

                    image_base64 = None
                    if "dream_image_bytes" in dream and isinstance(dream["dream_image_bytes"], bytes):
                        image_base64 = base64.b64encode(dream["dream_image_bytes"]).decode('utf-8')

                    summary = {
                        "thread_id": thread_id,
                        "dream": dream.get("dream", ""),
                        "status": dream.get("status", "active"),
                        "dream_image_bytes": image_base64,
                        "dream_card_bg": dream.get("dream_card_bg"),
                        "category": dream.get("category"),
                        "created_at": dream.get("created_at"),
                        "updated_at": dream.get("updated_at"),
                        "milestones_count": total_milestones,
                        "completed_milestones_count": completed_milestones,
                        "isComplete": dream.get("isComplete", False)
                    }

                    dreams_summary.append(summary)

                # Update user with dreams_summary
                await db.users.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "dreams_summary": dreams_summary,
                            "updated_at": datetime.now(timezone.utc)
                        }
                    }
                )

                users_migrated += 1
                total_dreams_migrated += dreams_migrated
                logger.info(f"[MIGRATION] User {user_id}: {dreams_migrated} dreams migrated")

            except Exception as e:
                error_msg = f"User {user.get('user_id', 'unknown')}: {str(e)}"
                logger.error(f"[MIGRATION] {error_msg}")
                errors.append(error_msg)

        logger.info("[MIGRATION] All users migration completed")

        return {
            "success": True,
            "message": "Migration completed for all users",
            "total_users": total_users,
            "users_migrated": users_migrated,
            "users_skipped": users_skipped,
            "total_dreams_migrated": total_dreams_migrated,
            "errors": errors,
            "error_count": len(errors)
        }

    except Exception as e:
        logger.error(f"[MIGRATION] Error in migrate_all_users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


@router.get("/migration-status/{user_id}", tags=["migration-temp"])
async def get_migration_status(user_id: str):
    """
    Check migration status for a user.

    TEMPORARY ENDPOINT - DELETE AFTER MIGRATION

    Returns:
        Migration status information
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        user = await db.users.find_one({"user_id": user_id})

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        has_old_dreams = "dreams" in user and len(user.get("dreams", [])) > 0
        has_dreams_summary = "dreams_summary" in user and len(user.get("dreams_summary", [])) > 0

        old_dreams_count = len(user.get("dreams", []))
        summary_count = len(user.get("dreams_summary", []))

        # Count dreams in dreams collection
        dreams_in_collection = await db.dreams.count_documents({"user_id": user_id})

        return {
            "user_id": user_id,
            "has_old_dreams_array": has_old_dreams,
            "old_dreams_count": old_dreams_count,
            "has_dreams_summary": has_dreams_summary,
            "dreams_summary_count": summary_count,
            "dreams_in_collection": dreams_in_collection,
            "migration_complete": has_dreams_summary and dreams_in_collection > 0,
            "needs_migration": has_old_dreams and (not has_dreams_summary or dreams_in_collection == 0)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking migration status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cleanup-old-dreams/{user_id}", tags=["migration-temp"])
async def cleanup_old_dreams_array(user_id: str):
    """
    Remove old dreams array from user document after successful migration.

    TEMPORARY ENDPOINT - DELETE AFTER MIGRATION
    ONLY USE AFTER VERIFYING MIGRATION WAS SUCCESSFUL!

    Args:
        user_id: User ID to cleanup

    Returns:
        Cleanup result
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        # Verify migration is complete first
        user = await db.users.find_one({"user_id": user_id})

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        has_dreams_summary = "dreams_summary" in user and len(user.get("dreams_summary", [])) > 0
        dreams_in_collection = await db.dreams.count_documents({"user_id": user_id})

        if not has_dreams_summary or dreams_in_collection == 0:
            raise HTTPException(
                status_code=400,
                detail="Migration not complete. Cannot cleanup old dreams array."
            )

        # Remove old dreams array
        result = await db.users.update_one(
            {"user_id": user_id},
            {"$unset": {"dreams": ""}}
        )

        return {
            "success": True,
            "message": "Old dreams array removed successfully",
            "user_id": user_id,
            "modified": result.modified_count > 0
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cleaning up user {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backfill-dream-metadata/{user_id}", tags=["migration-temp"])
async def backfill_dream_metadata(user_id: str):
    """
    Copy metadata (score, total_xp) from the users collection embedded dreams
    into the corresponding dreams collection documents.

    TEMPORARY ENDPOINT - DELETE AFTER BACKFILL IS COMPLETE

    Also copies isComplete, completed_at, status if the dream was completed.
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        user = await db.users.find_one({"user_id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        dreams = user.get("dreams", [])
        if not dreams:
            return {"success": True, "message": "No embedded dreams found", "updated": 0}

        updated = 0
        skipped = 0

        for dream in dreams:
            thread_id = dream.get("thread_id")
            if not thread_id:
                continue

            # Build the fields to copy
            fields_to_set = {}

            # Copy metadata if present
            metadata = dream.get("metadata")
            if metadata:
                fields_to_set["metadata"] = metadata
            else:
                # Compute from milestones if metadata missing on users doc too
                milestones = dream.get("roadmap", {}).get("milestones", [])
                total_xp = sum(m.get("xp_points", 0) for m in milestones)
                score = sum(m.get("xp_points", 0) for m in milestones if m.get("status") == "completed")
                fields_to_set["metadata"] = {"score": score, "total_xp": total_xp}

            # Copy completion fields
            if dream.get("isComplete"):
                fields_to_set["isComplete"] = True
            if dream.get("completed_at"):
                fields_to_set["completed_at"] = dream["completed_at"]
            if dream.get("status"):
                fields_to_set["status"] = dream["status"]
            if dream.get("dream_card_bg"):
                fields_to_set["dream_card_bg"] = dream["dream_card_bg"]

            if not fields_to_set:
                skipped += 1
                continue

            result = await db.dreams.update_one(
                {"thread_id": thread_id},
                {"$set": fields_to_set}
            )

            if result.modified_count > 0:
                updated += 1
                logger.info(f"[BACKFILL] Updated dream {thread_id}: {fields_to_set}")
            else:
                skipped += 1
                logger.info(f"[BACKFILL] Dream {thread_id} not found in dreams collection or already up to date")

        return {
            "success": True,
            "message": "Metadata backfill completed",
            "user_id": user_id,
            "updated": updated,
            "skipped": skipped,
            "total_dreams": len(dreams)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[BACKFILL] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
