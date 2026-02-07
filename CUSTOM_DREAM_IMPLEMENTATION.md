# Custom Dream Creation - Backend Implementation

## Implementation Summary

The custom dream creation endpoint has been successfully implemented in the LaunchPad backend.

## Endpoint Details

**Endpoint:** `POST /api/dreams/create-custom`

**Location:** `api/dreams.py`

**Request Body:**
```json
{
  "user_id": "string",
  "dream_title": "string",
  "milestones": [
    {
      "title": "string",
      "description": "string (optional)",
      "challenge_type": "power_move" | "knowledge_quest" | "prep_ritual" | "courage_check" | "skill_flex" | "decision_point" | "celebration_moment",
      "order": 1
    }
  ]
}
```

**Response:**
```json
{
  "thread_id": "string"
}
```

## Features Implemented

### 1. Dream Limit Enforcement
- **Free Plan**: Maximum 2 dreams total (active + completed)
- **Pro Plan**: Maximum 3 active dreams (completed dreams don't count)
- Returns 403 error when limit is reached

### 2. No Milestone Dependencies
- **All milestones have empty dependencies**: `dependencies: []`
- All custom milestones are unlocked from the start
- Users can complete them in any order they prefer
- This gives users full flexibility and control over their custom dreams
- The auto-dependency generation logic (for AI-created dreams) is skipped for custom dreams

### 3. Default Values for Custom Milestones
- `time_estimate`: "30 min"
- `xp_points`: 10
- `status`: "pending"
- `streak_eligible`: true
- `is_custom`: true

### 4. Dream Document Creation
Creates a complete dream document with:
- Unique `thread_id` (UUID)
- `status`: "active"
- `category`: "custom"
- `is_custom`: true (flag to identify DIY dreams)
- Full roadmap with milestones array
- Metadata with `score`, `total_xp`, `version`, `structure`

### 5. User Document Updates
- Adds dream to `dreams_metadata` array
- Increments `dreams_count`
- Sets first milestone as `up_next` if user doesn't have one

### 6. Challenge Type Validation
Supports all 7 official challenge types:
- `power_move` - Power Move (⚡)
- `knowledge_quest` - Knowledge Quest (📚)
- `prep_ritual` - Prep Ritual (🎯)
- `courage_check` - Courage Check (💪)
- `skill_flex` - Skill Flex (🔥)
- `decision_point` - Decision Point (🤔)
- `celebration_moment` - Celebration Moment (🎉)

## Database Operations

### Dreams Collection
```python
{
  "thread_id": "uuid",
  "user_id": "firebase_uid",
  "dream": "Dream Title",
  "status": "active",
  "category": "custom",
  "is_custom": true,
  "isComplete": false,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime",
  "roadmap": {
    "status": "active",
    "milestones": [...]
  },
  "metadata": {
    "score": 0,
    "total_xp": 30,  // 10 XP per milestone
    "version": "1.0",
    "structure": "custom"
  }
}
```

### Users Collection Updates
```python
# dreams_metadata array entry
{
  "thread_id": "uuid",
  "dream": "Dream Title",
  "status": "active",
  "category": "custom",
  "milestones_count": 3,
  "completed_milestones_count": 0,
  "created_at": "ISO datetime"
}

# up_next (if not set)
{
  "milestone_id": "uuid",
  "milestone_title": "First Milestone Title",
  "dream_thread_id": "uuid",
  "dream_title": "Dream Title",
  "time_estimate": "30 min",
  "xp_points": 10,
  "challenge_type": "power_move",
  "streak_eligible": true,
  "updated_at": "ISO datetime"
}
```

## Error Handling

| Status Code | Scenario |
|-------------|----------|
| 201 | Success - Dream created |
| 400 | No milestones provided |
| 403 | Dream limit reached (free or pro) |
| 404 | User not found |
| 500 | Database connection failed or internal error |

## Logging

The endpoint logs:
- User ID and dream title
- Number of milestones
- Generated thread_id
- Milestone creation details
- User document updates
- Success/failure status

## Testing

To test the endpoint:

1. Start the backend server:
   ```bash
   python main.py
   ```

2. Send a POST request to `http://localhost:8001/api/dreams/create-custom`:
   ```bash
   curl -X POST http://localhost:8001/api/dreams/create-custom \
     -H "Content-Type: application/json" \
     -d '{
       "user_id": "test_user_id",
       "dream_title": "Learn to Cook",
       "milestones": [
         {
           "title": "Buy cooking equipment",
           "description": "Get basic pots, pans, and utensils",
           "challenge_type": "prep_ritual",
           "order": 1
         },
         {
           "title": "Learn knife skills",
           "description": "Watch tutorials and practice chopping",
           "challenge_type": "skill_flex",
           "order": 2
         },
         {
           "title": "Cook first meal",
           "description": "Prepare a simple pasta dish",
           "challenge_type": "power_move",
           "order": 3
         }
       ]
     }'
   ```

3. Expected response:
   ```json
   {
     "thread_id": "d4f8a2c3-1b5e-4a3d-9c7e-2f1a8b6d4e9c"
   }
   ```

## Integration with Frontend

The frontend already has the integration code in place:
- API function: `createCustomDream()` in `src/config/api.ts`
- UI Component: `DIYDreamModal.tsx`
- Challenge types: All 7 types with proper colors and emojis

Once the backend server is running with this endpoint, the DIY dream creation feature will work end-to-end.

## Files Modified

1. `api/dreams.py` - Added `/create-custom` endpoint and models
2. `api/dreams_crud.py` - Enhanced `AddCustomMilestoneRequest` with optional description

## Important Notes

- Custom dreams are marked with `is_custom: True` flag
- Default values for custom milestones: 30 min time estimate, 10 XP points
- **Custom milestones have `streak_eligible: False`** - they are user-created and not AI-verified
- **All milestones have no dependencies** - users can complete them in any order
- The auto-dependency generation logic is skipped for custom dreams (checked via `is_custom` flag)
- The first milestone becomes `up_next` if the user doesn't have one already
- Category is always "custom" for DIY dreams
- `card_color` is optional - if not provided, the frontend will use default theme colors

## Next Steps

1. Restart the backend server to load the new endpoint
2. Test the endpoint with the frontend app
3. Verify dreams appear correctly in the Dreams list
4. Verify all milestones are unlocked (no dependencies) and can be completed in any order
