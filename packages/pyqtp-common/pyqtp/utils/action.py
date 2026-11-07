from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QIcon, QKeySequence

if TYPE_CHECKING:
    from PySide6.QtCore import QObject


class Action(QAction):
    def __init__(
        self,
        icon: QIcon,
        title: str,
        parent: QObject | None,
        description: str,
        shortcut: QKeySequence | QKeySequence.StandardKey | None = None,
    ):
        super().__init__(icon, title, parent)
        self.setShortcut(shortcut or QKeySequence())
        self.change_description(description)

    def change_description(self, description: str) -> None:
        self.setStatusTip(description)
        shortcut = self.shortcut()
        if shortcut is None:
            self.setToolTip(f'{description}')
        else:
            self.setToolTip(
                f'{description}<br/><span style="color: rgb(127, 127, 127); font-size: small">{self.shortcut().toString(QKeySequence.SequenceFormat.NativeText)}</span>'
            )
