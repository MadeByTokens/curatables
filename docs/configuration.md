# Configuration reference

Almost everything is set from the parent dashboard (**Settings**, and
**Kids** for per-child options). This page lists where each setting is
stored, for when you need to edit it by hand or script an install.

## Where settings live

| What | Where |
|---|---|
| Server and storage settings, parent password hash | `config.json` in the data dir |
| Kid profiles, channels, videos, comments, usage | `db/curatables.db` in the data dir |
| What YouTube answered (video details for 7 days, searches and channel listings for 6 hours) | `db/cache.db` in the data dir; safe to delete |
| Data dir location | `--data-dir` on the command line (the systemd unit passes `/home/curatables/curatables-data`); default `~/curatables-data` |

Stop the server before editing `config.json` by hand
(`sudo systemctl stop curatables`), and start it again afterwards.
Keys Curatables doesn't know are dropped without a warning, so check
the spelling: a misspelled key has no effect.

## `config.json`

### `server`

| Key | Default | Meaning |
|---|---|---|
| `port` | `8080` | Port to listen on. The systemd unit overrides it with `--port 80`. |
| `host` | `0.0.0.0` | Listen address. `127.0.0.1` = this computer only. |
| `log_level` | `info` | `debug`, `info`, `warning` or `error`. |
| `mdns_enabled` | `true` | Advertise the server as `curatables.local` on the network. |
| `mdns_name` | `Curatables` | Name shown in Bonjour/Avahi browsers. |
| `prometheus_enabled` | `false` | Expose counters at `/metrics` for Prometheus. |
| `timezone` | empty | The family's time zone (an IANA name such as `America/New_York`): where "today" starts in the stats and the "today/yesterday" dates. Times are stored in UTC and the Pi runs on UTC. Filled in from the parent's browser the first time they sign in; Settings → Storage → Time zone. Empty uses the box's own zone. |
| `kid_language` | `en` | Language of the "Who's watching?" picker (`en`, `pt-BR`, `es`). Settings → Storage → Kid screen language. |
| `allowed_hosts` | `[]` | Extra names the server answers to. It always answers to its hostname (with `.local`, `.lan`, `.home` and similar), `localhost` and IP addresses; other names are refused (DNS rebinding). |
| `helper_socket` | `/run/curatables-helper/helper.sock` | Socket of the root system helper behind the Box pages (Pi image only). Without it, those pages say they only work on the Pi image. |

### `storage`

| Key | Default | Settings page | Meaning |
|---|---|---|---|
| `path` | data dir | (read-only) | Where everything lives. Set by `--data-dir`. |
| `media_path` | empty | (read-only) | Where videos, uploads, thumbnails and channel pictures live, if not inside `path`. Set by `setup-external-drive.sh --media-only`, so the media can be on an exFAT, NTFS or FAT32 drive while the database stays on the SD card. If it is set and missing, the server starts without it: kids see a "videos are resting" screen and nothing is stored. |
| `import_roots` | `[]` | Import from a drive | Folders the [Import](getting-started.md#import-videos-already-on-a-drive) page may list video files from. The library's own folders are never listed. |
| `drive_mounts_dir` | `/mnt/curatables-drives` | Box → Drives & backups | USB drives opened with *Import from it* are mounted in folders here; each mounted one is an import folder too. |
| `drive_backups` | `false` | Box → Drives & backups | Also copy each nightly backup to `<media_path>/backups` on the videos drive. |
| `cache_days` | `0` | Settings → Freeing up space | A downloaded video nobody watched for this many days loses its file; it stays listed and downloads again when next tapped. Uploads, imported files and **Keep forever** videos are never removed. `0`, the default, keeps every video. This is the only setting that removes video files. |
| `low_space_percent` | `15` | — | The dashboard and the storage page warn when the videos drive has this much free or less. |
| `default_resolution` | `720p` | Storage | `360p`, `480p`, `720p` or `1080p`. Videos are always converted to at most 720p for playback. |
| `subtitle_langs` | `en` | Storage | Comma-separated language codes, `all`, or empty for none. |
| `min_free_disk_bytes` | 2 GB | Advanced | Downloads, conversions, imports and uploads are refused, or stopped part way with their partial files removed, before free space on the videos drive drops below this. Nothing is removed to make room. |
| `max_download_bytes` | 4 GB | Advanced | Source files bigger than this are not downloaded. |
| `max_concurrent_downloads` | `1` | Advanced | Downloads + conversions running at once. Keep `1` on a Raspberry Pi. |
| `max_upload_bytes` | 10 GB | Advanced | Largest file a parent can upload. |
| `max_kid_upload_bytes` | 0.5 GB | Advanced | Largest file a kid can upload. |
| `impersonate` | `chrome` | Advanced → Anti-Bot | Make yt-dlp's requests look like this browser. Empty = off. |
| `cookies_file` | empty | Advanced → Anti-Bot | Path to a Netscape `cookies.txt` for sites that need a login. |
| `cookies_from_browser` | empty | Advanced → Anti-Bot | Read cookies from a browser installed on the server (desktops only). |
| `cache_cleanup_interval_minutes` | `60` | — | How often the maintenance sweep runs. `0` turns it off. |
| `events_retention_days` | `365` | — | Usage events older than this are deleted by the sweep. `0` keeps them. |

### `parent`

| Key | Default | Meaning |
|---|---|---|
| `password_hash` | `null` | PBKDF2 hash of the parent password. `null` = first-run setup is open; see [Forgot the password](getting-started.md#i-forgot-the-parent-password). |
| `session_secret` | generated | Signs session cookies. Changing it signs everyone out. |
| `session_timeout_hours` | `24` | How long a parent sign-in lasts, counted from signing in, and how long an unused kid or parent cookie stays valid. |
| `session_epoch` | `0` | Bumped on every password change; sessions from an older epoch are signed out. |
| `setup_window_minutes` | `30` | While no password is set, the setup page only works for this many minutes after the server starts; restart it to reopen. `0` keeps it open. |
| `system_setup_done` | `false` | First-run step 2 (the Linux login password) is done. Only asked for on the Pi image. `curatables-reset.txt` on the SD card sets it back to `false`. |
| `linux_password_reset` | `false` | Set by `curatables-reset.txt`: step 2 may then choose a new Linux password without the old one. |

### `wishlist`

Rules for the YouTube results a kid sees on **Find new** (Settings →
Video requests). The trusted YouTube channels themselves are kept in the
database, by channel ID, not here.

| Key | Default | Meaning |
|---|---|---|
| `blocked_words` | a starter list in English, Spanish and Portuguese (horror, scary, gun, miedo, medo, …) | Results whose title, channel or description contains one of these whole words are hidden, and so are channels whose name does; a search containing one shows nothing. |
| `only_trusted_channels` | `false` | The family setting for **Kids find only their trusted channels** (Video requests → Trusted YouTube channels); each kid can follow it or not. |
| `auto_add_channel_id` | `null` | The family setting for requests from trusted channels: the id of a parent channel they go straight into, or `null` (they wait for review). Each kid can follow it or not. |
| `auto_add_kid_choice` | `false` | Requests from trusted channels go straight into the parent's channel the kid picked when asking, if they picked one (otherwise `auto_add_channel_id` decides). A kid set to wait always waits. |
| `max_duration_minutes` | `0` | Longer results are hidden; `0` (the default) = any length. |
| `max_results` | `12` | Results per page; **More** shows up to 5 pages. |

### `imports`

The Import page's online lookups (Settings → Import from a drive).

| Key | Default | Meaning |
|---|---|---|
| `lookups_enabled` | `true` | Offer pictures and descriptions from Wikipedia/Wikidata, YouTube, or a web page the parent links. Only the typed title, year or link is sent. Off: frames and uploaded pictures only. |
| `tmdb_api_key` | empty | Optional TMDB v3 key or v4 read token; adds film and TV posters and overviews from The Movie Database. |

## Per-child options (Kids → Edit)

Stored in the database, not `config.json`.

| Option | Meaning |
|---|---|
| Name, avatar | Shown on the profile picker and in the header. |
| PIN | Numbers (4–6 digits) or pictures (3 taps from 9 animals, for a child who doesn't read numbers yet). Keeps siblings out of each other's profiles; not a strong lock. Stored hashed. |
| Language | English, Português (Brasil) or Español for this kid's screens. Video and channel names are not translated. |
| Theme | Default, Playful, Calm, or Big & clear (large text, high contrast). |
| Search | Off, or search within the approved library. |
| What this kid can do | Reactions, comments, tags, customize, upload, automatic subtitles, asking for new videos from YouTube (the wishlist), downloading videos onto the device, and hiding videos (the parent is told). New profiles start with reactions, downloading and hiding. |
| Which channels can they see? | **All channels** (including new ones), **Only the ones ticked here**, or **None of yours** (only the kid's own channels). Ticking a channel means "only the ticked ones". |

## Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `CURATABLES_REQUIRE_MOUNT=1` | `run.py` | Refuse to start unless the data dir is a mounted drive. Set by `scripts/setup-external-drive.sh` (whole-library mode). |
| `CURATABLES_MEDIA_MOUNT` | `setup-external-drive.sh --media-only` | Where the media drive is mounted (default `/mnt/curatables-media`). |
| `LOG_FORMAT=json` | server | One JSON object per log line instead of plain text. |
| `CURATABLES_DATA_DIR` | `backup.sh`, `restore.sh` | Data dir to back up / restore into. |
| `RETAIN` | `backup.sh` | Number of snapshots to keep (default 14). |
| `CURATABLES_ROOT`, `CURATABLES_DATA`, `CURATABLES_USER` | `updater.sh` | Install dir, data dir and service user (set in `curatables-updater.service`). |
| `CURATABLES_YTDLP_STATE` | `ytdlp_update.py` | Where the yt-dlp update keeps its wheels and switch note (default `/var/lib/curatables-ytdlp`). |

## Command line

```text
python run.py [--port N] [--host ADDR] [--data-dir PATH] [--log-level LEVEL] [--check]
```

`--check` prints every dependency with its version and exits non-zero if
something required is missing.
