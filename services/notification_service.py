"""
Notification Service - Handles daily nudges and inactivity reminders

Sends push notifications via the Expo Push API:
https://exp.host/--/api/v2/push/send

Two notification types:
1. Daily Nudges: Sent at user's preferred time in their timezone
2. Inactivity Reminders: Sent if no milestone completed for 2+ days
"""

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import httpx
import pytz
from core.database import get_db

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Rotate messages so users don't see the same thing every day
DAILY_NUDGE_MESSAGES = [
    {"title": "Time to make progress!", "body": "Your dreams are waiting. Complete your next milestone today."},
    {"title": "Ready to crush it?", "body": "One milestone closer to your dream. Let's go."},
    {"title": "Your future self will thank you", "body": "What will you accomplish today?"},
    {"title": "Keep the momentum going", "body": "Small steps lead to big wins. Tackle a milestone now."},
]

INACTIVITY_MESSAGES = [
    {"title": "We miss you!", "body": "It's been 2 days since your last win. Don't break your momentum!"},
    {"title": "Your dreams need you", "body": "Come back and make progress. Even one milestone counts."},
    {"title": "Don't let your streak slip", "body": "You haven't checked off any milestones in 2 days."},
]

WELCOME_MESSAGE = {
    "title": "Welcome to LaunchPad! 🚀",
    "body": "You're all set. Create your first dream and start turning it into reality."
}


async def send_expo_push(tokens: List[str], title: str, body: str, data: Optional[Dict] = None, channel_id: str = "default") -> Dict:
    """
    Send push notifications via the Expo Push API.

    Args:
        tokens: List of ExponentPushToken[...] strings
        title: Notification title
        body: Notification body text
        data: Optional data payload
        channel_id: Android notification channel ID

    Returns:
        dict with send results
    """
    if not tokens:
        return {"sent": 0, "errors": 0}

    messages = [
        {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data or {},
            "channelId": channel_id,
        }
        for token in tokens
    ]

    sent = 0
    errors = 0
    invalid_tokens = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Expo accepts batches of up to 100 messages
            for i in range(0, len(messages), 100):
                batch = messages[i:i + 100]
                response = await client.post(
                    EXPO_PUSH_URL,
                    json=batch,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    for ticket in result.get("data", []):
                        if ticket.get("status") == "ok":
                            sent += 1
                        else:
                            errors += 1
                            # Track invalid tokens for cleanup
                            details = ticket.get("details", {})
                            if details.get("error") == "DeviceNotRegistered":
                                # Find which token failed based on position
                                idx = result["data"].index(ticket)
                                if idx < len(batch):
                                    invalid_tokens.append(batch[idx]["to"])
                else:
                    logger.error(f"[ExpoPush] API error: {response.status_code} {response.text}")
                    errors += len(batch)

    except Exception as e:
        logger.error(f"[ExpoPush] Request failed: {e}", exc_info=True)
        errors += len(messages)

    if invalid_tokens:
        logger.info(f"[ExpoPush] Found {len(invalid_tokens)} invalid tokens to clean up")

    return {"sent": sent, "errors": errors, "invalid_tokens": invalid_tokens}


class NotificationService:
    """Service for processing and sending user notifications"""

    def __init__(self):
        """Initialize the notification service"""
        self.db = get_db()
        if self.db is None:
            raise RuntimeError("Database connection not available")

    async def process_notifications(self):
        """
        Main entry point - check and send all due notifications.
        Called by the scheduler every 5 minutes.
        """
        try:
            current_time = datetime.now(timezone.utc)
            logger.info(f"[NotificationService] Processing notifications at {current_time.isoformat()}")

            daily_count = await self.send_daily_nudges(current_time)
            logger.info(f"[NotificationService] Sent {daily_count} daily nudges")

            inactivity_count = await self.send_inactivity_reminders(current_time)
            logger.info(f"[NotificationService] Sent {inactivity_count} inactivity reminders")

            return {
                "success": True,
                "daily_nudges_sent": daily_count,
                "inactivity_reminders_sent": inactivity_count,
                "processed_at": current_time.isoformat()
            }

        except Exception as e:
            logger.error(f"[NotificationService] Error processing notifications: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def send_daily_nudges(self, current_time: datetime) -> int:
        """Send daily nudges to users whose notification time has arrived."""
        try:
            users = await self.db.users.find({
                "pref_notification_time": {"$ne": None},
                "push_tokens": {"$exists": True, "$ne": []},
            }).to_list(length=None)

            sent_count = 0
            for user in users:
                user_id = user.get("user_id")

                if not await self._user_has_active_dreams(user_id):
                    continue

                if not self._is_notification_time(user, current_time):
                    continue

                if await self._sent_daily_nudge_recently(user):
                    continue

                tokens = user.get("push_tokens", [])
                if not tokens:
                    continue

                msg = random.choice(DAILY_NUDGE_MESSAGES)
                result = await send_expo_push(
                    tokens=tokens,
                    title=msg["title"],
                    body=msg["body"],
                    data={"type": "daily_nudge", "screen": "Home"},
                    channel_id="default",
                )

                # Clean up invalid tokens
                if result.get("invalid_tokens"):
                    await self._remove_invalid_tokens(user_id, result["invalid_tokens"])

                if result["sent"] > 0:
                    await self.db.users.update_one(
                        {"user_id": user_id},
                        {"$set": {"last_daily_nudge": datetime.now(timezone.utc).isoformat()}}
                    )
                    sent_count += 1
                    logger.info(f"[NotificationService] Daily nudge sent to user {user_id}")

            return sent_count

        except Exception as e:
            logger.error(f"[NotificationService] Error sending daily nudges: {e}", exc_info=True)
            return 0

    async def send_inactivity_reminders(self, current_time: datetime) -> int:
        """Send reminders to users inactive for 2+ days."""
        try:
            threshold = current_time - timedelta(days=2)

            users = await self.db.users.find({
                "$or": [
                    {"last_activity": {"$lt": threshold.isoformat()}},
                    {"last_activity": None}
                ],
                "pref_notification_time": {"$ne": None},
                "push_tokens": {"$exists": True, "$ne": []},
            }).to_list(length=None)

            sent_count = 0
            for user in users:
                user_id = user.get("user_id")

                if not await self._user_has_active_dreams(user_id):
                    continue

                if await self._sent_inactivity_reminder_recently(user):
                    continue

                tokens = user.get("push_tokens", [])
                if not tokens:
                    continue

                msg = random.choice(INACTIVITY_MESSAGES)
                result = await send_expo_push(
                    tokens=tokens,
                    title=msg["title"],
                    body=msg["body"],
                    data={"type": "inactivity_reminder", "screen": "Home"},
                    channel_id="reengagement",
                )

                if result.get("invalid_tokens"):
                    await self._remove_invalid_tokens(user_id, result["invalid_tokens"])

                if result["sent"] > 0:
                    await self.db.users.update_one(
                        {"user_id": user_id},
                        {"$set": {"last_inactivity_reminder": datetime.now(timezone.utc).isoformat()}}
                    )
                    sent_count += 1
                    logger.info(f"[NotificationService] Inactivity reminder sent to user {user_id}")

            return sent_count

        except Exception as e:
            logger.error(f"[NotificationService] Error sending inactivity reminders: {e}", exc_info=True)
            return 0

    def _is_notification_time(self, user: Dict, current_time: datetime) -> bool:
        """Check if current time matches user's preferred notification time (±5 min window)."""
        try:
            user_tz_str = user.get("pref_timezone", "UTC")
            pref_time = user.get("pref_notification_time")

            if not pref_time:
                return False

            hour, minute = map(int, pref_time.split(":"))

            user_tz = pytz.timezone(user_tz_str)
            user_local_time = current_time.astimezone(user_tz)

            return (
                user_local_time.hour == hour and
                abs(user_local_time.minute - minute) <= 5
            )

        except Exception as e:
            logger.error(f"Error checking notification time for user {user.get('user_id')}: {e}")
            return False

    async def _user_has_active_dreams(self, user_id: str) -> bool:
        """Check if user has any active dreams."""
        try:
            dream = await self.db.dreams.find_one({
                "user_id": user_id,
                "status": "active"
            })
            return dream is not None
        except Exception as e:
            logger.error(f"Error checking active dreams for user {user_id}: {e}")
            return False

    async def _sent_daily_nudge_recently(self, user: Dict) -> bool:
        """Check if we've sent a daily nudge in the last 23 hours."""
        last_nudge = user.get("last_daily_nudge")
        if not last_nudge:
            return False
        try:
            last_nudge_time = datetime.fromisoformat(last_nudge.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last_nudge_time < timedelta(hours=23)
        except Exception:
            return False

    async def _sent_inactivity_reminder_recently(self, user: Dict) -> bool:
        """Check if we've sent an inactivity reminder in the last 24 hours."""
        last_reminder = user.get("last_inactivity_reminder")
        if not last_reminder:
            return False
        try:
            last_reminder_time = datetime.fromisoformat(last_reminder.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) - last_reminder_time < timedelta(hours=24)
        except Exception:
            return False

    async def _remove_invalid_tokens(self, user_id: str, invalid_tokens: List[str]):
        """Remove invalid/expired push tokens from user's token list."""
        try:
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$pullAll": {"push_tokens": invalid_tokens}}
            )
            logger.info(f"[NotificationService] Removed {len(invalid_tokens)} invalid tokens for user {user_id}")
        except Exception as e:
            logger.error(f"Error removing invalid tokens for user {user_id}: {e}")


async def send_welcome_notification(user_id: str) -> Dict:
    """
    Send a welcome push notification to a newly registered user.

    Args:
        user_id: The user's unique identifier

    Returns:
        dict with send results
    """
    db = get_db()
    if db is None:
        logger.error("[WelcomeNotification] Database not available")
        return {"sent": 0, "errors": 1}

    user = await db.users.find_one({"user_id": user_id})
    if not user:
        logger.warning(f"[WelcomeNotification] User not found: {user_id}")
        return {"sent": 0, "errors": 1}

    tokens = user.get("push_tokens", [])
    if not tokens:
        logger.info(f"[WelcomeNotification] No push tokens for user {user_id}")
        return {"sent": 0, "errors": 0}

    result = await send_expo_push(
        tokens=tokens,
        title=WELCOME_MESSAGE["title"],
        body=WELCOME_MESSAGE["body"],
        data={"type": "welcome", "screen": "Home"},
        channel_id="default",
    )

    if result["sent"] > 0:
        logger.info(f"[WelcomeNotification] Welcome notification sent to user {user_id}")
    else:
        logger.warning(f"[WelcomeNotification] Failed to send welcome notification to user {user_id}")

    return result


# Global instance (initialized on first use after DB is ready)
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get the global notification service instance"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
