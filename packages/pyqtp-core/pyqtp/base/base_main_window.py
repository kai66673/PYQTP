from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

from pyqtp.base.pluggable_main_window import PluggableMainWindow
from pyqtp.controls.api_control.api_control import APIControl
from pyqtp.controls.events_control.events_control import EventsControl
from pyqtp.controls.help_control.help_control import HelpControl
from pyqtp.controls.plugins_control.user_plugin_control import UserPluginControl
from pyqtp.controls.settings_control.appearance_page import (
    AppearanceSettingsPage,
)
from pyqtp.controls.settings_control.settings_control import SettingsControl
from pyqtp.utils.styles.style_setting_registry import (
    StyleSettingDescriptor,
    StyleSettingsGroup,
)
from pyqtp.utils.styles.style_value_editors import QssStringValueEditBehavior
from pyqtp.utils.styles.styled_app import StyledApplication

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyqtp.controls.settings_control.settings_page import SettingsPage


class BaseMainWindow(PluggableMainWindow):
    events_ctrl: EventsControl
    api_ctrl: APIControl
    help_ctrl: HelpControl
    settings_ctrl: SettingsControl

    plug_ctrl: UserPluginControl

    def init_controls(self) -> None:
        # Базовые контролы
        self.events_ctrl = self.plug_register(EventsControl())
        self.api_ctrl = self.plug_register(APIControl())

        # Контекстные контролы
        self.plug_ctrl = self.plug_register(UserPluginControl())
        self.help_ctrl = self.plug_register(HelpControl())
        self.settings_ctrl = self.plug_register(SettingsControl())
        self.settings_ctrl.page_register(AppearanceSettingsPage())

    ############################################
    # API регистрации страниц настроек в приложении #
    ############################################

    def settings_page_register(self, page: SettingsPage) -> SettingsPage:
        """Регистрирует страницу настроек в окне настроек приложения."""
        return self.settings_ctrl.page_register(page)

    def settings_page_unregister(self, key: str) -> None:
        """Снимает страницу настроек с регистрации."""
        self.settings_ctrl.page_unregister(key)

    def settings_scope_register(
        self,
        key: str,
        label: str,
        is_available: Callable[[], bool] | None = None,
    ) -> None:
        """Объявляет область настроек для селектора окна (этап 9.6B)."""
        self.settings_ctrl.scope_register(key, label, is_available)

    def settings_scopes_refresh(self) -> None:
        """Пересобирает селектор области открытого окна настроек."""
        self.settings_ctrl.scopes_refresh()

    def setup(self) -> None:
        super().setup()

        # Регистрируем API главного окна
        self.register_main_wnd_api()

        # Плагины, загружаемые по умолчанию (отключаемые)...

    def register_main_wnd_api(self) -> None:
        # Регистрируем API для работы с плагинами и представлениями в Python консоли
        self.api_ctrl.register_variables(
            {
                'plugin_list': lambda: self.plugins,
                'plug_register': self.plug_register,
                'plug_deregister': self.plug_deregister,
                'view_register': self.view_register,
                'view_deregister': self.view_deregister,
                'view_get': self.view_get,
                'view_close': self.view_close,
                'view_show': self.view_show,
                'view_hide': self.view_hide,
                'focused_view_get': self.views_manager.focused_view_get,
                'settings_page_register': self.settings_page_register,
                'settings_page_unregister': self.settings_page_unregister,
                'settings_window_show': self.settings_ctrl.settings_window_show,
            }
        )

        # Регистрируем API-нотификаций в Python консоли
        self.api_ctrl.register_variables(
            {
                'events_notify': self.events_ctrl.events_notify,
                'events_show_error_popup': self.events_ctrl.events_show_exception_popup,
                'events_log_register': self.events_ctrl.events_log_register,
                'events_log_clear': self.events_ctrl.events_log_clear,
            }
        )

        # Регистрируем вспомогательное API в Python консоли
        self.api_ctrl.register_variables(
            {
            }
        )

        # Регистрируем вспомогательное API настройки стилей в Python консоли
        win_app = QApplication.instance()
        if isinstance(win_app, StyledApplication):
            win_app.register_style_group(
                StyleSettingsGroup(
                    name='Стыкуемые панели',
                    settings=[
                        StyleSettingDescriptor(
                            name='Таблица стилей',
                            editor_cls=QssStringValueEditBehavior,
                            editor_init_kwargs={},
                        ),
                    ],
                    getter=self.get_dock_system_style_settings,
                    setter=self.set_dock_system_style_settings,
                )
            )

            self.api_ctrl.register_variables(
                {
                    'edit_current_theme': self.edit_current_theme,
                }
            )

    def edit_current_theme(self):
        self.theme_manager.edit_current_theme(self)

    def set_dock_system_style_settings(
        self, style_settings: list[int | float | str]
    ) -> None:
        assert len(style_settings) == 1
        qss = style_settings[0]
        assert isinstance(qss, str)
        self.views_manager.set_style_sheet(qss)

    def get_dock_system_style_settings(self) -> list[int | float | str]:
        return [
            self.views_manager.style_sheet(),
        ]
