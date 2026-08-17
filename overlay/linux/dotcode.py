#!/usr/bin/env python3
"""Shared encode/decode logic for the "dots" watermark type.

Instead of rendering readable text, this mode tiles a grid of tiny
dots across the screen. Each tile encodes the same fixed-size payload
(username + timestamp), AES-GCM-encrypted with a shared key file, as a
bit pattern: bit 1 -> a faint dot is drawn, bit 0 -> nothing is drawn.
A screenshot or photo doesn't show readable text to casually recognize
and crop/inpaint away, and the payload can only be read back with the
key -- this module is the part shared between the renderer
(watermark_overlay.py) and the offline decoder (decode_dots.py).

Payload layout (28 bytes plaintext, fixed-length so grid geometry
never depends on username length):
    bytes 0..23   username, UTF-8, NUL-padded/truncated to 24 bytes
    bytes 24..27  unix timestamp (seconds), big-endian uint32

Encrypted with AES-256-GCM: a fresh random 12-byte nonce is generated
per render, so the ciphertext (and therefore the dot pattern) changes
every repaint even though the plaintext is often the same -- this
keeps the tag/nonce genuinely random rather than reusing one, which
AES-GCM requires for its security guarantees. AES-GCM's authentication
tag also means a wrong key, wrong grid alignment, or corrupted capture
fails decryption cleanly (raises/returns None) instead of silently
producing a plausible-looking wrong username -- there is no
"partially correct" decode.
"""
from __future__ import annotations

import base64
import struct
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

USER_FIELD_LEN = 24
PAYLOAD_LEN = USER_FIELD_LEN + 4  # + 4-byte timestamp
NONCE_LEN = 12
TAG_LEN = 16
BLOB_LEN = NONCE_LEN + PAYLOAD_LEN + TAG_LEN  # 12 + 28 + 16 = 56 bytes = 448 bits

# 448 bits packs exactly into a 16x28 grid with no leftover/padding bits.
GRID_COLS = 16
GRID_ROWS = (BLOB_LEN * 8) // GRID_COLS
assert GRID_COLS * GRID_ROWS == BLOB_LEN * 8

DEFAULT_KEY_PATH = Path("/etc/watermark-overlay/watermark.key")


def load_key(explicit_path: "str | Path | None" = None) -> "bytes | None":
    """Reads a 32-byte AES-256 key from a file, accepting either raw
    32-byte binary or base64 text (e.g. `openssl rand -base64 32`
    output). Returns None if the file is missing/unreadable/malformed
    rather than raising -- callers treat a missing key as "dots mode
    can't render/decode right now", not a crash.
    """
    path = Path(explicit_path) if explicit_path else DEFAULT_KEY_PATH
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) == 32:
        return data
    try:
        decoded = base64.b64decode(data.strip(), validate=True)
    except Exception:
        return None
    return decoded if len(decoded) == 32 else None


def pack_identity(user: str, timestamp: int) -> bytes:
    user_bytes = user.encode("utf-8")[:USER_FIELD_LEN].ljust(USER_FIELD_LEN, b"\x00")
    return user_bytes + struct.pack(">I", timestamp & 0xFFFFFFFF)


def unpack_identity(payload: bytes) -> "tuple[str, int]":
    user_bytes, ts_bytes = payload[:USER_FIELD_LEN], payload[USER_FIELD_LEN:PAYLOAD_LEN]
    user = user_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
    (timestamp,) = struct.unpack(">I", ts_bytes)
    return user, timestamp


def encrypt_identity(user: str, timestamp: int, key: bytes) -> bytes:
    """Returns nonce || ciphertext || tag, always BLOB_LEN bytes."""
    import os as _os  # local import: only needed on the encode path

    aesgcm = AESGCM(key)
    nonce = _os.urandom(NONCE_LEN)
    ciphertext = aesgcm.encrypt(nonce, pack_identity(user, timestamp), None)
    blob = nonce + ciphertext
    assert len(blob) == BLOB_LEN
    return blob


def decrypt_identity(blob: bytes, key: bytes) -> "tuple[str, int] | None":
    if len(blob) != BLOB_LEN:
        return None
    nonce, ciphertext = blob[:NONCE_LEN], blob[NONCE_LEN:]
    aesgcm = AESGCM(key)
    try:
        payload = aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        return None
    return unpack_identity(payload)


def bytes_to_bits(data: bytes) -> "list[int]":
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)
    return bits


def bits_to_bytes(bits: "list[int]") -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | (bit & 1)
        out.append(byte)
    return bytes(out)


def grid_cell_positions(dot_size: int) -> "list[tuple[int, int]]":
    """(x, y) top-left pixel offset of each grid cell within one tile,
    in row-major bit order, for a given dot pixel size. Cell pitch
    (dot_size + 2px gap) is shared by the renderer and decoder so both
    agree on tile geometry without needing to pass it around separately.
    """
    cell = dot_size + 2
    return [
        (col * cell, row * cell)
        for row in range(GRID_ROWS)
        for col in range(GRID_COLS)
    ]


def tile_size(dot_size: int) -> "tuple[int, int]":
    cell = dot_size + 2
    return GRID_COLS * cell, GRID_ROWS * cell
