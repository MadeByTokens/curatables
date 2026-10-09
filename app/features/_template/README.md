# Feature Template

Copy this directory to create a new feature module.

## Files

- `router.py` — FastAPI router with thin route handlers

## Rules

1. Routes call services, never repositories directly
2. Routes never contain SQL
3. Business logic belongs in `app/services/`
4. Schema changes are a new numbered migration in `app/db/migrations/`;
   also update the `app/db/schema.sql` snapshot (reference only, never executed)
5. New models go in `app/models/`
6. Video access goes through `ContentService.can_view` / `visible_channel_ids`
7. Kid features the parent can switch off use `require_kid_feature("allow_...")`
8. Wire your router in `app/main.py`

See [`docs/architecture.md`](../../../docs/architecture.md#adding-a-new-feature-contributor-guide).
