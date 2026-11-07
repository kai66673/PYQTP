from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal, Slot

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.events_control.event_message_box import EventMessageBox
from pyqtp.controls.events_control.events_icons import LogIconsStorage
from pyqtp.controls.events_control.events_models import (
    LogCategoriesModel,
    LogLevel,
    LogMessage,
    LogMessagesModel,
)
from pyqtp.controls.events_control.events_toolbar_widget import (
    EventsToolbarWidget,
)
from pyqtp.views.events_view.events_toolbar import EventsToolBar
from pyqtp.views.events_view.events_view import EventsView

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.theme.theme import Theme
    from pyqtp.utils.waiting_spinner import SpinnerMessage


class QLoggingHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        self._callback(LogMessage(record))


class EventsControl(BaseControl):
    log_registered = Signal(str, str)
    open_link_requested = Signal(str)

    insert_message_signal = Signal(LogMessage)

    _main_wnd: BaseMainWindow
    _events_statusbar: EventsToolbarWidget | None

    @property
    def verbose(self) -> int:
        return self._verbose

    def __init__(self) -> None:
        super().__init__()

        # Инициализируем модель перехвата сообщений журнала
        self.categories_model = LogCategoriesModel(parent=self)
        self.messages_model = LogMessagesModel(parent=self)
        self.logging_handler = QLoggingHandler(self._handle_log_message)
        self.insert_message_signal.connect(self._process_log_message)
        self._verbose = 0
        self._loggers = []
        self._events_statusbar = None

    def teardown(self) -> None:
        for logger in self._loggers:
            logger.removeHandler(self.logging_handler)
        self._loggers.clear()

    @classmethod
    def settings_setup_argparser(cls, parser: ArgumentParser) -> None:
        group = parser.add_argument_group('Параметры журнала событий')
        group.add_argument(
            '-v',
            '--verbose',
            action='count',
            default=0,
            required=False,
            help='выводить в журнале событий отладочные сообщения',
        )

    def settings_read(
        self,
        *,
        verbose: int = 0,
        **kwargs,  # noqa: ARG002
    ) -> None:
        """Загружает параметры журнала событий."""
        super().settings_read()
        self._verbose = verbose

        if verbose:
            self.logging_handler.setLevel(logging.DEBUG)
        else:
            self.logging_handler.setLevel(logging.INFO)

    ################
    # API событий  #
    ################

    def setup(self) -> None:
        super().setup()

        # Регистрируем в журнале системные логгеры самого фреймворка
        # (их пишет PluggableMainWindow и базовые классы ядра)
        self.events_log_register(name='bcad.dev', group='Разработка')
        self.events_log_register(name='bcad.project', group='Проект')

    def events_log_clear(self):
        self.messages_model.clear_log()

    def events_log_register(self, *, group: str, name: str) -> None:
        # Выставляем уровень
        logger = logging.getLogger(name)
        if not self._verbose and logger.level != logging.DEBUG:
            logger.setLevel(logging.INFO)
        else:
            logger.setLevel(logging.DEBUG)

        # Регистрируем обработчик в logging
        logger.addHandler(self.logging_handler)
        self._loggers.append(logger)

        # Регистрируем категорию в модели категорий
        self.categories_model.add_log(group, name)
        self.log_registered.emit(group, name)

    def _handle_log_message(self, log_msg: LogMessage):
        self.insert_message_signal.emit(log_msg)

    @Slot(LogMessage)
    def _process_log_message(self, log_msg: LogMessage):
        if not log_msg.notify:
            self.messages_model.add_message(log_msg)

    #################
    # API диалогов  #
    #################

    @Slot(LogMessage)
    def _ui_process_log_message(self, log_msg: LogMessage) -> None:
        self.log_message_notify(log_msg)
        if log_msg.popup:
            self._ui_show_popup_dialog(
                message=log_msg.message, level=log_msg.level, lang=log_msg.lang
            )

    def _ui_show_popup_dialog(
        self,
        message: str,
        *,
        level: LogLevel = LogLevel.Error,
        lang: str | None = None,
    ) -> None:
        message, *rest = message.split('\n', 1)
        detailed_text = rest[0] if rest else None

        message_box = EventMessageBox(
            self._main_wnd,
            title=self._main_wnd.windowTitle(),
            message=message,
            detailed_text=detailed_text,
            level=level,
        )
        if lang is not None:
            message_box.set_highlighting(lang=lang)
        message_box.exec_()

    def events_notify(self, text: str, duration: int = 10 * 1000) -> None:
        """Ненавязчивое уведомление в строке состояния."""
        self._main_wnd.statusBar().showMessage(text.split('\n', 1)[0], duration)

    def log_message_notify(
        self, log_msg: LogMessage, duration: int = 10 * 1000
    ) -> None:
        if (
            self._events_statusbar is not None
            and log_msg.level != LogLevel.Debug
        ):
            self._events_statusbar.show_log_message(log_msg, duration)

    def spinner_message_notify(
        self, spinner_message: SpinnerMessage, duration: int = 10 * 1000
    ):
        if self._events_statusbar is not None:
            self._events_statusbar.show_log_message(spinner_message, duration)

    def events_show_exception_popup(
        self, *, message: str, exception: Exception | None = None
    ) -> None:
        """Показывает диалог с описанием ошибки."""
        if exception is not None:
            message += '\n' + ''.join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            )
        self._ui_show_popup_dialog(message, level=LogLevel.Error, lang='python')

    ################
    # API виджетов #
    ################

    def setup_ui(self) -> None:
        super().setup_ui()
        self.insert_message_signal.connect(self._ui_process_log_message)

    def teardown_ui(self) -> None:
        super().teardown_ui()
        self.insert_message_signal.disconnect(self._ui_process_log_message)

    def setup_views(self) -> None:
        # Создаём вьюшку
        events_view = EventsView(self._main_wnd, f'{self.key}_events_view')
        dock = self._main_wnd.view_register(events_view, widget_area='bottom')

        assert self._events_statusbar is not None
        self._events_statusbar.add_action(dock.toggleViewAction())

        # Формируем тулбар вьюшки
        events_toolbar = EventsToolBar(
            categories_model=self.categories_model,
            events_view=events_view,
            parent=self._main_wnd,
            verbose=True,
        )
        events_view.set_toolbar(events_toolbar)
        events_toolbar.filter_settings_changed.connect(
            events_view.filter_settings_changed
        )
        events_toolbar.filter_layout.handle_filter_settings_changed()

        events_view.open_link_requested.connect(self.open_link_requested)

        self._main_wnd.theme_manager.theme_changed.connect(
            self._on_theme_changed
        )
        self._on_theme_changed(self._main_wnd.theme_manager.current_theme)

    def _on_theme_changed(self, theme: Theme) -> None:
        LogIconsStorage.update(theme)

    def teardown_views(self) -> None:
        super().teardown_views()
        self._main_wnd.view_deregister(f'{self.key}_events_view')

    def setup_statusbar(self) -> None:
        statusbar = self._main_wnd.statusBar()
        self._events_statusbar = EventsToolbarWidget()
        statusbar.addWidget(self._events_statusbar)

        self._events_statusbar.open_link_requested.connect(
            self.open_link_requested
        )
