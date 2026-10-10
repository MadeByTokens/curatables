# Getting started — a step-by-step guide

This guide is for a parent who is **willing to follow instructions but
isn't a software developer**. If you can copy-and-paste a line into a
black "terminal" window and press Enter, you can do this. Every command
is explained, and there's a [Troubleshooting](#troubleshooting) section
for when something doesn't go to plan.

By the end you'll have Curatables running on a small computer in your
home, reachable from any phone, tablet, or laptop on your Wi-Fi.

> **Using the ready-made Raspberry Pi image?** Follow
> [parents-guide.md](parents-guide.md) instead: no terminal at all.
>
> **Already comfortable with Linux and systemd?** You can skip this
> guide and read [deployment.md](deployment.md) instead — it's the
> terse ops version of the same thing.

---

## What you'll need

Curatables runs on a small Linux computer that stays on at home. It is
**not** an app you install on your phone or your everyday Mac/Windows
laptop — those are the *client* devices your family uses to watch. You
need one dedicated "server" box.

The best beginner choice is a **Raspberry Pi**:

| You want… | Get… | Notes |
|---|---|---|
| The simplest, cheapest box | **Raspberry Pi 5 (4 GB)** | Recommended. Fast enough that videos process quickly. |
| Cheaper, still fine | **Raspberry Pi 4 (2–4 GB)** | Works, but processing each added video is slower (see [the transcoding note](#why-does-a-new-video-take-a-while)). |
| You already own a spare laptop/mini-PC | Any machine that can run **Ubuntu** or **Debian** Linux | Even an old laptop is great — often faster than a Pi. |

You'll also need:

- A **microSD card** (32 GB or larger) if using a Raspberry Pi, plus its
  power supply.
- The box connected to your home network — **wired Ethernet is best**,
  Wi-Fi is fine.
- Another computer (your normal laptop) to do the initial setup from.

> **How much computer do I actually need?** Not much. Curatables itself
> is light. The one job that needs some muscle is *re-processing each
> video you add* so it plays on old devices — a Pi 4 does this slowly, a
> Pi 5 or a laptop does it quickly.
>
> **On a Raspberry Pi, plan for a USB hard drive or SSD** for the videos:
> SD cards are small and wear out. You can add it later
> ([Using an external hard drive](#using-an-external-hard-drive-for-the-videos)).

---

## Step 1 — Put Linux on the box

> **Have a Raspberry Pi?** Use the ready-made image and follow the
> [parents' guide](parents-guide.md) instead: no terminal, and you can
> skip this whole page. The manual route below (plain Raspberry Pi OS and
> the installer) is for a laptop or mini-PC, or if you prefer to see each
> step.

**If you're using a spare laptop or mini-PC that already runs Ubuntu or
Debian Linux, skip to [Step 2](#step-2--open-a-terminal-on-the-box).**

For a Raspberry Pi (manual route):

1. On your normal laptop, download and install the
   **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)**.
2. Insert the microSD card into your laptop.
3. Open Raspberry Pi Imager and choose:
   - **Device**: your Pi model.
   - **Operating System**: *Raspberry Pi OS (64-bit)* — the standard one.
   - **Storage**: your microSD card.
4. Click the **gear / "Edit settings"** button before writing. This lets
   you set things up so you never need a screen or keyboard on the Pi:
   - Set a **hostname** (e.g. `curatables`).
   - Set a **username and password** — write these down.
   - Enter your **Wi-Fi name and password** (skip if using Ethernet).
   - On the **Services** tab, **enable SSH** with password authentication.
5. Write the card, put it in the Pi, and power it on. Give it a couple of
   minutes to boot for the first time.

---

## Step 2 — Open a terminal on the box

You'll control the box by typing commands into it remotely from your
laptop, using a tool called **SSH**. It's built into macOS, Linux, and
modern Windows.

On your laptop, open a terminal (on Mac: *Terminal* app; on Windows:
*PowerShell* or *Terminal*) and type:

```sh
ssh <username>@<hostname>.local
```

For example, if your username is `pi` and hostname is `curatables`:

```sh
ssh pi@curatables.local
```

Type the password you set in Step 1. The first time, it'll ask if you
trust the machine — type `yes`. You're now "inside" the box: anything you
type runs there, not on your laptop.

> **`.local` name doesn't work?** See
> [Troubleshooting → "I can't reach the box by name"](#i-cant-reach-the-box-by-name).
> You can always use the box's IP address instead.

---

## Step 3 — Download and install Curatables

Curatables ships a single installer script that does everything: it
installs the system tools it needs (including **ffmpeg** for video
processing and the **mDNS** tools that make the friendly address work),
sets up an isolated Python environment, and installs Curatables itself.

Run these one at a time, pressing Enter after each:

```sh
# 1. Install git (used to download the code). Safe if already present.
sudo apt update && sudo apt install -y git

# 2. Download Curatables into a folder called "curatables"
git clone https://github.com/MadeByTokens/curatables.git
cd curatables

# 3. Run the installer. It will ask for your password (for "sudo").
scripts/install.sh
```

The installer prints `==>` lines as it works. It finishes with an
**acceptance test** that checks everything is in place. If the last lines
look healthy and there are no red `xx` errors, you're good.

> Want to install *and* set it up to start automatically every time the
> box powers on? Use `scripts/install.sh --systemd` instead, then see
> [Step 6](#step-6-optional--make-it-start-automatically). For your very
> first run, the plain installer is simplest.

---

## Step 4 — Start the server

```sh
.venv/bin/python run.py
```

You'll see startup lines, including (if mDNS is working) something like:

```
mDNS advertisement registered: http://curatables.local:8080/
```

Leave this window open — closing it stops the server. (Step 6 makes it
run permanently in the background; for now we just want to see it work.)

---

## Step 5 — Open it and set your parent password

On **any device on the same Wi-Fi** — your phone is perfect — open a web
browser and go to:

```
http://curatables.local:8080/parent/
```

- **First time:** you'll be asked to **create a parent password**. Pick a
  good one and save it in your password manager. This protects the parent
  dashboard. Do this within 30 minutes of starting Curatables; if the
  page says **Setup has timed out**, restart it
  (`sudo systemctl restart curatables`, or unplug and replug a Pi) and
  try again.
- After that, you land on the **parent dashboard**.

Your kids will use the other address — the same thing without `/parent/`:

```
http://curatables.local:8080/
```

That's the clean, ad-free kid view: profiles, then a grid of only the
videos you've approved.

### Add your first video

1. In the parent dashboard, find **Add a video** (paste a link).
2. Paste a video, channel, or playlist link — from YouTube, Vimeo,
   TikTok, and [~1,800 other sites](https://github.com/yt-dlp/yt-dlp).
3. Preview, then confirm. Curatables downloads it and prepares it for
   playback.

#### Why does a new video take a while?

Curatables re-processes every video you add into a format that plays even
on old phones and tablets (down to a 2015 iPad). This re-processing is
the one part that uses real computing power:

- On a **Raspberry Pi 5** or a **laptop/mini-PC**: usually quick.
- On a **Raspberry Pi 4**: it can take several minutes per video,
  especially for long or high-resolution ones. This is normal — the
  video appears in the kid view once it's done.

If you add a lot of content and the wait bothers you, a Pi 5 or a small
mini-PC will process videos noticeably faster than a Pi 4.

### Import videos already on a drive

Movies from DVDs, videos you saved from YouTube, home videos: if they are
already on a drive plugged into the box, **+ Add → import files already on
a drive** brings them in without uploading anything.

1. **Pick files.** Browse the drive's folders and tick the videos (files
   already imported are marked). A whole folder, subfolders included,
   can go at once: up to 200 files.
2. **Check the details.** The title, year and episode are guessed from the
   file name, and a `poster.jpg`, a Kodi `.nfo` or `.srt` subtitles next to
   a file are picked up. Say where each one is from (a movie or TV show,
   YouTube, another web page, a home video) and pick a channel. Files that
   look like episodes of one show are offered as **one channel for the
   show**, in episode order.
3. **Find a picture and description (optional).** The server looks the
   title up on Wikipedia (with the age rating from Wikidata when there is
   one), on YouTube, or on the web page you link, and shows a few matches
   to choose from. You can also pick a frame from the video or upload a
   picture. Only the title, year or link is sent. Read the description
   before using it, because encyclopedias give away the story.
4. **Import.** **Copy** leaves the files on the drive; **Move** takes
   them off once they are in the library (on the same drive that is
   instant). The Import page shows each file's progress: percent done,
   how fast the conversion is going and about how long is left, worked
   out as it runs. **Cancel** stops any of them at once and leaves no
   half-made files behind; the file on the drive is never changed until
   its import has finished.

Files that already play on every device are imported in seconds. Others
are converted, which on a Raspberry Pi 4 can take as long as the movie
itself, or longer.

---

## Step 6 (optional) — make it start automatically

So far the server only runs while your SSH window is open. To make it
start on boot and stay running, and to use the nicer address
`http://curatables.local/` with **no `:8080`**, install it as a system
service. First stop the test server with **Ctrl-C**, then:

```sh
# From inside the curatables folder
sudo scripts/install.sh --systemd
```

This copies Curatables to `/opt/curatables`, creates a `curatables`
user for it to run as, **moves over everything you already set up**
(password, profiles, videos from `~/curatables-data`), turns on nightly
backups, and starts it. When it finishes, open:

- parents: `http://curatables.local/parent/`
- kids: `http://curatables.local/`

Your original `~/curatables-data` folder is left in place; delete it once
you've checked everything is there.

Check on the service any time with:

```sh
sudo journalctl -u curatables -f      # live log; Ctrl-C to stop watching
```

### Using an external hard drive for the videos

On a Raspberry Pi it's worth keeping the videos on a USB hard drive or
SSD: SD cards are small and wear out. Plug the drive in and find it:

```sh
lsblk -f                                            # e.g. sda1, and its type
```

**Most drives: keep the database on the SD card, videos on the drive.**
This works with the exFAT and NTFS drives computers usually come with, and
with a drive that already holds your movies: nothing on it is erased or
moved.

```sh
sudo /opt/curatables/scripts/setup-external-drive.sh --media-only /dev/sda1
```

The library's videos go into a `curatables-library` folder on the drive,
and the drive is added to the Import page so you can bring in what is
already on it. (FAT32 works, but skips any file of 4 GB or more.)

**Or put the whole library on an ext4 drive:**

```sh
sudo mkfs.ext4 -L curatables /dev/sda1              # ERASES the drive
sudo /opt/curatables/scripts/setup-external-drive.sh /dev/sda1
```

Either way the Pi mounts the drive on every boot. If it is unplugged, a
videos-only drive leaves Curatables running with a "videos are resting"
screen; a whole-library drive keeps Curatables from starting until it is
back. Details:
[deployment.md → Media on an external drive](deployment.md#media-on-an-external-drive-raspberry-pi--usb-disk).

---

## Troubleshooting

### I can't reach the box by name

`curatables.local` (and `<hostname>.local` for SSH) relies on a feature
called **mDNS**. It works out of the box on macOS, iPhone/iPad, and most
Linux. It needs a little help elsewhere:

- **Windows clients**: install Apple's **Bonjour Print Services** (a free
  download, also bundled with iTunes). Without it, `.local` names won't
  resolve on Windows.
- **Android**: Chrome resolves `.local` names; many other browsers don't.
- **Any device**: as a fallback, use the box's **IP address** instead of
  the name. Find it by running `hostname -I` on the box (the first number,
  e.g. `192.168.50.42`), then browse to `http://192.168.50.42:8080/`.

The deployment doc has a deeper checklist:
[deployment.md → Verifying the mDNS setup](deployment.md#verifying-the-mdns-setup).

### The installer printed errors

- **`xx required command not found`**: you're probably not on a
  Debian/Ubuntu/Raspberry Pi OS system. The installer only supports those.
- **apt or network errors**: check the box is online (`ping -c3 github.com`)
  and try `sudo apt update` again.
- You can safely **re-run `scripts/install.sh`** — it's designed to be run
  multiple times.

### A video is stuck "processing" / takes forever

This is the [re-processing step](#why-does-a-new-video-take-a-while). On a
Raspberry Pi 4, several minutes per video is expected. Give it time. If a
specific video never finishes, check the server log (`journalctl` above,
or the open SSH window) for an error about that video.

### Adding videos suddenly fails (especially from YouTube)

YouTube and other sites change often, and the downloader (**yt-dlp**) needs
periodic updating to keep up. You don't need the terminal: open
**Settings → Updates** in the parent dashboard and click **Update yt-dlp
now**. It updates in the background and the server restarts itself; reload
the page to see the result. (This needs the `curatables-updater` service,
which the appliance image and `scripts/install.sh --systemd` install. See
[upgrade.md](upgrade.md) for details.)

### I forgot the parent password

**On the Raspberry Pi image:** turn the box off, put the SD card in any
computer, create an empty file named `curatables-reset.txt` on the part
the computer can open, and start the box again. See
[parents-guide.md → Something's wrong](parents-guide.md#7-somethings-wrong).

**On your own install:** the password hash is stored in `config.json` in your data folder
(`/home/curatables/curatables-data/` for a permanent install,
`~/curatables-data/` for a try-out). Clearing it puts Curatables back
into first-run mode so you can set a new one; nothing else is lost:

```sh
sudo systemctl stop curatables
sudo -u curatables /opt/curatables/.venv/bin/python -c '
import json
p = "/home/curatables/curatables-data/config.json"
cfg = json.load(open(p))
cfg["parent"]["password_hash"] = None
json.dump(cfg, open(p, "w"), indent=2)
'
sudo systemctl start curatables
```

Then open `http://curatables.local/parent/` straight away and set a new
password. Until you do, anyone on your network who opens that page
could set it.

### The server won't start: "ffmpeg not found" or similar

Run the built-in dependency check to see exactly what's missing:

```sh
.venv/bin/python run.py --check
```

It lists each required tool and its version. Re-running
`scripts/install.sh` installs anything missing.

---

## Where to go next

- **[deployment.md](deployment.md)** — run as a always-on service, port
  80, mDNS details.
- **[backup.md](backup.md)** — protect your library.
- **[upgrade.md](upgrade.md)** — install new versions later.
- **[../README.md](../README.md)** — what Curatables can do, and the
  roadmap.
