#!/usr/bin/env bash
# check_codecs.sh — M5 / AC-Plays (codec half) engine.
#
# Probe every downloaded video and report whether it meets the client
# playback baseline (docs/ui-and-playback-plan.md §2):
#   video: h264, height <= 720, fps <= 30
#   audio: aac (or no audio stream)
#   container: mp4 with +faststart (moov atom before mdat)
#
# REPORT ONLY — never mutates files. Use it to (a) get the true M5
# baseline against the developer's real library, and (b) confirm 100%
# after a backfill. Mutating the existing curated library is a human
# decision (re-encoding is lossy); this script just tells you what would
# change.
#
# Usage:
#   scripts/check_codecs.sh [VIDEOS_DIR]
# Default VIDEOS_DIR: $HOME/curatables-data/videos
#
# Note on faststart: ffprobe can't report it directly, so we scan the
# top-level MP4 atom order with python3 (falls back to "unknown" if
# python3 is absent).

set -u

VIDEOS_DIR="${1:-$HOME/curatables-data/videos}"
MAX_H=720
MAX_FPS=30

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe not found on PATH" >&2
  exit 2
fi
if [ ! -d "$VIDEOS_DIR" ]; then
  echo "videos dir not found: $VIDEOS_DIR" >&2
  exit 2
fi

faststart_check() {
  # $1 = file. echoes "yes" / "no" / "unknown"
  if ! command -v python3 >/dev/null 2>&1; then echo "unknown"; return; fi
  python3 - "$1" <<'PY'
import sys
p = sys.argv[1]
try:
    with open(p, "rb") as f:
        moov = mdat = None
        while True:
            h = f.read(8)
            if len(h) < 8: break
            size = int.from_bytes(h[:4], "big"); atype = h[4:8]; start = f.tell()-8
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8: break
                size = int.from_bytes(ext, "big")
            elif size == 0:
                if atype == b"moov" and moov is None: moov = start
                elif atype == b"mdat" and mdat is None: mdat = start
                break
            if atype == b"moov" and moov is None: moov = start
            elif atype == b"mdat" and mdat is None: mdat = start
            if moov is not None and mdat is not None: break
            if size < 8: break
            f.seek(start+size)
    print("yes" if (moov is not None and mdat is not None and moov < mdat) else "no")
except Exception:
    print("unknown")
PY
}

total=0
passed=0
echo "Scanning $VIDEOS_DIR ..."
echo

# Find video files: downloads are videos/<id>/video.mp4; uploads handled
# by the caller pointing at the uploads dir if desired.
while IFS= read -r -d '' f; do
  total=$((total+1))
  vcodec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "$f" 2>/dev/null | head -1)
  height=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=nw=1:nk=1 "$f" 2>/dev/null | head -1)
  rfr=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=nw=1:nk=1 "$f" 2>/dev/null | head -1)
  acodec=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "$f" 2>/dev/null | head -1)
  fps=$(awk -F/ 'NF==2 && $2>0 {printf "%.2f", $1/$2; next} {print $1+0}' <<<"${rfr:-0/0}")
  fstart=$(faststart_check "$f")

  reasons=""
  [ "$vcodec" = "h264" ] || reasons="$reasons vcodec=$vcodec"
  { [ -n "$height" ] && [ "$height" -le "$MAX_H" ] 2>/dev/null; } || reasons="$reasons height=$height"
  awk -v f="${fps:-0}" -v m="$MAX_FPS" 'BEGIN{exit !(f<=m+0.5)}' || reasons="$reasons fps=$fps"
  [ -z "$acodec" ] || [ "$acodec" = "aac" ] || reasons="$reasons acodec=$acodec"
  [ "$fstart" = "yes" ] || [ "$fstart" = "unknown" ] || reasons="$reasons faststart=$fstart"

  if [ -z "$reasons" ]; then
    passed=$((passed+1))
    echo "PASS  $f"
  else
    echo "FAIL  $f  —$reasons"
  fi
done < <(find "$VIDEOS_DIR" -type f \( -name 'video.mp4' -o -name 'video.webm' -o -name 'video.mkv' \) -print0)

echo
if [ "$total" -eq 0 ]; then
  echo "M5: no video files found under $VIDEOS_DIR"
  exit 0
fi
pct=$(awk -v p="$passed" -v t="$total" 'BEGIN{printf "%.1f", 100*p/t}')
echo "M5: $passed/$total in baseline ($pct%) — target 100%"
[ "$passed" -eq "$total" ]
