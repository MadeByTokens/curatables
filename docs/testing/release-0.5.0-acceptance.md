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
| P3 | Must | Sam edits a video (title, picture, tags), hides one, brings it back, moves one to "(none)", deletes one; uses bulk actions | each kid's screen follows within a reload; the "(none)" warning shows |
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

Not run yet.
