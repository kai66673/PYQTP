from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout

from pyqtp.views.base_view import BaseView
from pyqtp.views.terminal.terminal import XTermWidget

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow


class TerminalView(BaseView):
    def __init__(
        self, main_wnd: BaseMainWindow, /, *, key: str | None = None
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Терминал')
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon(
                'terminal.svg', is_checkable=True
            )
        )

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.terminal_widget = XTermWidget()
        self.main_layout.addWidget(self.terminal_widget)

    def on_teardown(self):
        self.terminal_widget.on_teardown()
