from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.controls.bookmark_control.bookmarks import Bookmark

# в UserRole child-элементов лежит пара (путь файла, строка); у заголовков
# файлов — None
_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_ENTRY_PAIR_LEN = 2


def _entry_of(item: QTreeWidgetItem | None) -> tuple[str, int] | None:
    """Пара (путь, строка) из элемента дерева (None — не закладка)."""
    if item is None:
        return None
    data = item.data(0, _ENTRY_ROLE)
    if not isinstance(data, tuple) or len(data) != _ENTRY_PAIR_LEN:
        return None
    return str(data[0]), int(data[1])


class _BookmarksTree(QTreeWidget):
    """Дерево панели с удалением закладки по клавише Delete."""

    remove_requested = Signal(str, int)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Delete):
            data = _entry_of(self.currentItem())
            if data is not None:
                self.remove_requested.emit(*data)
                event.accept()
                return
        super().keyPressEvent(event)


class BookmarksView(BaseMainWindowView):
    """Панель «Закладки» (этап 9.7): закладки проекта деревом «файл -> строки».

    Вьюха пассивная (паттерн панелей 9.x): состояние закладок живёт на
    ``BookmarkControl`` (файл ``<root>/.pyqtp/bookmarks.json``), а контрол
    складывает сюда очередной снимок через ``set_bookmarks``. Двойной
    клик/Enter по строке эмитит ``navigate_requested`` (payload для
    ``DocumentManager.open_definition_uri``), Delete/контекстное меню —
    ``remove_requested``/``clear_all_requested`` намерениями на контрол.
    """

    navigate_requested = Signal(dict)
    remove_requested = Signal(str, int)
    clear_all_requested = Signal()

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Закладки')

        self._root: Path | None = None

        self._status_label = QLabel('Проект не открыт')

        self._tree = _BookmarksTree()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.itemActivated.connect(self._on_item_activated)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.remove_requested.connect(self.remove_requested)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._status_label)
        layout.addWidget(self._tree)
        self.set_widget(container)

    # --- снимок закладок (пишет контрол после любого изменения) ---

    def set_bookmarks(self, root: Path | None, items: Sequence[Bookmark]) -> None:
        """Перестроить дерево по снимку; ``root=None`` — проект закрыт."""
        self._root = root
        self._tree.clear()
        if root is None:
            self._set_status('Проект не открыт')
            return
        grouped: dict[str, list[int]] = {}
        for item in items:
            grouped.setdefault(item.path, []).append(item.line)
        for path in sorted(grouped):
            header = QTreeWidgetItem([self._display_path(path, root)])
            header.setToolTip(0, path)
            for line in grouped[path]:
                child = QTreeWidgetItem([f'Строка {line}'])
                child.setData(0, _ENTRY_ROLE, (path, line))
                header.addChild(child)
            self._tree.addTopLevelItem(header)
            header.setExpanded(True)
        self._set_status(
            f'Закладок: {len(items)}' if items else 'Нет закладок'
        )

    def _display_path(self, path: str, root: Path) -> str:
        """Относительный путь внутри проекта (чужой путь — как есть)."""
        candidate = Path(path)
        try:
            return str(candidate.relative_to(root))
        except ValueError:
            return path

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)

    # --- намерения пользователя ---

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        entry = _entry_of(item)
        if entry is None:
            return
        path, line = entry
        self.navigate_requested.emit(
            {
                'uri': QUrl.fromLocalFile(path).toString(),
                'range': {'startLineNumber': line, 'startColumn': 1},
            }
        )

    def _on_context_menu(self, position: QPoint) -> None:
        """Контекстное меню: «Удалить закладку»/«Удалить все закладки»."""
        menu = QMenu(self)
        entry = _entry_of(self._tree.itemAt(position))
        if entry is not None:
            path, line = entry
            menu.addAction(
                'Удалить закладку',
                lambda: self.remove_requested.emit(path, line),
            )
        if self._tree.topLevelItemCount():
            menu.addAction('Удалить все закладки', self.clear_all_requested.emit)
        if not menu.isEmpty():
            menu.exec(self._tree.viewport().mapToGlobal(position))
