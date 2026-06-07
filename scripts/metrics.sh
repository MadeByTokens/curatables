#!/usr/bin/env bash
# metrics.sh — print the shell/ffprobe-measurable metrics from
# docs/ui-and-playback-plan.md §7a (M1–M7) with current value vs target.
#
# Run before Phase 1 and at each phase end; paste results into §7a.
# M5 (library codec compliance) is delegated to check_codecs.sh and only
# runs when --codecs is passed (it ffprobes every file, which is slow and
# touches the real data dir).
#
# Usage:
#   scripts/metrics.sh [--codecs] [VIDEOS_DIR]
set -u
cd "$(dirname "$0")/.." || exit 1

CODECS=0
VIDEOS_DIR="$HOME/curatables-data/videos"
for a in "$@"; do
  case "$a" in
    --codecs) CODECS=1 ;;
    *) VIDEOS_DIR="$a" ;;
  esac
done

BASE_TPL="app/templates/base"
KID_TPL="$BASE_TPL/kid"
CSS_FILES="app/static/kid/style.css app/static/kid/theme-calm.css app/static/kid/theme-playful.css app/static/parent/style.css"

hr(){ printf '%s\n' "------------------------------------------------------------"; }

echo "Curatables delivery-plan metrics ($(date +%Y-%m-%d 2>/dev/null || echo n/a))"
hr

# M1 — inline style= attrs in base templates  (target 0)
m1=$(grep -ro 'style="' "$BASE_TPL" | wc -l | tr -d ' ')
printf "M1  inline style= in %s ......... %s   (target 0)\n" "$BASE_TPL" "$m1"

# M2 — hardcoded #2a9d8f literals in app/  (target 1 = token def only)
m2=$(grep -roI --exclude-dir=__pycache__ '#2a9d8f' app/ | wc -l | tr -d ' ')
printf "M2  #2a9d8f literals in app/ ..... %s   (target 1)\n" "$m2"

# M3 — total app CSS size  (target <= 22528 bytes)
m3=$(cat $CSS_FILES 2>/dev/null | wc -c | tr -d ' ')
printf "M3  total app CSS bytes .......... %s   (target <= 22528)\n" "$m3"

# M4 — kid templates shipping any <script>  (target 2: watch, upload)
m4=0; m4list=""
for f in "$KID_TPL"/*.html; do
  if grep -ql '<script' "$f"; then m4=$((m4+1)); m4list="$m4list $(basename "$f")"; fi
done
printf "M4  kid templates with <script> .. %s   (target 2)  [%s ]\n" "$m4" "$m4list"

# M7 — kid JS bytes on non-watch/non-upload pages  (target 0)
hr
echo "M7  kid <script> bytes per page (non watch/upload target 0):"
total_other=0
for f in "$KID_TPL"/*.html; do
  name=$(basename "$f")
  bytes=$(awk 'BEGIN{inblk=0;n=0}
    /<script/{inblk=1}
    inblk{n+=length($0)+1}
    /<\/script>/{inblk=0}
    END{print n}' "$f")
  case "$name" in
    watch.html|upload.html) note="(allowed)";;
    *) note=""; total_other=$((total_other+bytes));;
  esac
  [ "$bytes" -gt 0 ] && printf "      %-22s %6s bytes %s\n" "$name" "$bytes" "$note"
done
printf "    -> non-watch/upload total: %s bytes (target 0)\n" "$total_other"

# M6 — token adherence: distinct font-size / colour / spacing values
hr
echo "M6  distinct value sets (each must be a subset of the token set):"
echo "    font-size values:"
grep -rhoE 'font-size:[^;\"]+' app/static $BASE_TPL 2>/dev/null | sed 's/^/      /' | sort -u
echo "    colour literals (hex / rgb):"
grep -rhoE '#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)' app/static $BASE_TPL 2>/dev/null | sed 's/^/      /' | sort -u
echo "    (review the above against the :root token set in kid/parent style.css)"

# M5 — library codec compliance (optional, slow)
hr
if [ "$CODECS" -eq 1 ]; then
  echo "M5  library codec compliance:"
  scripts/check_codecs.sh "$VIDEOS_DIR"
else
  echo "M5  (skipped — pass --codecs [VIDEOS_DIR] to run check_codecs.sh)"
fi
