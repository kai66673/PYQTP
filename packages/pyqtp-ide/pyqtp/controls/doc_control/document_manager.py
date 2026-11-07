from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, QKeySequence, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolBar

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.doc_control.editor_settings_page import EditorSettingsPage
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.monaco_document_view import MonacoDocumentView
from pyqtp.web.editor_options import (
    DEFAULT_EDITOR_UX_OPTIONS,
    read_editor_ux_options,
    to_monaco_options,
    validate_editor_ux_options,
    write_editor_ux_options,
)

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.base.docking_system.base_dock_widget import BaseDockWidget


def _document_view_cls_from_identity(_identity: str) -> type[BaseDocumentView]:
    return MonacoDocumentView


med_log = logging.getLogger('bcad.med')


class DocumentManager(BaseControl):
    _main_wnd: BaseMainWindow
    toolbar: QToolBar

    _last_open_dir: Path
    _opened_docs: dict[str, BaseDocumentView]

    document_opened = Signal(BaseDocumentView)
    document_closed = Signal(BaseDocumentView)

    def __init__(self) -> None:
        super().__init__()

        self._last_open_dir = Path.home()
        self._opened_docs = {}

        self.action_open_doc = Action(
            icon=QIcon(),
            title='Открыть Файл',
            parent=self,
            description='Открыть существующий документ',
            shortcut=QKeySequence.StandardKey.Open,
        )

        self.action_save_doc = Action(
            icon=QIcon(),
            title='Сохранить Файл',
            parent=self,
            description='Сохранить текущий документ',
            shortcut=QKeySequence.StandardKey.Save,
        )

        self.action_open_doc.triggered.connect(
            self._open_document
        )

    def setup_relations(self) -> None:
        super().setup_relations()

        # Регистрируем журнал редактора в панели событий
        # (bcad.med пишет QtMonacoEditorWidget из pyqtp/web/qtmonaco_editor.py,
        # qtmonaco -- сама vendored-библиотека, её дочерние логгеры
        # перехватываются по наследованию)
        self._main_wnd.events_ctrl.events_log_register(
            name=med_log.name, group='Редактор'
        )
        self._main_wnd.events_ctrl.events_log_register(
            name='qtmonaco', group='Редактор'
        )

    def setup(self) -> None:
        super().setup()

        self.action_open_doc.setIcon(
            self._main_wnd.theme_manager.icon('io/open.svg')
        )
        self.action_save_doc.setIcon(
            self._main_wnd.theme_manager.icon('io/save.svg')
        )

        # этап 7: страница «Редактор» в окне настроек приложения
        self._main_wnd.settings_page_register(EditorSettingsPage(self))

    def setup_toolbars(self) -> None:
        self.toolbar = toolbar = QToolBar('Документы')
        toolbar.setObjectName('DocumentsToolBar')
        toolbar.addAction(self.action_open_doc)
        toolbar.addAction(self.action_save_doc)
        self._main_wnd.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def setup_menu(self) -> None:
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            self.file_menu = menubar.by_path('Файл')
        except KeyError:
            self.file_menu = menubar.addMenu('Файл', priority=-10)

        assert isinstance(self.file_menu, PriorityMenu)

        self.file_menu.addAction(self.action_open_doc, priority=5)
        self.file_menu.addAction(self.action_save_doc, priority=6)

    # --- UX-настройки редактора (этап 7, страница «Редактор») ---

    def editor_ux_options(self) -> dict[str, Any]:
        """Текущие логические UX-настройки редактора (источник — QSettings)."""
        return read_editor_ux_options(self._main_wnd.settings)

    def apply_editor_ux_options(self, options: dict[str, Any]) -> None:
        """Валидирует, сохраняет в QSettings и применяет к всем открытым
        редакторам.

        При некорректных значениях бросает ValueError/TypeError (окно
        настроек покажет ошибку, источник не будет тронут).
        """
        validate_editor_ux_options(options)  # ValueError/TypeError до записи
        write_editor_ux_options(self._main_wnd.settings, options)
        self._main_wnd.settings.sync()
        monaco_payload = to_monaco_options(options)
        for view in self._opened_docs.values():
            self._apply_editor_ux_options(view, monaco_payload)

    def reset_editor_ux_options(self) -> None:
        """Сбрасывает настройки редактора к значениям по умолчанию."""
        self.apply_editor_ux_options(dict(DEFAULT_EDITOR_UX_OPTIONS))

    def _apply_editor_ux_options(
        self, view: BaseDocumentView, payload: dict[str, Any] | None = None
    ) -> None:
        """Доводит UX-настройки Monaco до редактора конкретной вьюхи (этап 7).

        ``payload`` — уже готовые опции Monaco (чтобы не перечитывать QSettings
        на каждую вьюху при массовом применении); по умолчанию — текущие
        сохранённые настройки. Вьюхи без Monaco (plain text) пропускаются.

        Сообщения, посланные до инициализации моста, буферизуются
        ``qtmonaco.Connector`` и уходят в JS сразу после неё, поэтому настройки
        применимы сразу после создания вьюхи.
        """
        edit_widget = getattr(view, 'edit_widget', None)
        if edit_widget is None:
            return
        if payload is None:
            payload = to_monaco_options(self.editor_ux_options())
        edit_widget.update_editor_options(payload)

    def _open_document(self):
        # Выбираем файл
        identity = QFileDialog.getOpenFileName(
            parent=self._main_wnd,
            caption='Открыть файл',
            dir=str(self._last_open_dir),
            filter='Все файлы (*.*)',
            options=QFileDialog.Option.DontUseNativeDialog,
        )[0]

        # Открываем файл
        if identity:
            self._last_open_dir = Path(identity).parent
            self.open_document(identity)

    def open_document(self, identity: str | Path) -> BaseDocumentView:
        """Открывает документ (или активирует уже открытый); путь — ключ вьюхи."""
        identity = str(Path(identity))
        doc_view = self._opened_docs.get(identity)
        if doc_view is not None:
            doc_view.ensure_visible()
            return doc_view

        new_view = _document_view_cls_from_identity(identity)(
            self._main_wnd, key=identity
        )
        self._apply_editor_ux_options(new_view)
        self._main_wnd.view_central_register(
            new_view, close_cb=self._handle_doc_view_close_requested
        )
        self._opened_docs[identity] = new_view
        new_view.ensure_visible()
        self.document_opened.emit(new_view)
        return new_view

    def open_definition_uri(self, payload: dict) -> None:
        """Этап 6: LSP-навигация «переход к определению» в другой файл.

        Ответ редактора (``QtMonacoEditorWidget.open_uri_requested``):
        ``{'uri': 'file:///...', 'range': {'startLineNumber': ...,
        'startColumn': ...} | None}`` — нумерация Monaco (1-based).
        Открывает (или активирует) документ и ставит каретку на определение.
        """
        uri = str(payload.get('uri') or '')
        parsed = urlparse(uri)
        if parsed.scheme != 'file' or not parsed.path:
            med_log.warning('Пропущен переход по не-файловому uri: %s', uri)
            return
        path = Path(unquote(parsed.path))
        if not path.is_file():
            med_log.warning('Пропущен переход к несуществующему файлу: %s', path)
            return

        view = self.open_document(path)
        line = (payload.get('range') or {}).get('startLineNumber')
        if not isinstance(line, int):
            return
        column = (payload.get('range') or {}).get('startColumn')
        edit_widget = getattr(view, 'edit_widget', None)
        if edit_widget is not None:
            # set_cursor буферизуется коннектором до инициализации моста JS —
            # для только что открытого документа каретка встанет после загрузки
            edit_widget.set_cursor(
                line, column if isinstance(column, int) else 1,
                move_to_position='center',
            )

    def _handle_doc_view_close_requested(
        self, dock: BaseDockWidget, view_key: str
    ):
        view = self._opened_docs.get(view_key)
        if view is None:  # уже закрыт (например, повторный клик по крестику)
            return
        if view.document().is_modified():
            message_box = QMessageBox(parent=self._main_wnd)
            message_box.setText(
                f'Документ "{view_key}" был изменен!\nСохранить изменения?'
            )
            message_box.setInformativeText('Сохранить изменения?')
            message_box.setWindowTitle(self._main_wnd.base_window_title)

            buttons = (
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Discard
            )

            message_box.setStandardButtons(buttons)
            message_box.setDefaultButton(QMessageBox.StandardButton.Save)

            message_box.button(QMessageBox.StandardButton.Save).setText(
                'Сохранить'
            )
            message_box.button(QMessageBox.StandardButton.Discard).setText(
                'Не сохранять'
            )
            message_box.button(QMessageBox.StandardButton.Cancel).setText(
                'Отмена'
            )

            result = message_box.exec()

            if result == QMessageBox.StandardButton.Save:
                if not view.document().save():
                    return
            elif result != QMessageBox.StandardButton.Discard:
                return

        # central-доки KDD — «голые» kdd.DockWidget: у них в python-биндингах
        # нет ads-совместимого closeDockWidget, есть только forceClose
        # (закрывает core-состояние дока без повторного QCloseEvent —
        # рекурсии в фильтр закрытия из view_central_register не будет)
        # getattr, а не прямой вызов: у «голых» kdd.DockWidget метода
        # closeDockWidget нет (ruff B009 / pyright об Protocol)
        close_dock = getattr(dock, 'closeDockWidget', None) or getattr(dock, 'forceClose')  # noqa: B009
        close_dock()
        self.document_closed.emit(view)
        del self._opened_docs[view_key]
