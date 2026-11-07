from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

from pyqtp.utils.styles.styled_app import StyledApplication


class DarkWinApplication(StyledApplication):
    def __init__(self, args: list[str]):
        super().__init__(args)

        dark_gray = QColor(53, 53, 53)
        gray = QColor(128, 128, 128)
        black = QColor(25, 25, 25)
        blue = QColor(
            0, 102, 153
        )  # Альтернативные варианты (42, 162, 162), (0, 153, 153)

        dark_palette = QPalette()
        dark_palette.setColor(QPalette.ColorRole.Window, dark_gray)
        dark_palette.setColor(
            QPalette.ColorRole.WindowText, Qt.GlobalColor.white
        )
        dark_palette.setColor(QPalette.ColorRole.Base, black)
        dark_palette.setColor(QPalette.ColorRole.AlternateBase, dark_gray)
        dark_palette.setColor(QPalette.ColorRole.ToolTipBase, blue)
        dark_palette.setColor(
            QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white
        )
        dark_palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        dark_palette.setColor(QPalette.ColorRole.Button, dark_gray)
        dark_palette.setColor(
            QPalette.ColorRole.ButtonText, Qt.GlobalColor.white
        )
        dark_palette.setColor(QPalette.ColorRole.Link, blue)
        dark_palette.setColor(QPalette.ColorRole.Highlight, blue)
        dark_palette.setColor(
            QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active, QPalette.ColorRole.Button, gray.darker()
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, gray
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, gray
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, gray
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Light, dark_gray
        )

        dark_palette.setColor(QPalette.ColorRole.Light, dark_gray)
        self.setPalette(dark_palette)
