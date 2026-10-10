# FAQ and troubleshooting

Short answers for everyday questions. On the Raspberry Pi image you won't
need a terminal: every answer starts with the dashboard. Lines marked
**Own install** are for people who installed Curatables themselves and
type commands on the box (over SSH). Setup problems are in the
[parents' guide](parents-guide.md#7-somethings-wrong) (Pi image) and
[getting-started.md → Troubleshooting](getting-started.md#troubleshooting).

## Getting connected

**`curatables.local` doesn't open.**
- Give a freshly started box two minutes.
- Some Android browsers can't open `.local` names: use Chrome, or the
  number address (next question).
- If a Windows computer can't open it, use the number address too.

**How do I find the box's number address?**
Look in your router's list of connected devices for `curatables`, then
browse to `http://<that address>/` (like `http://192.168.1.23/`; add
`:8080` on a try-out install). **Box → Login, network & power** also
shows it.

**The box didn't join my Wi-Fi.**
See [parents' guide → step 2](parents-guide.md#2-connect-the-box-to-your-network):
`curatables-firstboot.log` on the SD card says what went wrong. A network
cable always works. Once the box is on the network, **Box → Login,
network & power** switches Wi-Fi without the SD card.

**How do I put it on the kid's tablet like an app?**
The **Kids** page shows the kid address and a QR code; point the tablet's
camera at it to open the address. iPad: open the kid address in Safari → **Share → Add to Home Screen**.
Android: Chrome menu → **Add to Home screen**. To keep the child inside
it, see [safety-model.md → What you still need to do on the
device](safety-model.md#what-you-still-need-to-do-on-the-device).

## Videos

**I added a video but the kids don't see it.**
It is still downloading or converting. Open **Content → Downloading**. On
a Raspberry Pi 4 a 10-minute video can take several minutes, and videos
download one at a time. If it shows under **Failed**, read the advice
next to it and press **Retry**.

**Adding from YouTube suddenly fails.**
YouTube changes often, so the downloader needs updating: **Box → Updates
→ Update the downloader** (own install: **Settings → Updates**). Then
press **Retry** on the failed videos. If it still fails, turn on
**Settings → Advanced → Anti-Bot → Browser Impersonation** (makes
downloads look like a normal browser).

**A kid says a video "is getting ready" for a long time.**
It was removed from disk to save space (nobody watched it for a while)
and is downloading again. Mark videos you want to keep as **Keep
forever** on their edit page, or tick **Set newly added videos to Keep
forever** in **Settings → Storage**.

**A kid sees no videos at all.**
Check **Kids → Edit → Visible channels**: if "Only show the channels
ticked below" is on with nothing ticked, that kid sees only their own
channels. Also check the videos are not **Hidden**.

**The disk is full.**
The dashboard says so. Curatables first removes the least recently
watched cached videos; uploads and keep-forever videos are never
removed. **Storage** shows what uses the space. On the Pi image, move the
videos to a USB drive with **Box → Drives & backups → Use for videos**.

**I already have a drive full of movies.**
Don't format it. Plug it in, open **Box → Drives & backups** and press
**Import from it** (or **Use for videos** to keep the library on it too).
Nothing is erased. The movies then show under **+ Add → import files
already on a drive**, where you pick them, check their titles and
pictures, and import them
([getting-started.md](getting-started.md#import-videos-already-on-a-drive)).
**Own install:** `sudo /opt/curatables/scripts/setup-external-drive.sh --media-only /dev/sda1`
(find the name with `lsblk -f`).

**An import is taking hours.**
The file is being converted for the player, and a Raspberry Pi 4 can take
longer than the movie lasts. The Import page shows how fast it is going
and about how long is left; **Cancel** stops it and leaves the file on
the drive exactly as it was. Files that already play in a web browser
(MP4, up to 720p) need no conversion.

## Kids and profiles

**Can each child have different rules?**
Yes. Each profile has its own channels, PIN, theme and switches for
reactions, comments, tags, customizing, uploads, search and automatic
subtitles (**Kids → Edit**).

**A kid uploaded something. Who sees it?**
In the kid's own channel: only that kid (and anyone you shared that
channel with). In a shared channel: nobody until you approve it under
**Needs your attention**.

**Can my kid ask for new videos?**
Yes, if you switch on **Ask for new videos** for them (Kids → Edit).
They get a **Find new** page that searches YouTube through Curatables
and shows only results that pass your rules (Settings → Video
requests). Tapping **Ask** sends it to **Video requests** (linked from
the dashboard and the Kids page); you watch it and add it to a channel
they can see, or press **Not this one**. Nothing downloads before you
add it. See [safety-model.md](safety-model.md) for what the child sees
in the meantime.

**A kid forgot their PIN.**
**Kids → Edit**, type a new PIN or tick "Remove the PIN". A child who
doesn't read numbers yet can have a picture PIN instead (Kids → Edit →
PIN → Pictures).

## The box

**Something is wrong with the box.**
**Box → Diagnostics** checks everything, says what to do, and downloads a
report with the logs (passwords removed) for whoever helps you.
**Own install:** `sudo journalctl -u curatables -n 100` (add `-f` to follow).

**The external drive was unplugged.**
Kids see a "videos are resting" screen and nothing new is stored. Plug
it back in and press **Reconnect** on **Box → Drives & backups**; if it
won't reconnect, press **Check & repair**. **Own install:**
`sudo mount -a && sudo systemctl restart curatables`.

**I forgot the parent password.**
Pi image: create an empty `curatables-reset.txt` on the SD card
([parents' guide](parents-guide.md#7-somethings-wrong)). Own install:
[getting-started.md](getting-started.md#i-forgot-the-parent-password).

**How do I back up and update?**
Backups run every night; **Box → Drives & backups** makes one now or
restores one ([backup.md](backup.md)). **Box → Updates** installs new
versions ([upgrade.md](upgrade.md)).

**Is it safe to reach from outside my home?**
No. Keep it on your home network; see
[safety-model.md → Your network](safety-model.md#your-network).
