from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pyqtp.base.helpers import parse_block_uri
from pyqtp.controls.events_control.events_icons import LogIconsStorage
from pyqtp.controls.events_control.events_models import LogLevel
from pyqtp.utils.pygments_highlighter import PygmentsSyntaxHighlighter

_QWIDGETSIZE_MAX = 16777215


class EventMessageBox(QDialog):
    block_uri_activated = Signal(QUrl)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str,
        message: str,
        detailed_text: str | None = None,
        level: LogLevel = LogLevel.Error,
    ) -> None:
        super().__init__(parent)

        # Парсим адреса блоков
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        # Иконка уровня сообщения
        self.icon_label = QLabel()
        self.icon_label.setPixmap(
            LogIconsStorage.icon(level).pixmap(QSize(32, 32))
        )

        # Основное сообщение
        self.message_label = QLabel(parse_block_uri(message))
        sp = self.message_label.sizePolicy()
        sp.setHorizontalStretch(2)
        self.message_label.setSizePolicy(sp)
        self.message_label.setWordWrap(True)

        message_layout = QHBoxLayout()
        message_layout.addWidget(self.icon_label)
        message_layout.addWidget(self.message_label)

        # Кнопки
        self.toggle_details_button = QPushButton('Показать детали')
        self.toggle_details_button.setCheckable(True)
        self.toggle_details_button.setChecked(False)
        self.toggle_details_button.setVisible(bool(detailed_text))
        self.toggle_details_button.toggled.connect(self._toggle_details)

        self.close_button = QPushButton('Закрыть')
        self.close_button.clicked.connect(self.close)

        self.buttons_layout = QHBoxLayout()
        self.buttons_layout.addStretch()
        self.buttons_layout.addWidget(self.toggle_details_button)
        self.buttons_layout.addWidget(self.close_button)

        # Доп. информация
        self.details_edit = QTextBrowser()
        self.details_edit.setReadOnly(True)
        self.details_edit.setVisible(bool(detailed_text))
        fixed_font = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont
        )
        self.details_edit.setFont(fixed_font)
        if detailed_text:
            self.details_edit.setText(parse_block_uri(detailed_text))

        # Располагаем виджеты
        self.main_layout = QVBoxLayout(self)
        self.main_layout.addLayout(message_layout)
        self.main_layout.addWidget(self.details_edit)
        self.main_layout.addLayout(self.buttons_layout)

        self._toggle_details(toggled=False)

        self.message_label.linkActivated.connect(self.on_hyperlink_clicked)
        self.details_edit.anchorClicked.connect(self.on_hyperlink_clicked)

    def on_hyperlink_clicked(self, block_uri: QUrl | str) -> None:
        if not isinstance(block_uri, QUrl):
            block_uri = QUrl(block_uri)
        if block_uri.scheme() == 'block':
            self.block_uri_activated.emit(block_uri)

    def set_highlighting(self, *, lang: str = 'python') -> None:
        self._highlighter = PygmentsSyntaxHighlighter(
            self.details_edit.document(), lang=lang
        )

    def _toggle_details(self, toggled: bool) -> None:  # noqa: FBT001
        # Изменяем логику изменения размера окна
        if toggled:
            self.setMinimumHeight(240)
            self.setMaximumHeight(_QWIDGETSIZE_MAX)
            self.setMinimumWidth(0)
            self.setMaximumWidth(_QWIDGETSIZE_MAX)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        else:
            self.setFixedHeight(124)

        # Прячем окно доп информации
        self.details_edit.setVisible(toggled)
        self.toggle_details_button.setText(
            'Скрыть детали' if toggled else 'Показать детали'
        )

        # Подгоняем размер
        if toggled:
            self.resize(1000, 400)
        else:
            self.resize(300, self.height())
