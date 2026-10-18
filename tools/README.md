# tools/

Helpers that are not part of the server.

- `curatables-cli.py` — an interactive terminal tool for power users: search
  and filter videos on any yt-dlp site and download them to a folder. It talks
  to the `yt-dlp` command directly and does not touch a curatables library.
  Run `python tools/curatables-cli.py --help`.
- `make_icons.py` — draws the browser and home-screen icons in
  `app/static/icons/` (a maple leaf on the kid screens' accent colour).
  It needs Pillow and the Noto Color Emoji font:
  `python tools/make_icons.py [path/to/NotoColorEmoji.ttf]`.
