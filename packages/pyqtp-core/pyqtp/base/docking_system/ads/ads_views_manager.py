from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Literal

import PySide6QtAds as ads  # noqa: N813
from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer
from PySide6.QtWidgets import QLabel, QToolBar, QToolButton, QVBoxLayout
from qtpy import shiboken

from bcad.base.docking_system.ads.ads_qss_editor import (
    AdsQssEditor,
    _default_ads_qss,
)
from bcad.base.docking_system.base_group_widget import BaseGroupWidget
from bcad.base.docking_system.base_views_manager import (
    BaseViewsGroup,
    BaseViewsManager,
)
from bcad.views.base_view import BaseView

if TYPE_CHECKING:
    from bcad.base.docking_system.base_dock_widget import BaseDockWidget
    from bcad.base.pluggable_main_window import PluggableMainWindow
    from bcad.theme.theme import Theme
    from bcad.utils.priority_menu import PriorityMenu

_ADS_AREA_MAPPING = {
    None: ads.NoDockWidgetArea,
    'floating': ads.NoDockWidgetArea,
    'top': ads.TopDockWidgetArea,
    'bottom': ads.BottomDockWidgetArea,
    'left': ads.LeftDockWidgetArea,
    'right': ads.RightDockWidgetArea,
    'center': ads.CenterDockWidgetArea,
}


def title_bar_update(main_wnd, floating_container, dock_widget):
    def change_btn_style(button: QToolButton):
        is_maximized = floating_container.isMaximized()
        button.setToolTip('Свернуть' if is_maximized else 'Развернуть')
        button.setIcon(
            main_wnd.theme_manager.icon(
                'detach_btn.svg' if is_maximized else 'max_btn.svg'
            )
        )
        button.setIconSize(QSize(12, 12))

    def help_clicked(dock_wgt):
        if isinstance(dock_wgt, (BaseView, BaseGroupWidget)):
            main_wnd.help_requested.emit(dock_wgt.help_context)

    floating_title_bar = floating_container.titleBarWidget()
    floating_title_bar.layout().setSpacing(2)

    for btn in floating_title_bar.findChildren(QToolButton):
        if btn.objectName() == 'floatingTitleCloseButton':
            btn.setIcon(main_wnd.theme_manager.icon('close_btn.svg'))
            btn.setIconSize(QSize(12, 12))
            btn.setToolTip('Закрыть')
        elif btn.objectName() == 'floatingTitleMaximizeButton':
            btn.setIcon(main_wnd.theme_manager.icon('max_btn.svg'))
            btn.setIconSize(QSize(12, 12))
            btn.setToolTip('Развернуть')
            btn.clicked.connect(partial(change_btn_style, btn))

    if dock_widget is None:
        return

    if not floating_title_bar.findChildren(QLabel, 'window_icon_label'):
        window_icon = dock_widget.widget().windowIcon()
        window_icon_label = QLabel()
        window_icon_label.setObjectName('window_icon_label')
        window_icon_label.setPixmap(window_icon.pixmap(16, 16))
        floating_title_bar.layout().insertWidget(0, window_icon_label)

    if not floating_title_bar.findChildren(QToolButton, 'help_button'):
        help_button = QToolButton(dock_widget)
        help_button.setObjectName('help_button')
        help_button.setToolTip('Справка')
        help_button.setIcon(main_wnd.theme_manager.icon('help_context.svg'))
        help_button.setIconSize(QSize(18, 18))
        help_button.setAutoRaise(True)
        help_button.clicked.connect(partial(help_clicked, dock_widget.widget()))

        floating_title_bar.layout().insertWidget(3, help_button)


class AdsViewsGroup(BaseViewsGroup):
    group_id: str

    dock_manager: ads.CDockManager  # type: ignore[reportInvalidTypeForm]
    _docks: dict[str, ads.CDockWidget]  # type: ignore[reportInvalidTypeForm]
    views_menu: PriorityMenu
    views_toolbar: QToolBar

    group_dock: ads.CDockWidget  # type: ignore[reportInvalidTypeForm]
    group_widget: BaseGroupWidget
    group_layout: QVBoxLayout

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        group_widget: BaseGroupWidget,
    ):
        self.main_wnd = main_wnd

        self.group_id = group_widget.group_id
        self._docks: dict[str, ads.CDockWidget] = {}  # type: ignore[reportInvalidTypeForm]

        views_manager = main_wnd.views_manager
        group_name = group_widget.windowTitle()
        self.group_dock = ads.CDockWidget(group_name)
        views_manager.dock_manager.addDockWidget(
            ads.BottomDockWidgetArea, self.group_dock
        )

        self.group_widget = group_widget
        self.group_dock.setWidget(self.group_widget)
        self.group_dock.setIcon(group_widget.windowIcon())
        self.group_layout = QVBoxLayout(self.group_widget)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.dock_manager = ads.CDockManager(self.group_widget)
        self.group_layout.addWidget(self.dock_manager)

        toggle_view_action = self.group_dock.toggleViewAction()
        toggle_view_action.setIcon(group_widget.windowIcon())

        self.views_menu = views_manager.views_menu.addMenu(group_name)
        self.views_toolbar = main_wnd.addToolBar(
            f'Панели группы "{group_name}"'
        )
        self.views_toolbar.setObjectName(f'ViewsToolBar_{self.group_id}')

        self.views_menu.addAction(toggle_view_action)
        self.views_menu.addSeparator()
        self.views_toolbar.addAction(toggle_view_action)
        self.views_toolbar.addSeparator()

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        self.group_dock.setWindowTitle(title)

    def deregister(self):
        self.dock_manager.removeDockWidget(self.group_dock)
        self.main_wnd.removeToolBar(self.views_toolbar)
        self.main_wnd.views_manager.views_menu.remove_menu(self.views_menu)

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
    ) -> ads.CDockWidget:  # type: ignore[reportInvalidTypeForm]
        """Регистрирует и оборачивает вьюшку в доквиджет."""
        if view.key in self._docks:
            error_message = f'View with key "{view.key}" already registered!'
            raise ValueError(error_message)

        title = view.windowTitle()
        dock = ads.CDockWidget(title)
        dock.setObjectName(f'{view.key}_dock')
        view.windowTitleChanged.connect(dock.setWindowTitle)
        toggle_action = dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setToolTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setStatusTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setIcon(view.windowIcon())
        dock.setIcon(view.windowIcon())
        dock.setFeature(ads.CDockWidget.DockWidgetFocusable, True)  # noqa: FBT003
        dock.setWidget(view)
        view.ensure_visible_requested.connect(dock.raise_)

        self.dock_manager.addDockWidget(_ADS_AREA_MAPPING[widget_area], dock)
        if not delete_on_close:
            self.views_menu.addAction(toggle_action)

        if delete_on_close:
            dock.setFeature(ads.CDockWidget.CustomCloseHandling, True)  # noqa: FBT003
            dock.closeRequested.connect(
                partial(self._handle_dock_close_requested, view.key)
            )
        elif add_toggle_action_into_toolbar:
            self.views_toolbar.addAction(toggle_action)
        self._docks[view.key] = dock

        view.state_read()
        view.setup()

        return dock

    def _handle_dock_close_requested(self, view_key: str):
        self.view_deregister(view_key)

    def view_deregister(self, key: str) -> BaseView:
        self.view_close(key)
        dock = self._docks.pop(key)
        toggle_action = dock.toggleViewAction()
        self.views_menu.removeAction(toggle_action)
        self.views_toolbar.removeAction(toggle_action)
        view = dock.widget()
        view.teardown()

        self.dock_manager.removeDockWidget(dock)
        view.setParent(None)

        return view

    def view_close(self, key: str) -> None:
        dock = self._docks[key]
        view = dock.widget()
        view.state_write()
        dock.closeDockWidget()

    def view_show(self, key: str) -> None:
        self.group_dock.toggleView(Open=True)
        self.group_dock.raise_()

        dock = self._docks[key]
        dock.toggleView(Open=True)
        dock.raise_()

    def view_hide(self, key: str) -> None:
        dock = self._docks[key]
        dock.toggleView(Open=False)

    def view_get(self, key: str) -> BaseView:
        return self._docks[key].widget()

    def state_read(self, settings: QSettings) -> None:
        # Состояние главного окна
        settings.beginGroup('MainWindow')
        self.dock_manager.restoreState(
            settings.value(f'{self.group_id}_dockState', QByteArray())
        )
        settings.endGroup()

        # Загружаем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_read()

    def state_write(self, settings: QSettings) -> None:
        # Состояние главного окна
        settings.beginGroup('MainWindow')
        settings.setValue(
            f'{self.group_id}_dockState', self.dock_manager.saveState()
        )
        settings.endGroup()

        # Сохраняем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_write()

    def _reset_dock_icons(self):
        for d in self._docks.values():
            d.setIcon(d.icon())
        self.group_dock.setIcon(self.group_dock.icon())


class CCustomComponentsFactory(ads.CDockComponentsFactory):
    def __init__(self, views_manager: ViewsManagerAds):
        super().__init__()
        self.views_manager = views_manager

    def createDockAreaTitleBar(  # noqa: N802
        self,
        dock_area: ads.CDockAreaWidget,  # type: ignore[reportInvalidTypeForm]
    ) -> ads.CDockAreaTitleBar:  # type: ignore[reportInvalidTypeForm]
        title_bar = ads.CDockAreaTitleBar(dock_area)

        for button in title_bar.findChildren(QToolButton):
            if button.objectName() == 'dockAreaCloseButton':
                button.setIcon(
                    self.views_manager.main_wnd.theme_manager.icon(
                        'close_btn.svg'
                    )
                )
                button.setIconSize(QSize(12, 12))
                button.setToolTip('Закрыть группу')
            elif button.objectName() == 'detachGroupButton':
                button.setIcon(
                    self.views_manager.main_wnd.theme_manager.icon(
                        'detach_btn.svg'
                    )
                )
                button.setIconSize(QSize(12, 12))
                button.setToolTip('Открепить группу')

        help_button = QToolButton(dock_area)
        help_button.setToolTip('Справка')
        help_button.setIcon(
            self.views_manager.main_wnd.theme_manager.icon('help_context.svg')
        )
        help_button.setIconSize(QSize(18, 18))
        help_button.setAutoRaise(True)
        help_button.clicked.connect(partial(self.help_clicked, title_bar))
        index = title_bar.indexOf(title_bar.button(ads.TitleBarButtonTabsMenu))
        title_bar.insertWidget(index, help_button)

        return title_bar

    def help_clicked(self, title_bar):
        dock_area = title_bar.parent()
        dock_widget = dock_area.currentDockWidget().widget()
        if isinstance(dock_widget, (BaseView, BaseGroupWidget)):
            self.views_manager.main_wnd.help_requested.emit(
                dock_widget.help_context
            )


class ViewsManagerAds(BaseViewsManager):
    dock_manager: ads.CDockManager  # type: ignore[reportInvalidTypeForm]

    _docks: dict[str, ads.CDockWidget]  # type: ignore[reportInvalidTypeForm]
    _groups: dict[str, AdsViewsGroup]

    def __init__(self, main_wnd: PluggableMainWindow):
        super().__init__(main_wnd)
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.FloatingContainerHasWidgetIcon, On=True
        )
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.FocusHighlighting, On=True
        )
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.FloatingContainerForceNativeTitleBar, On=False
        )
        ads.CDockManager.setConfigFlag(
            ads.CDockManager.FloatingContainerForceQWidgetTitleBar, On=True
        )

        self.main_wnd = main_wnd
        self.dock_manager = ads.CDockManager(main_wnd)
        self._docks: dict[str, ads.CDockWidget] = {}  # type: ignore[reportInvalidTypeForm]

        self.central_dock_widget = ads.CDockWidget('Central Widget')

        self._groups = {}

        self._modal_view_key, self._modal_group_id = None, None

        ads.CDockComponentsFactory.setFactory(CCustomComponentsFactory(self))

        self.main_wnd.theme_manager.register_theme_meta(
            ('Таблица стилей (ADS)', AdsQssEditor),
        )

        self.dock_manager.focusedDockWidgetChanged.connect(
            self.on_focused_dock_widget_changed
        )
        self.dock_manager.floatingWidgetCreated.connect(
            self._set_focused_floating_window
        )

    def _set_focused_floating_window(
        self,
        window: ads.CFloatingDockContainer,  # type: ignore[reportInvalidTypeForm]
    ):
        QTimer.singleShot(100, lambda: self._focus_floating_window(window))

    def _focus_floating_window(
        self,
        window: ads.CFloatingDockContainer,  # type: ignore[reportInvalidTypeForm]
    ):
        dock_widget = window.topLevelDockWidget()

        if dock_widget is None:
            return

        dock_widget.setWindowFlag(Qt.WindowType.Window, True)  # noqa: FBT003
        dock_widget.raise_()
        dock_widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def on_focused_dock_widget_changed(self, _, focused_dock_widget):
        # Откладываем выполнение до следующей итерации event loop
        QTimer.singleShot(
            0, lambda: self.change_floating_group_name(_, focused_dock_widget)
        )

    def change_floating_group_name(self, _, focused_dock_widget):
        if not shiboken.isValid(focused_dock_widget):
            return

        container = focused_dock_widget.floatingDockContainer()

        if container is None:
            return

        for dock_widget in container.dockWidgets():
            title_bar = dock_widget.dockAreaWidget().titleBar()

            detach_button = title_bar.findChild(
                QToolButton, 'detachGroupButton'
            )

            if detach_button:
                detach_button.setVisible(True)

        dock_widget = container.topLevelDockWidget()

        title_bar_update(
            main_wnd=self.main_wnd,
            floating_container=container,
            dock_widget=dock_widget,
        )

        if len(container.dockWidgets()) > 1 and container.isFloating():
            floating_title_bar = container.titleBarWidget()

            title_label = floating_title_bar.findChild(
                ads.CElidingLabel, 'floatingTitleLabel'
            )
            title_label.setText(focused_dock_widget.windowTitle())

            window_icon_label = floating_title_bar.findChild(
                QLabel, 'window_icon_label'
            )

            if window_icon_label is None:
                window_icon_label = QLabel()
                window_icon_label.setObjectName('window_icon_label')
                floating_title_bar.layout().insertWidget(0, window_icon_label)

            window_icon = focused_dock_widget.widget().windowIcon()
            window_icon_label.setPixmap(window_icon.pixmap(16, 16))

            help_button = floating_title_bar.findChild(
                QToolButton, 'help_button'
            )
            if help_button is None:
                help_button = QToolButton(focused_dock_widget)
                help_button.setObjectName('help_button')
                help_button.setToolTip('Справка')
                help_button.setIcon(
                    self.main_wnd.theme_manager.icon('help_context.svg')
                )
                help_button.setIconSize(QSize(18, 18))
                help_button.setAutoRaise(True)
                floating_title_bar.layout().insertWidget(3, help_button)
            help_button.clicked.connect(
                partial(self.help_clicked, focused_dock_widget.widget())
            )

    def help_clicked(self, dock_widget):
        if isinstance(dock_widget, (BaseView, BaseGroupWidget)):
            self.main_wnd.help_requested.emit(dock_widget.help_context)

    def set_style_sheet(self, qss: str):
        self.dock_manager.setStyleSheet(qss)
        for group in self._groups.values():
            group.dock_manager.setStyleSheet(qss)

    def style_sheet(self) -> str:
        return self.dock_manager.styleSheet()

    def set_theme(self, theme: Theme) -> None:
        self.set_style_sheet(theme.meta.get('ads_qss', _default_ads_qss))
        QTimer.singleShot(0, lambda: self._reset_dock_icons())

    def _reset_dock_icons(self):
        for d in self._docks.values():
            d.setIcon(d.icon())

        for g in self._groups.values():
            g._reset_dock_icons()

    def register_group(
        self,
        *,
        main_wnd: PluggableMainWindow,
        group_widget: BaseGroupWidget,
    ) -> BaseViewsGroup:
        new_group = AdsViewsGroup(main_wnd, group_widget)
        new_group.dock_manager.setStyleSheet(self.style_sheet())
        self._groups[group_widget.group_id] = new_group
        return new_group

    def deregister_group(self, *, group_id: str):
        group_view = self._groups.pop(group_id)
        group_view.deregister()

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
        if group_id:
            view.on_modality_changed(
                is_modal=bool(self._modal_view_key),
                is_self_modal=group_id == self._modal_group_id
                and view.key == self._modal_view_key,
            )

            return self._groups[group_id].view_register(
                view=view,
                widget_area=widget_area,
                add_toggle_action_into_toolbar=add_toggle_action_into_toolbar,
                delete_on_close=delete_on_close,
            )

        view.on_modality_changed(
            is_modal=bool(self._modal_view_key),
            is_self_modal=group_id is None and view.key == self._modal_view_key,
        )

        """Регистрирует и оборачивает вьюшку в доквиджет."""
        if view.key in self._docks:
            error_message = f'View with key "{view.key}" already registered!'
            raise ValueError(error_message)

        title = view.windowTitle()
        dock = ads.CDockWidget(title)
        dock.setObjectName(f'{view.key}_dock')
        view.windowTitleChanged.connect(dock.setWindowTitle)
        toggle_action = dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setToolTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setStatusTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setIcon(view.windowIcon())
        dock.setIcon(view.windowIcon())
        dock.setFeature(ads.CDockWidget.DockWidgetFocusable, True)  # noqa: FBT003
        dock.setWidget(view)
        view.ensure_visible_requested.connect(
            lambda: dock.raise_() or dock.toggleView(Open=True)
        )

        self.dock_manager.addDockWidget(_ADS_AREA_MAPPING[widget_area], dock)
        if not delete_on_close:
            self.views_menu.addAction(toggle_action)

        if delete_on_close:
            dock.setFeature(ads.CDockWidget.CustomCloseHandling, True)  # noqa: FBT003
            dock.closeRequested.connect(
                partial(self._handle_dock_close_requested, view.key)
            )
        elif add_toggle_action_into_toolbar:
            self.views_toolbar.addAction(toggle_action)
        self._docks[view.key] = dock

        view.state_read()
        view.setup()

        return dock

    def _handle_dock_close_requested(self, view_key: str):
        self.view_deregister(view_key)

    def view_deregister(
        self, key: str, group_id: str | None = None
    ) -> BaseView:
        if group_id:
            return self._groups[group_id].view_deregister(key)

        self.view_close(key)
        dock = self._docks.pop(key)
        toggle_action = dock.toggleViewAction()
        self.views_menu.removeAction(toggle_action)
        self.views_toolbar.removeAction(toggle_action)
        view = dock.widget()
        view.teardown()

        self.dock_manager.removeDockWidget(dock)

        view.setParent(None)

        return view

    def view_close(self, key: str, group_id: str | None = None) -> None:
        if group_id:
            self._groups[group_id].view_close(key)
            return

        dock = self._docks[key]
        view = dock.widget()
        view.state_write()
        dock.closeDockWidget()

    def view_show(self, key: str, group_id: str | None = None) -> None:
        if group_id:
            self._groups[group_id].view_show(key)
            return

        dock = self._docks[key]
        dock.toggleView(Open=True)
        dock.raise_()

    def view_hide(self, key: str, group_id: str | None = None) -> None:
        if group_id:
            self._groups[group_id].view_hide(key)
            return

        dock = self._docks[key]
        dock.toggleView(Open=False)

    def view_get(self, key: str, group_id: str | None) -> BaseView:
        if group_id:
            return self._groups[group_id].view_get(key)
        return self._docks[key].widget()

    def focused_view_get(self) -> BaseView | None:
        for group in self._groups.values():
            gd = group.dock_manager.focusedDockWidget()
            if gd is not None:
                gv = gd.widget()
                if isinstance(gv, BaseView):
                    return gv
                return None

        focused_dock = self.dock_manager.focusedDockWidget()
        if focused_dock is not None:
            focused_view = focused_dock.widget()
            if isinstance(focused_view, BaseView):
                return focused_view

        return None

    def view_set_modal(self, key: str | None, group_id: str | None = None):
        self._modal_view_key, self._modal_group_id = key, group_id
        for view_key, dock in self._docks.items():
            view = dock.widget()
            assert isinstance(view, BaseView)
            view.on_modality_changed(
                is_modal=bool(key),
                is_self_modal=group_id is None and view_key == key,
            )

        for g_id, group in self._groups.items():
            assert isinstance(group, AdsViewsGroup)
            for group_view_key, group_dock in group._docks.items():
                group_view = group_dock.widget()
                assert isinstance(group_view, BaseView)
                group_view.on_modality_changed(
                    is_modal=bool(key),
                    is_self_modal=group_id == g_id and group_view_key == key,
                )

    def state_read(self, settings: QSettings) -> None:
        # Состояние главного окна
        settings.beginGroup('MainWindow')
        self.dock_manager.restoreState(
            settings.value('dockState', QByteArray())
        )
        settings.endGroup()

        # Загружаем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_read()

        # Загружаем состояния групп
        for group in self._groups.values():
            group.state_read(settings)

    def state_write(self, settings: QSettings) -> None:
        # Состояние главного окна
        settings.beginGroup('MainWindow')
        settings.setValue('dockState', self.dock_manager.saveState())
        settings.endGroup()

        # Сохраняем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_write()

        # Сохраняем состояния групп
        for group in self._groups.values():
            group.state_write(settings)

    def view_register_central(self, view: BaseView) -> ads.CDockWidget:  # type: ignore[reportInvalidTypeForm]
        """
        Выставляет вьюшка как центральный виджет.

        Должен быть вызван до регистрации остальных вьюшек.
        """
        self.dock_manager.setCentralWidget(self.central_dock_widget)
        self.central_dock_widget.setWidget(view)
        view.state_read()
        view.setup()
