from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from pyqtp.controls.help_control.help_tmp_files_cache import HelpTmpFilesCache
from pyqtp.theme.assets_manager import AssetsManager


class HelpRootPage(QWebEnginePage):
    _temp_files: dict[str, str]

    def __init__(self, assets_manager: AssetsManager, parent=None):
        super().__init__(parent)
        self.assets_manager = assets_manager
        self._temp_files = {}

    def acceptNavigationRequest(  # noqa: N802
        self,
        url: QUrl | str,
        type: QWebEnginePage.NavigationType,  # noqa: A002
        isMainFrame: bool,  # noqa: FBT001, N803
    ) -> bool:
        assert isinstance(url, QUrl)

        if (
            type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            and url.scheme() == 'qrc'
        ):
            asset_path = self.assets_manager.asset_path(url.path()[1:])
            url_str = url.toString() if asset_path is None else str(asset_path)
            load_url = QUrl.fromLocalFile(url_str) if asset_path else url
            if url_str.endswith('.html'):
                self.load(load_url)
            else:
                QDesktopServices.openUrl(
                    load_url if asset_path else HelpTmpFilesCache.file_path(load_url)
                )
            return False

        return super().acceptNavigationRequest(url, type, isMainFrame)


class HelpWidget(QWebEngineView):
    def __init__(self, url: QUrl, assets_manager: AssetsManager, parent=None):
        super().__init__(parent)
        self.assets_manager = assets_manager

        self.setPage(HelpRootPage(assets_manager, self))
        self.settings().setDefaultTextEncoding('utf-8')
        self.load(url)

    def set_url(self, url: QUrl):
        if url.scheme() == 'qrc':
            asset_path = self.assets_manager.asset_path(url.path()[1:])
            if asset_path is not None:
                self.load(QUrl.fromLocalFile(str(asset_path)))
                return
        self.setUrl(url)



if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('bcad')
    logger.setLevel(logging.DEBUG)

    app = QApplication(sys.argv)

    assm = AssetsManager()

    main_widget = HelpWidget(
        QUrl.fromLocalFile('/home/kai/BAS/bas311/bcad-core/bcad/assets/help/base/index.html'),
        assm
    )
    main_widget.show()
    sys.exit(app.exec())
