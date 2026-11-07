from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QMovie, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStatusBar,
    QToolButton,
    QWidget,
)

from pyqtp.base.helpers import parse_block_uri
from pyqtp.controls.events_control.events_icons import LogIconsStorage
from pyqtp.controls.events_control.events_models import LogMessage
from pyqtp.utils.waiting_spinner import SpinnerMessage


class EventsToolbarWidget(QWidget):
    ICON_SIZE = QSize(18, 18)

    open_link_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 0, 4)
        self.main_layout.setSpacing(5)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(self.ICON_SIZE)
        self.main_layout.addWidget(self._icon_label)

        self._message_label = QLabel()
        self._message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._message_label.setFixedHeight(self.ICON_SIZE.height())
        self.main_layout.addWidget(self._message_label)

        self._message_label.linkActivated.connect(self.open_link_requested)

        self._clear_timer = QTimer()
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._clear_message)

    def _clear_message(self):
        self._icon_label.setPixmap(QPixmap())
        self._message_label.setText('')

    def add_action(self, action: QAction):
        action_button = QToolButton()
        action_button.setAutoRaise(True)
        action_button.setDefaultAction(action)
        action_button.setIconSize(self.ICON_SIZE)
        action_button.setFixedSize(self.ICON_SIZE)

        status_bar = self.parentWidget()
        assert isinstance(status_bar, QStatusBar)
        status_bar.addPermanentWidget(action_button)

    def show_log_message(
        self,
        log_msg: LogMessage | SpinnerMessage | None,
        duration: int = 10 * 1000,
    ) -> None:
        if self._clear_timer.isActive():
            self._clear_timer.stop()

        if log_msg is None:
            self._icon_label.clear()
            self._message_label.clear()

        if isinstance(log_msg, LogMessage):
            self._icon_label.setPixmap(
                LogIconsStorage.icon(log_msg.level).pixmap(self.ICON_SIZE)
            )

            value = log_msg.message.split('\n', 1)[0]
            parsed_value = parse_block_uri(value).replace('\n', '<br>')
            self._message_label.setText(parsed_value)
        elif isinstance(log_msg, SpinnerMessage):
            movie = QMovie(log_msg.spinner_icon)
            movie.setScaledSize(self.ICON_SIZE)
            self._icon_label.setMovie(movie)
            movie.start()

            value = (log_msg.message or '').split('\n', 1)[0]
            parsed_value = parse_block_uri(value).replace('\n', '<br>')
            self._message_label.setText(parsed_value)

        if duration > 0:
            self._clear_timer.start(duration)
