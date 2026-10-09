"""Find video files on the drive for the import page.

Only folders listed in storage.import_roots are ever read, every path
is resolved and checked to stay inside its root, and symlinks, hidden
files, Windows system folders and the library's own media folder are
skipped.
"""
from __future__ import annotations

import os
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Config
from app.services.media_probe import MediaProbeService, ProbeError, ProbeResult
from app.services.subtitles import language_code

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".mpg", ".mpeg"}
PICTURE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
PICTURE_NAMES = ("poster", "folder", "cover", "movie", "fanart")
SUBTITLE_EXTS = (".srt", ".vtt")
_SKIP_NAMES = {"system volume information", "$recycle.bin", "lost+found", "@eadir"}
_MAX_NFO_BYTES = 256 * 1024
_MAX_SIDECAR_PICTURE = 8 * 1024 * 1024

_EPISODE_PATTERNS = (
    re.compile(r"[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})"),
    re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)"),
    re.compile(r"(?:episode|ep|epis[oó]dio)[ ._-]*(\d{1,3})(?!\d)", re.IGNORECASE),
)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_TAGS = re.compile(
    r"\b(?:2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr|bluray|blu-ray|brrip|bdrip|"
    r"dvdrip|dvd|webrip|web-dl|webdl|web|hdtv|hdrip|x264|x265|h264|h\.264|h265|hevc|"
    r"xvid|divx|aac|ac3|dts|ddp?5\.1|5\.1|remux|proper|repack|extended|unrated|"
    r"multi|dual|dubbed|subbed|internal|limited)\b", re.IGNORECASE)
_SEASON_FOLDER = re.compile(r"^(?:season|temporada|s)[ ._-]*\d{1,2}$", re.IGNORECASE)
_LANGUAGE_WORDS = {
    "english": "en", "portuguese": "pt", "portugues": "pt", "português": "pt",
    "brazilian": "pt-BR", "spanish": "es", "espanol": "es", "español": "es",
    "french": "fr", "german": "de", "italian": "it", "japanese": "ja",
}


class ImportPathError(ValueError):
    """A path outside the import folders, or one that is not there."""


@dataclass
class FileEntry:
    name: str
    rel: str
    size: int
    mtime: float
    imported: bool = False


@dataclass
class Listing:
    root_index: int
    root: Path
    rel: str
    parent_rel: str | None
    folders: list[str] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)


@dataclass
class NameGuess:
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    show: str = ""

    @property
    def is_episode(self) -> bool:
        return self.episode is not None


@dataclass
class SidecarSubtitle:
    path: Path
    language: str


@dataclass
class Sidecars:
    pictures: list[Path] = field(default_factory=list)
    subtitles: list[SidecarSubtitle] = field(default_factory=list)
    nfo_title: str = ""
    nfo_plot: str = ""
    nfo_year: int | None = None


def _skip(entry: os.DirEntry) -> bool:
    name = entry.name
    return (name.startswith(".") or name.startswith("$")
            or name.lower() in _SKIP_NAMES or entry.is_symlink())


class ImportRoots:
    def __init__(self, config: Config):
        self.config = config

    def roots(self) -> list[Path]:
        media = self.config.media_dir.resolve()
        out = []
        for raw in self.config.storage.import_roots:
            root = Path(raw).expanduser()
            if root.is_dir():
                root = root.resolve()
                if root != media and not root.is_relative_to(media):
                    out.append(root)
        return out

    def problems(self) -> list[tuple[str, str]]:
        """Configured folders that cannot be used, with the reason."""
        media = self.config.media_dir.resolve()
        out = []
        for raw in self.config.storage.import_roots:
            root = Path(raw).expanduser()
            if not root.is_dir():
                out.append((raw, "is not there. Is the drive plugged in?"))
            elif root.resolve() == media or root.resolve().is_relative_to(media):
                out.append((raw, "is the library's own folder, so nothing is imported from it."))
        return out

    def _excluded(self, path: Path) -> bool:
        media = self.config.media_dir.resolve()
        data = self.config.data_dir.resolve()
        return path == media or path.is_relative_to(media) or path == data \
            or path.is_relative_to(data)

    def resolve(self, root_index: int, rel: str) -> tuple[Path, Path]:
        roots = self.roots()
        if not 0 <= root_index < len(roots):
            raise ImportPathError("That folder is not one of the import folders.")
        root = roots[root_index]
        rel = (rel or "").strip().lstrip("/")
        if any(part in ("..", "") for part in Path(rel).parts if rel):
            raise ImportPathError("That path is not allowed.")
        target = (root / rel).resolve() if rel else root
        if target != root and not target.is_relative_to(root):
            raise ImportPathError("That path leaves the import folder.")
        if not target.exists():
            raise ImportPathError("That file or folder is not there any more.")
        if self._excluded(target):
            raise ImportPathError("That is the library's own folder.")
        return root, target

    def check_file(self, path: str) -> Path:
        """An absolute path saved in a job: still a video inside a root?"""
        candidate = Path(path)
        for i, root in enumerate(self.roots()):
            if candidate.is_relative_to(root):
                _, target = self.resolve(i, str(candidate.relative_to(root)))
                if not target.is_file() or target.suffix.lower() not in VIDEO_EXTS:
                    raise ImportPathError("That is not a video file.")
                return target
        raise ImportPathError("That file is not in an import folder any more.")

    def listing(self, root_index: int, rel: str, imported: set[str] | None = None
                ) -> Listing:
        root, folder = self.resolve(root_index, rel)
        if not folder.is_dir():
            raise ImportPathError("That is not a folder.")
        rel_path = "" if folder == root else str(folder.relative_to(root))
        parent = None
        if rel_path:
            parent = str(Path(rel_path).parent)
            parent = "" if parent == "." else parent
        listing = Listing(root_index, root, rel_path, parent)
        with os.scandir(folder) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
        for entry in entries:
            if _skip(entry):
                continue
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                if not self._excluded(path.resolve()):
                    listing.folders.append(entry.name)
            elif entry.is_file(follow_symlinks=False) and path.suffix.lower() in VIDEO_EXTS:
                st = entry.stat(follow_symlinks=False)
                listing.files.append(FileEntry(
                    name=entry.name, rel=str(path.relative_to(root)), size=st.st_size,
                    mtime=st.st_mtime, imported=str(path) in (imported or set())))
        return listing

    def videos_under(self, root_index: int, rel: str, limit: int = 500) -> list[Path]:
        """Every video file in a folder and its subfolders, for "select all"."""
        _, folder = self.resolve(root_index, rel)
        found: list[Path] = []
        stack = [folder]
        while stack and len(found) < limit:
            current = stack.pop()
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower(), reverse=True)
            for entry in entries:
                if _skip(entry):
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if not self._excluded(path.resolve()):
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False) \
                        and path.suffix.lower() in VIDEO_EXTS:
                    found.append(path)
        return sorted(found)[:limit]


def _clean(text: str) -> str:
    text = re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+-\s*$|^\s*-\s+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -([{")
    if text and text == text.lower():
        text = text.title()
    return text


def _show_from_folders(path: Path) -> str:
    folder = path.parent
    if _SEASON_FOLDER.match(folder.name):
        folder = folder.parent
    return _clean(folder.name)


def parse_name(path: Path) -> NameGuess:
    """A first guess at title, year and episode from the file name."""
    stem = path.stem
    season = episode = None
    found = None
    for pattern in _EPISODE_PATTERNS:
        found = pattern.search(stem)
        if found:
            if found.lastindex == 2:
                season, episode = int(found.group(1)), int(found.group(2))
            else:
                season, episode = 1, int(found.group(1))
            break
    cut = found.start() if found else len(stem)
    year = None
    for m in _YEAR.finditer(stem):
        if 0 < m.start() <= cut:
            year = int(m.group(1))
            cut = m.start()
            break
    tag = _TAGS.search(stem)
    if tag and tag.start() > 0:
        cut = min(cut, tag.start())
    head = _clean(stem[:cut])
    if found is None:
        return NameGuess(title=head or _clean(stem) or stem, year=year)
    show = head or _show_from_folders(path)
    after = stem[found.end():]
    after_tag = _TAGS.search(after)
    name = _clean(after[:after_tag.start()] if after_tag else after)
    title = name or f"{show} – S{season:02d}E{episode:02d}"
    return NameGuess(title=title, year=year, season=season, episode=episode, show=show)


def find_sidecars(video: Path) -> Sidecars:
    """Posters, a Kodi .nfo and subtitle files next to a video."""
    out = Sidecars()
    folder = video.parent
    stem = video.stem.lower()
    try:
        entries = [e for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
    except OSError:
        return out
    by_name = {e.name.lower(): Path(e.path) for e in entries}
    for name in [f"{stem}{ext}" for ext in PICTURE_EXTS] + [
            f"{stem}-poster{ext}" for ext in PICTURE_EXTS] + [
            f"{base}{ext}" for base in PICTURE_NAMES for ext in PICTURE_EXTS]:
        path = by_name.get(name)
        if path is not None and path not in out.pictures \
                and path.stat().st_size <= _MAX_SIDECAR_PICTURE:
            out.pictures.append(path)
    for lower, path in sorted(by_name.items()):
        if not lower.startswith(stem) or not lower.endswith(SUBTITLE_EXTS):
            continue
        middle = lower[len(stem):-4].strip(".")
        tag = middle.split(".")[-1] if middle else ""
        lang = language_code(tag) or _LANGUAGE_WORDS.get(tag, "")
        if middle and not lang and tag not in ("forced", "sdh", "cc"):
            continue
        out.subtitles.append(SidecarSubtitle(path=path, language=lang))
    nfo = by_name.get(f"{stem}.nfo") or by_name.get("movie.nfo") or by_name.get("tvshow.nfo")
    if nfo is not None and nfo.stat().st_size <= _MAX_NFO_BYTES:
        try:
            raw = nfo.read_bytes()
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("entity declarations are not allowed")
            tree = ET.fromstring(raw)
            out.nfo_title = (tree.findtext("title") or "").strip()
            out.nfo_plot = (tree.findtext("plot") or tree.findtext("outline") or "").strip()
            year = (tree.findtext("year") or tree.findtext("premiered") or "")[:4]
            out.nfo_year = int(year) if year.isdigit() else None
        except (ET.ParseError, OSError, ValueError):
            pass
    return out


class ProbeCache:
    """ffprobe results keyed by path, size and mtime."""

    def __init__(self, probe: MediaProbeService, capacity: int = 512):
        self.probe = probe
        self.capacity = capacity
        self._items: OrderedDict[tuple[str, int, float], ProbeResult | None] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> ProbeResult | None:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        try:
            result: ProbeResult | None = self.probe.probe(path)
        except ProbeError:
            result = None
        with self._lock:
            self._items[key] = result
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return result
