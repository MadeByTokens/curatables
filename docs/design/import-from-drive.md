# Import From the Drive — Design Notes

**Date:** 2026-10-09
**Status:** Planned, not built.
**Scope:** (1) Let the media live on an external drive that is not ext4, with the database staying on the SD card. (2) A parent page that turns video files already on that drive into library videos, with a step that looks up a cover image and a description online.

---

## 1. Motivation

A parent who already has a drive full of movies (DVD rips, saved YouTube videos, files from the internet) can only add them today by uploading each one through the browser: a second copy of every file, a slow transfer over Wi-Fi, and a bare title with a random frame as the picture.

Two things stand in the way:

- `setup-external-drive.sh` mounts the drive **as the whole data dir** and so requires ext4. A drive that already holds a library is almost always exFAT or NTFS, and reformatting it erases the movies.
- Nothing on the server can see files that did not arrive through an upload or a download.

## 2. Part 1: media on any drive

### Split the data dir

| Stays in the data dir (SD card, ext4) | Moves to the media dir (external drive, any filesystem) |
|---|---|
| `config.json`, `db/`, `logs/`, update flag files | `videos/`, `uploads/` (and `uploads/.tmp/`), `thumbnails/`, `channels/` |

- New setting `storage.media_dir`. Empty means "same as the data dir", so every existing install keeps working unchanged.
- SQLite stays on ext4. A journaled filesystem plus POSIX locking is what keeps the database safe through a power cut. exFAT has neither a journal nor Unix permissions, and the database is the one thing that cannot be re-downloaded.
- The database writes are small, so SD-card wear is not a concern. The wear comes from video writes, which move to the drive. Nightly backups stay where they are (`/var/lib/curatables-backups`).

### Code changes

- Every path built as `config.data_dir / "videos"` (or `"uploads"`, `"thumbnails"`, `"channels"`) goes through `StorageService` or a `config.media_dir` property instead:
  - `app/db/schema.py`
  - `app/features/media/router.py`
  - `app/services/thumbnails.py`
  - `app/services/uploads.py`
  - `app/config.py` `ensure_dirs`
  - storage report and relocation
- A guard test greps `app/` for `data_dir / "videos"`-style joins so new code cannot reintroduce them.
- `CURATABLES_REQUIRE_MOUNT=1` checks the media dir when one is set.
- The free-space checks (`min_free_disk_bytes`) measure the media dir, not the data dir.

### Supported filesystems

| Filesystem | Supported | Notes |
|---|---|---|
| ext4 | Yes, best | Journaled; ownership works normally. |
| exFAT | Yes | Kernel driver. No journal: after an unplug or power cut, run `fsck.exfat`. |
| NTFS | Yes | Kernel `ntfs3` driver. |
| FAT32 | Refused | 4 GB file limit; movies do not fit. |

- exFAT and NTFS have no Unix owners, so the drive is mounted with `uid=curatables,gid=curatables,umask=0027`.
- The library's own file names are hash or id based, so the characters those filesystems forbid never come up.

### Setup script

`setup-external-drive.sh --media-only /dev/sda1`:

- accepts ext4, exFAT or NTFS, refuses FAT32 and **never formats**.
- mounts the drive by UUID with `nofail` at `/mnt/curatables-media`, with the uid and gid options when the filesystem needs them.
- creates `curatables-library/` on the drive and points `storage.media_dir` there. Everything else on the drive stays untouched.
- moves any existing `videos/`, `uploads/`, `thumbnails/` and `channels/` folders across.
- adds a drop-in with `RequiresMountsFor=` and `ReadWritePaths=` for the mount (the hardened unit cannot write anywhere else).

The current whole-data-dir ext4 mode stays the default when `--media-only` is not passed.

## 3. Part 2: the import page

Parent-only, at `/parent/import`, linked from **Add videos**. It is a wizard: pick the files, answer a few questions about each one, review, and import in the background.

### Step 1: pick files

- **Lists video files** under the import roots: setting `import.roots`, defaulting to the media drive's mount point minus `curatables-library/`.
  - Each folder can be expanded.
  - Each file shows its name, size and duration, and whether it plays as-is or needs converting, with a rough time.
  - ffprobe runs lazily, and the result is cached by path, size and mtime.
- **Path rules:**
  - The path must `realpath` inside a root.
  - Symlinks and hidden files are skipped.
  - Extensions come from the upload allowlist (`mp4 mkv webm mov m4v avi mpg mpeg`).
- **Already imported** files are marked. Identity is the content hash, the same `up_<sha256[:16]>` id uploads use, so a file that was uploaded once is recognised too.
- **Sidecar files** next to a movie are picked up and offered first in Step 3:
  - `poster.jpg`, `folder.jpg` or `<name>.jpg`
  - a Kodi `.nfo`
  - `<name>.srt` subtitles

### Step 2: a few questions per file

The form is pre-filled from the file name: `Finding.Nemo.2003.1080p.mkv` becomes the title "Finding Nemo" and the year 2003, with the release tags stripped.

1. **Title.** Pre-filled; the parent can edit it.
2. **Where is it from?**
   - Movie or TV show (DVD, Blu-ray, a streaming rip)
   - YouTube
   - Somewhere else on the internet
   - Home video
3. **Extra details**, depending on the answer:
   - Movie or TV show: year, and optionally season and episode.
   - YouTube: the link if they have it, otherwise just the title.
   - Somewhere else: the page URL if they have it.
4. **Which channel or folder** it goes in, and which kids can see it. The choices are the same as on the upload page.

Answers can be applied to a whole folder at once, for a season of a show or a set of videos from one channel.

### Step 3: look it up

The server, never the parent's or kid's browser, searches according to the answer to "where is it from". It shows up to six candidates, each with an image, a title and a short description. The parent picks one, edits the description, or chooses a fallback.

| Origin | Source | Needs a key | What it gives |
|---|---|---|---|
| Movie or TV show | Wikipedia (REST search and page summary), in the parent's language with English as the fallback | No | Summary text and the infobox image, which is usually the poster. Wikidata adds the year, the age rating (MPAA/BBFC), and a check that it really is a film or TV series. |
| Movie or TV show | TMDB, optional, used when a key is set in Settings | Free key | Posters in several sizes, a short family-friendly overview, certification. |
| YouTube | `backend.search()` (the yt-dlp search the wishlist already uses), or `fetch_video_info()` when a link was given | No | Real title, channel, description, thumbnail, and the original URL. |
| Somewhere else | The given URL fetched with `safe_fetch`; `og:image`, `og:title`, `og:description` | No | Whatever the page declares. |
| Any origin | Fallbacks | No | Four frames taken from the video (at 10%, 30%, 50% and 70%) to choose from; upload an image; or a sidecar file from Step 1. |

**Rules for what comes back:**

- Every image download goes through `safe_fetch`, so only public addresses are reached and redirects are checked.
- Every image passes `validate_image` and is saved as the video's thumbnail (`thumbnail_type="custom"`). Kid pages only ever load it from the server, so the CSP stays `img-src 'self'`.
- Descriptions are plain text with markup stripped. They are capped at about 600 characters and cut at a sentence boundary.
- The parent sees the full text before saving. Encyclopedia summaries can contain plot spoilers or mature themes, so nothing is accepted automatically.
- When an age rating is found, it is shown next to the candidate, for example "Rated PG". It is information for the parent, not a filter.

### Step 4: review and import

- **A summary table:** file, title, picture, where it goes, and what will happen ("plays as-is", "repackage, seconds", "convert, about N h").
- **Move or copy:**
  - **Move** is the default when the file and the library are on the same drive. It is an instant `rename` and uses no extra space.
  - **Copy** leaves the original in place.
  - Across drives, a move is copy, verify the hash, then delete the original.
- **Keep the original when converting:** off by default when moving.
- **Import** queues the jobs. Each job runs the same pipeline as `UploadService.finalize`: probe and validate, hash, place under `uploads/up_<hash>/`, normalize (remux or transcode), thumbnail, insert the row. Only the source differs: a file on the drive instead of `uploads/.tmp`.
  - That shared tail is pulled out of `finalize` into one function both callers use.
- **Visibility:** videos land with the status the parent chose in Step 2. A kid sees nothing before the parent finishes the wizard.

### Background jobs

- New table `import_jobs`: id, source path, size and mtime, the chosen metadata as JSON, mode (move or copy), state (`queued`, `working`, `done`, `failed`), error, video id.
- A single worker, sharing the "Downloads at once" limit, because transcoding is what a Pi 4 is slowest at.
- After a restart, `working` jobs go back to `queued`. A move only deletes the source after the new row is committed, so a crash leaves a duplicate at worst, never a loss.
- The import page shows progress. The dashboard shows "3 imports running" while any job is active.

### Data model

Migration `0012_imports`:

- `import_jobs`, as above.
- New columns on `videos`:
  - `origin`: `movie`, `youtube`, `web`, `home` or empty.
  - `origin_ref`: a Wikidata id, a TMDB id or a YouTube id.
  - `source_path`: where the file came from, for display and for the "already imported" check.
- When the origin is YouTube and the id is known, `original_url` is set too. A later wishlist request or channel download of the same video is then shown as "already in the library".

### Safety and privacy

- Parent-only routes with CSRF. The file browser never leaves the import roots.
- Lookups send the title and year (never the file, and never anything about the kids) to Wikipedia, Wikidata, TMDB or YouTube from the server. The threat model gets a line for this, and Settings can turn lookups off ("Offline: frames and sidecar images only").
- New outbound hosts: `*.wikipedia.org`, `www.wikidata.org`, `upload.wikimedia.org`, `api.themoviedb.org`, `image.tmdb.org`, all fetched through `safe_fetch`.
- No new dependencies: Wikipedia and TMDB are plain JSON over the existing HTTP client, and file-name parsing is a small regex.

### Kid side

Nothing changes in how kids browse. The description shows on the watch page where downloaded videos already show theirs, and the poster becomes the card picture.

## 4. Build order

1. **Media dir split** and `setup-external-drive.sh --media-only`, with tests for the path refactor and a docker-chroot run of the script against an exFAT image.
2. **Shared upload/import tail** pulled out of `UploadService.finalize`, behaviour unchanged (the existing upload tests must pass untouched).
3. **Import page, Steps 1, 2 and 4,** with frames and sidecars as the only pictures, plus `import_jobs`. This is already useful offline.
4. **Lookups:** Wikipedia and Wikidata first, YouTube via the existing backend, then web pages via `og:` tags, and TMDB last behind its optional key.
5. **Docs:** configuration, deployment (`--media-only`), the threat model's outbound list, getting-started, and the FAQ ("I already have a drive full of movies").

## 5. Open questions

- **Pi 4 conversion time** for a full movie has to be measured on real hardware before the page promises an estimate. The Pi 4's hardware H.264 encoder (`h264_v4l2m2m`) could cut it a lot if the quality is acceptable.
- **TV series:** one channel per show with episodes in order, or a plain folder? This depends on how the kid UI should present a season.
- **Subtitles:** importing `<name>.srt` alongside the video needs to be checked against how `media/subs` serves downloaded subtitles.
