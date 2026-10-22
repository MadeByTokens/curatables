# tools/

Helpers that are not part of the server.

- `curatables-cli.py` — an interactive terminal tool for power users: search
  and filter videos on any yt-dlp site and download them to a folder. It talks
  to the `yt-dlp` command directly and does not touch a curatables library.
  Run `python tools/curatables-cli.py --help`.
- `make_guides.py` — makes the animated guides: it starts a throw-away
  Curatables with a made-up family (Mia, Leo, pictures it draws itself)
  and a pretend YouTube, acts out each task in a browser and saves a
  screenshot of every step with the spot to tap. Output:
  `app/static/guides/` (played on the box under Help → Watch how and
  Help → Show me how; the kids' guide in English, Spanish and Portuguese)
  and `docs/guide/img/anim/` (moving pictures for the written guides).
  Run it again when a screen in a tour changes:
  `python tools/make_guides.py [--only kids|parents] [--tour ID]`.
  It needs Playwright with Chromium, Pillow and ffmpeg (not on the box).
- `make_icons.py` — draws the browser and home-screen icons in
  `app/static/icons/` (a maple leaf on the kid screens' accent colour).
  It needs Pillow and the Noto Color Emoji font:
  `python tools/make_icons.py [path/to/NotoColorEmoji.ttf]`.
