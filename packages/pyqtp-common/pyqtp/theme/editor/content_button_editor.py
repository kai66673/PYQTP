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
from PySide6.QtGui import QColor, QFontDatabase, QMouseEvent
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QItemDelegate,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pyqtp.theme.editor.base_theme_part_editor import BaseThemePartEditor

if TYPE_CHECKING:
    from pyqtp.theme.theme import Theme


class ContentButtonModel(QAbstractTableModel):
    def __init__(self, theme: Theme, parent: QObject | None = None):
        super().__init__(parent)
        self.theme = theme
        self._data = deepcopy(theme.meta['content_button'])

    def reset(self, theme_list: list[list] | None):
        self.beginResetModel()
        self._data = deepcopy(theme_list or [])
        self.endResetModel()

    def get_data(self) -> list | None:
        return self._data

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()):  # noqa: ARG002, B008
        return len(self._data)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()):  # noqa: ARG002, B008
        return 2

    def headerData(
        self, section, orientation, role: int =Qt.ItemDataRole.DisplayRole
    ):
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return (
                'Параметр',
                'Значение',
            )[section]
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        item = self._data[index.row()]

        if index.column() == 1 and not isinstance(item, str):
            return (
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )

        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid():
            return None

        item = self._data[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return item[0]
            return item[1]

        if (
            role == Qt.ItemDataRole.BackgroundRole
            and index.column() == 1
            and isinstance(item[1], str)
        ):
            return QColor(item[1])

        if (
            role == Qt.ItemDataRole.ForegroundRole
            and index.column() == 1
            and isinstance(item[1], str)
        ):
            fill_color = QColor(item[1])
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
        if index.isValid() and index.column() == 1:
            self._data[index.row()][index.column()] = value
            self.dataChanged.emit(index, index)
            return True
        return False

    def apply(self):
        self.theme.meta['content_button'] = self._data


class ContentButtonView(QTableView):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)

        index = self.indexAt(event.pos())
        item = self.model().data(index, Qt.ItemDataRole.DisplayRole)

        is_color_data = (
            index.isValid() and index.column() == 1,
            isinstance(item, str) and item.startswith('#'),
        )

        if all(is_color_data):
            new_color = QColorDialog.getColor(
                initial=QColor(self.model().data(index)), parent=self
            )
            if new_color.isValid():
                self.model().setData(index, new_color.name())


class ContentButtonFloatValueField(QDoubleSpinBox):
    def __init__(self, decimals: int, parent: QWidget | None):
        super().__init__(parent)
        self.setMaximum(5.0)
        self.setMinimum(0.1)
        self.setDecimals(decimals)


class ContentButtonIntValueField(QSpinBox):
    def __init__(self, parent: QWidget | None):
        super().__init__(parent)
        self.setMaximum(200)
        self.setMinimum(1)


class ContentButtonFontValueField(QComboBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.addItems(QFontDatabase.families())

    def set_value(self, val: str):
        index = self.findText(val)
        if index >= 0:
            self.setCurrentIndex(index)


class ContentButtonDelegate(QItemDelegate):
    """Делегат для управления виджетами редактирования ячеек таблицы"""

    def createEditor(
        self, parent, option, index
    ) -> ContentButtonIntValueField | ContentButtonFloatValueField | QWidget:
        value = index.model().data(index)
        if isinstance(value, float):
            return ContentButtonFloatValueField(2, parent)
        if isinstance(value, int):
            return ContentButtonIntValueField(parent)
        if isinstance(value, str) and not value.startswith('#'):
            return ContentButtonFontValueField(parent)
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        value = index.model().data(index)
        if isinstance(
            editor, ContentButtonIntValueField | ContentButtonFloatValueField
        ):
            editor.setValue(value)
        if isinstance(editor, ContentButtonFontValueField):
            editor.set_value(value)

    def setModelData(self, editor, model, index):
        if isinstance(
            editor, ContentButtonIntValueField | ContentButtonFloatValueField
        ):
            model.setData(index, editor.value())
        if isinstance(editor, ContentButtonFontValueField):
            model.setData(index, editor.currentText())

    def updateEditorGeometry(self, editor, option, index):  # noqa: ARG002
        editor.setGeometry(option.rect)  # type:ignore[reportAttributeAccessIssue]


class ContentButtonEditor(BaseThemePartEditor):
    def __init__(self, theme: Theme, parent: QWidget | None = None):
        super().__init__(theme, parent)

        self.main_layout = QVBoxLayout(self)
        self.setContentsMargins(0, 0, 0, 0)

        self.spinner_view = ContentButtonView()
        self.spinner_model = ContentButtonModel(theme)
        self.spinner_view.setModel(self.spinner_model)
        self.spinner_view.setItemDelegateForColumn(1, ContentButtonDelegate())

        self.main_layout.addWidget(self.spinner_view)

    def apply(self):
        self.spinner_model.apply()
