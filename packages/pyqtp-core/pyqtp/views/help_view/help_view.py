from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Slot
from PySide6.QtWidgets import QVBoxLayout

from pyqtp.views.base_view import BaseView
from pyqtp.views.help_view.help_widget import HelpWidget

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class HelpView(BaseView):
    def __init__(
        self,
        index_url: QUrl,
        main_wnd: PluggableMainWindow, /, *, key: str | None = None
    ):
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Справка')
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon('wiki.svg', is_checkable=True)
        )

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.help_widget = HelpWidget(index_url, main_wnd.assets_manager)
        self.main_layout.addWidget(self.help_widget)

    @Slot(bool)
    def on_modality_changed(self, *, is_modal: bool, is_self_modal: bool):
        pass

    @property
    def help_context(self) -> str | QUrl:
        return QUrl('qrc:/help/help_view/index.html')

    def show_html(self, html: str):
        self.help_widget.setHtml(html)

    def show_url(self, url: QUrl):
        self.help_widget.set_url(url)
