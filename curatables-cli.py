#!/usr/bin/env python3
"""curatables - Interactive video search, filter, and download CLI.

Wraps yt-dlp so power users can find, filter, and download videos
from any site yt-dlp supports. The YouTube fast-path is still the
dominant case, but nothing in the CLI is locked to a single
platform any more.
"""

import argparse
import cmd
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime


# Defaults passed to the external `yt-dlp` CLI when the user runs
# the download command. The youtube-specific extractor arg is
# silently ignored by non-YouTube extractors, so leaving it on
# for every download is harmless.
YTDLP_DEFAULT_ARGS = [
    "--extractor-args", "youtube:player_client=all",
    "--merge-output-format", "mp4",
]


def _entry_to_result(e):
    """Project a yt-dlp entry dict onto the CLI's result shape.

    Returns None for entries we can't use (no ID, unsafe ID).
    """
    vid_id = e.get("id", "")
    if not vid_id or not re.fullmatch(r"[a-zA-Z0-9._-]+", vid_id):
        return None
    duration = e.get("duration") or 0
    if isinstance(duration, str):
        try:
            duration = int(float(duration))
        except (ValueError, TypeError):
            duration = 0
    extractor = (e.get("extractor_key") or e.get("extractor") or "").lower()
    return {
        "id": vid_id,
        "extractor": extractor,
        "title": e.get("title", "N/A"),
        "url": e.get("webpage_url") or e.get("url") or "",
        "channel": e.get("channel") or e.get("uploader") or "Unknown",
        "duration": duration,
        "view_count": e.get("view_count") or 0,
        "upload_date": e.get("upload_date") or "",
        "description": e.get("description") or "",
    }


def search_videos(query, channel=None, max_results=50):
    """Search for videos using yt-dlp and return metadata.

    With a bare query, defaults to `ytsearchN:` (YouTube's search).
    With an explicit channel handle/URL, searches within that
    channel instead. Pass a full URL to any yt-dlp-supported source
    to scope the search there.
    """
    import yt_dlp

    search_query = query
    if channel:
        # Channel-scoped search. If channel is a URL, use it as-is;
        # otherwise assume it's a YouTube @handle / username.
        if channel.startswith("http"):
            search_url = f"{channel.rstrip('/')}/search?query={query}"
        elif channel.startswith("@"):
            search_url = f"https://www.youtube.com/{channel}/search?query={query}"
        else:
            search_url = f"https://www.youtube.com/@{channel}/search?query={query}"
    else:
        # Fallback: YouTube search via yt-dlp's ytsearch: scheme.
        search_url = f"ytsearch{max_results}:{search_query}"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if channel:
        opts["playlistend"] = max_results

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(search_url, download=False)
        except Exception as e:
            print(f"Error searching: {e}")
            return []

    results = []
    for e in info.get("entries", []):
        if not e:
            continue
        r = _entry_to_result(e)
        if r is not None:
            results.append(r)
    return results


def fetch_playlist(url, max_results=None):
    """Fetch all videos from a playlist URL (any yt-dlp-supported source)."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if max_results:
        opts["playlistend"] = max_results

    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"Error fetching playlist: {e}")
            return []

    results = []
    for e in info.get("entries", []):
        if not e:
            continue
        r = _entry_to_result(e)
        if r is not None:
            results.append(r)
    return results


def fmt_duration(seconds):
    """Format seconds into H:MM:SS or M:SS."""
    if not seconds:
        return "?"
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_views(n):
    """Format view count compactly."""
    if not n:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def print_table(videos, show_index=True):
    """Print videos as a formatted table."""
    if not videos:
        print("  (no results)")
        return
    term_width = shutil.get_terminal_size((100, 24)).columns
    idx_w = len(str(len(videos))) + 2 if show_index else 0
    dur_w = 9
    views_w = 8
    chan_w = 20
    fixed = idx_w + dur_w + views_w + chan_w + 6  # separators
    title_w = max(20, term_width - fixed)

    for i, v in enumerate(videos):
        idx = f"{i + 1:>{idx_w - 1}} " if show_index else ""
        title = v["title"]
        if len(title) > title_w:
            title = title[: title_w - 1] + "…"
        channel = v["channel"]
        if len(channel) > chan_w:
            channel = channel[: chan_w - 1] + "…"
        dur = fmt_duration(v["duration"])
        views = fmt_views(v["view_count"])
        print(f"{idx}{title:<{title_w}}  {dur:>{dur_w}}  {views:>{views_w}}  {channel}")


class DwnloadybShell(cmd.Cmd):
    intro = (
        "\nType 'help' for available commands. "
        "Use 'filter <text>' to narrow results, 'sort <field>' to reorder.\n"
    )
    prompt = "curatables> "

    def __init__(self, videos):
        super().__init__()
        self.all_videos = list(videos)  # original full set
        self.videos = list(videos)  # current working set

    def _resolve_indices(self, arg):
        """Parse index spec like '1,3,5-7' into a set of 0-based indices."""
        indices = set()
        for part in arg.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                try:
                    a, b = int(a), int(b)
                    indices.update(range(a - 1, b))
                except ValueError:
                    pass
            else:
                try:
                    indices.add(int(part) - 1)
                except ValueError:
                    pass
        return {i for i in indices if 0 <= i < len(self.videos)}

    # -- Display --
    def do_list(self, arg):
        """Show current video list."""
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos")

    do_ls = do_list
    do_l = do_list

    def do_info(self, arg):
        """Show full details for a video: info <number>"""
        try:
            idx = int(arg) - 1
            v = self.videos[idx]
        except (ValueError, IndexError):
            print("Usage: info <number>")
            return
        print(f"  Title:    {v['title']}")
        print(f"  Channel:  {v['channel']}")
        print(f"  Duration: {fmt_duration(v['duration'])}")
        print(f"  Views:    {fmt_views(v['view_count'])}")
        print(f"  Date:     {v['upload_date']}")
        print(f"  URL:      {v['url']}")
        if v["description"]:
            desc = v["description"][:300]
            print(f"  Desc:     {desc}")

    # -- Filtering --
    def do_filter(self, arg):
        """Keep only videos matching text (case-insensitive): filter <text>"""
        if not arg:
            print("Usage: filter <text>")
            return
        pat = arg.lower()
        self.videos = [
            v for v in self.videos
            if pat in v["title"].lower()
            or pat in v["channel"].lower()
            or pat in v["description"].lower()
        ]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos match '{arg}'")

    do_f = do_filter

    def do_regex(self, arg):
        """Keep only videos whose title matches a regex: regex <pattern>"""
        if not arg:
            print("Usage: regex <pattern>")
            return
        try:
            pat = re.compile(arg, re.IGNORECASE)
        except re.error as e:
            print(f"Invalid regex: {e}")
            return
        self.videos = [v for v in self.videos if pat.search(v["title"])]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos match")

    def do_exclude(self, arg):
        """Remove videos matching text: exclude <text>"""
        if not arg:
            print("Usage: exclude <text>")
            return
        pat = arg.lower()
        self.videos = [
            v for v in self.videos
            if pat not in v["title"].lower()
            and pat not in v["channel"].lower()
            and pat not in v["description"].lower()
        ]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos remaining")

    do_x = do_exclude

    def do_keep(self, arg):
        """Keep only specific videos by index: keep 1,3,5-7"""
        if not arg:
            print("Usage: keep 1,3,5-7")
            return
        indices = self._resolve_indices(arg)
        if not indices:
            print("No valid indices.")
            return
        self.videos = [self.videos[i] for i in sorted(indices)]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos kept")

    def do_remove(self, arg):
        """Remove specific videos by index: remove 1,3,5-7"""
        if not arg:
            print("Usage: remove 1,3,5-7")
            return
        indices = self._resolve_indices(arg)
        if not indices:
            print("No valid indices.")
            return
        self.videos = [v for i, v in enumerate(self.videos) if i not in indices]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos remaining")

    do_rm = do_remove

    def do_reset(self, arg):
        """Reset to the original search results."""
        self.videos = list(self.all_videos)
        print_table(self.videos)
        print(f"\n  Reset to {len(self.videos)} videos")

    # -- Additional searches --
    def do_search(self, arg):
        """Run a new search and add results to current list: search [-n 50] [-c channel] [-p playlist] <query>
        Use -n to set max results, -c to restrict to a channel, -p to fetch a playlist.
        Results are merged with the current list (use 'dedup' to remove duplicates)."""
        if not arg:
            print("Usage: search [-n 50] [-c channel] [-p playlist] <query>")
            return
        parts = shlex.split(arg)
        max_results = 50
        channel = None
        playlist = None
        query_parts = []
        i = 0
        while i < len(parts):
            if parts[i] == "-n" and i + 1 < len(parts):
                try:
                    max_results = int(parts[i + 1])
                except ValueError:
                    pass
                i += 2
            elif parts[i] == "-c" and i + 1 < len(parts):
                channel = parts[i + 1]
                i += 2
            elif parts[i] == "-p" and i + 1 < len(parts):
                playlist = parts[i + 1]
                i += 2
            else:
                query_parts.append(parts[i])
                i += 1

        if playlist:
            print(f"  Fetching playlist: {playlist} ...")
            new_videos = fetch_playlist(playlist, max_results=max_results)
        elif query_parts:
            query = " ".join(query_parts)
            label = f"  Searching for: {query}"
            if channel:
                label += f" (channel: {channel})"
            print(label + " ...")
            new_videos = search_videos(query, channel=channel, max_results=max_results)
        else:
            print("Usage: search [-n 50] [-c channel] [-p playlist] <query>")
            return

        if not new_videos:
            print("  No results found.")
            return
        before = len(self.videos)
        self.videos.extend(new_videos)
        self.all_videos.extend(new_videos)
        print(f"  Added {len(new_videos)} videos ({before} -> {len(self.videos)} total)")
        print_table(self.videos)

    # -- Sorting --
    def do_sort(self, arg):
        """Sort videos: sort <field> [asc|desc]
        Fields: title, channel, duration, views, date"""
        parts = arg.split()
        if not parts:
            print("Usage: sort <title|channel|duration|views|date> [asc|desc]")
            return
        field = parts[0].lower()
        reverse = True  # default descending for numeric
        if len(parts) > 1:
            reverse = parts[1].lower() != "asc"

        key_map = {
            "title": ("title", False),
            "channel": ("channel", False),
            "duration": ("duration", True),
            "dur": ("duration", True),
            "views": ("view_count", True),
            "date": ("upload_date", True),
        }
        if field not in key_map:
            print(f"Unknown field '{field}'. Use: title, channel, duration, views, date")
            return
        key_field, default_reverse = key_map[field]
        if len(parts) < 2:
            reverse = default_reverse
        self.videos.sort(key=lambda v: v[key_field] or "", reverse=reverse)
        print_table(self.videos)

    do_s = do_sort

    # -- Deduplication --
    def do_dedup(self, arg):
        """Remove duplicate videos (same video ID)."""
        seen = set()
        unique = []
        for v in self.videos:
            if v["id"] not in seen:
                seen.add(v["id"])
                unique.append(v)
        removed = len(self.videos) - len(unique)
        self.videos = unique
        print(f"  Removed {removed} duplicate(s). {len(self.videos)} videos remaining.")

    # -- Duration filter --
    def do_longer(self, arg):
        """Keep videos longer than N minutes: longer <minutes>"""
        try:
            mins = float(arg)
        except (ValueError, TypeError):
            print("Usage: longer <minutes>")
            return
        self.videos = [v for v in self.videos if v["duration"] >= mins * 60]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos")

    def do_shorter(self, arg):
        """Keep videos shorter than N minutes: shorter <minutes>"""
        try:
            mins = float(arg)
        except (ValueError, TypeError):
            print("Usage: shorter <minutes>")
            return
        self.videos = [v for v in self.videos if 0 < v["duration"] <= mins * 60]
        print_table(self.videos)
        print(f"\n  {len(self.videos)} videos")

    # -- Export & Download --
    def do_export(self, arg):
        """Export URLs to a file (yt-dlp batch format): export [filename]"""
        fname = arg.strip() or "videos.txt"
        if not self.videos:
            print("  Nothing to export.")
            return
        with open(fname, "w") as f:
            for v in self.videos:
                f.write(f"# {v['title']}\n")
                f.write(f"{v['url']}\n")
        print(f"  Exported {len(self.videos)} URLs to {fname}")
        default_args = " ".join(YTDLP_DEFAULT_ARGS)
        print(f"  Download with: yt-dlp {default_args} -a {fname}")

    def do_json(self, arg):
        """Export current list as JSON: json [filename]"""
        fname = arg.strip() or "videos.json"
        with open(fname, "w") as f:
            json.dump(self.videos, f, indent=2)
        print(f"  Exported {len(self.videos)} videos to {fname}")

    def do_download(self, arg):
        """Download current list with yt-dlp: download [extra yt-dlp args]"""
        if not self.videos:
            print("  Nothing to download.")
            return
        urls = [v["url"] for v in self.videos]
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            print("  yt-dlp not found in PATH.")
            return
        extra = shlex.split(arg) if arg else []
        cmd_args = [ytdlp] + YTDLP_DEFAULT_ARGS + extra + urls
        print(f"  Downloading {len(urls)} video(s)...")
        try:
            subprocess.run(cmd_args)
        except KeyboardInterrupt:
            print("\n  Download interrupted.")

    do_dl = do_download

    # -- Shell --
    def do_quit(self, arg):
        """Exit the REPL."""
        return True

    do_q = do_quit
    do_exit = do_quit
    do_EOF = do_quit

    def emptyline(self):
        pass

    def default(self, line):
        print(f"  Unknown command: {line}. Type 'help' for available commands.")


def main():
    parser = argparse.ArgumentParser(
        prog="curatables",
        description="Search video sources, filter interactively, download with yt-dlp.",
    )
    parser.add_argument("query", nargs="*", help="Search terms")
    parser.add_argument(
        "-c", "--channel",
        help="Restrict search to a channel (handle, @handle, or URL)",
    )
    parser.add_argument(
        "-p", "--playlist",
        help="Fetch videos from a playlist URL instead of searching",
    )
    parser.add_argument(
        "-n", "--max-results", type=int, default=50,
        help="Maximum number of results (default: 50)",
    )
    parser.add_argument(
        "-e", "--exact", action="store_true",
        help="Search for the exact phrase (wrap query in quotes)",
    )

    # Non-interactive filtering flags
    parser.add_argument(
        "--filter", dest="filters", action="append", default=[], metavar="TEXT",
        help="Keep only videos matching TEXT (repeatable)",
    )
    parser.add_argument(
        "--exclude", dest="excludes", action="append", default=[], metavar="TEXT",
        help="Remove videos matching TEXT (repeatable)",
    )
    parser.add_argument(
        "--longer", type=float, metavar="MINS",
        help="Keep videos longer than MINS minutes",
    )
    parser.add_argument(
        "--shorter", type=float, metavar="MINS",
        help="Keep videos shorter than MINS minutes",
    )
    parser.add_argument(
        "--sort", dest="sort_field", metavar="FIELD",
        choices=["title", "channel", "duration", "views", "date"],
        help="Sort by: title, channel, duration, views, date",
    )
    parser.add_argument(
        "--sort-asc", action="store_true",
        help="Sort ascending (default is descending for numeric, ascending for text)",
    )

    # Output modes
    parser.add_argument(
        "--export", metavar="FILE",
        help="Export results to file and exit (no REPL)",
    )
    parser.add_argument(
        "--urls", action="store_true",
        help="Print URLs to stdout and exit (pipe to yt-dlp -a -)",
    )
    parser.add_argument(
        "--download", dest="auto_download", action="store_true",
        help="Download all results with yt-dlp and exit (no REPL)",
    )
    parser.add_argument(
        "--dl-args", default="",
        help="Extra arguments to pass to yt-dlp when using --download",
    )
    args = parser.parse_args()

    if args.playlist:
        print(f"Fetching playlist: {args.playlist} ...")
        videos = fetch_playlist(args.playlist, max_results=args.max_results)
    elif args.query:
        query = " ".join(args.query)
        if args.exact:
            query = f'"{query}"'
        print(f"Searching for: {query}", end="")
        if args.channel:
            print(f"  (channel: {args.channel})", end="")
        print(" ...")
        videos = search_videos(query, channel=args.channel, max_results=args.max_results)
    else:
        parser.error("provide search terms or --playlist URL")
    if not videos:
        print("No results found.")
        sys.exit(1)

    print(f"\nFound {len(videos)} videos:", file=sys.stderr)

    # Apply non-interactive filters
    for f_text in args.filters:
        pat = f_text.lower()
        videos = [
            v for v in videos
            if pat in v["title"].lower()
            or pat in v["channel"].lower()
            or pat in v["description"].lower()
        ]
    for x_text in args.excludes:
        pat = x_text.lower()
        videos = [
            v for v in videos
            if pat not in v["title"].lower()
            and pat not in v["channel"].lower()
            and pat not in v["description"].lower()
        ]
    if args.longer:
        videos = [v for v in videos if v["duration"] >= args.longer * 60]
    if args.shorter:
        videos = [v for v in videos if 0 < v["duration"] <= args.shorter * 60]
    if args.sort_field:
        key_map = {
            "title": "title", "channel": "channel",
            "duration": "duration", "views": "view_count", "date": "upload_date",
        }
        key_field = key_map[args.sort_field]
        text_fields = {"title", "channel"}
        default_reverse = args.sort_field not in text_fields
        reverse = not args.sort_asc if args.sort_asc else default_reverse
        videos = sorted(videos, key=lambda v: v[key_field] or "", reverse=reverse)

    if not videos:
        print("No videos remaining after filtering.")
        sys.exit(1)

    # Non-interactive output modes
    if args.urls:
        for v in videos:
            print(v["url"])
        sys.exit(0)

    if args.export:
        with open(args.export, "w") as f:
            for v in videos:
                f.write(f"# {v['title']}\n")
                f.write(f"{v['url']}\n")
        default_a = " ".join(YTDLP_DEFAULT_ARGS)
        print(f"\nExported {len(videos)} URLs to {args.export}")
        print(f"Download with: yt-dlp {default_a} -a {args.export}")
        sys.exit(0)

    if args.auto_download:
        ytdlp = shutil.which("yt-dlp")
        if not ytdlp:
            print("yt-dlp not found in PATH.")
            sys.exit(1)
        extra = shlex.split(args.dl_args) if args.dl_args else []
        urls = [v["url"] for v in videos]
        cmd_args = [ytdlp] + YTDLP_DEFAULT_ARGS + extra + urls
        print(f"Downloading {len(urls)} video(s)...")
        sys.exit(subprocess.call(cmd_args))

    # Interactive mode
    print(f"\n{len(videos)} videos:\n")
    print_table(videos)

    shell = DwnloadybShell(videos)
    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
