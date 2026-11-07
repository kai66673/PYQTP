from __future__ import annotations

import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pyqtp.base.base_table_model import BaseCheckableTableModel
from pyqtp.base.helpers import get_main_wnd_annotation
from pyqtp.plugins.base_plugin import BasePlugin
from pyqtp.utils.action import Action

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.controls.plugins_control.user_plugin_control import (
        UserPluginControl,
    )


sys_log = logging.getLogger('bcad.sys')


class PluginSelectionDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        main_wnd: BaseMainWindow,
        plug_ctrl: UserPluginControl,
    ) -> None:
        super().__init__(parent)
        self._main_wnd = main_wnd
        self._plug_ctrl = plug_ctrl

        self.setWindowTitle('Подключаемые модули')

        # Таблица настроек
        self.plugins_model = PluginsSettingsModel()
        self.plugins_model._main_wnd = main_wnd
        self.plugins_view = QTableView(self)
        self.plugins_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.plugins_view.clicked.connect(self.set_checkbox)
        self.sorted_model = QSortFilterProxyModel()
        self.sorted_model.setSortRole(Qt.ItemDataRole.UserRole)
        self.sorted_model.setSourceModel(self.plugins_model)
        self.plugins_view.setModel(self.sorted_model)
        self.plugins_view.setSortingEnabled(True)
        self.plugins_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.plugins_view.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.plugins_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.plugins_view.horizontalHeader().setSectionsMovable(True)

        # Кнопки
        self.ok_button = QPushButton('Применить')
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.try_accept)
        self.cancel_button = QPushButton('Отменить')
        self.cancel_button.clicked.connect(self.close)
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.ok_button)
        buttons_layout.addWidget(self.cancel_button)

        # Тулбар
        self.select_all_action = Action(
            icon=main_wnd.theme_manager.icon("common/check_all.svg"),
            title='Выбрать все',
            parent=self,
            description='Выбрать все',
        )
        self.select_none_action = Action(
            icon=main_wnd.theme_manager.icon("common/uncheck_all.svg"),
            title='Убрать все',
            parent=self,
            description='Убрать все',
        )
        self.select_all_action.triggered.connect(self.plugins_model.select_all)
        self.select_none_action.triggered.connect(
            self.plugins_model.select_none
        )
        self.select_default_action = Action(
            icon=main_wnd.theme_manager.icon("common/refresh.svg"),
            title='Сбросить к списку модулей по умолчанию',
            parent=self,
            description='Сброс к подключаемым модулям, загружаемым по умолчанию',
        )
        self.select_default_action.triggered.connect(self.reset)
        self.plugin_settings_reset_action = Action(
            icon=main_wnd.theme_manager.icon("common/redo.svg"),
            title='Удалить настройки выбранного модуля',
            parent=self,
            description='Удаление пользовательских настроек выбранного модуля',
        )
        self.plugin_settings_reset_action.setVisible(False)
        self.plugin_settings_reset_action.triggered.connect(self.reset_plugin)
        self.toolbar = self.create_toolbar()

        # Располагаем элементы
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.toolbar)
        main_layout.addWidget(self.plugins_view)
        main_layout.addLayout(buttons_layout)
        self.setLayout(main_layout)

        self.resize(1100, 700)
        self.refresh()
        self.plugins_view.sortByColumn(1, Qt.SortOrder.AscendingOrder)

    def set_checkbox(self, ind: QModelIndex):
        self.plugins_view.clearSelection()

        if ind.column() == 0:
            return

        source_index = self.sorted_model.mapToSource(ind)
        row = source_index.row()
        column_zero_index = self.plugins_model.index(row, 0)
        check_val = self.plugins_model.data(
            column_zero_index, Qt.ItemDataRole.CheckStateRole
        )

        checked = Qt.CheckState.Checked
        unchecked = Qt.CheckState.Unchecked
        new_val = unchecked.value if check_val == checked else checked.value

        self.plugins_model.setData(
            column_zero_index, new_val, Qt.ItemDataRole.CheckStateRole
        )

    def create_toolbar(self, parent: QWidget | None = None) -> QToolBar:
        toolbar = QToolBar(parent)
        toolbar.addAction(self.select_all_action)
        toolbar.addAction(self.select_none_action)
        toolbar.addAction(self.select_default_action)
        toolbar.addSeparator()
        toolbar.addAction(self.plugin_settings_reset_action)
        return toolbar

    def try_accept(self) -> None:
        # Выбранные плагины
        plugins = self.plugins_model.selection_data
        # Проверяем на дубликаты ключей
        keys = {p.key for p in plugins}
        if len(keys) != len(plugins):
            self._main_wnd.events_ctrl.events_show_exception_popup(
                message='Обнаружены несовместимые плагины!'
            )
            return
        try:
            self._plug_ctrl.plugins_list_set(plugins)
        except Exception as ex:  # noqa: BLE001
            self._main_wnd.events_ctrl.events_show_exception_popup(
                message='Не удалось инициализировать плагины!', exception=ex
            )
            return
        self.accept()

    def refresh(self) -> None:
        """Синхронизируем список с текущим набором загруженных плагинов."""
        all_plugins = self._plug_ctrl.plugins_import_all()
        loaded_plugins = {type(p) for p in self._main_wnd.plugins.values()}
        selected = [(p in loaded_plugins) for p in all_plugins]
        self.plugins_model.reset(all_plugins)
        self.plugins_model.select(selected)

    def reset(self) -> None:
        """Сбрасываем список плагинов на состояние по умолчанию"""
        all_plugins = self._plug_ctrl.plugins_import_all()
        default_plugins = self._plug_ctrl.plugin_list_default
        selected = [(p in default_plugins) for p in all_plugins]
        self.plugins_model.reset(all_plugins)
        self.plugins_model.select(selected)

    def reset_plugin(self) -> None:
        select = self.plugins_view.selectionModel()
        if not select.hasSelection():
            return

        proxy = self.sorted_model
        source = self.plugins_model

        # Индексация в фильтруемой модели
        result = []
        for index in select.selectedRows():
            row = index.row()
            proxy_index = self.plugins_view.model().index(row, 0)

            # Индексация в оригинальной модели
            source_index = proxy.mapToSource(proxy_index)
            result.append(source.data_storage[source_index.row()])
        plugin = result[0]()
        plugin._main_wnd = self._main_wnd
        plugin.settings_write()
        name = plugin.__doc__.split('\n', 1)[0].rstrip('.')
        sys_log.info(f'Настройки "{name}" сброшены.')


class PluginsSettingsModel(BaseCheckableTableModel[type[BasePlugin]]):
    HEADER_DATA: Mapping[str, tuple[str, str]] = MappingProxyType(
        {
            'key': (
                'Группа',
                'Группа совместимости подключаемых модулей (модули в одной группе нельзя использовать одновременно).',
            ),
            'name': ('Имя', 'Программное название подключаемого модуля.'),
            'import_path': ('Путь', 'Программный путь к плагину.'),
            'main_wnd': ('Тип', 'Тип совместимого главного окна.'),
            'docstring': ('Наименование', 'Наименование подключаемого модуля.'),
            'loaded': ('Загружен', 'Текущее состояние плагина.'),
            'checked': ('', 'Выбор плагина для загрузки'),
        }
    )
    HEADERS = ['checked', 'docstring', 'key']  # noqa: RUF012

    _data: list[type[BasePlugin]]

    #########################
    # API отображения ячеек #
    #########################

    def tooltip_at(
        self, index: QModelIndex | QPersistentModelIndex
    ) -> str | None:
        return self._data[index.row()].__doc__

    def key_value(self, index: QModelIndex) -> str:
        return self._data[index.row()].key  # type:ignore[reportReturnType]

    def import_path_value(self, index: QModelIndex) -> str:
        cls = self._data[index.row()]
        return f'{cls.__module__}.{cls.__qualname__}'

    def main_wnd_value(self, index: QModelIndex) -> str:
        cls = self._data[index.row()]
        return get_main_wnd_annotation(cls)

    def name_value(self, index: QModelIndex) -> str:
        return self._data[index.row()].__qualname__

    def docstring_value(self, index: QModelIndex) -> str:
        doc = self._data[index.row()].__doc__
        assert doc is not None
        return doc.strip().split('\n', 1)[0].rstrip('.')

    def loaded_value(self, index: QModelIndex) -> bool:
        assert self._main_wnd is not None
        plugin_cls = self._data[index.row()]
        return any(isinstance(p, plugin_cls) for p in self._main_wnd.plugins)

    def checked_value(self, index: QModelIndex) -> bool:
        return self._selection[index.row()]

    def checked_display(self, _index: QModelIndex) -> str:
        return ''

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role == Qt.ItemDataRole.FontRole and index.column() != 0:
            font = QFont()
            font.setBold(self._selection[index.row()])
            return font
        return super().data(index, role)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            row = index.row()
            self._selection[row] = (value == Qt.CheckState.Checked)

            top_left = self.createIndex(row, 0)
            bottom_right = self.createIndex(row, self.columnCount() - 1)
            self.dataChanged.emit(
                top_left,
                bottom_right,
                [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.FontRole]
            )
        return super().setData( index, value, role)
