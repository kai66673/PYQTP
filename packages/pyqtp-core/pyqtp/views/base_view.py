from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Signal, Slot
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow

dev_log = logging.getLogger('bcad.dev')


class BaseView(QWidget):
    """
    Базовый класс для представления.

    Представление реализует виджет-контейнер для отображения в стыкуемой панели.

    """

    ensure_visible_requested = Signal()

    @property
    def key(self) -> str:
        return self._key

    def __init__(
        self, main_wnd: PluggableMainWindow, /, *, key: str | None = None
    ) -> None:
        super().__init__(main_wnd)
        self._main_wnd = main_wnd
        self._key = key or self.__class__.__qualname__

    @property
    def help_context(self) -> str | QUrl:
        return f'<h1>Справка для представления (key={self.key}) не реализована!</h1>'

    @Slot(bool)
    def on_modality_changed(self, *, is_modal: bool, is_self_modal: bool):
        self.setEnabled(is_self_modal or not is_modal)
        if is_modal and is_self_modal:
            self.ensure_visible()
            self.set_highlighted(highlighted=True)
        else:
            self.set_highlighted(highlighted=False)

    def set_highlighted(self, *, highlighted: bool = True):
        pass

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(key={self.key})'

    def ensure_visible(self):
        self.ensure_visible_requested.emit()

    def setup(self) -> None:
        """Регистрирует вьюшку в приложении."""
        dev_log.debug(f'{self.key}::register')

    def teardown(self) -> None:
        """Дерегистрируем вьюшку в приложении."""
        dev_log.debug(f'{self.key}::deregister')

    def state_read(self) -> None:
        """Здесь сохраняются состояние вьюшки."""

    def state_write(self) -> None:
        """Здесь загружаются состояние вьюшки."""
