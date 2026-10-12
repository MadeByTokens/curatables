# Curatables in pictures: for parents

This is the everyday side of Curatables, one screen at a time: adding
videos, making a profile for each kid, answering their requests and
looking after the box. Putting Curatables on the SD card and the first
start are in the [parents' guide](../parents-guide.md); this guide
picks up once you have set the two passwords.

The pictures come from a real box with an example family: **Sam**, the
parent, and three kids, **Mia**, **Leo** and **Lucía**. The kids' side
has [its own guide](kids.md), to look at together with your kids. The
videos are free films; see [the credits](#credits).

- [1. Sign in](#1-sign-in)
- [2. Make a channel](#2-make-a-channel)
- [3. Add a video](#3-add-a-video)
- [4. Make a profile for each kid](#4-make-a-profile-for-each-kid)
- [5. Your kids ask for a video](#5-your-kids-ask-for-a-video)
- [6. Day to day](#6-day-to-day)
- [7. Settings](#7-settings)
- [8. Looking after the box](#8-looking-after-the-box)
- [Credits](#credits)

## 1. Sign in

Open **http://curatables.local/parent/** on a computer, phone or tablet on
the same network as the box, and type the parent dashboard password.
Signing in on one device doesn't sign you out on another.

![The parent sign-in page](img/parent-sign-in.webp)

The first time, the dashboard shows a short list of what to do. The QR
code opens the kids' screen on a tablet's camera.

![The dashboard on a new box, with its Getting started list](img/parent-dashboard-first-time.webp)

## 2. Make a channel

A channel is a shelf of videos, like "Animals" or "Bedtime". Kids only
see videos that are in a channel, and you choose which channels each kid
sees. Open **Channels**, type a name (and, if you like, a few words
about it) and press **Create**.

![Creating the Cartoons channel](img/parent-channel-create.webp)

Sam made two: **Cartoons** and **Space**.

![The Channels page with two channels](img/parent-channels.webp)

## 3. Add a video

Open **+ Add** and paste a link: one video, a whole YouTube channel or a
playlist. Press **Add Content**. Looking it up takes a few seconds.

![The Add page](img/parent-add.webp)

**Review & Add** shows the video so you can watch it before any kid
does.

1. Pick its **channel**. With no channel, kids can't find it.
2. Change the **title** if you like, and read the **description**. It
   comes from YouTube and kids see it, so take out anything you don't
   want them to read, such as links or adverts. The page reminds you.
3. Watch it, then tick **I have already fully watched this video**.
   **Add** stays grey until you do.

![Review & Add for Caminandes 3, with the YouTube description and the watched box](img/parent-review-and-add.webp)

The video then downloads in the background. On a Raspberry Pi 4 a short
cartoon is ready in a minute or two.

![Added: the video downloads in the background](img/parent-added.webp)

**Content** lists every video with how its download is going. The tabs
along the top show only those still waiting for your OK, still
downloading, failed or hidden.

![The Content page with six videos](img/parent-content.webp)

**Edit** on a video changes its title, description, picture or channel.
Sam shortened YouTube's long title to "Caminandes 2: Gran Dillama".

![Editing a video's title](img/parent-video-edit.webp)

## 4. Make a profile for each kid

Open **Kids → Create Profile**. Give the kid a name and a picture, then
choose:

- **A PIN**, if you want one: four to six numbers, or three pictures for a
  kid who doesn't read numbers yet. Leave it empty for no PIN.
- **Language** and **theme**: Playful, Calm, or Big & clear (larger text,
  stronger colours).
- **What they may do.** A new profile can watch and react, nothing more.
  Tick more when you're ready: a search box, comments, tags, their own
  channels and uploads, or asking for new videos.
- **Which channels they see**: all of them, only the ones you tick, or
  none of yours (only channels they make themselves).

![The profile form filled in for Mia: picture PIN, Playful theme, everything on](img/parent-kid-form.webp)

The **Kids** page sums up each profile. Mia has everything switched on,
Leo has the Portuguese Big & clear screen and sees only Cartoons, and
Lucía has the Spanish Calm screen with a number PIN.

![The Kids page with Leo, Lucía and Mia](img/parent-kids.webp)

**Set up the tablet:** on the kid's tablet, open **http://curatables.local/**
(or scan the QR code on this page with its camera) and add it to the
home screen (on an iPad, use Safari; see the
[FAQ](../faq.md#getting-connected)). To keep your kid inside it, use Guided Access on an iPad
or App pinning on Android; see the
[safety model](../safety-model.md#what-you-still-need-to-do-on-the-device).

## 5. Your kids ask for a video

A kid with **Ask for new videos** switched on can look for videos on
YouTube and ask for one. Nothing is downloaded until you add it. The
dashboard puts requests at the top, under **Needs your attention**.
Everything here works on your phone too.

![Video requests on a phone: Mia asked for Llama Drama](img/parent-requests-phone.webp)

**Review & add** opens the same Review & Add page as before, already
filled in with the name, description and channel your kid chose. Watch
it, change anything you like and add it. Your kid's request then says
**Yes! Watch**. **Not this one** turns it down, and that kid won't see
it in their searches again.

**Comments:** when a kid writes a comment, open the video from the
dashboard or **Content** to read it, reply or delete it. Your reply
shows to the kid as coming from "Parent".

![Replying to Mia's comment from a phone](img/parent-comments-phone.webp)

## 6. Day to day

The **dashboard** shows today at a glance: what each kid watched, new
comments and reactions, what was added, and anything that needs you
(requests, failed downloads, a drive that's getting full).

![The dashboard during the day](img/parent-dashboard.webp)

It works the same way on a phone.

![The dashboard on a phone](img/parent-dashboard-phone.webp)

**Stats** adds up what was watched today, in the last 7 days or ever,
for each video and each kid.

![The Stats page](img/parent-stats.webp)

## 7. Settings

**Settings** holds the choices for the whole box: storage and picture
quality, freeing up space when the disk gets full, updates, the rules
for video requests, importing from a drive, and your parent password.
Most families never need to change them.

![The top of the Settings page](img/parent-settings.webp)

## 8. Looking after the box

**Box** in the menu looks after the box itself. Its four pages are
explained in more detail in the
[parents' guide](../parents-guide.md#4-the-box-pages).

**Login, network & power** shows how the box is connected. It is also
where you switch Wi-Fi, change the box's Linux login password, turn SSH
on or off, and restart or shut down the box.

![Box: login, network and power](img/parent-box.webp)

**Restart the box** asks first, then the page waits and comes back by
itself, usually within a minute. Kids' tablets stay in their profiles.

![Restarting the box](img/parent-restarting.webp)

**Drives & backups** is where you add a USB drive for more videos. The
box also saves its settings, profiles and video list every night.
**Back up now** saves one straight away, and **Restore** puts a backup
back, after asking.

![Box: drives and backups, with one backup](img/parent-backups.webp)

**Updates** installs a new Curatables version, updates the video
downloader (try it first when YouTube videos stop downloading) and
installs system updates. Security updates install by themselves every
day.

![Box: updates](img/parent-updates.webp)

**Diagnostics** checks the box and says what to do about anything that
isn't right. **Download the diagnostics report** saves a text file you
can send to someone helping you; it leaves out passwords and keys.

![Box: diagnostics, all checks green](img/parent-diagnostics.webp)

If something's wrong, the [parents' guide](../parents-guide.md#7-somethings-wrong)
and the [FAQ](../faq.md) list the usual fixes.

## Credits

The videos in these pictures are free films, used under their licences:

- *Caminandes 1: Llama Drama*, *Caminandes 2: Gran Dillama* and
  *Caminandes 3: Llamigos* by Pablo Vazquez and the Blender Institute,
  [Creative Commons Attribution](https://creativecommons.org/licenses/by/4.0/).
- *Big Buck Bunny* by the Blender Foundation,
  [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/).
- *NASA | Tour of the Moon* by NASA's Goddard Space Flight Center.

The family is made up.
