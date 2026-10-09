"""QR codes (SVG) for opening the kid address on a tablet."""
from __future__ import annotations

import io
from types import ModuleType

segno: ModuleType | None
try:
    import segno
except ImportError:
    segno = None


def qr_available() -> bool:
    return segno is not None


def qr_svg(text: str) -> bytes | None:
    """An SVG QR code for `text`, or None when segno is not installed."""
    if segno is None:
        return None
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=6, border=2,
                                     dark="#222", light="#fff")
    return out.getvalue()
