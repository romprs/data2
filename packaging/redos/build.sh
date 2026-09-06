#!/usr/bin/env bash
# Builds the watermark-overlay RPM for RED OS / RHEL-family systems.
# Run on any machine with rpmbuild installed (does not need to be RED OS
# itself -- the package is noarch and only contains a Python script,
# a wrapper, a desktop entry and a config file).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

NAME="watermark-overlay"
VERSION="0.8.0"

TOPDIR="$SCRIPT_DIR/rpmbuild"
rm -rf "$TOPDIR"
mkdir -p "$TOPDIR"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

# Assemble the source tarball rpmbuild's %setup expects: a single
# top-level directory named <name>-<version>/ with the payload files.
STAGE="$(mktemp -d)"
PKGDIR="$STAGE/$NAME-$VERSION"
mkdir -p "$PKGDIR"
cp "$REPO_ROOT/overlay/linux/watermark_overlay.py" "$PKGDIR/watermark_overlay.py"
cp "$REPO_ROOT/overlay/linux/dotcode.py" "$PKGDIR/dotcode.py"
cp "$REPO_ROOT/overlay/linux/decode_dots.py" "$PKGDIR/decode_dots.py"
cp "$REPO_ROOT/overlay/linux/decode_dots_gui.py" "$PKGDIR/decode_dots_gui.py"
cp "$SCRIPT_DIR/watermark-overlay.wrapper" "$PKGDIR/watermark-overlay.wrapper"
cp "$SCRIPT_DIR/watermark-decode.wrapper" "$PKGDIR/watermark-decode.wrapper"
cp "$SCRIPT_DIR/watermark-decode-gui.wrapper" "$PKGDIR/watermark-decode-gui.wrapper"
cp "$SCRIPT_DIR/watermark-overlay.desktop" "$PKGDIR/watermark-overlay.desktop"
cp "$SCRIPT_DIR/watermark-decode-gui.desktop" "$PKGDIR/watermark-decode-gui.desktop"
cp "$REPO_ROOT/overlay/linux/config.example.json" "$PKGDIR/config.json"

tar -C "$STAGE" -czf "$TOPDIR/SOURCES/$NAME-$VERSION.tar.gz" "$NAME-$VERSION"
rm -rf "$STAGE"

cp "$SCRIPT_DIR/watermark-overlay.spec" "$TOPDIR/SPECS/"

rpmbuild --define "_topdir $TOPDIR" -ba "$TOPDIR/SPECS/watermark-overlay.spec"

RPM_PATH="$(find "$TOPDIR/RPMS" -name '*.rpm' | head -n1)"
mkdir -p "$SCRIPT_DIR/dist"
cp "$RPM_PATH" "$SCRIPT_DIR/dist/"
echo
echo "Built: $SCRIPT_DIR/dist/$(basename "$RPM_PATH")"
