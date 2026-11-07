from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, Qt
from PySide6.QtWidgets import QMenu, QMessageBox

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.help_control.help_tmp_files_cache import HelpTmpFilesCache
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.help_view.help_view import HelpView

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.base.help_menu_context import HelpMenuContext


help_log = logging.getLogger('bcad.help')


class HelpControl(BaseControl):
    """
    Контроллер справки.

    Обеспечивает:
      - реализует меню справки

    """

    help_menu_context: HelpMenuContext
    _main_wnd: BaseMainWindow

    def __init__(self) -> None:
        super().__init__()

        # Пункт "Контекстная справка"
        self.context_help_action = QAction(QIcon(), 'Контекстная справка', self)
        self.context_help_action.triggered.connect(self._on_context_help_action)
        self.context_help_action.setShortcut(QKeySequence('F1'))
        self.context_help_action.setShortcutContext(
            Qt.ShortcutContext.ApplicationShortcut
        )

        # Пункт "О программе"
        self.about_action = QAction(QIcon(), 'О программе', self)
        self.about_action.triggered.connect(self._on_about_action)

    # #####
    # API #
    #######

    def setup_views(self) -> None:
        assert self._main_wnd is not None
        url_path = self._main_wnd.assets_manager.asset_path(self.help_menu_context.index_url)
        assert url_path is not None
        help_view = HelpView(
            QUrl.fromLocalFile(url_path),
            self._main_wnd, key='HelpView'
        )
        self._main_wnd.view_register(help_view)

        help_context_icon = self._main_wnd.theme_manager.icon(
            'help_context.svg'
        )
        self.about_action.setIcon(help_context_icon)
        self.context_help_action.setIcon(help_context_icon)

    def show_html(self, html: str) -> None:
        assert self._main_wnd is not None
        help_view = self._main_wnd.view_get(key='HelpView')
        assert isinstance(help_view, HelpView)
        help_view.show_html(html)

    def show_url(self, url: QUrl) -> None:
        assert self._main_wnd is not None
        help_view = self._main_wnd.view_get(key='HelpView')
        assert isinstance(help_view, HelpView)
        help_view.show_url(url)

    #####################
    # Интерактивное API #
    #####################

    def ui_show_about_dialog(self) -> None:
        assert self._main_wnd is not None

        about_source = self.help_menu_context.about_source
        if isinstance(about_source, str):
            QMessageBox.about(self._main_wnd, 'О программе', about_source)
        else:
            about_source(self._main_wnd)

    ##################
    # Слоты действий #
    ##################

    @Slot()
    def _on_about_action(self) -> None:
        self.ui_show_about_dialog()

    @Slot()
    def _on_context_help_action(self):
        assert self._main_wnd is not None
        focused_view = self._main_wnd.views_manager.focused_view_get()
        if focused_view is not None:
            self._show_help_context(focused_view.help_context)

    def _show_help_context(self, help_context):
        if isinstance(help_context, str):
            self.show_html(help_context)
        else:
            self.show_url(help_context)
        assert self._main_wnd is not None
        self._main_wnd.view_show('HelpView')

    ################
    # API виджетов #
    ################

    def setup_menu(self) -> None:
        """
        Реализация создания меню.

        """
        super().setup_menu()
        assert self._main_wnd is not None
        menubar = self._main_wnd.menuBar()
        try:
            help_menu = menubar.by_path('Справка')
        except KeyError:
            help_menu = menubar.addMenu('Справка', priority=10)

        self.help_menu_context = self._main_wnd.create_help_menu_context()
        if self.help_menu_context.docs:
            assert isinstance(help_menu, QMenu)
            manuals_menu = help_menu.addMenu('Руководства')
            for menu_title, file_path in self.help_menu_context.docs.items():
                manuals_menu.addAction(
                    DocAction(
                        self,
                        doc_path=str(file_path),
                        name=menu_title,
                        description=f'Открыть документ "{menu_title}"',
                    )
                )

        assert isinstance(help_menu, PriorityMenu)
        help_menu.addSeparator()
        help_menu.addAction(self.context_help_action, priority=50)

        help_menu.addSeparator()
        help_menu.addAction(self.about_action, priority=100)

    def setup_relations(self):
        super().setup_relations()
        assert self._main_wnd is not None
        self._main_wnd.help_requested.connect(self._show_help_context)

        # Регистрируем журнал справки в панели событий
        self._main_wnd.events_ctrl.events_log_register(
            name=help_log.name, group='Справка'
        )


class DocAction(QAction):
    _url: QUrl

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        doc_path: str,
        name: str,
        description: str | None = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(name, parent)
        self._url = QUrl.fromLocalFile(doc_path)

        if description:
            self.setToolTip(description)

        if self._url.isValid():
            self.triggered.connect(self._on_triggered)

    @Slot()
    def _on_triggered(self) -> None:
        """
        Реализация открытия документа.

        """
        if self._url.scheme() == 'qrc':
            QDesktopServices.openUrl(HelpTmpFilesCache.file_path(self._url))
        else:
            QDesktopServices.openUrl(self._url)
