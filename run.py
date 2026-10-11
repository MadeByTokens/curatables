#!/usr/bin/env python3
"""curatables server entry point.

Third-party packages are imported only after check_dependencies() has
had a chance to report the missing ones.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from importlib.metadata import version as package_version


def _tool_version(cmd, args=("--version",)):
    """Best-effort short version string for a system binary. Returns '' on failure."""
    path = shutil.which(cmd)
    if not path:
        return ""
    try:
        out = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=5
        )
        lines = (out.stdout or out.stderr).splitlines()
        return lines[0].strip() if lines else ""
    except Exception:
        return ""


def check_dependencies(verbose=False):
    """Check that all required dependencies are available.

    This is one of two places that enumerate what Curatables needs;
    the other is docs/dependencies.md (the canonical bill of
    materials used when packaging the project). When you add or
    remove a check here, update that file in the same commit so the
    two stay in sync.

    With verbose=True, prints resolved versions for every dependency
    (Python, ffmpeg, deno, and each Python package) before the
    warnings/errors summary. Used by `run.py --check` as a standalone
    acceptance test for install.sh.
    """
    errors = []
    warnings = []

    if verbose:
        print(f"python:          {sys.version.split()[0]} ({sys.executable})")
        ff = _tool_version("ffmpeg", ("-version",))
        print(f"ffmpeg:          {ff or 'NOT FOUND'}")
        de = _tool_version("deno")
        print(f"deno:            {de or 'NOT FOUND'}")
        for mod in ("fastapi", "uvicorn", "jinja2", "python_multipart", "yt_dlp", "curl_cffi", "zeroconf", "itsdangerous", "reportlab", "prometheus_client", "segno"):
            try:
                m = __import__(mod)
                # yt-dlp keeps its version in yt_dlp.version.__version__, not
                # on the top-level module; fall back to importlib metadata for
                # anything else that doesn't expose __version__.
                v = getattr(m, "__version__", None)
                if v is None and mod == "yt_dlp":
                    v = getattr(getattr(m, "version", None), "__version__", None)
                if v is None:
                    try:
                        v = package_version(mod.replace("_", "-"))
                    except Exception:
                        v = "?"
                print(f"{mod+':':<17}{v}")
            except ImportError:
                print(f"{mod+':':<17}NOT INSTALLED")
        print()

    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        errors.append("yt-dlp is not installed. Install with: pip install yt-dlp")

    try:
        import fastapi  # noqa: F401
    except ImportError:
        errors.append("FastAPI is not installed. Install with: pip install fastapi")

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        errors.append("uvicorn is not installed. Install with: pip install uvicorn")

    try:
        import jinja2  # noqa: F401
    except ImportError:
        errors.append("Jinja2 is not installed. Install with: pip install jinja2")

    try:
        import multipart  # noqa: F401  (installed as `python-multipart`, imported as `multipart`)
    except ImportError:
        errors.append("python-multipart is not installed. Form submissions (login, settings, add) will 500. Install with: pip install 'python-multipart>=0.0.6'")

    try:
        import itsdangerous  # noqa: F401
    except ImportError:
        errors.append("itsdangerous is not installed. Session cookie signing will fail at startup. Install with: pip install 'itsdangerous>=2.0'")

    if not shutil.which("ffmpeg"):
        errors.append("ffmpeg is not found in PATH. Install with: apt install ffmpeg")

    # Optional but strongly recommended
    if not shutil.which("deno"):
        warnings.append("Deno is not found in PATH. Required by yt-dlp for YouTube. Install from: https://deno.land")

    # Optional but recommended
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        warnings.append("curl_cffi is not installed. Browser impersonation (anti-bot) won't work. Install with: pip install 'curl_cffi>=0.10,<0.15'")

    # Optional: mDNS advertisement (makes curatables.local discoverable
    # on the LAN). Skipped silently if absent, the server still works.
    try:
        import zeroconf  # noqa: F401
    except ImportError:
        warnings.append("zeroconf is not installed. Server will not advertise itself over mDNS (curatables.local won't resolve). Install with: pip install zeroconf")

    # Optional: reportlab powers the PDF export in shared curation.
    # Without it, /parent/channels/{id}/export?format=pdf returns a
    # clean error; .ytc and .txt export still work.
    try:
        import reportlab  # noqa: F401
    except ImportError:
        warnings.append("reportlab is not installed. PDF export in shared curation will be disabled. Install with: pip install 'reportlab>=4.0,<5.0'")

    try:
        import segno  # noqa: F401
    except ImportError:
        warnings.append("segno is not installed. The tablet-setup QR code will not be shown. Install with: pip install 'segno>=1.6,<2.0'")

    # Required even with prometheus_enabled off: app.services.metrics
    # always imports it.
    try:
        import prometheus_client  # noqa: F401
    except ImportError:
        errors.append("prometheus_client is not installed. Install with: pip install 'prometheus_client>=0.20,<1.0'")

    if warnings:
        print("Warnings:\n")
        for w in warnings:
            print(f"  ! {w}")
        print()

    if errors:
        print("Missing dependencies:\n")
        for e in errors:
            print(f"  - {e}")
        print()
        print("See docs/dependencies.md for the full bill of materials.")
        print()
        sys.exit(1)

    if verbose:
        print("All hard-required dependencies present.")
        if warnings:
            print(f"({len(warnings)} optional dependency warnings above.)")


def check_data_mount(data_dir):
    """With CURATABLES_REQUIRE_MOUNT=1 (set by
    scripts/setup-external-drive.sh), refuse to start unless the data dir
    is a mounted filesystem. Without this, a drive that failed to mount
    leaves an empty directory on the SD card: the app would start fresh,
    offer first-run setup to anyone on the network, and download new
    videos onto the SD card."""
    if os.environ.get("CURATABLES_REQUIRE_MOUNT") != "1":
        return
    path = os.path.abspath(data_dir or os.path.expanduser("~/curatables-data"))
    if not os.path.ismount(path):
        print(f"ERROR: {path} is not a mounted drive (CURATABLES_REQUIRE_MOUNT=1).\n"
              "Plug in / mount the media drive, then restart curatables.",
              file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="curatables",
        description="Start the curatables server for curating YouTube content.",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="HTTP port (default: from config or 8080)",
    )
    parser.add_argument(
        "--host", default=None,
        help="Listen address (default: from config or 0.0.0.0)",
    )
    parser.add_argument(
        "--data-dir",
        help="Data directory (default: ~/curatables-data)",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: from config or info)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Run the dependency check, print resolved versions, and exit. "
             "Used by scripts/install.sh as an acceptance test.",
    )
    args = parser.parse_args()

    if args.check:
        check_dependencies(verbose=True)
        return

    check_dependencies()
    check_data_mount(args.data_dir)

    import uvicorn
    from app.main import create_app

    app = create_app(data_dir=args.data_dir, log_level=args.log_level)
    logger = logging.getLogger("curatables")

    config = app.state.config
    host = args.host if args.host is not None else config.server.host
    port = args.port if args.port is not None else config.server.port
    log_level = args.log_level or config.server.log_level

    # Mutate the live config so the FastAPI lifespan (and therefore the
    # mDNS advertiser, which reads config.server.port) sees the real
    # listening port when the user overrode it on the command line.
    config.server.host = host
    config.server.port = port

    logger.info("curatables server starting on http://%s:%s", host, port)
    logger.info("Kid UI:           http://localhost:%s/", port)
    logger.info("Parent dashboard: http://localhost:%s/parent/", port)

    # Cap graceful shutdown so Ctrl+C exits promptly even when browsers
    # are holding keep-alive sockets on /media/video — HTML5 <video>
    # preload parks a range-request connection on every open watch page.
    # Requests are logged once, by RequestLoggingMiddleware (with the
    # request id and timing), so uvicorn's own access log is off.
    uvicorn.run(app, host=host, port=port, log_level=log_level,
                access_log=False, timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
