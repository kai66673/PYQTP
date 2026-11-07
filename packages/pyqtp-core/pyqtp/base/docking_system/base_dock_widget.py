from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QToolBar, QWidget

    from pyqtp.views.base_view import BaseView


class BaseDockWidget(Protocol):
    """
    Внешний интерфейс для работы с DockWidget.

    Все функции имитируют функции ads.CDockWidget
    """

    def setWindowTitle(self, title: str) -> None:  # noqa: N802
        ...

    def closeEvent(self, event) -> None: ...

    def toggleView(self, Open) -> None:  # noqa: N803, N802
        ...

    def toggleViewAction(self) -> QAction:  # noqa: N802
        ...

    def setWidget(self, widget: BaseView | QWidget) -> None:  # noqa: N802
        raise NotImplementedError

    def setToolBar(self, toolbar: QToolBar) -> None:  # noqa: N802
        ...

    def toolBar(self) -> QToolBar:  # noqa: N802
        ...

    def closeDockWidget(self) -> None:  # noqa: N802
        ...

    def widget(self) -> BaseView: ...
