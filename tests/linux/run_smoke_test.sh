#!/usr/bin/env bash
set -euo pipefail

# Headless smoke test for the Linux watermark overlay prototype.
# Boots a virtual X server + minimal WM, launches an xterm as a stand-in
# "document window", starts the overlay, and checks:
#   1. the overlay actually renders watermark pixels over the desktop
#   2. it covers the full screen geometry
#   3. mouse clicks pass through it to the window underneath (click-through)
#
# Requires: Xvfb, openbox, xterm, xdotool, imagemagick (import), python3+Pillow.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OVERLAY_SCRIPT="$REPO_ROOT/overlay/linux/watermark_overlay.py"

DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"
WORKDIR="$(mktemp -d)"

XVFB_PID=""
WM_PID=""
XTERM_PID=""
OVERLAY_PID=""

cleanup() {
  for pid in "$OVERLAY_PID" "$XTERM_PID" "$WM_PID" "$XVFB_PID"; do
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "[1/6] Starting Xvfb on $DISPLAY ..."
Xvfb "$DISPLAY" -screen 0 1024x768x24 -nolisten tcp &
XVFB_PID=$!
for _ in $(seq 1 20); do
  xdpyinfo >/dev/null 2>&1 && break
  sleep 0.5
done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: Xvfb did not come up"; exit 1; }

echo "[2/6] Starting openbox (minimal EWMH window manager) ..."
openbox &
WM_PID=$!
sleep 1

echo "[3/6] Starting xterm (stand-in document window) ..."
xterm -bg white -fg black -geometry 80x24+50+50 -T smoke-xterm -e "sleep 60" &
XTERM_PID=$!
sleep 1
XTERM_WIN=$(xdotool search --name smoke-xterm | head -n1)
[ -n "$XTERM_WIN" ] || { echo "FAIL: xterm window not found"; exit 1; }

echo "[4/6] Baseline screenshot (before overlay) ..."
import -display "$DISPLAY" -window root "$WORKDIR/before.png"

echo "[5/6] Starting watermark overlay ..."
python3 "$OVERLAY_SCRIPT" --text "{user}" --opacity 60 --font-size 20 &
OVERLAY_PID=$!
sleep 2

import -display "$DISPLAY" -window root "$WORKDIR/after.png"

echo "[6/6] Verifying render + full-screen coverage + click-through ..."
OVERLAY_WIN=$(xdotool search --name watermark-overlay | head -n1)
[ -n "$OVERLAY_WIN" ] || { echo "FAIL: overlay window not found"; exit 1; }

eval "$(xdotool getwindowgeometry --shell "$OVERLAY_WIN")"
if [ "$WIDTH" != "1024" ] || [ "$HEIGHT" != "768" ]; then
  echo "FAIL: overlay does not cover full screen (${WIDTH}x${HEIGHT})"
  exit 1
fi
echo "  OK: overlay covers full screen (${WIDTH}x${HEIGHT})"

python3 "$SCRIPT_DIR/check_watermark_pixels.py" "$WORKDIR/before.png" "$WORKDIR/after.png"

echo "  Click-through: clicking inside xterm area (behind overlay) ..."
xdotool mousemove 90 65 click 1
sleep 0.5
ACTIVE_WIN=$(xdotool getactivewindow)
if [ "$ACTIVE_WIN" != "$XTERM_WIN" ]; then
  echo "FAIL: click did not reach xterm (active window: $ACTIVE_WIN, expected: $XTERM_WIN)"
  exit 1
fi
echo "  OK: click passed through the overlay and activated xterm"

echo "ALL CHECKS PASSED"
