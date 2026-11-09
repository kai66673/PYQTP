from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.project_control.project_control import SCOPE_PROJECT
from pyqtp.controls.project_control.project_settings import (
    KEY_THEME_NAME,
    SECTION_APPEARANCE,
)
from pyqtp.controls.settings_control.appearance_page import (
    AppearanceSettingsPage,
)

if TYPE_CHECKING:
    from pyqtp.controls.project_control.project_control import ProjectControl
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow


class AppearanceScopePage(AppearanceSettingsPage):
    """
    Страница «Внешний вид» с селектором области (этап 9.6B2).

    Заменяет core-страницу в IDE: та же клавиша/порядок, но страница
    умеет править оба слоя темы. Область ``app`` — тема в настройках
    приложения (поведение core-страницы); область ``project`` — тема-
    перекрытие открытого проекта (``.pyqtp/settings.json``, секция
    ``appearance.theme_name``): без перекрытия показывается тема приложения
    (она сейчас и эффективна), «Применить» пишет перекрытие проекта,
    «Сбросить» снимает его. Тот же принцип маршрутизации, что у комбика
    темы в углу меню (``IdeMainWindow.handle_theme_switch``).
    """

    scope_applicable = True

    _HINT = (
        'Тема оформления применяется после нажатия «Применить». '
        'Область «Проект» — тема-перекрытие для открытого проекта '
        '(.pyqtp/settings.json), оно старше настроек приложения; '
        'область «Приложение» — общая тема: по умолчанию для всех '
        'проектов и для проектов без перекрытия.'
    )

    def create_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(self._HINT, widget)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._theme_combo = QComboBox(widget)
        self._theme_combo.addItems(self.main_wnd.theme_manager.theme_names)
        form.addRow('Тема оформления:', self._theme_combo)
        layout.addLayout(form)

        layout.addStretch()
        self.load()
        return widget

    def load(self) -> None:
        assert self._theme_combo is not None
        self._theme_combo.setCurrentText(self._scope_theme_value())

    def apply(self) -> None:
        assert self._theme_combo is not None
        theme_name = self._theme_combo.currentText()
        if theme_name not in self.main_wnd.theme_manager.theme_names:
            error_message = f'Тема «{theme_name}» не найдена'
            raise ValueError(error_message)
        if self.scope == SCOPE_PROJECT:
            # правка перекрытия проекта (валидация имени — в нём же);
            # слой приложения не трогается
            self._project_ctrl().project_settings_update(
                SECTION_APPEARANCE, {KEY_THEME_NAME: theme_name}
            )
        else:
            # правка слоя приложения; эффективную тему перекрытие проекта
            # не «пробивает» (хук theme_on_app_changed в IDE)
            self.main_wnd.theme_set_app(theme_name)

    def reset_defaults(self) -> None:
        if self.scope == SCOPE_PROJECT:
            # снимает тему-перекрытие у проекта (эффективной становится тема
            # приложения), а не пишет «Светлая» как перекрытие
            self._project_ctrl().project_settings_reset(SECTION_APPEARANCE)
            self.load()
        else:
            super().reset_defaults()

    def _scope_theme_value(self) -> str:
        """Какую тему показать в комбике для текущей области страницы."""
        if self.scope == SCOPE_PROJECT:
            values: dict[str, Any] = self._project_ctrl(
            ).project_settings_section(SECTION_APPEARANCE)
            name = values.get(KEY_THEME_NAME)
            if (
                isinstance(name, str)
                and name in self.main_wnd.theme_manager.theme_names
            ):
                return name
            # перекрытия нет (или оно битое) — показываем тему приложения:
            # именно она сейчас эффективна
        return self.main_wnd.theme_app_name

    def _project_ctrl(self) -> ProjectControl:
        # страница живёт в IDE: главное окно здесь всегда IdeMainWindow
        return cast('IdeMainWindow', self.main_wnd).project_ctrl
