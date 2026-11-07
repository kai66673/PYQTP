from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyqtp.plugins.base_plugin import BasePlugin
from pyqtp.views.terminal.terminal_view import TerminalView

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow

dev_log = logging.getLogger('fcad.dev')


class TerminalPlugin(BasePlugin):
    """
    Модуль командной строки.

    Обеспечивает:
      - встроенный терминал.
    """

    _main_wnd: BaseMainWindow
    terminal_view: TerminalView | None

    def setup_views(self) -> None:
        self.terminal_view = terminal_view = TerminalView(
            self._main_wnd, key=f'{self.key}_terminal_view'
        )
        self._main_wnd.view_register(terminal_view, widget_area='bottom')

    def teardown_views(self) -> None:
        super().teardown_views()

        assert self.terminal_view is not None
        self.terminal_view.on_teardown()
        self._main_wnd.view_deregister(self.terminal_view.key)
        self.terminal_view = None
