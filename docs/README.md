# Documentation

Start with the project [README](../README.md). Then pick the section for
who you are and what you're doing. The parent dashboard also has a
built-in **Help** page.

## Parents

For anyone looking after a Curatables box. No programming needed.

| Doc | Read it when |
|-----|--------------|
| [parents-guide.md](parents-guide.md) | **Start here if you have the ready-made Raspberry Pi image.** Flashing the SD card, Wi-Fi, the two passwords, your first video and kid, the Box pages, USB drives, backups and what to do when something's wrong. From Windows, Mac or a phone; no terminal. |
| [guide/parents.md](guide/parents.md) | You'd like to see it: every everyday screen in pictures, from adding a video and making a kid's profile to answering requests, backups and the Box pages. |
| [guide/kids.md](guide/kids.md) | A picture guide to the kids' screen, to look at together with your kid. |
| [getting-started.md](getting-started.md) | You are installing Curatables yourself on a Raspberry Pi or spare Linux laptop, and can copy and paste commands into a terminal. |
| [safety-model.md](safety-model.md) | You want to know what a child can and can't reach, what is recorded, and how to lock the tablet to Curatables. |
| [faq.md](faq.md) | You have an everyday question: can't connect, a video is stuck, YouTube fails, the disk is full, a PIN is forgotten. |
| [backup.md](backup.md) | You want to know what the nightly backup keeps, how to restore it and how to test it. |
| [upgrade.md](upgrade.md) | You are updating Curatables, the video downloader (yt-dlp) or the system. |

## People running their own server

For installs you set up and maintain yourself. Assumes you're
comfortable with a Linux terminal.

| Doc | What it covers |
|-----|----------------|
| [deployment.md](deployment.md) | Permanent install with systemd, port 80, `curatables.local` (mDNS), media on an external USB drive. |
| [configuration.md](configuration.md) | Every setting in `config.json`, per-child options, environment variables, command line. |
| [dependencies.md](dependencies.md) | Every Python package and system program, and where files live on disk. |
| [../pi-gen/README.md](../pi-gen/README.md) | What is in the Raspberry Pi image and how to build it. |

## Contributors

For people changing the code.

| Doc | What it covers |
|-----|----------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Development setup, tests, lint, CI, migrations, commit style. |
| [architecture.md](architecture.md) | Layers, services, request flow, download queue, background tasks. |
| [testing/release-0.6.0-ux.md](testing/release-0.6.0-ux.md) | Record of the 0.6.0 release test on a real box: scenarios with a parent and two kids, results, what is still open. |
| [testing/release-0.6.0-acceptance.md](testing/release-0.6.0-acceptance.md) | Record of the go/no-go run before publishing 0.6.0: a parent and three kids use and look after the box through the browser, with release criteria and results. |
| [threat-model.md](threat-model.md) | Trust boundaries and attack surface; read with [../SECURITY.md](../SECURITY.md). |

## Design notes (future work)

Background and plans for contributors. Not instructions.

| Doc | Status |
|-----|--------|
| [design/filtered-search-and-proxy.md](design/filtered-search-and-proxy.md) | Kids' video requests with parent approval: built, with the word list, channel allowlist and length rules, but no classifier. A filtered real-time proxy: not built. |
| [design/import-from-drive.md](design/import-from-drive.md) | Media on a non-ext4 drive and the Import page: built. The Pi 4 hardware video encoder: not built. |
| [design/v0.6-safety-agent.md](design/v0.6-safety-agent.md) | A local AI helper that pre-screens videos for the parent. Not built. |
| [design/offline-web-pages.md](design/offline-web-pages.md) | Idea: offline web pages in the library. Not a commitment. |

## History (reference only, not maintained)

Early planning documents, kept as they were written. They don't describe
how Curatables works today.

| Doc | Note |
|-----|------|
| [history/prd.md](history/prd.md) | Original product requirements. |
| [history/ui-and-playback-plan.md](history/ui-and-playback-plan.md) | Playback and UI plan. |
| [history/open-source-media-center-features.md](history/open-source-media-center-features.md) | Feature comparison with Jellyfin, Plex, Kodi, Stremio, YouTube Kids. |
| [history/search-research-playlists-at.md](history/search-research-playlists-at.md) | URL-grammar research, with the snapshot in [history/research/playlists_at_script.js](history/research/playlists_at_script.js). |
