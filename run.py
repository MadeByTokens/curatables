#!/usr/bin/env python3
"""curatables server entry point."""

import argparse
import shutil
import sys


def _tool_version(cmd, args=("--version",)):
    """Best-effort short version string for a system binary. Returns '' on failure."""
    import subprocess
    path = shutil.which(cmd)
    if not path:
        return ""
    try:
        out = subprocess.run(
            [path, *args], capture_output=True, text=True, timeout=5
        )
        first = (out.stdout or out.stderr).splitlines()[0].strip() if (out.stdout or out.stderr) else ""
        return first
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
        for mod in ("fastapi", "uvicorn", "jinja2", "python_multipart", "yt_dlp", "curl_cffi", "zeroconf", "itsdangerous", "reportlab", "prometheus_client"):
            try:
                m = __import__(mod)
                v = getattr(m, "__version__", "?")
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

    # prometheus_client is imported unconditionally by app.services.metrics
    # (cheap, ~64 KB wheel, no transitive deps), so it is a hard runtime
    # requirement even when config.server.prometheus_enabled=False — the
    # /metrics route exists and the recorders are constructed regardless;
    # only the registry contents are gated.
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

    import uvicorn
    from app.main import create_app

    app = create_app(data_dir=args.data_dir, log_level=args.log_level)

    import logging
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
    uvicorn.run(app, host=host, port=port, log_level=log_level,
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
