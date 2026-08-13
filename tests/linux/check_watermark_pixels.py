#!/usr/bin/env python3
"""Compares two root-window screenshots (before/after starting the
overlay) and asserts the overlay actually painted visible watermark
pixels, roughly matching the expected neutral-grey tint.
"""
import sys

from PIL import Image


def main() -> int:
    before_path, after_path = sys.argv[1], sys.argv[2]
    before = Image.open(before_path).convert("RGB")
    after = Image.open(after_path).convert("RGB")
    if before.size != after.size:
        print(f"FAIL: screenshot size mismatch {before.size} vs {after.size}")
        return 1

    before_px = before.load()
    after_px = after.load()
    w, h = before.size

    changed = 0
    plausible = 0
    for y in range(0, h, 2):  # sample every other row/col for speed
        for x in range(0, w, 2):
            b = before_px[x, y]
            a = after_px[x, y]
            if a != b:
                changed += 1
                # QColor(110, 110, 110, alpha) painted over a lighter
                # background should read as a roughly-neutral grey that's
                # darker than the background, not a random color shift.
                if max(a) - min(a) <= 12 and sum(a) / 3 < 250:
                    plausible += 1

    print(f"changed pixels (sampled): {changed}, watermark-like: {plausible}")
    if changed < 200:
        print("FAIL: overlay did not visibly change the screen")
        return 1
    if plausible < 100:
        print("FAIL: changed pixels don't look like the expected grey watermark tint")
        return 1
    print("  OK: watermark tiles are visibly rendered on screen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
