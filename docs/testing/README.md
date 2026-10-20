# How Curatables is tested

Four layers, from seconds to hours. Each catches things the others
can't, so a change that touches what kids or parents see goes through
more than one.

| Layer | What it is | Runs | Catches |
|-------|-----------|------|---------|
| 1. Test suite | `pytest`, in `tests/` | every push (CI), ~13 min | logic, rules, routes, permissions, guard rules over the source |
| 2. Local demo | `scripts/seed_demo.py` + `run.py` on the laptop | by hand | how a page looks and reads, on phone and tablet sizes |
| 3. Virtual box | `scripts/qemu-box.sh`: the release image booted whole in QEMU, plus the box tests in `tests/box/` | before a release, after risky changes | what only the whole system shows: systemd, the helper, first boot, real uploads and downloads, many people at once |
| 4. Real box | the family's Raspberry Pi | release acceptance | Wi-Fi, drives, power, the real iPad and iPhone |

The release write-ups in this folder
([release-0.6.0-ux.md](release-0.6.0-ux.md),
[release-0.6.0-acceptance.md](release-0.6.0-acceptance.md)) are layer 4
runs, scripted by hand.

## 1. The test suite

```bash
.venv/bin/pytest -q -p no:cacheprovider     # everything CI runs
```

[CONTRIBUTING.md](../../CONTRIBUTING.md#running-the-test-suite) explains
the fixtures (CSRF stub, host check, fake system helper) and the guard
tests. Two groups matter most for the kid screens:

- **Kid behaviour** (`tests/test_kid_*.py`): Home, hide, download, PIN,
  customize, wishlist, uploads.
- **Special needs** (`tests/test_kid_special_needs.py`): how children
  with special needs, autistic children among them, use the screens.
  See [Special needs](#special-needs) below.

A bug found in layers 2 to 4 gets a test here before it is fixed, one
that fails on the old code. A race between two people becomes a test
that forces the bad timing (for example by making a folder vanish at the
moment it is read, as `test_a_video_deleted_while_its_player_loads_is_not_found`
does).

## 2. The local demo

```bash
.venv/bin/python scripts/seed_demo.py /tmp/demo
.venv/bin/python run.py --data-dir /tmp/demo --port 8090
```

Parent password `demo-password`; kids Ana (no PIN) and Ben (PIN 1234).
Look at every changed page at phone (375×667, iPhone SE) and tablet
(820×1180, iPad) sizes. Browsers on phones have overlay scrollbars, so
a desktop browser at 375 px wide has about 15 px less room than the
phone; emulate a mobile device (Playwright `is_mobile=True`) before
calling something cut off.

## 3. The virtual box

`scripts/qemu-box.sh` boots a release image (the latest from GitHub, or
`--image FILE`) as a whole arm64 machine; see
[pi-gen/README.md](../../pi-gen/README.md#trying-an-image-on-a-laptop-qemu).
Curatables is at <http://localhost:8080/>, SSH at port 2223 once it is
turned on during setup. `--fresh` starts over from a just-flashed card.

To test code that isn't in a release yet, deploy the local commits onto
the virtual box the way they go onto a real one (`PI_HOST=admin@127.0.0.1
PI_PORT=2223` for the deploy script).

What it can't show: Wi-Fi, USB drives, the Pi's power readings and
firmware updates (`rpi-eeprom-update` fails on every boot there), Pi
speed (it is several times slower), and anything about Safari on iOS.

### The box tests

`tests/box/` drives the virtual box (or any box) through a browser, like
people would. They need Playwright, kept out of the app's own
environment:

```bash
python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/python -m playwright install chromium
tests/box/make-clips.sh /tmp/clips
B=http://127.0.0.1:8080
/tmp/pw/bin/python tests/box/seed.py $B setup PARENT_PW ADMIN_PW   # both setup screens, SSH on
# (deploy local commits here, if any)
/tmp/pw/bin/python tests/box/seed.py $B content PARENT_PW /tmp/clips # six videos, kids Kiki and Ben
/tmp/pw/bin/python tests/box/chaos.py $B PARENT_PW 40 /tmp/chaos /tmp/clips
/tmp/pw/bin/python tests/box/personas.py $B /tmp/personas --idle-minutes 20
```

`seed.py content` makes Kiki (picture PIN frog-dog-cat, KLT trusted,
requests from it go straight in) and Ben (no PIN), both with every
feature on.

#### chaos.py: everyone at once, at random

A parent (phone size) and two kids (iPad size) use the box at the same
time for the given minutes. Each one, again and again, picks a random
link or form on the page it is on, fills every field with something
awkward (empty, 5,000 characters, emoji, HTML, quotes, right-to-left
marks, path tricks) and sends it; sometimes it double-taps, goes Back,
reloads, or opens an old address from earlier. Alongside, a hammer loop
signed in as Kiki downloads every video and fires Home adds, moves,
removes, hides and unhides as fast as it can, with bad positions and
unknown videos mixed in.

Every page is checked for:

| Finding | Meaning |
|---------|---------|
| `http-500` (any 5xx but 503) | the server crashed on that request |
| `oops`, `not-found`, `raw-error` | the kid or parent was shown an error page, or a bare error |
| `js-error` | the page's script threw |
| `empty-download`, `download-NNN` | a download came back empty or failed |
| `targeted-…-NNN` | the hammer loop got an unexpected answer |

Off limits, because they end the test rather than test the app: reboot,
shutdown, restarting the server or the network, Wi-Fi, drives, the
admin and parent passwords, updates (a yt-dlp update restarts the
server, which looks like the box dropping everyone), restoring a backup, moving the data
folder, the clock, Advanced settings (port and address), signing the
parent out, deleting kids, and the setup screens. Test those on purpose,
one at a time.

Output: `actions.log` (every step, findings marked `!!!`),
`findings.json` (each finding with the last six steps that led to it)
and `counts.json`.

#### personas.py: kids with special needs

One persona at a time, each acting out how some children use a tablet;
each prints PASS or FAIL and saves a screenshot of a failure. See
[Special needs](#special-needs). `pin_storm` runs last on purpose: it
leaves Kiki locked out from that address for a few minutes.

### Reading the results

1. **Server side first, always.** The browser only reports answers to
   pages that are still open: a background request (an upload
   finishing, a reaction) whose page the random person already left
   is never seen by `chaos.py`. The box's log is the complete record.
   Over SSH: `journalctl -u curatables -b | grep -E
   "Traceback|ERROR"` and the access log lines with a 5xx status. A 500
   always comes with a traceback there, and the request id in brackets
   ties it to the access line.
2. **Each finding's trail** in `findings.json` shows what that person did
   just before. Many `not-found` findings are expected: the parent
   deleted a video another tab still showed. A finding is a bug when the
   page a person lands on is an error page instead of a calm "not here"
   with a way home, or when the server crashed.
3. **Reproduce in layer 1.** Write the failing test, fix, and check the
   test fails on the old code (`git stash push app/...`).
4. **Re-run** the same chaos minutes on a fresh box with the fix
   deployed.
5. **Check the data afterwards:** the database (`PRAGMA integrity_check`,
   `PRAGMA foreign_key_check`) and that the parent's pages still list
   what the kids see.

## Special needs

Curatables is made first for a child with special needs. These are the
behaviours the tests act out, and what each must hold:

| Behaviour | What must hold | Where |
|-----------|----------------|-------|
| The same tap, over and over (a reaction, Put on my Home, Hide/Undo, Ask) | it settles: one reaction, one spot on Home, one hidden row, one request; never an error or a pile of duplicates | `test_the_same_tap_over_and_over_settles`, `test_asking_for_the_same_video_again_and_again_makes_one_request`, `repeat_tapper` |
| Routine: Home arranged just so | new videos never shift an arranged Home | `test_an_arranged_home_stays_put_when_new_videos_arrive` |
| Getting lost | Home is the first button on every kid page, in exactly the same spot | `test_home_is_the_first_button_on_every_kid_page`, `same_place` |
| Something unexpected (a deleted video, an old tab, a wrong address) | a calm page with no codes or technical words, and a big Home button | `test_something_unexpected_is_calm_and_has_a_way_home` |
| A quick finger on the PIN pictures | a few wrong tries lock that profile on that tablet only, with "Wait a little", no blame, and Back | `test_wrong_pictures_lock_kindly_and_only_that_profile`, `pin_storm` |
| Sensitivity to sound | a video never starts by itself | `test_videos_wait_for_a_tap_to_start` |
| Sensitivity to motion | Reduce Motion stops every animation and transition | `test_reduced_motion_stops_every_animation`, `still` |
| Flashing | nothing repeats faster than about three times a second; no blink or marquee | `test_nothing_on_kid_screens_flashes_fast` |
| Watching one favourite again and again | it ends and replays every time, many times | `replayer` |
| Back held down, history exhausted | never an error page, never the parent login | `back_masher` |
| Opening video after video without waiting | the last one opens normally | `rapid_switcher` |
| Big text or 200% zoom, less precise fingers | no sideways scrolling; buttons at least 44×44 px | `big_zoom` |
| Keyboard mashing, misspellings, emoji in search | no errors | `key_masher` |
| A page left open a long time | still signed in when they come back | `long_pause` |

## What none of this catches

- **Safari on iPad and iPhone:** cookie settings, the home-screen icon,
  Screen Time, Reduce Motion as the iPad applies it. Only the real
  devices show these (the "4-digit PIN" that was really Screen Time, the
  sign-in loop with cookies blocked).
- **Hardware:** Wi-Fi, USB drives, power cuts, a Pi's speed and heat.
- **Judgement:** whether a page is clear to a child, whether a video
  suits them, how loud it is. A person looks at screenshots of every
  changed page at phone and tablet size.

## Making the tests better

- Every bug a person finds on a real box: ask what the box tests would
  have needed to find it, and add that (a new persona, a new field
  value, a new check).
- `chaos.py` follows links at random, so the parent soon changes which
  channels each kid can see and the kids end up with little to do.
  Weighting the choices, or restoring the kids' channels every few
  minutes, would keep both sides busy.
- Add personas as we learn how children use it: two kids on one tablet
  taking turns, a kid holding a finger down (long press), a tablet
  rotated mid-video, Guided Access on.
- Run the personas on WebKit too (`pw.webkit`), the nearest Playwright
  gets to Safari.

## Findings so far

| Found by | What | Fixed in |
|----------|------|----------|
| Real box logs | an emptied kid description came back as the original | 3166962 |
| Real box logs | reloading the PIN page showed the "Oops" page | 3166962 |
| Real box logs | a paused download was logged as a failure, "the drive is full" | 3166962 |
| Virtual box, first boot | `systemd-networkd-wait-online` failed: the QEMU kernel line had `rw`, which let systemd's first-boot presets turn on networkd | 3d6df8b |
| chaos.py | a video deleted while a kid's player loaded it crashed the request (500) | 61ead84 |
| chaos.py, from the box's log | the same file sent twice at once (a double-tapped Send, or a parent and a kid sending the same clip) was stored twice over itself; a take-back in between crashed the second (500) | this change |
| Special-needs tests | the edit page of a deleted video crashed (500); any page now answers "not found" | 4bbce56 |
