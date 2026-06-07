# Getting started — a step-by-step guide

This guide is for a parent who is **willing to follow instructions but
isn't a software developer**. If you can copy-and-paste a line into a
black "terminal" window and press Enter, you can do this. Every command
is explained, and there's a [Troubleshooting](#troubleshooting) section
for when something doesn't go to plan.

By the end you'll have Curatables running on a small computer in your
home, reachable from any phone, tablet, or laptop on your Wi-Fi.

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
> Pi 5 or a laptop does it quickly. The big hardware numbers you may see
> mentioned elsewhere in this project (16–32 GB RAM, many CPU cores) are
> for a *future, optional* AI feature that isn't built yet. **Ignore
> those for now** — they don't apply to running Curatables today.

---

## Step 1 — Put Linux on the box

**If you're using a spare laptop or mini-PC that already runs Ubuntu or
Debian Linux, skip to [Step 2](#step-2--open-a-terminal-on-the-box).**

For a Raspberry Pi:

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
  dashboard.
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

---

## Step 6 (optional) — make it start automatically

So far the server only runs while your SSH window is open. To make it
start on boot and stay running — and to use the nicer address
`http://curatables.local/` with **no `:8080`** — install it as a system
service:

```sh
# From inside the curatables folder
scripts/install.sh --systemd
```

This sets up the background service. Then follow
[deployment.md](deployment.md) to point the service at your install
folder and switch it on with `systemctl enable --now curatables`. That
doc also explains how port 80 (the "no `:8080`" address) works safely
without running as the all-powerful root user.

Once it's a service, you can check on it any time with:

```sh
sudo journalctl -u curatables -f      # live log; Ctrl-C to stop watching
```

And **set up backups** — see [backup.md](backup.md). Do this before you
put in a lot of content you'd hate to re-add.

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
  e.g. `192.168.1.42`), then browse to `http://192.168.1.42:8080/`.

The deployment doc has a deeper checklist:
[deployment.md → Verifying the mDNS setup](deployment.md#verifying-the-mdns-setup).

### The installer printed errors

- **`xx required command not found`**: you're probably not on a
  Debian/Ubuntu/Raspberry Pi OS system. The installer only supports those.
- **apt or network errors**: check the box is online (`ping -c3 deno.land`)
  and try `sudo apt update` again.
- You can safely **re-run `scripts/install.sh`** — it's designed to be run
  multiple times.

### A video is stuck "processing" / takes forever

This is the [re-processing step](#why-does-a-new-video-take-a-while). On a
Raspberry Pi 4, several minutes per video is expected. Give it time. If a
specific video never finishes, check the server log (`journalctl` above,
or the open SSH window) for an error about that video.

### I forgot the parent password

The password is stored in the database under your data folder
(`~/curatables-data/`). Recovery is a maintenance task — see
[backup.md](backup.md) for how the data is laid out, or open an issue if
you're stuck.

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
