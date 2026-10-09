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
.venv/bin/pytest                          # full suite (smoke tests excluded)
.venv/bin/pytest -q --cov=app             # with coverage
.venv/bin/pytest tests/test_routes.py -v  # one file
```

Tests are flat in `tests/` (`test_<area>.py`), with shared fixtures
in `tests/conftest.py`.

- **CSRF in route tests.** The `app` fixture in `tests/conftest.py`
  replaces `app.state.csrf.validate` with a stub that accepts any
  token, so route tests can POST without fetching one. This is a
  test-only stub; the production app has no bypass. CSRF itself is
  tested end to end in `tests/test_csrf.py`, which builds an app
  without the stub.
- **Guard tests** scan the source for project rules:
  `tests/test_template_response_signature.py` (the
  `TemplateResponse(request, name, context)` signature) and
  `tests/test_kid_js_es3.py` (no ES6+ in kid JS, H.264-first yt-dlp
  format). Re-run them when you touch templates, routes or kid JS.

The default `pytest` run **excludes** the end-to-end smoke suite
(`tests/test_smoke_e2e.py`, `addopts = "-m 'not smoke'"` in
`pyproject.toml`). It spawns a real `uvicorn` subprocess and walks
the parent-setup → kid-grid → add-flow golden path. Run it when you
change anything in the boot path (lifecycle, migrator, DI wiring,
middleware, template loading):

```bash
.venv/bin/pytest -m smoke
```

The smoke suite is **hermetic**: `tests/fixtures/fake_ytdlp/` is
prepended to the subprocess's `PYTHONPATH` so `import yt_dlp`
resolves to a stub that returns deterministic metadata. No
network access is required at any point.

### Lint

```bash
.venv/bin/ruff check .
shellcheck scripts/*.sh pi-gen/build.sh pi-gen/stage-curatables/*/*.sh
```

`ruff` is in `requirements-dev.txt`; its rule set (correctness-focused:
pyflakes, pycodestyle errors, bugbear, async) is in `pyproject.toml`.
`shellcheck` comes from your package manager (`apt install shellcheck`).
New scripts are linted too.

### CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every
push and PR to `main`:

| Job | What it runs | Blocking |
|---|---|---|
| `test` | `pytest -q --cov=app` on Python 3.10, 3.11, 3.12 and 3.13 (the Pi image runs 3.13) | yes |
| `lint` | `ruff check .` and the `shellcheck` command above | yes |
| `typecheck` | `mypy app` | yes |
| `build` | `python -m build` (sdist + wheel), after `test` | yes |

[`.github/workflows/build-image.yml`](.github/workflows/build-image.yml)
builds the Raspberry Pi image with pi-gen. It is heavy (~30–60 min), so
it runs only by hand (`workflow_dispatch`) or when a GitHub Release is
published, which gets the `.img.xz` attached. See
[`pi-gen/README.md`](pi-gen/README.md).

Run the test, lint and `mypy app` commands locally before pushing.

### Demo library

`scripts/seed_demo.py` builds a throwaway library for screenshots and
manual testing: two profiles (Ana in "watch + react" mode, Ben with
everything on and PIN 1234), a few channels, and short ffmpeg test clips
with generated thumbnails, so nothing is downloaded.

```bash
.venv/bin/python scripts/seed_demo.py /tmp/curatables-demo
.venv/bin/python run.py --data-dir /tmp/curatables-demo --port 8090
# parent password: demo-password
```

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
- **Changing the schema:** add a new numbered file to
  `app/db/migrations/` (`NNNN_slug.sql`, or `NNNN_slug.py` with
  `def up(conn)` when you need logic such as a conditional
  `ALTER TABLE`) and update the `app/db/schema.sql` snapshot to
  match. `schema.sql` is never executed. The migrator is
  forward-only (no downgrades), so a migration must be idempotent
  and safe on existing data. Never edit a migration that has
  shipped.
- **TemplateResponse signature:** always
  `TemplateResponse(request, "name.html", {...})`. The old
  `TemplateResponse("name.html", {...})` form silently 500s on
  Starlette ≥1.0; `tests/test_template_response_signature.py`
  enforces the new form.
- **Multi-source URLs:** the add path accepts any URL `yt-dlp`
  supports. Keep YouTube-specific URL handling inside the
  fast-path parser in `app/services/video_source.py`, the Tier 1
  iframe entries in `app/services/embeds.py` and the yt-dlp
  backend (`app/backends/ytdlp.py`).
- **Composite video IDs:** every video is keyed on
  `{extractor}_{raw_id}` (assembled via
  `app.services.ids.make_video_id`). Routes, filesystem paths,
  and URLs use the composite; embed URLs need the raw ID via
  `Video.raw_id`.
- **Kid visibility:** anything that lists, plays or reacts to a
  video for a kid goes through `ContentService.can_view` /
  `visible_channel_ids`. Don't write a second rule.
- **Per-kid feature switches:** kid routes for comments,
  reactions, tags, customize and uploads are guarded with
  `require_kid_feature("allow_...")`; a new switchable feature
  gets a profile column and the same guard.
- **Kid UI compatibility:** must work on Safari iOS 9. Kid JS lives
  in `app/static/kid/*.js` and is ES3: no `const`/`let`, no arrow
  functions, no `fetch` (use XHR). `tests/test_kid_js_es3.py`
  enforces this. Reference static files with `?v={{ asset_v }}`
  so browsers pick up changes after an upgrade. Test with the
  oldest browser you can find.

[`docs/architecture.md`](docs/architecture.md) documents the
project conventions and layering; if you change one, update the
docs in the same PR.

## Branch and PR flow

- Fork the repo, create a topic branch off `main`.
- Write a focused commit (or commits) — small, reviewable diffs
  beat one giant rewrite. Commit messages follow the existing
  history: a plain imperative subject, capitalised, no trailing
  period (e.g. `Fix the systemd install path and support media on
  an external drive`, `Add GitHub Actions workflow to build the Pi
  image`). A short area prefix such as `CI:` or `Docs:` is fine
  when it helps. Put the details in a bulleted body when the
  change has several parts.
- Run the tests and lint locally before pushing (see above).
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
