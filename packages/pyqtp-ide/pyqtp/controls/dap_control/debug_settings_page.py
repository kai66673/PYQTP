from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from pyqtp.controls.project_control.project_control import SCOPE_PROJECT
from pyqtp.controls.project_control.project_settings import (
    KEY_BREAK_ON_RAISED,
    SECTION_DEBUG,
)
from pyqtp.controls.settings_control.settings_page import SettingsPage

if TYPE_CHECKING:
    from pyqtp.controls.dap_control.dap_control import DapControl
    from pyqtp.controls.project_control.project_control import ProjectControl
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow


class DebugSettingsPage(SettingsPage):
    """Страница «Отладка» (этап 11, срез D3).

    Одна опция — «Останавливаться на необработанных исключениях всегда»
    отсутствует по построению (``uncaught`` включён всегда); настраивается
    только ``break_on_raised`` — останавливаться ли и на ПОЛНОСТЬЮ
    перехваченных исключениях (фильтр DAP ``raised``).

    Область ``app`` — слой настроек приложения (QSettings, группа
    ``DapOptions``); область ``project`` — перекрытие открытого проекта
    (``.pyqtp/settings.json``, секция ``debug.break_on_raised``): load
    показывает ЭФФЕКТИВное значение, «Применить» пишет слой проекта,
    «Сбросить» снимает перекрытие (общее правило двухслойности 9.6B).
    Смена фильтра в живой сессии переприменяется автоматически
    (``setExceptionBreakpoints``).
    """

    title = 'Отладка'
    order = 60
    scope_applicable = True

    _HINT = (
        'Остановка на необработанных исключениях включена всегда. '
        'Флаг ниже добавляет останов и на перехваченных исключениях '
        '(try/except) — удобно искать проглоченные ошибки.'
    )

    def __init__(self, ctrl: DapControl) -> None:
        super().__init__(key='debug')
        self._ctrl = ctrl
        self._check: QCheckBox | None = None

    def create_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(self._HINT, widget)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._check = QCheckBox(
            'Останавливаться на всех исключениях (включая перехваченные)',
            widget,
        )
        layout.addWidget(self._check)
        layout.addStretch(1)

        self.load()
        return widget

    def load(self) -> None:
        assert self._check is not None
        # в обеих областях грузим ЭФФЕКТИВное значение: проектное перекрытие
        # старее слоя приложения, и в области «Проект» показываем то, что
        # реально действует (как страницы «Редактор»/«LSP» из 9.6B2)
        options = self._ctrl.debug_options()
        self._check.setChecked(bool(options[KEY_BREAK_ON_RAISED]))

    def apply(self) -> None:
        assert self._check is not None
        value = self._check.isChecked()
        if self.scope == SCOPE_PROJECT:
            # запись строгая: ValueError при незакрытом проекте уходит в окно
            self._project_ctrl().project_settings_set(
                SECTION_DEBUG, KEY_BREAK_ON_RAISED, value,
            )
        else:
            # слой приложения (переприменение фильтров в живую сессию — на
            # самом контроле)
            self._ctrl.set_debug_option_app(KEY_BREAK_ON_RAISED, value)

    def reset_defaults(self) -> None:
        if self.scope == SCOPE_PROJECT:
            # снимает перекрытие проекта (флаг приложения остаётся)
            self._project_ctrl().project_settings_reset(SECTION_DEBUG)
        else:
            self._ctrl.set_debug_option_app(KEY_BREAK_ON_RAISED, value=False)
        self.load()

    def _project_ctrl(self) -> ProjectControl:
        # страница живёт в IDE: главное окно здесь всегда IdeMainWindow
        return cast('IdeMainWindow', self.main_wnd).project_ctrl
