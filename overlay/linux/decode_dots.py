#!/usr/bin/env python3
"""Offline decoder for the "dots" watermark type (see dotcode.py).

Given a screenshot (or, in principle, a photo -- see the caveat below)
that contains at least one complete watermark tile, finds it and
recovers the encrypted username + timestamp.

Because the exact pixel offset of a tile within an arbitrary
screenshot/crop is unknown, this brute-forces every candidate origin
within one tile "step" (tile size + gap): for each, it reads the 448
grid-cell intensities via a fast vectorized box filter, converts them
to bits by comparing each dot's footprint against its neighboring
(always-empty) gap pixels, and attempts an AES-GCM decrypt. AES-GCM's
authentication tag means a wrong offset/misread bits fail decryption
cleanly instead of producing a plausible-looking wrong answer, so the
first candidate that decrypts successfully is the real one.

Usage:
    python3 decode_dots.py SCREENSHOT.png [--key-file PATH] [--dot-size PX]

Requires: Pillow, numpy, cryptography (same as the renderer).

Caveat -- screenshots vs. photos: this has only been validated against
pixel-perfect screenshots (the case it was built for: the encoder and
decoder agree exactly on tile geometry with no distortion). A phone
photo of a screen introduces perspective warp, moire from the
screen's own pixel grid, lighting gradients, and lens blur/noise that
this brute-force-grid-alignment approach does not attempt to correct
for. Making that work reliably is a separate, harder computer-vision
problem (perspective rectification, moire-resistant dot sizing, real
error-correction rather than relying solely on the GCM tag) that needs
testing against actual photos on real hardware, which isn't possible
in this development environment -- treat photo decoding as unproven
until tested against real captures.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import numpy as np
from PIL import Image

import dotcode


def box_filtered(gray: np.ndarray, k: int) -> np.ndarray:
    """Returns an array where out[y, x] is the average of gray's
    k x k block starting at (y, x) -- i.e. exactly what a dot of size
    k drawn with its top-left corner at (x, y) would average over.
    Computed via an integral image so it's O(H*W) regardless of k.
    """
    h, w = gray.shape
    integral = np.zeros((h + 1, w + 1), dtype=np.float64)
    np.cumsum(np.cumsum(gray, axis=0, dtype=np.float64), axis=1, out=integral[1:, 1:])
    out_h, out_w = h - k + 1, w - k + 1
    if out_h <= 0 or out_w <= 0:
        return np.zeros((0, 0))
    box_sum = (
        integral[k : k + out_h, k : k + out_w]
        - integral[0:out_h, k : k + out_w]
        - integral[k : k + out_h, 0:out_w]
        + integral[0:out_h, 0:out_w]
    )
    return box_sum / (k * k)


_ROW_CHUNK = 60  # candidate y0 rows processed per batch; bounds peak memory


def try_decode(image_path: str, key: bytes, dot_size: int) -> "tuple[str, int, tuple[int, int]] | None":
    img = Image.open(image_path).convert("L")  # dots are neutral grey; luminance is enough
    gray = np.asarray(img, dtype=np.float32)

    avg = box_filtered(gray, dot_size)
    tile_w, tile_h = dotcode.tile_size(dot_size)
    cells = dotcode.grid_cell_positions(dot_size)  # (dx, dy) offsets within a tile, bit order
    n_bits = len(cells)

    avg_h, avg_w = avg.shape
    max_y0 = avg_h - tile_h
    max_x0 = avg_w - tile_w
    if max_x0 < 0 or max_y0 < 0:
        return None  # image smaller than one tile -- can't contain a full code

    cell = dot_size + 2
    ref_dx, ref_dy = cell // 2, cell // 2  # inside the always-empty gap between dots
    cand_h, cand_w = max_y0 + 1, max_x0 + 1

    # For every candidate origin at once (per row-chunk), gather the 448
    # dot-footprint values and their neighboring-gap reference values via
    # plain numpy slicing (each cell/reference is one shifted view over
    # the whole candidate grid -- no Python-level loop per candidate).
    for y0_start in range(0, cand_h, _ROW_CHUNK):
        y0_end = min(y0_start + _ROW_CHUNK, cand_h)
        chunk_h = y0_end - y0_start

        dot_vals = np.empty((n_bits, chunk_h, cand_w), dtype=np.float32)
        ref_vals = np.empty((n_bits, chunk_h, cand_w), dtype=np.float32)
        for i, (dx, dy) in enumerate(cells):
            dot_vals[i] = avg[y0_start + dy : y0_end + dy, dx : dx + cand_w]
            ry0, ry1 = y0_start + dy + ref_dy, y0_end + dy + ref_dy
            rx0, rx1 = dx + ref_dx, dx + ref_dx + cand_w
            if ry1 <= avg_h and rx1 <= avg_w:
                ref_vals[i] = avg[ry0:ry1, rx0:rx1]
            else:
                ref_vals[i] = dot_vals[i]  # near the image edge: no delta, reads as 0

        deltas = np.abs(dot_vals - ref_vals)
        # Self-calibrating threshold: each delta is one of only two
        # values by construction -- ~0 where no dot was drawn (bit 0)
        # or a consistent contrast where one was (bit 1) -- so the
        # midpoint between the min and max delta *for that candidate*
        # cleanly separates the two clusters, regardless of the actual
        # background brightness or configured opacity, and regardless
        # of how many of the 448 bits happen to be 1 vs 0 for this
        # particular ciphertext (a plain top/bottom-half-by-rank split
        # breaks whenever that ratio isn't close to 50/50, which is
        # common -- ciphertext bits are only *expected* to average out
        # to 50/50 over many samples, not guaranteed per-sample).
        lo = deltas.min(axis=0, keepdims=True)
        hi = deltas.max(axis=0, keepdims=True)
        threshold = (lo + hi) / 2
        bits = deltas > threshold  # shape (n_bits, chunk_h, cand_w)

        # Pack bits -> bytes for every candidate at once: byte b covers
        # bits[8b:8b+8], MSB first (matches dotcode.bytes_to_bits).
        n_bytes = n_bits // 8
        bits_u8 = bits.astype(np.uint8).reshape(n_bytes, 8, chunk_h, cand_w)
        weights = (1 << np.arange(7, -1, -1)).reshape(1, 8, 1, 1).astype(np.uint8)
        bytes_grid = (bits_u8 * weights).sum(axis=1).astype(np.uint8)  # (n_bytes, chunk_h, cand_w)

        for row in range(chunk_h):
            for col in range(cand_w):
                blob = bytes_grid[:, row, col].tobytes()
                result = dotcode.decrypt_identity(blob, key)
                if result is not None:
                    return result[0], result[1], (col, y0_start + row)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", help="Screenshot (PNG/etc.) to decode")
    parser.add_argument("--key-file", default=None, help=f"default: {dotcode.DEFAULT_KEY_PATH}")
    parser.add_argument("--dot-size", type=int, default=3, help="Must match the value used to render (default: 3)")
    args = parser.parse_args()

    key = dotcode.load_key(args.key_file)
    if key is None:
        path = args.key_file or dotcode.DEFAULT_KEY_PATH
        print(f"error: no valid 32-byte key at {path}", file=sys.stderr)
        return 2

    result = try_decode(args.image, key, args.dot_size)
    if result is None:
        print("No watermark decoded -- wrong key/dot-size, image too small, or image doesn't contain a tile.")
        return 1

    user, ts, (x0, y0) = result
    when = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    print("Watermark decoded:")
    print(f"  user:      {user}")
    print(f"  timestamp: {when.isoformat()}")
    print(f"  found at:  ({x0}, {y0}) in {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
