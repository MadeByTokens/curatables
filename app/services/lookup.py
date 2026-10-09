"""Look up a picture and a description for an imported video.

The server does every request, through safe_fetch (public addresses
only, checked redirects), and only ever sends the title and year the
parent typed (or a link they pasted). Results are plain text; the
parent reviews them before anything is saved.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote, urlencode, urljoin

from app.config import Config
from app.services.images import MAX_IMAGE_BYTES, validate_image
from app.services.safe_fetch import BlockedURL, fetch_public

logger = logging.getLogger(__name__)

USER_AGENT = "curatables/0.6 (+https://github.com/MadeByTokens/curatables)"
MAX_JSON = 2 * 1024 * 1024
MAX_PAGE = 1024 * 1024
DESCRIPTION_LIMIT = 600
MAX_CANDIDATES = 6

_FILM = {"Q11424", "Q202866", "Q24869", "Q506240", "Q17517379", "Q24862"}
_SERIES = {"Q5398426", "Q581714", "Q117467246", "Q1259759", "Q526877", "Q15416"}
_EPISODE = {"Q21191270", "Q1983062"}
_YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


@dataclass
class Candidate:
    source: str          # wikipedia | tmdb | youtube | web
    title: str
    description: str = ""
    image_url: str = ""
    year: int | None = None
    ref: str = ""        # Wikidata Q-id, tmdb:<id>, YouTube id
    url: str = ""
    kind: str = ""       # Film | TV series | Episode | YouTube video | ...
    rating: str = ""     # e.g. "PG", from Wikidata when known


def plain_text(raw: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """Markup stripped, entities decoded, cut at a sentence end."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…"


def wiki_language(lang: str) -> str:
    base = (lang or "en").split("-")[0].lower()
    return base if re.fullmatch(r"[a-z]{2,3}", base) else "en"


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 300:
            self.title += data


class LookupService:
    def __init__(self, config: Config, backend=None,
                 fetch: Callable[..., bytes] = fetch_public):
        self.config = config
        self.backend = backend
        self.fetch = fetch

    @property
    def enabled(self) -> bool:
        return self.config.imports.lookups_enabled

    def _json(self, url: str, headers: dict[str, str] | None = None):
        data = self.fetch(url, MAX_JSON, timeout=15,
                          headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                   **(headers or {})})
        return json.loads(data[:MAX_JSON].decode("utf-8", "replace"))

    def search(self, origin: str, title: str, year: int | None = None, link: str = "",
               language: str = "en") -> list[Candidate]:
        if not self.enabled:
            return []
        title = " ".join((title or "").split())[:150]
        try:
            if origin == "youtube":
                return self.youtube(title, link)
            if origin == "web":
                return [c for c in [self.web_page(link)] if c] if link else []
            if origin == "movie" and title:
                found = self.wikipedia(title, year, language)
                if self.config.imports.tmdb_api_key:
                    found = self.tmdb(title, year, language) + found
                return found[:MAX_CANDIDATES]
        except (OSError, ValueError, BlockedURL) as e:
            logger.info("lookup for %r failed: %s", title, e)
        return []

    # -- Wikipedia + Wikidata -------------------------------------------------

    def wikipedia(self, title: str, year: int | None, language: str) -> list[Candidate]:
        lang = wiki_language(language)
        found = self._wikipedia_in(lang, title, year)
        if len(found) < 2 and lang != "en":
            found += self._wikipedia_in("en", title, year)
        self._add_wikidata(found)
        def rank(c: Candidate) -> tuple:
            return (c.kind not in ("Film", "TV series", "Episode"),
                    bool(year) and c.year != year)
        return sorted(found, key=rank)[:MAX_CANDIDATES]

    def _wikipedia_in(self, lang: str, title: str, year: int | None) -> list[Candidate]:
        query = f"{title} {year}" if year else title
        base = f"https://{lang}.wikipedia.org"
        result = self._json(f"{base}/w/rest.php/v1/search/page?"
                            + urlencode({"q": query, "limit": MAX_CANDIDATES}))
        out = []
        for page in (result.get("pages") or [])[:MAX_CANDIDATES]:
            key = page.get("key")
            if not key:
                continue
            try:
                summary = self._json(f"{base}/api/rest_v1/page/summary/{quote(key, safe='')}")
            except (OSError, ValueError, BlockedURL):
                continue
            if summary.get("type") == "disambiguation":
                continue
            image = (summary.get("thumbnail") or {}).get("source", "")
            image = re.sub(r"/(\d+)px-", "/500px-", image) if image else ""
            out.append(Candidate(
                source="wikipedia", title=summary.get("title") or page.get("title", ""),
                description=plain_text(summary.get("extract", "")),
                image_url=image, ref=summary.get("wikibase_item", ""),
                url=((summary.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
                kind=plain_text(summary.get("description", ""), 80)))
        return out

    def _add_wikidata(self, candidates: list[Candidate]) -> None:
        ids = [c.ref for c in candidates if re.fullmatch(r"Q\d+", c.ref or "")]
        if not ids:
            return
        try:
            entities = self._json("https://www.wikidata.org/w/api.php?" + urlencode({
                "action": "wbgetentities", "ids": "|".join(ids), "props": "claims",
                "format": "json"})).get("entities", {})
        except (OSError, ValueError, BlockedURL):
            return
        rating_ids: dict[str, str] = {}
        for c in candidates:
            claims = (entities.get(c.ref) or {}).get("claims") or {}
            kinds = {self._item(v) for v in claims.get("P31", [])}
            if kinds & _FILM:
                c.kind = "Film"
            elif kinds & _SERIES:
                c.kind = "TV series"
            elif kinds & _EPISODE:
                c.kind = "Episode"
            for prop in ("P577", "P580"):
                for v in claims.get(prop, []):
                    t = ((v.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
                    m = re.match(r"[+-](\d{4})", str(t.get("time", "")))
                    if m:
                        c.year = int(m.group(1)) if c.year is None else min(c.year,
                                                                            int(m.group(1)))
                if c.year:
                    break
            for v in claims.get("P1657", []):
                rid = self._item(v)
                if rid:
                    rating_ids[c.ref] = rid
                    break
        if not rating_ids:
            return
        try:
            labels = self._json("https://www.wikidata.org/w/api.php?" + urlencode({
                "action": "wbgetentities", "ids": "|".join(set(rating_ids.values())),
                "props": "labels", "languages": "en", "format": "json"})).get("entities", {})
        except (OSError, ValueError, BlockedURL):
            return
        for c in candidates:
            rating_id = rating_ids.get(c.ref)
            if rating_id:
                label = (((labels.get(rating_id) or {}).get("labels") or {}).get("en") or {})
                c.rating = plain_text(label.get("value", ""), 40)

    @staticmethod
    def _item(claim: dict) -> str:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
        return str(value.get("id", "")) if isinstance(value, dict) else ""

    # -- TMDB -------------------------------------------------------------------

    def tmdb(self, title: str, year: int | None, language: str) -> list[Candidate]:
        key = self.config.imports.tmdb_api_key.strip()
        params = {"query": title, "language": language or "en", "include_adult": "false"}
        headers = {}
        if len(key) > 40:
            headers["Authorization"] = f"Bearer {key}"
        else:
            params["api_key"] = key
        try:
            result = self._json("https://api.themoviedb.org/3/search/multi?"
                                + urlencode(params), headers=headers)
        except (OSError, ValueError, BlockedURL) as e:
            logger.info("TMDB lookup failed: %s", e)
            return []
        out = []
        for item in (result.get("results") or [])[:MAX_CANDIDATES]:
            media = item.get("media_type")
            if media not in ("movie", "tv"):
                continue
            date = item.get("release_date") or item.get("first_air_date") or ""
            poster = item.get("poster_path") or ""
            out.append(Candidate(
                source="tmdb", title=item.get("title") or item.get("name") or "",
                description=plain_text(item.get("overview", "")),
                image_url=f"https://image.tmdb.org/t/p/w500{poster}" if poster else "",
                year=int(date[:4]) if date[:4].isdigit() else None,
                ref=f"tmdb:{media}/{item.get('id')}",
                url=f"https://www.themoviedb.org/{media}/{item.get('id')}",
                kind="Film" if media == "movie" else "TV series"))
        if year:
            out.sort(key=lambda c: c.year != year)
        return out

    # -- YouTube and web pages ----------------------------------------------------

    def youtube(self, title: str, link: str = "") -> list[Candidate]:
        if self.backend is None:
            return []
        metas = []
        m = _YOUTUBE_ID.search(link or "")
        if m:
            meta = self.backend.fetch_video_info(f"https://www.youtube.com/watch?v={m.group(1)}")
            metas = [meta] if meta else []
        elif title:
            metas = self.backend.search(title, max_results=MAX_CANDIDATES)
        return [Candidate(
            source="youtube", title=meta.title, description=plain_text(meta.description),
            image_url=meta.thumbnail_url or
            f"https://i.ytimg.com/vi/{meta.video_id}/hqdefault.jpg",
            ref=meta.video_id, url=meta.original_url
            or f"https://www.youtube.com/watch?v={meta.video_id}",
            kind=f"YouTube · {meta.channel}" if meta.channel else "YouTube video",
            year=int(meta.upload_date[:4]) if (meta.upload_date or "")[:4].isdigit() else None)
            for meta in metas if meta][:MAX_CANDIDATES]

    def web_page(self, url: str) -> Candidate | None:
        data = self.fetch(url, MAX_PAGE, timeout=15, headers={"User-Agent": USER_AGENT})
        parser = _MetaParser()
        parser.feed(data[:MAX_PAGE].decode("utf-8", "replace"))
        meta = parser.meta
        image = meta.get("og:image") or meta.get("twitter:image") or ""
        title = meta.get("og:title") or meta.get("twitter:title") or parser.title
        if not (title or image):
            return None
        return Candidate(
            source="web", title=plain_text(title, 200),
            description=plain_text(meta.get("og:description") or meta.get("description", "")),
            image_url=urljoin(url, image) if image else "", url=url, kind="Web page")

    def image(self, url: str) -> bytes:
        """Download a candidate's picture; raises ValueError if it isn't one."""
        data = self.fetch(url, MAX_IMAGE_BYTES, timeout=15, headers={"User-Agent": USER_AGENT})
        validate_image(data)
        return data
