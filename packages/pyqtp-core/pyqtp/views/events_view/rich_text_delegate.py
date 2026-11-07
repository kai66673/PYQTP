from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRectF, Qt
from PySide6.QtWidgets import (
    QItemDelegate,
    QStyle,
    QStyleOptionViewItem,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QPainter


class RichTextDelegate(QItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        role = Qt.ItemDataRole.UserRole + 1
        if option.state & QStyle.StateFlag.State_Selected:  # type:ignore[reportAttributeAccessIssue]
            painter.fillRect(option.rect, option.palette.highlight())  # type:ignore[reportAttributeAccessIssue]
            role -= 1

        document = index.data(role)
        document.setTextWidth(option.rect.width())  # type:ignore[reportAttributeAccessIssue]

        painter.save()
        painter.translate(option.rect.topLeft())  # type:ignore[reportAttributeAccessIssue]
        document.drawContents(
            painter,
            QRectF(
                0.0,
                0.0,
                float(option.rect.width()),  # type:ignore[reportAttributeAccessIssue]
                float(option.rect.height()),  # type:ignore[reportAttributeAccessIssue]
            ),
        )  # type:ignore[reportAttributeAccessIssue]
        painter.restore()
