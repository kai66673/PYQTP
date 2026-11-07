from __future__ import annotations

from typing import TYPE_CHECKING, cast

import PyKDDockWidgets.KDDockWidgets as kdd  # type: ignore[reportMissingImports] # noqa: N813
from PySide6.QtCore import Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

if TYPE_CHECKING:
    from PySide6.QtWidgets import QToolBar

    from pyqtp.views.base_view import BaseView


class KddDockWidget(kdd.DockWidget):
    """
    Реализация интерфейса BaseDockWidget на основе kdd.DockWidget

    Создает прокси виджет для того чтобы подключать toolbar как в библиотеке ads.

    Args:
        name(str): имя виджета

    """

    _widget: QWidget | BaseView
    _toolbar: QToolBar | None = None

    def __init__(self, name: str):
        super().__init__(
            name, options=kdd.DockWidgetOption.DockWidgetOption_None
        )
        self.setAffinityName('panels')
        self._proxy = QWidget()
        self._proxy_layout = QVBoxLayout(self._proxy)
        self._proxy_layout.setContentsMargins(0, 0, 0, 0)
        self._proxy_layout.setSpacing(0)
        self._proxy.setLayout(self._proxy_layout)
        self.toggleAction().toggled.connect(self._on_toggled)
        super().setWidget(self._proxy)

    def closeEvent(self, event) -> None:  # noqa: ARG002
        """
        Вызывается при событии закрытия dock widget

        Без этой реализации dock widget остается при отключении view из меню
        """
        self.forceClose()

    def toggleView(self, Open) -> None:  # noqa: N803, N802
        if Open:
            self.open()
        else:
            self.forceClose()

    def toggleViewAction(self):  # noqa: N802
        return self.toggleAction()

    def setWidget(self, widget: QWidget | BaseView) -> None:  # noqa: N802
        """
        Добавляет виджет или вьюшку на прокси виджет

        Args:
            widget(QWidget | BaseView): виджет или вьюшка. Виджет подается только в ads поэтому
            тут приходится добавлять для совместимости

        """
        if hasattr(self, '_widget'):
            self._proxy_layout.removeWidget(self._widget)
        self._widget = widget
        self._proxy_layout.insertWidget(
            1 if self._toolbar else 0, self._widget, 1
        )

    def setToolBar(self, toolbar: QToolBar) -> None:  # noqa: N802
        """
        Добавляет toolbar на прокси виджет

        Args:
            toolbar(QToolBar): toolbar

        """
        if self._toolbar:
            self._proxy_layout.removeWidget(self._toolbar)
        self._toolbar = toolbar
        self._proxy_layout.insertWidget(0, self._toolbar, 1)

    def toolBar(self) -> QToolBar:  # noqa: N802
        assert self._toolbar is not None
        return self._toolbar

    def closeDockWidget(self) -> None:  # noqa: N802
        self.forceClose()

    def widget(self) -> BaseView:
        """
        Возвращает вьюшку.

        Returns:
            BaseView: не возвращает QWidget чтобы избавиться от множественных cast.
        """
        return cast('BaseView', self._widget)

    @Slot(bool)
    def _on_toggled(self, toggle) -> None:
        if not toggle:
            self.closeDockWidget()
