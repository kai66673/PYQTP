from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Literal

import PyKDDockWidgets.KDDockWidgets as kdd  # type: ignore[reportMissingImports] # noqa: N813
from PySide6.QtCore import QByteArray, QSettings
from PySide6.QtWidgets import QLabel

from pyqtp.base.docking_system.base_views_manager import (
    BaseViewsGroup,
    BaseViewsManager,
)
from pyqtp.base.docking_system.kdd.kdd_dock_widget import KddDockWidget
from pyqtp.views.base_view import BaseView

if TYPE_CHECKING:
    from pyqtp.base.docking_system.base_dock_widget import BaseDockWidget
    from pyqtp.base.docking_system.base_group_widget import BaseGroupWidget
    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.theme.theme import Theme


class KddViewsGroup(BaseViewsGroup):
    group_dock: KddDockWidget

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        group_widget: BaseGroupWidget,
    ):
        self.group_id = group_widget.group_id
        title = group_widget.windowTitle()

        self.dock_manager = kdd.MainWindow(
            f'KDDDockManager_{self.group_id}',
            options=kdd.MainWindowOption.MainWindowOption_None,
        )
        affinities = [self.group_id]
        self.dock_manager.setAffinities(affinities)
        self.group_dock = KddDockWidget(title)
        self.group_dock.setObjectName(f'{self.group_id}_dock')
        self.group_dock.setWidget(self.dock_manager)
        self.group_dock.setIcon(group_widget.windowIcon())

        main_wnd.views_manager.dock_manager.addKDockWidget(
            self.group_dock, kdd.Location.Location_OnRight
        )

        self._docks: dict[str, KddDockWidget] = {}

        toggle_action = self.group_dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setToolTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setStatusTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setIcon(group_widget.windowIcon())
        self.group_dock.setIcon(group_widget.windowIcon())

        views_manager = main_wnd.views_manager

        self.views_menu = views_manager.views_menu.addMenu(title)
        self.views_toolbar = main_wnd.addToolBar(f'Панели группы "{title}"')
        self.views_toolbar.setObjectName(f'ViewsToolBar_{self.group_id}')

        self.views_menu.addAction(toggle_action)
        self.views_menu.addSeparator()
        self.views_toolbar.addAction(toggle_action)
        self.views_toolbar.addSeparator()

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        # TODO: find out how dock may be renamed...
        pass

    def deregister(self):
        self.group_dock.closeDockWidget()

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
        """Регистрирует и оборачивает вьюшку в доквиджет."""
        if view.key in self._docks:
            error_message = f'View with key "{view.key}" already registered!'
            raise ValueError(error_message)

        title = view.windowTitle()
        dock_object_name = f'{view.key}_dock'
        dock = KddDockWidget(dock_object_name)
        dock.setObjectName(dock_object_name)
        toggle_action = dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setToolTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setStatusTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setIcon(view.windowIcon())
        dock.setTitle(title)
        dock.setIcon(view.windowIcon())
        dock.setWidget(view)
        view.ensure_visible_requested.connect(dock.raise_)

        match widget_area:
            case 'top':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnTop
                )
            case 'bottom':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnBottom
                )
            case 'left':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnLeft
                )
            case 'right':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnRight
                )
            case '_':
                dock.open()

        if not delete_on_close:
            self.views_menu.addAction(toggle_action)
        if delete_on_close:
            dock.setOptions(kdd.DockWidgetOption.DockWidgetOption_DeleteOnClose)
            dock.closeRequested.connect(
                partial(self._handle_dock_close_requested, view.key)
            )
        elif add_toggle_action_into_toolbar:
            self.views_toolbar.addAction(toggle_action)
        dock.setAffinities([self.group_id])
        self._docks[view.key] = dock

        view.state_read()
        view.setup()

        return dock

    def view_deregister(self, key: str) -> BaseView:
        self.view_close(key)
        dock = self._docks.pop(key)
        toggle_action = dock.toggleViewAction()
        self.views_menu.removeAction(toggle_action)
        view = dock.widget()
        view.teardown()
        view.setParent(None)
        dock.deleteLater()
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
        dock.setAsCurrentTab()
        dock.raise_()

    def view_hide(self, key: str) -> None:
        dock = self._docks[key]
        dock.toggleView(Open=False)

    def view_get(self, key: str) -> BaseView:
        return self._docks[key].widget()

    def _handle_dock_close_requested(self, view_key: str):
        self.view_deregister(view_key)

    def state_read(self, settings: QSettings) -> None:
        settings.beginGroup('MainWindowKDD')
        state = settings.value(f'{self.group_id}_dockState', QByteArray())
        settings.endGroup()
        if state:
            saver = kdd.LayoutSaver(
                options=kdd.RestoreOption.RestoreOption_None
            )
            saver.restoreLayout(state)

        # Загружаем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_read()

    def state_write(self, settings: QSettings) -> None:
        saver = kdd.LayoutSaver(options=kdd.RestoreOption.RestoreOption_None)
        settings.beginGroup('MainWindowKDD')
        settings.setValue(f'{self.group_id}_dockState', saver.serializeLayout())
        settings.endGroup()

        # Сохраняем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_write()


class ViewsManagerKDD(BaseViewsManager):
    """
    Реализация dock системы на основе библиотеки PyKDDockWidgets

    В конструкторе вызывается инициализация библиотеки KDD для QtWidgets.
    Пока ее не сделать все запросы к типам данных из kdd вернуть исключения.
    Нет реализации групп как в ads.

    Args:
        main_wnd(PluggableMainWindow): окно на которое добавляется dock система.

    Attributes:
        dock_manager(kdd.MainWindow): сделано для совместимости с ads. По идее PluggableMainWindow
                                      должно было бы наследоваться от kdd.MainWindow.
                                      К счастью KDD позволяет добавить это окно как обычный виджет.
        central_dock_widget(KddDockWidget): для установки центрального виджета в dock системе kdd. Пока не используется.

    """

    dock_manager: kdd.MainWindow
    _docks: dict[str, KddDockWidget]  # pyright: ignore[reportRedeclaration]
    central_dock_widget: KddDockWidget

    _groups: dict[str, KddViewsGroup]

    def __init__(self, main_wnd: PluggableMainWindow):
        kdd.initFrontend(kdd.FrontendType.QtWidgets)
        self._set_config_flags()

        self.dock_manager = kdd.MainWindow(
            'KDDDockManager',
            kdd.MainWindowOption.MainWindowOption_HasCentralWidget
        )
        main_wnd.setCentralWidget(self.dock_manager)
        self.dock_manager.setAffinities(["panels"])

        self._docks: dict[str, KddDockWidget] = {}
        self.documents_dock = {}

        super().__init__(main_wnd)

        self._groups = {}

    def set_style_sheet(self, qss: str):
        self.dock_manager.setStyleSheet(qss)
        for group in self._groups.values():
            group.dock_manager.setStyleSheet(qss)

    def style_sheet(self) -> str:
        return self.dock_manager.styleSheet()

    def set_theme(self, theme: Theme) -> None:
        pass

    def register_group(
        self,
        *,
        main_wnd: PluggableMainWindow,
        group_widget: BaseGroupWidget,
    ) -> BaseViewsGroup:
        self._groups[group_widget.group_id] = KddViewsGroup(
            main_wnd, group_widget
        )
        return self._groups[group_widget.group_id]

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
        """
        Регистрация вьюшки.

        Args:
            view(BaseView): вьюшка для регистрации
            widget_area(Literal["floating", "top", "bottom", "left", "right", "center"] | None): куда подключать виджет. "center" аналогичен "floating".
            add_toggle_action_into_toolbar(bool): добавление на toolbar
            delete_on_close(bool): удаление при закрытии, не тестировалось.
            group_id(str | None): не используется.

        Returns:
            KddDockWidget: DockWidget

        Raises:
            ValueError: если вюшка уже зарегестрирована

        """
        if group_id:
            return self._groups[group_id].view_register(
                view=view,
                widget_area=widget_area,
                add_toggle_action_into_toolbar=add_toggle_action_into_toolbar,
                delete_on_close=delete_on_close,
            )

        """Регистрирует и оборачивает вьюшку в доквиджет."""
        if view.key in self._docks:
            error_message = f'View with key "{view.key}" already registered!'
            raise ValueError(error_message)

        title = view.windowTitle()
        dock_object_name = f'{view.key}_dock'
        dock = KddDockWidget(dock_object_name)
        dock.setObjectName(dock_object_name)
        toggle_action = dock.toggleViewAction()
        toggle_action.setText(title)
        toggle_action.setToolTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setStatusTip(f'Показать/скрыть панель "{title}"')
        toggle_action.setIcon(view.windowIcon())
        dock.setTitle(title)
        dock.setIcon(view.windowIcon())
        dock.setWidget(view)
        view.ensure_visible_requested.connect(dock.raise_)

        match widget_area:
            case 'top':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnTop
                )
            case 'bottom':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnBottom
                )
            case 'left':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnLeft
                )
            case 'right':
                self.dock_manager.addKDockWidget(
                    dock, kdd.Location.Location_OnRight
                )
            case '_':
                dock.open()

        if not delete_on_close:
            self.views_menu.addAction(toggle_action)
        if delete_on_close:
            dock.setOptions(kdd.DockWidgetOption.DockWidgetOption_DeleteOnClose)
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
        view = dock.widget()
        view.teardown()
        view.setParent(None)
        dock.deleteLater()
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
        dock.setAsCurrentTab()
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

    def view_set_modal(self, key: str | None, group_id: str | None = None):
        for view_key, dock in self._docks.items():
            view = dock.widget()
            assert isinstance(view, BaseView)
            view.on_modality_changed(
                is_modal=bool(key),
                is_self_modal=group_id is None and view_key == key,
            )

    def focused_view_get(self) -> BaseView | None:
        for group in self._groups.values():
            for group_dock in group._docks.values():
                if group_dock.isFocused():
                    focused_view = group_dock.widget()
                    if isinstance(focused_view, BaseView):
                        return focused_view
                    return None

        for dock in self._docks.values():
            if dock.isFocused():
                focused_view = dock.widget()
                if isinstance(focused_view, BaseView):
                    return focused_view
                return None

        return None

    def state_read(self, settings: QSettings) -> None:
        settings.beginGroup('MainWindowKDD')
        state = settings.value('dockState', QByteArray())
        settings.endGroup()
        if state:
            saver = kdd.LayoutSaver(
                options=kdd.RestoreOption.RestoreOption_None
            )
            saver.restoreLayout(state)
            # Чтобы можно было тестировать различные флаги, т.к. они тоже записываются в settings
            self._set_config_flags()

        # Загружаем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_read()

        # Загружаем состояния групп
        for group in self._groups.values():
            group.state_read(settings)

    def state_write(self, settings: QSettings) -> None:
        saver = kdd.LayoutSaver(options=kdd.RestoreOption.RestoreOption_None)
        settings.beginGroup('MainWindowKDD')
        settings.setValue('dockState', saver.serializeLayout())
        settings.endGroup()

        # Сохраняем состояния вьюшек
        for dock in self._docks.values():
            dock.widget().state_write()

        # Сохраняем состояния групп
        for group in self._groups.values():
            group.state_write(settings)

    def view_register_central(self, view: BaseView) -> KddDockWidget:  # noqa: ARG002
        raise RuntimeError('Deprecated...')  # noqa: EM101

    def show_central_widget(self):
        self.central_dock_manager = kdd.MainWindow(
            "CentralMainWindow",
            kdd.MainWindowOption.MainWindowOption_None,
            self.dock_manager,
        )
        self.dock_manager.setPersistentCentralWidget(self.central_dock_manager)
        self.central_dock_manager.setAffinities(["documents"])

    def view_central_register(
        self,
        view: BaseView,
        *,
        widget_area: Literal["floating", "top", "bottom", "left", "right", "center"]
        | None = None,
        close_cb=None,
    ) -> BaseDockWidget | None:
        dock = kdd.DockWidget(view.key)
        dock.setAffinityName('documents')
        dock.setWidget(view)
        self.central_dock_manager.addKDockWidget(dock, kdd.Location.Location_OnLeft)
        self.documents_dock[view.key] = dock
        return dock

    @staticmethod
    def _set_config_flags() -> None:
        kdd.Config.self().setFlags(
            kdd.Config.self().flags()
            | kdd.Config.Flag.Flag_HideTitleBarWhenTabsVisible
            | kdd.Config.Flag.Flag_TabsHaveCloseButton
            | kdd.Config.Flag.Flag_ShowButtonsOnTabBarIfTitleBarHidden
            | kdd.Config.Flag.Flag_TitleBarIsFocusable
        )
