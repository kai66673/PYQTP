from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pyqtp.base.pluggable_main_window import PluggableMainWindow


@dataclass
class HelpMenuContext:
    docs: dict[str, Path]
    about_source: str | Callable[[PluggableMainWindow], None]
    index_url: str = 'help/base/index.html'
