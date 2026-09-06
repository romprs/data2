#!/usr/bin/env python3
"""Drag-and-drop / button GUI front end for decode_dots.py (Linux/X11).

Not a new decoding algorithm -- this is a thin PyQt5 wrapper around
decode_dots.try_decode() (see that module for how the actual
brute-force alignment + AES-GCM decrypt works). Built so investigating
a leak doesn't require a terminal: drag a screenshot/photo onto the
window, or click "Открыть..." and pick a file, and read the result off
the window. Uses PyQt5 rather than e.g. Tkinter to match the rest of
this project's stack -- no extra runtime dependency beyond what
watermark-overlay/watermark-decode already require.

First run: asks once for the AES-256 key file (the same
/etc/watermark-overlay/watermark.key the overlay itself uses, and
already the default path this dialog opens to) and remembers the path
in ~/.config/watermark-overlay/decode-gui.json, so every run after
that is just "open or drop an image". The key itself is never bundled
with this tool -- see the "Честно про хранение ключа" note in this
directory's README.md for why that boundary matters.

Run:
    watermark-decode-gui                 # opens with an "Открыть" button
    watermark-decode-gui SCREENSHOT.png  # decodes immediately
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

import decode_dots
import dotcode

GUI_CONFIG_PATH = Path.home() / ".config/watermark-overlay/decode-gui.json"


def _load_saved_key_path() -> "str | None":
    try:
        data = json.loads(GUI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = data.get("key_file")
    return path if path and Path(path).is_file() else None


def _save_key_path(path: str) -> None:
    GUI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUI_CONFIG_PATH.write_text(json.dumps({"key_file": path}), encoding="utf-8")


class DecodeWorker(QtCore.QThread):
    finished_ok = QtCore.pyqtSignal(str, int, int, int)  # user, timestamp, x, y
    finished_none = QtCore.pyqtSignal()
    failed = QtCore.pyqtSignal(str)

    def __init__(self, image_path: str, key: bytes, dot_size: int):
        super().__init__()
        self._image_path = image_path
        self._key = key
        self._dot_size = dot_size

    def run(self) -> None:
        try:
            result = decode_dots.try_decode(self._image_path, self._key, self._dot_size)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
            self.failed.emit(str(exc))
            return
        if result is None:
            self.finished_none.emit()
            return
        user, ts, (x, y) = result
        self.finished_ok.emit(user, ts, x, y)


class MainWindow(QtWidgets.QWidget):
    def __init__(self, initial_file: "str | None" = None):
        super().__init__()
        self.setWindowTitle("Расшифровка водяного знака")
        self.resize(640, 440)
        self.setAcceptDrops(True)

        self.key_path = _load_saved_key_path()
        self._worker: "DecodeWorker | None" = None

        layout = QtWidgets.QVBoxLayout(self)

        buttons = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton("Открыть скриншот / фото...")
        open_btn.clicked.connect(self.pick_image)
        key_btn = QtWidgets.QPushButton("Указать файл ключа...")
        key_btn.clicked.connect(self.pick_key)
        buttons.addWidget(open_btn)
        buttons.addWidget(key_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        dot_row = QtWidgets.QHBoxLayout()
        dot_row.addWidget(QtWidgets.QLabel("Размер точки (dot_size из конфига, обычно 3):"))
        self.dot_size_edit = QtWidgets.QLineEdit("3")
        self.dot_size_edit.setFixedWidth(50)
        self.dot_size_edit.setValidator(QtGui.QIntValidator(1, 99))
        dot_row.addWidget(self.dot_size_edit)
        dot_row.addStretch(1)
        layout.addLayout(dot_row)

        self.key_label = QtWidgets.QLabel()
        self.key_label.setWordWrap(True)
        self.key_label.setStyleSheet("color: #666;")
        layout.addWidget(self.key_label)
        self._refresh_key_label()

        self.result = QtWidgets.QTextEdit()
        self.result.setReadOnly(True)
        self.result.setStyleSheet("background-color: #f7f7f7;")
        layout.addWidget(self.result, stretch=1)

        hint = QtWidgets.QLabel("Подсказка: файл можно перетащить прямо в это окно.")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        self._set_result(
            "Готово к работе.\n\n"
            "1. Если ключ ещё не указан -- нажмите «Указать файл ключа...» (один раз).\n"
            "2. Откройте скриншот/фото кнопкой «Открыть» или перетащите файл в это окно."
        )

        if initial_file:
            QtCore.QTimer.singleShot(200, lambda: self.decode(initial_file))

    def _refresh_key_label(self) -> None:
        text = f"Ключ: {self.key_path}" if self.key_path else "Ключ ещё не выбран."
        self.key_label.setText(text)

    # -- drag and drop -----------------------------------------------
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.decode(urls[0].toLocalFile())

    # -- actions -------------------------------------------------------
    def pick_key(self) -> None:
        start_dir = str(dotcode.DEFAULT_KEY_PATH.parent) if dotcode.DEFAULT_KEY_PATH.parent.is_dir() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите файл ключа (watermark.key)", start_dir
        )
        if path:
            self.key_path = path
            _save_key_path(path)
            self._refresh_key_label()

    def pick_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Выберите скриншот или фото",
            "",
            "Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;Все файлы (*)",
        )
        if path:
            self.decode(path)

    def decode(self, image_path: str) -> None:
        if not Path(image_path).is_file():
            QtWidgets.QMessageBox.critical(self, "Файл не найден", f"Не найден файл:\n{image_path}")
            return
        if not self.key_path:
            QtWidgets.QMessageBox.warning(self, "Нет ключа", "Сначала укажите файл ключа (watermark.key).")
            self.pick_key()
            if not self.key_path:
                return

        key = dotcode.load_key(self.key_path)
        if key is None:
            self._set_result(
                "Ошибка: файл ключа повреждён или имеет неверный размер "
                "(нужно ровно 32 байта, raw или base64)."
            )
            return

        try:
            dot_size = int(self.dot_size_edit.text())
        except ValueError:
            QtWidgets.QMessageBox.critical(self, "Неверный размер точки", "Размер точки должен быть целым числом.")
            return

        self._set_result(
            f"Расшифровываю: {Path(image_path).name}\n\n"
            "Это может занять до минуты на больших изображениях -- полный "
            "перебор положения решётки по всему кадру, а не мгновенная операция."
        )

        self._worker = DecodeWorker(image_path, key, dot_size)
        self._worker.finished_ok.connect(lambda u, ts, x, y: self._on_decoded(u, ts, x, y, image_path))
        self._worker.finished_none.connect(self._on_not_found)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_decoded(self, user: str, ts: int, x: int, y: int, image_path: str) -> None:
        when = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        self._set_result(
            "Расшифровано:\n\n"
            f"Пользователь:  {user}\n"
            f"Время:         {when.isoformat()}\n"
            f"Найдено в:     ({x}, {y})\n\n"
            f"Файл: {image_path}"
        )

    def _on_not_found(self) -> None:
        self._set_result(
            "Код НЕ найден.\n\n"
            "Возможные причины: неверный ключ, неверный размер точки, в "
            "изображении нет полного тайла водяного знака, или это фото с "
            "камеры, а не точный скриншот -- расшифровка реальных фото (в "
            "отличие от скриншотов) пока не проверена на практике, см. "
            "PROJECT_PLAN.md."
        )

    def _on_failed(self, message: str) -> None:
        self._set_result(f"Ошибка при обработке файла:\n{message}")

    def _set_result(self, text: str) -> None:
        self.result.setPlainText(text)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    window = MainWindow(initial_file=initial)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
