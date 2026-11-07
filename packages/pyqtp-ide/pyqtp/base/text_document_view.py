from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pyqtp.base.base_document_view import BaseDocumentView

if TYPE_CHECKING:
    from pyqtp.base.base_document import Document
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class TextDocumentView(BaseDocumentView):
    _is_modified: bool

    def __init__(
        self, main_wnd: PluggableMainWindow, /, *, key: str
    ) -> None:
        super().__init__(main_wnd, key=key)
        self._is_modified = False

        self.setWindowTitle(str(Path(key).name))

    def content(self) -> str:
        raise NotImplementedError

    def document(self) -> Document:
        return self

    def identity(self) -> str:
        return self._identity

    def is_modified(self) -> bool:
        return self._is_modified

    def set_modified(self, modified: bool = True) -> None:  # noqa: FBT001, FBT002
        self._is_modified = modified

    def do_save(self) -> bool:
        with Path(self._identity).open('wt', encoding='utf-8') as f:
            f.write(self.content())

        return True

    def save(self) -> bool:
        if self.do_save():
            self.set_modified(False)
            return True

        return False

    def language(self) -> str:
        return ''
