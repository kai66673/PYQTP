from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout

from pyqtp.base.text_document_view import TextDocumentView

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow


class PlainTextDocumentView(TextDocumentView):
    def content(self) -> str:
        return self.edit_widget.toPlainText()

    def __init__(
        self, main_wnd: BaseMainWindow, /, *, key: str
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon('doc/txt.svg')
        )

        self.edit_widget = QPlainTextEdit()
        self.edit_widget.setPlainText(
            Path(key).read_text(encoding='utf-8')
        )
        self.edit_widget.document().modificationChanged.connect(self.set_modified)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addWidget(self.edit_widget)
