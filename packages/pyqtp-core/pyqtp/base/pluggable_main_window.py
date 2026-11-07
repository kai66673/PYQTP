from __future__ import annotations

import argparse
import inspect
import logging
from contextlib import contextmanager
from functools import lru_cache
from itertools import chain
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Literal,
    TypeVar,
    cast,
)

from PySide6.QtCore import QByteArray, QSettings, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplashScreen,
    QWidget,
)

from pyqtp.base.docking_system.base_views_manager import (
    ViewsManagerType,
    create_views_manager,
)
from pyqtp.base.help_menu_context import HelpMenuContext
from pyqtp.base.helpers import get_main_wnd_annotation, setup_logging
from pyqtp.base.pluggable import PluggableObject
from pyqtp.controls.base_control import BaseControl
from pyqtp.plugins.base_plugin import BasePlugin
from pyqtp.theme.theme_main_window import ThemeMainWindow
from pyqtp.views.base_view import BaseView

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyqtp.base.docking_system.base_dock_widget import BaseDockWidget
    from pyqtp.base.docking_system.base_views_manager import BaseViewsManager
    from pyqtp.utils.priority_menu import PriorityMenuBar


PluggableObjectT = TypeVar('PluggableObjectT', bound=PluggableObject)

dev_log = logging.getLogger('bcad.dev')
project_log = logging.getLogger('bcad.project')


class PluggableMainWindow(ThemeMainWindow):
    """Базовый класс расширяемого приложения."""

    views_manager: BaseViewsManager

    help_requested = Signal(object)

    @staticmethod
    def dbus_iface_name() -> str:
        raise NotImplementedError

    @staticmethod
    def dbus_object_name() -> str:
        raise NotImplementedError

    @property
    def key(self) -> str:
        return self.__class__.__qualname__

    def init_dock_system(self, dock_system_type: ViewsManagerType):
        self.views_manager = create_views_manager(self, dock_system_type)
        self.theme_manager.theme_changed.connect(self.views_manager.set_theme)

    #################################
    # DBUS methods
    #################################

    @Slot()
    def activate(self):
        old_state = self.windowState()
        self.setWindowState(
            old_state & ~Qt.WindowState.WindowMinimized
            | Qt.WindowState.WindowActive
        )
        self.activateWindow()
        self.raise_()

    @Slot(result=bool)
    def activate_and_close(self):
        self.activate()
        self.close()

    def __init__(
        self,
        *,
        org_name: str,
        app_name: str,
        dock_system_type: ViewsManagerType = ViewsManagerType.kdd,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle('БАЗА')
        self.setWindowIcon(
            self.theme_manager.icon('abstract_app.svg')
        )

        # Настройки
        self.org_name = org_name
        self.app_name = app_name
        self.settings = QSettings(org_name, app_name)
        if not self.settings.allKeys():
            self.set_default_settings()
        self.dock_system_type = dock_system_type
        self.splash = None

        # Инициализируем стыкуемые панели
        self.init_dock_system(self.dock_system_type)

        # Состояние интерфейса
        self._setup_ui_done = False

        # Плагины
        self._plugs = {}
        self.init_controls()

        # Панель статуса
        self.statusBar().show()

    @property
    def base_window_title(self) -> str:
        return self.windowTitle()

    #########################################
    # API формирования меню справки
    #########################################

    def create_help_menu_context(self) -> HelpMenuContext:
        pdf_path = self.assets_manager.doc_path_get('OM_base.pdf')
        return HelpMenuContext(
            docs={
                'Руководство оператора': Path(pdf_path),
            } if pdf_path is not None else {},
            about_source='ПКт ... ✨ ПАК ОРТ БАС',
        )

    #########################################
    # API манипуляций с состоянием сценария #
    #########################################

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}()'

    def menuBar(self) -> PriorityMenuBar:  # noqa: N802
        return cast('PriorityMenuBar', super().menuBar())

    #############################
    # API стартового сплэш окна #
    #############################

    splash: QSplashScreen | None

    @contextmanager
    def with_splash(self, *, image: QIcon | None = None) -> Iterator[None]:
        if image is None:
            image = self.windowIcon()
        self.splash = QSplashScreen(image.pixmap(256, 256))
        self.splash.show()

        try:
            yield
        finally:
            self.splash.finish(self)
            self.splash = None

    def show_splash_message(self, message: str) -> None:
        if self.splash is None:
            return

        self.splash.raise_()
        self.splash.showMessage(
            message, Qt.AlignmentFlag.AlignBottom, Qt.GlobalColor.white
        )
        QApplication.instance().processEvents()  # type: ignore[reportOptionalMemberAccess]

    #####################
    # API инициализации #
    #####################

    ctrls_init_signal = Signal()
    ctrls_setup_signal = Signal()
    ctrls_ready_signal = Signal()

    def init_controls(self) -> None:
        """Инициализация контролов."""

    def setup(self) -> None:
        """Подготовка подключаемых модулей к работе."""
        dev_log.debug(f'{self.key}::setup')
        # Сообщаем, что контролы инстанциированы
        self.ctrls_init_signal.emit()
        # Сообщаем, что контролы запускаются
        self.ctrls_setup_signal.emit()
        # Сообщаем, что контролы готовы
        self.ctrls_ready_signal.emit()

    setup_ui_signal = Signal()

    @property
    def setup_ui_done(self) -> bool:
        return self._setup_ui_done

    def setup_ui(self) -> None:
        """
        Инициализация графической части приложения.

        В общем случае, является опциональной.
        """
        dev_log.debug(f'{self.key}::setup_ui')

        # Сообщаем что интерфейс инициализируется
        self.setup_ui_signal.emit()
        self._setup_ui_done = True

        # Загружаем состояние главного окна
        self.state_read()

    #####################
    # API закрытия окна #
    #####################

    close_signal = Signal(QMainWindow)

    def complete_pending_transactions(self) -> None:
        for plugin in reversed(self._plugs.values()):
            plugin.complete_pending_transactions()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Сохраняем настройки
        self.settings_write()
        self.state_write()

        # Спрашиваем подключаемые объекты о возможности выхода
        for plug in reversed(self._plugs.values()):
            if not plug.on_shutdown():
                event.ignore()
                return

        # Завершаем работу подключаемых объектов
        for plug in reversed(self._plugs.values()):
            plug.deregister(self)

        # Сообщаем о завершении проложения
        self.close_signal.emit(self)
        event.accept()

    ##########################
    # API регистрации вьюшек #
    ##########################

    view_register_signal = Signal(BaseView)
    view_deregister_signal = Signal(BaseView)

    def view_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal[
            'floating', 'top', 'bottom', 'left', 'right', 'center'
        ]
        | None = None,
        add_toggle_action_into_toolbar: bool = True,
        delete_on_close: bool = False,
        group_id: str | None = None,
    ) -> BaseDockWidget:
        """Регистрирует и оборачивает вьюшку в доквиджет."""
        dock = self.views_manager.view_register(
            view,
            widget_area=widget_area,
            add_toggle_action_into_toolbar=add_toggle_action_into_toolbar,
            delete_on_close=delete_on_close,
            group_id=group_id,
        )
        self.view_register_signal.emit(view)
        return dock

    def view_deregister(self, key: str, group_id: str | None = None) -> None:
        view = self.views_manager.view_deregister(key, group_id)
        self.view_deregister_signal.emit(view)

    def view_get(self, key: str, group_id: str | None = None) -> BaseView:
        return self.views_manager.view_get(key, group_id)

    def view_close(self, key: str, group_id: str | None = None) -> None:
        self.views_manager.view_close(key, group_id)

    def view_show(self, key: str, group_id: str | None = None) -> None:
        self.views_manager.view_show(key, group_id)

    def view_hide(self, key: str, group_id: str | None = None) -> None:
        self.views_manager.view_hide(key, group_id)

    def show_central_widget(self):
        self.views_manager.show_central_widget()

    def view_central_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal["floating", "top", "bottom", "left", "right", "center"]
        | None = None,
        close_cb=None,
    ) -> BaseDockWidget | None:
        return self.views_manager.view_central_register(
            view,
            widget_area=widget_area,
            close_cb=close_cb,
        )
    ##############################################
    # Общее API регистрации подключаемых модулей #
    ##############################################

    _plugs: dict[str, PluggableObject]

    plug_register_signal = Signal(str, PluggableObject)
    plug_deregister_signal = Signal(str, PluggableObject)

    def plug_register(self, plugin: PluggableObjectT) -> PluggableObjectT:
        """Регистрирует подключаемый объект."""
        if plugin.key in self._plugs:
            error_message = f'plugin "{plugin.key}" already registered!'
            raise ValueError(error_message)
        self._plugs[plugin.key] = plugin
        plugin.setParent(self)
        plugin.register(self)
        self.plug_register_signal.emit(plugin.key, plugin)
        return plugin

    def plug_deregister(self, key: str) -> None:
        """Дерегистрирует подключаемый объект."""
        plugin = self._plugs.pop(key)
        plugin.deregister(self)
        self.plug_deregister_signal.emit(plugin.key, plugin)
        plugin.setParent(None)

    @lru_cache  # noqa: B019
    def plug_compatibility_check(self, cls: type) -> bool:
        """
        Проверка подключаемого объекта на совместимость.

        Чтобы объект считался совместимым, необходимо чтобы аннотация
        на уровне класса, для атрибута _main_wnd, являлась подклассом текущего
        главного окна.
        FIXME: Из-за проблем с отложенной обработкой типов и двойного импорта
               сделана в виде страшного костыля.
        """
        mro_names = [c.__qualname__ for c in type(self).__mro__]
        cls_annotation = get_main_wnd_annotation(cls)
        return cls_annotation in mro_names

    ############################
    # API контролов и плагинов #
    ############################

    @property
    def controls(self) -> dict[str, BaseControl]:
        """Не выгружаемые в динамике подключаемые модули."""
        return {
            k: v for k, v in self._plugs.items() if isinstance(v, BaseControl)
        }

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        """Выгружаемые подключаемые модули."""
        return {
            k: v for k, v in self._plugs.items() if isinstance(v, BasePlugin)
        }

    ###########################
    # API настроек приложения #
    ###########################

    settings: QSettings

    # noinspection PyMethodMayBeStatic
    def settings_argparser(self) -> argparse.ArgumentParser:
        """Формирует парсер аргументов командной строки приложения."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            '-d',
            dest='debug_loggers',
            default=[],
            nargs=1,
            action='append',
            metavar='logger_name',
            help=argparse.SUPPRESS,
        )
        parser.add_argument(
            '--reset-settings',
            action='store_true',
            help='cброс настроек приложения',
        )
        return parser

    def settings_parse_argv(self, argv: list[str]) -> argparse.Namespace:
        """Парсит аргументы командной строки."""
        # Формируем парсер на основе парсера главного окна и контролов
        parser = self.settings_argparser()
        for ctrl in self.controls.values():
            ctrl.settings_setup_argparser(parser)

        # Парсим аргументы командной строки
        return parser.parse_args(argv)

    def settings_read(
        self,
        *,
        debug_loggers: list[str] | None = None,
        reset_settings: bool = False,
        **kwargs,
    ) -> None:
        """Считываем настройки приложения."""
        # Настраиваем журналирование
        setup_logging(debug_loggers=list(chain(*(debug_loggers or []))))

        # Проверка совместимости настроек
        main_wnd_key = self.settings.value('main_wnd', None, str)
        if main_wnd_key and main_wnd_key != self.key:
            print('Найдены несовместимые настройки! Настройки будут сброшены.')  # noqa: T201
            reset_settings = True

        self.theme_settings_read(self.settings)

        # Сбрасываем настройки, если надо
        if reset_settings:
            dev_log.info('!!! application settings reset !!!')
            self.set_default_settings()

        # Загружаем настройки для контролов
        dev_log.info(f'{self.key}::settings_read')
        for ctrl in self.controls.values():
            ctrl.settings_read(**kwargs)

    def settings_write(self) -> None:
        dev_log.info(f'{self.key}::settings_write')

        # Пишем ключ совместимости настроек
        self.settings.setValue('main_wnd', self.key)

        # Сохраняем текущую выбранную тему
        self.theme_settings_write(self.settings)

        # Сохраняем настройки для плагинов
        for ctrl in self.controls.values():
            ctrl.settings_write()

    ############################
    # API состояния приложения #
    ############################

    def state_read(self) -> None:
        """Считываем состояния виджетов приложения."""
        dev_log.info(f'{self.key}::state_read')

        # Состояние главного окна
        self.settings.beginGroup('MainWindow')
        self.restoreGeometry(self.settings.value('geometry', QByteArray()))  # type: ignore[reportArgumentType]
        self.restoreState(self.settings.value('windowState', QByteArray()))  # type: ignore[reportArgumentType]
        self.settings.endGroup()

        # Состояние вьюшек
        self.views_manager.state_read(self.settings)

    def state_write(self) -> None:
        """Записываем состояния виджетов приложения."""
        dev_log.info(f'{self.key}::state_write')

        # Состояние главного окна
        self.settings.beginGroup('MainWindow')
        self.settings.setValue('geometry', self.saveGeometry())
        self.settings.setValue('windowState', self.saveState())
        self.settings.endGroup()

        # Состояние вьюшек
        self.views_manager.state_write(self.settings)

    def set_default_settings(self) -> None:
        """Установка дефолтных настроек."""
        config_path = self.default_settings_path()
        if config_path and config_path.exists():
            default_settings = QSettings(str(config_path), QSettings.Format.IniFormat)
            self.settings.clear()
            for key in default_settings.allKeys():
                self.settings.setValue(key, default_settings.value(key))
            self.settings.sync()
        else:
            project_log.error(f'{self.app_name} Настройки по умолчанию не найдены: {config_path!s}')

    def reset_settings(self) -> None:
        """Сброс настроек."""
        self.set_default_settings()
        [self.plug_deregister(key) for key in self.plugins]
        self.ctrls_ready_signal.emit()
        self.state_read()
        self.theme_settings_read(self.settings)

    def default_settings_path(self) -> Path:
        """Возвращает путь к файлу с настройками по умолчанию
        Returns:
            Path: путь к файлу с настройками по умолчанию
        """
        return Path(inspect.getfile(self.__class__)).parent.parent / f'default_{self.app_name}.conf'
