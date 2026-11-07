from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QToolBar, QWidget

if TYPE_CHECKING:
    from bcad.theme.theme_manager import ThemeManager


class ThemeSwitchToolBar(QToolBar):
    def __init__(
        self, theme_manager: ThemeManager, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setObjectName('ThemeSwitchToolBar')

        self.theme_combo_box = QComboBox()
        self.theme_combo_box.addItems(theme_manager.theme_names)
        self.theme_combo_box.setCurrentText(theme_manager.current_theme.name)

        self.addWidget(self.theme_combo_box)

        self.theme_combo_box.currentTextChanged.connect(theme_manager.switch_to)
