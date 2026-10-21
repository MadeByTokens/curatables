"""Check a yt-dlp the way Curatables uses it, before it replaces the one
in use (see scripts/ytdlp_update.py).

    python -I scripts/ytdlp-check.py DATA_DIR [--site DIR] [--offline]
    python -I scripts/ytdlp-check.py DATA_DIR --busy

Run as the service user with the Curatables venv's Python. --site puts a
staged yt-dlp ahead of the installed one. Prints one line of JSON:
{"version", "solver", "ok", "checks": [{"name", "ok", "detail", "seconds"}]}.
Online it fetches a video's details, a search, a channel's list and the
first bytes of the file a download would fetch, through the same code
and settings (cookies, impersonation) as the server. --busy prints how
many downloads are running instead.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Long-lived public videos and channel, unlikely to go away.
VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
CHANNEL_URL = "https://www.youtube.com/@YouTube/videos"
SEARCH = "trains for kids"
FIRST_BYTES = 64 * 1024


def _check(name: str, fn) -> dict:
    started = time.monotonic()
    try:
        ok, detail = fn()
    except Exception as e:  # a broken yt-dlp can fail in any way
        ok, detail = False, f"{type(e).__name__}: {e}"
    return {"name": name, "ok": bool(ok), "detail": str(detail)[:300],
            "seconds": round(time.monotonic() - started, 1)}


def _busy(data_dir: str) -> int:
    from app.db.connection import get_db_path
    db = sqlite3.connect(f"file:{get_db_path(Path(data_dir))}?mode=ro", uri=True, timeout=10)
    try:
        return int(db.execute("SELECT COUNT(*) FROM videos WHERE download_status = 'downloading'").fetchone()[0])
    finally:
        db.close()


def run(data_dir: str, site: str = "", offline: bool = False) -> dict:
    try:
        import yt_dlp
    except Exception as e:  # a broken release can fail to import in any way
        return {"version": "", "solver": "", "ok": False,
                "checks": [{"name": "loads", "ok": False, "detail": f"{type(e).__name__}: {e}"[:300],
                            "seconds": 0}]}

    from app.backends.ytdlp import YtdlpBackend, download_format
    from app.config import load_config

    backend = YtdlpBackend.from_config(load_config(data_dir))
    found = {}

    def loads():
        from yt_dlp.version import __version__
        found["version"] = __version__
        if site and not Path(yt_dlp.__file__).resolve().is_relative_to(Path(site).resolve()):
            return False, f"loaded {yt_dlp.__file__}, not the one being checked"
        return True, __version__

    def solver():
        # Without YouTube's challenge solver most formats go missing.
        from importlib.metadata import version
        import yt_dlp_ejs  # noqa: F401
        found["solver"] = version("yt-dlp-ejs")
        return True, found["solver"]

    def settings():
        with yt_dlp.YoutubeDL({**backend._base_opts(), "format": download_format(720)}):
            return True, "takes Curatables' settings"

    def why() -> str:
        error = backend.last_error
        return error.message if error else "nothing came back"

    def details():
        meta = backend.fetch_video_info(VIDEO_URL)
        return (meta is not None and bool(meta.title) and meta.duration > 0), (meta.title if meta else why())

    def search():
        hits = backend.search(SEARCH, max_results=3)
        return bool(hits), f"{len(hits)} results" if hits else why()

    def channel():
        _title, videos = backend.fetch_channel_videos(CHANNEL_URL, max_results=3)
        return bool(videos), f"{len(videos)} videos" if videos else why()

    def download():
        from yt_dlp.networking import Request
        opts = {**backend._base_opts(), "format": download_format(720), "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(VIDEO_URL, download=False)
            parts = info.get("requested_formats") or [info]
            for part in parts:
                headers = dict(part.get("http_headers") or {}, Range=f"bytes=0-{FIRST_BYTES - 1}")
                with ydl.urlopen(Request(part["url"], headers=headers)) as resp:
                    if not resp.read(FIRST_BYTES):
                        return False, f"format {part.get('format_id')} sent nothing"
            return True, "+".join(str(p.get("format_id")) for p in parts)

    checks = [_check("loads", loads), _check("solver", solver), _check("settings", settings)]
    if not offline and checks[0]["ok"]:
        checks += [_check(n, f) for n, f in (("details", details), ("search", search),
                                               ("channel", channel), ("download", download))]
    return {"version": found.get("version", ""), "solver": found.get("solver", ""),
            "ok": all(c["ok"] for c in checks), "checks": checks}


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("data_dir")
    p.add_argument("--site", default="")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--busy", action="store_true")
    args = p.parse_args(argv)
    if args.site:
        sys.path.insert(0, args.site)
    sys.path.insert(1 if args.site else 0, str(ROOT))
    if args.busy:
        print(json.dumps({"downloading": _busy(args.data_dir)}))
        return 0
    result = run(args.data_dir, args.site, args.offline)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
