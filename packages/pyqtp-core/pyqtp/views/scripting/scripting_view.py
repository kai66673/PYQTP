from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QUrl, Slot
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QVBoxLayout

from pyqtp.views.base_view import BaseView
from pyqtp.views.scripting.ipython_console_widget import IPythonConsoleWidget

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow


class ScriptingView(BaseView):
    def __init__(
        self, main_wnd: BaseMainWindow, /, *, key: str | None = None
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Командная строка Python')
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon(
                'scripting/python.svg', is_checkable=True
            )
        )

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.python_widget = IPythonConsoleWidget()
        self.main_layout.addWidget(self.python_widget)  # type:ignore[reportArgumentType]
        self.python_widget.installEventFilter(self)  # type:ignore[reportAttributeAccessIssue]

        self._main_wnd.theme_manager.theme_changed.connect(
            self.python_widget.set_theme
        )
        self.python_widget.set_theme(self._main_wnd.theme_manager.current_theme)

    @Slot(bool)
    def on_modality_changed(self, *, is_modal: bool, is_self_modal: bool):
        pass

    def state_read(self) -> None:
        settings = self._main_wnd.settings
        font_size = settings.value(f'{self.key}/font_size', 10, type=int)
        assert isinstance(font_size, int)
        self.python_widget.font_size = font_size
        self.python_widget.reset_font()

    def state_write(self) -> None:
        settings = self._main_wnd.settings
        settings.setValue(f'{self.key}/font_size', self.python_widget.font_size)

    def on_teardown(self):
        self.python_widget.on_teardown()

    def register_variables(self, variable_dict) -> None:
        self.python_widget.push_variables(variable_dict)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched == self.python_widget and event.type() == QEvent.Type.Wheel:
            assert isinstance(event, QWheelEvent)
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                self.python_widget.handle_wheel_with_ctrl(event)
                return True

        return False

    @property
    def help_context(self) -> str | QUrl:
        return QUrl('qrc:/help/scripting_view/index.html')
