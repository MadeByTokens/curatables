# Feature Template

Copy this directory to create a new feature module.

## Files

- `router.py` — FastAPI router with thin route handlers
- `schemas.py` — (optional) Pydantic models for request/response validation

## Rules

1. Routes call services, never repositories directly
2. Routes never contain SQL
3. Business logic belongs in `app/services/`
4. New database tables go in `app/db/schema.sql`
5. New models go in `app/models/`
6. Wire your router in `app/main.py`
