# Changelog

Shipped feature history. The forward-looking roadmap lives in
[README.md](README.md#roadmap); the design rationale for each area lives
under [docs/](docs/README.md).

Tagged GitHub releases are what Pi boxes install from **Box → Updates**;
there is no PyPI or Docker release. The sections below v0.6.0 are the
development milestones up to the first public release, v0.1.0 (whose
code already called itself 0.5.0).

## Unreleased

**For parents**
- Requests from trusted channels can go straight into the channel the
  kid picked when asking: tick **If they picked one of your channels
  when asking, it goes straight there instead** (Trusted YouTube
  channels). With nothing picked, the usual setting decides; a kid set
  to wait still waits, and the daily limit still counts.
- **Help → Show me how** has the same kind of guides for parents:
  adding a kid, answering a video request, a video a kid hid. The
  written guides on GitHub use them as moving pictures.

**For kids**
- **Channels** in the menu lists every channel as big round pictures,
  like a channel list on YouTube, with **All videos** first, even when
  there is only one channel. Home shows only the kid's videos.
- **Help → Watch how** plays step-by-step guides made from real screens,
  with a finger showing what to tap: signing in, watching, arranging
  Home, channels, naming and making a channel, searching, asking for a
  video, hiding one. In the kid's language (English, Spanish or
  Portuguese), listing only what that kid can do. It moves on by itself
  every few seconds, or with the buttons; with Reduce Motion it waits
  for a tap and nothing glides. Each guide is checked against the
  screens it shows, so a changed screen can't leave it out of date.
- **All videos** has the same **Order** buttons as a channel (Newest,
  Oldest, A to Z, Z to A), kept for each kid apart from each channel's
  order. Adds one database migration.
- **New channel** on the Channels page makes a channel of the kid's own
  (with **Customize videos and channels** on).
- **Change** under a channel gives it the kid's own name and picture (a
  drawing or a photo from the tablet). Only that kid sees it; the
  channel stays as the parent set it for everyone else. Needs
  **Customize videos and channels**. Adds one database migration.

- **Came out first** is a new **Order** button on channels and on All
  videos: by when the video came out on YouTube (or when it was added,
  for one with no date), so a series lines up the way it was made,
  whatever order it was downloaded in.
- **Newest** and **Oldest** go by when a video was last downloaded: a
  video whose files were freed keeps its place, and when it downloads
  again it counts as the newest. Orders a kid already has stay as they
  are. Adds one database migration.
- **My order** on a channel lets the kid put its videos in exactly the
  order they want. Picking it changes nothing by itself; **Change the
  order** opens a short list (30 to a page) to arrange a draft: **Start
  from** Newest, Oldest, Came out first, A to Z or Z to A (asking first
  if moves would be lost), then ▲ ▼, **To the top**, **To the end** or
  **Move** to a place number. **Undo** takes back the last change, and
  nothing shows on the channel until **Save**; **Cancel** drops the
  draft and **Undo save** brings back the order from before. A video
  added later goes in by when it came out without moving any placed
  one. Each kid's own, per channel. Adds one database migration.

**Fixes**
- Exporting a channel whose name has accents or another script
  ("Canções", "名前の動画") failed with an error page; the file keeps the
  channel's name.
- Find new showed "Yes!" for a video that was added once and then
  deleted, moved out of the kid's channels or hidden by the kid, and
  asking for it again did nothing. It offers **Ask** again, and the old
  request waits for you again; saying yes to a video the kid had hidden
  gives it back to them. A video you hid from kids is left out of their
  results, like one you turned down.
- "What I asked for" leaves out a video that was added and then deleted
  or hidden (by a grown-up or by the kid), instead of offering **Yes!
  Watch** for a video that isn't there.

## v0.9.0

Each kid's own Home, channel order and Hide; Download onto the device;
search that forgives spelling; a downloader update that tries the new
version before using it; and a daily limit on requests that go straight
in. Boxes on v0.8.x install it from **Box → Updates**; it adds four
database migrations, applied on the first start (they only add tables
and columns).

**For parents**
- Requests from trusted channels go straight in at most 10 times a day
  for each kid (Trusted YouTube channels → **At most this many a day
  for each kid**; 0 means none). After that, a kid's requests wait for
  you until the next day, so a run of taps on Ask can't fill the
  library or the disk. Video requests marks them "Waited: daily limit
  reached"; the kid sees the usual "a grown-up will look" and nothing
  about a limit. Videos taken back still count for that day.
  Adds one database migration.
- A kid can hide a video they don't like: **Hide** under the video
  takes it out of that kid's lists, search and tags (with an Undo right
  after). It shows under **Needs your attention**, and on the video's
  page you delete it for everyone, keep it hidden from that kid, or show
  it to them again. Content → **Hidden by a kid** lists them all.
- A kid can save a video onto their device: **Download** under the
  video (on an iPad, into the Files app), named as the kid sees it; on
  a computer the player's own download option works too.
- Both are new switches under Kids → Edit → What this kid can do, on
  for every kid. Adds one database migration.
- A blocked word of 6 letters or more also catches a spelling one
  letter off ("skibidy" for "skibidi") in a kid's Find new search, and
  in titles and channel names.
- **Update the downloader** tries the new yt-dlp before using it: it
  must read a video's details, search, list a channel and start a
  download. Only then does the box switch, after running downloads
  finish, and if Curatables doesn't run well with it the previous
  version goes back in. A new version that fails is not used, and the
  page says what it couldn't do. The page shows each step and can be
  left; the update carries on, even through a power cut.

**For kids**
- Home is the kid's own, arranged like a tablet's home screen: at first
  their 12 newest videos, then whatever they put where. **Change my
  Home** adds a video to any of 24 spots, moves one (tap Move, then the
  spot) or takes one off; **Put on my Home** under a video adds it to
  the first free spot. **All** and the channel buttons show every
  video as before, and the Home button always comes back. Adds one
  database migration.
- A picture PIN allows 10 wrong tries before "Wait a little" (a
  number PIN still 5), so a quick finger isn't locked out as soon.
- On a channel, a kid picks the order of its videos: **Newest**,
  **Oldest**, **A to Z** or **Z to A**. A to Z goes by the kid's own
  titles, and numbers count as numbers ("2 Boats" before "10 Cars"), so
  numbering titles puts the videos in the kid's order. Each kid's choice
  stays for each channel. Adds one database migration.
- Search forgives spelling: when no video has the word typed, it shows
  the videos with a word that sounds like it ("dinasor" finds the
  dinosaurs) under "Did you mean one of these?", closest first, and only
  from what the kid can already see.
- A description the kid empties stays empty (**Reset to original**
  brings the first one back).
- When a video isn't there any more (a grown-up deleted it while an
  old page still pointed at it), the kid sees "This video isn't here."
  with the Home button, and any other missing page says "There's
  nothing here.", instead of "Oops! That didn't work."

**Fixes**
- The PIN page has an address of its own: Back, a reload or a tab the
  iPad restores shows it again, instead of asking to send a form again
  or showing a blank or "Oops" page.
- A download that is paused or cancelled part way is logged as
  stopped, not as a failure with "the drive is full".
- A video deleted while someone is on its page, or while a kid's
  player is loading it, answers "not found" on every page instead of
  an error.
- The same video sent twice at once (a double-tapped Send, or a parent
  and a kid sending the same clip) is stored once.

## v0.8.1

Signing in on a device that blocks cookies, and the Home Screen icon's
name. Boxes on v0.8.0 install it from **Box → Updates**; no database
changes.

**For parents**
- A browser that blocks cookies is told so ("This browser didn't keep
  the sign-in") on the kids' picker or the parent sign-in page, with
  what to do (on an iPad or iPhone, open Curatables from its Home Screen
  icon), instead of going back to "Who's watching?" or round in a loop.
- Added to a Home Screen, the box is named "Curatables", whichever page
  it was added from.

## v0.8.0

Trusted YouTube channels, for the whole family or one kid, and parent
pages that are easier on a small phone. Boxes on v0.7.0 install it from
**Box → Updates**; it adds one database migration, applied on the first
start. The list of channel names under Settings → Video requests
("Only show these channels") is replaced by trusted channels and not
carried over: if you used it, add those channels again on the Trusted
YouTube channels page.

**For parents**
- **Trusted YouTube channels** (Video requests → Trusted YouTube
  channels), matched by channel ID, so a look-alike channel with the
  same name doesn't count. Trust a channel for every kid or for one
  kid; on a kid's page, untick a family channel for that kid or add one
  just for them. Add one by pasting its link, @handle, ID or a link to
  one of its videos, by searching for it by name, or with **Trust** on
  a request.
- Two family settings, each of which a kid can follow or not: **Kids
  find only their trusted channels** (off by default: trusted channels
  don't limit what kids find unless you ask), and whether requests from
  trusted channels wait for you or go straight into a channel you pick.
- Easier on small phones: the whole menu fits on screen (in two rows),
  the page keeps its size when you tap a text box or menu on an iPhone,
  tick boxes are big enough for a thumb, and the Stats tables scroll on
  their own while the page stays put.
- The box keeps its system log on the SD card (up to 100 MB), so what
  happened before a restart or a power cut can still be looked up.

## v0.7.0

Undo and cancel everywhere, and more say for kids over their channels
and requests. Boxes on v0.6.0 install it from **Box → Updates**; it
adds three database migrations, applied on the first start.

**For kids**
- A request can be taken back ("Oops, don't ask"), and the video asked
  for again later.
- When asking for a video, a kid can make a new channel of their own
  for it (with Customize videos and own channels on), or suggest one's
  name to the parent. The video's Customize page makes a channel too,
  not only Upload.
- A kid can delete their own channel (it asks first). Videos they
  uploaded to it stay in the library with no channel, for the parent.
  Renaming to a name already in use says so.
- An upload still waiting for the parent can be taken back ("Don't
  send"): the video and its files are deleted.
- Stop while an upload is sending: the title and channel stay, so the
  kid can pick another file. Cancel leaves the Upload page.
- Kids change or delete their own comments. A changed comment says
  "edited" (with the time for the parent); a deleted one others replied
  to stays as a placeholder, so their replies remain.

**For parents**
- Video requests: **Remove** drops a request without turning the video
  down, and **Let (name) ask again** takes back a **Not this one**.
  Both can be undone with **Put back** under Recently decided; if the
  kid asks again, the same request waits again.
- A kid's suggested channel name is filled in as the new channel on
  Review & add.
- Downloads can be paused (the partial file is kept, also across a
  restart, and Resume carries on) or stopped (its files go; the video
  stays listed with Download again), one at a time, ticked together, or
  all at once from the Content page.
- Moving the videos to a USB drive, from one drive to another, or back
  to the SD card shows how much is copied and can be cancelled while it
  copies. A cancelled or failed copy is deleted and nothing is switched;
  a drive holding a copy that never finished gets a fresh copy instead
  of being used as it is. The Settings move (installs without a fixed
  data folder) copies and checks before deleting the old folder.
- Per kid, requests for videos from the YouTube channels on the allowed
  list can be added without review, into a channel the parent picks
  (Kids → Edit). They still pass every other rule, re-checked against
  YouTube's full details. **Remove from library** next to "Added
  automatically" deletes the video and turns the request down.

## v0.6.0

The first release since v0.1.0. Boxes on v0.1.0 can't install it from
the dashboard (that release only updates the downloader): flash the new
image. Later releases install from **Box → Updates**.

### Family use: search, stats and phone screens

**For kids**
- Search finds a video by the kid's own title, description and tags,
  ignores accents ("vulcao" finds "Vulcão") and shows the kid's title.
- On a phone the menu is one short row, so videos start near the top.
- Channels with nothing to watch yet are not shown (the kid's own
  channels always are).
- Comment threads have one Reply button that opens the box; the count
  includes replies.
- File pickers, the reaction hint ("One per video") and the profile
  tiles are in the kid's language and line up.
- The upload page lists what the kid sent: waiting, ready to watch, or
  not kept. Re-sending a video the box already has says so.
- Find new on a phone: the search box and button share a row and the
  pills are smaller, so results start higher; a channel page offers
  More while the channel has more. A video YouTube won't serve is
  marked "Can't get this one".
- The Big & clear menu wraps to two rows on phones; the picture PIN
  pad is bigger on tablets.

**For parents**
- Approving a request returns to Video requests. Every Add page asks
  for a channel, or "none" on purpose, before Add works.
- Approving a kid upload, and creating, saving or deleting a channel,
  say what happened.
- Stats list every kid, and Top Videos includes videos that were only
  opened or reacted to (with an Opened column).
- Content and video pages name the family channel and say "Kids can
  see it", "Hidden from kids" or "Waiting for your OK"; on a phone the
  list is compact and the bulk buttons open once a video is ticked.
- The dashboard shows the kids' avatars, and a kid upload awaiting
  approval as waiting rather than ready.
- Diagnostics shows the clock in the family's time zone; each page has
  its own browser title.
- Adding, saving or deleting a kid, and hiding a video, say what
  happened. The dashboard says which kid uploaded a video and to which
  channel; Video requests show the kid's note and chosen channel.
- Find new's starter list of blocked words covers English, Spanish and
  Portuguese (new boxes). There is no length limit until the parent
  sets one.

**Fixes**
- Finishing a parent upload asked "Leave site?", and answering "Stay"
  left the video unprocessed.
- Freed videos ("Free up files", or freeing up space automatically)
  vanished from every kid's screens; they stay listed and download
  again when opened.
- A kid's Stats page counted other kids' reactions; in Spanish, Search
  and Find new were both "Buscar".

### Running for years: a full drive, a stale clock, power cuts

**New for parents**
- Content sorts by least watched, not watched for longest or largest
  file, with each video's plays, last watch and size; **Free up files**
  deletes the files of the ticked videos and keeps them listed.
- Dashboard warning when the box's own memory card runs low.
- **Settings → Time zone**: where "today" starts in the stats and dates.
- Video requests: **Recently decided** lists the 20 latest decisions; a
  list of more than 100 waiting says how many there are.

**Fixes**
- Downloads, conversions, imports and uploads stop cleanly before the
  videos drive drops under its minimum free space; nightly backups never
  fill the memory card.
- Without internet after a power cut, a signed-in device's page sets the
  box's clock; freeing up space waits for a trusted clock.
- After a power cut mid-conversion, the leftovers are removed and the
  video is converted again.
- Abandoned uploads and import folders are removed after 24 hours idle.
  Thumbnails and the drive backup copy are never left cut off.
- Deleting a video deletes its comments and reactions; deleting a kid
  deletes their pictures. Kid search stays quick in a big library; the
  log file on the memory card leaves out per-request lines.
- Kid help page tiles stay in level rows.

### Import from a drive

**New for parents**
- Import page: browse a drive, pick movies, episodes or home videos, check
  the guessed titles (from file names, posters, Kodi `.nfo` files), find a
  picture and description on Wikipedia/Wikidata (with the age rating),
  TMDB (optional key), YouTube or a linked page, or pick a frame. Episodes
  of a show can go into one channel, in episode order. Copy, or move off
  the drive once imported.
- Every import shows live progress, conversion speed and time left, and
  can be cancelled at any moment; nothing is left half-made and the file
  on the drive is untouched until its import has finished. Running
  imports show on the dashboard.
- Media on any drive: `setup-external-drive.sh --media-only` keeps the
  database on the SD card and puts the videos on an exFAT, NTFS, FAT32
  or ext4 drive without formatting it.
- Pi image: with an Ethernet cable plugged in, the Pi uses only the cable
  and disconnects Wi-Fi; unplugging the cable brings Wi-Fi back. A Wi-Fi
  network that doesn't show up in a scan is tried once as hidden.
- `curatables.local` works behind routers that drop multicast on Wi-Fi:
  the Pi also answers the lookup directly to the device that asked, by
  its hardware address, so it works on the first try after a restart
  even when the router drops broadcasts too.
- Pi image, everything from the dashboard: a new **Box** section changes
  the Linux login password and SSH, switches Wi-Fi (testing the new
  network first and going back if it fails), and restarts Curatables,
  the network or the box.
- Two-step first-run setup on the Pi image: after the parent password,
  a different password for the box's Linux login. The image now ships
  with SSH off. Password managers can generate and save both passwords.
- **Box → Drives & backups**: use a USB drive for the videos, move them
  to another drive or back to the SD card, eject, reconnect, check and
  repair, format as exFAT or ext4, open a drive for importing; a power
  warning; back up now, restore, and keep backups on the videos drive.
  FAT32 drives work, with a 4 GB per-file limit.
- If the videos drive is missing, Curatables still starts: kids see a
  calm "videos are resting" screen and the dashboard says how to
  reconnect, while nothing in the library is reset or evicted.
- **Box → Diagnostics**: health checks with what to do for each, buttons
  for the usual fixes, and a downloadable report with passwords removed.
- SD-card recovery files: `curatables-reset.txt` (forgotten parent
  password) and `curatables-diagnose.txt` (a report written to the card),
  next to `curatables-wifi.txt`, explained in `curatables-README.txt`.
- **Box → Updates**: update Curatables from the newest GitHub release
  without re-flashing, keeping the library; it is prepared while the old
  version runs, and the box goes back by itself if the new one does not
  start (or on **Go back**). Update the downloader and all system
  packages from the same page; Debian security updates now install by
  themselves every day.
- **Shut down** button, so the box can be unplugged safely.
- New [parents' guide](docs/parents-guide.md) for the Pi image, written
  for Windows, Mac and phone users.

**Fixes that also help uploads and downloads**
- Conversions had a one-hour limit a Pi 4 can hit on a movie; they now
  run until done, with progress, stall detection and a clean stop.
- Conversions picked streams on their own: cover art could become the
  video, a commentary track the audio, and a subtitle stream could fail
  the whole conversion. They now map one video and one audio stream.
- Uploaded videos could never have subtitles, and `.srt` files were not
  played at all; every subtitle is now served as WebVTT, with language
  names instead of codes.
- Server-side fetches waited out a broken IPv6 route before trying IPv4
  (15 s per picture on some home networks); they now try IPv4 first and
  connect only to the addresses that were checked.
- First boot: Wi-Fi is saved as a NetworkManager profile and retried
  until the network shows up, with a log on the boot partition.

### Wishlist, languages, picture PIN and hardening

**New for kids and parents**
- Kids can ask for new videos (off by default per kid): a Find new page
  searches YouTube through the server under the parent's rules, and the
  parent reviews requests in a Video requests inbox before anything is
  downloaded.
- Kid screens in English, Portuguese (Brazil) and Spanish, per profile.
- Picture PIN: three taps from nine animals, for a child who doesn't
  read numbers yet.
- QR code of the kid address for setting up a tablet.
- Playlist review shows progress and can add just the watched videos;
  Settings has section links and clearer "keep forever" wording.

**Security**
- Content-Security-Policy (no inline scripts left); logout is a POST
  with a CSRF token.
- Thumbnail fetches refuse home-network addresses.
- Failed parent logins are stored, so restarts don't reset the limit,
  and there is a cap across all addresses.
- First-run setup only works during the first 30 minutes after start.
- The Wi-Fi password is removed from the boot partition once applied.
- Deno is installed from a pinned, checksummed release.

**Fixes and maintenance**
- mypy is clean and blocking in CI; the kid visibility rule is one SQL
  fragment; one apt package list for install.sh and the Pi image.
- Deleting a video during its download left its files behind;
  re-uploading a file into another channel silently returned the old
  video (a kid could also skip review that way); firstboot exited early
  for an open network with no psk line; the wheel was missing the
  migrations and schema.

### Safety, Raspberry Pi robustness and a simpler kid screen

**Child safety**
- Channel restriction is an explicit per-profile flag and fails closed:
  deleting a restricted kid's channels no longer unrestricts them; search
  never falls back to the whole library; anonymous visitors see nothing;
  subtitles and channel art check the viewer. One visibility policy
  (`ContentService.visible_channel_ids`) serves every list, search and
  media route.
- Picking a kid profile ends any parent sign-in on that device; changing
  the parent password signs out every other device.
- Kid uploads into shared channels wait for parent approval
  (dashboard: "waiting for your OK").
- Videos re-downloaded after cache eviction go back to review if their
  length changed.
- PIN and password guessing are rate-limited; PINs are stored hashed;
  parent passwords need 8+ characters.
- The root yt-dlp updater touches the data dir only as the service user.
- Fixed stored XSS in parent delete confirmations, CSS injection via
  channel colour, unrestricted image uploads and `file://` thumbnail
  fetches.

**Raspberry Pi + external drive**
- Downloads go through a queue (one at a time by default) with a size
  limit; blocking work moved off the event loop; request-size limits run
  before anything reads the body.
- Cache eviction is least-recently-watched, and frees space when the disk
  is full; thumbnails survive eviction.
- `install.sh --systemd` is a complete permanent install (the shipped
  unit used to run the wrong Python); nightly backups of the database
  and `config.json` work out of the box (they needed a `sqlite3` binary
  that was never installed).
- `scripts/setup-external-drive.sh` moves the library onto a USB drive;
  Curatables refuses to start without the drive instead of opening a
  fresh install, and crash recovery won't reset the library when the
  drive is missing.

**Kid screen**
- Per-child switches for reactions, comments, tags, customizing, uploads
  and automatic subtitles; new profiles start with "watch + react".
- On-screen PIN pad, "Again / Home" card when a video ends, no
  download/cast/picture-in-picture, pictures instead of text for empty
  and error pages, current page marked in the nav, reaction emoji on
  cards, friendly dates, higher contrast, focus ring, reduced-motion
  support and a new "Big & clear" theme.

**Parent dashboard**
- Getting-started checklist, content filters (waiting / downloading /
  failed / hidden), Retry for failed downloads with plain-language
  advice, no more 5-second page reloads, a tidier nav that works on
  phones, working upload progress, and Approve buttons for kid uploads.

**Docs and tooling**
- New: [safety model](docs/safety-model.md), [FAQ](docs/faq.md),
  [configuration reference](docs/configuration.md); install, backup and
  upgrade guides rewritten; design and history docs moved under
  `docs/design/` and `docs/history/`.
- CI lints with ruff and shellcheck and tests Python 3.13.
- Migrations 0004–0007: `profiles.channels_restricted`, hashed PINs,
  `videos.last_played_at`, per-profile feature switches.

## Appliance image & no-terminal updates

- **In-app yt-dlp update:** a **Settings → Updates → Update yt-dlp** button
  updates the downloader from the dashboard with no terminal. The sandboxed
  app (`ProtectSystem=strict`, `NoNewPrivileges`) can't pip-install or
  restart itself, so it drops a request flag in the data dir and a new
  root-owned `curatables-updater.path`/`.service` does the pip-upgrade +
  restart, writing a result the dashboard reads back
  (`app/services/updates.py`, `scripts/updater.sh`). yt-dlp only by design.
  Docs: [docs/upgrade.md](docs/upgrade.md).
- **Pre-built Raspberry Pi appliance image:** `pi-gen/build.sh` builds a
  flashable arm64 (Raspberry Pi OS Lite, trixie) image with Curatables, its
  venv, ffmpeg, and Deno pre-installed and all systemd units enabled. No
  baked passwords (OS login set at flash time; parent password set on first
  visit via `/parent/setup`). Networking with no terminal: Ethernet DHCPs
  automatically; Wi-Fi via an editable `curatables-wifi.txt` on the FAT boot
  partition, applied each boot by `curatables-firstboot.service`. Build runs
  in Docker via qemu; a `build-image` GitHub Actions workflow produces the
  `.img.xz` on release. Docs: [pi-gen/README.md](pi-gen/README.md).

## Playback & GUI delivery

- **Playback baseline:** every downloaded and uploaded video is normalized
  on ingest to H.264/AAC, ≤720p30, MP4 `+faststart`, so it plays on old
  devices (Safari/iOS 9, ~2015 tablets) even when the source is VP9/AV1 or
  Opus. The yt-dlp format string prefers `avc1`+`mp4a`; only non-baseline
  pulls pay for a transcode (`app/services/normalize.py`). Design +
  verification: [docs/history/ui-and-playback-plan.md](docs/history/ui-and-playback-plan.md).
- **GUI refresh within the iOS-9 baseline:** inline styles consolidated into
  a utility + token CSS layer (single-sourced teal accent), system font
  stack, fixed-aspect thumbnails (no layout shift), ≥44px touch targets,
  zero-JS kid pages except `watch`/`upload`, CSS-grid + responsive parent
  nav behind `@supports`/`@media` (float/iOS-9 fallback intact).
- **Watch page is reload-free:** reactions, logging, subtitle load, tag
  add/remove and comment/reply posting all update in place via tiny ES3
  XHR (a full reload would restart the playing video); each falls back to
  a plain `<form>` POST when JS is off. Fixed an intermittent 403 on the
  kid multipart `/upload` (the CSRF token extractor truncated tokens at a
  `-`).

## v0.5 — Shared Curation

- Export a parent channel as `.ytc` (JSON), plain text, or PDF from
  `/parent/channels/`; import at `/parent/channels/import` (file or paste).
- Imported URLs always flow through the normal review/preview flow and are
  re-fetched from source (file hints ignored to prevent injection); PDF
  export degrades gracefully without `reportlab`.

## v0.4 — Networking, Discovery & Source Coverage

- mDNS / Zeroconf advertisement (`_http._tcp.local.`) → reachable at
  `http://curatables.local/` (`app/services/mdns.py` + systemd unit).
- Multi-source support via yt-dlp (~1,800 sites) with a Tier 1 iframe
  embed allow-list (YouTube, Vimeo, Dailymotion, PeerTube, TED) and a
  composite `{extractor}_{raw_id}` video_id (`video_source.py`,
  `embeds.py`, `ids.py`).

## v0.3 — Storage, Uploads & Caching

- Disk quota guard (configurable minimum free space) + storage report at
  `/parent/storage` with per-channel breakdown and a live free-space chip.
- Parent uploads via resumable tus.io protocol with content-hash dedup and
  an ffmpeg codec allow-list; kid uploads at `/upload` (plain XHR, iOS-9
  compatible) with a smaller ceiling.
- Kid-created channels owned by the creating profile (`owner_profile_id`),
  sibling visibility isolation, parent "(by X)" badge and friendly event
  labels in `/parent/stats`.
- Web file management: bulk video ops on `/parent/content`, file-clean
  deletion (removes on-disk files + thumbnails), channel delete with
  reassignment, parent adoption of kid channels, data-directory relocation.
- Kid library personalization: per-kid title/thumbnail overrides, personal
  tags + tag cloud, kid channel art (banner/icon/color/description),
  per-kid channel bookmarks (one video, many channels, no file copies).
- Parent stats overhaul: summary dashboard (Today / 7 Days / All Time),
  KPI tiles, Top Videos + Per-Kid tables, per-kid and per-video drill-downs,
  per-video moderation.
- Cache lifecycle: background sweep evicts cache-mode files older than
  `cache_days` and re-downloads on demand; per-video "library" toggle pins
  favourites; uploads are never evicted.
- Plumbing: forward-only schema migrator (`app/db/migrator.py` +
  `app/db/migrations/`), CSRF tokens (SameSite=Strict), request-id + body-
  size middleware, timing-safe password compare, rate-limited comments,
  backup/restore scripts + systemd timer ([docs/backup.md](docs/backup.md)).

## v0.2 — Child Profiles & Theming

- Multiple child profiles with PIN selection, display name, per-profile
  settings, and per-profile channel restrictions.
- Themes (base, playful, calm) via CSS custom-property overlays.
- Curated per-profile search, emoji reactions (love/funny/cool/wow/learned/
  boring), threaded parent-moderated family comments.
- Parent channel and profile management (CRUD) with comment moderation.

## v0.1 — MVP

- First-run setup wizard (set parent password).
- Two-step add flow (fetch metadata → preview/edit → confirm), internal
  channels, background downloading with status tracking, subtitle download.
- Kid UI: responsive video grid, player with subtitle tracks, server-
  proxied streaming (kid device never contacts the source).
- Thumbnail caching, usage event logging, duplicate protection, pagination,
  configurable storage path / cache duration / resolution / subtitles.
