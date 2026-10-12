# Release acceptance: 0.5.0 on a real box

The last check before 0.5.0 is published. One family uses the box the
way real families will, only through the browser: a parent sets it up,
looks after it for a few "weeks", and two children use it every day.
Nothing is checked from code or the command line; the Python test suite
covers the code and its logic. This run follows
[release-0.5.0-ux.md](release-0.5.0-ux.md) (the first round, kid and
parent screens) and adds everything a parent does to look after the box.

## When we can release

0.5.0 can be published when, after the run:

1. every **Must** scenario below passed;
2. no **Blocker** or **Major** finding is open (fixed and re-checked on
   the box, or shown not to be a bug);
3. the box's log for the whole run has no errors or tracebacks that the
   run did not cause on purpose;
4. after the restarts (A10, A11) everything the family made is still
   there and works: kids, PINs, channels, videos, requests, comments,
   settings.

**Minor** findings may ship if they are written down under Open with a
reason.

| Severity | Meaning |
|----------|---------|
| Blocker | A kid sees or reaches something the parent did not allow; the family's data is lost or damaged; the box cannot be reached or does not come back; a password or other secret shows up where it should not |
| Major | Something a family needs does not work and there is no obvious way around it; a misleading message that would make a parent do the wrong thing |
| Minor | Wording, layout, slowness that does not get in the way |

## The box and the family

- **Box:** the test Pi, wired, running HEAD of `main` (all 396 shipped
  files checked against the commit before the run), with `yt-dlp-ejs`
  installed. No USB drive is plugged in. Data left from the first round
  is still on it.
- **Sam (parent):** laptop 1280×800 and phone 390×844.
- **Mia, 9:** tablet 810×1080. Picture PIN, English, Find new on, may
  comment, react, tag, customise and upload.
- **Leo, 5:** small tablet 600×960. No PIN, Português (Brasil), Big &
  clear theme, only the Songs channel, Find new off, reactions only.
- **Lucía, 7 (new this round):** phone 390×844. Number PIN, Español,
  Calm theme, may search the videos she can see, nothing else.

Each person has their own browser context (own cookies), like separate
devices. All names are made up.

## Scenarios

"Passes when" is what a person would notice. Every page visited is also
checked for console errors, sideways scrolling at that device's width,
and load time (scenario X1).

### Week 1: starting over and setting up

| # | Must | What happens | Passes when |
|---|------|--------------|-------------|
| W1 | Must | Sam signs in (wrong password first) and decides to start over: deletes the old kids, channels and videos | each delete asks first and says what will go; Storage shows the space freed; kids' screens show nobody |
| W2 | Must | Sam goes through Settings: kid language, resolution, limits, blocked words, session time; saves each section | each save says it worked and the values are still there on reload |
| W3 | Must | Sam makes channels Songs, Animals and Stories, adds videos by YouTube link (one video, then a short playlist), names them and picks pictures | previews show title and picture; downloads start and finish; videos appear in the right channel |
| W4 | Must | Sam uploads a video from the laptop into Stories | progress shows; the video plays for a kid allowed Stories |
| W5 | Must | Sam makes Mia, Leo and Lucía as above and opens the kid address / QR on each device | each kid sees only what was set; languages and themes as set |

### Week 1: the children

| # | Must | What happens | Passes when |
|---|------|--------------|-------------|
| K1 | Must | Mia: picture PIN wrong then right, browses, plays a video to the end, reacts, comments, adds a tag | the video plays; reaction, comment and tag stay after a reload |
| K2 | Must | Mia: Find new, searches, sorts, More, opens a channel, taps very fast, asks for two videos, names one | results with pictures; the playful slow-down on fast taps; "Asked!"; both under What I asked for |
| K3 | Must | Mia: uploads a short video from her tablet into her own new channel, renames a video and gives it a picture | upload finishes; her channel and changes show for her |
| K4 | Must | Leo: Portuguese screens; plays a Songs video; tries Find new, a parent page and an Animals video by typing its address | Portuguese labels; Find new, parent pages and the Animals video refused kindly |
| K5 | Must | Lucía: number PIN wrong 3+ times, then right; Spanish screens; searches for a video she can see and for one she cannot | the box slows wrong PINs down; she finds only what she may see; no comment, tag or upload buttons |
| K6 | Should | A kid leaves a video half-way and comes back later | it offers to carry on where it stopped |

### Week 2: the parent keeps an eye on things

| # | Must | What happens | Passes when |
|---|------|--------------|-------------|
| P1 | Must | Sam on the phone: sees Mia's requests, Review & add one (timed), turns the other down | Review opens fast with Mia's name/channel; download starts; Mia sees "Yes! Watch" and "Not this time" |
| P2 | Must | Sam reads Mia's comment, replies, deletes another; checks what each kid watched (Stats) | reply shows for Mia; deleted comment gone; stats name the videos |
| P3 | Must | Sam edits a video (title, picture; tags are each kid's own), hides one, brings it back, moves one to "(none)", deletes one; uses bulk actions | each kid's screen follows within a reload; the "(none)" warning shows |
| P4 | Must | Sam changes Leo to also see Animals, then to "none of yours"; turns Mia's Find new off | kids' screens follow; Mia's Find new disappears |
| P5 | Must | Sam shares the Songs channel (exports .ytc and the printable list) and imports the .ytc as a new channel | files download; the import preview lists the videos; the new channel fills |
| P6 | Should | Sam opens Import (files on a drive) with no drive plugged in | the page explains what to do; nothing breaks |
| P7 | Must | Sam signs out on the laptop; the phone session is unaffected; a kid device cannot get to the parent pages | sign-out works; kids still blocked |

### Week 3: looking after the box

| # | Must | What happens | Passes when |
|---|------|--------------|-------------|
| A1 | Must | Diagnostics: reads the checks, downloads the report | every check explains itself; the report has no passwords |
| A2 | Must | Updates: checks for a Curatables update; presses Update the downloader | the Curatables check says clearly there is nothing newer (or why it could not check); the downloader update finishes and the box comes back by itself |
| A3 | Must | Updates: installs system updates | the job shows progress and finishes; says whether a restart is needed |
| A4 | Must | Changes the parent password (wrong current one first), signs in with the new one, changes it back | wrong current password refused; new one works; old one refused until changed back |
| A5 | Must | Box password (for SSH): tries a too-short one, a mismatch and the parent password, then changes it and changes it back | each mistake refused with a clear reason; the change works (checked by an SSH login) and is reverted |
| A6 | Must | Turns SSH logins off and on again | the page says so each time; SSH refused while off, works again after |
| A7 | Must | Drives and backups with no drive: makes a backup now, restores that backup | the backup is listed; restore warns first, finishes, and the family's data is all there |
| A8 | Should | Storage page after all of the above | numbers make sense; "Freeing up space" is 0 when nothing is waiting |
| A9 | Must | Help: reads it from each section it links from | links work; it matches what the screens say |
| A10 | Must | Restarts Curatables from the Box page while a download is running and a kid is watching | the page comes back by itself; the download finishes or says it will retry; the kid's screen recovers on reload |
| A11 | Must | Restarts the box (reboot) | the page comes back by itself in about two minutes; `curatables.local` works again; all data still there (see "When we can release", 4); kids' devices are still signed in to their profiles or ask for their PIN again |

### Throughout

| # | Must | What is checked | Passes when |
|---|------|-----------------|-------------|
| X1 | Must | Every page above on its person's device | no console errors; no sideways scrolling; pages load in under ~2 s except searches, lookups and long jobs, which show that they are working |
| X2 | Must | The box's log for the whole run | no unexplained errors or tracebacks; each request logged once |
| X3 | Must | Wording a parent reads before an action that cannot be undone | says what will happen and asks first |

## Not covered

Each is left out on purpose, with what still covers it:

- **Shut down, Wi-Fi changes, network restart:** the box might not come
  back without someone there. The Python tests cover the logic only;
  these have not been tried on hardware.
- **USB drives (use, eject, format, move data):** no drive is plugged
  in. A7 covers the drive page with no drive.
- **First-run setup on a new card:** needs a fresh flash; done in the
  first round with the image of `09934b3`.
- **Rolling back a Curatables update:** needs a real update to roll back
  from; nothing newer than 0.5.0 is published.
- **Real touch screens and video codecs on old tablets.**

## Results

Run on 2026-10-11, 15:19–16:30 box time, against `main` from `27712b2`
onwards: each fix below was committed, deployed to the box and checked
again before the run went on. Same box, wired; the test laptop on Wi-Fi.

### Verdict

**Not ready to publish yet, for two reasons outside the app code:**

1. The backup fix (F12) is only half on the box: the scripts are, but
   the systemd unit in `/etc/systemd/system` is still the image's. It
   reaches boxes with the next image (or an update that runs
   `apply-system.sh`); a new image is needed before release.
2. X2 is only partly checked: the box keeps its journal in memory, so the
   reboot cleared it, and the diagnostics report carries only the last
   300 lines of the app log. 15:20–15:31, around 15:45 and 16:18–16:30
   were read (no errors other than the ones under F4); about 15:50–16:15
   were not.

Everything else passed, after the fixes below.

| # | Result | Notes |
|---|--------|-------|
| W1 | Pass | Each delete asks first; Storage 0.00 GB after; the kid screen says there are no kids yet. |
| W2 | Pass after F1 | General, request rules, imports and Advanced each say they saved and keep their values. |
| W3 | Pass after F2–F5 | One video: preview 11–17 s; a 37-video playlist: 6 s. Add stays disabled until "I have already fully watched" is ticked; the summary counts what was skipped. |
| W4 | Pass | 4.7 MB upload with progress in 8.5 s; plays at 1280×720 for Mia. |
| W5 | Pass after F6, F7 | The Kids list reads as set; QR on the Kids page. |
| K1 | Pass | Picture PIN wrong then right; the video plays to the end (Again / Home); reaction, comment and tag survive a reload. |
| K2 | Pass | 12 results with pictures in 7 s; "Looking…" for Search and More; More is a second page with Back; two requests, one named, back on the results with "⌛ Asked". |
| K3 | Pass | Mia's own channel, a 2.3 MB upload in 3 s, a new title and picture on her home. |
| K4 | Pass | Portuguese, larger text; Find new, uploads, a parent page, an Animals video and Mia's video all refused (sent home or to the parent sign-in; the file itself 404). |
| K5 | Pass | Five wrong PINs lock the profile for 5 minutes ("Demasiados intentos"), even for the right PIN; then in; only Animals and Songs; no comment, reaction, tag or upload controls. |
| K6 | Pass after F14 | Added after the run (`958c3d5`): stopped at 32 s, back later, Seguir viendo goes on from 32 s; Desde el principio starts at 0 and forgets the spot; finishing the video forgets it too. Checked at 320, 390 and 820 px. |
| P1 | Pass | On the phone; Review 2.5 s with Mia's name and channel; "Yes! Watch" at 1280×720; the turn-down asks "Hide this video from Mia's searches?". |
| P2 | Pass after F8 | Reply reaches Mia; deleting asks and removes it for both. |
| P3 | Pass | Title and picture changes reach the kids (pictures are revalidated); hide, bring back, "(none)" with its warning, delete, bulk hide/unhide/move. |
| P4 | Pass | Leo + Animals, then "None of yours" (kind Portuguese empty screen); Mia's Find new off removes the button and the page. |
| P5 | Pass after F9, F10 | .ytc, .txt and .pdf export correctly; importing a pasted list into a chosen channel. |
| P6 | Pass after F11 | No drive: the page explains; it pointed to a command line, now to Box → Drives. |
| P7 | Pass | Signing out on the laptop leaves the phone signed in; kids get the parent sign-in. |
| A1 | Pass after F12 | All checks explain themselves; the report (1,000 lines) holds none of the four passwords used in the run and says it names networks. |
| A2 | Pass after F13 | "0.5.0 is the newest version"; the downloader update came back by itself in 12 s. |
| A3 | Pass | `apt` ran to the end in under a minute ("0 upgraded": the daily security updates had already run). |
| A4 | Pass | Wrong current password and a mismatch refused; the change signs other devices out; changed back. |
| A5 | Pass | Too short (browser), mismatch, same as the parent password, wrong parent password: each refused; a fresh SSH login took the new password and refused the old; changed back and checked again. |
| A6 | Pass | SSH off: "Connection refused"; on: logs in. |
| A7 | Pass after F12 | Back up now, then restoring it: 9 s, every kid, channel and video as before, the parent still signed in. The empty file the failed nightly run had left was refused. |
| A8 | Pass | Per-channel sizes add up; 0.12 GB for 11 videos. |
| A9 | Pass | No broken links; matches the screens (its list of kid switches leaves out Ask for new videos, which has its own section). |
| A10 | Pass | Back by itself in 13 s; the kid's video kept playing; the cut download was reset and finished ("Resumed 1 pending download(s) after restart"). |
| A11 | Pass | A real reboot (uptime checked), back by itself in 42 s; `curatables.local` answers; kids, channels, videos, requests, comments and settings identical; each kid device still in its profile; Diagnostics all green. |
| X1 | Pass | No script errors on any device; every console error was expected (deliberate 404/400s, the wait pages polling during restarts); no sideways scrolling at 1280, 810, 600 or 390 px; parent pages 0.25–1.2 s. |
| X2 | Partly checked | See the verdict. Each request is logged once. |
| X3 | Pass | Every delete, restore, restart and system update asks first and says what happens; SSH and password changes need the parent password instead. |

### Found and fixed

| | Severity | Finding | Fix |
|-|----------|---------|-----|
| F12 | Blocker | The nightly backup failed after a boot (`ProtectHome=read-only`; SQLite has to write the `-shm` file to read a WAL database) and left an empty `curatables-*.db`, listed as a backup; an empty file passes `PRAGMA integrity_check`, so restoring it would have replaced the family's data with nothing | The unit may write the db folder; the snapshot is renamed into place only when complete; restore needs the profiles table (`08127dd`); Diagnostics says to press Back up now (`acb9aaf`) |
| F6 | Major | A new kid's chosen channels were never saved on the box (written after the commit); the kid saw none of the parent's channels | `d262980`, with a test that reads through a second connection |
| F1 | Major | Advanced settings could not be saved on a new box: the kid upload limit showed as 0.49 in a field that takes steps of 0.1, and the browser refused the form without a visible word | `0e88aa7` |
| F4 | Major | With subtitles en,pt,es, a 429 on one subtitle track failed the whole video (2 of 4) | `979f4b9` |
| F5 | Major | 429 was shown as "site blocked, update yt-dlp or turn on impersonation" | `5d02c76` |
| F10 | Major | The import page's target channel was ignored; imported videos had no channel | `286810d` |
| F2 | Minor | Looking up a link (6–17 s) showed nothing on screen | `bd614c2` |
| F3 | Minor | A gone video in a playlist was listed as "None" | `76dea7f` |
| F7 | Minor | With "None of yours" selected, a ticked channel was dropped | `8c7784a` |
| F8 | Minor | Stats showed video codes instead of names | `e1cf383` |
| F9 | Minor | "Added 0 video(s). Downloading…" when everything was a duplicate | `6af9fc8` |
| F11 | Minor | Command-line advice (sudo, install.sh, docs paths) on the box's pages | `9432293` |
| F13 | Minor | Box → Updates said the result was on the page; it was not | `aa715ac` |
| F14 | Minor | On a phone, the end-of-video buttons stacked past the bottom of the video and over its title (found while checking K6) | `366ed43` |
| – | Minor | Empty Downloading/Failed tab said "No videos yet"; deleting a kid did not say what goes; "Restart the server"; a "Family" channel that did not exist | `74b30b1`, `23a6240` |

### Second run: the new image

On 2026-10-12, 00:43–01:20 box time (UTC), on a box flashed with the
image built from `3d2498c`. The image was first checked in a chroot:
all 398 shipped files match the commit, the ten systemd units match the
repo, and the backup unit has `ReadWritePaths` for the db folder. The
SD card was read back and matched the image byte for byte. The parent
did setup from scratch; then the same family (with Mia on a landscape
tablet, Leo on a phone and Lucía on a portrait tablet) used the box,
and the screenshots for the [picture guides](../guide/parents.md) were
taken along the way.

| Area | Result |
|------|--------|
| Setup, channels, four videos added (lookups about 3 s each), three kids | Pass |
| Mia: picture PIN, home, watch, reaction, comment, tag, carry on, end card, search, Find new, ask with a name and channel, upload to her own channel, customize, Help | Pass after F16, F18 |
| Leo (phone, Big & clear, Portuguese) sees only Cartoons; Lucía (wrong then right number PIN, Spanish, Calm) sees everything but Mia's channel; search matches from the start of a word | Pass |
| Sam on the phone: the request at the top of the dashboard, Review & add in 0.3 s with Mia's choices, reply to Mia's comment, stats | Pass after F17, F20 |
| Diagnostics and report: no password in it; the week's warnings section works | Pass after F15, F19 |
| Back up now (1.1 s), a change, Restore: the change is gone, everything else back, nobody signed out | Pass |
| Restart the box from Box: back by itself in 46 s; every device still in its profile; all nine checks green | Pass |
| No sideways scrolling: 20 parent pages at 390 px, 15 kid pages at 360–390 px | Pass after F20 |
| Log (X2): the system journal from boot to before the reboot, and the app log, read in full | Pass: no errors; two subtitle 429 warnings, handled as F4 intended |

Two things this run could not settle:

- **The nightly backup unit itself** has not run on this box yet: the
  timer's first run is 03:00 UTC, and starting the unit by hand with the
  app stopped (the case that failed in F12) was not allowed from the test
  laptop. Its fix is in the image and checked there.
- **An unexplained power cut** before setup: the app log has a run of zero
  bytes after 23:49, the kernel ran orphan cleanup on the root file
  system at the next boot, and the boot reason is power-on
  (`rsts=0x1000`), not a watchdog or a crash. Most likely the box was
  unplugged; the parent has been asked.

| | Severity | Finding | Fix |
|-|----------|---------|-----|
| F15 | Minor | Right after setup, Diagnostics warned "System services: Stopped with an error: userconfig.service": setup stops Raspberry Pi OS's first-boot dialog, and systemd marks a stopped dialog as failed | `1b24c60` |
| F16 | Minor | A long description stopped mid-sentence ("…with improving a free and open") on the kid's watch page: the Add form cut it at exactly 500 characters | `909ea04` |
| F17 | Minor | The dashboard's Recently added said "no channel" for Mia's upload, which was in her own channel | `1d1faeb` |
| F18 | Minor | The kids' Help page did not mention Tags, Find new or Carry on | `00e3164` |
| F19 | Minor | After the power cut, the diagnostics report carried the zero bytes into the text file | `bb0a8c7` |
| F20 | Minor | On a phone, the Stats tables were about 410 px wide and the page scrolled sideways | `cded92a` |

F16–F18 and F20 were deployed and checked on the box. F15 and F19 are in
the helper, which the deploy script does not restart; they are covered
by tests and reach the box with the next image.

### Verdict after the second run

**Ready to publish once the release image is built from the final
commit**, a chroot check of it passes, and the first nightly backup on
this box (03:00 UTC) is seen to work. The F12 blocker is fixed in the
image, every Must passed, the log of the run is clean, and the six new
findings are Minor and fixed.

### Third pass: regression and polish

Same box and family, 2026-10-12 01:30–02:10 UTC, after a deploy of every
commit since the image. Each fix was checked again in the browser on the
box (or, where noted, only by tests), and the first round's open
kid-screen items were done.

| Check | Result |
|-------|--------|
| F1 Advanced settings save (and set back) | Pass |
| F2 Add says "Looking it up…" and disables the button while it looks | Pass |
| F7 ticking a channel picks "Only the ones ticked here" | Pass |
| F9, F10 a shared list goes into the chosen new channel; a duplicate is skipped and said so | Pass, then F28 |
| F11 Import with no drive explains what to do | Pass |
| F19 the report has no zero bytes, with the power cut's still in the log | Pass |
| F15 no failed service after setup | Tests only: it needs a new setup |
| Mia uploads to a channel that is not hers: it waits for the parent; approved, Leo sees it | Pass |
| A kid's own title (Customize) shows only to that kid | Pass |
| Console errors on 25 parent and 12 kid pages | None, other than the deliberate 404s |
| The log of this boot | No warnings or errors |
| Restart the box from Box again | Back in 43 s; checks green; devices still in their profiles |

| | Severity | Finding | Fix |
|-|----------|---------|-----|
| F30 | Major | The kids' error page (old or mistyped link, a crash on the kid side) showed raw template code: `{% include "_icons.html" %} 🙈 {{ _("Oops! …") }}` | Rendered, in the kid's language (`6e0b7ab`) |
| F26 | Major | With a cookies file the service cannot write (for example on the SD card's boot partition), every lookup and download failed: yt-dlp writes cookies back when it closes | A private copy in the cache folder (`e541133`); checked with a real YouTube lookup |
| F21 | Minor | A kid with one channel got "All" and that channel as buttons showing the same videos | `ebd6bed` |
| F22 | Minor | Big & clear kept three 180 px cards a row on a 600 px tablet | Two a row up to 900 px (`dd7ceac`, `e5babfc`) |
| F23 | Minor | After Send on Find new, the kid was back at the top of the results | Back at the card (`6ad3012`) |
| F24 | Minor | Decided requests had no link to the video or to YouTube | `d6cde8f` |
| F25 | Minor | The picture PIN stayed on screen as it was tapped | Each picture shows for 0.9 s (`ade693a`) |
| F27 | Minor | A parent 404 said "An unexpected error occurred. The details have been logged." | Its own words (`f970606`) |
| F28 | Minor | Review & Add offered videos the library already has, under YouTube's title | Listed as left out (`ded0183`) |
| F29 | Minor | `/favicon.ico` was a 404 on pages without the icon link | `12be8a5` |

The FAQ now explains why a few YouTube videos come in 360p and how to
use a cookies file from a spare account (`6fdb8e5`).

### Verdict after the third pass

**Ready for the release image.** Every finding from the three runs is
fixed and checked again on the box, except F15 (tests only: it needs a
new setup) and F26 (checked on the laptop with a real YouTube lookup;
putting a cookies file on the box was not part of the run). What is left for the
image built from the final commit: a chroot check, then on the box the
first nightly backup, and Diagnostics right after setup (F15).

### Open

- **The journal is kept in memory** (on purpose: it spares the SD card),
  so a reboot or power cut loses the system log. The app's own log
  survives, and the report carries its warnings and errors of the last
  7 days (`59b2957`).
- **Kid requests:** when the kid leaves "What is it about?" empty, the
  review form still fills in YouTube's description, and says so and asks
  the parent to read it first (`73415fd`).
- **Carry on watching** is kept on each device, so it does not follow a
  kid to another device.
- **Higher quality for every video:** the solver-only setup stays; the
  FAQ explains the cookies option and its risks.
