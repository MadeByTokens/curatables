# The Curatables box: a guide for parents

This guide is for setting up and looking after the ready-made Curatables
box (a Raspberry Pi 4 or 5 running the Curatables SD-card image) **from a
Windows PC, a Mac, a phone or a tablet**. You never need a terminal or
any Linux knowledge: everything is done from the parent dashboard in a
web browser, and the few things that can't be (because the box isn't on
the network yet) are done by editing text files on the SD card.

If you're installing Curatables on a computer of your own instead of the
ready-made image, follow [getting-started.md](getting-started.md).

Once the box is set up, the picture guides show every everyday screen:
[for parents](guide/parents.md) and [for kids](guide/kids.md).

- [What you need](#what-you-need)
- [1. Put Curatables on the SD card](#1-put-curatables-on-the-sd-card)
- [2. Connect the box to your network](#2-connect-the-box-to-your-network)
- [3. First start: two passwords](#3-first-start-two-passwords)
  - [Your first video and your first kid](#your-first-video-and-your-first-kid)
- [4. The Box pages](#4-the-box-pages)
- [5. USB drives for more videos](#5-usb-drives-for-more-videos)
- [6. Backups](#6-backups)
- [7. Something's wrong](#7-somethings-wrong)
- [About the SD card and safety](#about-the-sd-card-and-safety)

## What you need

- A **Raspberry Pi 4 or 5** (2 GB of memory or more) with the **official
  Raspberry Pi USB-C power supply**. Phone chargers often can't give
  enough power, and a short supply causes drives to disconnect and files
  to get damaged.
- A **microSD card**, 32 GB or larger (16 GB is enough if the videos go
  on a USB drive), and a way to put it in your
  computer (a card slot or a cheap USB card reader).
- **Optional:** an Ethernet cable to your router (the simplest way to
  connect), and a USB drive or USB hard disk for more videos. A USB hard
  disk usually needs its own power adapter or a powered USB hub.

## 1. Put Curatables on the SD card

1. Open the newest
   [release](https://github.com/MadeByTokens/curatables/releases/latest)
   on GitHub and, under **Assets**, download
   `curatables-<version>-raspberry-pi.img.xz` (about 900 MB). The other
   files there are the source code; you don't need them. Don't unzip
   the image.

   *Optional check:* the `.sha256` file next to it holds the image's
   SHA-256 checksum. On Windows, `certutil -hashfile <image file> SHA256`
   in a Command Prompt prints the image's checksum; on a Mac or Linux,
   `shasum -a 256 <image file>`. The two should match.
2. Install **Raspberry Pi Imager** from
   [raspberrypi.com/software](https://www.raspberrypi.com/software/) and
   open it.
3. **Device:** your Raspberry Pi model. **Operating system:** scroll down to
   **Use custom** and pick the file you downloaded. **Storage:** your SD
   card. Double-check it's the SD card: it will be erased.
4. When Imager offers to **customise the settings**, you can say **No**.
   You'll choose the passwords and the Wi-Fi from the dashboard and the
   SD card instead. If you do customise, keep the hostname as
   **`curatables`**, or the box's address becomes whatever name you typed
   plus `.local`.
5. Write the card and wait until Imager says it's done.

> **Windows asks "You need to format the disk in drive E: before you can
> use it"?** Click **Cancel**. The SD card has a second part that only
> Linux can read; formatting it would erase Curatables. The part Windows
> *can* open (often called `bootfs`) is the one this guide uses.

## 2. Connect the box to your network

**With an Ethernet cable (easiest):** plug the box into your router.
That's all: skip to step 3.

**With Wi-Fi:**

1. Take the SD card out and put it back in your computer. Open the drive
   that appears (`bootfs`).
2. Open **`curatables-wifi.txt`** in Notepad (on a Mac, TextEdit).
3. Fill in the three lines at the bottom: your two-letter `country` (US,
   GB, CA, DE, BR…), the Wi-Fi name after `ssid=` and its password after
   `psk=`. Capital letters matter. Save the file.
4. Eject the card safely, put it in the box and plug the box in.

Once the box has joined the network it removes the name and password
from that file, so they don't stay on the card. If it can't connect,
put the card back in your computer and open `curatables-firstboot.log`
next to it: it says whether the network was found and whether the
password was accepted.

Later on, you can change the Wi-Fi from the dashboard instead (see
[The Box pages](#4-the-box-pages)). When an Ethernet cable is plugged
in, the box uses the cable and keeps Wi-Fi on standby.

## 3. First start: two passwords

Give the box two or three minutes after plugging it in the first time,
then open **http://curatables.local** in a browser on the same network.

> **The page doesn't open?** Some Android phones don't understand
> `.local` addresses: try a laptop, an iPhone or an iPad instead, or find
> the box (called `curatables`) in your router's list of connected
> devices and type its number address (like `http://192.168.1.50`).

Setup opens by itself and asks for **two different passwords**. Let your
password manager suggest and save both: the pages are made so it
recognises them.

1. **The parent dashboard password.** You'll use this one all the time
   to manage videos and kids. Don't use the kids' PIN or anything they
   could guess.
2. **The box's Linux login password.** The box runs Linux, and its
   `admin` account can change anything on it. You'll almost never need
   it, but it has to be different from the dashboard password, so that
   someone who learns one doesn't get both. If you already set a password
   in Raspberry Pi Imager, type it to keep it (or replace it). Leave
   **Allow SSH logins** off unless you know you want the remote command
   line.

Do this within 30 minutes of turning the box on. After that, setup
closes for safety; unplug the box for ten seconds and plug it back in to
open it again.

### Your first video and your first kid

1. **Make a channel or two.** A channel is a shelf of videos, like
   "Animals" or "Bedtime". Open **Channels** in the parent menu and
   create one.
2. **Add a video.** Open **+ Add** and paste a link to a video, a whole
   channel or a playlist. Watch each video in the preview, tick **I have
   already fully watched this video**, pick its channel and press
   **Add**. A video with no channel is hidden from the kids. It then
   downloads and is converted so it plays on older tablets too; on a
   Raspberry Pi 4 that takes a few minutes. **Content → Downloading**
   shows how it's going.
3. **Make a profile for each kid.** Open **Kids → Create Profile**, give
   it a name and a picture, and choose what the child may do. A new
   profile can watch and react, nothing more, and sees every channel
   until you tick only some. A PIN is optional.
4. **Set up the tablet.** On the kid's tablet, open the kid address
   shown on the **Kids** page (or scan its QR code with the camera) and
   add it to the home screen (on an iPad, use Safari; see the
   [FAQ](faq.md#getting-connected)). To keep your child inside it, see
   [What you still need to do on the device](safety-model.md#what-you-still-need-to-do-on-the-device).

The dashboard's **Help** page has more tips.

## 4. The Box pages

**Box** in the parent menu manages the box itself:

- **Login, network & power** shows how the box is connected and lets you:
  - **Switch Wi-Fi.** Press *Look for networks*, then *Use this* next to
    yours, type its
    password and press *Switch Wi-Fi*. The box tries the new network
    first and **goes back to the old one if it can't connect**, so a
    typo can't lock you out. If you're on Wi-Fi yourself, the page may
    drop for a moment.
  - **Change the Linux login password** or **turn SSH on or off** (both
    ask for your parent password again).
  - **Restart Curatables**, **Reconnect network** or **Restart the box**.
    Restarting fixes most "it's stuck" moments.
  - **Shut down** the box. Use it before unplugging, and unplug once the
    green light stops flickering (about 20 seconds). To turn the box on
    again, unplug it for a few seconds and plug it back in.
- **Drives & backups** is described in the next two sections.
- **Updates** keeps the box current without touching the SD card:
  - **Update to …** installs a new Curatables version from GitHub. All
    videos, kids and settings are kept. The new version is prepared while
    the old one keeps running; kids only lose about a minute. If it
    doesn't start, the box goes back to the old one by itself, and
    **Go back to …** undoes an update you don't like (keeping today's
    videos, kids and settings whenever it can).
  - **Update the downloader** when YouTube videos stop downloading.
  - **Update system packages** installs the operating system's updates.
    Security updates also install by themselves every day. If the page
    says a restart is needed, restart the box when nobody is watching.
- **Diagnostics** checks everything and says what to do (see
  [Something's wrong](#7-somethings-wrong)).

## 5. USB drives for more videos

The SD card is small. A USB drive gives you room for many more videos.

**Which drive?** Any USB stick, SSD or hard disk works. The format
matters:

| Format | Works? | Notes |
|--------|--------|-------|
| **exFAT** | Yes (recommended) | Most drives bigger than 32 GB come this way. Windows and Mac can read it too. |
| **NTFS** | Yes | The usual Windows format. |
| **FAT32** | Yes, with a limit | Most small USB sticks come this way. It **can't hold a file of 4 GB or more**, so long movies at high quality are skipped. |
| **ext4** | Yes | Linux only: your computer can't read it. |
| Anything else (Mac OS Extended, APFS…) | No | Format it from the Drives page. |

**To use a drive for the videos:**

1. Plug it into one of the box's **blue** USB ports (the faster ones).
2. Open **Box → Drives & backups**. The drive appears under **USB
   drives** with its format and free space.
3. Press **Use for videos**. The box copies the videos already on the SD
   card to a `curatables-library` folder on the drive and uses it from
   then on. Anything else on the drive is left alone. This can take a
   while; the progress page updates by itself.
4. Once it's done, the page offers to **delete the old copy** on the SD
   card to free the space.

**Formatting a drive:** under the drive, open *Format this drive*, pick
**exFAT**, give it a name and type **ERASE** to confirm. Everything on
the drive is deleted.

**Before unplugging it,** press **Eject safely**. Pulling
a drive out while it's in use is the most common way to damage it.

**If the drive is unplugged or the power goes out,** kids see a calm
"videos are resting" screen instead of broken videos, and the dashboard
shows a red notice. Plug the drive back in, wait ten seconds and press
**Reconnect** on the Drives page. If it won't reconnect, press **Check &
repair**.

**Moving to a bigger drive:** plug in the new one and press **Use for
videos** on it. The box copies everything across and switches over; the
old drive keeps its copy until you erase it. **Move videos back to the
SD card** does the reverse, if they fit.

**Importing videos you already have on a drive:** press **Import from it**
under the drive. The Import page then lists the drive's
folders.

**Power warning:** if the Drives page says the box is **not getting
enough power**, use the official power supply, and give a USB hard disk
its own power adapter or a powered USB hub.

## 6. Backups

Every night the box saves its settings and the list of videos (not the
video files themselves, which can be downloaded again) on the SD card.
On **Box → Drives & backups** you can:

- press **Back up now**,
- turn on **Also keep backups on the videos drive**, so the library can
  be rebuilt even if the SD card dies,
- **Restore** a backup, which puts the settings, kids' profiles and video
  list back as they were on that day.

See [backup.md](backup.md) for the details.

## 7. Something's wrong

| If… | Then… |
|-----|-------|
| YouTube videos stop downloading | **Box → Updates → Update the downloader**, then **Retry** on the failed videos |
| Kids see "the videos are resting" | Plug the videos drive in, then **Reconnect** on **Box → Drives & backups** |
| A kid sees no videos | **Kids → Edit → Which channels can they see?**; check the videos have a channel and aren't **Hidden** |
| A new video doesn't show up | It is still downloading: **Content → Downloading** |
| You forgot the parent password | `curatables-reset.txt` on the SD card (table below) |
| The dashboard won't open | The list under "The dashboard won't open at all" below |
| Anything else | **Box → Diagnostics** |

**Start with Box → Diagnostics.** It checks the network, the internet
and YouTube, the clock, the services, the free space, the videos drive,
the power supply and the temperature, and puts problems at the top with
**what to do** for each. The buttons below the checks fix the usual
problems: **Update the downloader** (YouTube changes often and old
versions stop working), and the restart buttons.

**Asking someone for help?** Press **Download the diagnostics report**
and send them the file. Passwords, Wi-Fi passwords and keys are removed,
but it names your networks and local addresses, so share it only with
someone you trust.

**The dashboard won't open at all.** Work down this list:

1. Wait two minutes after plugging the box in, then try again.
2. Unplug the box for ten seconds and plug it back in.
3. Try the box's number address from your router's device list instead
   of `curatables.local`.
4. Plug in an Ethernet cable: it works even when the Wi-Fi settings are
   wrong.
5. Use the help files on the SD card (below).

**Help files on the SD card.** Turn the box off, put the SD card in your
computer, open the `bootfs` drive and do one of these, then put the card
back and turn the box on. `curatables-README.txt` on the card lists them
too.

| To… | Do this on the card |
|-----|---------------------|
| Set up or change Wi-Fi | Edit `curatables-wifi.txt` ([step 2](#2-connect-the-box-to-your-network)). Read `curatables-firstboot.log` to see what happened. |
| **Reset a forgotten parent password** | Create an empty file named `curatables-reset.txt`. On the next start the dashboard password is cleared, and setup opens again (open the dashboard within 30 minutes). It asks for a new Linux password too. Videos, kids and settings are kept. |
| Forgot the Linux password | Same as above: the reset lets you choose a new one. |
| Get a diagnostics report without the dashboard | Create an empty file named `curatables-diagnose.txt`, start the box, wait five minutes, turn it off. The card now has `curatables-diagnostics-report.txt`. |
| Turn on SSH | Create an empty file named `ssh` (no extension). |

> **Creating an empty file on Windows:** in the `bootfs` window,
> right-click → **New → Text Document**, and name it exactly as shown.
> If Windows hides file extensions you'll end up with
> `curatables-reset.txt.txt`: turn on **View → File name extensions**
> first (in Windows 11, **View → Show → File name extensions**).

Each file except `ssh` is used once and then renamed to `…-done.txt`;
to use it again, rename it back. (Raspberry Pi OS deletes the `ssh` file
once SSH is on.)

## About the SD card and safety

Whoever holds the SD card can change anything on the box: they could
reset the passwords with the files above, or simply put in a different
card. That's deliberate, because it's how you recover from a forgotten
password without any special tools. It does mean the SD card is the real
key to the box: keep the box somewhere the kids can't open it, and don't
lend the card out.

On the network, the parent dashboard is protected by its password and
the rest of the box by the separate Linux password. Keep the box on your
home network and don't make it reachable from the internet (see
[safety-model.md](safety-model.md)).
