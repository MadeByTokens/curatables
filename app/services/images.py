"""Small helpers for user-supplied image files."""

from __future__ import annotations

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_extension(data: bytes) -> str | None:
    """Return the file extension matching the image's magic bytes, or None
    if the bytes are not a JPEG, PNG, WebP or GIF."""
    head = data[:12]
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def validate_image(data: bytes) -> str:
    """Return the extension for a valid image, else raise ValueError."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too big.")
    ext = image_extension(data)
    if ext is None:
        raise ValueError("That file isn't a picture.")
    return ext
