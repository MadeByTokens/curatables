# Documentation

Start with the project [README](../README.md). Pick the section for what
you're doing.

## Parents

| Doc | What it covers |
|-----|----------------|
| [getting-started.md](getting-started.md) | **Start here.** Step-by-step setup on a Raspberry Pi or spare laptop, for non-developers. |
| [safety-model.md](safety-model.md) | What a child can and can't reach, what is recorded, and how to lock the tablet. |
| [faq.md](faq.md) | Everyday questions: can't connect, video stuck, YouTube blocked, disk full, PINs. |
| [backup.md](backup.md) | Nightly backups, restoring, testing a backup. |
| [upgrade.md](upgrade.md) | Updating yt-dlp from the dashboard; upgrading Curatables. |

The dashboard also has a built-in help page (**Help** in the parent menu).

## Running the server

| Doc | What it covers |
|-----|----------------|
| [deployment.md](deployment.md) | Permanent install with systemd, port 80, mDNS, media on an external USB drive. |
| [../pi-gen/README.md](../pi-gen/README.md) | The ready-made Raspberry Pi image: what's in it, building it. |
| [configuration.md](configuration.md) | Every setting in `config.json`, per-child options, environment variables, command line. |
| [dependencies.md](dependencies.md) | Bill of materials: Python packages, system binaries, filesystem layout. |

## Contributors

| Doc | What it covers |
|-----|----------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Dev setup, tests, lint, CI, migrations, commit style. |
| [architecture.md](architecture.md) | Layers, services, request flow, download queue, background tasks. |
| [threat-model.md](threat-model.md) | Trust boundaries and attack surface; with [../SECURITY.md](../SECURITY.md). |

## Design notes (future work)

| Doc | Status |
|-----|--------|
| [design/filtered-search-and-proxy.md](design/filtered-search-and-proxy.md) | Kid "wishlist" search with parent approval (Mode A, built with the word list, allowlist and length rules; no classifier yet), and a filtered real-time proxy (not built). |
| [design/v0.6-safety-agent.md](design/v0.6-safety-agent.md) | AI content-safety agent RFC. Not implemented. |
| [design/offline-web-pages.md](design/offline-web-pages.md) | Idea: offline web pages in the library. Not a commitment. |

## History (reference only, not maintained)

| Doc | Note |
|-----|------|
| [history/prd.md](history/prd.md) | Original product requirements. |
| [history/ui-and-playback-plan.md](history/ui-and-playback-plan.md) | Playback-baseline and UI plan; executed. |
| [history/open-source-media-center-features.md](history/open-source-media-center-features.md) | Feature comparison with Jellyfin, Plex, Kodi, Stremio, YouTube Kids. |
| [history/search-research-playlists-at.md](history/search-research-playlists-at.md) | URL-grammar research, with the snapshot in [history/research/playlists_at_script.js](history/research/playlists_at_script.js). |
