from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QTextDocument, QTextOption

from pyqtp.base.helpers import parse_block_uri
from pyqtp.controls.events_control.events_icons import LogIconsStorage


class LogLevel(IntEnum):
    Error = 0
    Warning = 1
    Information = 2
    Debug = 3


@dataclass
class LogFilterInfo:
    error: bool = True
    warning: bool = True
    info: bool = True
    debug: bool = True
    containing_str: str | None = None
    accepted_log_names: list[str] = field(default_factory=list)

    def is_level_enabled(self, level: LogLevel):
        return (self.error, self.warning, self.info, self.debug)[level.value]


class LogCategoriesModel(QAbstractTableModel):
    categories_visibility_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._group_names = {}
        self._groups = []

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return len(self._groups)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return 1

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            section == 0
            and orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return 'Категория'
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: ARG002
        return (
            Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
        )

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.column() != 0:
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._groups[index.row()][1]
        if role == Qt.ItemDataRole.CheckStateRole:
            return (
                Qt.CheckState.Checked
                if self._groups[index.row()][0]
                else Qt.CheckState.Unchecked
            )

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if (
            index.isValid()
            and index.column() == 0
            and role == Qt.ItemDataRole.CheckStateRole
        ):
            visible = value == Qt.CheckState.Checked.value
            self._groups[index.row()][0] = visible
            # noinspection PyUnresolvedReferences
            self.dataChanged.emit(index, index)
            # noinspection PyUnresolvedReferences
            self.categories_visibility_changed.emit()
            return True

        return False

    def add_log(self, group: str, name: str):
        if group not in self._group_names:
            row_count = len(self._groups)
            self.beginInsertRows(QModelIndex(), row_count, row_count)
            self._groups.append([True, group, {name}])
            self.endInsertRows()
            self._group_names[group] = row_count
            # noinspection PyUnresolvedReferences
            self.categories_visibility_changed.emit()
            return

        group_item = self._groups[self._group_names[group]]
        if name in group_item[2]:
            return
        group_item[2].add(name)
        if group_item[0]:
            # noinspection PyUnresolvedReferences
            self.categories_visibility_changed.emit()

    def accepted_log_names(self) -> list[str]:
        result = []
        for group_item in self._groups:
            if group_item[0]:
                result += list(group_item[2])
        return list(set(result))


class LogMessage:
    message: str
    level: LogLevel
    log_name: str
    notify: bool
    popup: bool
    lang: str | None

    def __init__(self, record: logging.LogRecord):
        self.level = self._log_level_from_int(record.levelno)
        self.log_name = record.name

        # Формируем текст сообщения
        self.message = record.getMessage()
        if record.exc_info:
            trace_msg = ''.join(traceback.format_exception(*record.exc_info))
            self.message = f'{self.message}\n{trace_msg}'

        # Опции сообщений
        self.notify = getattr(record, 'notify', False)
        self.popup = getattr(record, 'popup', False)
        self.lang = getattr(record, 'lang', None)

        # Для исключений включаем python по умолчанию
        if record.exc_info:
            self.lang = 'python'

    @staticmethod
    def _log_level_from_int(val: int) -> LogLevel:
        if val >= logging.ERROR:
            return LogLevel.Error
        if val >= logging.WARNING:
            return LogLevel.Warning
        if val >= logging.INFO:
            return LogLevel.Information
        return LogLevel.Debug


@dataclass
class _MessageData:
    message: LogMessage
    document: QTextDocument | None = None
    document_first_line: QTextDocument | None = None


class LogMessagesModel(QAbstractTableModel):
    _data: list[_MessageData]

    _rest_item_count: int = 1000
    _head_item_count: int = 50

    def __init__(self, data: list[LogMessage] | None = None, parent=None):
        super().__init__(parent)
        self._data = [_MessageData(message=m) for m in data or []]

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return len(self._data)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return 2

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return ('Ур', 'Сообщение')[section]
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: ARG002
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or index.column() < 0 or index.column() > 1:
            return None

        if role == Qt.ItemDataRole.DisplayRole and index.column() == 1:
            return self._data[index.row()].message.message

        if role == Qt.ItemDataRole.UserRole and index.column() == 1:
            data = self._data[index.row()]
            if data.document is None:
                value = data.message.message
                parsed_value = parse_block_uri(value).replace('\n', '<br>')
                data.document = QTextDocument(None)
                data.document.setHtml(parsed_value)
            return data.document

        if role == Qt.ItemDataRole.UserRole + 1 and index.column() == 1:
            data = self._data[index.row()]
            if data.document_first_line is None:
                value = data.message.message
                parsed_value = parse_block_uri(value.split('/n')[0])
                data.document_first_line = QTextDocument(None)
                if self._data[index.row()].message.level == LogLevel.Debug:
                    data.document_first_line.setHtml(
                        f'<div style="color:gray;">{parsed_value}</div>'
                    )
                else:
                    data.document_first_line.setHtml(parsed_value)
                to = QTextOption()
                to.setAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                to.setWrapMode(QTextOption.WrapMode.NoWrap)
                data.document_first_line.setDefaultTextOption(to)
            return data.document_first_line

        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0:
            return LogIconsStorage.icon(self._data[index.row()].message.level)

        return None

    def add_message(self, new_message: LogMessage) -> int:
        row_num = self.rowCount()

        self.beginInsertRows(QModelIndex(), row_num, row_num)
        self._data.append(_MessageData(message=new_message))
        self.endInsertRows()

        if row_num >= self._head_item_count + self._rest_item_count:
            self.beginRemoveRows(QModelIndex(), 0, self._head_item_count - 1)
            self._data = self._data[self._head_item_count :]
            self.endRemoveRows()
            row_num -= self._head_item_count

        return row_num

    def message(self, row_num: int) -> LogMessage:
        return self._data[row_num].message

    def clear_log(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()
