from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import QColorDialog, QTableView, QVBoxLayout, QWidget

from pyqtp.theme.editor.base_theme_part_editor import BaseThemePartEditor

if TYPE_CHECKING:
    from pyqtp.theme.theme import Theme


class PaletteModel(QAbstractTableModel):
    def __init__(self, theme: Theme, parent: QObject | None = None):
        super().__init__(parent)
        self.theme = theme
        self._data = deepcopy(theme.palette)

    def reset(self, theme_lst: list[list[str]] | None):
        self.beginResetModel()
        self._data = deepcopy(theme_lst or [])
        self.endResetModel()

    def get_data(self) -> list[list[str]] | None:
        return self._data

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()):  # noqa: ARG002, B008
        return len(self._data)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()):  # noqa: ARG002, B008
        return 4

    def headerData(
        self, section, orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return ('Роль', 'Active', 'Disabled', 'Inactive')[section]
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,  # noqa: ARG002
    ) -> Qt.ItemFlag:
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return QPalette.ColorRole(index.row()).name
            return self._data[index.row()][index.column() - 1]

        if role == Qt.ItemDataRole.BackgroundRole and index.column() > 0:
            return QColor(self._data[index.row()][index.column() - 1])

        if role == Qt.ItemDataRole.ForegroundRole and index.column() > 0:
            fill_color = QColor(self._data[index.row()][index.column() - 1])
            bright = (
                fill_color.redF() + fill_color.greenF() + fill_color.blueF()
            ) / 3.0
            bright_constant = 0.5
            return (
                QColor('#000000')
                if bright > bright_constant
                else QColor('#FFFFFF')
            )

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,  # noqa: ARG002
    ) -> bool:
        if index.isValid() and index.column() > 0:
            self._data[index.row()][index.column() - 1] = value
            self.dataChanged.emit(index, index)
            return True
        return False

    def apply(self):
        self.theme.palette = self._data


class PaletteView(QTableView):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)

        index = self.indexAt(event.pos())
        if index.isValid() and index.column() > 0:
            new_color = QColorDialog.getColor(
                initial=QColor(self.model().data(index)), parent=self
            )
            if new_color.isValid():
                self.model().setData(index, new_color.name())


class PaletteEditor(BaseThemePartEditor):
    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(theme, parent)

        self.main_layout = QVBoxLayout(self)
        self.setContentsMargins(0, 0, 0, 0)

        self.palette_view = PaletteView()
        self.palette_model = PaletteModel(theme)
        self.palette_view.setModel(self.palette_model)

        self.main_layout.addWidget(self.palette_view)

    def apply(self):
        self.palette_model.apply()
