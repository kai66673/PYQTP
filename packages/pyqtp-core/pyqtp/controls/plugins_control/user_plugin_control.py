from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtGui import QIcon
from typing_extensions import NamedTuple

from pyqtp.base.helpers import resolve_name, slot_single_shot
from pyqtp.controls.plugins_control.plugin_control import PluginControl
from pyqtp.controls.plugins_control.plugin_selection_widget import (
    PluginSelectionDialog,
)
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.plugins.base_plugin import BasePlugin


sys_log = logging.getLogger('bcad.sys')


class _Diff(NamedTuple):
    to_remove: list[type[BasePlugin]]
    to_add: list[type[BasePlugin]]


class UserPluginControl(PluginControl):
    """
    Контроллер плагинов к приложению с пользовательским доступом.

    Обеспечивает:
      - управление текущим набором плагинов к приложению.
      - загружает плагины, заданные по умолчанию
      - при старте не выгружает уже загруженные плагины
    """

    _main_wnd: BaseMainWindow

    def __init__(self) -> None:
        super().__init__()

        # Действия
        self.plugins_settings_action = Action(
            icon=QIcon(),
            title='Модули',
            parent=self,
            description='Выбор подключаемых модулей приложения',
        )
        self.plugins_settings_action.triggered.connect(
            self.show_dialog_plugins_settings
        )

        # Действия
        self.reset_settings_action = Action(
            icon=QIcon(),
            title='Сброс настроек',
            parent=self,
            description='Сброс настроек',
        )
        self.reset_settings_action.triggered.connect(self.reset_settings)

        # Список плагинов по умолчанию
        self._default: list[type[BasePlugin]] = []
        # Патч к списку плагинов по умолчанию
        self._diff: _Diff = _Diff([], [])

    def settings_read(self, **kwargs: Any) -> None:
        super().settings_read(**kwargs)

        # Читаем из настроек списки плагинов для загрузки и выгрузки
        settings = self._main_wnd.settings
        try:
            self._diff = _Diff(
                to_remove=[
                    cast('type[BasePlugin]', resolve_name(cls))
                    for cls in json.loads(
                        cast('str', settings.value('plugins_unload', '[]', str))
                    )
                ],
                to_add=[
                    cast('type[BasePlugin]', resolve_name(cls))
                    for cls in json.loads(
                        cast('str', settings.value('plugins_load', '[]', str))
                    )
                ],
            )
        except (ImportError, AttributeError):
            self._diff = _Diff([], [])
            slot_single_shot(
                sys_log.error,
                self._main_wnd.ctrls_ready_signal,
                'Unable to import plugins in settings! '
                'Using default plugins set.',
            )

    def settings_write(self) -> None:
        super().settings_write()

        # Пишем в настройки списки плагинов для загрузки и выгрузки
        settings = self._main_wnd.settings
        settings.setValue(
            'plugins_unload',
            json.dumps(
                [
                    f'{cls.__module__}.{cls.__qualname__}'
                    for cls in self._diff.to_remove
                ]
            ),
        )
        settings.setValue(
            'plugins_load',
            json.dumps(
                [
                    f'{cls.__module__}.{cls.__qualname__}'
                    for cls in self._diff.to_add
                ]
            ),
        )

    def setup_relations(self) -> None:
        super().setup_relations()

        self.plugins_settings_action.setIcon(
            self._main_wnd.theme_manager.icon('modules.svg')
        )

        self.reset_settings_action.setIcon(
            self._main_wnd.theme_manager.icon('common/refresh.svg')
        )

        # Регистрируем журнал плагинов
        self._main_wnd.events_ctrl.events_log_register(
            name=sys_log.name, group='Система'
        )

        # При старте загружаем плагины
        self._main_wnd.ctrls_ready_signal.connect(self._plugins_sync)

    ####################################################
    # API управления пользовательским набором плагинов #
    ####################################################

    @property
    def plugin_list_default(self) -> list[type[BasePlugin]]:
        """Список плагинов, загружаемых по умолчанию."""
        return self._default

    @contextmanager
    def default_ctx(self) -> Iterator[None]:
        """
        Контекстный менеджер, который позволяет регистрировать все
        плагины как плагины по умолчанию через стандартное API главного окна.
        """
        orig_plug_register = self._main_wnd.plug_register

        def wrapper(plugin: BasePlugin) -> BasePlugin:
            if plugin not in self._default:
                self._default.append(type(plugin))
            # Проверяем что плагин надо загрузить
            if type(plugin) in self._diff.to_remove:
                return plugin
            # Загружаем плагин
            return orig_plug_register(plugin)

        self._main_wnd.plug_register = wrapper
        try:
            yield
        finally:
            self._main_wnd.plug_register = orig_plug_register

    def plugins_register_current_as_default(self) -> None:
        """Регистрирует уже загруженные плагины как плагины по умолчанию."""
        for plugin in self._main_wnd.plugins.values():
            if type(plugin) not in self._default:
                self._default.append(type(plugin))

    def plugin_register_default(self, plugin: BasePlugin) -> BasePlugin | None:
        """Регистрация плагина для загрузки по умолчанию."""
        # Добавляем в список плагинов по умолчанию
        if type(plugin) not in self._default:
            self._default.append(type(plugin))
        # Проверяем что плагин надо загрузить
        if type(plugin) in self._diff.to_remove:
            return None
        # Загружаем плагин
        return self._main_wnd.plug_register(plugin)

    def plugins_list_reset(self) -> None:
        """Сброс к списку плагинов по умолчанию."""
        self._diff = _Diff([], [])
        self._plugins_sync()

    def plugins_list_set(self, plugins: list[type[BasePlugin]]) -> None:
        """Задание списка плагинов."""
        new, default = set(plugins), set(self._default)
        self._diff = _Diff(
            to_remove=list(default - new), to_add=list(new - default)
        )
        self._plugins_sync()

    def _plugins_sync(self) -> None:
        """Синхронизация текущих плагинов с заданным списком."""
        current_plugins = {type(p) for p in self._main_wnd.plugins.values()}
        selected_plugins = set(self._default) | set(self._diff.to_add)
        selected_plugins -= set(self._diff.to_remove)

        def extract_name(kls: type) -> str:
            doc = kls.__doc__
            assert doc is not None
            return doc.strip().split('\n', 1)[0].rstrip('.')

        # Выгружаем лишние плагины
        for cls in current_plugins - selected_plugins:
            self._main_wnd.plug_deregister(cls.key)  # type:ignore[reportArgumentType]
            sys_log.info(f'{extract_name(cls)} выгружен.')

        # Загружаем новые плагины
        for cls in selected_plugins - current_plugins:
            self._main_wnd.plug_register(cls())
            sys_log.info(f'{extract_name(cls)} загружен.')

    #####################
    # Интерактивное API #
    #####################

    def show_dialog_plugins_settings(self) -> None:
        dialog = PluginSelectionDialog(
            self._main_wnd, main_wnd=self._main_wnd, plug_ctrl=self
        )
        dialog.exec()
        dialog.setParent(None)

    def reset_settings(self) -> None:
        """Сброс настроек."""
        self._main_wnd.reset_settings()

    ################
    # API виджетов #
    ################

    def setup_menu(self) -> None:
        super().setup_menu()
        menubar = self._main_wnd.menuBar()
        try:
            settings_menu = menubar.by_path('Настройки')
        except KeyError:
            settings_menu = menubar.addMenu('Настройки', priority=9)

        assert isinstance(settings_menu, PriorityMenu)
        settings_menu.addAction(self.plugins_settings_action, priority=100)
        settings_menu.addAction(self.reset_settings_action, priority=101)

    def teardown_menu(self) -> None:
        super().teardown_menu()
        settings_menu = self._main_wnd.menuBar().by_path('Настройки')
        assert isinstance(settings_menu, PriorityMenu)
        settings_menu.removeAction(self.plugins_settings_action)
