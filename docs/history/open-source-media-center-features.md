# Open Source Media Center Features

Research into existing open source and mainstream media center systems to identify features worth adopting in Curatables. Systems surveyed: Jellyfin, Plex, Kodi, Stremio, and YouTube Kids.

Date: 2026-04-07

---

## 1. Parental Controls

| Feature | Jellyfin | Plex | Kodi | YouTube Kids |
|---------|----------|------|------|-------------|
| Content rating filters | Per-user max rating | Presets (Younger Kid, Older Kid, Teen) with granular overrides | No | N/A |
| Tag-based allow-lists | Yes (child sees ONLY tagged items) | Custom label restrictions (Plex Pass) | No | No |
| Per-child search control | No | No | No | Yes (disable search entirely) |
| Access schedules | Yes (time-of-day windows, playback stops outside) | No | No | No |
| Usage timers | No | No | No | Yes (daily limits) |
| Section/profile PIN lock | No | No | Yes (PIN gates settings, file manager, addons) | No |
| Managed accounts (no email) | Yes | Yes ("Plex Home") | Yes (local profiles) | Yes |
| Block specific content | Via tags | Via labels | Via channel lock | Yes (block videos/channels) |
| Per-user download control | N/A | Yes (admin toggle per managed user) | N/A | N/A |

**Key insight:** Plex's restriction profile presets (age-tier templates) are the most user-friendly approach. Jellyfin's access schedules and YouTube Kids' usage timers address screen time — a top parent concern. No system offers Curatables' whitelist-only model.

---

## 2. Library Management and Metadata

| Feature | Jellyfin | Plex | Kodi | Stremio |
|---------|----------|------|------|---------|
| Auto metadata scraping | TMDb, TheTVDB, custom plugins | TMDb, TheTVDB, built-in agents | TMDb, TheTVDB via scrapers | Cinemeta addon |
| Library types | Movies, Shows, Music, Books, Photos | Movies, Shows, Music, Photos, Other | Movies, Shows, Music | Movies, Shows |
| NFO/sidecar metadata | Yes | Limited | Yes (primary workflow) | No |
| Collections | Manual + auto (genre, year, etc.) | Manual + smart, plus "edition" support | Smart playlists (rule-based) | No |
| Custom metadata fields | Via plugins | Via agents | Via NFO files | No |
| Trailers/extras | Community plugin | Built-in | Add-on dependent | No |

**Key insight:** Kodi's smart playlists (auto-collections by rule — genre, duration, channel, tag) are particularly relevant for Curatables, where a parent might want an auto-updating "videos under 5 minutes" or "science channel only" collection.

---

## 3. Watch History and Activity Tracking

| Feature | Jellyfin | Plex | Kodi | Stremio |
|---------|----------|------|------|---------|
| Per-user watch status | Yes | Yes | Yes (local only) | Yes (cloud sync) |
| Resume position | To the second | To the second | To the second | Yes |
| Play count | Yes | Yes | Yes | No |
| Admin activity dashboard | Basic (+ Jellystat/JellyWatch third-party) | Basic (+ Tautulli third-party for detail) | No | No |
| Cross-device sync | Built-in (server-based) | Built-in (server + cloud) | Requires Trakt addon | Cloud-based |

**Key insight:** Plex + Tautulli is the gold standard for activity tracking — what was watched, when, for how long, by whom, with rich visualizations. Curatables already has an events table; building a richer stats dashboard (per-child viewing reports) would be high value.

---

## 4. Playback Features

| Feature | Jellyfin | Plex | Kodi | Stremio |
|---------|----------|------|------|---------|
| Resume from last position | Yes | Yes | Yes | Yes |
| Subtitle selection | Yes | Yes | Yes | Yes (addon-based) |
| Audio track switching | Yes | Yes | Yes | Yes |
| Server-side transcoding | Yes | Yes | No (direct play only) | No |
| Play queues | Yes | Yes | Yes | Limited |
| "Up Next" / autoplay | Yes (for series) | Yes (for series) | Add-on dependent | Yes |
| Playback speed control | No | No | Yes | No |
| Chapter support | No | Yes | Yes | No |
| Skip intro/credits | Plugin | Built-in (Plex Pass) | Add-on | No |

**Key insight:** "Continue Watching" and "Up Next" are expected features in any modern media UI. Resume position tracking is table stakes. Curatables should track per-child watch position and surface a "Continue Watching" row on the kid home page.

---

## 5. Discovery Features

| Feature | Jellyfin | Plex | Kodi |
|---------|----------|------|------|
| Continue Watching row | Yes | Yes | Via skin widgets |
| Recently Added | Yes | Yes | Via skin widgets |
| Next Up (next episode) | Yes | Yes | Add-on dependent |
| Personalized recommendations | No | Yes (algorithmic) | No |
| Collections/playlists | Manual + auto | Manual + smart | Smart playlists (rule engine) |
| Cross-library search | Yes | Yes | Yes |
| Filters (genre, year, rating) | Yes | Yes | Yes |

**Key insight:** Curatables operates in a whitelist-only model, so "recommendations" means parent-curated suggestions rather than algorithms. Collections and smart playlists are the right fit — parents organize content into themed groups, and the kid UI surfaces them naturally.

---

## 6. Multi-User and Remote Access

| Feature | Jellyfin | Plex | Kodi | Stremio |
|---------|----------|------|------|---------|
| Unlimited local users | Yes | Yes (Plex Home) | Yes (profiles) | No (single account) |
| Per-user library access | Yes | Yes | No | No |
| Per-user settings | Yes | Yes | Yes | No |
| Remote access | Via reverse proxy | Built-in relay + direct | No (single device) | Cloud-based |
| Share with other households | Manual setup | Built-in friend sharing | No | No |
| Guest accounts | No | Yes | No | No |

**Key insight:** Curatables is designed for a single household. Jellyfin's model (unlimited local users, per-user library access, fully self-hosted) is the closest match. Future sharing between households (e.g., grandparents curating content) aligns with Plex's friend-sharing concept.

---

## 7. Plugin and Extension Systems

| System | Language | Capabilities | Ecosystem |
|--------|----------|-------------|-----------|
| Kodi | Python, C++ | Skins, scrapers, PVR backends, video sources, subtitle providers, context menus | Most mature; official + third-party repos |
| Jellyfin | .NET | REST endpoints, metadata providers, scheduled tasks, UI modifications | Growing; official plugin repo |
| Stremio | JavaScript | Catalogs, streams, metadata, subtitles | Simple protocol; easy to self-host |
| Plex | Deprecated | Formerly Python-based plugins; now relies on scanners + external tools | Effectively closed |

**Key insight:** If Curatables ever needs an extension system, Stremio's JavaScript addon protocol is the simplest model — addons are just HTTP endpoints that return JSON catalogs. This could enable community-contributed content sources beyond YouTube.

---

## 8. Offline and Download Capabilities

| Feature | Plex | Jellyfin | Kodi | Stremio |
|---------|------|----------|------|---------|
| Mobile download/sync | Yes (Plex Pass) | No (frequently requested) | N/A (local files) | Limited caching |
| Admin control over downloads | Yes (per managed user) | N/A | N/A | No |
| Automatic quality selection | Yes (based on storage) | N/A | N/A | No |

**Key insight:** Curatables already downloads everything locally — it is inherently an offline-first system. The relevant Plex feature is admin control over which users can download (relevant if Curatables ever supports mobile apps).

---

## 9. Relevance to Curatables

### High Priority (aligns with existing architecture)

| Feature | Inspiration | Effort | Impact |
|---------|-------------|--------|--------|
| Continue Watching / resume | Jellyfin, Plex | Medium (track position per child in events table) | High — expected UX |
| Collections / playlists | Plex, Kodi | Medium (new table, parent UI to group videos) | High — core curation tool |
| Access schedules | Jellyfin | Low (check time in viewer middleware) | High — top parent request |
| Usage timers | YouTube Kids | Medium (track daily watch time per profile, enforce limit) | High — screen time control |
| Richer activity dashboard | Plex + Tautulli | Medium (aggregate existing events data) | Medium — parent peace of mind |

### Medium Priority (good for future)

| Feature | Inspiration | Notes |
|---------|-------------|-------|
| Smart playlists | Kodi | Auto-collections by rule (channel, duration, tag) |
| Age-tier restriction presets | Plex | Templates instead of per-channel manual setup |
| "Next Up" autoplay | Plex, Jellyfin | For sequential content within a channel |
| Skip intro detection | Plex | Complex; low priority for short-form content |

### Curatables' Differentiator

No surveyed system offers a **parent-curated whitelist-only model** where children see exclusively hand-picked content. Every other system starts with "show everything, then filter." Curatables starts with "show nothing, then approve." This is the core value proposition and should remain the foundation for all feature additions.
