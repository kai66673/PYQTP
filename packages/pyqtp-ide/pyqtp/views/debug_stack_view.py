from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.dap_control.debug_tree import frame_display
from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.controls.dap_control.debug_tree import FrameRow, ThreadRow

# роль Qt.UserRole: frame_id строки кадра
_ROLE_FRAME_ID = Qt.ItemDataRole.UserRole


class DebugStackView(BaseMainWindowView):
    """Панель «Стек вызовов» (этап 11, срез D2).

    Вьюха пассивная (паттерн панелей 9.x/10.x): список кадров показывает то,
    что положил ``DapControl``; наружу — только намерения ``frame_activated``
    (двойной клик по кадру) и ``thread_selected`` (смена потока в комбике).
    Собственных DAP-запросов не шлёт.

    Комбик потоков показан только при >1 потока (offscreen-тесты меряют
    ``thread_count()`` — видимость Qt в offscreen не проверяема).
    Текущий кадр (выбранный для переменных) — жирным шрифтом; программная
    проверка — ``current_frame_id()``.
    """

    frame_activated = Signal(int)  # frame_id кадра из stackTrace
    thread_selected = Signal(int)  # thread_id из threads

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Стек вызовов')

        self._thread_label = QLabel('Поток:')
        self._thread_combo = QComboBox()
        self._thread_combo.currentIndexChanged.connect(self._on_thread_changed)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)

        header = QHBoxLayout()
        header.addWidget(self._thread_label)
        header.addWidget(self._thread_combo)
        header.addStretch(1)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self._tree)
        container = QWidget()
        container.setLayout(layout)
        self.set_widget(container)

    # ------------------------------------------------------------------ #
    # Показ (вызывает только DapControl из GUI-потока)                   #
    # ------------------------------------------------------------------ #

    def set_threads(self, threads: Sequence[ThreadRow], current_thread: int) -> None:
        """Заполнить комбик потоков и выбрать текущий (сигнал при этом не шлётся)."""
        self._syncing = True
        try:
            self._thread_combo.clear()
            for row in threads:
                self._thread_combo.addItem(row.name, row.thread_id)
            index = self._thread_combo.findData(current_thread)
            if index >= 0:
                self._thread_combo.setCurrentIndex(index)
            has_choice = len(threads) > 1
            self._thread_label.setVisible(has_choice)
            self._thread_combo.setVisible(has_choice)
        finally:
            self._syncing = False

    def set_frames(
        self, frames: Sequence[FrameRow], root: Path | None,
    ) -> None:
        """Заменить список кадров (пустой набор — панель очищена)."""
        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            for row in frames:
                item = QTreeWidgetItem([frame_display(row, root)])
                item.setData(0, _ROLE_FRAME_ID, row.frame_id)
                item.setToolTip(0, row.path or row.name)
                self._tree.addTopLevelItem(item)
        finally:
            self._tree.setUpdatesEnabled(True)

    def set_current_frame(self, frame_id: int | None) -> None:
        """Выделить текущий кадр (жирный); None — выделения нет."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            font = item.font(0)
            font.setBold(self._is_current(item, frame_id))
            item.setFont(0, font)
            if self._is_current(item, frame_id):
                self._tree.setCurrentItem(item)

    # ------------------------------------------------------------------ #
    # Программные проверки (тесты без vision)                            #
    # ------------------------------------------------------------------ #

    def frame_ids(self) -> list[int]:
        """frame_id всех показанных кадров по порядку."""
        ids: list[int] = []
        for i in range(self._tree.topLevelItemCount()):
            data = self._tree.topLevelItem(i).data(0, _ROLE_FRAME_ID)
            if isinstance(data, int):
                ids.append(data)
        return ids

    def frame_texts(self) -> list[str]:
        """Тексты строк кадра (порядок как в ``frame_ids``)."""
        return [
            self._tree.topLevelItem(i).text(0)
            for i in range(self._tree.topLevelItemCount())
        ]

    def current_frame_id(self) -> int | None:
        """frame_id жирного (текущего) кадра или None."""
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.font(0).bold():
                data = item.data(0, _ROLE_FRAME_ID)
                return data if isinstance(data, int) else None
        return None

    def thread_ids(self) -> list[int]:
        """thread_id из комбика потоков (программная проверка числа потоков)."""
        ids: list[int] = []
        for i in range(self._thread_combo.count()):
            data = self._thread_combo.itemData(i)
            if isinstance(data, int):
                ids.append(data)
        return ids

    def current_thread_id(self) -> int | None:
        data = self._thread_combo.currentData()
        return data if isinstance(data, int) else None

    # ------------------------------------------------------------------ #
    # Пользовательские намерения                                        #
    # ------------------------------------------------------------------ #

    def _on_double_clicked(
        self, item: QTreeWidgetItem, _column: int,
    ) -> None:
        frame_id = item.data(0, _ROLE_FRAME_ID)
        if isinstance(frame_id, int):
            self.frame_activated.emit(frame_id)

    def _on_thread_changed(self, _index: int) -> None:
        if getattr(self, '_syncing', False):
            return
        thread_id = self._thread_combo.currentData()
        if isinstance(thread_id, int):
            self.thread_selected.emit(thread_id)

    @staticmethod
    def _is_current(item: QTreeWidgetItem, frame_id: int | None) -> bool:
        return (
            frame_id is not None
            and item.data(0, _ROLE_FRAME_ID) == frame_id
        )
