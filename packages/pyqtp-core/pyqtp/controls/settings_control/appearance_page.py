from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.settings_control.settings_page import SettingsPage

DEFAULT_THEME_NAME = 'Светлая'


class AppearanceSettingsPage(SettingsPage):
    """
    Страница «Внешний вид».

    Эталонная страница системы настроек: тема применяется кнопкой
    «Применить» (в отличие от комбикоса в углу меню, который переключает
    тему мгновенно). Значение темы сохраняется общим механизмом
    ``theme_settings_write`` при закрытии приложения.
    """

    title = 'Внешний вид'
    order = 0

    def __init__(self) -> None:
        super().__init__(key='appearance')
        self._theme_combo: QComboBox | None = None

    def create_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(
            'Тема оформления применяется после нажатия «Применить» и '
            'сохраняется при закрытии приложения. Это настройка '
            'приложения: если для открытого проекта задана тема в его '
            'настройках (.pyqtp/settings.json), она перекрывает эту.',
            widget,
        )
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
        # слой настроек приложения, а не эффективная тема: страница не должна
        # копировать проектное перекрытие в настройки приложения (этап 9.6B)
        self._theme_combo.setCurrentText(self.main_wnd.theme_app_name)

    def apply(self) -> None:
        assert self._theme_combo is not None
        theme_name = self._theme_combo.currentText()
        if theme_name not in self.main_wnd.theme_manager.theme_names:
            error_message = f'Тема «{theme_name}» не найдена'
            raise ValueError(error_message)
        # правка слоя приложения; эффективную тему перекрытие проекта не
        # пробивает (хук theme_on_app_changed в IDE)
        self.main_wnd.theme_set_app(theme_name)

    def reset_defaults(self) -> None:
        if DEFAULT_THEME_NAME not in self.main_wnd.theme_manager.theme_names:
            return
        assert self._theme_combo is not None
        self._theme_combo.setCurrentText(DEFAULT_THEME_NAME)
        self.apply()
