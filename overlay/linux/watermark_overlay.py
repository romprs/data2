#!/usr/bin/env python3
"""Screen watermark overlay for Linux/X11 (Stage 0-1 prototype).

Draws a full-screen, click-through, semi-transparent tiled watermark
carrying the current OS user's name across every connected display.
Content and editing are untouched: the window forwards all mouse and
keyboard input to whatever is beneath it (Qt.WindowTransparentForInput)
and never takes focus.

Settings (text/opacity/font size/angle/spacing) come from, in order of
increasing priority:
  1. built-in defaults
  2. /etc/watermark-overlay/config.json      (system-wide default)
  3. ~/.config/watermark-overlay/config.json (per-user)
  4. --config PATH, if given (replaces 2+3 entirely)
  5. matching --text/--opacity/... CLI flags

The active config file is watched for changes: editing it (e.g. to
tweak --opacity) updates the on-screen watermark within ~1s, no restart
needed -- handy when running this under systemd.

Run:
    python3 watermark_overlay.py [--config PATH] [--text TEMPLATE]
                                  [--opacity 0-255] [--font-size PX]
                                  [--angle DEG] [--spacing PX]
"""
import argparse
import getpass
import json
import socket
import sys
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

try:
    from Xlib import display as xlib_display

    _HAS_XLIB = True
except ImportError:
    _HAS_XLIB = False

HARD_DEFAULTS = {
    "text": "{user}",
    "opacity": 45,
    "font_size": 18,
    "angle": -30.0,
    "spacing": 80,
}

SYSTEM_CONFIG_PATH = Path("/etc/watermark-overlay/config.json")
USER_CONFIG_PATH = Path.home() / ".config/watermark-overlay/config.json"


def _pin_to_all_desktops(win_id: int) -> None:
    """Best-effort EWMH hints beyond what Qt exposes: keep the overlay
    sticky on every virtual desktop and out of taskbars/pagers. Silently
    no-ops without python-xlib or on a non-EWMH window manager.
    """
    if not _HAS_XLIB:
        return
    try:
        d = xlib_display.Display()
        window = d.create_resource_object("window", win_id)

        net_wm_state = d.intern_atom("_NET_WM_STATE")
        atom_type = d.intern_atom("ATOM")
        cardinal_type = d.intern_atom("CARDINAL")

        states = [
            d.intern_atom("_NET_WM_STATE_STICKY"),
            d.intern_atom("_NET_WM_STATE_SKIP_TASKBAR"),
            d.intern_atom("_NET_WM_STATE_SKIP_PAGER"),
            d.intern_atom("_NET_WM_STATE_ABOVE"),
        ]
        window.change_property(net_wm_state, atom_type, 32, states)
        window.change_property(
            d.intern_atom("_NET_WM_DESKTOP"), cardinal_type, 32, [0xFFFFFFFF]
        )
        d.flush()
    except Exception:
        # Non-fatal: overlay still works, just may not survive a
        # virtual-desktop switch on some window managers.
        pass


class LiveSettings(QtCore.QObject):
    """Holds the effective settings dict and reloads it whenever the
    backing config file(s) change on disk, so opacity/text/etc. can be
    tuned live without restarting the overlay process."""

    changed = QtCore.pyqtSignal()

    def __init__(self, explicit_config: str | None, cli_overrides: dict):
        super().__init__()
        self._explicit_config = Path(explicit_config) if explicit_config else None
        self._cli_overrides = {k: v for k, v in cli_overrides.items() if v is not None}
        self.values = dict(HARD_DEFAULTS)

        self._watcher = QtCore.QFileSystemWatcher()
        for path in self._candidate_paths():
            if path.is_file():
                self._watcher.addPath(str(path))
        self._watcher.fileChanged.connect(self._on_file_changed)

        self.reload()

    def _candidate_paths(self) -> list[Path]:
        if self._explicit_config:
            return [self._explicit_config]
        return [SYSTEM_CONFIG_PATH, USER_CONFIG_PATH]

    def reload(self) -> None:
        file_values: dict = {}
        for path in self._candidate_paths():
            if not path.is_file():
                continue
            try:
                file_values.update(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"watermark-overlay: skipping bad config {path}: {exc}", file=sys.stderr)

        values = dict(HARD_DEFAULTS)
        values.update({k: v for k, v in file_values.items() if k in HARD_DEFAULTS})
        values.update(self._cli_overrides)
        self.values = values

    def _on_file_changed(self, path: str) -> None:
        self.reload()
        # Editors commonly save via unlink+rename, which drops the old
        # inode from the watch list -- re-arm if the path still exists.
        if Path(path).is_file() and path not in self._watcher.files():
            self._watcher.addPath(path)
        self.changed.emit()


class WatermarkOverlay(QtWidgets.QWidget):
    def __init__(self, settings: LiveSettings, screen: QtGui.QScreen):
        super().__init__(
            flags=QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.X11BypassWindowManagerHint
            | QtCore.Qt.WindowTransparentForInput
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self._settings = settings
        self._screen = screen

        self.setWindowTitle("watermark-overlay")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

        self._sync_geometry()
        screen.geometryChanged.connect(self._sync_geometry)
        settings.changed.connect(self.update)

    def _sync_geometry(self, *_ignored) -> None:
        self.setGeometry(self._screen.geometry())

    def watermark_text(self) -> str:
        return self._settings.values["text"].format(
            user=getpass.getuser(),
            host=socket.gethostname(),
            time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        v = self._settings.values
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)

        font = QtGui.QFont("DejaVu Sans", v["font_size"], QtGui.QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(110, 110, 110, v["opacity"]))

        text = self.watermark_text()
        metrics = QtGui.QFontMetrics(font)
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()
        step_x = text_w + v["spacing"]
        step_y = text_h + v["spacing"]

        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(v["angle"])

        # Rotation can expose corners outside the original rect, so tile
        # across the bounding diagonal rather than just width/height.
        diag = int((self.width() ** 2 + self.height() ** 2) ** 0.5)
        y = -diag
        while y < diag:
            x = -diag
            while x < diag:
                painter.drawText(x, y, text)
                x += step_x
            y += step_y
        painter.end()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        _pin_to_all_desktops(int(self.winId()))


class OverlayManager(QtCore.QObject):
    """Keeps one overlay window per connected screen, adding/removing
    them as monitors are plugged in or out."""

    def __init__(self, app: QtWidgets.QApplication, settings: LiveSettings):
        super().__init__()
        self._app = app
        self._settings = settings
        self._overlays: dict[QtGui.QScreen, WatermarkOverlay] = {}

        for screen in app.screens():
            self._add_screen(screen)
        app.screenAdded.connect(self._add_screen)
        app.screenRemoved.connect(self._remove_screen)

    def _add_screen(self, screen: QtGui.QScreen) -> None:
        overlay = WatermarkOverlay(self._settings, screen)
        overlay.show()
        self._overlays[screen] = overlay

    def _remove_screen(self, screen: QtGui.QScreen) -> None:
        overlay = self._overlays.pop(screen, None)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"Config file path (default: {SYSTEM_CONFIG_PATH} then {USER_CONFIG_PATH}, user wins)",
    )
    parser.add_argument(
        "--text", default=None, help="Watermark text template; supports {user}, {host}, {time}"
    )
    parser.add_argument("--opacity", type=int, default=None, help="Alpha channel of watermark text, 0-255")
    parser.add_argument("--font-size", type=int, default=None, help="Point size")
    parser.add_argument("--angle", type=float, default=None, help="Tile rotation in degrees")
    parser.add_argument("--spacing", type=int, default=None, help="Gap between tiles in pixels")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    settings = LiveSettings(
        explicit_config=args.config,
        cli_overrides={
            "text": args.text,
            "opacity": args.opacity,
            "font_size": args.font_size,
            "angle": args.angle,
            "spacing": args.spacing,
        },
    )
    manager = OverlayManager(app, settings)  # noqa: F841 - keeps overlays alive
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
