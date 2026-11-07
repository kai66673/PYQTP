from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtGui import QIcon

if TYPE_CHECKING:
    from bcad.controls.events_control.events_models import LogLevel
    from bcad.theme.theme import Theme


class LogIconsStorage:
    _icons: ClassVar[list[QIcon]] = [
        QIcon(),
        QIcon(),
        QIcon(),
        QIcon(),
    ]

    @staticmethod
    def update(theme: Theme) -> None:
        log_icons_dir = (
            Path(__file__).parent.parent.parent / 'assets' / 'images' / 'log'
        )
        LogIconsStorage._icons = [
            QIcon(str(log_icons_dir / 'log_error.svg')),
            QIcon(str(log_icons_dir / 'log_warning.svg')),
            QIcon(str(log_icons_dir / 'log_info.svg')),
            QIcon(
                str(log_icons_dir / 'log_debug_dark.svg')
                if theme.is_dark
                else str(log_icons_dir / 'log_debug.svg')
            ),
        ]

    @staticmethod
    def icon(level: LogLevel) -> QIcon:
        return LogIconsStorage._icons[level.value]
