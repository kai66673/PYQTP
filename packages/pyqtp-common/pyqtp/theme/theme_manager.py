from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon

from pyqtp.theme.assets_manager import AssetsManager
from pyqtp.theme.editor.theme_editor_dialog import ThemeEditorDialog
from pyqtp.theme.theme import Theme
from pyqtp.theme.theme_svg_icon_engine import ThemeSvgIconEngine
from pyqtp.utils.waiting_spinner import WaitingSpinner

if TYPE_CHECKING:
    from pyqtp.theme.editor.base_theme_part_editor import BaseThemePartEditor


class ThemeManager(QObject):
    _themes: list[tuple[Theme, Path | None]]
    _current_theme: Theme
    _current_theme_path: Path | None
    _icons: dict[tuple[str, bool], QIcon]
    _assets_manager: AssetsManager
    _not_found_icon_path: Path
    _theme_meta_info: list[tuple[str, type[BaseThemePartEditor]]]

    theme_changed = Signal(Theme)

    def __init__(
        self, assets_manager: AssetsManager, parent: QObject | None = None
    ):
        super().__init__(parent)
        self._assets_manager = assets_manager
        self._not_found_icon_path = Path(__file__).parent / 'not_found_icon.svg'
        self._theme_meta_info = []

        current_dir = Path(__file__).parent

        self._themes = []
        for path_name in (
            'light_theme.json',
            'dark_theme.json',
            'dark_theme_win.json',
        ):
            theme_path = current_dir / path_name
            with theme_path.open('rt', encoding='utf-8') as f:
                theme = Theme.from_dict(json.load(f))
                self._themes.append(
                    (
                        theme,
                        theme_path,
                    )
                )

        self._current_theme, self._current_theme_path = self._themes[0]

        self._icons = {}

    def register_theme_meta(
        self, meta_info: tuple[str, type[BaseThemePartEditor]]
    ) -> None:
        self._theme_meta_info.append(meta_info)

    def edit_current_theme(self, parent: QWidget | None):
        edit_dialog = ThemeEditorDialog(
            theme=self._current_theme,
            theme_path=self._current_theme_path,
            theme_meta_info=self._theme_meta_info,
            parent=parent,
        )
        edit_dialog.apply_signal.connect(self._current_theme_changed)
        edit_dialog.exec()

    def _current_theme_changed(self):
        self.theme_changed.emit(self._current_theme)
        WaitingSpinner.set_parameters(
            self._current_theme.meta['waiting_spinner']
        )

    @property
    def current_theme(self) -> Theme:
        return self._current_theme

    @property
    def current_theme_path(self) -> Path | None:
        return self._current_theme_path

    @property
    def theme_names(self) -> list[str]:
        return [t.name for t, _ in self._themes]

    def add_theme(self, new_theme: Theme, theme_path: Path | None = None):
        self._themes.append(
            (
                new_theme,
                theme_path,
            )
        )

    def switch_to(self, theme_name: str):
        for t, tp in self._themes:
            if t.name == theme_name:
                self._current_theme = t
                self._current_theme_path = tp
                self.theme_changed.emit(self._current_theme)
                return
        raise KeyError

    def icon(
        self,
        icon_path: str,
        *,
        is_checkable: bool = False,
    ) -> QIcon:
        found_icon = self._icons.get((icon_path, is_checkable))
        if found_icon is not None:
            return found_icon

        icon_full_path = icon_path
        if icon_path[0] != ':':
            icon_full_path = self._assets_manager.image_path_get(icon_path)
            if icon_full_path is None:
                icon_full_path = self._not_found_icon_path

        new_icon = QIcon(
            ThemeSvgIconEngine(
                path_or_content=str(icon_full_path),
                theme=self._current_theme,
                theme_changed_signal=self.theme_changed,  # type: ignore[reportArgumentType]
                is_checkable=is_checkable,
            )
        )
        self._icons[(icon_path, is_checkable)] = new_icon
        return new_icon


if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    assets_manager = AssetsManager()
    theme_manager = ThemeManager(assets_manager)
    assert theme_manager.current_theme.name == 'Светлая'

    theme_manager.switch_to('Темная')
    assert theme_manager.current_theme.name == 'Темная'

    app = QApplication([])

    button = QPushButton('Демо')
    button.setCheckable(True)
    button.setEnabled(False)
    button.setIcon(theme_manager.icon('io/save.svg'))

    button.show()

    app.exec()
