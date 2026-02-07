"""
Notification Scheduler - Background job scheduler for notifications

Uses APScheduler to run notification processing jobs at regular intervals.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from services.notification_service import get_notification_service

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Get the global scheduler instance"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def process_notifications_job():
    """Background job that processes notifications"""
    try:
        notification_service = get_notification_service()
        result = await notification_service.process_notifications()
        logger.info(f"[NotificationScheduler] Job completed: {result}")
    except Exception as e:
        logger.error(f"[NotificationScheduler] Job failed: {e}", exc_info=True)


def start_notification_scheduler():
    """
    Start the notification scheduler.
    Should be called during application startup.
    """
    try:
        scheduler = get_scheduler()

        # Add job to process notifications every 5 minutes
        scheduler.add_job(
            process_notifications_job,
            trigger=IntervalTrigger(minutes=5),
            id='process_notifications',
            name='Process notifications (daily nudges + inactivity reminders)',
            replace_existing=True,
            max_instances=1  # Prevent overlapping executions
        )

        # Start the scheduler
        scheduler.start()
        logger.info("[NotificationScheduler] Started notification scheduler (runs every 5 minutes)")

    except Exception as e:
        logger.error(f"[NotificationScheduler] Failed to start scheduler: {e}", exc_info=True)
        raise


def stop_notification_scheduler():
    """
    Stop the notification scheduler.
    Should be called during application shutdown.
    """
    try:
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("[NotificationScheduler] Stopped notification scheduler")
    except Exception as e:
        logger.error(f"[NotificationScheduler] Error stopping scheduler: {e}", exc_info=True)
