#!/usr/bin/env bash
set -euo pipefail

# Headless end-to-end test for watermark_type=dots: renders the
# encrypted dot code under Xvfb, screenshots it (full screen and an
# arbitrary off-grid crop), and confirms decode_dots.py recovers the
# correct username from both.
#
# Requires: Xvfb, python3 + PyQt5/python-xlib/cryptography/numpy/Pillow.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OVERLAY_DIR="$REPO_ROOT/overlay/linux"

DISPLAY_NUM=92
export DISPLAY=":${DISPLAY_NUM}"
WORKDIR="$(mktemp -d)"

XVFB_PID=""
OVERLAY_PID=""

cleanup() {
  for pid in "$OVERLAY_PID" "$XVFB_PID"; do
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

python3 -c "import secrets, pathlib; pathlib.Path('$WORKDIR/test.key').write_bytes(secrets.token_bytes(32))"

echo "[1/5] Starting Xvfb on $DISPLAY ..."
Xvfb "$DISPLAY" -screen 0 1024x768x24 -nolisten tcp &
XVFB_PID=$!
for _ in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.3; done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: Xvfb did not come up"; exit 1; }

echo "[2/5] Starting overlay with watermark_type=dots ..."
python3 "$OVERLAY_DIR/watermark_overlay.py" \
  --watermark-type dots --key-file "$WORKDIR/test.key" --opacity 200 --dot-size 3 &
OVERLAY_PID=$!
sleep 2

echo "[3/5] Screenshotting (full screen + an arbitrary off-grid crop) ..."
import -display "$DISPLAY" -window root "$WORKDIR/full.png"
python3 -c "
from PIL import Image
img = Image.open('$WORKDIR/full.png')
img.crop((300, 200, 450, 380)).save('$WORKDIR/crop.png')
"

echo "[4/5] Decoding the full screenshot ..."
FULL_OUT="$(python3 "$OVERLAY_DIR/decode_dots.py" "$WORKDIR/full.png" --key-file "$WORKDIR/test.key" --dot-size 3)"
echo "$FULL_OUT"
echo "$FULL_OUT" | grep -q "user:      $(whoami)" || { echo "FAIL: full-screenshot decode did not recover the expected user"; exit 1; }

echo "[5/5] Decoding the cropped, off-grid screenshot ..."
CROP_OUT="$(python3 "$OVERLAY_DIR/decode_dots.py" "$WORKDIR/crop.png" --key-file "$WORKDIR/test.key" --dot-size 3)"
echo "$CROP_OUT"
echo "$CROP_OUT" | grep -q "user:      $(whoami)" || { echo "FAIL: cropped-screenshot decode did not recover the expected user"; exit 1; }

echo "  OK: both full and cropped screenshots decoded to the correct user"

echo "--- Negative check: decoding with the wrong key must fail cleanly ---"
python3 -c "import secrets, pathlib; pathlib.Path('$WORKDIR/wrong.key').write_bytes(secrets.token_bytes(32))"
if python3 "$OVERLAY_DIR/decode_dots.py" "$WORKDIR/full.png" --key-file "$WORKDIR/wrong.key" --dot-size 3; then
  echo "FAIL: decode with the wrong key should not have succeeded"
  exit 1
fi
echo "  OK: wrong key correctly fails to decode"

echo "ALL CHECKS PASSED"
