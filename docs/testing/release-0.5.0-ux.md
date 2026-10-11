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
test laptop on Wi-Fi). Fixes made after the run are in commits `bedf0cf`..`04a1201`; the
app-code ones were deployed to the box afterwards and re-checked (below).
`yt-dlp-ejs` is not on the box yet: it needs a new image or an update.

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
| F1 | Ask took 12–15 s on the box while yt-dlp fetched full details | Recorded at once from the search result; details fetched afterwards, and the request is turned down if they break the rules (`98abaa8`); Review fetches them itself if that lookup failed (`eebceca`). Re-checked on the box: Ask 108 ms on the server, review 34 ms |
| F2 | "What is it about?" showed the kid YouTube's description (an advert with links) | Kid's box starts empty; the parent sees YouTube's text (`bedf0cf`) |
| F3 | Finished requests under What I asked for lost their names | Fixed (`bedf0cf`) |
| F4 | yt-dlp: "n challenge solving failed, formats may be missing"; downloads came out at 360p | `yt-dlp-ejs` installed and upgraded with yt-dlp (`66ce992`); with it a test video offered H.264 up to 1080p on the box. Needs a new image or an app update there |
| F6 | yt-dlp and deno could not cache anything (the service's home is read-only), so every lookup fetched YouTube's player code again | Both cache in `<data>/cache` (`04a1201`) |
| F5 | A cut-off link said "An unexpected error occurred" | Plain message (`9799861`) |

### Re-checked on the box after deploying the fixes

Ask: 108 ms on the server (under 1 s end to end); the details arrived in
the background and Review then opened in 34 ms. "What is it about?"
started empty; finished requests showed their names.

Switching yt-dlp to its default YouTube clients first made every lookup
fail on the box, which had no challenge solver yet, so it was reverted.
After `yt-dlp-ejs` 0.8.0 (the version yt-dlp 2026.08.19 expects) was
installed there, with an empty cache:

| Video | default clients | `player_client=all` |
|-------|-----------------|---------------------|
| the one that came out at 360p | OK, 10.0 s, H.264 up to 720p | OK, 17.6 s, only 360p |
| a second one | OK, 10.5 s, up to 1080p | OK, 17.3 s, up to 1080p |

So the defaults are back (`13af8c0`). End to end on the box: Ask 0.67 s,
Review 116 ms, and the added video downloaded at 1280×720.

### Follow-up fixes

- Log each request once (`9a060f9`).
- A maple-leaf app icon for tabs and tablet home screens (`7d00a1f`).
- Empty kid screens offer Find new when the kid may use it (`a125cf4`).
- Clearer Kids list, a three-way "Which channels can they see?",
  channel cards without "Position: 0" and with delete behind a button
  (`48805d4`, `0905c82`, `5f4ce61`).
- The thanks to yt-dlp on the dashboard is a card with a Sponsor button (`83ad5cc`).
- The picture PIN showing as it's tapped is accepted as is: it isn't a
  banking PIN, and parents can sort it out between siblings.

### Open (decide or do later)

- **Higher quality for every video:** with the solver most videos come
  in HD; a few may still be limited to 360p unless YouTube gets a PO
  token. Options: keep the
  solver only, add a PO-token provider (no personal data, a new
  component), or a cookie file from a YouTube account (personal data).
- **Kid screens:** after Send the kid is back at the top of the results;
  the Big & clear theme keeps small cards on a 600 px tablet; a single
  channel still shows the channel chips.
- **Requests page:** decided requests have no YouTube link.
- **Visual guides, once the UI is frozen:** a picture guide for parents and one for kids, and a fresh screenshot for the README. The run above can be re-used to take them (same people, devices and steps).
- **Not covered:** Wi-Fi, USB drives, updates, restarts, a real tablet's
  touch and video codecs.
