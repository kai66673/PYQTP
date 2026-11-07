from __future__ import annotations

from typing import TYPE_CHECKING

from pyqtp.base.pluggable import PluggableObject

if TYPE_CHECKING:
    from argparse import ArgumentParser

    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class BaseControl(PluggableObject):
    """
    Базовый класс контроллера приложения.

    Контроллер -- персистенный плагин, осуществляющий важные функции внутри
    приложения и доступный в качестве API внутри приложения.

    Описание жизненного цикла контрола:
      - вызывается конструктор и происходит запись в атрибут BaseMainWindow
      - вызывается read_settings с передачей аргументов командной строки
      - плагин регистрируется в приложении
      - после инстанцирования всех контролов вызывается метод setup_relations
      - после setup_relations всех контролов вызывается метод setup
      - вызываются (опционально!) методы регистрации менюшек, вьюшек и т.д.
      - ...
      - при попытке выйти из приложения вызывается метод on_shutdown
      - перед выходом вызывается метод teardown
      - плагин удаляется
    """

    def register(self, main_wnd: PluggableMainWindow) -> BaseControl:
        """
        Регистрирует контрол в приложении.

        Отложенно вызывает метод инициализации сигналов и
        метод инициализации плагина.
        """
        self._main_wnd = main_wnd
        main_wnd.ctrls_init_signal.connect(self.setup_relations)
        main_wnd.ctrls_setup_signal.connect(self.setup)
        main_wnd.setup_ui_signal.connect(self.setup_ui)
        return self

    def deregister(self, main_wnd: PluggableMainWindow) -> None:  # noqa: ARG002
        """Дерегистрируем контрол в приложении."""
        assert self._main_wnd is not None
        if self._main_wnd.setup_ui_done:
            self.teardown_ui()
        self.teardown()

    #############################
    # API стадий жизни контрола #
    #############################

    def setup_relations(self) -> None:
        """
        Осуществляем коннекты к сигналам других плагинов.

        Важно понимать, что порядок вызова слотов тут не определён!
        """

    @classmethod
    def settings_setup_argparser(cls, parser: ArgumentParser) -> None:
        """Добавляет аргументы для данного контрола."""
