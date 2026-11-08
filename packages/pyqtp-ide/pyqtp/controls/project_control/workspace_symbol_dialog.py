from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import (
    QEvent,
    QObject,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.lsp_control.workspace_symbol import (
    SYMBOL_KIND_NAMES,
    WorkspaceSymbol,
    open_payload,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent

    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.controls.lsp_control.lsp_hub import LspHub

# Сколько результатов показываем (сервер уже возвращает ранжированный список).
_MAX_RESULTS = 200
# Задержка перед запросом после последнего нажатия (похоже на 300 мс didChange).
_QUERY_DELAY_MS = 400
# Контейнер («pkg.MyClass») дописываем к короткому имени в основной подписи.
_CONTAINER_INLINE_LIMIT = 40


class _SymbolSignals(QObject):
    """Мост «поток event-loop хаба -> поток GUI» для результатов запроса."""

    result = Signal(int, list, object)  # поколение + список + ошибка (или None)


class WorkspaceSymbolDialog(QDialog):
    """Диалог «Переход к символу проекта» (Ctrl+T, остаток этапа 8).

    Немодальный: поле ввода + список символов всего проекта — ответы
    ``workspace/symbol`` берутся из ``LspHub.workspace_symbols_async``
    (дебаунс + гейт поколения, запоздалые ответы отбрасываются).
    Enter/двойной клик выбирает символ — сигнал ``symbol_selected``
    (payload для ``DocumentManager.open_definition_uri``).
    """

    symbol_selected = Signal(dict)

    def __init__(
        self, main_wnd: PluggableMainWindow, hub: LspHub, /
    ) -> None:
        super().__init__(main_wnd)
        self.setWindowTitle('Переход к символу проекта')
        self.setModal(False)

        self._hub = hub
        self._root: Path | None = None
        self._req_gen = 0

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText(
            'Имя символа во всём проекте (LSP) — Esc закрыть'
        )
        self._edit.setClearButtonEnabled(True)

        self._list = QListWidget(self)
        self._list.setUniformItemSizes(True)

        status = QWidget(self)
        status_label = QLabel('', status)
        status_layout = QHBoxLayout(status)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.addWidget(status_label)
        self._status_label = status_label

        layout_root = QVBoxLayout(self)
        layout_root.setContentsMargins(6, 6, 6, 6)
        layout_root.addWidget(self._edit)
        layout_root.addWidget(self._list)
        layout_root.addWidget(status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_QUERY_DELAY_MS)
        self._timer.timeout.connect(self._run_query)

        self._signals = _SymbolSignals()
        self._signals.result.connect(self._on_result)

        self._edit.textChanged.connect(self._on_text_changed)
        # Up/Down/Enter из поля ввода управляем списком (фильтруем клавиатуру)
        self._edit.installEventFilter(self)
        self._list.itemActivated.connect(self._accept_item)

    # --- проект и запрос ---

    def set_project(self, root: Path | None) -> None:
        """Смена корня проекта: индекс сбрасывается, чужие ответы не нужны."""
        self._root = root
        self.drop_pending()
        self._list.clear()
        if root is None:
            self._status_label.setText('Проект не открыт')
        else:
            self._status_label.setText('Начните ввод имени символа')

    def drop_pending(self) -> None:
        """Сбросить незавершённый запрос (таймер + гейт поколения)."""
        self._timer.stop()
        self._req_gen += 1

    def _on_text_changed(self, text: str) -> None:
        self._timer.stop()
        self._list.clear()
        if self._root is None:
            self._status_label.setText('Проект не открыт')
            return
        if not text.strip():
            self._status_label.setText('Начните ввод имени символа')
            return
        self._status_label.setText('Поиск…')
        self._timer.start()

    def _run_query(self) -> None:
        """Отправить запрос в хаб; ответ вернётся queued-сигналом в GUI."""
        query = self._edit.text().strip()
        if self._root is None or not query:
            return
        self._req_gen += 1
        gen = self._req_gen
        try:
            self._hub.workspace_symbols_async(
                self._root, query,
                cb=lambda symbols, error: self._signals.result.emit(
                    gen, symbols, error
                ),
            )
        except RuntimeError as exc:  # хаб не поднялся/остановлен
            self._status_label.setText(f'Ошибка: {exc}')

    def _on_result(self, gen: int, symbols: list, error: object) -> None:
        if gen != self._req_gen:
            return  # запрос уже устарел (новая строка ввода/смена проекта)
        self._list.clear()
        if not symbols:
            self._status_label.setText(
                f'Ошибка: {error}' if error else 'Совпадений нет'
            )
            return
        for symbol in symbols[:_MAX_RESULTS]:
            item = QListWidgetItem(self._display_text(symbol), self._list)
            item.setToolTip(self._display_text(symbol, full=True))
            item.setData(Qt.ItemDataRole.UserRole, open_payload(symbol))
        if self._list.count():
            self._list.setCurrentRow(0)
        suffix = f'показано {min(len(symbols), _MAX_RESULTS)}'
        if error:
            suffix += f' (ошибка части серверов: {error})'
        self._status_label.setText(f'Совпадений: {len(symbols)}; {suffix}')

    def _display_text(self, symbol: WorkspaceSymbol, *, full: bool = False) -> str:
        kind = SYMBOL_KIND_NAMES.get(symbol.kind, 'символ')
        where = QUrl(symbol.file_uri).toLocalFile()
        if self._root is not None:
            with contextlib.suppress(ValueError):
                where = str(
                    Path(where).relative_to(self._root)
                ).replace('\\', '/')  # вне корня — остаётся абсолютный путь
        name = symbol.name
        if symbol.container and (full or len(name) <= _CONTAINER_INLINE_LIMIT):
            name = f'{name} [{symbol.container}]'
        return f'{name} — {kind} — {where}:{symbol.line}'

    # --- выбор символа ---

    def accept_current(self) -> None:
        """Выбрать текущую строку списка (то, что делает Enter)."""
        self._accept_item(self._list.currentItem())

    def _accept_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        payload = item.data(Qt.ItemDataRole.UserRole)
        self.drop_pending()
        self.hide()
        self.symbol_selected.emit(dict(cast('dict', payload)))

    # --- клавиатура ---

    def eventFilter(  # noqa: N802
        self, arg__1: QObject, arg__2: QEvent
    ) -> bool:
        """Up/Down/Enter из поля ввода перенаправляем в управление списка."""
        if arg__1 is self._edit and arg__2.type() == QEvent.Type.KeyPress:
            key = cast('QKeyEvent', arg__2).key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if not self._list.count():
                    return True
                step = 1 if key == Qt.Key.Key_Down else -1
                new_row = min(
                    max(self._list.currentRow() + step, 0),
                    self._list.count() - 1,
                )
                self._list.setCurrentRow(new_row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.accept_current()
                return True
        return super().eventFilter(arg__1, arg__2)

    def keyPressEvent(self, arg__1: QKeyEvent) -> None:
        """Enter при фокусе на списке (не в поле ввода) выбирает текущую строку."""
        if arg__1.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept_current()
            return
        super().keyPressEvent(arg__1)

    # --- показ ---

    def show_for_use(self) -> None:
        """Поверх всех окон, со сброшенным полем и фокусом на вводе."""
        self._edit.setText('')
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.show()
        self.raise_()
        self.activateWindow()
