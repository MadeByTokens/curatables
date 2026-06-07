"""Operational endpoints: /healthz and /metrics.

A 24/7 family appliance has one dominant failure mode: silent
breakage that nobody notices for weeks. ``/healthz`` is a public,
no-auth, fast endpoint that monitoring (uptime-kuma, a curl-and-
grep cron, the upgrade docs' post-restart smoke check) can poll
to confirm the server is up *and* its database is reachable.

``/metrics`` exposes Prometheus-format counters when the operator
opts in via ``config.server.prometheus_enabled``. Vanilla self-
hosters don't get the surface; people who wire Curatables into
Prometheus flip the flag.
"""

import sqlite3
import time

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from app import __version__
from app.dependencies import get_db


router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz(request: Request, db: sqlite3.Connection = Depends(get_db)):
    """Liveness + readiness probe.

    Returns 200 when the process is up and the DB answers a trivial
    query. Returns 503 with the error string when the DB check fails
    so external monitors can flag degraded state without parsing the
    body. The JSON body always includes version + uptime so an
    operator can confirm what they're talking to.
    """
    start = getattr(request.app.state, "start_time", None)
    uptime = round(time.monotonic() - start, 3) if start is not None else None

    db_status: str
    http_status = 200
    try:
        row = db.execute("SELECT 1 AS one").fetchone()
        if row is None or row["one"] != 1:
            db_status = "error: unexpected SELECT 1 result"
            http_status = 503
        else:
            db_status = "ok"
    except sqlite3.Error as exc:
        db_status = f"error: {exc.__class__.__name__}: {exc}"
        http_status = 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if http_status == 200 else "degraded",
            "version": __version__,
            "uptime_seconds": uptime,
            "db": db_status,
        },
    )


@router.get("/metrics")
def metrics(request: Request):
    """Prometheus exposition.

    404 when ``config.server.prometheus_enabled`` is False so the
    route is invisible on default installs. When enabled, returns
    the registry rendered in Prometheus 0.0.4 text format and
    refreshes the uptime gauge on each scrape so it doesn't drift.
    """
    metrics_svc = request.app.state.metrics
    if not metrics_svc.enabled:
        return PlainTextResponse("Not found", status_code=404)

    start = getattr(request.app.state, "start_time", None)
    if start is not None:
        metrics_svc.set_uptime(time.monotonic() - start)

    body, content_type = metrics_svc.render()
    return Response(content=body, media_type=content_type)
