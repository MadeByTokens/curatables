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

5. **Audio and subtitles**, only when the file has more than one audio track or has subtitles (Step 1 already read the streams):
   - The audio language is pre-selected from the kid's language, and commentary tracks are named so they can be avoided.
   - Subtitle tracks are listed with their language and kind (see "Subtitles" below).

### TV shows

When a folder looks like a show, the page **suggests** grouping it and the parent confirms or declines. A folder looks like a show when its files carry episode markers (`S01E03`, `1x03`, `Episode 3`), or when Wikidata says the looked-up title is a TV series.

- **One channel per show**, created on the fly: "Bluey".
  - The poster becomes the channel icon.
  - Episodes go in order, season then episode number, so "next" on the watch page plays the next episode.
- The episode title comes from the lookup when Wikidata or TMDB has the episode list. Otherwise it is "Season 1, Episode 3".
- "No, keep them as separate videos" is always one click away, and a show channel can be merged into an existing channel.
- Seasons are not their own channels. A kid browsing one big channel per show is simpler than nested folders, and the episode order already carries the season.

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

- New table `import_jobs`: id, source path, size and mtime, the chosen metadata as JSON, mode (move or copy), state (`queued`, `working`, `cancelling`, `cancelled`, `done`, `failed`), phase, progress, ETA, error, video id.
- A single worker, sharing the "Downloads at once" limit, because transcoding is what a Pi 4 is slowest at.
- After a restart, `working` and `cancelling` jobs are cleaned up (below). `working` ones go back to `queued`, and `cancelling` ones become `cancelled`.
- A move only deletes the source after the new row is committed, so a crash leaves a duplicate at worst, never a loss.
- The dashboard shows "3 imports running" while any job is active.

### Following a conversion live

There is no up-front promise of how long a conversion takes. The estimate is measured as the job runs and keeps improving.

- **Every phase reports progress:** copy, hash, convert, subtitles, thumbnail.
  - **Copy and hash** are byte counts read in 8 MB chunks.
  - **Convert** runs ffmpeg with `-progress pipe:1 -nostats`. The worker reads `out_time_us` and `speed` line by line and compares them with the duration ffprobe gave.
- **The page shows the measured state**, for example "Converting: 34%, 1.6× real time, about 41 min left". The ETA is the remaining media time divided by a moving average of `speed` over the last minute, so it settles after the first minute instead of jumping around.
  - The page polls a small JSON endpoint every 2 s. The same numbers go on the dashboard.
- **A stall is visible.** If `out_time_us` stops advancing for 2 minutes, the job is shown as "stuck" with a Cancel button, instead of a spinner that never ends.
- **No fixed timeout.** Today's `subprocess.run(..., timeout=3600)` in `NormalizeService` would kill a full movie on a Pi 4 after an hour. A real progress signal plus stall detection replaces the fixed limit.
- **Afterwards,** the worker records the achieved speed per kind of conversion (remux, 480p, 720p from 1080p…) in `import_jobs`. The *next* file's estimate starts from that measured figure instead of a guess.

### Cancelling cleanly

Cancel is available at every phase, from the job list and from the dashboard. The parent can also cancel everything at once.

- **Everything temporary lives in one folder per job:** `uploads/.tmp/import-<job id>/`. That holds the partial copy, the ffmpeg output, the extracted subtitles, the thumbnail and ffmpeg's stderr log. Nothing is written elsewhere until the final commit step.
- **The final commit is a short step that cannot be interrupted:** rename the job folder's files into `uploads/up_<hash>/`, insert the row, and only then (for a move) delete the source. Cancelling before this step touches nothing outside the job folder.
- **ffmpeg** starts in its own process group (`start_new_session=True`).
  - Cancel sends `SIGTERM` to the group, waits 5 s, then sends `SIGKILL`, and reaps the process so no zombie is left.
  - ffmpeg's stderr goes to a file in the job folder, not a pipe, so a chatty ffmpeg cannot fill a pipe buffer or grow memory.
  - Progress lines are parsed one at a time and dropped. Only the latest numbers are kept.
- **The copy and hash loops** check the cancel flag between chunks, so a cancel takes effect within one 8 MB chunk.
- **After a cancel** the job folder is deleted and the job becomes `cancelled`. The source file is untouched, even for a "move".
- **Startup sweep:** any `uploads/.tmp/import-*` folder without a `working` job is deleted. This covers a crash or a power cut in the middle of a job.
- **Space is reserved up front.** A job starts only if free space on the media drive is at least the source size, plus the existing `min_free_disk_bytes` margin. A conversion cannot fill the drive halfway through.
- **Tests use a fake `ffmpeg`** that prints progress lines and sleeps. They cancel it at each phase and assert:
  - no job folder is left.
  - no `videos` row is created.
  - the source file is unchanged.
  - no child process is still alive.
  - a restart after a simulated crash cleans up.
- The same runner replaces the blocking call in `NormalizeService`, so uploads and channel downloads get the progress and the clean cancel too.

### Subtitles

How it works today (`app/features/media/router.py`, `StorageService.list_subtitles`, `static/kid/watch.js`):

- Subtitles are files named `video.<lang>.vtt` (or `.srt`) next to the video. The kid player fetches `/media/subs/<id>` and adds one `<track>` per file. The first one is switched on when the profile has "Turn subtitles on automatically".
- Only yt-dlp writes them (`subtitlesformat: vtt`). VTT files have their cue positions stripped on the way out, so captions are centred.

**Gaps found:**

1. **Lookups only search `videos/<id>/`.** Both the listing and the serving route call `storage.video_dir()`, so an uploaded video, which lives in `uploads/<id>/`, can never have subtitles. Fix: one `subtitle_dir(video)` that follows `storage_mode`, the same way `video_file_path` already does.
2. **`.srt` is served but browsers do not play it.** HTML `<track>` only accepts WebVTT, so an `.srt` file shows nothing. Fix: convert to VTT at import time, and serve only VTT. The conversion:
   - adds the `WEBVTT` header.
   - changes `00:01:02,500` to `00:01:02.500`.
   - strips the BOM and `{\an8}`-style tags.
   - keeps `<i>` and `<b>`.
   - decodes UTF-8 first, then Windows-1252, because many DVD-rip `.srt` files are not UTF-8.
3. **The track label is the raw code.** Kids see "en" or "pt-BR". Fix: label tracks with the language name from `app/i18n` ("English", "Português").
4. **Conversion can trip over subtitle streams.** `NormalizeService` passes no `-map` and no `-sn`, so ffmpeg picks streams on its own. Usually that is harmless, but an MKV with picture subtitles going to `.mp4` would likely make the whole conversion fail. ffmpeg's default may also pick a commentary track as the audio. Fix: map exactly one video stream and the chosen audio stream, plus `-sn`. Subtitles are handled separately (below).

**What import does with each kind:**

| Source | Example | What happens |
|---|---|---|
| Sidecar text file | `Movie.en.srt`, `Movie.pt-BR.vtt`, `Movie.srt` | Converted to `video.<lang>.vtt`. The language comes from the file name, or the parent picks it in Step 2 when the name has none. |
| Text stream inside the file | MKV with SubRip or ASS subtitles | Extracted with `ffmpeg -map 0:s:N -c:s webvtt`. The language comes from the stream's tag. |
| Picture stream inside the file | DVD VobSub (`dvd_subtitle`), Blu-ray PGS | These can't become text without OCR. The parent can choose "burn into the picture", which forces a full transcode and can't be switched off later, or skip them. Step 1 marks these tracks so it is clear before importing. |

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

## 3b. Fixes that help today, before the import page

Gaps 1, 2 and 4 from "Subtitles" above, and the 1-hour ffmpeg limit, are bugs in the existing upload and download paths. They are worth fixing first and on their own.

## 4. Build order

0. **Fixes from 3b:** subtitle directory, SRT to VTT, explicit stream mapping, and the progress-reporting, cancellable ffmpeg runner.
1. **Media dir split** and `setup-external-drive.sh --media-only`, with tests for the path refactor and a docker-chroot run of the script against an exFAT image.
2. **Shared upload/import tail** pulled out of `UploadService.finalize`, behaviour unchanged (the existing upload tests must pass untouched).
3. **Import page, Steps 1, 2 and 4,** with frames and sidecars as the only pictures, plus `import_jobs`. This is already useful offline.
4. **Lookups:** Wikipedia and Wikidata first, YouTube via the existing backend, then web pages via `og:` tags, and TMDB last behind its optional key.
5. **Docs:** configuration, deployment (`--media-only`), the threat model's outbound list, getting-started, and the FAQ ("I already have a drive full of movies").

## 5. Open questions

- **Hardware encoder.** The Pi 4's hardware H.264 encoder (`h264_v4l2m2m`) could make conversions several times faster than `libx264`. It needs a quality check on real files before becoming the default on a Pi. The live estimate works either way.
- **Episode order across shows.** Episode order needs a per-channel sort key. Check whether the existing channel ordering can carry it, or whether a `sort_index` column is needed.
