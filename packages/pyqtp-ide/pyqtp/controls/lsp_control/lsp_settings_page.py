from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.project_control.project_control import SCOPE_PROJECT
from pyqtp.controls.project_control.project_settings import (
    KEY_REGISTRY_JSON,
    SECTION_LSP,
)
from pyqtp.controls.settings_control.settings_page import SettingsPage

if TYPE_CHECKING:
    from pyqtp.controls.lsp_control.lsp_hub import LspHub
    from pyqtp.controls.project_control.project_control import ProjectControl
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow


class LspRegistrySettingsPage(SettingsPage):
    """
    Страница «Языковые серверы (LSP)».

    Редактирование стека LSP-серверов целиком в JSON-виде. Конфигурация
    валидируется ``LspRegistry.from_json``: при битом JSON ``apply()``
    бросает ошибку, действующий реестр не меняется.

    Область ``app`` — реестр в настройках ПРИЛОЖЕНИЯ (QSettings);
    область ``project`` — перекрытие реестра открытым проектом
    (``.pyqtp/settings.json``, секция ``lsp.registry_json``): показываются
    эффективные значения, «Применить» пишет перекрытие проекта, «Сбросить»
    снимает его (реестр приложения снова становится эффективным).
    Применённый реестр используется новыми подключениями редакторов; уже
    поднятые процессы серверов живут до завершения IDE.
    """

    title = 'Языковые серверы (LSP)'
    order = 50
    scope_applicable = True

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
        if self.scope == SCOPE_PROJECT:
            # эффективный реестр: проектное перекрытие, а где его нет —
            # слой приложения (в виде, в котором он реально действует)
            self._editor.setPlainText(self._hub.registry_json())
        else:
            # слой настроек приложения, а не эффективный реестр: страница
            # не должна копировать проектное перекрытие в настройки приложения
            self._editor.setPlainText(self._hub.registry_app_json())

    def apply(self) -> None:
        assert self._editor is not None
        # ValueError/TypeError при битом конфиге уходит в окно настроек;
        # действующий реестр остаётся нетронутым
        if self.scope == SCOPE_PROJECT:
            # валидация JSON + запись перекрытия проекта (одна запись файла);
            # при ошибке — источник не тронут
            self._project_ctrl().project_settings_update(
                SECTION_LSP, {KEY_REGISTRY_JSON: self._editor.toPlainText()}
            )
        else:
            self._hub.registry_apply_json(self._editor.toPlainText())

    def reset_defaults(self) -> None:
        if self.scope == SCOPE_PROJECT:
            # снимает перекрытие реестра у проекта (реестр приложения живёт)
            self._project_ctrl().project_settings_reset(SECTION_LSP)
        else:
            self._hub.registry_reset()
        self.load()

    def _project_ctrl(self) -> ProjectControl:
        # страница живёт в IDE: главное окно здесь всегда IdeMainWindow
        return cast('IdeMainWindow', self.main_wnd).project_ctrl
