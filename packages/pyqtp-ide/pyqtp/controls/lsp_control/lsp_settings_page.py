from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.settings_control.settings_page import SettingsPage

if TYPE_CHECKING:
    from pyqtp.controls.lsp_control.lsp_hub import LspHub


class LspRegistrySettingsPage(SettingsPage):
    """
    Страница «Языковые серверы (LSP)».

    Редактирование стека LSP-серверов целиком в JSON-виде. Конфигурация
    валидируется ``LspRegistry.from_json``: при битом JSON ``apply()``
    бросает ошибку, действующий реестр не меняется. Применённый реестр
    используется новыми подключениями редакторов; уже поднятые процессы
    серверов живут до завершения IDE.
    """

    title = 'Языковые серверы (LSP)'
    order = 50

    _HINT = (
        'Формат: languageId → {extensions, root_markers, '
        'servers: [{name, command, enabled}]} (порядок серверов = '
        'приоритет). Применяется к новым редакторам; уже запущенные '
        'процессы серверов завершатся при выходе из IDE.'
    )

    def __init__(self, hub: LspHub) -> None:
        super().__init__(key='lsp')
        self._hub = hub
        self._editor: QPlainTextEdit | None = None

    def create_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(self._HINT, widget)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._editor = QPlainTextEdit(widget)
        font = QFont('monospace')
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        layout.addWidget(self._editor, stretch=1)

        self.load()
        return widget

    def load(self) -> None:
        assert self._editor is not None
        self._editor.setPlainText(self._hub.registry_json())

    def apply(self) -> None:
        assert self._editor is not None
        # ValueError/TypeError при битом конфиге уходит в окно настроек;
        # действующий реестр остаётся нетронутым
        self._hub.registry_apply_json(self._editor.toPlainText())

    def reset_defaults(self) -> None:
        self._hub.registry_reset()
