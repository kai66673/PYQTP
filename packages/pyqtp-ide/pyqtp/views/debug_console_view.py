from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow


class DebugConsoleView(BaseMainWindowView):
    """Панель «Консоль отладки» (этап 11, срез D1): read-only вывод.

    Вьюха пассивная (паттерн панелей 9.x/10.x): ничего не порождает сама,
    только показывает строки, которые кладёт ``DapControl`` — вывод debuggee
    (``output``-события DAP) и служебные строки состояния («[Отладка] ...»).
    Кнопка «Очистить» снимает только показ (сигнал ``clear_requested``):
    владельцем сессии/состояния остаёт контрол.

    ``set_status_text``/``status_text`` — короткая строка состояния
    (IDLE/RUNNING/STOPPED) в шапке; доступна программно (тесты без vision).
    """

    clear_requested = Signal()

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Консоль отладки')

        self._status_label = QLabel('Не отлаживаем')
        self._clear_button = QPushButton('Очистить')
        self._clear_button.setToolTip('Очистить вывод консоли')
        self._clear_button.clicked.connect(self.clear_console)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        # моноширинный шрифт как у редактора сообщений (перенос по словам,
        # вывод debuggee приходит чанками с переводом строк)
        font = self._text.font()
        font.setStyleHint(font.StyleHint.Monospace)
        font.setFamily('Monospace')
        self._text.setFont(font)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        # защита от разрастания буфера (debuggee с обильной трассировкой)
        self._text.setMaximumBlockCount(_MAX_BLOCKS)

        header = QHBoxLayout()
        header.addWidget(self._status_label)
        header.addStretch(1)
        header.addWidget(self._clear_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self._text)
        container = QWidget()
        container.setLayout(layout)
        self.set_widget(container)

    # ------------------------------------------------------------------ #
    # Показ (вызывает только DapControl из GUI-потока)                  #
    # ------------------------------------------------------------------ #

    def append_text(self, text: str) -> None:
        """Сырой чанк вывода программы (переводы строк — внутри текста)."""
        if text:
            self._text.appendPlainText(text.rstrip('\n'))

    def append_line(self, text: str) -> None:
        """Служебная строка контрола (всегда с новой строки)."""
        self._text.appendPlainText(text)

    def clear_console(self) -> None:
        """Снять показ (кнопка или новый запуск)."""
        self._text.clear()

    def set_status_text(self, text: str) -> None:
        self._status_label.setText(text)

    def status_text(self) -> str:
        """Текущая строка состояния (программная проверка в тестах)."""
        return self._status_label.text()

    def console_text(self) -> str:
        """Весь показанный вывод (программная проверка в тестах)."""
        return self._text.toPlainText()


# защита от разрастания буфера консоли (болванка: debuggee с трассировкой)
_MAX_BLOCKS = 20000
