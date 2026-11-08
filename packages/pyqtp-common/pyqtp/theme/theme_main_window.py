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

        # имя темы в слое настроек приложения (без учёта проектных
        # перекрытий, этап 9.6B) — его пишет к себе theme_settings_write,
        # чтобы тема проекта не «утекла» в настройки приложения при свёртке
        self._theme_app_name = self.theme_manager.current_theme.name

        self.theme_switch_toolbar = ThemeSwitchToolBar(
            self.theme_manager, on_switch=self.handle_theme_switch
        )
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

    ############
    # Тема: два слоя (этап 9.6B).
    # Слой приложения — значение из настроек приложения; слой проекта —
    # перекрытие из .pyqtp/settings.json (IDE). Эффективная тема — верхнее
    # перекрытие; правки слоя приложения не «пробивают» перекрытие проекта.
    ############

    @property
    def theme_app_name(self) -> str:
        """Имя темы в слое настроек приложения (без проектных перекрытий)."""
        return self._theme_app_name

    def handle_theme_switch(self, theme_name: str) -> None:
        """Выбор темы уголковым комбиком (хук: переопределяется в IDE, там
        при проектном перекрытии темы правится слой проекта).
        """
        self.theme_set_app(theme_name)

    def theme_on_app_changed(self, theme_name: str) -> None:
        """Хук: что делать со сменой значения темы слоем приложения
        (в IDE — не трогать эффективную тему, пока её держит перекрытие
        проекта).
        """
        self.theme_apply(theme_name)

    def theme_set_app(self, theme_name: str) -> None:
        """Меняет значение темы в слое приложения (неизвестная — отказ)."""
        if theme_name not in self.theme_manager.theme_names:
            dev_log.info(f'Невозможно установить тему "{theme_name}"')
            return
        self._theme_app_name = theme_name
        self.theme_on_app_changed(theme_name)

    def theme_apply(self, theme_name: str) -> None:
        """Делает тему эффективной, не трогая слой настроек приложения."""
        if theme_name == self.theme_manager.current_theme.name:
            return  # уже эффективна — лишних emit без смысла
        if theme_name not in self.theme_manager.theme_names:
            dev_log.info(f'Невозможно установить тему "{theme_name}"')
            return
        self.theme_manager.switch_to(theme_name)
        self.theme_switch_toolbar.theme_combo_box.blockSignals(True)  # noqa: FBT003
        self.theme_switch_toolbar.theme_combo_box.setCurrentText(theme_name)
        self.theme_switch_toolbar.theme_combo_box.blockSignals(False)  # noqa: FBT003

    def theme_settings_read(self, settings: QSettings) -> None:
        theme_name = settings.value('theme_name', 'Светлая')
        assert isinstance(theme_name, str)
        if theme_name not in self.theme_manager.theme_names:
            dev_log.info(f'Невозможно установить тему "{theme_name}"')
            return
        self._theme_app_name = theme_name
        self.theme_apply(theme_name)

    def theme_settings_write(self, settings: QSettings) -> None:
        settings.setValue('theme_name', self.theme_app_name)
