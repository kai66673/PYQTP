from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QLibraryInfo, Qt, QTranslator
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from PySide6.QtGui import QFont, QFontDatabase

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.dbus_helper import connect_to_bcad_dbus_session

if sys.platform == 'win32':
    import PySide6.QtSvg  # noqa: F401

from pyqtp.utils.dark_win_app import DarkWinApplication

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from pyqtp.base.pluggable_main_window import PluggableMainWindow


def _apply_application_font(application: QApplication):
    font_path = Path(__file__).parent / 'assets' / 'fonts' / 'MICROSS.TTF'
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
    app_font = QFont(font_family, 10)
    application.setFont(app_font)


def run_main_window(
    main_wnd_cls: type[PluggableMainWindow],
    *,
    org_name: str,
    app_name: str,
    app: QApplication | None = None,
) -> None:
    """
    Запуск главного окна
    """
    # Установка переменной окружения для текущего процесса (wayland BUG-fix)
    if sys.platform.startswith('linux') or sys.platform.startswith('darwin'):
        os.environ['QT_QPA_PLATFORM'] = 'xcb'

    # Проверка переменной отключения D-Bus (Single Application)
    disable_dbus_single_app = bool(os.environ.get('BCAD_DISABLE_DBUS'))

    if disable_dbus_single_app:
        run_main_window_no_dbus(
            main_wnd_cls,
            org_name=org_name,
            app_name=app_name,
            app=app,
        )
    else:
        run_main_window_dbus(
            main_wnd_cls,
            org_name=org_name,
            app_name=app_name,
            app=app,
        )


def run_main_window_no_dbus(
    main_wnd_cls: type[PluggableMainWindow],
    *,
    org_name: str,
    app_name: str,
    app: QApplication | None = None,
) -> None:
    """
    Запуск главного окна (no DBUS)
    """
    if app is None:
        app = DarkWinApplication([])

    _apply_application_font(app)

    qt_translator = QTranslator()
    if qt_translator.load('qt_ru', QLibraryInfo.location(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt_translator)

    use_kdd = bool(os.environ.get('BCAD_GUI_USE_KDD'))
    main_wnd = main_wnd_cls(
        org_name=org_name,
        app_name=app_name,
        dock_system_type=ViewsManagerType.kdd
        if use_kdd
        else ViewsManagerType.ads,
    )

    main_wnd.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    args = main_wnd.settings_parse_argv(sys.argv[1:])
    main_wnd.settings_read(**vars(args))  # загрузка настроек приложения
    with main_wnd.with_splash():
        main_wnd.show_splash_message('Загружаем подгружаемые модули...')
        main_wnd.setup()  # инициализация всех плагинов
        main_wnd.show_splash_message(
            'Выполняем инициализацию элементов управления...'
        )
        main_wnd.setup_ui()  # инициализация виджетов, в том числе плагинов
    main_wnd.show()

    app.exec()


def run_main_window_dbus(
    main_wnd_cls: type[PluggableMainWindow],
    *,
    org_name: str,
    app_name: str,
    app: QApplication | None = None,
) -> None:
    """
    Запуск главного окна (DBUS/Single App)
    """
    if app is None:
        app = DarkWinApplication([])

    _apply_application_font(app)

    qt_translator = QTranslator()
    if qt_translator.load('qt_ru', QLibraryInfo.location(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt_translator)


    session_bus = connect_to_bcad_dbus_session()
    if not session_bus.isConnected():
        sys.exit(-1)

    iface_name = main_wnd_cls.dbus_iface_name()
    adapter_name = main_wnd_cls.dbus_object_name()
    reply = session_bus.interface().isServiceRegistered(iface_name)
    if not reply.isValid():
        sys.exit(-2)
    if reply.value():
        app_iface = QDBusInterface(iface_name, adapter_name)
        if app_iface.isValid():
            app_iface.call('activate')
            sys.exit(0)
        sys.exit(-3)

    use_kdd = True
    main_wnd = main_wnd_cls(
        org_name=org_name,
        app_name=app_name,
        dock_system_type=ViewsManagerType.kdd
        if use_kdd
        else ViewsManagerType.ads,
    )

    main_wnd.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    args = main_wnd.settings_parse_argv(sys.argv[1:])
    main_wnd.settings_read(**vars(args))  # загрузка настроек приложения
    with main_wnd.with_splash():
        main_wnd.show_splash_message('Загружаем подгружаемые модули...')
        main_wnd.setup()  # инициализация всех плагинов
        main_wnd.show_splash_message(
            'Выполняем инициализацию элементов управления...'
        )
        main_wnd.setup_ui()  # инициализация виджетов, в том числе плагинов
    main_wnd.show()

    session_bus.registerService(iface_name)
    session_bus.registerObject(
        adapter_name, main_wnd, QDBusConnection.RegisterOption.ExportAllSlots
    )

    app.exec()


if __name__ == '__main__':
    from pyqtp.base.base_main_window import BaseMainWindow

    class _MainWindow(BaseMainWindow):
        @staticmethod
        def dbus_iface_name() -> str:
            return 'bcad.mipt.TestLaunchInterface'

        @staticmethod
        def dbus_object_name() -> str:
            return '/SimpleTest'

    run_main_window(_MainWindow, org_name='PYQTP', app_name='PYQTP-STUB')
