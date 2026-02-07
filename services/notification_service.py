"""
Notification Service - Handles daily nudges and inactivity reminders

This service processes notifications for users based on their preferences:
1. Daily Nudges: Sent at user's preferred time in their timezone
2. Inactivity Reminders: Sent if no milestone completed for 2+ days
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import pytz
from core.database import get_db

logger = logging.getLogger(__name__)


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
        This should be called by a scheduler every 5-10 minutes.
        """
        try:
            current_time = datetime.now(timezone.utc)
            logger.info(f"[NotificationService] Processing notifications at {current_time.isoformat()}")

            # 1. Send daily nudges
            daily_count = await self.send_daily_nudges(current_time)
            logger.info(f"[NotificationService] Sent {daily_count} daily nudges")

            # 2. Send inactivity reminders
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
            return {
                "success": False,
                "error": str(e)
            }

    async def send_daily_nudges(self, current_time: datetime) -> int:
        """
        Send daily nudges to users whose notification time has arrived.

        Args:
            current_time: Current UTC time

        Returns:
            int: Number of nudges sent
        """
        try:
            # Query users who:
            # - Have notifications enabled (pref_notification_time is not null)
            # - Have at least one active dream
            users = await self.db.users.find({
                "pref_notification_time": {"$ne": None},
            }).to_list(length=None)

            sent_count = 0
            for user in users:
                # Check if user has active dreams
                user_id = user.get("user_id")
                has_active_dreams = await self._user_has_active_dreams(user_id)
                if not has_active_dreams:
                    continue

                # Check if it's time to send notification
                if self._is_notification_time(user, current_time):
                    # Check if we haven't sent one in the last 23 hours (prevent duplicates)
                    if not await self._sent_daily_nudge_recently(user):
                        await self._send_daily_nudge(user)
                        sent_count += 1

            return sent_count

        except Exception as e:
            logger.error(f"[NotificationService] Error sending daily nudges: {e}", exc_info=True)
            return 0

    async def send_inactivity_reminders(self, current_time: datetime) -> int:
        """
        Send reminders to users inactive for 2+ days.

        Args:
            current_time: Current UTC time

        Returns:
            int: Number of reminders sent
        """
        try:
            # Calculate threshold (2 days ago)
            threshold = current_time - timedelta(days=2)

            # Query users who:
            # - Last activity was before threshold (or never)
            # - Have notifications enabled
            # - Have active dreams
            users = await self.db.users.find({
                "$or": [
                    {"last_activity": {"$lt": threshold.isoformat()}},
                    {"last_activity": None}
                ],
                "pref_notification_time": {"$ne": None},
            }).to_list(length=None)

            sent_count = 0
            for user in users:
                user_id = user.get("user_id")

                # Check if user has active dreams
                has_active_dreams = await self._user_has_active_dreams(user_id)
                if not has_active_dreams:
                    continue

                # Only send if we haven't sent one in the last 24 hours
                if not await self._sent_inactivity_reminder_recently(user):
                    await self._send_inactivity_reminder(user)
                    sent_count += 1

            return sent_count

        except Exception as e:
            logger.error(f"[NotificationService] Error sending inactivity reminders: {e}", exc_info=True)
            return 0

    def _is_notification_time(self, user: Dict, current_time: datetime) -> bool:
        """
        Check if current time matches user's preferred notification time.

        Args:
            user: User document
            current_time: Current UTC time

        Returns:
            bool: True if it's time to send notification
        """
        try:
            user_tz_str = user.get("pref_timezone", "UTC")
            pref_time = user.get("pref_notification_time")  # e.g., "09:00"

            if not pref_time:
                return False

            # Parse preferred time
            hour, minute = map(int, pref_time.split(":"))

            # Get current time in user's timezone
            user_tz = pytz.timezone(user_tz_str)
            user_local_time = current_time.astimezone(user_tz)

            # Check if it's within the notification window (±5 minutes)
            return (
                user_local_time.hour == hour and
                abs(user_local_time.minute - minute) <= 5
            )

        except Exception as e:
            logger.error(f"Error checking notification time for user {user.get('user_id')}: {e}")
            return False

    async def _user_has_active_dreams(self, user_id: str) -> bool:
        """
        Check if user has any active dreams.

        Args:
            user_id: User ID

        Returns:
            bool: True if user has active dreams
        """
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
        """
        Check if we've sent a daily nudge in the last 23 hours.

        Args:
            user: User document

        Returns:
            bool: True if nudge was sent recently
        """
        last_nudge = user.get("last_daily_nudge")
        if not last_nudge:
            return False

        try:
            last_nudge_time = datetime.fromisoformat(last_nudge.replace("Z", "+00:00"))
            time_since = datetime.now(timezone.utc) - last_nudge_time
            return time_since < timedelta(hours=23)
        except Exception as e:
            logger.error(f"Error checking last daily nudge: {e}")
            return False

    async def _sent_inactivity_reminder_recently(self, user: Dict) -> bool:
        """
        Check if we've sent an inactivity reminder in the last 24 hours.

        Args:
            user: User document

        Returns:
            bool: True if reminder was sent recently
        """
        last_reminder = user.get("last_inactivity_reminder")
        if not last_reminder:
            return False

        try:
            last_reminder_time = datetime.fromisoformat(last_reminder.replace("Z", "+00:00"))
            time_since = datetime.now(timezone.utc) - last_reminder_time
            return time_since < timedelta(hours=24)
        except Exception as e:
            logger.error(f"Error checking last inactivity reminder: {e}")
            return False

    async def _send_daily_nudge(self, user: Dict):
        """
        Send daily nudge notification to user.

        Args:
            user: User document
        """
        user_id = user.get("user_id")

        try:
            # In a real implementation, you would:
            # 1. Get user's device token(s) from database
            # 2. Send push notification via FCM/OneSignal/etc.
            # 3. Handle delivery failures

            # For now, we'll just log and update the timestamp
            logger.info(f"[NotificationService] Sending daily nudge to user {user_id}")

            # TODO: Implement actual notification sending here
            # message = {
            #     "title": "Time to make progress! 🚀",
            #     "body": "Your dreams are waiting. Complete your next milestone today!",
            #     "data": {"type": "daily_nudge"}
            # }
            # await notification_provider.send(user_id, message)

            # Update last notification timestamp
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_daily_nudge": datetime.now(timezone.utc).isoformat()}}
            )

            logger.info(f"[NotificationService] Daily nudge sent to user {user_id}")

        except Exception as e:
            logger.error(f"[NotificationService] Error sending daily nudge to user {user_id}: {e}", exc_info=True)

    async def _send_inactivity_reminder(self, user: Dict):
        """
        Send inactivity reminder notification to user.

        Args:
            user: User document
        """
        user_id = user.get("user_id")

        try:
            logger.info(f"[NotificationService] Sending inactivity reminder to user {user_id}")

            # TODO: Implement actual notification sending here
            # message = {
            #     "title": "We miss you! 🌟",
            #     "body": "You haven't checked off any milestones in 2 days. Don't break your momentum!",
            #     "data": {"type": "inactivity_reminder"}
            # }
            # await notification_provider.send(user_id, message)

            # Update last inactivity reminder timestamp
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_inactivity_reminder": datetime.now(timezone.utc).isoformat()}}
            )

            logger.info(f"[NotificationService] Inactivity reminder sent to user {user_id}")

        except Exception as e:
            logger.error(f"[NotificationService] Error sending inactivity reminder to user {user_id}: {e}", exc_info=True)


# Global notification service instance (initialized on app startup)
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get the global notification service instance"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
