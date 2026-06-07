# Documentation index

Start with the project [README](../README.md) and [CHANGELOG](../CHANGELOG.md).
The docs below are grouped by how current they are — **operational** docs are
kept in sync with the code; **design** and **research** docs are point-in-time
and may lag the implementation.

## Operational (kept current)

| Doc | What it covers |
|-----|----------------|
| [getting-started.md](getting-started.md) | **Start here if you're new.** Plain-language, step-by-step setup for a non-developer parent (Raspberry Pi or spare laptop). |
| [architecture.md](architecture.md) | Three-layer design, feature layout, services (incl. the ingest normalization pipeline), request flow, crash recovery, testing. |
| [deployment.md](deployment.md) | Supported bare-metal Linux install: systemd unit, port 80 via `CAP_NET_BIND_SERVICE`, mDNS (`curatables.local`). The terse ops version of getting-started. |
| [dependencies.md](dependencies.md) | Canonical bill of materials: Python deps, system binaries, runtime filesystem layout, capabilities, container gotchas. |
| [backup.md](backup.md) | Backup/restore scripts and the systemd timer; SQLite WAL handling. |
| [upgrade.md](upgrade.md) | Upgrading an existing install; the forward-only DB migrator. |

## Security

| Doc | What it covers |
|-----|----------------|
| [threat-model.md](threat-model.md) | Trust boundaries and attack surface; companion to [../SECURITY.md](../SECURITY.md). |

## Design & planning (forward-looking specs)

| Doc | Status |
|-----|--------|
| [v0.6-safety-agent.md](v0.6-safety-agent.md) | RFC for the AI content-safety agent (v0.6 roadmap); not yet implemented. |
| [ui-and-playback-plan.md](ui-and-playback-plan.md) | Playback-baseline + GUI refresh plan — **executed** (see its final metric snapshot); kept as the design + verification record. |
| [filtered-search-and-proxy.md](filtered-search-and-proxy.md) | Exploratory design for wishlist search + real-time proxy (future). |

## Research & historical (reference only, not maintained)

| Doc | Note |
|-----|------|
| [prd.md](prd.md) | Original product requirements from inception; preserved for context. |
| [open-source-media-center-features.md](open-source-media-center-features.md) | One-off feature comparison (Jellyfin, Plex, Kodi, Stremio, YouTube Kids). |
| [search-research-playlists-at.md](search-research-playlists-at.md) | URL-grammar research; references the snapshot in [research/playlists_at_script.js](research/playlists_at_script.js). |
