from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from PySide6.QtCore import QUrl


class BaseGroupWidget(QWidget):
    @property
    def group_id(self) -> str:
        raise NotImplementedError

    @property
    def help_context(self) -> str | QUrl:
        return f'<h1>Справка для группы представлений (group_id={self.group_id}) не реализована!</h1>'
