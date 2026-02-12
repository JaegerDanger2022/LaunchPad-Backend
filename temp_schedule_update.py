@router.patch("/schedule/step/{step_id}")
async def update_scheduled_step(step_id: str, completed: Optional[bool] = None):
    """Update scheduled step (mark complete/incomplete). If completed=True, delete the step."""
    db = get_db()

    logger.info(f"[SCHEDULE] Updating step {step_id}, completed={completed}")

    from bson import ObjectId
    
    # If marking as complete, delete the task from the database
    if completed:
        result = await db.scheduled_steps.delete_one({"_id": ObjectId(step_id)})
        
        if result.deleted_count == 0:
            logger.error(f"[SCHEDULE] Step not found: {step_id}")
            raise HTTPException(status_code=404, detail="Step not found")
        
        logger.info(f"[SCHEDULE] Successfully deleted completed step {step_id}")
        
        return {
            "success": True,
            "deleted": True,
            "step_id": step_id
        }
    
    # If marking as incomplete (unchecking), update the status
    update_doc = {
        "completed": False,
        "completed_at": None,
        "updated_at": datetime.now()
    }

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
        "deleted": False,
        "step": {
            "_id": str(step["_id"]),
            "completed": step["completed"],
            "step_description": step["step_description"]
        }
    }
