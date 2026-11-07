from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtWidgets import QPlainTextEdit, QVBoxLayout

from pyqtp.base.base_main_window import BaseMainWindow
from pyqtp.launcher import run_main_window
from pyqtp.views.base_view import BaseView

if TYPE_CHECKING:
    from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class DocumentView(BaseView):
    def __init__(self, main_wnd: PluggableMainWindow, /, *, key: str | None = None) -> None:
        super().__init__(main_wnd, key=key)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_view = QPlainTextEdit()
        self.main_layout.addWidget(self.edit_view)


class CentralMainWindow(BaseMainWindow):
    @staticmethod
    def dbus_iface_name() -> str:
        return 'bcad.mipt.CentralDock'

    @staticmethod
    def dbus_object_name() -> str:
        return '/CentralDock'

    def init_dock_system(self, dock_system_type: ViewsManagerType):
        super().init_dock_system(dock_system_type)
        self.show_central_widget()
        self.view_central_register(DocumentView(self, key='file_1.txt'))
        self.view_central_register(DocumentView(self, key='file_2.txt'))


if __name__ == '__main__':
    run_main_window(CentralMainWindow, org_name='PYQTP', app_name='PYQTP-CENTRAL-DOCK')
