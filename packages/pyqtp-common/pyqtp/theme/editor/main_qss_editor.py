from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from pyqtp.theme.editor.base_theme_part_editor import BaseThemePartEditor

if TYPE_CHECKING:
    from pyqtp.theme.theme import Theme


class MainQssEditor(BaseThemePartEditor):
    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(theme, parent)

        self.main_layout = QVBoxLayout(self)
        self.setContentsMargins(0, 0, 0, 0)

        self.qss_edit = QPlainTextEdit()
        self.qss_edit.setPlainText(self.theme.main_qss)

        self.main_layout.addWidget(self.qss_edit)

    def apply(self):
        self.theme.main_qss = self.qss_edit.toPlainText()
