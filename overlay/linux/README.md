# Screen watermark overlay — Linux/X11 prototype (Этап 0-1)

Полноэкранное click-through окно с тайловым водяным знаком (имя
пользователя), поверх всех приложений. Часть модуля "экранный оверлей"
из [`PROJECT_PLAN.md`](../../PROJECT_PLAN.md) (раздел 4.2).

## Зависимости

Пакеты apt (Ubuntu/Debian):

```bash
sudo apt-get install -y python3-pyqt5 python3-xlib
```

или через pip (см. `requirements.txt`):

```bash
pip install -r requirements.txt
```

> Если система ставит PyQt5 под другую версию Python3, чем та, что
> запускает скрипт (несовпадение `ABI`, ошибка `No module named
> 'PyQt5.sip'`), поставьте пакет через pip для нужного интерпретатора:
> `pip install --force-reinstall PyQt5`.

## Запуск

```bash
python3 watermark_overlay.py
```

Параметры (все опциональны):

| Флаг | По умолчанию | Описание |
|---|---|---|
| `--text` | `{user}` | Шаблон текста. Доступны `{user}`, `{host}`, `{time}` |
| `--opacity` | `45` | Alpha-канал текста, 0-255 |
| `--font-size` | `18` | Размер шрифта |
| `--angle` | `-30` | Угол наклона тайлов, градусы |
| `--spacing` | `80` | Отступ между тайлами, px |

Пример: `python3 watermark_overlay.py --text "{user} · {time}" --opacity 60`

## Как это работает

- Окно создаётся с флагами `FramelessWindowHint | WindowStaysOnTopHint |
  Tool | X11BypassWindowManagerHint | WindowTransparentForInput |
  WindowDoesNotAcceptFocus` — оно всегда поверх остальных окон, не
  показывается в панели задач и **не получает ввод** (клики и клавиатура
  проходят к нижележащему приложению).
- `WA_TranslucentBackground` + отрисовка текста с низким alpha — знак не
  перекрывает читаемость контента.
- На каждый подключённый монитор (`QApplication.screens()`) создаётся
  своё окно-оверлей; при подключении/отключении монитора список
  синхронизируется автоматически (`screenAdded`/`screenRemoved`).
- Дополнительно (если доступен `python-xlib`) на окно навешиваются EWMH
  хинты `_NET_WM_STATE_STICKY` + `_NET_WM_DESKTOP=-1`, чтобы оно
  оставалось поверх при переключении виртуальных рабочих столов —
  это то, что штатные флаги Qt не всегда покрывают на всех WM.

## Ограничения прототипа (см. раздел 8 плана)

- Требует композитор/WM с поддержкой EWMH для полной устойчивости к
  переключению рабочих столов; без `python-xlib` (или на WM без EWMH)
  окно всё равно рисуется и остаётся поверх на текущем столе, но не
  "прилипает" ко всем столам.
- **Wayland не поддерживается** — X11-специфичные флаги
  (`X11BypassWindowManagerHint`) не действуют на Wayland-композиторах;
  нужна отдельная реализация через `wlr-layer-shell` (см. раздел 4.2
  плана, риск №2 в разделе 8).
- Это только экранный/скриншотный/фото-слой. Печать этим модулем **не**
  покрывается — для неё отдельный модуль перехвата печати (раздел 4.3
  плана, Этап 2), т.к. печать не идёт через композитор рабочего стола.
- Anti-tamper здесь минимальный: пример systemd-юнита в `systemd/`
  перезапускает процесс при падении, но не защищает от `systemctl
  --user stop` обычным пользователем — полноценный anti-tamper
  (раздел 6 плана) требует системной службы с правами, недоступными
  текущему пользователю, что выходит за рамки Этапа 0-1.

## Автозапуск (systemd user service)

```bash
mkdir -p ~/.config/systemd/user
cp systemd/watermark-overlay.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now watermark-overlay.service
```

## Тесты

Headless smoke-тест (поднимает Xvfb + openbox + xterm, проверяет что
знак реально рисуется на экране, покрывает весь экран и прозрачен для
кликов) — см. [`../../tests/linux/`](../../tests/linux/).

```bash
sudo apt-get install -y xvfb openbox xterm xdotool imagemagick
pip install pillow
bash ../../tests/linux/run_smoke_test.sh
```
