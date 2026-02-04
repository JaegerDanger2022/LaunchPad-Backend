from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import logging
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

client: Optional[AsyncIOMotorClient] = None
db: Optional[AsyncIOMotorDatabase] = None

async def connect_db():
    global client, db
    try:
        mongodb_url = os.getenv("MONGODB_URL")
        db_name = os.getenv("MONGODB_DB_NAME", "dream_to_do")
        if not mongodb_url:
            raise ValueError("MONGODB_URL environment variable is not set")
        logger.info(f"[DB] MongoDB URL: {mongodb_url[:50]}...")
        logger.info(f"[DB] Database name: {db_name}")

        logger.info("[DB] Creating AsyncIOMotorClient...")
        client = AsyncIOMotorClient(mongodb_url)
        logger.info("[DB] AsyncIOMotorClient created")

        logger.info("[DB] Getting database reference...")
        db = client[db_name]
        logger.info("[DB] Database reference obtained")

        logger.info("[DB] Pinging MongoDB...")
        await client.admin.command("ping")
        logger.info("[DB] Successfully connected to MongoDB!")

        logger.info("[DB] Creating indexes...")
        await create_indexes()
        logger.info("[DB] Indexes created successfully")
    except Exception as e:
        logger.error(f"[DB] Failed to connect to MongoDB: {e}", exc_info=True)
        raise

async def close_db():
    global client
    if client:
        client.close()
        logger.info("MongoDB connection closed")

async def create_indexes():
    """Create necessary database indexes"""
    if db is None:
        raise RuntimeError("Database not connected")

    try:
        # Users collection
        logger.info("[DB] Creating index: users.email (unique)")
        await db.users.create_index("email", unique=True)
        logger.info("[DB] Index created: users.email")

        logger.info("[DB] Creating index: users.created_at")
        await db.users.create_index("created_at")
        logger.info("[DB] Index created: users.created_at")

        # Roadmaps collection
        logger.info("[DB] Creating index: roadmaps.user_id")
        await db.roadmaps.create_index("user_id")
        logger.info("[DB] Index created: roadmaps.user_id")

        logger.info("[DB] Creating index: roadmaps (user_id, created_at)")
        await db.roadmaps.create_index([("user_id", 1), ("created_at", -1)])
        logger.info("[DB] Index created: roadmaps (user_id, created_at)")

        # Challenges collection
        logger.info("[DB] Creating index: challenges.user_id")
        await db.challenges.create_index("user_id")
        logger.info("[DB] Index created: challenges.user_id")

        logger.info("[DB] Creating index: challenges (user_id, roadmap_id)")
        await db.challenges.create_index([("user_id", 1), ("roadmap_id", 1)])
        logger.info("[DB] Index created: challenges (user_id, roadmap_id)")

        logger.info("[DB] Creating index: challenges.completed_at (sparse)")
        await db.challenges.create_index("completed_at", sparse=True)
        logger.info("[DB] Index created: challenges.completed_at")

        # Streaks collection
        logger.info("[DB] Creating index: streaks.user_id (unique)")
        await db.streaks.create_index("user_id", unique=True)
        logger.info("[DB] Index created: streaks.user_id")

        # Victory Cards collection (Community Features)
        logger.info("[DB] Creating index: victory_cards.dreamCategory")
        await db.victory_cards.create_index("dreamCategory")
        logger.info("[DB] Index created: victory_cards.dreamCategory")

        logger.info("[DB] Creating index: victory_cards.completedDate")
        await db.victory_cards.create_index("completedDate")
        logger.info("[DB] Index created: victory_cards.completedDate")

        logger.info("[DB] Creating index: victory_cards.createdAt")
        await db.victory_cards.create_index("createdAt")
        logger.info("[DB] Index created: victory_cards.createdAt")

        logger.info("[DB] Creating index: victory_cards.userId")
        await db.victory_cards.create_index("userId")
        logger.info("[DB] Index created: victory_cards.userId")

        logger.info("[DB] Creating index: victory_cards.id (unique)")
        await db.victory_cards.create_index("id", unique=True)
        logger.info("[DB] Index created: victory_cards.id")

        logger.info("[DB] Creating index: victory_cards.milestoneId (unique)")
        await db.victory_cards.create_index("milestoneId", unique=True)
        logger.info("[DB] Index created: victory_cards.milestoneId")

        # Courage Boosts collection (Community Features)
        logger.info("[DB] Creating index: courage_boosts.victoryCardId")
        await db.courage_boosts.create_index("victoryCardId")
        logger.info("[DB] Index created: courage_boosts.victoryCardId")

        logger.info("[DB] Creating index: courage_boosts.giverId")
        await db.courage_boosts.create_index("giverId")
        logger.info("[DB] Index created: courage_boosts.giverId")

        logger.info("[DB] Creating index: courage_boosts.receiverId")
        await db.courage_boosts.create_index("receiverId")
        logger.info("[DB] Index created: courage_boosts.receiverId")

        logger.info("[DB] Creating index: courage_boosts.id (unique)")
        await db.courage_boosts.create_index("id", unique=True)
        logger.info("[DB] Index created: courage_boosts.id")

        logger.info("[DB] Creating compound index: courage_boosts (victoryCardId, giverId) unique")
        await db.courage_boosts.create_index([("victoryCardId", 1), ("giverId", 1)], unique=True)
        logger.info("[DB] Index created: courage_boosts (victoryCardId, giverId)")

        # Permission Slips collection (Community Features - Phase 2)
        logger.info("[DB] Creating compound index: permission_slips (victoryCardId, createdAt)")
        await db.permission_slips.create_index([("victoryCardId", 1), ("createdAt", -1)])
        logger.info("[DB] Index created: permission_slips (victoryCardId, createdAt)")

        logger.info("[DB] Creating compound index: permission_slips (receiverId, createdAt)")
        await db.permission_slips.create_index([("receiverId", 1), ("createdAt", -1)])
        logger.info("[DB] Index created: permission_slips (receiverId, createdAt)")

        logger.info("[DB] Creating index: permission_slips.giverId")
        await db.permission_slips.create_index("giverId")
        logger.info("[DB] Index created: permission_slips.giverId")

        logger.info("[DB] Creating index: permission_slips.id (unique)")
        await db.permission_slips.create_index("id", unique=True)
        logger.info("[DB] Index created: permission_slips.id")

        logger.info("[DB] Creating compound index: permission_slips (victoryCardId, giverId) unique")
        await db.permission_slips.create_index([("victoryCardId", 1), ("giverId", 1)], unique=True)
        logger.info("[DB] Index created: permission_slips (victoryCardId, giverId)")

        # Me Toos collection (Community Features - Phase 2)
        logger.info("[DB] Creating compound index: me_toos (victoryCardId, userId) unique")
        await db.me_toos.create_index([("victoryCardId", 1), ("userId", 1)], unique=True)
        logger.info("[DB] Index created: me_toos (victoryCardId, userId)")

        logger.info("[DB] Creating compound index: me_toos (userId, createdAt)")
        await db.me_toos.create_index([("userId", 1), ("createdAt", -1)])
        logger.info("[DB] Index created: me_toos (userId, createdAt)")

        logger.info("[DB] Creating index: me_toos.id (unique)")
        await db.me_toos.create_index("id", unique=True)
        logger.info("[DB] Index created: me_toos.id")

        logger.info("[DB] Creating index: me_toos.victoryCardId")
        await db.me_toos.create_index("victoryCardId")
        logger.info("[DB] Index created: me_toos.victoryCardId")

        # Journey Recaps collection (Community Features - Phase 2)
        logger.info("[DB] Creating compound index: journey_recaps (dreamCategory, createdAt)")
        await db.journey_recaps.create_index([("dreamCategory", 1), ("createdAt", -1)])
        logger.info("[DB] Index created: journey_recaps (dreamCategory, createdAt)")

        logger.info("[DB] Creating compound index: journey_recaps (userId, createdAt)")
        await db.journey_recaps.create_index([("userId", 1), ("createdAt", -1)])
        logger.info("[DB] Index created: journey_recaps (userId, createdAt)")

        logger.info("[DB] Creating index: journey_recaps.createdAt")
        await db.journey_recaps.create_index("createdAt")
        logger.info("[DB] Index created: journey_recaps.createdAt")

        logger.info("[DB] Creating compound index: journey_recaps (userId, dreamId) unique")
        await db.journey_recaps.create_index([("userId", 1), ("dreamId", 1)], unique=True)
        logger.info("[DB] Index created: journey_recaps (userId, dreamId) - ensures one recap per dream")

        # ============= DREAMS COLLECTION INDEXES (NEW) =============
        logger.info("[DB] Creating index: dreams.user_id")
        await db.dreams.create_index("user_id")
        logger.info("[DB] Index created: dreams.user_id")

        logger.info("[DB] Creating index: dreams.thread_id (unique)")
        await db.dreams.create_index("thread_id", unique=True)
        logger.info("[DB] Index created: dreams.thread_id")

        logger.info("[DB] Creating compound index: dreams (user_id, status)")
        await db.dreams.create_index([("user_id", 1), ("status", 1)])
        logger.info("[DB] Index created: dreams (user_id, status)")

        logger.info("[DB] Creating compound index: dreams (user_id, updated_at)")
        await db.dreams.create_index([("user_id", 1), ("updated_at", -1)])
        logger.info("[DB] Index created: dreams (user_id, updated_at)")

        logger.info("[DB] All database indexes created successfully")
    except Exception as e:
        logger.error(f"[DB] Failed to create indexes: {e}", exc_info=True)
        raise

def get_db():
    return db
