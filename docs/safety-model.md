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
- **Re-downloaded videos are checked.** To save disk space, a video
  nobody watched for a while is removed and downloaded again the next
  time someone taps it. If the length of the new copy is clearly
  different from the one you approved, it goes back to "waiting for
  your OK" instead of playing. Mark favourites **keep forever** to avoid
  re-downloads entirely.

## Keeping it simple for your child

On **Kids → Edit** you choose what each child can do. A new profile
starts with only emoji reactions; comments, tags, customizing, uploads
and search are off until you turn them on. Fewer buttons means fewer
ways to get lost. The **Big & clear** theme uses large text and high
contrast; **Calm** removes colour and shadows.

## The parent area

- The parent dashboard needs the parent password (at least 8
  characters). After 10 wrong tries from one device it waits 5 minutes.
- If you sign in as a parent on the child's tablet and then pick a kid
  profile, the parent sign-in on that tablet ends. Changing the parent
  password signs out every other device.
- A kid **PIN** keeps siblings out of each other's profiles. It is not a
  strong lock: a determined older child could guess 4 digits over time
  (5 tries, then a 5-minute wait).

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
  network** who opens the dashboard can set it. Do that first, right
  after installing.
- With the library on an external drive, Curatables refuses to start if
  the drive is missing, so it never comes up as a fresh install that
  asks for a new password.

## What is recorded

Curatables keeps, on your server only: which kid watched which video and
when, reactions, comments, searches and uploads. You see these under
**Stats** and on the dashboard. Usage events older than a year are
deleted automatically (see
[`events_retention_days`](configuration.md#storage)). Nothing is sent to
any outside service. The server itself goes online only to fetch a
video and its thumbnail from the site it came from (when you add it, or
when it is downloaded again) and to update yt-dlp when you press the
button.
