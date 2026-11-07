from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pyqtp.theme.editor.content_button_editor import ContentButtonEditor
from pyqtp.theme.editor.icon_colors_editor import IconColorsEditor
from pyqtp.theme.editor.main_qss_editor import MainQssEditor
from pyqtp.theme.editor.palette_editor import PaletteEditor
from pyqtp.theme.editor.spinner_editor import WaitingSpinnerEditor
from pyqtp.theme.theme import Theme

if TYPE_CHECKING:
    from pathlib import Path

    from pyqtp.theme.editor.base_theme_part_editor import BaseThemePartEditor


class ThemeEditorDialog(QDialog):
    _parts: list[tuple[str, BaseThemePartEditor]]
    theme: Theme

    apply_signal = Signal(Theme)

    def __init__(
        self,
        *,
        theme: Theme,
        theme_path: Path | None,
        theme_meta_info: list[tuple[str, type[BaseThemePartEditor]]],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle('Редактировать тему')
        self.theme_path = theme_path

        self.main_layout = QVBoxLayout(self)

        self.theme_name_label = QLabel('Наименование темы:')
        self.theme_name_label.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignTrailing
            | Qt.AlignmentFlag.AlignVCenter
        )
        self.theme_name_edit = QLineEdit()
        self.theme_name_edit.setReadOnly(True)
        self.theme_name_label.setBuddy(self.theme_name_edit)

        self.grid_layout = QGridLayout()
        self.grid_layout.addWidget(self.theme_name_label, 0, 0, 1, 1)
        self.grid_layout.addWidget(self.theme_name_edit, 0, 1, 1, 1)

        self.tab_widget = QTabWidget()

        self.set_theme(theme, theme_meta_info)

        self.buttons_layout = QHBoxLayout()
        self.save_button = QPushButton('Сохранить')
        self.save_button.setEnabled(theme_path is not None)
        self.ok_button = QPushButton('Применить')
        self.ok_button.setDefault(True)
        self.cancel_button = QPushButton('Закрыть')
        self.buttons_layout.addWidget(self.save_button)
        self.buttons_layout.addStretch()
        self.buttons_layout.addWidget(self.ok_button)
        self.buttons_layout.addWidget(self.cancel_button)

        self.main_layout.addLayout(self.grid_layout)
        self.main_layout.addWidget(self.tab_widget)
        self.main_layout.addLayout(self.buttons_layout)

        self.save_button.clicked.connect(self.apply_save_exit)
        self.ok_button.clicked.connect(self.apply_theme_changes)
        self.cancel_button.clicked.connect(self.reject)

    def set_theme(
        self,
        theme: Theme,
        theme_meta_info: list[tuple[str, type[BaseThemePartEditor]]],
    ):
        self.theme = theme
        self.theme_name_edit.setText(theme.name)

        self._parts = [
            ('Палитра', PaletteEditor(theme)),
            ('Цвета иконок', IconColorsEditor(theme)),
            ('Таблица стилей', MainQssEditor(theme)),
            ('Параметры спиннера', WaitingSpinnerEditor(theme)),
            ('Параметры кнопки с контентом', ContentButtonEditor(theme)),
        ] + [(tm[0], tm[1](theme)) for tm in theme_meta_info]

        self.tab_widget.clear()
        for tab_title, widget in self._parts:
            self.tab_widget.addTab(widget, tab_title)

    def accept_changes(self):
        self.theme.name = self.theme_name_edit.text().strip()
        for _, widget in self._parts:
            widget.apply()

    def apply_theme_changes(self):
        self.accept_changes()
        self.apply_signal.emit(self.theme)

    def apply_save_exit(self):
        assert self.theme_path is not None

        self.apply_theme_changes()
        with self.theme_path.open(mode='w', encoding='utf-8') as file:
            theme_dict = self.theme.to_dict()
            file.write(json.dumps(theme_dict, ensure_ascii=False, indent=2))

        self.accept()


if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication

    from pyqtp.theme.assets_manager import AssetsManager
    from pyqtp.theme.theme_manager import ThemeManager

    assets_manager = AssetsManager()
    theme_manager = ThemeManager(assets_manager)
    assert theme_manager.current_theme.name == 'Светлая'

    app = QApplication([])

    theme_edit_form = ThemeEditorDialog(
        theme=theme_manager.current_theme,
        theme_path=theme_manager.current_theme_path,
        theme_meta_info=[],
    )

    theme_edit_form.show()

    app.exec()
