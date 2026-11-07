from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QToolBar

    from pyqtp.base.docking_system.base_dock_widget import BaseDockWidget
    from pyqtp.base.docking_system.base_group_widget import BaseGroupWidget
    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.theme.theme import Theme
    from pyqtp.utils.priority_menu import PriorityMenu
    from pyqtp.views.base_view import BaseView


class BaseViewsGroup:
    group_dock: BaseDockWidget

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        raise NotImplementedError

    def view_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal[
            'floating', 'top', 'bottom', 'left', 'right', 'center'
        ]
        | None = None,
        add_toggle_action_into_toolbar: bool = True,
        delete_on_close: bool = False,
    ) -> BaseDockWidget:
        raise NotImplementedError


class BaseViewsManager:
    """
    Базовый класс для реализации dock виджетов

    Создает меню и toolbar "Вид" в конструкторе

    Args:
        main_wnd(PluggableMainWindow): основное окно

    Attributes:
        dock_manager: класс для управления окнами, зависит от реализации

    """

    views_menu: PriorityMenu
    views_toolbar: QToolBar
    dock_manager: Any

    def __init__(self, main_wnd: PluggableMainWindow):
        self.views_menu = main_wnd.menuBar().addMenu('Вид', priority=-5)
        self.views_toolbar = main_wnd.addToolBar('Вид')
        self.views_toolbar.setObjectName('ViewsToolBar')

    def register_group(
        self,
        *,
        main_wnd: PluggableMainWindow,
        group_widget: BaseGroupWidget,
    ) -> BaseViewsGroup:
        raise NotImplementedError

    def deregister_group(self, *, group_id: str) -> None:
        raise NotImplementedError

    def view_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal[
            'floating', 'top', 'bottom', 'left', 'right', 'center'
        ]
        | None = None,
        add_toggle_action_into_toolbar: bool = True,
        delete_on_close: bool = False,
        group_id: str | None = None,
    ) -> BaseDockWidget:
        raise NotImplementedError

    def view_deregister(
        self, key: str, group_id: str | None = None
    ) -> BaseView:
        raise NotImplementedError

    def view_close(self, key: str, group_id: str | None = None) -> None:
        raise NotImplementedError

    def view_show(self, key: str, group_id: str | None = None) -> None:
        raise NotImplementedError

    def view_hide(self, key: str, group_id: str | None = None) -> None:
        raise NotImplementedError

    def view_get(self, key: str, group_id: str | None) -> BaseView:
        raise NotImplementedError

    def view_set_modal(self, key: str | None, group_id: str | None = None):
        raise NotImplementedError

    def focused_view_get(self) -> BaseView | None:
        raise NotImplementedError

    def state_read(self, settings: QSettings) -> None:
        raise NotImplementedError

    def state_write(self, settings: QSettings) -> None:
        raise NotImplementedError

    def view_register_central(self, view: BaseView) -> BaseDockWidget:
        """
        Выставляет вьюшка как центральный виджет.

        Должен быть вызван до регистрации остальных вьюшек.
        """
        raise NotImplementedError

    def set_style_sheet(self, qss: str) -> None:
        raise NotImplementedError

    def style_sheet(self) -> str:
        raise NotImplementedError

    def set_theme(self, theme: Theme) -> None:
        raise NotImplementedError

    def show_central_widget(self):
        raise NotImplementedError

    def view_central_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal["floating", "top", "bottom", "left", "right", "center"]
        | None = None,
        close_cb=None,
    ) -> BaseDockWidget | None:
        raise NotImplementedError


class ViewsManagerType(StrEnum):
    """Типы реализации dock систем через различные библиотеки"""

    ads = 'PySide6QtAds'
    kdd = 'PyKDDockWidgets'


def create_views_manager(
    main_wnd: PluggableMainWindow, views_manager_type: ViewsManagerType
) -> BaseViewsManager:
    """
    Создает dock систему в зависимости от реализации.

    Args:
        main_wnd(PluggableMainWindow): окно, на которое добавляется dock система.
        views_manager_type(ViewsManagerType): реализация dock системы.

    Returns:
        BaseViewsManager: в зависимости от реализации.

    """
    match views_manager_type:
        case ViewsManagerType.ads:
            from pyqtp.base.docking_system.ads.ads_views_manager import (  # noqa: PLC0415
                ViewsManagerAds,
            )

            return ViewsManagerAds(main_wnd=main_wnd)
        case ViewsManagerType.kdd:
            from pyqtp.base.docking_system.kdd.kdd_views_manager import (  # noqa: PLC0415
                ViewsManagerKDD,
            )

            return ViewsManagerKDD(main_wnd=main_wnd)
