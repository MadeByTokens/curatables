# How Curatables keeps kids safe

A plain-language description of what a child can and cannot reach, and
what you still have to do on the child's device. The technical version
is [threat-model.md](threat-model.md).

## What the child sees

- **Only videos you approved.** A video reaches the kid screen after you
  watched it all and ticked "I have already fully watched this video".
  Nothing is suggested, nothing autoplays, there is no "up next". When a
  video ends, the child gets two big buttons: **Again** and **Home**.
- **Only the channels you allow.** Each kid profile can be limited to
  some channels. A restricted profile stays restricted even if you
  delete all of its channels: it then shows nothing rather than
  everything.
- **No links out.** The kid pages have no links to YouTube or any other
  site, no embedded players and no ads. Videos play from your own
  server; the child's tablet never talks to YouTube.
- **Videos kids upload into a shared channel wait for you.** They appear
  under **Needs your attention** on the dashboard and stay hidden from
  the other kids until you approve them. Uploads into a kid's own
  channel are visible to that kid straight away.
- **Asking for new videos is off unless you turn it on.** With **Ask
  for new videos** switched on (Kids → Edit), a child gets a **Find
  new** page: they type what they want, the server searches YouTube and
  shows the results that pass your rules (Settings → Video requests:
  words to hide, channels to allow, a maximum length; live streams are
  never shown). The tablet still never talks to YouTube, and nothing
  plays or downloads: tapping **Ask** puts the video in your
  **Video requests** inbox, where you watch it like any other video
  before adding it. **Not this one** hides it from that child's
  searches for good. Be aware that the child sees the titles and
  pictures of the results before you do, and YouTube's own SafeSearch
  is not applied, so for a younger child keep the word list strict or
  allow only channels you trust.
- **Re-downloaded videos are checked.** Every video is kept unless you
  turn on **Freeing up space** in Settings. Then a video
  nobody watched for a while loses its file; it stays on the kids'
  screens, marked "Takes a moment to start", and downloads again the
  next time someone taps it. If the length of the new copy is clearly
  different from the one you approved, it goes back to "waiting for
  your OK" instead of playing. Mark favourites **keep forever** so Freeing up
  space never removes them (a manual **Free up files** on Content still
  can).

## Keeping it simple for your child

On **Kids → Edit** you choose what each child can do. A new profile
starts with only emoji reactions; comments, tags, customizing, uploads,
search and asking for new videos are off until you turn them on. Fewer buttons means fewer
ways to get lost. The **Big & clear** theme uses large text and high
contrast; **Calm** removes colour and shadows.

## The parent area

- The parent dashboard needs the parent password (at least 8
  characters). After 10 wrong tries from one device it waits 5 minutes.
- If you sign in as a parent on the child's tablet and then pick a kid
  profile, the parent sign-in on that tablet ends. Changing the parent
  password signs out every other device.
- A kid **PIN** keeps siblings out of each other's profiles. It can be
  numbers or three pictures (tap the lion, the dog, the turtle). It is
  not a strong lock: a determined older child could guess it over time
  (5 tries, then a 5-minute wait); a picture PIN has 729 combinations,
  a 4-digit one 10,000.

## What you still need to do on the device

Curatables controls what is *inside* its pages. It cannot stop a child
from leaving the browser. On the child's tablet:

- **iPad / iPhone:** open the kid address in Safari, tap **Share → Add to
  Home Screen**, open it from the new icon, then turn on **Guided Access**
  (Settings → Accessibility → Guided Access) and triple-click to lock the
  tablet to that screen. Screen Time can also block Safari and other
  apps.
- **Android:** use **App pinning** (Settings → Security → App pinning)
  or a kiosk / kids-mode launcher, and remove other browsers.
- Don't leave the parent password saved in the tablet's browser.

## Your network

- Curatables is meant for your **home network only**. Don't forward a
  port to it from your router: it has no HTTPS and was not designed to
  face the internet.
- Until you set the parent password the first time, **anyone on your
  network** who opens the dashboard within 30 minutes of the box starting
  can set it. Do that first, right after installing.
- With only the videos on a USB drive, the settings and password stay on
  the SD card: if the drive is missing, kids see a "videos are resting"
  screen and nothing is stored until it is back. With the whole library
  on the drive, Curatables refuses to start without it, so it never
  comes up as a fresh install that asks for a new password.

## What is recorded

Curatables keeps, on your server only: which kid watched which video and
when, reactions, comments, searches and uploads. You see these under
**Stats** and on the dashboard. Usage events older than a year are
deleted automatically (see
[`events_retention_days`](configuration.md#storage)). Nothing is sent to
any outside service. The server goes online only to download the videos
and pictures you add; look up titles on Wikipedia, Wikidata, TMDB (with
your key) or YouTube when you import; search YouTube when a child uses
**Find new**; and check for updates (Curatables, the downloader, and the
daily security updates on the Pi image).
