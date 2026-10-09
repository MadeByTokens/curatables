# Offline Web Pages — Future Work Notes

**Date:** 2026-07-17
**Status:** Idea capture only. No code, no commitment, no milestone. Written so we
don't forget the prior art.
**Prompt:** if Curatables ever expands from "a curated *video* library" to "a curated
*content* library", web pages are the obvious second content type — and
[kage](https://github.com/tamnd/kage) already solves the hard part.

---

## 1. Why this fits Curatables

Curatables already is a local-first library with a parent-curates / kid-consumes
split: a parent pastes a URL, the server fetches and normalizes the content, stores
it locally, and re-serves it to LAN devices that never touch the origin. The kid
device makes no outbound request, sees no ads, and cannot wander off.

Every one of those properties is content-type agnostic. Swap "video" for "web page"
and the same shape holds: a parent pastes an article, a Wikipedia page, a tutorial,
a recipe; the server snapshots it; the kid reads it offline from
`curatables.local` with no tracking, no comment section, and no link out to the
open web. It is the same trust boundary and the same UX, applied to reading instead
of watching.

The natural pitch: **yt-dlp is our video supply; something kage-shaped would be our
web-page supply.**

## 2. What kage is

kage (影, "shadow") is an MIT-licensed Go CLI (~2.8k stars as of July 2026) that
clones a website into a folder you can browse offline **with all JavaScript
stripped out**. Its pipeline:

```
seed URL ─▶ headless Chrome ─▶ final DOM ─▶ strip JS ─▶ localise assets ─▶ disk
              (render)          (snapshot)   (sanitize)   (rewrite links)
```

It renders each page in real headless Chrome, waits for it to settle, snapshots the
DOM a human would have seen, deletes every script, and rewrites CSS/image/font
references to local paths. The result is plain `.html` on disk that renders like the
live site and executes no code.

Commands: `kage clone <url>`, `kage serve <dir>`, `kage pack <dir>`,
`kage open <file.zim>`.

**On reuse.** kage is MIT-licensed, so we can ship it, vendor it, or adapt its code
outright — the licence asks for the copyright notice to travel with it, which is a
condition we should meet gladly rather than work around. If any of this ever becomes
real, credit kage by name in the docs and keep its `LICENSE` alongside whatever we
reuse. Where we only take the *approach* rather than the code, saying so publicly
still costs nothing and is the right thing to do.

## 3. The ideas worth borrowing

Ordered by how directly they map onto Curatables.

### 3.1 Render-then-strip, rather than fetch-then-hope

"Save As" on a modern page yields a spinner that never resolves, because the page was
a thin client for someone else's JavaScript. kage's answer — let a real browser
finish the work, snapshot the *result*, then remove the scripts — is exactly the
same move Curatables already makes for video with the ingest normalization pipeline:
**normalize the content at ingest so the client device needs nothing special and
nothing dynamic.** A JS-free snapshot is the reading equivalent of "H.264/AAC ≤720p30
+faststart": it plays on a 2015 tablet and on Safari/iOS 9, which is a hard
constraint we already honour.

It also happens to be a security win that lines up with
[threat-model.md](../threat-model.md): a stripped snapshot cannot phone home, cannot run
a tracker, and cannot execute anything on the kid device. Curated web pages that
still ran arbitrary origin JS would blow a hole through the property that makes the
kid side trustworthy today.

### 3.2 Sanitization as a distinct, testable stage

kage splits the work into single-concern packages — `browser/` (drive Chrome, snapshot
DOM), `sanitize/` (strip scripts, inline handlers, `javascript:` URLs), `asset/`
(download and localise CSS/images/fonts), `urlx/` (deterministic URL→path mapping).
The sanitizer being its own stage with its own tests is the part worth following: for us
"what exactly did we strip, and can we prove it" is a safety claim we'd need to be
able to defend, not an implementation detail.

### 3.3 ZIM as the storage/interchange format

`kage pack` collapses a mirror into a single [ZIM](https://wiki.openzim.org/) file —
the open, documented, compressed, indexed archive format behind
[Kiwix](https://kiwix.org) (Wikipedia, Stack Overflow, Gutenberg, offline, on a boat).
Attractive for us because:

- **One file per curated item** beats thousands of little files for backup
  ([backup.md](../backup.md)), disk-quota accounting, and the storage report.
- **Not our invention.** A ZIM written today opens in any ZIM reader years from now,
  so a family's library survives Curatables. That is the same reasoning behind our
  `.ytc` export existing at all.
- **Ecosystem for free.** `kiwix-serve`, Kiwix desktop, Kiwix Android/iOS. And the
  inverse is interesting: Kiwix already publishes curated ZIM packs (Wikipedia for
  Schools, Wiktionary, TED). *Importing* an existing ZIM might be a cheaper first
  feature than cloning the live web at all — a parent adds "Wikipedia for Schools" to
  the library in one step, and the kid browses it offline.
- Caveat noted in kage's own README: it writes valid ZIM with standard metadata but
  **does not build the full-text search index** Kiwix's own packs ship with, so
  in-reader search is limited. If we ever wanted our curated search
  ([filtered-search-and-proxy.md](filtered-search-and-proxy.md)) to cover pages, we'd
  index the text ourselves in SQLite rather than lean on ZIM search.

### 3.4 Deterministic, idempotent, resumable crawling

Details worth remembering because our ingest has the same problems: each page is keyed
by the file it writes, so the same page reached via http/https, with or without a
trailing slash, is fetched exactly once; Ctrl-C saves crawl state and a re-run
resumes; `--refresh` re-renders in place; packing is deterministic (archive UUID
derived from content, not randomized) so the output is safe to checksum and cache.
Curatables' dedup and crash-recovery story wants the same discipline
([architecture.md](../architecture.md)).

Also polite by default: reads `robots.txt`, seeds from `sitemap.xml`, stays on the
seed host, honours `Crawl-delay`. If we ever crawl on a parent's behalf from their
home IP, defaults like these are not optional.

### 3.5 Packaging tricks (interesting, probably not for us)

`kage pack --format binary` glues the archive onto a copy of kage to produce a single
executable that serves the site; `--app` wraps it as a macOS `.app` / Linux
`.AppImage` with the site's favicon as the icon; a `webview` build tag opens it in a
native OS window instead of a browser tab. Filed here because our
[Roadmap → Future](../../README.md#roadmap) bucket has an AppImage/desktop-packaging item
and kage's approach (cross-compile by pointing `--base` at another platform's binary,
read the executable header to infer the target) is a worked example. Not a
content-side idea.

## 4. Where it would land in our architecture

Sketch only — this is not a design doc.

- **Ingest.** A page snapshotter would be a sibling of the yt-dlp downloader inside
  the ingest normalization pipeline, not a new subsystem: fetch → normalize (render +
  strip) → store → surface for approval. The existing *fetch → preview → confirm*
  flow already fits ("here's what this page will look like once frozen — add it?").
- **Storage.** One ZIM (or one snapshot dir) per curated page, under the data dir,
  counted by the disk-quota guard and the storage report, covered by backup/restore,
  eligible for cache expiry and "keep forever".
- **Serving.** A static handler over the snapshot, same-origin under
  `curatables.local`, with the same per-profile restrictions, tags, reactions, and
  comments the video side already has. Reading time logged like watch time.
- **Kid UI.** A "read" card alongside the video grid. No outbound links that escape
  the library — links to pages we did not snapshot need a policy (rewrite to dead,
  or to a "ask a parent for this" request, tying into the wishlist idea in
  [filtered-search-and-proxy.md](filtered-search-and-proxy.md)).

## 5. Open questions / risks

- **Dependency weight.** kage needs Chrome/Chromium on the host. Curatables' bill of
  materials ([dependencies.md](../dependencies.md)) is deliberately lean, and headless
  Chrome on a Pi 4 is not free — CPU, RAM, and a large apt footprint on the appliance
  image. Cheaper snapshotters exist (readability-style extraction, `monolith`,
  single-file) at the cost of fidelity on JS-heavy pages. A "reader mode" extract may
  actually be *better* for kids than a pixel-faithful clone.
- **Go vs Python.** kage is a Go binary. Adopting it means shipping another system
  binary (like `yt-dlp`/`ffmpeg` — precedent exists, and its container image bundles
  Chromium) rather than importing a library. Alternatively: take the *design* as
  inspiration and implement it in Python.
- **Scope creep.** `kage clone` mirrors a whole site. Curatables curates *items*. The
  interesting mode is single-page (`--max-pages 1 --max-depth 0`), not a site mirror.
  A parent cloning a whole domain onto a Pi is a disk-quota incident waiting to
  happen.
- **Legal/ToS.** Same posture as video: a parent making a private local copy of a page
  they can lawfully read, for their own household, not redistributed. Crawl politeness
  defaults matter for keeping that story clean.
- **Freshness.** A frozen page is stale by definition. Fine for an essay, wrong for
  anything time-sensitive. `--refresh`-style re-snapshot would need a UI story.

## 6. Links

- kage — https://github.com/tamnd/kage (MIT, Go). Docs: https://kage.tamnd.com
- ZIM format — https://wiki.openzim.org/
- Kiwix (readers + curated ZIM library) — https://kiwix.org/
