#!/usr/bin/env python3
"""Screen watermark overlay for Linux/X11 (Stage 0-1 prototype).

Draws a full-screen, click-through, semi-transparent tiled watermark
carrying the current OS user's name across every connected display.
Content and editing are untouched: the window forwards all mouse and
keyboard input to whatever is beneath it (Qt.WindowTransparentForInput)
and never takes focus.

Settings (text/opacity/font size/angle/spacing/mode/apps/watermark_type)
come from, in order of increasing priority:
  1. built-in defaults
  2. /etc/watermark-overlay/config.json      (system-wide default)
  3. ~/.config/watermark-overlay/config.json (per-user)
  4. --config PATH, if given (replaces 2+3 entirely)
  5. matching --text/--opacity/... CLI flags

The active config file is watched for changes: editing it (e.g. to
tweak --opacity) updates the on-screen watermark within ~1s, no restart
needed -- handy when running this under systemd.

Two settings control *when* and *what* is shown:
  - "mode": "always" (default) shows the watermark all the time,
    "app_list" shows it only while one of the "apps" substrings matches
    the WM_CLASS of any currently open window (checked ~2x/sec via
    _NET_CLIENT_LIST; requires python-xlib and an EWMH window manager),
    or "none" disables it entirely -- if set at launch the process exits
    immediately without creating any window; if set while already
    running (e.g. an admin editing the shared /etc config), the running
    process quits within ~1s. This is the kill switch: no watermark
    process ends up running at all, rather than just an invisible one.
  - "watermark_type": "text" (default) renders the "text" template, or
    "account" ignores "text" and always renders the OS account identity
    (full name from /etc/passwd GECOS if set, else "user@host").

Run:
    python3 watermark_overlay.py [--config PATH] [--text TEMPLATE]
                                  [--opacity 0-255] [--font-size PX]
                                  [--angle DEG] [--spacing PX]
                                  [--mode always|app_list] [--apps A,B,C]
                                  [--watermark-type text|account]
"""
import argparse
import getpass
import json
import pwd
import socket
import sys
from datetime import datetime
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

try:
    from Xlib import X, display as xlib_display

    _HAS_XLIB = True
except ImportError:
    _HAS_XLIB = False

HARD_DEFAULTS = {
    "text": "{user}",
    "opacity": 45,
    "font_size": 18,
    "angle": -30.0,
    "spacing": 80,
    "watermark_type": "text",  # "text" | "account"
    "mode": "always",  # "always" | "app_list" | "none"
    "apps": [],  # WM_CLASS substrings used when mode == "app_list"
}


def account_label() -> str:
    """Fixed 'учётная запись' watermark: full name (GECOS) if available,
    otherwise just user@host -- unlike "text" this ignores the template
    and can't be customized beyond opacity/font/etc., so it can't
    accidentally be reconfigured to omit the identity."""
    user = getpass.getuser()
    host = socket.gethostname()
    try:
        full_name = pwd.getpwnam(user).pw_gecos.split(",")[0].strip()
    except (KeyError, IndexError):
        full_name = ""
    return f"{full_name} ({user}@{host})" if full_name else f"{user}@{host}"


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


def _candidate_config_paths(explicit_config: str | None) -> list[Path]:
    if explicit_config:
        return [Path(explicit_config)]
    return [SYSTEM_CONFIG_PATH, USER_CONFIG_PATH]


def resolve_settings(explicit_config: str | None, cli_overrides: dict) -> dict:
    """Merges built-in defaults, config file(s) and CLI overrides into
    the effective settings dict. Deliberately plain (no Qt/PyQt5 use)
    so it can be called before a QApplication exists -- main() needs
    that to decide whether to start Qt/X11 at all when mode == "none".
    """
    file_values: dict = {}
    for path in _candidate_config_paths(explicit_config):
        if not path.is_file():
            continue
        try:
            file_values.update(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"watermark-overlay: skipping bad config {path}: {exc}", file=sys.stderr)

    values = dict(HARD_DEFAULTS)
    values.update({k: v for k, v in file_values.items() if k in HARD_DEFAULTS})
    values.update({k: v for k, v in cli_overrides.items() if v is not None})
    return values


class LiveSettings(QtCore.QObject):
    """Holds the effective settings dict and reloads it whenever the
    backing config file(s) change on disk, so opacity/text/etc. can be
    tuned live without restarting the overlay process."""

    changed = QtCore.pyqtSignal()

    def __init__(self, explicit_config: str | None, cli_overrides: dict):
        super().__init__()
        self._explicit_config = explicit_config
        self._cli_overrides = cli_overrides
        self.values = dict(HARD_DEFAULTS)

        self._watcher = QtCore.QFileSystemWatcher()
        self._watcher.fileChanged.connect(self._on_change)
        self._watcher.directoryChanged.connect(self._on_change)
        self._arm_watches()

        self.reload()

    def _arm_watches(self) -> None:
        # QFileSystemWatcher can only watch paths that already exist, so
        # a config file created *after* this process started would
        # never be noticed by fileChanged alone. Watching each config
        # file's parent directory too catches that: creating/renaming a
        # file into an already-watched directory fires
        # directoryChanged, which re-runs this and picks up the new
        # file for direct watching from then on. (If neither the file
        # nor its parent directory exists yet at all, there is still
        # nothing to watch -- a known gap for that edge case.)
        watched_files = set(self._watcher.files())
        watched_dirs = set(self._watcher.directories())
        for path in _candidate_config_paths(self._explicit_config):
            if path.is_file() and str(path) not in watched_files:
                self._watcher.addPath(str(path))
            parent = path.parent
            if parent.is_dir() and str(parent) not in watched_dirs:
                self._watcher.addPath(str(parent))

    def reload(self) -> None:
        self.values = resolve_settings(self._explicit_config, self._cli_overrides)

    def _on_change(self, _path: str) -> None:
        self.reload()
        self._arm_watches()
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
        v = self._settings.values
        if v.get("watermark_type") == "account":
            return account_label()
        return v["text"].format(
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
        self._visible = True

        for screen in app.screens():
            self._add_screen(screen)
        app.screenAdded.connect(self._add_screen)
        app.screenRemoved.connect(self._remove_screen)

    def _add_screen(self, screen: QtGui.QScreen) -> None:
        overlay = WatermarkOverlay(self._settings, screen)
        overlay.setVisible(self._visible)
        self._overlays[screen] = overlay

    def _remove_screen(self, screen: QtGui.QScreen) -> None:
        overlay = self._overlays.pop(screen, None)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        for overlay in self._overlays.values():
            overlay.setVisible(visible)


class AppListWatcher(QtCore.QObject):
    """When settings["mode"] == "app_list", periodically checks (via
    _NET_CLIENT_LIST) whether any currently open window's WM_CLASS
    matches one of settings["apps"], and emits `matched` whenever that
    verdict flips. In "always" mode it just emits matched(True) once.

    This is deliberately poll-based rather than event-driven: EWMH
    property-change events would need a dedicated Xlib event loop
    thread interleaved with Qt's, which is more moving parts than a
    prototype warrants. ~2 checks/sec is imperceptible overhead and
    plenty responsive for "did the user just open a sensitive app".
    """

    matched = QtCore.pyqtSignal(bool)

    def __init__(self, settings: LiveSettings, poll_ms: int = 500):
        super().__init__()
        self._settings = settings
        self._display = None
        if _HAS_XLIB:
            try:
                self._display = xlib_display.Display()
            except Exception:
                self._display = None
        self._last_state: bool | None = None

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(poll_ms)
        # Deferred rather than called directly: `matched` has no
        # listener yet at construction time (main() wires it up right
        # after this returns), so an immediate emit here would be lost
        # and the overlay would keep its initial visible-by-default
        # state until the next poll tick.
        QtCore.QTimer.singleShot(0, self.check)

    def _open_window_identities(self) -> list[str]:
        if not self._display:
            return []
        try:
            root = self._display.screen().root
            client_list_atom = self._display.intern_atom("_NET_CLIENT_LIST")
            prop = root.get_full_property(client_list_atom, X.AnyPropertyType)
            if not prop or not prop.value:
                return []
            identities = []
            for win_id in prop.value:
                try:
                    window = self._display.create_resource_object("window", win_id)
                    wm_class = window.get_wm_class()
                    if wm_class:
                        identities.append(" ".join(wm_class).lower())
                except Exception:
                    continue
            return identities
        except Exception:
            return []

    def check(self) -> None:
        v = self._settings.values
        mode = v.get("mode", "always")
        if mode == "none":
            # Belt-and-braces: main() also quits the whole process when
            # mode flips to "none" live, but hiding immediately here
            # avoids a visible flash in the moment before that happens.
            visible = False
        elif mode != "app_list":
            visible = True
        elif not self._display:
            # No python-xlib / no X connection: can't evaluate the app
            # list, so fail open (show the watermark) rather than risk
            # silently hiding it forever.
            visible = True
        else:
            apps = [a.lower() for a in v.get("apps", []) if a]
            identities = self._open_window_identities()
            visible = bool(apps) and any(
                app in identity for identity in identities for app in apps
            )
        if visible != self._last_state:
            self._last_state = visible
            self.matched.emit(visible)


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
    parser.add_argument(
        "--mode",
        choices=["always", "app_list", "none"],
        default=None,
        help="Show watermark always, only while a listed app is open, or 'none' to disable/not start at all",
    )
    parser.add_argument(
        "--apps",
        default=None,
        help="Comma-separated WM_CLASS substrings to match in app_list mode, e.g. 'soffice,acroread'",
    )
    parser.add_argument(
        "--watermark-type",
        choices=["text", "account"],
        default=None,
        dest="watermark_type",
        help="'text' renders --text, 'account' always renders the OS account identity",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    cli_overrides = {
        "text": args.text,
        "opacity": args.opacity,
        "font_size": args.font_size,
        "angle": args.angle,
        "spacing": args.spacing,
        "mode": args.mode,
        "apps": [a.strip() for a in args.apps.split(",") if a.strip()] if args.apps else None,
        "watermark_type": args.watermark_type,
    }

    if resolve_settings(args.config, cli_overrides).get("mode") == "none":
        # Config already says "off" at launch: exit before touching Qt
        # or X11 at all -- this is the literal "daemon doesn't start"
        # case, not just an invisible/idle process.
        print("watermark-overlay: mode=none in config, not starting.", file=sys.stderr)
        return 0

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    settings = LiveSettings(explicit_config=args.config, cli_overrides=cli_overrides)

    manager = OverlayManager(app, settings)
    watcher = AppListWatcher(settings)  # noqa: F841 - keeps overlays/watcher alive
    watcher.matched.connect(manager.set_visible)
    settings.changed.connect(watcher.check)

    def _quit_if_disabled() -> None:
        # Config flipped to "off" while already running (e.g. an admin
        # editing the shared /etc config as a kill switch): actually
        # stop the process, not just hide the window, so re-enabling
        # later means flipping the config back rather than relying on
        # a process that's still silently running.
        if settings.values.get("mode") == "none":
            print("watermark-overlay: mode changed to none, exiting.", file=sys.stderr)
            app.quit()

    settings.changed.connect(_quit_if_disabled)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
