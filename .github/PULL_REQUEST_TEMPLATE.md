<!--
Thanks for sending a patch. Keep the diff focused — small,
reviewable PRs land faster than large rewrites. See
CONTRIBUTING.md for layering and coding conventions.
-->

## Summary

<!-- 1-3 sentences: what changed and why. -->

## Test plan

<!--
How a reviewer can verify this works. Include:
  - which tests you ran (`pytest`, a specific file, or manual steps),
  - any new tests you added,
  - manual UI checks if you touched templates or static assets.
-->

- [ ]
- [ ]
- [ ]

## Checklist

- [ ] Full test suite passes locally (`pytest -q`).
- [ ] Coverage on touched files did not drop.
- [ ] If a DB column or table was added/changed, a migration in
      `app/db/migrations/NNNN_slug.{sql,py}` was added and
      `app/db/schema.sql` reflects the new canonical shape.
- [ ] If a route, field, or class was renamed, every call site
      was updated (no commented-out / `legacy_X` branches left
      behind).
- [ ] If a template was added or changed, it uses the
      `TemplateResponse(request, "name.html", {...})` signature.
- [ ] Documentation that referred to changed behavior was updated.

## Related issues

<!-- Closes #NNN, refs #NNN. Optional. -->
