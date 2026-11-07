from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QMainWindow, QToolBar, QVBoxLayout, QWidget

from pyqtp.views.base_view import BaseView

if TYPE_CHECKING:
    from PySide6.QtGui import QAction

    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class BaseMainWindowView(BaseView):
    """
    Базовый класс для представления с собственной инструментальной панелью.

    Представление реализует виджет-контейнер для отображения в стыкуемой панели.

    """

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self._inner_main_window = QMainWindow()

        self._toolbar = self._inner_main_window.addToolBar('Действия')
        self._toolbar.setObjectName(f'{key}__ActionsToolBar')
        self._toolbar.setFloatable(False)
        self._toolbar.toggleViewAction().setEnabled(False)
        self._toolbar.setContextMenuPolicy(
            Qt.ContextMenuPolicy.PreventContextMenu
        )

        self.main_layout.addWidget(self._inner_main_window)

        self._toolbar.close()
        self._is_toolbar_empty = True

    def set_toolbar(self, toolbar: QToolBar):
        self._toolbar.close()
        self._inner_main_window.removeToolBar(self._toolbar)

        self._toolbar = toolbar
        self._toolbar.setObjectName(f'{self.key}__ActionsToolBar')
        self._toolbar.setFloatable(False)
        self._toolbar.toggleViewAction().setEnabled(False)
        self._toolbar.setContextMenuPolicy(
            Qt.ContextMenuPolicy.PreventContextMenu
        )

        self._inner_main_window.addToolBar(self._toolbar)
        self._toolbar.show()
        self._is_toolbar_empty = False

    def add_toolbar_action(self, action: QAction):
        self._toolbar.addAction(action)
        self._toolbar.show()

    def add_toolbar_actions(self, actions: list[QAction]):
        self._toolbar.addActions(actions)
        self._toolbar.show()
        self._is_toolbar_empty = False

    def add_toolbar_widget(self, widget: QWidget):
        self._toolbar.addWidget(widget)
        self._toolbar.show()
        self._is_toolbar_empty = False

    def set_widget(self, widget: QWidget):
        self._inner_main_window.setCentralWidget(widget)

    def state_read(self) -> None:
        settings = self._main_wnd.settings
        window_state = settings.value(f'{self.key}/window_state', QByteArray())
        assert isinstance(window_state, QByteArray)
        if not window_state.isEmpty():
            self._inner_main_window.restoreState(window_state)
        if not self._is_toolbar_empty:
            self._toolbar.show()

    def state_write(self) -> None:
        settings = self._main_wnd.settings
        settings.setValue(
            f'{self.key}/window_state', self._inner_main_window.saveState()
        )
