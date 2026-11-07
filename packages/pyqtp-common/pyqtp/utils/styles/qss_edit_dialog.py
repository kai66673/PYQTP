from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class QssEditDialog(QDialog):
    def __init__(self, value: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle('Редактировать таблицу стилей')

        self.main_layout = QVBoxLayout(self)

        self.qss_editor = QPlainTextEdit()
        self.qss_editor.setPlainText(value)

        self.buttons_layout = QHBoxLayout()
        self.ok_button = QPushButton('Выполнить')
        self.ok_button.setDefault(True)
        self.cancel_button = QPushButton('Отмена')
        self.buttons_layout.addStretch()
        self.buttons_layout.addWidget(self.ok_button)
        self.buttons_layout.addWidget(self.cancel_button)

        self.main_layout.addWidget(self.qss_editor)
        self.main_layout.addLayout(self.buttons_layout)

        self.ok_button.clicked.connect(self.try_accept)
        self.cancel_button.clicked.connect(self.reject)

    def get_qss(self) -> str:
        return self.qss_editor.toPlainText()

    def try_accept(self):
        # TODO: validate qss...
        self.accept()
