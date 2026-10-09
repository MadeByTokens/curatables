# FAQ and troubleshooting

Short answers for everyday questions. Installation errors are covered in
[getting-started.md → Troubleshooting](getting-started.md#troubleshooting).
Most commands below are typed on the Curatables box itself, over SSH
(`ssh <your-user>@curatables.local`, using the login you set in Raspberry
Pi Imager).

## Getting connected

**`curatables.local` doesn't open.**
- On Windows, install Apple's *Bonjour Print Services*, or use the IP
  address instead (next question).
- On Android, use Chrome; many other browsers can't open `.local` names.
- Give a freshly booted Pi two minutes.

**How do I find the box's IP address?**
Look in your router's list of connected devices for `curatables`, or run
`hostname -I` on the box. Then browse to `http://<that address>/`
(add `:8080` on a try-out install).

**The Pi didn't join my Wi-Fi.**
Put the SD card in a computer and open `curatables-firstboot.log` on the
small boot drive: it says whether the Pi found the network and whether
the password was accepted. Then open `curatables-wifi.txt` next to it,
check `country` (two letters, e.g. `US`), `ssid` (case-sensitive) and
`psk`, save, and boot the Pi again. A network cable always works as a fallback.
Once the Pi has joined, it empties `ssid` and `psk` in that file so your
password doesn't stay on the card; to move to another network, fill
them in again and reboot.

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
YouTube changes often. Open **Settings → Updates → Update yt-dlp now**,
wait for the restart, then press **Retry** on the failed videos. If it
still fails, turn on **Settings → Advanced → Anti-Bot → Browser
Impersonation**.

**A kid says a video "is getting ready" for a long time.**
It was removed from disk to save space (nobody watched it for a while)
and is downloading again. Mark videos you want to keep as **keep
forever** on their edit page, or tick **Settings → Keep newly added
videos forever**.

**A kid sees no videos at all.**
Check **Kids → Edit → Visible channels**: if "Only show the channels
ticked below" is on with nothing ticked, that kid sees only their own
channels. Also check the videos are not **Hidden**.

**The disk is full.**
The dashboard says so. Curatables first removes the least recently
watched cached videos; uploads and keep-forever videos are never
removed. **Storage** shows what uses the space. On a Pi, move the library
to a USB drive ([deployment.md](deployment.md#media-on-an-external-drive-raspberry-pi--usb-disk)).

**I already have a drive full of movies.**
Don't format it. Plug it into the Pi and run
`sudo /opt/curatables/scripts/setup-external-drive.sh --media-only /dev/sda1`
(find the name with `lsblk -f`). It works with exFAT, NTFS and ext4, keeps
every file where it is, and adds the drive to **+ Add → import files
already on a drive**, where you pick the movies, check their titles and
pictures, and import them
([getting-started.md](getting-started.md#import-videos-already-on-a-drive)).

**An import is taking hours.**
The file is being converted for the player, and a Raspberry Pi 4 can take
longer than the movie lasts. The Import page shows how fast it is going
and about how long is left; **Cancel** stops it and leaves the file on
the drive exactly as it was. Files that are already H.264/AAC MP4 at up to
720p need no conversion.

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

**Where are the logs?**
`sudo journalctl -u curatables -n 100` (add `-f` to follow). Older logs
are in `logs/` inside the data dir.

**The external drive was unplugged.**
Curatables stops and waits. Plug it back in and run
`sudo mount -a && sudo systemctl restart curatables`, or reboot. An exFAT
drive should be checked first: `sudo fsck.exfat /dev/sda1`.

**I forgot the parent password.**
See [getting-started.md → I forgot the parent
password](getting-started.md#i-forgot-the-parent-password).

**How do I back up and upgrade?**
Backups run every night ([backup.md](backup.md)). To upgrade, see
[upgrade.md](upgrade.md).

**Is it safe to reach from outside my home?**
No. Keep it on your home network; see
[safety-model.md → Your network](safety-model.md#your-network).
