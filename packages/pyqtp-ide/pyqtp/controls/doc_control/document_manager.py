from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, QKeySequence, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolBar

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.controls.base_control import BaseControl
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.monaco_document_view import MonacoDocumentView

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
            doc_view = self._opened_docs.get(identity)
            if doc_view is not None:
                doc_view.ensure_visible()
                return

            new_view = _document_view_cls_from_identity(identity)(
                self._main_wnd, key=identity
            )
            self._main_wnd.view_central_register(
                new_view, close_cb=self._handle_doc_view_close_requested
            )
            self._opened_docs[identity] = new_view
            new_view.ensure_visible()
            self.document_opened.emit(new_view)

    def _handle_doc_view_close_requested(
        self, dock: BaseDockWidget, view_key: str
    ):
        view = self._opened_docs[view_key]
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

        dock.closeDockWidget()
        self.document_closed.emit(view)
        del self._opened_docs[view_key]
