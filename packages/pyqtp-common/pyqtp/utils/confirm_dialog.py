from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


class ConfirmDialog(QMessageBox):
    def __init__(
        self, title: str, info_text: str, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setText(info_text)

        self.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        self.button(QMessageBox.StandardButton.Yes).setText('Да')
        self.button(QMessageBox.StandardButton.No).setText('Нет')

        self.setDefaultButton(QMessageBox.StandardButton.Yes)
        self.reply = self.exec()
