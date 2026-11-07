from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyqtp.base.pluggable import PluggableObject

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


dev_log = logging.getLogger('bcad.dev')


class BasePlugin(PluggableObject):
    """
    Базовый класс плагина приложения.

    В отличие от контроллера, может выгружаться из памяти.
    Отличается процедура регистрации:
        нет setup_relations, нет аргументов командной строки.
    """

    def register(self, main_wnd: PluggableMainWindow) -> BasePlugin:
        """Регистрирует плагин в приложении."""
        self._main_wnd = main_wnd
        self.settings_read()
        self.setup()
        if main_wnd.setup_ui_done:
            self.setup_ui()
        else:
            main_wnd.setup_ui_signal.connect(self.setup_ui)
        return self

    def deregister(self, main_wnd: PluggableMainWindow) -> None:  # noqa: ARG002
        """
        Дерегистрируем плагин в приложении.

        Перед вызовом необходимо опрашивать плагин с помощью on_shutdown.
        """
        assert self._main_wnd is not None
        self.settings_write()
        if self._main_wnd.setup_ui_done:
            self.teardown_ui()
        self.teardown()
