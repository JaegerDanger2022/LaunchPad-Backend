# Quick Setup - Add These Lines

Quick reference for adding migration support to your backend.

---

## 1. Add to `main.py`

Add this import with your other router imports:

```python
from api import migration_temp
```

Add this router registration with your other routers:

```python
app.include_router(migration_temp.router, prefix="/api/migration", tags=["migration-temp"])
```

**Complete example:**

```python
# ... other imports ...
from api import users, dreams, milestone, victories, community, journey_recap
from api import migration_temp  # ← ADD THIS

# ... app initialization ...

# Routers
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(dreams.router, prefix="/api/dreams", tags=["dreams"])
app.include_router(milestone.router, prefix="/api/milestone", tags=["milestone"])
app.include_router(victories.router, prefix="/api/victories", tags=["victories"])
app.include_router(community.router, prefix="/api", tags=["community"])
app.include_router(journey_recap.router, prefix="/api/journey-recaps", tags=["journey-recap"])
app.include_router(migration_temp.router, prefix="/api/migration", tags=["migration-temp"])  # ← ADD THIS
```

---

## 2. Add Indexes to `core/database.py`

In the `setup_indexes()` function, add these lines:

```python
async def setup_indexes():
    # ... existing indexes ...

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
```

---

## 3. Deploy

```bash
railway up
# OR
git push
```

---

## 4. Test Migration Endpoints

```bash
# Test if endpoints are working
curl http://localhost:5000/api/migration/migration-status/YOUR_USER_ID
```

---

## 5. Run Migration

See [MIGRATION_SCRIPT_USAGE.md](./MIGRATION_SCRIPT_USAGE.md) for detailed instructions.

---

## 6. After Migration is Complete (Delete These)

### Backend:
```bash
rm api/migration_temp.py
```

### main.py:
Remove this line:
```python
app.include_router(migration_temp.router, prefix="/api/migration", tags=["migration-temp"])
```

### Frontend:
```bash
rm scripts/migrate-user-data.ts
```

---

That's it! 🎉
