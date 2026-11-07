from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence, Qt

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.settings_control.settings_window import SettingsWindow
from pyqtp.utils.priority_menu import PriorityMenu

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.controls.settings_control.settings_page import SettingsPage

dev_log = logging.getLogger('bcad.dev')


class SettingsControl(BaseControl):
    """
    Контроллер системы страниц настроек.

    Обеспечивает:
      - реестр страниц настроек (``page_register`` / ``page_unregister``,
        как реестр вьюшек у главного окна);
      - немодальное окно настроек со списком страниц, поиском и кнопками
        «Применить» / «Откатить» / «Сбросить по умолчанию»;
      - пункт меню «Настройки...» (Ctrl+,).

    Конкретные страницы регистрируют свои контролы-владельцы.
    """

    _main_wnd: BaseMainWindow

    page_registered = Signal(str)
    page_deregistered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._pages: dict[str, SettingsPage] = {}
        self._window: SettingsWindow | None = None
        self._settings_action: QAction | None = None
        self._settings_menu: PriorityMenu | None = None

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(pages={list(self._pages)})'

    ##############################
    # API реестра страниц настроек #
    ##############################

    def page_register(self, page: SettingsPage) -> SettingsPage:
        """Регистрирует страницу настроек в окне."""
        if page.key in self._pages:
            error_message = f'settings page "{page.key}" already registered!'
            raise ValueError(error_message)
        page._main_wnd = self._main_wnd
        self._pages[page.key] = page
        self.page_registered.emit(page.key)
        if self._window is not None:
            self._window.reload_pages()
        return page

    def page_unregister(self, key: str) -> None:
        """Снимает страницу настроек с регистрации."""
        page = self._pages.pop(key)
        self.page_deregistered.emit(page.key)
        if self._window is not None:
            self._window.reload_pages()

    def page_get(self, key: str) -> SettingsPage | None:
        """Возвращает страницу по ключу (или ``None``)."""
        return self._pages.get(key)

    def pages_sorted(self) -> list[SettingsPage]:
        """Страницы в порядке отображения (по ``order``, затем по key)."""
        return sorted(self._pages.values(), key=lambda p: (p.order, p.key))

    #####################
    # API окна настроек #
    #####################

    def settings_window_show(self) -> None:
        """Открывает (или показывает уже открытое) окно настроек."""
        if self._window is None:
            self._window = SettingsWindow(self, self._main_wnd)
        # Синхронизируем значения созданных страниц с актуальными
        # настройками (могли поменяться извне, например тема из уголков меню)
        self._window.sync_pages_values()
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    @Slot()
    def _on_settings_action(self) -> None:
        self.settings_window_show()

    ################
    # API виджетов #
    ################

    def setup_menu(self) -> None:
        super().setup_menu()
        # Подсаживаемся в уже существующее меню «Настройки»
        # (его создаёт UserPluginControl; если плагинов нет -- создаём своё)
        menubar = self._main_wnd.menuBar()
        try:
            settings_menu = menubar.by_path('Настройки')
        except KeyError:
            settings_menu = menubar.addMenu('Настройки', priority=9)
        assert isinstance(settings_menu, PriorityMenu)
        self._settings_menu = settings_menu

        self._settings_action = QAction(
            QIcon(), 'Настройки приложения...', self
        )
        self._settings_action.triggered.connect(self._on_settings_action)
        self._settings_action.setShortcut(QKeySequence('Ctrl+,'))
        self._settings_action.setShortcutContext(
            Qt.ShortcutContext.ApplicationShortcut
        )
        settings_menu.addAction(self._settings_action, priority=10)

    def teardown_menu(self) -> None:
        super().teardown_menu()
        if self._settings_action is not None and self._settings_menu:
            self._settings_menu.removeAction(self._settings_action)
            self._settings_action = None
            self._settings_menu = None

    def teardown(self) -> None:
        super().teardown()
        if self._window is not None:
            self._window.close()
            self._window = None
        self._pages.clear()
