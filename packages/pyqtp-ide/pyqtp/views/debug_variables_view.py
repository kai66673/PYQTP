from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.controls.dap_control.debug_tree import ScopeRow, VariableRow

# роли Qt.UserRole: у строки — variablesReference ячейки, у плейсхолдера —
# признак «плейсхолдер незагруженной ячейки»
_ROLE_REF = Qt.ItemDataRole.UserRole
_ROLE_PLACEHOLDER = Qt.ItemDataRole.UserRole + 1

# дочерняя строка-заглушка: даёт стрелку раскрытия до реального запроса
_PLACEHOLDER_TEXT = '…'
# метка плейсхолдера в _ROLE_PLACEHOLDER (int, не bool — Qt data() живёт в int)
_PLACEHOLDER_MARK = 1


class DebugVariablesView(BaseMainWindowView):
    """Панель «Переменные» (этап 11, срез D2): дерево областей/значений.

    Вьюха пассивная (паттерн панелей 9.x/10.x): показывает то, что положил
    ``DapControl`` (области текущего кадра — ``set_scopes``, детей ячейки —
    ``replace_children``). Лень загрузки — наружу только намерение
    ``children_requested(variablesReference)`` при первом раскрытии ячейки с
    детьми (плейсхолдер «…» рисует стрелку заранее; ``expensive``-области
    debugpy раскрываются так же).

    Программные проверки (тесты без vision): ``scope_names``,
    ``child_names(ref)``, ``is_loaded(ref)``, ``is_empty``.
    """

    children_requested = Signal(int)  # variablesReference раскрытой ячейки

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Переменные')

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(['Имя', 'Значение'])
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.itemExpanded.connect(self._on_expanded)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._tree)
        container = QWidget()
        container.setLayout(layout)
        self.set_widget(container)

        # ref -> строка ячейки (для вставки детей и флагов загрузки)
        self._items_by_ref: dict[int, QTreeWidgetItem] = {}

    # ------------------------------------------------------------------ #
    # Показ (вызывает только DapControl из GUI-потока)                   #
    # ------------------------------------------------------------------ #

    def set_scopes(self, scopes: Sequence[ScopeRow]) -> None:
        """Заменить верхний уровень дерева областями текущего кадра."""
        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            self._items_by_ref.clear()
            for row in scopes:
                item = self._make_item(row.name, '', row.scope_id)
                if row.expensive:
                    item.setToolTip(0, 'дорогая область — читается по раскрытию')
                self._tree.addTopLevelItem(item)
                self._register(row.scope_id, item)
        finally:
            self._tree.setUpdatesEnabled(True)

    def replace_children(self, parent_ref: int, rows: Sequence[VariableRow]) -> None:
        """Выгрузить ответ ``variables`` в ячейку ``parent_ref`` (без детей-ещё-детей).

        Пустой ответ — ячейка остаётся нераскрытой (плейсхолдер на месте,
        повторное раскрытие запросит снова: так живучее с дорогими/поздними
        областями debugpy, чем молча съеденная стрелка).
        """
        parent = self._items_by_ref.get(parent_ref)
        if parent is None:
            return  # кадр/область сменились (новое останове) — запоздалый ответ
        self._clear_children(parent)
        if not rows:
            self._add_placeholder(parent)
            return
        for row in rows:
            child = self._make_item(row.name, row.value, row.children_ref)
            if row.type_name:
                child.setToolTip(0, f'{row.name}: {row.type_name}')
            parent.addChild(child)
            if row.children_ref:
                self._register(row.children_ref, child)
        parent.setExpanded(parent.isExpanded())

    def clear_all(self) -> None:
        """Снять весь показ (новое останове вне слоёв/конец сессии)."""
        self._tree.clear()
        self._items_by_ref.clear()

    # ------------------------------------------------------------------ #
    # Программные проверки (тесты без vision)                            #
    # ------------------------------------------------------------------ #

    def scope_names(self) -> list[str]:
        """Имена верхних областей по порядку."""
        return [
            self._tree.topLevelItem(i).text(0)
            for i in range(self._tree.topLevelItemCount())
        ]

    def child_names(self, parent_ref: int) -> list[str]:
        """Имена выгруженных детей ячейки (плейсхолдер не считается)."""
        parent = self._items_by_ref.get(parent_ref)
        if parent is None:
            return []
        return [
            parent.child(i).text(0)
            for i in range(parent.childCount())
            if not parent.child(i).data(0, _ROLE_PLACEHOLDER)
        ]

    def child_value(self, parent_ref: int, name: str) -> str | None:
        """Значение ребёнка по имени (или None — нет такой строки)."""
        parent = self._items_by_ref.get(parent_ref)
        if parent is None:
            return None
        for i in range(parent.childCount()):
            child = parent.child(i)
            if not child.data(0, _ROLE_PLACEHOLDER) and child.text(0) == name:
                return child.text(1)
        return None

    def is_loaded(self, ref: int) -> bool:
        """Загружены ли дети ячейки (плейсхолдер снят реальным ответом)."""
        item = self._items_by_ref.get(ref)
        if item is None:
            return False
        return not self._has_placeholder(item)

    def is_empty(self) -> bool:
        """Показ пуст (конец сессии/сброс)."""
        return self._tree.topLevelItemCount() == 0

    # ------------------------------------------------------------------ #
    # Внутреннее                                                        #
    # ------------------------------------------------------------------ #

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        ref = item.data(0, _ROLE_REF)
        if not isinstance(ref, int) or ref == 0:
            return
        if not self._has_placeholder(item):
            return  # дети уже выгружены — повторный запрос не нужен
        self.children_requested.emit(ref)

    def _make_item(self, name: str, value: str, children_ref: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name, value])
        item.setData(0, _ROLE_REF, children_ref)
        if children_ref:
            self._add_placeholder(item)
        return item

    def _register(self, ref: int, item: QTreeWidgetItem) -> None:
        self._items_by_ref[ref] = item

    @staticmethod
    def _add_placeholder(item: QTreeWidgetItem) -> None:
        placeholder = QTreeWidgetItem([_PLACEHOLDER_TEXT, ''])
        placeholder.setData(0, _ROLE_PLACEHOLDER, _PLACEHOLDER_MARK)
        item.addChild(placeholder)

    @staticmethod
    def _has_placeholder(item: QTreeWidgetItem) -> bool:
        for i in range(item.childCount()):
            if item.child(i).data(0, _ROLE_PLACEHOLDER):
                return True
        return False

    @staticmethod
    def _clear_children(item: QTreeWidgetItem) -> None:
        while item.childCount():
            delete = item.takeChild(0)
            del delete
