from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QEvent, QObject, Qt, Signal
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

from pyqtp.controls.project_control.quick_open import fuzzy_score

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtGui import QKeyEvent

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# Сколько результатов показываем (верх списка по скорингу).
_MAX_RESULTS = 100


class QuickOpenDialog(QDialog):
    """Диалог «Быстрый переход по файлам» (Ctrl+P, этап 9.2).

    Немодальный: поле ввода + список файлов проекта, отфильтрованных
    ``fuzzy_score`` по относительным путям. Enter/двойной клик выбирает файл —
    сигнал ``file_selected`` (абсолютный путь-строка, тот же ключ, что у
    ``DocumentManager.open_document``).
    """

    file_selected = Signal(str)

    def __init__(self, main_wnd: PluggableMainWindow, /) -> None:
        super().__init__(main_wnd)
        self.setWindowTitle('Быстрый переход по файлам')
        self.setModal(False)

        self._root: Path | None = None
        self._files: list[str] = []

        self._edit = QLineEdit(self)
        self._edit.setPlaceholderText('Имя файла — ввод для поиска, Esc закрыть')
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

        self._edit.textChanged.connect(self._refilter)
        # Up/Down/Enter из поля ввода управляем списком (фильтруем клавиатуру)
        self._edit.installEventFilter(self)
        self._list.itemActivated.connect(self._accept_item)

    # --- кэш файлов ---

    def set_index(self, root: Path | None, files: list[str]) -> None:
        """Загрузить кэш относительных путей файлов проекта."""
        self._root = root
        self._files = files
        self._refilter(self._edit.text())

    # --- отбор и показ ---

    def _refilter(self, text: str) -> None:
        query = text.strip()
        self._list.clear()
        if self._root is None:
            self._status_label.setText('Проект не открыт')
            return
        if not query:
            self._status_label.setText(
                f'Файлов в проекте: {len(self._files)} — начните ввод'
            )
            return
        scored: list[tuple[int, int, str]] = []
        for order, rel in enumerate(self._files):
            score = fuzzy_score(query, rel)
            if score is None:
                continue
            scored.append((score, order, rel))
        # при равном скоре — порядок обхода (он отсортирован, короткие пути
        # раньше длинных в пределах общего префикса)
        scored.sort(key=lambda item: (-item[0], item[1]))
        for _, _, rel in scored[:_MAX_RESULTS]:
            item = QListWidgetItem(rel, self._list)
            item.setData(Qt.ItemDataRole.UserRole, str(self._root / rel))
        if self._list.count():
            self._list.setCurrentRow(0)
        self._status_label.setText(f'Совпадений: {len(scored)}')

    # --- выбор файла ---

    def accept_current(self) -> None:
        """Выбрать текущую строку списка (то, что делает Enter)."""
        self._accept_item(self._list.currentItem())

    def _accept_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        abs_path = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        self.file_selected.emit(str(abs_path))

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
                new_row = min(max(self._list.currentRow() + step, 0), self._list.count() - 1)
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
