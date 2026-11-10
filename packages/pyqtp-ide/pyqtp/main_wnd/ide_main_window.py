from __future__ import annotations

from typing import TYPE_CHECKING

from pyqtp.base.base_main_window import BaseMainWindow
from pyqtp.controls.bookmark_control.bookmark_control import BookmarkControl
from pyqtp.controls.doc_control.document_manager import DocumentManager
from pyqtp.controls.lsp_control.lsp_hub import LspHub
from pyqtp.controls.lsp_control.lsp_monitor_control import LspMonitorControl
from pyqtp.controls.problems_control.problems_control import ProblemsControl
from pyqtp.controls.project_control.project_control import ProjectControl
from pyqtp.controls.project_control.project_settings import (
    KEY_THEME_NAME,
    SECTION_APPEARANCE,
)
from pyqtp.main_wnd.appearance_scope_page import AppearanceScopePage

if TYPE_CHECKING:
    from pyqtp.base.docking_system.base_views_manager import ViewsManagerType


class IdeMainWindow(BaseMainWindow):
    doc_ctrl: DocumentManager
    lsp_hub: LspHub
    project_ctrl: ProjectControl
    bookmark_ctrl: BookmarkControl
    problems_ctrl: ProblemsControl
    lsp_monitor_ctrl: LspMonitorControl

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
        # «Внешний вид» core — это страница только слоя приложения; в IDE
        # та же клавиша/порядок у scope-aware подкласса (этап 9.6B2)
        self.settings_page_unregister('appearance')
        self.settings_page_register(AppearanceScopePage())
        self.lsp_hub = self.plug_register(LspHub())
        self.doc_ctrl = self.plug_register(DocumentManager())
        self.project_ctrl = self.plug_register(ProjectControl())
        # закладки (этап 9.7) — после проекта: его корень определяет, живы ли закладки
        self.bookmark_ctrl = self.plug_register(BookmarkControl())
        # панель «Проблемы» (этап 10.1) — агрегирует стор диагностик хаба
        self.problems_ctrl = self.plug_register(ProblemsControl())
        # «Монитор LSP» (этап 10.3) — кольцо трафика того же хаба
        self.lsp_monitor_ctrl = self.plug_register(LspMonitorControl())

    #############################################
    # Тема: два слоя (этап 9.6B, см. ThemeMainWindow) #

    def handle_theme_switch(self, theme_name: str) -> None:
        """Комбик темы в углу меню: пока проектное перекрытие темы держит
        эффективное значение — правим слой проекта (``.pyqtp/settings.json``),
        иначе — слой настроек приложения (маршрутизация по источнику
        текущего значения).
        """
        if self.project_ctrl.theme_provided_by_project():
            self.project_ctrl.project_settings_set(
                SECTION_APPEARANCE, KEY_THEME_NAME, theme_name
            )
        else:
            self.theme_set_app(theme_name)

    def theme_on_app_changed(self, theme_name: str) -> None:
        """Смена темы в слое приложения не «пробивает» проектное перекрытие:
        эффективной она станет только когда перекрытия нет.
        """
        if self.project_ctrl.theme_provided_by_project():
            return
        self.theme_apply(theme_name)

