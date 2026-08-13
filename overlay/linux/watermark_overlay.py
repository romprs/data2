#!/usr/bin/env python3
"""Screen watermark overlay for Linux/X11 (Stage 0-1 prototype).

Draws a full-screen, click-through, semi-transparent tiled watermark
carrying the current OS user's name across every connected display.
Content and editing are untouched: the window forwards all mouse and
keyboard input to whatever is beneath it (Qt.WindowTransparentForInput)
and never takes focus.

Run:
    python3 watermark_overlay.py [--text TEMPLATE] [--opacity 0-255]
                                  [--font-size PX] [--angle DEG] [--spacing PX]
"""
import argparse
import getpass
import socket
import sys
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets

try:
    from Xlib import X, display as xlib_display

    _HAS_XLIB = True
except ImportError:
    _HAS_XLIB = False


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


class WatermarkOverlay(QtWidgets.QWidget):
    def __init__(self, args: argparse.Namespace, screen: QtGui.QScreen):
        super().__init__(
            flags=QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.X11BypassWindowManagerHint
            | QtCore.Qt.WindowTransparentForInput
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self._args = args
        self._screen = screen

        self.setWindowTitle("watermark-overlay")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

        self._sync_geometry()
        screen.geometryChanged.connect(self._sync_geometry)

    def _sync_geometry(self, *_ignored) -> None:
        self.setGeometry(self._screen.geometry())

    def watermark_text(self) -> str:
        return self._args.text.format(
            user=getpass.getuser(),
            host=socket.gethostname(),
            time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)

        font = QtGui.QFont("DejaVu Sans", self._args.font_size, QtGui.QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(110, 110, 110, self._args.opacity))

        text = self.watermark_text()
        metrics = QtGui.QFontMetrics(font)
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()
        step_x = text_w + self._args.spacing
        step_y = text_h + self._args.spacing

        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._args.angle)

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

    def __init__(self, app: QtWidgets.QApplication, args: argparse.Namespace):
        super().__init__()
        self._app = app
        self._args = args
        self._overlays: dict[QtGui.QScreen, WatermarkOverlay] = {}

        for screen in app.screens():
            self._add_screen(screen)
        app.screenAdded.connect(self._add_screen)
        app.screenRemoved.connect(self._remove_screen)

    def _add_screen(self, screen: QtGui.QScreen) -> None:
        overlay = WatermarkOverlay(self._args, screen)
        overlay.show()
        self._overlays[screen] = overlay

    def _remove_screen(self, screen: QtGui.QScreen) -> None:
        overlay = self._overlays.pop(screen, None)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text",
        default="{user}",
        help="Watermark text template; supports {user}, {host}, {time} (default: %(default)s)",
    )
    parser.add_argument(
        "--opacity",
        type=int,
        default=45,
        help="Alpha channel of watermark text, 0-255 (default: %(default)s)",
    )
    parser.add_argument("--font-size", type=int, default=18, help="Point size (default: %(default)s)")
    parser.add_argument("--angle", type=float, default=-30.0, help="Tile rotation in degrees (default: %(default)s)")
    parser.add_argument("--spacing", type=int, default=80, help="Gap between tiles in pixels (default: %(default)s)")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    manager = OverlayManager(app, args)  # noqa: F841 - keeps overlays alive
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
