# Release test: 0.5.0 UX on a real box

A scripted, real-world-style walk through Curatables before the first
public release: one parent and two children, each on their own device,
against the Raspberry Pi image built from `09934b3`
(`pi-gen/.pi-gen/deploy/image_2026-10-10-curatables.img.xz`), freshly
flashed and set up with its two passwords.

Run with Playwright (one isolated browser context per person, so each has
its own cookies, like separate devices). Results are in
[Results](#results); bugs found are fixed in later commits and listed
there.

## People and devices

All names are made up.

| Who | Device (viewport) | Set up as |
|-----|-------------------|-----------|
| Parent "Sam" | laptop 1280×800, phone 390×844 | the dashboard password |
| "Mia", 9 | tablet 810×1080 | picture PIN, English, Find new on, comments and reactions on |
| "Leo", 5 | small tablet 600×960 | no PIN, Portuguese, large-text theme, only the Songs channel, Find new off |

## Scenarios

| # | Who | What happens | Passes when |
|---|-----|--------------|-------------|
| S1 | Parent | Signs in (wrong password first), looks around the dashboard, Help | wrong password refused with a clear message; dashboard has no errors; Help ends with feedback + thanks; yt-dlp note at the dashboard bottom |
| S2 | Parent | Creates channels Songs and Animals, creates Mia and Leo as above, opens the kid address / QR | profiles saved with the right settings; QR shown |
| S3 | Mia | Picks her picture, gets the PIN wrong, then right; sees an empty home | wrong PIN refused kindly; empty home says what to do |
| S4 | Mia | Find new: searches, sorts, More, Channels, opens a channel, taps very fast | results with pictures; "Looking…" shows; fast taps get the playful slow-down, not an error |
| S5 | Mia | Asks for two videos, names one and picks a channel, sends | "Make it yours" page; back on the same results with "Asked!"; both under What I asked for |
| S6 | Parent | Sees the requests, opens Review & add (timed), adds one, rejects the other | review opens instantly with Mia's name/channel filled in; download starts |
| S7 | Mia | Sees "Yes! Watch", plays the video, reacts, comments; the rejected one says "Not this time" | video plays (time advances); reaction and comment saved |
| S8 | Leo | Uses the kid screen in Portuguese; tries Find new, a parent page and a video from Animals by address | Portuguese labels; no Find new; parent pages and the Animals video refused |
| S9 | Parent | Moves a video to "(none)", checks the warning; checks Settings, Storage, Box pages (view only) | warning shown; kids no longer see it; "Freeing up space" is 0; Box pages load |
| S10 | All | Every page above: browser console, horizontal scroll at the device width, page load time | no console errors; no sideways scrolling; pages load in under ~2 s (searches excepted) |

Out of scope (need hardware hands or would change the box): Wi-Fi
switching, USB drives, formatting, restarts, updates, password changes.

## Results

Run on 2026-10-11 against the freshly flashed box (Pi 4, wired; the
test laptop on Wi-Fi). Fixes made after the run are in commits
`bedf0cf`..`98abaa8`; they were **not yet on the box** during the run.

| # | Result | Notes |
|---|--------|-------|
| S1 | Pass | "Incorrect password." on a wrong one; sign-in takes ~3.5 s (password hashing on the Pi). Help ends with feedback + thanks; yt-dlp note at the dashboard bottom. |
| S2 | Pass | Channels and both kids saved as set; QR on the dashboard and Kids page. |
| S3 | Pass | Wrong picture PIN: "❌ Wrong PIN. Try again."; the third picture submits on its own; empty home says to ask a grown-up. |
| S4 | Pass | 12 results with pictures; "Looking…" shows mid-screen for search, sort and More; Newest, A–Z, More, Channels and opening a channel work; fast taps get the 🐢 slow-down. |
| S5 | Pass, slow | "Make it yours" and back to the same results with "Asked!". **Ask took 15–18 s** (fixed, see F1). |
| S6 | Pass | Review shows Mia's title, description and channel; the box built it in 80 ms (the rest is the YouTube player loading). Added, downloaded in under a minute; turn-down recorded. |
| S7 | Pass | "Yes! Watch"; the video plays, reaction and comment work without a reload; end screen offers Again and Home. |
| S8 | Pass | Portuguese UI, no Find new, only Songs; parent pages, Find new, a sibling's request and the Animals video (page and file) all refused. |
| S9 | Pass | "(none)" warns on the edit page and after saving; the video disappears for both kids. Freeing up space is 0. All 16 parent pages load. |
| S10 | Pass | No console errors on any device (one expected 404 from S8's probe); no sideways scrolling at 1280, 810, 600 or 390 px. Server times 10–100 ms; occasional multi-second waits came from the laptop resolving `curatables.local`, not the box. |

### Found and fixed

| | Finding | Fix |
|-|---------|-----|
| F1 | Ask took 12–15 s on the box while yt-dlp fetched full details | Recorded at once from the search result; details fetched afterwards, request turned down if they break the rules (`98abaa8`). yt-dlp's default YouTube clients instead of `all`: ~17 s → ~10 s per lookup, measured on the box, with fewer requests to YouTube (`bf49013`) |
| F2 | "What is it about?" showed the kid YouTube's description (an advert with links) | Kid's box starts empty; the parent sees YouTube's text (`bedf0cf`) |
| F3 | Finished requests under What I asked for lost their names | Fixed (`bedf0cf`) |
| F4 | yt-dlp: "n challenge solving failed, formats may be missing"; downloads came out at 360p | `yt-dlp-ejs` installed and upgraded with yt-dlp (`66ce992`); with it a test video offered H.264 up to 1080p on the box. Needs a new image or an app update there |
| F5 | A cut-off link said "An unexpected error occurred" | Plain message (`9799861`) |

### Re-checked on the box after deploying the fixes

Ask answered in 108 ms on the box (3.9 s end to end over the laptop's
Wi-Fi), down from 12–15 s; "What is it about?" started empty; finished
requests showed their names. One background lookup got a one-off "video
not available" from YouTube (the same lookup then worked 3 times out of 3),
so Review now fetches the details itself when they are still missing.

### Open (decide or do later)

- **YouTube 360p:** some videos come only as 360p without a PO token
  (yt-dlp skips the other formats). A PO-token provider plugin or
  cookies would fix it; it's a new component, so it's a decision.
- **Picture PIN shown in the clear** as it's tapped: a sibling can read it.
- **Parent wording:** the Kids list says "Search: curated" and not the
  language or Find new; the Visible Channels help text is hard to follow;
  every channel shows its delete options and "Position: 0".
- **Kid screens:** an empty home or "Nothing found" could point to Find
  new when it's on; after Send the kid is back at the top of the results;
  the Big & clear theme keeps small cards on a 600 px tablet; a single
  channel still shows the channel chips.
- **Requests page:** decided requests have no YouTube link.
- **No favicon:** every page asks for `/favicon.ico` and gets a 404.
- **Logs:** every request is logged twice (uvicorn and Curatables), which
  doubles SD-card writes.
- **Not covered:** Wi-Fi, USB drives, updates, restarts, a real tablet's
  touch and video codecs.
