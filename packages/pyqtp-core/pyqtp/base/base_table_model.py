from __future__ import annotations

import logging
from contextlib import contextmanager
from types import CodeType, MappingProxyType
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
    cast,
)

from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtWidgets import QTableView

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

T = TypeVar('T')

sim_log = logging.getLogger('bcad.sim')


class DeclarativeTableModel(Generic[T], QAbstractTableModel):  # noqa: PYI059
    """
    Обертка над стандартной табличной моделью Qt.

    В качестве хранилища данных использует список объектов.

    Позволяет декларативно задавать способ получения значений колонок и их
    способ отображения.
    """

    HEADER_DATA: Mapping[str, str] = MappingProxyType({})
    HEADERS: list[str] = []  # noqa: RUF012

    _value_handlers: dict[
        str, Callable[[QModelIndex | QPersistentModelIndex], Any]
    ]
    _display_handlers: dict[
        str, Callable[[QModelIndex | QPersistentModelIndex], Any]
    ]
    _edit_handlers: dict[
        str, Callable[[QModelIndex | QPersistentModelIndex, Any], None]
    ]

    _data: Sequence[T]
    _main_wnd: PluggableMainWindow | None = None

    #########################
    # API отображения ячеек #
    #########################

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        data: Sequence[T] | None = None,
        main_wnd: PluggableMainWindow | None = None,
    ) -> None:
        super().__init__(parent=parent)

        # Кэшируем обработчики
        self._process_handlers()

        self._main_wnd = main_wnd
        self.on_reset(data if data is not None else [])

    def value_at(self, index: QModelIndex | QPersistentModelIndex) -> Any:
        """
        Возвращает данные на пересечении строки и колонки.

        Позволяет использовать методы вида {name}_value для реализации
        логики отображения значений ячеек, где name - имя в self.HEADERS
        """
        key: str = self.HEADERS[index.column()]
        if handler := self._value_handlers.get(key):
            return handler(index)

        msg = f'No value getter defined for "{key}"!'
        raise ValueError(msg)

    def display_at(
        self, index: QModelIndex | QPersistentModelIndex
    ) -> str | None:
        """
        Логика отображения значения.

        Позволяет использовать методы вида {name}_display для реализации
        логики отображения значений ячеек, где name - имя в self.HEADERS

        Может возвращать либо строку, либо кортеж (строка, цвет)
        """
        key: str = self.HEADERS[index.column()]
        if handler := self._display_handlers.get(key):
            return handler(index)

        return self.value_at(index)

    def edit_at(
        self, index: QModelIndex | QPersistentModelIndex, value: Any
    ) -> None:
        """
        Логика редактирования значения.

        Позволяет использовать методы вида {name}_edit для реализации
        логики отображения значений ячеек, где name - имя в self.HEADERS
        """
        key: str = self.HEADERS[index.column()]
        if handler := self._edit_handlers.get(key):
            handler(index, value)
        else:
            msg = f'No value setter defined for "{key}"'
            raise TypeError(msg)

    def tooltip_at(
        self, index: QModelIndex | QPersistentModelIndex
    ) -> str | None:
        """
        Логика отображения тултипа.

        Возвращает отображаемое значение.
        """
        return self.display_at(index)

    @staticmethod
    def _extract_from_tuple(
        result: Any, index: int, *, default: bool = False
    ) -> Any:
        """
        Извлекает {index} из {result}, если это кортеж, иначе
        возвращает {default} | None
        """
        if isinstance(result, tuple):
            return result[index]

        if default:
            return result

        return None

    def _process_handlers(self) -> None:
        """Кэширует обработчики методов получения и отображения значений."""
        self._value_handlers, self._edit_handlers = {}, {}
        self._display_handlers = {}
        for column in self.HEADER_DATA:
            # Методы отображения
            if disp_method := getattr(self, f'{column}_display', None):
                self._display_handlers[column] = disp_method
            # Методы значения
            if value_method := getattr(self, f'{column}_value', None):
                self._value_handlers[column] = value_method
            # Методы записи
            if edit_method := getattr(self, f'{column}_edit', None):
                self._edit_handlers[column] = edit_method

    #################
    # API модели Qt #
    #################

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return self._extract_from_tuple(
                self.HEADER_DATA[self.HEADERS[section]], 0, default=True
            )

        if role == Qt.ItemDataRole.ToolTipRole:
            return self._extract_from_tuple(
                self.HEADER_DATA[self.HEADERS[section]], 1
            )

        return None

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return len(self._data)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = QModelIndex(),  # noqa: ARG002, B008
    ) -> int:
        return len(self.HEADERS)

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: ARG002
        # TODO: наверное тоже стоит ввести свой хэндлер
        return Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        # Текстовый вид для отображения значения
        if role == Qt.ItemDataRole.DisplayRole:
            display_value = self._extract_from_tuple(
                self.display_at(index), 0, default=True
            )
            return '' if display_value is None else display_value

        # Текстовый вид для редактирования значения
        if role == Qt.ItemDataRole.EditRole:
            edit_value = self.value_at(index)
            return '' if edit_value is None else str(edit_value)
        if role == Qt.ItemDataRole.UserRole:
            return self.value_at(index)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.tooltip_at(index)

        # Цвет текста
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._extract_from_tuple(self.display_at(index), 1)

        return None

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False

        # Вызываем метод записи значения
        self.edit_at(index, value)
        self.dataChanged.emit(index, index)
        return True

    ###########################################
    # Контекстные менеджеры обновления данных #
    ###########################################

    @contextmanager
    def row_change_ctx(self, row: int) -> Iterator[None]:
        try:
            yield
        finally:
            self.dataChanged.emit(
                self.createIndex(row, 0),
                self.createIndex(row, self.columnCount() - 1),
            )

    @contextmanager
    def reset_ctx(self: QAbstractItemModel) -> Iterator[None]:
        self.beginResetModel()
        try:
            yield
        finally:
            self.endResetModel()

    @contextmanager
    def row_append_ctx(self: QAbstractItemModel) -> Iterator[None]:
        self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
        try:
            yield
        finally:
            self.endInsertRows()

    @contextmanager
    def row_delete_ctx(self: QAbstractItemModel, row: int) -> Iterator[None]:
        self.beginRemoveRows(QModelIndex(), row, row)
        try:
            yield
        finally:
            self.endRemoveRows()

    ##################################
    # Кастомное API табличной модели #
    ##################################

    @property
    def data_storage(self) -> Sequence[T]:
        return self._data

    def on_reset(self, data: Sequence[T]) -> None:
        self._data = data

    @Slot(list)
    @Slot(object)
    def reset(self, data: Sequence[T] | None = None) -> None:
        self.beginResetModel()
        try:
            self.on_reset(data if data is not None else [])
        finally:
            self.endResetModel()


class BaseTableModel(DeclarativeTableModel[T]):
    """
    Декларативная компилируемый табличная модель Qt.

    Позволяет декларативно задавать способ получения значений колонок и их
    способ отображения с возможностью использования компилируемых
    декларативных выражений, например 'record.name' и record['name'].

    В декларативных методах недопустимые символы заменяются на "_".
    """

    _compiled_value_handlers: dict[str, CodeType]
    _compiled_edit_handlers: dict[str, CodeType]

    #########################
    # API отображения ячеек #
    #########################

    def value_at(self, index: QModelIndex | QPersistentModelIndex) -> Any:
        """
        Возвращает данные на пересечении строки и колонки.

        Осуществляет eval ключа атрибута в виде "record.{key}" для получения
        значений.
        """
        try:
            return super().value_at(index)
        except ValueError:
            key: str = self.HEADERS[index.column()]
            if handler := self._compiled_value_handlers.get(key):
                return eval(handler, {'record': self._data[index.row()]})  # noqa: S307
            raise

    def edit_at(
        self, index: QModelIndex | QPersistentModelIndex, value: Any
    ) -> None:
        """
        Логика редактирования значения.

        Осуществляет eval ключа атрибута в виде "record.{key}" для получения
        значений.
        """
        try:
            super().edit_at(index, value)
        except TypeError:
            key: str = self.HEADERS[index.column()]
            if handler := self._compiled_edit_handlers.get(key):
                exec(  # noqa: S102
                    handler,
                    {
                        'record': self._data[index.row()],
                        'value': value,
                    },
                )
            else:
                msg = f'No value setter defined for "{key}"'
                raise TypeError(msg) from None

    #################
    # Кастомное API #
    #################

    def _process_handlers(self) -> None:
        """Кэширует обработчики методов получения и отображения значений."""
        super()._process_handlers()

        self._compiled_value_handlers, self._compiled_edit_handlers = {}, {}
        for column in self.HEADER_DATA:
            # Компилируемые методы
            if not column.startswith('_'):  # пропускаем приватные колонки
                self._compiled_value_handlers[column] = compile(
                    f'record.{column}', column, 'eval'
                )
                self._compiled_edit_handlers[column] = compile(
                    f'record.{column} = value', column, 'exec'
                )


class BaseCheckableTableModel(BaseTableModel[T]):
    """
    Миксин для поддержки выделения в таблице.

    Первый элемент в таблице становится checkable.
    """

    #################
    # API модели Qt #
    #################

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)

        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            index.isValid()
            and index.column() == 0
            and role == Qt.ItemDataRole.CheckStateRole
        ):
            return (
                Qt.CheckState.Checked
                if self._selection[index.row()]
                else Qt.CheckState.Unchecked
            )
        return super().data(index, role)

    def setData(
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        super().setData(index, value, role)

        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            self._selection[index.row()] = value == Qt.CheckState.Checked.value
            self.dataChanged.emit(index, index)
            self.selection_signal.emit(self._selection)
            return True

        return False

    ##########################
    # API выделения объектов #
    ##########################

    selection_signal = Signal(list)

    @property
    def selection_data(self) -> list[T]:
        return [
            self._data[i]
            for i, selected in enumerate(self._selection)
            if selected
        ]

    @property
    def selection(self) -> list[bool]:
        return list(self._selection)

    def select(self, selection: list[bool]) -> None:
        assert len(selection) == len(self._data)
        self.beginResetModel()
        self._selection = selection
        self.endResetModel()
        self.selection_signal.emit(self._selection)

    def select_all(self) -> None:
        self.beginResetModel()
        self._selection = [True] * len(self._data)
        self.endResetModel()
        self.selection_signal.emit(self._selection)

    def select_none(self) -> None:
        self.beginResetModel()
        self._selection = [False] * len(self._data)
        self.endResetModel()
        self.selection_signal.emit(self._selection)

    def on_reset(self, data: Sequence[T]) -> None:
        self._selection = [False] * len(data)
        super().on_reset(data)
        self.selection_signal.emit(self._selection)

    def get_selected_with_proxy(self, table_view: QTableView) -> list[T]:
        """Извлекает выбранные данные из таблицы с прокси."""
        select = table_view.selectionModel()
        if not select.hasSelection():
            return []

        proxy = table_view.model()
        assert isinstance(proxy, QSortFilterProxyModel)
        source = cast('BaseCheckableTableModel[T]', proxy.sourceModel())
        assert source is self

        # Индексация в фильтруемой модели
        result = []
        for index in select.selectedRows():
            row = index.row()
            proxy_index = table_view.model().index(row, 0)

            # Индексация в оригинальной модели
            source_index = proxy.mapToSource(proxy_index)
            result.append(source.data_storage[source_index.row()])
        return result


if __name__ == '__main__':
    import sys

    from PySide6.QtWidgets import QApplication

    class MyModel(BaseTableModel[dict[str, str]]):
        HEADER_DATA = MappingProxyType(
            {'foo': 'foo header', 'bar': 'bar_header'}
        )
        HEADERS = ['bar', 'foo']  # noqa: RUF012

        def foo_display(self, index: QModelIndex) -> str:
            return self._data[index.row()]['foo']

        def bar_display(self, index: QModelIndex) -> str:
            return self._data[index.row()]['bar'] + 'baz'

    app = QApplication()
    model1 = MyModel(data=[{'foo': '1', 'bar': '2'}, {'foo': '3', 'bar': '4'}])
    view1 = QTableView(parent=None)
    view1.setModel(model1)
    view1.show()

    sys.exit(app.exec_())
