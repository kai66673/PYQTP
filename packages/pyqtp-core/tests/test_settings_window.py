"""Тесты окна настроек (core-каркас + эталонная страница темы).

Перенос харнеса /tmp/opencode/smoke_settings.py в pytest.
Окно строится напрямую (без launcher.py): offscreen-платформа из корневого
conftest, изоляция QSettings — уникальный org/app на тест с зачисткой.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from pyqtp.base.base_main_window import BaseMainWindow
from pyqtp.controls.settings_control.settings_page import SettingsPage


class BrokenPage(SettingsPage):
    """Страница, бросающая ValueError из apply() — для проверки статуса окна."""

    title = 'Сломанная'
    order = 99

    def create_page_widget(self) -> QWidget:
        return QWidget()

    def apply(self) -> None:
        msg = 'сломанный конфиг'
        raise ValueError(msg)


@pytest.fixture
def org_app(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    """Уникальное пространство QSettings на тест, зачищаемое до и после."""
    org, app_name = 'PYQTP_TESTS', f'TEST_{request.node.name}'
    settings = QSettings(org, app_name)
    settings.clear()
    settings.sync()
    yield org, app_name
    settings.clear()
    settings.sync()


@pytest.fixture
def wnd(org_app: tuple[str, str], qapp: QApplication) -> Iterator[Any]:
    """BaseMainWindow с полным жизненным циклом (как в смоуке).

    Зависимость от ``qapp`` обязательна: без QApplication конструктор
    QMainWindow аварийно завершает процесс (qFatal без исключения в Python).
    """
    org, app_name = org_app
    window = BaseMainWindow(org_name=org, app_name=app_name)
    args = window.settings_parse_argv([])
    window.settings_read(**vars(args))
    window.setup()
    window.setup_ui()
    yield window
    window.close()


class TestRegistryCore:
    """Реестр страниц без открытого окна."""

    def test_appearance_registered(self, wnd) -> None:
        keys = [p.key for p in wnd.settings_ctrl.pages_sorted()]
        assert keys == ['appearance']

    def test_duplicate_key_rejected(self, wnd) -> None:
        with pytest.raises(ValueError):
            wnd.settings_ctrl.page_register(SettingsPage(key='appearance'))


class TestSettingsWindow:
    """Живое окно настроек: список, кнопки, apply/revert/reset, поиск."""

    def test_show_list_and_buttons(self, wnd) -> None:
        ctrl = wnd.settings_ctrl
        ctrl.settings_window_show()
        win = ctrl._window
        assert win is not None and win.isVisible()
        titles = [
            win._pages_list.item(i).text() for i in range(win._pages_list.count())
        ]
        assert titles == ['Внешний вид']
        win._pages_list.setCurrentRow(0)
        assert win._apply_btn.isEnabled()
        assert win._revert_btn.isEnabled()
        assert win._reset_btn.isEnabled()

    def test_apply_and_revert_theme(self, wnd) -> None:
        ctrl = wnd.settings_ctrl
        ctrl.settings_window_show()
        win = ctrl._window
        assert win is not None
        win._pages_list.setCurrentRow(0)

        page = ctrl.page_get('appearance')
        assert page is not None
        combo = page.widget.findChild(QComboBox)
        assert combo is not None
        names = wnd.theme_manager.theme_names
        other = next(n for n in names if n != wnd.theme_manager.current_theme.name)

        # применяем другую тему кнопкой окна
        combo.setCurrentText(other)
        win._on_apply()
        assert wnd.theme_manager.current_theme.name == other
        assert 'применены' in win._status_label.text()
        # комбо смены темы в углу меню синхронен
        assert wnd.theme_switch_toolbar.theme_combo_box.currentText() == other

        # откат: ставим в комбо другое значение и грузим заново из настроек
        combo.setCurrentText(names[0])
        win._on_revert()
        assert combo.currentText() == other

    def test_broken_apply_filter_deregister(self, wnd) -> None:
        ctrl = wnd.settings_ctrl
        ctrl.settings_window_show()
        win = ctrl._window
        assert win is not None
        win._pages_list.setCurrentRow(0)

        broken = BrokenPage()
        ctrl.page_register(broken)
        win._pages_list.setCurrentRow(win._row_for_key(broken.key))
        win._on_apply()
        status = win._status_label.text()
        assert 'Ошибка' in status
        assert 'сломанный конфиг' in status

        # поиск скрывает лишние строки
        win._apply_filter('слом')
        hidden = [
            win._pages_list.item(i).isHidden()
            for i in range(win._pages_list.count())
        ]
        assert hidden == [True, False]  # appearance скрыта, broken видна
        win._apply_filter('')

        # снятие с регистрации перестраивает список
        ctrl.page_unregister(broken.key)
        titles = [
            win._pages_list.item(i).text() for i in range(win._pages_list.count())
        ]
        assert titles == ['Внешний вид']

    def test_reset_defaults_theme(self, wnd) -> None:
        ctrl = wnd.settings_ctrl
        ctrl.settings_window_show()
        win = ctrl._window
        assert win is not None
        win._pages_list.setCurrentRow(0)
        win._on_reset_defaults()
        assert wnd.theme_manager.current_theme.name == 'Светлая'
        assert wnd.theme_switch_toolbar.theme_combo_box.currentText() == 'Светлая'

    def test_hide_show_state_persisted(self, wnd, org_app: tuple[str, str]) -> None:
        org, app_name = org_app
        ctrl = wnd.settings_ctrl
        ctrl.settings_window_show()
        win = ctrl._window
        assert win is not None
        win._pages_list.setCurrentRow(0)

        # закрытие окна = скрытие, состояние — в QSettings
        win._hide_window()
        assert not win.isVisible()
        saved = QSettings(org, app_name).value('SettingsControl/current_page', '', str)
        assert saved == 'appearance'

        # повторный показ — то же окно и та же строка списка
        ctrl.settings_window_show()
        assert ctrl._window is win
        assert win.isVisible()
        assert win._pages_list.currentRow() == 0
