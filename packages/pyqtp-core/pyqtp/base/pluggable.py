from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject

from pyqtp.base.helpers import classproperty

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


dev_log = logging.getLogger('bcad.dev')


class PluggableObject(QObject):
    """
    Базовый класс для плагина приложения.

    Плагин является моделью в концепции Model-View.
    Плагин может (опционально!) создавать свои вьюшки в меню, тулбарах,
    статусбарах и создавать встраиваемые панели.
    """

    _main_wnd: PluggableMainWindow | None

    @classproperty
    def key(cls) -> str:  # noqa: N805
        return cls.__qualname__  # pyright: ignore[reportAttributeAccessIssue]

    ############################
    # API стадий жизни плагина #
    ############################

    def __init__(self) -> None:
        super().__init__()
        self._main_wnd = None
        self.setObjectName(self.key)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(key="{self.key}")'

    def register(self, main_wnd: PluggableMainWindow) -> PluggableObject:
        """Регистрирует плагин в приложении."""
        raise NotImplementedError

    def deregister(self, main_wnd: PluggableMainWindow) -> None:
        """Дерегистрируем плагин в приложении."""
        raise NotImplementedError

    def setup(self) -> None:
        """
        Инициализация плагина.

        Осуществляем такие вещи как подключение к СУБД, загрузку файлов и т.д.
        """
        dev_log.debug(f'{self.key}::setup')

    def complete_pending_transactions(self) -> None:
        """
        Принудительное завершение незаконченных действий.

        Инициируем такие вещи, как применение изменений в активных редакторах.
        """

    def on_shutdown(self) -> bool:
        """
        Запрос завершения работы приложения.

        Осуществляем такие вещи как запрос сохранения изменений.
        """
        return True

    def teardown(self) -> None:
        """
        Вызывается при завершении работы плагина.

        Осуществляем такие вещи как отключение от сервера.
        """
        dev_log.debug(f'{self.key}::teardown')

    ###################################################
    # API редактирования пользовательского интерфейса #
    ###################################################

    def setup_ui(self) -> None:
        """
        Регистрирует виджеты в главном окне.

        Опциональный функционал.
        """
        dev_log.debug(f'{self.key}::setup_ui')
        self.setup_menu()
        self.setup_statusbar()
        self.setup_toolbars()
        self.setup_views()

    def teardown_ui(self) -> None:
        """
        Дерегистрирует виджеты в главном окне.

        Опциональный функционал.
        """
        dev_log.debug(f'{self.key}::teardown_ui')
        self.teardown_menu()
        self.teardown_statusbar()
        self.teardown_toolbars()
        self.teardown_views()

    def setup_menu(self) -> None:
        pass

    def setup_statusbar(self) -> None:
        pass

    def setup_toolbars(self) -> None:
        pass

    def setup_views(self) -> None:
        pass

    def teardown_menu(self) -> None:
        pass

    def teardown_statusbar(self) -> None:
        pass

    def teardown_toolbars(self) -> None:
        pass

    def teardown_views(self) -> None:
        pass

    ########################
    # API настроек плагина #
    ########################

    def settings_read(self, **kwargs: Any) -> None:  # noqa: ARG002
        """
        Инициализация настроек плагина.

        Описание:
          - загружаем параметры из QSettings
          - в качестве приоритетных параметров в kwargs
            передаются результаты обработки аргументы командной строки.
        """
        dev_log.debug(f'{self.key}::settings_read')

    def settings_write(self) -> None:
        """Сохранение настроек плагина."""
        dev_log.debug(f'{self.key}::settings_write')
