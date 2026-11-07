from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFile

if TYPE_CHECKING:
    from PySide6.QtCore import QUrl


class HelpTmpFilesCache:
    _temp_files: dict[str, str] = {}  # noqa: RUF012

    @staticmethod
    def file_path(url: QUrl) -> str:
        url_str = url.toString()

        temp_file_path = HelpTmpFilesCache._temp_files.get(url_str)
        if temp_file_path is None or not Path(temp_file_path).exists():
            last_point_index = url_str.rfind('.')
            temp_file_path = tempfile.mktemp(  # noqa: S306
                suffix=''
                if last_point_index == -1
                else url_str[last_point_index:]
            )
            QFile.copy(url_str[3:], temp_file_path)
            HelpTmpFilesCache._temp_files[url_str] = temp_file_path

        return temp_file_path
