# curatables

A small server you run at home so your kids can watch **only the videos
you picked**: no ads, no recommendations, no autoplay, no way out to the
rest of the internet. You add videos from YouTube, Vimeo, Dailymotion,
TikTok, PeerTube, TED and [~1,800 other sites](https://github.com/yt-dlp/yt-dlp);
they are downloaded to your server and play from there on any phone,
tablet or laptop in the house.

It was built for children who can't be handed the open
internet, so the kid screen is deliberately calm and simple, and each
child's screen can be made simpler still.

## Get started

| You have… | Do this |
|---|---|
| A **Raspberry Pi 4 or 5** | Flash the ready-made image, boot, open `http://curatables.local`, set a password. [pi-gen/README.md](pi-gen/README.md) |
| A spare **Linux** laptop / mini-PC (Debian, Ubuntu) | Follow the [step-by-step guide](docs/getting-started.md), or run `sudo scripts/install.sh --systemd`. |
| Want to try it on your computer | `scripts/install.sh && .venv/bin/python run.py`, then open `http://localhost:8080/parent/`. |

New to all this? The [Getting started guide](docs/getting-started.md) is
written for parents who can copy and paste a command but aren't
developers. Then read [How Curatables keeps kids safe](docs/safety-model.md).

## How it works

1. You open the **parent dashboard** and paste a video, channel or
   playlist link.
2. You watch each video in the preview and tick that you've seen all of
   it; only then is it added.
3. The server downloads it and converts it to a format that plays on old
   devices too (H.264/AAC, up to 720p; an iPad from 2015 is fine).
4. Your kid opens `http://curatables.local/` on their tablet, taps their
   picture, and sees a grid of the videos you approved. Tap to play;
   when it ends: **Again** or **Home**.
5. You see what was watched, reactions and comments on the dashboard.

## Screenshots

_A demo library (generated test clips, no real data)._

**Kid side** — calm, finite, picture-driven.

| Who's watching? | Video grid | Watch page |
|:---:|:---:|:---:|
| [![Profile picker](docs/screenshots/kid-profiles.png)](docs/screenshots/kid-profiles.png) | [![Kid home grid](docs/screenshots/kid-home.png)](docs/screenshots/kid-home.png) | [![Kid watch page](docs/screenshots/kid-watch.png)](docs/screenshots/kid-watch.png) |

**Parent side** — glanceable triage.

| Dashboard | Content library | Add a video |
|:---:|:---:|:---:|
| [![Parent dashboard](docs/screenshots/parent-dashboard.png)](docs/screenshots/parent-dashboard.png) | [![Content library](docs/screenshots/parent-content.png)](docs/screenshots/parent-content.png) | [![Add a video](docs/screenshots/parent-add.png)](docs/screenshots/parent-add.png) |
| Usage stats | Per-video detail | Settings |
| [![Usage stats](docs/screenshots/parent-stats.png)](docs/screenshots/parent-stats.png) | [![Per-video detail](docs/screenshots/parent-content-detail.png)](docs/screenshots/parent-content-detail.png) | [![Settings](docs/screenshots/parent-settings.png)](docs/screenshots/parent-settings.png) |

## What it does

- **Curate** — add videos, whole channels or playlists by link; preview
  and approve each one; organise them into channels; share a channel's
  list with another family (`.ytc`, text or PDF) and import theirs.
- **Per-child rules** — each kid gets a profile with an avatar, optional
  PIN (digits or three pictures), language (English, Portuguese,
  Spanish), theme (including a large-text, high-contrast one), the channels
  they may see, and switches for reactions, comments, tags, customizing,
  uploads, search and subtitles. New profiles start with "watch + react".
- **Kid screen** — big picture buttons, an on-screen PIN pad, no ads, no
  autoplay, no recommendations, no links out; works back to Safari on
  iOS 9.
- **Kid uploads** — kids can add their own videos; ones sent to a shared
  channel wait for your approval.
- **Parent dashboard** — "needs your attention" (failed downloads, kid
  uploads to approve, low disk), per-kid activity, stats, comments.
- **Looks after itself** — videos nobody watched for a while are removed
  to save space and come back when tapped (favourites can be kept
  forever); downloads queue one at a time; nightly backups; yt-dlp
  updates with one button; works with the library on a USB drive.

## Hardware

A **Raspberry Pi 4 (2 GB+) or Pi 5**, or any old laptop or mini-PC
running Debian/Ubuntu. Converting each new video is the only heavy job: a
Pi 5 or laptop does it quickly, a Pi 4 takes a few minutes per video.

On a Pi, keep the videos on a **USB hard drive or SSD**: SD cards are
small and wear out. `scripts/setup-external-drive.sh` moves the library
over and makes Curatables wait for the drive at boot
([details](docs/deployment.md#media-on-an-external-drive-raspberry-pi--usb-disk)).
A 2.5" hard drive may need a powered USB hub on a Pi 4.

macOS, Windows, iOS and Android work as the devices you **watch** on;
the server itself runs on Linux.

## Documentation

- **Parents:** [getting started](docs/getting-started.md) ·
  [safety](docs/safety-model.md) · [FAQ](docs/faq.md) ·
  [backups](docs/backup.md) · [upgrading](docs/upgrade.md)
- **Running the server:** [deployment](docs/deployment.md) ·
  [Pi image](pi-gen/README.md) · [configuration](docs/configuration.md) ·
  [dependencies](docs/dependencies.md)
- **Contributors:** [CONTRIBUTING](CONTRIBUTING.md) ·
  [architecture](docs/architecture.md) · [threat model](docs/threat-model.md)
- Everything: [docs/README.md](docs/README.md). Changes: [CHANGELOG.md](CHANGELOG.md).

## Roadmap

- **Next: kid "wishlist" search.** A kid searches YouTube through the
  server, sees filtered results, and taps "ask a grown-up"; you approve or
  reject from a requests inbox before anything is downloaded. Design:
  [docs/design/filtered-search-and-proxy.md](docs/design/filtered-search-and-proxy.md).
- **AI safety helper (v0.6).** A local model that pre-screens videos
  against per-child criteria and flags or suggests, never decides
  silently. Design: [docs/design/v0.6-safety-agent.md](docs/design/v0.6-safety-agent.md).
- **Sign in to video sites (v0.7).** Pair a YouTube account from a phone
  so downloads stop hitting bot checks and age gates.
- **Later:** subscribe to another family's shared lists, HTTPS with a
  local certificate, the parent dashboard in other languages, Docker and
  NAS packages, offline web pages in the library
  ([idea](docs/design/offline-web-pages.md)).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and feature requests
use the [issue templates](.github/ISSUE_TEMPLATE); security issues go
through [SECURITY.md](SECURITY.md). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). Copyright 2026 Ricardo de Azambuja.
