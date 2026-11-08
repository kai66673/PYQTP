from __future__ import annotations

from typing import TYPE_CHECKING

from pyqtp.base.base_main_window import BaseMainWindow
from pyqtp.controls.doc_control.document_manager import DocumentManager
from pyqtp.controls.lsp_control.lsp_hub import LspHub
from pyqtp.controls.project_control.project_control import ProjectControl

if TYPE_CHECKING:
    from pyqtp.base.docking_system.base_views_manager import ViewsManagerType


class IdeMainWindow(BaseMainWindow):
    doc_ctrl: DocumentManager
    lsp_hub: LspHub
    project_ctrl: ProjectControl

    @staticmethod
    def dbus_iface_name() -> str:
        return 'pyqtp.Ide'

    @staticmethod
    def dbus_object_name() -> str:
        return '/CentralViewExample'

    def init_dock_system(self, dock_system_type: ViewsManagerType):
        super().init_dock_system(dock_system_type)
        self.show_central_widget()

    def init_controls(self) -> None:
        super().init_controls()
        self.lsp_hub = self.plug_register(LspHub())
        self.doc_ctrl = self.plug_register(DocumentManager())
        self.project_ctrl = self.plug_register(ProjectControl())

