from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout

from pyqtp.base.text_document_view import TextDocumentView
from pyqtp.web.qtmonaco_editor import QtMonacoEditorWidget

if TYPE_CHECKING:
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow


class MonacoDocumentView(TextDocumentView):
    _main_wnd: IdeMainWindow

    def __init__(
        self, main_wnd: IdeMainWindow, /, *, key: str
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon('doc/txt.svg')
        )

        file_path = Path(key)
        self.edit_widget = QtMonacoEditorWidget(
            file_path,
            lsp_host=self._main_wnd.lsp_hub.lsp_stack_for(file_path.as_uri()),
        )
        self.edit_widget.modification_changed.connect(self.set_modified)
        # этап 6: LSP-навигация (F12/ctrl+click) в другой файл — менеджер
        # открывает/активирует документ и ставит каретку на определение
        self.edit_widget.open_uri_requested.connect(
            self._main_wnd.doc_ctrl.open_definition_uri
        )
        # этап 8.2: rename/quickfix затронул другие файлы — чужую часть
        # WorkspaceEdit применяет менеджер (редакторы/диск)
        self.edit_widget.workspace_edit_requested.connect(
            self._main_wnd.doc_ctrl.on_workspace_edit_requested
        )

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addWidget(self.edit_widget)

    def content(self) -> str:
        return self.edit_widget.get_text()

    def save(self) -> bool:
        if super().save():
            self.edit_widget.mark_saved()
            return True

        return False
