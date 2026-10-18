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

**My browser says the box is "Not secure".**
The box sends plain web pages (`http://`), not padlocked ones
(`https://`): a padlock needs a certificate from the internet, and the
box stays inside your home. The warning means only that the pages are
not encrypted on their way across your home network. At home that is
fine, because nobody outside your network can reach the box. Browsers
word this differently, and some warn on the parent password box or show
a warning page before opening the box; you can carry on to the box.
Don't open the parent dashboard on shared Wi-Fi (an apartment network,
a hotspot), where someone else on the network could copy your login
([threat-model.md](threat-model.md#session-theft-on-the-lan)).

**The box opens in Safari but not in another browser on an iPad or iPhone.**
On an iPad or iPhone, browsers other than Safari need your permission
to reach devices on your home network, and they ask for it the first
time. If that was refused, the box never loads in that browser. Turn
it back on in the iPad's **Settings**, on the browser's own page (look
for **Local Network**). Or use Safari, which needs no permission.

**How do I find the box's number address?**
Look in your router's list of connected devices for `curatables`, then
browse to `http://<that address>/` (like `http://192.168.1.50/`; add
`:8080` on a try-out install). **Box → Login, network & power** also
shows it.

**The box didn't join my Wi-Fi.**
See [parents' guide → step 2](parents-guide.md#2-connect-the-box-to-your-network):
`curatables-firstboot.log` on the SD card says what went wrong. A network
cable always works. Once the box is on the network, **Box → Login,
network & power** switches Wi-Fi without the SD card.

**How do I put it on the kid's tablet like an app?**
The **Kids** page shows the kid address and a QR code; point the tablet's
camera at it to open the address. iPad: open the kid address in Safari
(other browsers need a permission first, see above) → **Share → Add to
Home Screen**.
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

**A few YouTube videos only come in low quality (360p).**
YouTube keeps its better formats for players it trusts, and the box
doesn't sign in to YouTube. Most videos still come in HD; a few stay at
360p, which looks soft on a tablet but is fine on a phone. You can leave
it like that: nothing needs setting up and no account is involved.

If you want those videos in HD, the box can sign in with a YouTube
login saved in a cookies file. Use a **separate Google account made just
for the box**, never your own or the family's: YouTube can block an
account it sees used this way, and anyone who holds the SD card can read
the file.

1. On a computer, open a private browser window, sign in to YouTube
   with that account, and save its cookies with a browser extension that
   exports a `cookies.txt` file (Netscape format; yt-dlp's FAQ lists
   some). Close the private window without signing out: signing out
   would end the login the file holds.
2. **Box → Shut down** the box. Put its SD card in the computer and copy
   `cookies.txt` onto the part the computer opens (`bootfs`). Put the
   card back and start the box.
3. In **Settings → Advanced → Anti-Bot**, type `/boot/firmware/cookies.txt`
   under **Cookie File**, save, and restart Curatables when the page
   asks.

The login runs out after a while; when HD videos come out at 360p
again, repeat steps 1 and 2. To stop, clear **Cookie File** and delete
the file from the card.

**A kid sees "Getting your video ready…" for a long time.**
Its file is not on the box, so it is downloading again. Videos are kept
unless you turned on **Settings → Freeing up space**; then a video nobody
watched for that many days loses its file; its picture stays, marked
"Takes a moment to start", and the file comes back when tapped. To
stop that for a favourite, tick **Keep forever** on its edit page, or set
Freeing up space back to `0`.

**A kid sees no videos at all.**
Check **Kids → Edit → Which channels can they see?**: with **None of
yours** that kid sees only their own channels. Also check the videos have a channel (a video whose channel is
**(none)** is hidden from every kid) and are not **Hidden**.

**The disk is full.**
Curatables keeps every video and never deletes one on its own. The
dashboard warns when the videos drive has 15% free or less, and lists the
ways out: move the videos to a bigger USB drive (**Box → Drives & backups →
Use for videos**), turn on **Settings → Freeing up space** so videos nobody
watched for a number of days you pick lose their files (they stay listed and
download again when tapped; uploads and keep-forever videos are never
touched), or pick what nobody watches: **Content** sorted by **Least
watched** (or **Not watched for longest**, **Largest files**) shows how often
each video was played and its size; tick some and press **Free up files**
(the files go, the videos stay listed and download again when tapped) or
**Delete**. Below the hard minimum (2 GB) downloads, imports and uploads
stop, even ones already running, and remove what they had written.

The box's own memory card (the system, the library's records and the
nightly backups) gets its own warning on the dashboard when it has under
1 GB or 10% free; nightly backups are skipped until there is room again.

**The box's clock, or "today" in the stats, is wrong.**
The Raspberry Pi has no battery-backed clock. After a power cut it starts
from the last time it saved and catches up once it reaches the internet.
Without internet, opening any page from a signed-in kid's or parent's
device sets the clock from that device. "Today" starts at midnight in the
family's time zone, filled in from your browser; change it in **Settings →
Storage → Time zone**.

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

**What does a kid's search look in?**
The videos that kid can see: their titles and descriptions, plus the
names, descriptions and tags that child gave them (another kid's names
and tags stay private). Accents and capitals don't matter, so "vulcao"
finds "Vulcão".

**Can each child have different rules?**
Yes. Each profile has its own channels, PIN, language, theme and
switches for reactions, comments, tags, customizing, uploads, search,
asking for new videos and automatic subtitles (**Kids → Edit**).

**A kid uploaded something. Who sees it?**
In the kid's own channel: only that kid (and anyone you shared that
channel with). In a shared channel: nobody until you approve it under
**Needs your attention**.

**Can my kid ask for new videos?**
Yes, if you switch on **Ask for new videos** for them (Kids → Edit).
They get a **Find new** page that searches YouTube videos or channels
through Curatables and shows only results that pass your rules
(Settings → Video requests). Tapping **Ask** sends it to **Video requests** (linked from
the dashboard and the Kids page), and the child can then give it their own name and
description and pick one of their channels, make a new one of their own (with
**Customize videos and own channels** on; otherwise the name is a suggestion for you),
or leave the choice to you: then **Add** waits until you pick a channel, or "none".
The child can take a request back, and you can **Remove** one without turning the video
down. You watch it, change what you like and add it, or press **Not this one**; either way you are back on the list for the next one. The review page opens instantly: it shows what was
saved when the child asked, without asking YouTube again. Nothing downloads before you
add it. See [safety-model.md](safety-model.md) for what the child sees
in the meantime. Searches are cached and rate-limited, so a child
tapping fast gets a "slow down" screen instead of getting the house
flagged by YouTube.

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
