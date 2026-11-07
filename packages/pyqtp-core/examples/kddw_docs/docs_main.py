import os
import sys

import PyKDDockWidgets.KDDockWidgets as kdd  # type: ignore[reportMissingImports] # noqa: N813
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
)
from qtpy.QtWidgets import QLabel, QPlainTextEdit


class MyMainWindow(QMainWindow):

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.dock_manager = kdd.MainWindow(
            "DocsMainWindow",
            kdd.MainWindowOption.MainWindowOption_HasCentralWidget
        )
        self.setCentralWidget(self.dock_manager)
        self.dock_manager.setAffinities(["panels"])

        self.central_dock_manager = kdd.MainWindow(
            "CentralMainWindow",
            kdd.MainWindowOption.MainWindowOption_None
        )
        self.dock_manager.setPersistentCentralWidget(self.central_dock_manager)
        self.central_dock_manager.setAffinities(["documents"])

        self.d1 = kdd.DockWidget("file_1.txt")
        self.d1.setAffinityName("documents")
        self.wd1 = QPlainTextEdit()
        self.d1.setWidget(self.wd1)
        self.central_dock_manager.addKDockWidget(self.d1, kdd.Location.Location_OnLeft)

        self.d2 = kdd.DockWidget("file_2.txt")
        self.d2.setAffinityName("documents")
        self.wd2 = QPlainTextEdit()
        self.d2.setWidget(self.wd2)
        self.central_dock_manager.addKDockWidget(self.d2, kdd.Location.Location_OnLeft)

        self.p1 = kdd.DockWidget("Project")
        self.p1.setAffinityName("panels")
        self.wp1 = QLabel("Project")
        self.p1.setWidget(self.wp1)
        self.dock_manager.addKDockWidget(self.p1, kdd.Location.Location_OnLeft)

        self.p2 = kdd.DockWidget("Terminal")
        self.p2.setAffinityName("panels")
        self.wp2 = QLabel("Terminal")
        self.p2.setWidget(self.wp2)
        self.dock_manager.addKDockWidget(self.p2, kdd.Location.Location_OnBottom)



if __name__ == "__main__":
    if sys.platform.startswith('linux') or sys.platform.startswith('darwin'):
        os.environ['QT_QPA_PLATFORM'] = 'xcb'

    app = QApplication(sys.argv)

    kdd.initFrontend(kdd.FrontendType.QtWidgets)
    kdd.Config.self().setFlags(
        kdd.Config.self().flags()
        | kdd.Config.Flag.Flag_HideTitleBarWhenTabsVisible
        | kdd.Config.Flag.Flag_AllowReorderTabs
        | kdd.Config.Flag.Flag_AllowSwitchingTabsViaMenu
        | kdd.Config.Flag.Flag_TabsHaveCloseButton
        | kdd.Config.Flag.Flag_ShowButtonsOnTabBarIfTitleBarHidden
        | kdd.Config.Flag.Flag_TitleBarIsFocusable
    )

    window = MyMainWindow()
    window.resize(1024, 768)
    window.show()

    sys.exit(app.exec())
