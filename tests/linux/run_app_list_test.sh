#!/usr/bin/env bash
set -euo pipefail

# Headless test for mode=app_list: the watermark must stay hidden while
# only a non-listed app is open, appear once a listed app opens too,
# and hide again once that app closes.
#
# Each screenshot is diffed against a same-window-layout baseline taken
# *without* the overlay running, so window-manager chrome (title bars,
# borders) never gets mistaken for watermark pixels -- only what the
# overlay itself adds shows up as a diff.
#
# Requires: Xvfb, openbox, xterm, xdotool, imagemagick (import), python3+Pillow.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OVERLAY_SCRIPT="$REPO_ROOT/overlay/linux/watermark_overlay.py"

DISPLAY_NUM=95
export DISPLAY=":${DISPLAY_NUM}"
WORKDIR="$(mktemp -d)"

XVFB_PID=""
WM_PID=""
OTHER_PID=""
SENSITIVE_PID=""
OVERLAY_PID=""

cleanup() {
  for pid in "$OVERLAY_PID" "$SENSITIVE_PID" "$OTHER_PID" "$WM_PID" "$XVFB_PID"; do
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "[1/7] Starting Xvfb + openbox on $DISPLAY ..."
Xvfb "$DISPLAY" -screen 0 1024x768x24 -nolisten tcp &
XVFB_PID=$!
for _ in $(seq 1 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.3; done
xdpyinfo >/dev/null 2>&1 || { echo "FAIL: Xvfb did not come up"; exit 1; }
openbox &
WM_PID=$!
sleep 1

echo "[2/7] Opening the non-listed app (OtherApp) and taking a no-overlay baseline ..."
xterm -class OtherApp -bg white -geometry 60x15+50+50 -T other-app -e "sleep 60" &
OTHER_PID=$!
sleep 1
import -display "$DISPLAY" -window root "$WORKDIR/base_other.png"

echo "[3/7] Also opening the listed app (SensitiveApp) and taking a second no-overlay baseline ..."
xterm -class SensitiveApp -bg white -geometry 60x15+400+300 -T sensitive-doc -e "sleep 60" &
SENSITIVE_PID=$!
sleep 1
import -display "$DISPLAY" -window root "$WORKDIR/base_both.png"

SENSITIVE_WIN=$(xdotool search --name sensitive-doc | head -n1)
[ -n "$SENSITIVE_WIN" ] || { echo "FAIL: sensitive app window not found"; exit 1; }
xdotool windowkill "$SENSITIVE_WIN" 2>/dev/null || true
sleep 1

echo "[4/7] Starting overlay: mode=app_list, apps=sensitiveapp (only OtherApp open) ..."
python3 "$OVERLAY_SCRIPT" --mode app_list --apps "sensitiveapp" --watermark-type account &
OVERLAY_PID=$!
sleep 2
import -display "$DISPLAY" -window root "$WORKDIR/state_hidden1.png"

echo "[5/7] Re-opening the listed app (SensitiveApp) ..."
xterm -class SensitiveApp -bg white -geometry 60x15+400+300 -T sensitive-doc -e "sleep 60" &
SENSITIVE_PID=$!
sleep 1.5
import -display "$DISPLAY" -window root "$WORKDIR/state_shown.png"

echo "[6/7] Closing the listed app again ..."
SENSITIVE_WIN2=$(xdotool search --name sensitive-doc | head -n1)
[ -n "$SENSITIVE_WIN2" ] || { echo "FAIL: sensitive app window not found (2nd open)"; exit 1; }
xdotool windowkill "$SENSITIVE_WIN2" 2>/dev/null || true
sleep 1.5
import -display "$DISPLAY" -window root "$WORKDIR/state_hidden2.png"

echo "[7/7] Verifying visibility toggled correctly ..."
python3 - "$WORKDIR" <<'PYEOF'
import sys
from PIL import Image

workdir = sys.argv[1]

def diff_count(path_a, path_b):
    a = Image.open(path_a).convert("RGB")
    b = Image.open(path_b).convert("RGB")
    ap, bp = a.load(), b.load()
    w, h = a.size
    count = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if ap[x, y] != bp[x, y]:
                count += 1
    return count

hidden1 = diff_count(f"{workdir}/base_other.png", f"{workdir}/state_hidden1.png")
shown = diff_count(f"{workdir}/base_both.png", f"{workdir}/state_shown.png")
hidden2 = diff_count(f"{workdir}/base_other.png", f"{workdir}/state_hidden2.png")
print(f"diff vs no-overlay baseline: hidden1={hidden1} shown={shown} hidden2={hidden2}")

if hidden1 > 50:
    print("FAIL: watermark visible while only the non-listed app was open")
    sys.exit(1)
if shown < 200:
    print("FAIL: watermark did not appear once the listed app opened")
    sys.exit(1)
if hidden2 > 50:
    print("FAIL: watermark stayed visible after the listed app closed")
    sys.exit(1)
print("  OK: watermark hidden -> shown -> hidden, tracking the configured app list")
PYEOF

echo "ALL CHECKS PASSED"
