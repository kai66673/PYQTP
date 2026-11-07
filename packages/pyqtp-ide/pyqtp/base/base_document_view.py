from __future__ import annotations

from typing import TYPE_CHECKING

from pyqtp.views.base_view import BaseView

if TYPE_CHECKING:
    from pyqtp.base.base_document import Document
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class BaseDocumentView(BaseView):
    def document(self) -> Document:
        raise NotImplementedError

    def __init__(
        self, main_wnd: PluggableMainWindow, /, *, key: str
    ) -> None:
        super().__init__(main_wnd, key=key)
        self._main_wnd = main_wnd
        self._identity = key
