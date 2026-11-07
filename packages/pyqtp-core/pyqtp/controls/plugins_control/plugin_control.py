from __future__ import annotations

import importlib
import logging
import pkgutil
from functools import lru_cache
from typing import TYPE_CHECKING

from pyqtp.controls.base_control import BaseControl
from pyqtp.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyqtp.base.pluggable_main_window import PluggableMainWindow


dev_log = logging.getLogger('bcad.dev')


class PluginControl(BaseControl):
    """Базовый контроллер плагинов к приложению."""

    _main_wnd: PluggableMainWindow

    #########################
    # API каталога плагинов #
    #########################

    @lru_cache  # noqa: B019
    def plugins_import_all(
        self, *, compatible: bool = True
    ) -> list[type[BasePlugin]]:
        """Импортируем все доступные плагины."""
        import pyqtp.plugins  # noqa: PLC0415

        module = pyqtp.plugins
        for _, modname, _ in pkgutil.walk_packages(
            path=module.__path__,  # type:ignore[reportAttributeAccessIssue]
            prefix=module.__name__ + '.',  # type:ignore[reportAttributeAccessIssue]
            onerror=lambda _: None,
        ):
            try:
                importlib.import_module(modname)
            except ImportError:
                dev_log.error(f'failed to import plugin module "{modname}".')

        # Формируем список плагинов
        def get_subclassess(cls) -> Iterator[type[BasePlugin]]:
            for subclass in cls.__subclasses__():
                yield subclass
                yield from get_subclassess(subclass)

        # Фильтруем базовые классы
        plugins = [
            cls
            for cls in get_subclassess(BasePlugin)
            if not cls.__name__.startswith(('_', 'Base'))
        ]

        # Фильтруем совместимые
        if compatible:
            plugins = [
                cls
                for cls in plugins
                if self._main_wnd.plug_compatibility_check(cls)
            ]

        return plugins
