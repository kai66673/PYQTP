from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QToolBar, QWidget

if TYPE_CHECKING:
    from collections.abc import Callable

    from bcad.theme.theme_manager import ThemeManager


class ThemeSwitchToolBar(QToolBar):
    def __init__(
        self,
        theme_manager: ThemeManager,
        parent: QWidget | None = None,
        on_switch: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName('ThemeSwitchToolBar')

        self.theme_combo_box = QComboBox()
        self.theme_combo_box.addItems(theme_manager.theme_names)
        self.theme_combo_box.setCurrentText(theme_manager.current_theme.name)

        self.addWidget(self.theme_combo_box)

        # выбор темы уводит не в сам ThemeManager, а в обработчик главного
        # окна (хук handle_theme_switch — этап 9.6B: при открытом проекте с
        # проектным перекрытием темы правится слой проекта); без обработчика
        # — прежнее прямое переключение (standalone-использование тулбара)
        self.theme_combo_box.currentTextChanged.connect(
            on_switch if on_switch is not None else theme_manager.switch_to
        )
