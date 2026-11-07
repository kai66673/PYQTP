from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from pyqtp.theme.assets_manager import AssetsManager
from pyqtp.theme.theme_manager import ThemeManager
from pyqtp.theme.theme_switch_toolbar import ThemeSwitchToolBar
from pyqtp.utils.priority_menu import PriorityMenuBar
from pyqtp.utils.waiting_spinner import WaitingSpinner

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

    from pyqtp.theme.theme import Theme


dev_log = logging.getLogger('bcad.dev')


class ManagedMainWindow(QMainWindow):
    assets_manager: AssetsManager

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.assets_manager = AssetsManager()


class ThemeMainWindow(ManagedMainWindow):
    theme_manager: ThemeManager

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.theme_manager = ThemeManager(self.assets_manager)

        self.theme_switch_toolbar = ThemeSwitchToolBar(self.theme_manager)
        menu_bar = PriorityMenuBar()
        menu_bar.setCornerWidget(self.theme_switch_toolbar)
        self.setMenuBar(menu_bar)

        self.theme_manager.theme_changed.connect(self.handle_theme_changed)
        self.handle_theme_changed(self.theme_manager.current_theme)

    def handle_theme_changed(self, current_theme: Theme):
        gui_app = QApplication.instance()
        assert isinstance(gui_app, QApplication)
        gui_app.setPalette(current_theme.create_palette())
        gui_app.setStyleSheet(current_theme.main_qss)
        # устанавливаем параметры для WaitingSpinner
        WaitingSpinner.set_parameters(current_theme.meta['waiting_spinner'])

    def theme_settings_read(self, settings: QSettings) -> None:
        theme_name = settings.value('theme_name', 'Светлая')
        assert isinstance(theme_name, str)
        if theme_name not in self.theme_manager.theme_names:
            dev_log.info(f'Невозможно установить тему "{theme_name}"')
            return
        self.theme_manager.switch_to(theme_name)
        self.theme_switch_toolbar.theme_combo_box.blockSignals(True)  # noqa: FBT003
        self.theme_switch_toolbar.theme_combo_box.setCurrentText(theme_name)
        self.theme_switch_toolbar.theme_combo_box.blockSignals(False)  # noqa: FBT003

    def theme_settings_write(self, settings: QSettings) -> None:
        settings.setValue('theme_name', self.theme_manager.current_theme.name)
