"""Stub for the impersonation submodule the real yt-dlp ships.

``app/backends/ytdlp.py`` does ``from yt_dlp.networking.impersonate
import ImpersonateTarget`` inside a try/except, so even returning a
no-op class is enough to avoid the silent fallback path."""


class ImpersonateTarget:
    @classmethod
    def from_str(cls, value):
        return cls()
