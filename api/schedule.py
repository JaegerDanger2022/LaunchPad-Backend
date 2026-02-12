"""
Schedule API Endpoints

Handles weekly milestone scheduling using LangGraph scheduler workflow.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import httpx
import os
import logging

from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================

class PlanMilestoneRequest(BaseModel):
    user_id: str
    milestone_id: str
    thread_id: str
    days_per_week: int
    week_start_date: str  # ISO 8601 (YYYY-MM-DD)

class ScheduledStepResponse(BaseModel):
    scheduled_date: str
    day_of_week: str
    step_description: str
    time_estimate_minutes: int
    order: int

# ============================================================================
# POST /schedule/plan-milestone
# ============================================================================

@router.post("/schedule/plan-milestone")
async def plan_milestone(req: PlanMilestoneRequest):
    """
    Trigger LangGraph scheduler workflow to create weekly plan.

    Steps:
    1. Fetch milestone details from dreams collection
    2. Invoke LangGraph scheduler_graph
    3. Parse scheduled_steps from response
    4. Enrich with dream metadata (title, color)
    5. Insert into scheduled_steps collection
    6. Return schedule
    """
    db = get_db()

    logger.info(f"[SCHEDULE] Planning milestone {req.milestone_id} for user {req.user_id}")

    # 1. Fetch dream + milestone details
    dream = await db.dreams.find_one({"thread_id": req.thread_id})
    if not dream:
        logger.error(f"[SCHEDULE] Dream not found: {req.thread_id}")
        raise HTTPException(status_code=404, detail="Dream not found")

    milestone = next(
        (m for m in dream.get("roadmap", {}).get("milestones", [])
         if m["id"] == req.milestone_id),
        None
    )
    if not milestone:
        logger.error(f"[SCHEDULE] Milestone not found: {req.milestone_id}")
        raise HTTPException(status_code=404, detail="Milestone not found")

    logger.info(f"[SCHEDULE] Found milestone: {milestone['title']}")

    # 2. Invoke LangGraph scheduler_graph
    langgraph_url = os.getenv("LANGGRAPH_AGENT_URL")
    assistant_id = os.getenv("LANGGRAPH_SCHEDULER_ASSISTANT_ID")
    api_key = os.getenv("LANGGRAPH_API_KEY")

    if not langgraph_url or not assistant_id or not api_key:
        logger.error("[SCHEDULE] Missing LangGraph configuration")
        raise HTTPException(
            status_code=500,
            detail="Scheduler service not configured. Please contact support."
        )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Create thread
            logger.info("[SCHEDULE] Creating LangGraph thread...")
            thread_response = await client.post(
                f"{langgraph_url}/threads",
                headers={"x-api-key": api_key},
                json={}
            )
            if thread_response.status_code != 200:
                logger.error("[SCHEDULE] Thread creation returned %s: %s", thread_response.status_code, thread_response.text)
            thread_response.raise_for_status()
            thread_data = thread_response.json()
            langgraph_thread_id = thread_data["thread_id"]

            logger.info(f"[SCHEDULE] Created thread: {langgraph_thread_id}")

            # Invoke scheduler
            logger.info("[SCHEDULE] Invoking scheduler workflow...")
            run_response = await client.post(
                f"{langgraph_url}/threads/{langgraph_thread_id}/runs/wait",
                headers={"x-api-key": api_key},
                json={
                    "assistant_id": assistant_id,
                    "input": {
                        "user_id": req.user_id,
                        "milestone_id": req.milestone_id,
                        "milestone_title": milestone["title"],
                        "milestone_description": milestone.get("description", ""),
                        "time_estimate": milestone.get("time_estimate", "30 mins"),
                        "days_per_week": req.days_per_week,
                        "week_start_date": req.week_start_date,
                        "user_timezone": "America/New_York"  # TODO: Get from user profile
                    }
                }
            )
            run_response.raise_for_status()

            # Wait for completion
            run_data = run_response.json()
            logger.info(f"[SCHEDULE] Scheduler workflow completed with status: {run_data.get('status')}")

            # Extract scheduled_steps from output
            output = run_data.get("output", {})
            scheduled_steps = output.get("scheduled_steps")

            if not scheduled_steps:
                error_msg = output.get("error", "Scheduler failed to generate schedule")
                logger.error(f"[SCHEDULE] Workflow error: {error_msg}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate schedule: {error_msg}"
                )

            logger.info(f"[SCHEDULE] Generated {len(scheduled_steps)} steps")

    except httpx.HTTPError as e:
        logger.error(f"[SCHEDULE] LangGraph API error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Scheduler service error: {str(e)}"
        )

    # 3. Enrich with dream metadata
    dream_color = dream.get("dream_card_bg", "#FF5733")
    dream_title = dream.get("dream", "Untitled Dream")

    # 4. Insert into scheduled_steps collection
    week_start = datetime.fromisoformat(req.week_start_date)
    inserted_steps = []

    for step in scheduled_steps:
        doc = {
            "user_id": req.user_id,
            "milestone_id": req.milestone_id,
            "dream_thread_id": req.thread_id,
            "dream_title": dream_title,
            "dream_color": dream_color,
            "scheduled_date": datetime.fromisoformat(step["scheduled_date"]),
            "day_of_week": step["day_of_week"],
            "week_start_date": week_start,
            "step_description": step["step_description"],
            "time_estimate_minutes": step["time_estimate_minutes"],
            "order": step["order"],
            "completed": False,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        result = await db.scheduled_steps.insert_one(doc)
        inserted_steps.append({**step, "_id": str(result.inserted_id)})

    logger.info(f"[SCHEDULE] Successfully inserted {len(inserted_steps)} scheduled steps")

    return {
        "success": True,
        "scheduled_steps": inserted_steps,
        "message": f"Scheduled {len(inserted_steps)} steps across {req.days_per_week} days"
    }

# ============================================================================
# GET /schedule/weekly
# ============================================================================

@router.get("/schedule/weekly")
async def get_weekly_schedule(user_id: str, week_start: Optional[str] = None):
    """
    Fetch user's weekly schedule grouped by day.

    Returns:
    {
      week_start_date: "2026-02-17",
      week_end_date: "2026-02-23",
      days: [
        {
          date: "2026-02-17",
          day_of_week: "Monday",
          tasks: [...]
        },
        ...
      ]
    }
    """
    db = get_db()

    logger.info(f"[SCHEDULE] Fetching weekly schedule for user {user_id}, week_start={week_start}")

    # Calculate week_start (default to current Monday)
    if week_start:
        week_start_dt = datetime.fromisoformat(week_start)
    else:
        today = datetime.now()
        week_start_dt = today - timedelta(days=today.weekday())

    week_end_dt = week_start_dt + timedelta(days=6)

    logger.info(f"[SCHEDULE] Week range: {week_start_dt.date()} to {week_end_dt.date()}")

    # Query scheduled_steps for this week
    cursor = db.scheduled_steps.find({
        "user_id": user_id,
        "scheduled_date": {
            "$gte": week_start_dt,
            "$lte": week_end_dt
        }
    }).sort([("scheduled_date", 1), ("order", 1)])

    steps = await cursor.to_list(length=1000)

    logger.info(f"[SCHEDULE] Found {len(steps)} scheduled steps")

    # Group by date
    days_map = {}
    for step in steps:
        date_str = step["scheduled_date"].strftime("%Y-%m-%d")
        if date_str not in days_map:
            days_map[date_str] = {
                "date": date_str,
                "day_of_week": step["day_of_week"],
                "tasks": []
            }
        days_map[date_str]["tasks"].append({
            "_id": str(step["_id"]),
            "milestone_id": step["milestone_id"],
            "dream_title": step["dream_title"],
            "dream_color": step["dream_color"],
            "step_description": step["step_description"],
            "time_estimate_minutes": step["time_estimate_minutes"],
            "completed": step["completed"],
            "order": step["order"]
        })

    # Fill empty days (Mon-Sun)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_array = []
    for i in range(7):
        date = week_start_dt + timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        days_array.append(days_map.get(date_str, {
            "date": date_str,
            "day_of_week": day_names[i],
            "tasks": []
        }))

    return {
        "week_start_date": week_start_dt.strftime("%Y-%m-%d"),
        "week_end_date": week_end_dt.strftime("%Y-%m-%d"),
        "days": days_array
    }

# ============================================================================
# PATCH /schedule/step/{step_id}
# ============================================================================

@router.patch("/schedule/step/{step_id}")
async def update_scheduled_step(step_id: str, completed: Optional[bool] = None):
    """Update scheduled step (mark complete/incomplete)."""
    db = get_db()

    logger.info(f"[SCHEDULE] Updating step {step_id}, completed={completed}")

    from bson import ObjectId
    update_doc = {"updated_at": datetime.now()}

    if completed is not None:
        update_doc["completed"] = completed
        if completed:
            update_doc["completed_at"] = datetime.now()
        else:
            update_doc["completed_at"] = None

    result = await db.scheduled_steps.update_one(
        {"_id": ObjectId(step_id)},
        {"$set": update_doc}
    )

    if result.matched_count == 0:
        logger.error(f"[SCHEDULE] Step not found: {step_id}")
        raise HTTPException(status_code=404, detail="Step not found")

    # Fetch updated step
    step = await db.scheduled_steps.find_one({"_id": ObjectId(step_id)})

    logger.info(f"[SCHEDULE] Successfully updated step {step_id}")

    return {
        "success": True,
        "step": {
            "_id": str(step["_id"]),
            "completed": step["completed"],
            "step_description": step["step_description"]
        }
    }

# ============================================================================
# DELETE /schedule/milestone/{milestone_id}
# ============================================================================

@router.delete("/schedule/milestone/{milestone_id}")
async def delete_milestone_schedule(milestone_id: str, user_id: str):
    """Delete all scheduled steps for a milestone (cascade delete)."""
    db = get_db()

    logger.info(f"[SCHEDULE] Deleting schedule for milestone {milestone_id}, user {user_id}")

    result = await db.scheduled_steps.delete_many({
        "user_id": user_id,
        "milestone_id": milestone_id
    })

    logger.info(f"[SCHEDULE] Deleted {result.deleted_count} scheduled steps")

    return {
        "success": True,
        "deleted_count": result.deleted_count
    }
