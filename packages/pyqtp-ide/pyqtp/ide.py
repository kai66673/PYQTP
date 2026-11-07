from __future__ import annotations

import os

from pyqtp.launcher import run_main_window
from pyqtp.main_wnd.ide_main_window import IdeMainWindow
from pyqtp.utils.dark_win_app import DarkWinApplication

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"

if __name__ == '__main__':
    app = DarkWinApplication([])

    run_main_window(IdeMainWindow, org_name='PYQTP', app_name='PYQTP_IDE', app=app)
