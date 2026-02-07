# Notification System - Backend Implementation

## Overview
This document describes the notification system backend implementation for PacksLight. The system sends two types of notifications:
1. **Daily Nudges** - Sent at user's preferred time in their timezone
2. **Inactivity Reminders** - Sent if no milestone completed for 2+ days

## Implementation Status ✅ COMPLETE

### 1. Database Schema Updates

#### User Collection - New Fields
```python
{
  "pref_notification_time": Optional[str],  # Format: "HH:MM" (24-hour), null if disabled
  "last_activity": Optional[str],  # ISO timestamp of last milestone completion
  "last_daily_nudge": Optional[str],  # ISO timestamp of last daily nudge sent
  "last_inactivity_reminder": Optional[str],  # ISO timestamp of last inactivity reminder sent
  # ... existing fields
}
```

**Field Details**:
- `pref_notification_time`: User's preferred daily notification time (e.g., "09:00" for 9 AM)
  - `null` = notifications disabled
  - Format validated: HH:MM where hour is 0-23, minute is 0-59
- `last_activity`: Updated automatically when user completes a milestone
- `last_daily_nudge`: Prevents duplicate daily nudges (must be 23+ hours apart)
- `last_inactivity_reminder`: Prevents spam (must be 24+ hours apart)

### 2. API Endpoints

#### 2.1 User Registration ✅
**Endpoint**: `POST /api/users/register`

**Updated Schema**:
```python
class CreateUserRequest(BaseModel):
    user_id: str
    email: str
    firstname: str
    lastname: str = ""
    pref_timezone: Optional[str] = None  # IANA timezone (e.g., "America/New_York")
    pref_notification_time: Optional[str] = None  # NEW: HH:MM format or null
```

**Changes Made**:
- Added `pref_notification_time` field to CreateUserRequest model
- User document now includes `pref_notification_time` and `last_activity` fields
- Both fields default to `None` on user creation

**File**: `api/users.py` (lines 25-32, 159-178)

#### 2.2 Update Notification Preferences ✅
**Endpoint**: `PATCH /api/users/{user_id}/notification-preferences`

**Request Schema**:
```python
class UpdateNotificationPreferencesRequest(BaseModel):
    pref_notification_time: Optional[str]  # HH:MM or null
```

**Validation**:
- Time format must be HH:MM (24-hour)
- Hour: 0-23, Minute: 0-59
- Returns 400 if invalid format

**Response**:
```json
{
  "success": true,
  "message": "Notification preferences set to 09:00",
  "pref_notification_time": "09:00"
}
```

**File**: `api/users.py` (lines 842-906)

#### 2.3 Milestone Completion - Activity Tracking ✅
**Endpoint**: `PUT /api/milestone/update-status/{user_id}/{thread_id}/{milestone_id}`

**Changes Made**:
- When milestone status is set to `"completed"`, automatically updates `last_activity` timestamp
- Uses current UTC time in ISO format
- Prevents inactivity reminders from being sent unnecessarily

**File**: `api/milestone.py` (lines 176-182)

### 3. Notification Service

#### 3.1 NotificationService Class ✅
**File**: `services/notification_service.py`

**Main Methods**:
```python
async def process_notifications():
    """Main entry point called by scheduler"""
    # 1. Send daily nudges
    # 2. Send inactivity reminders
    # Returns: counts of notifications sent

async def send_daily_nudges(current_time):
    """Send to users whose notification time has arrived"""
    # Checks:
    # - pref_notification_time is not null
    # - User has active dreams
    # - Current time matches user's preference (±5 min window)
    # - No nudge sent in last 23 hours

async def send_inactivity_reminders(current_time):
    """Send to users inactive for 2+ days"""
    # Checks:
    # - last_activity > 2 days ago OR never set
    # - pref_notification_time is not null
    # - User has active dreams
    # - No reminder sent in last 24 hours
```

**Helper Methods**:
- `_is_notification_time(user, current_time)` - Timezone-aware time matching
- `_user_has_active_dreams(user_id)` - Queries dreams collection
- `_sent_daily_nudge_recently(user)` - Checks last_daily_nudge timestamp
- `_sent_inactivity_reminder_recently(user)` - Checks last_inactivity_reminder timestamp
- `_send_daily_nudge(user)` - Placeholder for actual notification sending
- `_send_inactivity_reminder(user)` - Placeholder for actual notification sending

**Timezone Handling**:
- Uses `pytz` for timezone conversions
- Converts user's preferred time to UTC for comparison
- Notification window: ±5 minutes of target time
- Fallback to UTC if user has no timezone set

**Rate Limiting**:
- Daily nudges: Once per 23 hours
- Inactivity reminders: Once per 24 hours
- Single instance execution (via scheduler config)

#### 3.2 Background Scheduler ✅
**File**: `services/notification_scheduler.py`

**Configuration**:
- Uses `APScheduler` with `AsyncIOScheduler`
- Runs every 5 minutes
- Single instance execution (no overlap)
- Graceful startup/shutdown

**Job Configuration**:
```python
scheduler.add_job(
    process_notifications_job,
    trigger=IntervalTrigger(minutes=5),
    id='process_notifications',
    name='Process notifications (daily nudges + inactivity reminders)',
    replace_existing=True,
    max_instances=1
)
```

**Integration**:
- Started in `main.py` during app startup
- Stopped during app shutdown
- Logs job execution results

**File**: `main.py` (lines 18-20, 46-54)

### 4. Dependencies

#### New Requirements ✅
Added to `requirements.txt`:
```
apscheduler>=3.10.0  # Background job scheduler
pytz>=2023.3         # Timezone handling
```

**Installation**:
```bash
pip install -r requirements.txt
```

### 5. Current Implementation Status

#### ✅ Completed
- [x] User registration accepts notification preferences
- [x] PATCH endpoint to update notification preferences
- [x] Time format validation (HH:MM, 24-hour)
- [x] last_activity tracking on milestone completion
- [x] Notification service with timezone support
- [x] Background scheduler (runs every 5 minutes)
- [x] Daily nudge logic (23-hour cooldown)
- [x] Inactivity reminder logic (2-day threshold, 24-hour cooldown)
- [x] Rate limiting and duplicate prevention
- [x] Graceful startup/shutdown
- [x] Logging and error handling

#### 🚧 TODO - Integration with Push Notification Provider
The notification service currently **logs notification events** but does NOT send actual push notifications. To complete the integration:

1. **Choose a Provider**:
   - Firebase Cloud Messaging (FCM) - Recommended
   - OneSignal
   - Amazon SNS
   - Twilio

2. **Store Device Tokens**:
   - Add `device_tokens` array to user document
   - Update on app login/logout
   - Handle token refresh

3. **Implement Notification Sending**:
   - Replace TODO comments in `_send_daily_nudge()` and `_send_inactivity_reminder()`
   - Add notification provider client
   - Handle delivery failures (retry logic, token invalidation)

4. **Message Templates** (Already Defined):
   - Daily Nudge: "Time to make progress! 🚀 Your dreams are waiting."
   - Inactivity: "We miss you! 🌟 You haven't checked off any milestones in 2 days."

### 6. Testing

#### Manual Testing Checklist
- [x] User registration with notification time
- [x] Update notification preferences endpoint
- [x] Disable notifications (set to null)
- [x] Milestone completion updates last_activity
- [x] Scheduler starts on app startup
- [x] Scheduler stops on app shutdown

#### Integration Testing (To Be Done)
- [ ] Daily nudge sent at correct time in user's timezone
- [ ] Inactivity reminder sent after 2 days
- [ ] No duplicate notifications within cooldown period
- [ ] Notifications stop when user completes milestone
- [ ] Notifications stop when user has no active dreams
- [ ] Notifications stop when disabled by user

#### Load Testing Considerations
- Current implementation queries ALL users every 5 minutes
- For large user bases (10,000+ users), consider:
  - Indexing on `pref_notification_time` and `last_activity`
  - Batch processing with pagination
  - Distributed task queue (Celery)

### 7. Monitoring & Observability

#### Logs to Monitor
```
[NotificationService] Processing notifications at <timestamp>
[NotificationService] Sent X daily nudges
[NotificationService] Sent X inactivity reminders
[NotificationScheduler] Started notification scheduler (runs every 5 minutes)
[NotificationScheduler] Job completed: {...}
```

#### Metrics to Track (Future)
- Daily nudges sent per day
- Inactivity reminders sent per day
- Notification open rate (requires provider integration)
- User opt-out rate
- Scheduler execution time

#### Error Scenarios
- Database connection failure → Logged, job skips
- Invalid timezone → Defaults to UTC, logged
- Notification provider failure → Logged, retries next cycle

### 8. Security & Privacy

#### Data Privacy ✅
- Notification preferences stored per-user
- Users can disable at any time
- No sensitive data in notification messages
- Timezone stored securely in user document

#### Rate Limiting ✅
- Daily nudges: Max 1 per 23 hours
- Inactivity reminders: Max 1 per 24 hours
- Single scheduler instance prevents race conditions

#### GDPR Compliance
- Users control notification preferences
- Can disable notifications anytime
- Data deletion: Remove `pref_notification_time` on account deletion

### 9. Deployment Notes

#### Production Checklist
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Restart application to start scheduler
- [ ] Verify scheduler logs in production
- [ ] Set up monitoring alerts for failed jobs
- [ ] Add push notification provider credentials
- [ ] Test with small user cohort first

#### Environment Variables (No Changes Required)
- Existing variables are sufficient
- Notification provider may require additional config

### 10. Future Enhancements

#### Planned Features
- [ ] Smart timing (ML-based best time for each user)
- [ ] Custom notification messages by dream category
- [ ] Weekly recap notifications
- [ ] Streak about to break warnings
- [ ] Milestone deadline reminders
- [ ] Social notifications (friend completed milestone)

#### Performance Optimizations
- [ ] Database indexes on notification fields
- [ ] Redis cache for active dream status
- [ ] Distributed task queue (Celery/RQ)
- [ ] A/B testing framework for message templates

---

## Quick Start

### Running the Backend with Notifications

1. **Install Dependencies**:
   ```bash
   cd LaunchPad-Backend
   pip install -r requirements.txt
   ```

2. **Start the Server**:
   ```bash
   python main.py
   ```

   You should see:
   ```
   [STARTUP] Database connection completed
   [STARTUP] Notification scheduler started
   [NotificationScheduler] Started notification scheduler (runs every 5 minutes)
   ```

3. **Verify Scheduler is Running**:
   - Check logs every 5 minutes for:
     ```
     [NotificationService] Processing notifications at <timestamp>
     [NotificationService] Sent X daily nudges
     [NotificationService] Sent X inactivity reminders
     ```

4. **Test the Endpoints**:
   ```bash
   # Update notification preferences
   curl -X PATCH http://localhost:8001/api/users/{user_id}/notification-preferences \
     -H "Content-Type: application/json" \
     -d '{"pref_notification_time": "09:00"}'

   # Disable notifications
   curl -X PATCH http://localhost:8001/api/users/{user_id}/notification-preferences \
     -H "Content-Type: application/json" \
     -d '{"pref_notification_time": null}'
   ```

---

## Support

For issues or questions:
1. Check logs for error messages
2. Verify database connection
3. Ensure APScheduler is installed
4. Review notification service logs

## References

- Frontend Implementation: `PacksLight---Expo-Frontend/NOTIFICATION_SYSTEM_IMPLEMENTATION.md`
- APScheduler Docs: https://apscheduler.readthedocs.io/
- PyTZ Docs: https://pythonhosted.org/pytz/
