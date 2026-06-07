# Search Feature Research — playlists.at/youtube/search/

**Date:** 2026-04-10
**Purpose:** Research notes for a future "search YouTube from the parent UI" feature in Curatables. Based on reverse-engineering [playlists.at/youtube/search/](https://playlists.at/youtube/search/) via Playwright (DOM + inline script inspection).
**Primary source:** the inline `<script>` block captured at the time of this research is checked in verbatim at [`research/playlists_at_script.js`](research/playlists_at_script.js) — if the live site changes, that file is the snapshot the tables below were derived from.

---

## TL;DR

`playlists.at/youtube/search/` is a **pure client-side URL builder**. There is no backend, no API, no scraping, no proxy. The entire "advanced search" is one `performSearch()` function in inline JavaScript that assembles a URL from form inputs and calls `window.open(searchUrl, '_self')`, handing the user off to YouTube's (or Google's) own results page. The value it provides is knowing **which query operators and `sp=` protobuf tokens** to stitch onto the URL.

For Curatables, this is useful in two very different ways:

1. **Cheap path:** mirror the URL-builder approach and open a new tab to YouTube. Zero scraping, zero backend load. Parents leave Curatables briefly to pick videos, then paste URLs back.
2. **Proper path:** do the search ourselves on the server, using `yt-dlp`'s `ytsearch` extractor (or the query string format), render results inside the parent UI, and let parents select without leaving Curatables. More work, better UX, respects the "web-only UI" principle.

The playlists.at research matters most for the **proper path**, because the filter grammar and `sp=` tokens it documents are directly reusable as `yt-dlp` query strings.

---

## 1. How playlists.at actually works

### 1.1 Architecture

- Single static HTML page (~39 KB).
- One inline `<script>` block, no external JS bundles, no framework.
- State kept in three module-level vars: `selectedVideos`, `searchResults`, `currentSource` (`'youtube'` or `'google'`).
- Advanced filter UI is two template-literal HTML strings (`youtubeFilters`, `googleFilters`) injected into `#filtersGrid` when the source dropdown changes.
- On submit: builds a URL string, calls `window.open(searchUrl, '_self')`. That's it.

### 1.2 The `performSearch()` function in one sentence

Read each filter input, concatenate the relevant YouTube/Google search operator onto the query, optionally append a `sp=` or `tbs=` encoded filter param, then navigate.

### 1.3 No result rendering

The UI has scaffolding for `selectedVideos`, `updatePlaylistBar`, `openPlaylist` etc., but on the search page there is no results grid — selection only happens on the separate `/youtube/` playlist-creator page. The search page's only job is to redirect.

---

## 2. URL grammar (the actually useful part)

### 2.1 YouTube — global search

**Base:** `https://www.youtube.com/results?search_query=<query>`

All operators are appended to `search_query` using `+` as the word separator:

| Filter           | Operator appended                                           |
|------------------|-------------------------------------------------------------|
| Exact phrase     | `+"phrase+with+spaces"`                                     |
| Exclude term     | `+-term +-intitle:term +-description:term` (triple exclude) |
| Title contains   | `+intitle:word`                                             |
| Date before      | `+before:YYYY-MM-DD`                                        |
| Date after       | `+after:YYYY-MM-DD`                                         |

**After** the operators, a `&sp=...` param is appended for length/type filters. These are base64-encoded protobufs, double-URL-encoded (`%253D` = encoded `%3D` = encoded `=`):

| Filter              | `sp=` value        | Decoded protobuf meaning         |
|---------------------|--------------------|----------------------------------|
| Under 4 minutes     | `EgIYAQ%253D%253D` | filter.duration = SHORT          |
| 4 – 20 minutes      | `EgIYAw%253D%253D` | filter.duration = MEDIUM         |
| Over 20 minutes     | `EgIYAg%253D%253D` | filter.duration = LONG           |
| Remove Shorts (videos only) | `EgIQAQ%253D%253D` | filter.type = VIDEO      |

Note: "Remove Shorts" and an explicit length filter are mutually exclusive — selecting "Under 4 minutes" (which *would* include Shorts) automatically unchecks Remove Shorts in the JS (`handleVideoLengthChange()`).

### 2.2 YouTube — channel-scoped search

If the user enters a channel in the "Search in Channel" field, the URL flips to:

**Base:** `https://www.youtube.com/@<handle>/search?query=<query>`

Same operators/`sp=` params as above. The handle is normalized by `normalizeChannel()`:

```
https://www.youtube.com/@foo/...  →  @foo
@foo/...                          →  @foo
foo                               →  @foo
```

The exclude-term logic is *simpler* here — only `+-term` is appended, not the triple exclude. Presumably YouTube's per-channel search doesn't honor `intitle:`/`description:` exclusions the same way.

### 2.3 Google video search (alternative backend)

**Base:** `https://www.google.com/search?tbm=vid&as_q=<query>` with `&udm=7` always appended (Google's "Videos" tab).

Operators use `%20` as word separator (Google-style) and differ in places:

| Filter           | Operator appended                           |
|------------------|---------------------------------------------|
| Exact phrase     | `%20"phrase%20with%20spaces"`               |
| Exclude          | `%20-"term"` (single exclusion, no triple)  |
| Title contains   | `%20intitle:word`                           |
| Site restrict    | `%20site:https://www.youtube.com` (default) |
| Date before/after| `%20before:YYYY-MM-DD` / `%20after:YYYY-MM-DD` |

Plus `tbs=` for quality/duration:

| Filter             | `tbs=` value  |
|--------------------|---------------|
| HD                 | `hq:h`        |
| 0 – 4 min          | `dur:s`       |
| 4 – 20 min         | `dur:m`       |
| 20+ min            | `dur:l`       |

Multiple `tbs` parts are comma-joined: `&tbs=hq:h,dur:s`.

Plus `lr=` for language: `&lr=lang_en`, `&lr=lang_de`, etc. (13 languages hardcoded.)

### 2.4 Known gotchas in their implementation

A few bugs/quirks worth noting — **don't copy them blindly**:

- **No URL-encoding of user input.** If someone types `a&b` or `a#b`, the raw string is jammed into `search_query`. For Curatables we'd use `encodeURIComponent`.
- **Title/exclude multi-word handling is buggy.** `titleIncludes.replace(/ /g, '+intitle:')` produces `intitle:foo+intitle:bar` — which is actually what you want for YouTube, but reads wrong. Multi-word "exact term" becomes `"foo+bar"` — the quotes survive but spaces are `+`, which YouTube treats correctly.
- **The `+-description:` exclusion operator may be legacy.** I haven't verified YouTube still honors it.
- **Date format is echoed unchanged.** `dateBefore.replace(/-/g, '-')` is a no-op. They probably meant something else.
- **Same-tab navigation (`_self`).** A search-from-our-UI feature should use `_blank` so parents don't lose Curatables state.

---

## 3. The `sp=` tokens, decoded

YouTube's `sp=` parameter is a base64-encoded, URL-safe protobuf of their `SearchFilter` message. The values playlists.at uses are stable and widely documented on third-party sites. For reference, here's what each one decodes to (from public reverse-engineering):

```
EgIQAQ==  →  field 2 (filter) { field 2 (type) = 1 (VIDEO) }
EgIYAQ==  →  field 2 (filter) { field 3 (duration) = 1 (SHORT, <4min) }
EgIYAg==  →  field 2 (filter) { field 3 (duration) = 2 (LONG, >20min) }
EgIYAw==  →  field 2 (filter) { field 3 (duration) = 3 (MEDIUM, 4-20min) }
```

(Note the LONG/MEDIUM ordering in the enum — counterintuitive but correct.)

**Useful additional tokens** not in playlists.at that we might want for Curatables:

| Token (base64) | Meaning                          |
|----------------|----------------------------------|
| `CAI%253D`     | Sort by upload date              |
| `CAM%253D`     | Sort by view count               |
| `CAE%253D`     | Sort by rating                   |
| `EgQQARgB`     | Video + short duration combined  |
| `EgQIBBAB`     | Upload date: today + video       |
| `EgQIAxAB`     | Upload date: this week + video   |
| `EgQIBRAB`     | Upload date: this month + video  |
| `EgQIBhAB`     | Upload date: this year + video   |
| `EgIIAQ%253D%253D` | Features: HD                 |
| `EgIgAQ%253D%253D` | Features: subtitles/CC       |

These are composable (filter messages concatenate) but verifying composition is fiddly — for anything beyond the four playlists.at uses, we should test each token against live YouTube before shipping.

---

## 4. Mapping to Curatables

Curatables already has `yt-dlp` as its download backend and uses `curl_cffi` browser impersonation for anti-bot. That gives us two implementation options:

### 4.1 Option A — Client-side URL builder (the playlists.at model)

**What it is:** Add a `/parent/search` page that's a form + JS that builds a YouTube URL and opens it in a new tab. Parent browses YouTube, copies a URL, pastes it back into Curatables' existing "add video" flow.

**Pros:**
- Trivial to implement. One Jinja template, ~100 lines of JS.
- Zero server load, zero scraping, zero anti-bot exposure.
- No risk of getting Curatables rate-limited by YouTube.
- Works today.

**Cons:**
- Breaks the "web-only UI, don't leave Curatables" feel.
- Parent has to context-switch, copy URLs manually.
- Can't surface already-downloaded / already-curated state on results.
- Doesn't help kids at all (kids never see search anyway per current design).

**When to pick it:** If search is a "nice to have" and we want it shipped in an afternoon.

### 4.2 Option B — Server-side search via yt-dlp

**What it is:** A new `/parent/search` endpoint that calls `yt-dlp` with a search query and renders results (thumbnail, title, channel, duration, upload date) in the parent UI. Each result has an "Add to library" button that triggers the existing download flow.

**Two ways to ask yt-dlp to search:**

1. **`ytsearchN:query` extractor** — yt-dlp's built-in: `yt-dlp "ytsearch20:cute puppies" --flat-playlist --dump-json`. Returns JSON metadata for N results without downloading. Supports `ytsearchdate:` for chronological ordering.
2. **Direct URL** — Build the same `https://www.youtube.com/results?search_query=...` URL playlists.at builds, pass it to yt-dlp with `--flat-playlist`. This is what lets us reuse the `sp=` tokens and filter grammar directly.

**Recommended:** use approach (2) internally so we can pass through playlists.at-style filters (duration, date range, channel scoping) without re-implementing them. The URL builder lives server-side, yt-dlp does the actual fetch.

**Architecture fit** (per `architecture.md`):

- **Route** (`routes/parent.py`): `GET /parent/search?q=...&duration=...&before=...` — thin, just parses query params and calls service.
- **Service** (`services/search.py`, new): builds the YouTube URL from filters, calls `yt-dlp` (existing wrapper), normalizes the JSON into a `SearchResult` dataclass, cross-references the videos repo to mark "already in library" items, returns the list.
- **Repo**: no changes — we're not persisting search results. Maybe cache recent queries in-memory with a short TTL if rate-limiting becomes a concern.
- **Template** (`templates/parent/search.html`): results grid, reuses existing video-card partial.

**Pros:**
- Stays in Curatables. Parent workflow: search → click → video is downloading.
- We can show "already downloaded" / "already in a kid's playlist" badges on results.
- Can apply Curatables policy (e.g., max duration per kid) as a result filter.
- Reuses existing yt-dlp + curl_cffi anti-bot stack — nothing new to maintain.

**Cons:**
- yt-dlp search is slower than we'd like (~2-5s for 20 results). Need a loading state.
- Burns yt-dlp/YouTube requests. Need rate-limiting per-parent to avoid getting the server IP flagged.
- `--flat-playlist` returns minimal metadata; thumbnails require an extra round-trip unless we construct `https://i.ytimg.com/vi/<id>/mqdefault.jpg` ourselves (which works but is unofficial).

**When to pick it:** If search is meant to be a first-class feature and we're ready to own the rate-limiting/caching work.

### 4.3 Hybrid suggestion

Ship **Option B** but with the URL-builder logic extracted into its own pure-function module (`services/youtube_search_url.py`) that has no I/O. That module:

- Takes a structured `SearchFilters` dataclass.
- Returns a fully-formed YouTube search URL.
- Has the `sp=` token table as a constant.
- Is trivially unit-testable (no network).

Then the search service just calls `build_youtube_search_url(filters)` and passes the result to the yt-dlp wrapper. If we later want to fall back to Option A for any reason (maintenance, YouTube blocking us, etc.), the same module powers a client-side form too.

---

## 5. Open questions to answer before building

1. **Do we need per-kid search, or only parent search?** The current design says kids never search. Confirm before scoping.
2. **Rate-limiting model.** Per-parent? Per-IP? Global? What's the backoff if YouTube starts returning captchas?
3. **Caching.** Is a 5-minute in-memory LRU on `(query, filters)` enough, or do we want to persist to SQLite?
4. **Thumbnails.** Use `i.ytimg.com` directly (fast, unofficial) or proxy through Curatables (slower, hides YouTube from client, matches the existing thumbnail-caching plans in `project_vision.md`)?
5. **Sort order.** playlists.at doesn't expose sort at all — YouTube's default ("relevance") is what you get. Do we want upload-date / view-count sort tokens exposed?
6. **Feature parity with Google mode?** playlists.at has a Google fallback. Probably irrelevant for Curatables — yt-dlp talks to YouTube directly, we don't need Google as a middleman.
7. **Handling non-YouTube sources.** Curatables is meant to be platform-agnostic. Does this feature's URL/filter grammar generalize, or is it YouTube-only with a "(other platforms TBD)" note?

---

## 6. References

- Live page: `https://playlists.at/youtube/search/`
- The inline script was extracted via `browser_evaluate` during this research session; it's ~300 lines and could be saved verbatim if we want a frozen reference.
- YouTube search operators: widely documented, stable for years (`intitle:`, `before:`, `after:`, `-term`).
- `sp=` protobuf format: not officially documented by Google; community-reverse-engineered. The four tokens playlists.at uses have been stable since ~2019.
- yt-dlp search extractor: `ytsearch`, `ytsearchdate`, `ytsearchall` — see `yt-dlp --extractor-descriptions`.
