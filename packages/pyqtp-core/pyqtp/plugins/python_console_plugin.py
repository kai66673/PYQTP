from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Slot

from pyqtp.plugins.base_plugin import BasePlugin
from pyqtp.views.scripting.scripting_view import ScriptingView

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.controls.api_control.api_control import APIControl


dev_log = logging.getLogger('fcad.dev')


class PythonConsolePlugin(BasePlugin):
    """
    Модуль командной строки Python.

    Обеспечивает:
      - встроенную Python консоль для задач автоматизации.
    """

    _main_wnd: BaseMainWindow
    scripting_view: ScriptingView | None

    @property
    def api(self) -> APIControl:
        return self._main_wnd.api_ctrl

    def teardown_ui(self) -> None:
        self._push_to_widget(dict.fromkeys(self.api.env))
        super().teardown_ui()

    def setup_views(self) -> None:
        self.scripting_view = scripting_view = ScriptingView(
            self._main_wnd, key=f'{self.key}_scripting_view'
        )
        self._main_wnd.view_register(scripting_view, widget_area='bottom')
        self.api.env_updated_signal.connect(self._push_to_widget)
        self._push_to_widget(self.api.env)

    def teardown_views(self) -> None:
        super().teardown_views()

        assert self.scripting_view is not None
        self.scripting_view.on_teardown()
        self._main_wnd.view_deregister(self.scripting_view.key)
        self.scripting_view = None

    @Slot(dict)
    def _push_to_widget(self, variable_dict: dict[str, Any]) -> None:
        assert self.scripting_view is not None
        self.scripting_view.register_variables(variable_dict)
