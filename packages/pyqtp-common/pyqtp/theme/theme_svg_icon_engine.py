from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtCore import QFile, QIODeviceBase, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QIcon, QIconEngine, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

if TYPE_CHECKING:
    from pyqtp.theme.theme import Theme


def _read_file_content(file_path: str) -> bytes:
    file = QFile(file_path)
    if not file.open(QIODeviceBase.OpenModeFlag.ReadOnly):
        raise ValueError
    content = bytes(file.readAll().data())
    file.close()
    return content


class ThemeSvgIconEngine(QIconEngine):
    svg_content: bytes
    primary_color: bytes
    icon_colors: list[bytes]

    def __init__(
        self,
        *,
        path_or_content: str | bytes,
        theme: Theme,
        theme_changed_signal: Signal,
        primary_color: bytes = b'#1E1E1E',
        is_checkable: bool = False,
    ) -> None:
        super().__init__()

        self.is_checkable = is_checkable

        self.svg_content = (
            path_or_content
            if isinstance(path_or_content, bytes)
            else _read_file_content(path_or_content)
        )
        self._replacer = re.compile(re.escape(primary_color), re.IGNORECASE)

        self.icon_colors = [
            theme.icon_color_normal_checked.encode('utf-8'),
            theme.icon_color_disabled_checked.encode('utf-8'),
            theme.icon_color_active_checked.encode('utf-8'),
            theme.icon_color_selected_checked.encode('utf-8'),
            theme.icon_color_normal.encode('utf-8'),
            theme.icon_color_disabled.encode('utf-8'),
            theme.icon_color_active.encode('utf-8'),
            theme.icon_color_selected.encode('utf-8'),
        ]

        theme_changed_signal.connect(self._handle_theme_changed)  # type: ignore[reportAttributeAccessIssue]

    def _handle_theme_changed(self, theme: Theme):
        self.icon_colors = [
            theme.icon_color_normal_checked.encode('utf-8'),
            theme.icon_color_disabled_checked.encode('utf-8'),
            theme.icon_color_active_checked.encode('utf-8'),
            theme.icon_color_selected_checked.encode('utf-8'),
            theme.icon_color_normal.encode('utf-8'),
            theme.icon_color_disabled.encode('utf-8'),
            theme.icon_color_active.encode('utf-8'),
            theme.icon_color_selected.encode('utf-8'),
        ]

    def paint(self, painter, rect, mode, state):
        assert isinstance(mode, QIcon.Mode)

        icon_index = mode.value
        if self.is_checkable:
            icon_index += state.value * 4
        else:
            icon_index += 4
        color = self.icon_colors[icon_index]

        renderer = QSvgRenderer(
            self._replacer.sub(color, self.svg_content),
        )
        renderer.render(painter, rect)

    def pixmap(self, size, mode, state):
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        pixmap = QPixmap.fromImage(
            image, Qt.ImageConversionFlag.NoFormatConversion
        )
        painter = QPainter(pixmap)
        rect = QRect(QPoint(0, 0), size)
        self.paint(painter, rect, mode, state)
        return pixmap
