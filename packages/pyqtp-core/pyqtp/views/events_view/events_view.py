from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QMessageBox,
    QTableView,
)

from pyqtp.controls.events_control.events_models import (
    LogFilterInfo,
    LogMessage,
    LogMessagesModel,
)
from pyqtp.utils.confirm_dialog import ConfirmDialog
from pyqtp.utils.ui_constants import VIEWS_ROW_HEIGHT
from pyqtp.views.base_main_window_view import BaseMainWindowView
from pyqtp.views.events_view.rich_text_delegate import RichTextDelegate

if TYPE_CHECKING:
    from pyqtp.base.base_main_window import BaseMainWindow
    from pyqtp.controls.events_control.events_control import EventsControl


DEFAULT_ROW_HEIGHT = 22


class FilteredLogMessageModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filter_info = LogFilterInfo()

    @property
    def source_model(self) -> LogMessagesModel:
        return cast('LogMessagesModel', self.sourceModel())

    def filterAcceptsRow(  # noqa: N802
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,  # noqa: ARG002
    ) -> bool:
        # noinspection PyUnresolvedReferences
        log_message: LogMessage = self.source_model.message(source_row)

        if not self.filter_info.is_level_enabled(log_message.level):
            return False

        # Имя логгера совпадает с зарегистрированным или является его
        # дочерним (для иерархических логгеров вроде qtmonaco.*)
        if not any(
            log_message.log_name == name
            or log_message.log_name.startswith(f'{name}.')
            for name in self.filter_info.accepted_log_names
        ):
            return False

        if not self.filter_info.containing_str:
            return True
        return (
            self.filter_info.containing_str.casefold()
            in log_message.message.casefold()
        )

    def update_filter_info(self, new_filter_info: LogFilterInfo):
        self.filter_info = new_filter_info
        self.invalidateFilter()


class LogMessagesWidget(QTableView):
    link_clicked = Signal(str)

    def __init__(self, events_ctrl: EventsControl, parent=None):
        super().__init__(parent)
        self.events_ctrl = events_ctrl

        self.verticalHeader().setDefaultSectionSize(DEFAULT_ROW_HEIGHT)
        self.verticalHeader().setMinimumHeight(DEFAULT_ROW_HEIGHT)
        self.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.filtered_model = FilteredLogMessageModel()
        self.filtered_model.setSourceModel(events_ctrl.messages_model)
        self.setModel(self.filtered_model)
        self.verticalHeader().setDefaultSectionSize(VIEWS_ROW_HEIGHT)
        self.horizontalHeader().setMinimumSectionSize(24)
        self.horizontalHeader().setDefaultSectionSize(24)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().hide()

        self.setItemDelegateForColumn(1, RichTextDelegate())

        self.selectionModel().currentChanged.connect(
            self.handle_selected_row_changed
        )
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self._resize_row_height)
        self._current = QModelIndex()
        self._prev = QModelIndex()

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.setMouseTracking(True)

        theme_manager = events_ctrl._main_wnd.theme_manager
        self._clean_action = QAction(
            theme_manager.icon('common/trash.svg'), 'Очистить', self
        )
        self._clean_action.setToolTip('Удалить все сообщения из  журнала')
        self._clean_action.setStatusTip('Удалить все сообщения из  журнала')
        self._clean_action.triggered.connect(self._handle_clean)

        self._copy_action = QAction(
            theme_manager.icon('common/copy.svg'), 'Скопировать', self
        )
        self._copy_action.setToolTip(
            'Скопировать выбранное сообщение в буфер обмена'
        )
        self._copy_action.setStatusTip(
            'Скопировать выбранное сообщение в буфер обмена'
        )
        self._copy_action.setEnabled(False)
        self._copy_action.triggered.connect(self.copy_message)

        self._menu = QMenu(self)
        self._menu.addAction(self._clean_action)
        self._menu.addSeparator()
        self._menu.addAction(self._copy_action)

        events_ctrl.insert_message_signal.connect(self.scrollToBottom)

    def _handle_clean(self):
        confirm_dialog = ConfirmDialog(
            'Подтверждение',
            'Вы уверены, что хотите удалить все сообщения из журнала?',
        )

        if confirm_dialog.reply == QMessageBox.StandardButton.No:
            return

        self.events_ctrl.events_log_clear()
        self._copy_action.setEnabled(False)

    def clean_action(self):
        return self._clean_action

    def copy_action(self):
        return self._copy_action

    def show_context_menu(self, point):
        self._menu.exec(self.viewport().mapToGlobal(point))

    def _resize_row_height(self):
        if self._prev.isValid():
            self.setRowHeight(self._prev.row(), DEFAULT_ROW_HEIGHT)
        if self._current.isValid():
            row_num = self._current.row()
            text_width = self.horizontalHeader().sectionSize(1)
            index = self.filtered_model.index(row_num, 1)
            document = self.filtered_model.data(index, Qt.ItemDataRole.UserRole)
            if document is None:
                return
            document.setTextWidth(text_width)
            height = int(document.size().height())
            height = max(height, DEFAULT_ROW_HEIGHT)
            self.setRowHeight(row_num, height)

    def handle_selected_row_changed(
        self, current: QModelIndex, prev: QModelIndex
    ):
        self._copy_action.setEnabled(current.isValid())
        self._prev = prev
        self._current = current
        self.resize_timer.start()

    def update_filter_info(self, filter_info: LogFilterInfo):
        self.filtered_model.update_filter_info(filter_info)

    def copy_message(self):
        ind = self.selectionModel().currentIndex()
        if ind.isValid():
            source_index = self.filtered_model.mapToSource(ind)
            row_num = source_index.row()
            message = self.events_ctrl.messages_model.message(row_num)
            QApplication.clipboard().setText(message.message)
            self.events_ctrl.events_notify('Текст события скопирован!')

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        anchor = self.anchor_at(event.pos())
        if anchor is not None:
            self.link_clicked.emit(anchor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        anchor = self.anchor_at(event.pos())
        if anchor is None:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        else:
            QApplication.setOverrideCursor(Qt.CursorShape.PointingHandCursor)

    def anchor_at(self, pos: QPoint) -> str | None:
        index = self.indexAt(pos)
        if index.isValid() and index.column() == 1:
            item_rect = self.visualRect(index)
            relative_click_position = pos - item_rect.topLeft()

            document = self.filtered_model.data(index, Qt.ItemDataRole.UserRole)
            text_layout = document.documentLayout()
            return text_layout.anchorAt(relative_click_position) or None

        return None


class EventsView(BaseMainWindowView):
    open_link_requested = Signal(str)

    def __init__(
        self, main_wnd: BaseMainWindow, /, key: str | None = None
    ) -> None:
        super().__init__(main_wnd, key=key)

        self.setWindowTitle('Журнал событий')
        self.setWindowIcon(
            self._main_wnd.theme_manager.icon('log/logs.svg', is_checkable=True)
        )
        self.messages_view = LogMessagesWidget(main_wnd.events_ctrl)
        self.set_widget(self.messages_view)

        self.messages_view.link_clicked.connect(self.open_link_requested)

    @Slot(bool)
    def on_modality_changed(self, *, is_modal: bool, is_self_modal: bool):
        pass

    def filter_settings_changed(self, filter_info: LogFilterInfo):
        self.messages_view.update_filter_info(filter_info)

    @property
    def help_context(self) -> str | QUrl:
        return QUrl('qrc:/help/events_view/index.html')
