# Filtered Search and Real-Time YouTube Proxy — Design Notes

**Date:** 2026-04-10
**Status:** Exploratory design. No code yet. Intended as the conceptual companion to `search-research-playlists-at.md`, which covers the URL-grammar side of the same feature.
**Scope:** Two related features that share one filter pipeline — (1) a kid-facing wishlist search, (2) a longer-term real-time filtered YouTube proxy.

---

## 1. Motivation

Today, Curatables is a pull-only library: parents add videos, kids watch what has been added. That model keeps kids safe but also keeps them passive. They cannot express "I want to watch videos about volcanoes" without telling a parent out loud, which means the parent becomes the bottleneck for every curiosity.

At the same time, Curatables sits in an unusually privileged position:

- The machine running it already has internet access and `yt-dlp`.
- The kid accesses Curatables over the local network, not over the public internet.
- Curatables owns both sides of the pipe: it sees every query, and it serves every byte the kid watches.

That combination unlocks two features no hosted YouTube frontend could build:

1. **Wishlist search** — the kid searches, Curatables filters the results, the kid browses thumbnails and "asks for" videos; the parent approves or rejects from an inbox.
2. **Real-time filtered proxy** — the kid searches, Curatables filters, and the kid watches immediately, because `yt-dlp` fetches on demand and Curatables re-serves the stream from `localhost`.

Both features share one filter pipeline. This document describes the pipeline, the two feature modes, the architecture fit, and the risks.

The reframing that matters: **this is not "filtered YouTube." It is a curated video library with a discovery mechanism, where YouTube is the current supply.** That framing keeps Curatables platform-agnostic (per the project rename), keeps the family's library as the durable artifact, and keeps the legal story clean — we're helping a parent build a local library, and YouTube happens to be one source.

---

## 2. Two modes, one pipeline

### 2.1 Mode A — Wishlist search (kid → parent → library)

**Flow:**

1. Kid opens `/kid/search`, types a query (or picks from a parent-approved topic chip).
2. Curatables runs the query server-side through the filter pipeline.
3. Kid sees a grid of thumbnails + titles that passed all filter layers.
4. Kid taps a video: it is added to their personal wishlist with status `pending`.
5. Parent sees a "requests" inbox on `/parent/requests` with the kid's pick, a one-line LLM rationale ("passed: educational, age-appropriate, allowlisted channel"), and Approve / Reject buttons.
6. Approve → existing download flow runs; the video appears in that kid's library next session. Reject → video is remembered as "rejected for this kid" so it does not re-surface.

**Why this comes first:**

- Zero streaming infrastructure. Reuses the existing download-and-serve flow.
- No latency pressure: filtering can take 5 seconds and nobody notices, because the kid is browsing.
- Produces real-world filter-quality data before committing to Mode B.
- Gives parents immediate visibility into *what their kid is curious about* — which is arguably the most valuable output of the whole feature.

### 2.2 Mode B — Real-time filtered proxy (kid → watch now)

**Flow:**

1. Kid searches (same UI as Mode A, plus a per-kid "direct watch" toggle the parent controls).
2. Filter pipeline runs; on each result card, a green "Watch now" button appears only if the video passed the *strict* variant of the pipeline (see §3.5).
3. Kid taps Watch now: Curatables asks `yt-dlp` to start downloading into its cache directory and begins serving bytes to a plain `<video>` tag as soon as enough has landed.
4. Playback flows through `http://curatables.local/stream/<video_id>`. No HTTPS, no CORS, no mixed-content, no DRM.
5. Background: transcript check (§3.6) runs; if it flags the video mid-play, playback is pulled and the parent gets a notification.
6. Cache governance: the rolling cache directory is governed by the existing disk-quota code (`de7c4f3`). Videos watched more than N times get promoted to the permanent library automatically (with a parent notification).

**Why this is uniquely possible for Curatables:**

- `yt-dlp` + existing `curl_cffi` impersonation already solves the hard part (fetching YouTube media).
- Local re-serving kills the HTTPS/CORS/DRM wall that blocks most "YouTube filter proxy" attempts. The browser only ever talks to Curatables over `http://local`.
- The kid UI already targets old Safari (iOS 9, no `fetch`, no ES6). That constraint becomes a *feature*: serve plain `<video>` with a direct MP4 URL from our own box. A hosted frontend could not do this.

### 2.3 Why one pipeline, not two

Mode A and Mode B ask the same question ("is this video okay for this kid right now?") and differ only in what they do with a "yes." Separating them into two filter stacks would double the maintenance and let the two drift out of sync — a recipe for a video being safe to *request* but not safe to *watch*, which is exactly backwards. The pipeline should be single-sourced and return a verdict object rich enough that both modes can make their own decisions from the same result.

---

## 3. The filter pipeline

Layers are ordered cheapest → most expensive, so expensive checks only run on candidates that survived the cheap ones. Each layer caches its verdict in SQLite keyed by `video_id` (and by policy version, so policy changes invalidate cleanly).

### 3.1 Layer 1 — Hard blocklist

- Banned keywords in the query itself (reject the query before it even hits YouTube).
- Banned channel IDs.
- Banned video IDs (both "previously rejected for this kid" and "globally banned for all kids").

O(1) lookups, no network, no LLM. Nothing ever gets past this layer for free.

### 3.2 Layer 2 — Allowlist shortcuts

If the video's channel is on the parent's approved-channel allowlist for this kid, skip straight to verdict = pass (optionally still running Layer 3 duration checks). Huge cost savings for the "kid always watches Channel X" case, which is the common case in practice.

### 3.3 Layer 3 — Metadata heuristics

Everything we can decide from the `yt-dlp --flat-playlist --dump-json` output with no further I/O:

- Duration bounds (per-kid max length).
- Age-restricted flag.
- Live-stream flag.
- Category.
- Language (if detectable from metadata).
- Upload date (per-kid min/max freshness).
- View count floor or ceiling if the parent wants to filter out "too obscure" or "too viral" content.
- Premiere / upcoming state (reject).

Still effectively free. Catches a surprising amount.

### 3.4 Layer 4 — LLM text classifier

Input: title + description + tags + channel name + duration + any metadata flags from Layer 3.

Prompt shape: *"Given this child's profile and policy P, is this video appropriate? Return {verdict, confidence, one-line reason}."* Use a small/cheap model (Haiku-class or a local model — see §7). Cache verdict per `(video_id, policy_version)` forever, because the metadata does not change.

This is the layer that earns its keep. It catches the videos that pass keyword checks but would still make a parent uncomfortable — clickbait titles, channels that mix innocent and inappropriate content, videos targeted at adults that happen to mention kid topics.

### 3.5 Layer 5 — LLM vision classifier on thumbnail

Catches clickbait / gore / sexualized thumbnails that pass text. More expensive, so only runs on candidates that passed Layer 4. One call per thumbnail, cached per `video_id`.

The "strict" variant required for Mode B's "Watch now" button requires passing this layer. Mode A can make Layer 5 optional and let the verdict remain "needs parent review" if Layer 4 passed but Layer 5 was skipped.

### 3.6 Layer 6 — Transcript sweep (async, belt-and-braces)

For Mode B, or for already-approved videos before they are added to the permanent library, pull captions via `yt-dlp --write-auto-sub --skip-download`. Sample or full-scan with an LLM. Can run *after* the kid starts watching in Mode B, and yank playback mid-stream if it fails — unpleasant but better than the alternative.

Transcripts are also the gateway to the "kid learning insight" features in §5, so this work pays for itself twice.

### 3.7 Per-kid policy as free-text prompt

Every kid record grows a `filter_policy_prompt` field — a free-text paragraph the parent writes in English:

> *"No violence, no scary thumbnails, OK with mild cartoon peril. Maya is 7 and loves Lego, animals, and crafts. She gets nightmares from realistic animal-attack content. No unboxing videos (parent aesthetic preference, not a safety thing)."*

That paragraph goes straight into the Layer 4 and Layer 5 prompts. **Parents write the filter in English.** This is the killer feature: it replaces a brittle taxonomy of categories with the one interface that actually captures a parent's judgment. It also makes the filter's decisions auditable in plain language ("rejected because the prompt says no realistic animal-attack content and the description describes a shark bite").

### 3.8 Verdict object

Every pipeline run returns one object:

```
FilterVerdict {
  video_id
  status: pass | needs_review | reject
  strict_status: pass | reject         # whether Mode B's "Watch now" should light up
  layers_run: [hard_block, allowlist, metadata, llm_text, llm_vision, transcript]
  layer_results: { ... per-layer booleans and reasons ... }
  reason: "one-line explanation for parent UI"
  policy_version: integer
  cached_at: timestamp
  model_version: string                # so policy/model upgrades invalidate cleanly
}
```

Both modes consume the same object and decide what UI to render.

---

## 4. Architecture fit

Curatables already has the right shape for this. Mapping to `architecture.md`:

### 4.1 New services

- **`services/search.py`** — query + filters → filtered result list. Calls the existing `yt-dlp` wrapper with the URL built by the pure-function module from `search-research-playlists-at.md` §4.3.
- **`services/content_filter.py`** — video metadata → `FilterVerdict`. Orchestrates layers 1-5, short-circuits on failure, writes cache entries.
- **`services/stream_proxy.py`** (Mode B only) — wraps `yt-dlp` download-to-disk-then-serve. Handles the rolling cache, coordinates with the disk-quota guard (`de7c4f3`), and manages concurrent watchers of the same video.

### 4.2 New repos

- **`repos/filter_cache.py`** — `(video_id, policy_version) → FilterVerdict`. New SQLite table. This is the table that makes Mode B affordable.
- **`repos/wishlist.py`** — `(kid_id, video_id) → {status, requested_at, decided_at, decided_by}`. Status machine: `pending → approved | rejected`.
- **`repos/filter_policy.py`** — versioned per-kid policy prompts. Bumping the policy invalidates filter cache entries under the old version, which is why the cache key includes `policy_version`.

### 4.3 New routes

- **`routes/kid.py`** gains `GET /kid/search`, `POST /kid/wishlist/request`, `GET /kid/wishlist` (their own pending/rejected list), and for Mode B `GET /stream/<video_id>`.
- **`routes/parent.py`** gains `GET /parent/requests` (inbox), `POST /parent/requests/<id>/approve`, `POST /parent/requests/<id>/reject`, `GET/POST /parent/kids/<id>/policy` (edit the free-text prompt), and `GET /parent/insights` (see §5).

### 4.4 New templates

- **`templates/kid/search.html`** — query form + results grid. Must target old Safari (no `fetch`, no ES6). Plain `<form method="get">` and full-page navigation for results. Each result is a card with a thumbnail, title, duration, and a single big "Ask to watch" button (Mode A) or "Watch now" button (Mode B, only when `strict_status = pass`).
- **`templates/parent/requests.html`** — inbox with thumbnail, one-line LLM rationale, Approve / Reject.
- **`templates/parent/kid_policy.html`** — a textarea, a character count, a "this took effect at [timestamp]" line, and a link to browse past verdicts under the previous policy.

### 4.5 Existing components reused

- `yt-dlp` wrapper (no changes).
- `curl_cffi` browser impersonation for anti-bot (no changes).
- Download flow, for approved wishlist items (no changes).
- Disk-quota guard (`de7c4f3`) for Mode B's cache directory. This component was prescient for exactly this.
- Kid-PIN and session code for gating `/kid/search`.

The theme: **Mode A adds services, repos, routes, and templates. Mode B adds one more service (`stream_proxy`) and one more route (`/stream/<id>`). Everything else is the filter pipeline, which is single-sourced.**

---

## 5. Capabilities unlocked

Worth listing explicitly because they change the product, not just the feature set.

### 5.1 Curatables as the recommender

YouTube's "related videos" rail is the main rabbit-hole risk. Once Curatables mediates search, it can replace the rail with its own: "more from allowlisted channels on similar topics, filtered through the same pipeline." Curatables becomes the recommender, YouTube is just the content pool.

### 5.2 Search history as parent insight

Every kid search is a signal about what they're curious about. `/parent/insights` can surface: *"this week Maya searched for 'dinosaurs' 14 times — here are 5 pre-vetted dinosaur channels you could allowlist with one click."* This is parenting data that YouTube would never give a parent, and it comes for free from the query log.

### 5.3 Attention and time budgets

Because playback flows through Curatables (Mode B) or the library (Mode A), the server can enforce:

- *"30 minutes of new videos per day, unlimited re-watches of already-approved favourites."*
- *"No search after 19:00 — bedtime routine starts."*
- *"After 3 wishlist requests today, the fourth has to wait until tomorrow."*

These feel to the kid like natural constraints of the app, not punishments — which is the right framing.

### 5.4 Transcripts → learning

Once Layer 6 runs, Curatables has transcripts for everything the kid has watched. That unlocks:

- *"What did Maya learn about volcanoes this month?"* as a real query.
- Per-kid topic summaries the parent can read.
- Spaced-repetition quiz generation from watched content (optional, but obvious follow-on).

This is big alignment with the `project_vision.md` "LLM-ready" goal — the transcripts are the corpus.

### 5.5 Multi-kid personalization

Same video, different verdicts per kid, because the LLM prompt includes each kid's policy and profile. Already supported by the data model once `filter_policy_prompt` is per-kid. No extra architecture.

### 5.6 Offline mode

Mode B with aggressive caching effectively *is* an offline mode. Library + cache + "queued for next time online" wishlist = Curatables works on a plane or in a cabin. Differentiator for families who travel.

---

## 6. Risks and sharp edges

Name them now so the design goes in eyes open.

### 6.1 The LLM is a filter, not a safety system

False negatives are inevitable. The honest framing is *"better than unfiltered YouTube, not a substitute for parental attention."* Concretely:

- Always show a one-tap "report this" button on every result card and every playing video. Reports feed straight into the per-kid and global blocklists. This is also free training data for tuning the prompt.
- Never market Mode B as "safe search." Market it as "parent-trusted search."
- Never autoplay a video the kid has not explicitly tapped. Autoplay is where filter mistakes turn into harm.

### 6.2 Cost creep in Mode B

Every search and every playback touches an LLM unless caching is aggressive. Mitigations:

- `filter_cache` keyed by `(video_id, policy_version)` — a hot library hits the LLM approximately zero times after the first week.
- Local small model for Layer 4 text (Llama 3 8B on a modest GPU, or even a classifier-only model for the first-pass). Reserve API calls for Layer 5 vision.
- Per-parent monthly spend cap with a visible meter. If the cap is hit, Mode B degrades to Mode A until next cycle.

### 6.3 YouTube ToS and rate limits

Re-serving content at household scale (one family, a handful of users) is indistinguishable from personal use — the same as running an ad blocker or a DVR. Fine.

What is *not* fine:

- Pointing Curatables at a classroom without thinking through ToS.
- Letting `yt-dlp` request volume scale linearly with playback. Cache hard.

Practically: `yt-dlp` + `curl_cffi` will eat occasional captchas. Graceful degradation is "YouTube is asking us to verify — ask a parent," not a crash.

### 6.4 Sibling-of-a-banned-video problem

Kid finds a channel they like; one video on that channel fails the filter; they now know the channel exists and can keep probing. Mitigation:

- Two filter rejections from a channel within a rolling window auto-quarantines the whole channel pending parent review. Parent gets a notification with the two offending videos and one-click "ban this channel for all kids."
- A quarantined channel stays visible in existing library entries (parents already vetted those) but disappears from search results.

### 6.5 Latency budget for Mode B

First-play of a new video is `yt-dlp` resolve + Layer 4-5 filter + download-start. That is 3-8 seconds in practice. Kids will tolerate it if the wait is presented as *"checking this video is okay…"* with a small animation — make the wait part of the story, not a bug. Library replays and cached results should be instant.

### 6.6 Bypass via pasted URLs

A friend sends the kid a link. If Curatables has any place a kid can paste a URL, it must go through the *same* filter pipeline as search. This is a design rule, not a feature: **every path to a video passes through `content_filter.py`.** There is no back door.

### 6.7 Policy version drift

When a parent edits the free-text policy, what happens to already-approved wishlist items, already-cached filter verdicts, and already-downloaded library videos?

- Cached filter verdicts: invalidated silently. Next access re-runs the pipeline under the new version.
- Approved wishlist items in flight: respect the *version the parent approved under*. The parent's click is the authoritative signal; re-filtering behind them would be confusing.
- Library videos already downloaded: not auto-removed. Parent gets a one-time "your new policy flags these N existing videos — review?" prompt.

This is fiddly but exactly the kind of detail that decides whether parents trust the feature.

---

## 7. Open questions

1. **Which LLM, where?** Cloud (Haiku-class, per-request cost, better quality) vs. local (one-time hardware cost, slower, privacy). Probably hybrid: local for Layer 4, cloud for Layer 5 vision. Needs a prototype to compare.
2. **Vision model for thumbnails — which one?** Haiku vision is cheap and good enough based on priors, but needs validation on a sample of borderline thumbnails before committing.
3. **Sort order in the kid UI.** YouTube's "relevance" is the default. Do we expose "newest" too, or is that a rabbit-hole risk? (Lean: no sort controls for kids, parent configures a default per kid.)
4. **Rate-limit model.** Per-kid per-day? Per-parent? Global across the household? Needs a number.
5. **Can a kid see their own *rejected* wishlist items?** Pro: transparency, they learn the policy. Con: turns rejections into a "how do I word it differently?" game. Lean: show "rejected" with no reason text — they know it was asked, they don't see why.
6. **Mode B default.** Ships off, parent opts in per kid? Or ships on with the "Watch now" button gated behind strict-pass? Lean: off by default, opt-in per kid after they've built trust with Mode A.
7. **Non-YouTube sources.** Curatables is meant to be platform-agnostic. Does the pipeline work unchanged for other sources `yt-dlp` supports? (Probably yes for Layer 1-4; Layer 5 and 6 depend on per-source metadata availability.)
8. **What happens when the kid's wishlist has 200 items?** UX, not just storage. Parent needs a way to bulk-triage.

---

## 8. Recommended build order

Not a plan of record — a suggested sequence that front-loads learning and back-loads the ambitious infrastructure.

1. **Filter pipeline layers 1-3** (hard blocklist + allowlist + metadata). No LLM yet. Ship behind a feature flag in the parent UI as a "try this query" tool so the parent can tune blocklists against real queries.
2. **`filter_cache` table + `FilterVerdict` object.** Wire through layers 1-3. Cache works end-to-end before LLMs enter the picture.
3. **Mode A wishlist** using only layers 1-3. Real kid traffic, real parent approvals, real data about what the filter misses.
4. **Layer 4 LLM text classifier** with the free-text policy prompt. A/B it against the non-LLM baseline on the same queries. Tune until parents trust it.
5. **Layer 5 vision classifier.** Only after Layer 4 is trusted — otherwise it's impossible to tell which layer is making decisions.
6. **`/parent/insights` dashboard.** Turns the search history that has been accumulating since step 3 into something the parent values.
7. **Mode B stream proxy.** Only after the filter has been trusted for weeks and the cache hit rate is well understood. This is the most expensive step and the one that benefits most from real usage data.
8. **Layer 6 transcript sweep + learning insights.** Lowest priority because its value is additive, not foundational.

The sequence is designed so that every step ships something useful on its own, and every later step benefits from data collected by earlier steps.

---

## 9. Relationship to other docs

- **`search-research-playlists-at.md`** — covers the URL-builder side (how to *express* a filtered YouTube search). This doc picks up where that one ends, at the moment `yt-dlp` returns a result list.
- **`open-source-media-center-features.md`** — context for what Curatables competes with and where this feature sits in that landscape.
- **`architecture.md`** — the service/repo/route boundaries referenced in §4.
- **`project_vision.md` (memory)** — the "LLM-ready" and "shared curation" goals that this feature advances.
- **`feedback_web_only_ui.md` (memory)** — the constraint that kids never leave Curatables. This feature is the first one where that constraint actively *enables* the design (local `<video>` re-serving) rather than just restricting it.

---

## 10. Summary

Curatables is uniquely positioned to ship a filtered YouTube experience for kids because it already owns both ends of the pipe: the search query and the served bytes. The design here is one filter pipeline with two consumers — a low-risk wishlist feature that ships first and generates the data needed to trust the pipeline, and a higher-ambition real-time proxy that turns Curatables into a family-safe YouTube frontend running on the household's own box.

The killer feature is **parents writing the filter in English**, via a free-text per-kid policy prompt fed straight into the LLM layers. It replaces a brittle taxonomy with the one interface that actually captures parental judgment.

The reframing that protects the product: **this is a curated library with a discovery mechanism. YouTube is the current supply, not the product.**
