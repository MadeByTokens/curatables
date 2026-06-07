# Contributing to Curatables

Thanks for considering a contribution. Curatables is a self-hosted
video curation server for parents and kids — every change should
reflect the constraints of that audience: zero ads, no algorithmic
recommendations, the parent stays in control, and the kid UI works
on devices as old as Safari iOS 9.

## Getting set up

Curatables is a Linux-only host today (Debian/Ubuntu family,
including Raspberry Pi OS). macOS and Windows can be **clients**
of a running server but are not supported as hosts.

```bash
# Clone and bootstrap a venv
git clone https://github.com/MadeByTokens/curatables.git
cd curatables
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# Validate the dependency probe
.venv/bin/python run.py --check

# Start the server (defaults to :8080)
.venv/bin/python run.py
```

Or, on a fresh Debian/Ubuntu box:

```bash
scripts/install.sh --dry-run   # validate without mutating the system
scripts/install.sh             # actually install
```

See [`docs/dependencies.md`](docs/dependencies.md) for the
canonical bill of materials and pinned version ranges.

## Running the test suite

```bash
.venv/bin/pytest                          # full suite
.venv/bin/pytest -q --cov=app             # with coverage
.venv/bin/pytest tests/test_routes.py -v  # one file
```

CI runs the same suite on Python 3.10 / 3.11 / 3.12 (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)). PRs are
expected to keep the suite green.

If you change templates or routes, also re-run the regression
guard explicitly — it scans `app/` for the deprecated
`TemplateResponse(name, context)` signature:

```bash
.venv/bin/pytest tests/test_template_response_signature.py -q
```

The default `pytest` run **excludes** the end-to-end smoke suite
(`tests/test_smoke_e2e.py`), which spawns a real `uvicorn`
subprocess and walks the parent-setup → kid-grid → add-flow
golden path. Run it explicitly when you change anything in the
boot path (lifespan, migrator, DI wiring, template loading):

```bash
.venv/bin/pytest -m smoke
```

The smoke suite is **hermetic**: `tests/fixtures/fake_ytdlp/` is
prepended to the subprocess's `PYTHONPATH` so `import yt_dlp`
resolves to a stub that returns deterministic metadata. No
network access is required at any point.

## Architecture conventions

The codebase uses a strict three-layer separation:

1. **Routes** (`app/features/<feature>/router.py`) — thin
   handlers, request parsing, response rendering. No business
   logic, no SQL.
2. **Services** (`app/services/`) — business logic. Pure Python
   functions where possible; orchestrate repositories.
3. **Repositories** (`app/repositories/`) — SQL, and only SQL.
   No HTTP concerns, no template rendering.

Other conventions worth knowing:

- **Adding a feature:** copy `app/features/_template/` to a new
  directory, write the router, wire it in `app/main.py` (one
  line). See [`docs/architecture.md`](docs/architecture.md).
- **Adding a DB column:** create a numbered migration in
  `app/db/migrations/NNNN_slug.sql` (or `.py` for logic) and
  update `app/db/schema.sql` to reflect the canonical shape.
  Don't rely on `CREATE TABLE IF NOT EXISTS` for new columns —
  the migrator is forward-only and authoritative.
- **TemplateResponse signature:** always
  `TemplateResponse(request, "name.html", {...})`. The old
  `TemplateResponse("name.html", {...})` form silently 500s on
  Starlette ≥1.0; `tests/test_template_response_signature.py`
  enforces the new form.
- **Multi-source URLs:** the add path accepts any URL `yt-dlp`
  supports. Don't hardcode YouTube URLs anywhere outside the
  fast-path regex in `app/services/video_source.py:parse_url`
  and the documented Tier 1 iframe entries in
  `app/services/embeds.py`. `rg -i youtube app/` should turn
  up only those two before you commit.
- **Composite video IDs:** every video is keyed on
  `{extractor}_{raw_id}` (assembled via
  `app.services.ids.make_video_id`). Routes, filesystem paths,
  and URLs use the composite; embed URLs need the raw ID via
  `Video.raw_id`.
- **Kid UI compatibility:** must work on Safari iOS 9. No ES6+,
  no `fetch`, no `const`/`let`. Test with the oldest browser
  you can find.

[`docs/architecture.md`](docs/architecture.md) documents the
project conventions and layering; if you change one, update the
docs in the same PR.

## Branch and PR flow

- Fork the repo, create a topic branch off `main`.
- Write a focused commit (or commits) — small, reviewable diffs
  beat one giant rewrite. Commit messages: lowercase, terse,
  scope-prefixed when natural (e.g.,
  `parent_settings: validate disk-quota input`).
- Run the full test suite locally before pushing.
- Open a PR against `main`. CI must be green; coverage should
  not regress on the file(s) you touched.
- Use the PR template — `## Summary` + `## Test plan` is the
  minimum.

## Reporting bugs

File an issue using the bug report template. Include:
- The version / commit hash you're running.
- Steps to reproduce.
- The full traceback or relevant log lines (Curatables logs to
  stderr by default; under `systemd`, `journalctl -u curatables`).
- Expected vs. actual behavior.

For security issues, **do not** open a public issue. See
[`SECURITY.md`](SECURITY.md) for the disclosure process.

## Code of conduct

By participating you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
