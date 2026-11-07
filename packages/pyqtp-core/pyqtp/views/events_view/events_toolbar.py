from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QLineEdit,
    QMenu,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
)

from pyqtp.controls.events_control.events_models import (
    LogCategoriesModel,
    LogFilterInfo,
)

if TYPE_CHECKING:
    from pyqtp.views.events_view.events_view import EventsView


class LogFilterLayout(QVBoxLayout):
    filter_settings_changed = Signal(LogFilterInfo)

    def __init__(
        self, categories_model: LogCategoriesModel, *, verbose: bool = False
    ):
        super().__init__()
        self.verbose = verbose

        ##############################################################################
        # Категории протоколирования
        self.categories_view = QTableView()
        self.categories_view.setMinimumHeight(200)
        self.categories_view.setMinimumWidth(300)
        self.categories_model = categories_model
        self.categories_view.setModel(self.categories_model)
        self.categories_view.horizontalHeader().setStretchLastSection(True)
        # noinspection PyUnresolvedReferences
        self.categories_model.categories_visibility_changed.connect(
            self.handle_filter_settings_changed
        )

        ##############################################################################
        # Уровень протоколирования
        self.level_group_box = QGroupBox('Уровень')
        self.level_group_box_layout = QVBoxLayout()

        self.error_check_box = QCheckBox('Ошибка')
        self.error_check_box.setChecked(True)
        # noinspection PyUnresolvedReferences
        self.error_check_box.toggled.connect(
            self.handle_filter_settings_changed
        )
        self.level_group_box_layout.addWidget(self.error_check_box)

        self.warning_check_box = QCheckBox('Предупреждение')
        self.warning_check_box.setChecked(True)
        # noinspection PyUnresolvedReferences
        self.warning_check_box.toggled.connect(
            self.handle_filter_settings_changed
        )
        self.level_group_box_layout.addWidget(self.warning_check_box)

        self.information_check_box = QCheckBox('Информация')
        self.information_check_box.setChecked(True)
        # noinspection PyUnresolvedReferences
        self.information_check_box.toggled.connect(
            self.handle_filter_settings_changed
        )
        self.level_group_box_layout.addWidget(self.information_check_box)

        if self.verbose:
            self.debug_check_box = QCheckBox('Отладка')
            self.debug_check_box.setChecked(True)
            # noinspection PyUnresolvedReferences
            self.debug_check_box.toggled.connect(
                self.handle_filter_settings_changed
            )
            self.level_group_box_layout.addWidget(self.debug_check_box)

        self.level_group_box.setLayout(self.level_group_box_layout)

        self.addWidget(self.level_group_box)
        self.addWidget(self.categories_view)

    def log_filter_info(self) -> LogFilterInfo:
        return LogFilterInfo(
            error=self.error_check_box.isChecked(),
            warning=self.warning_check_box.isChecked(),
            info=self.information_check_box.isChecked(),
            debug=False
            if not self.verbose
            else self.debug_check_box.isChecked(),
            accepted_log_names=self.categories_model.accepted_log_names(),
        )

    def handle_filter_settings_changed(self):
        # noinspection PyUnresolvedReferences
        self.filter_settings_changed.emit(self.log_filter_info())


class EventsToolBar(QToolBar):
    filter_settings_changed = Signal(LogFilterInfo)

    def __init__(
        self,
        *,
        categories_model: LogCategoriesModel,
        events_view: EventsView,
        verbose: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName(f'{events_view.key}_ToolBar')
        self.setAllowedAreas(
            Qt.ToolBarArea.TopToolBarArea | Qt.ToolBarArea.BottomToolBarArea
        )

        self.filter_button = QToolButton()
        self.filter_button.setText('Фильтрация')
        self.filter_button.setToolTip('Установка критериев фильтрации')
        self.filter_button.setStatusTip('Установка критериев фильтрации')
        self.filter_button.setIcon(
            events_view._main_wnd.theme_manager.icon('log/filter_log.svg')
        )
        self.filter_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.filter_menu = QMenu()
        self.filter_layout = LogFilterLayout(
            categories_model=categories_model, verbose=verbose
        )
        self.filter_menu.setLayout(self.filter_layout)
        self.filter_button.setMenu(self.filter_menu)

        self.substr_edit = QLineEdit()
        self.substr_edit.setPlaceholderText('Введите (под)строку поиска')
        self.substr_edit.setStyleSheet('QLineEdit { color: grey; }')
        self.substr_edit.setMaximumWidth(340)

        self.clear_action = QAction(self.substr_edit)
        self.clear_action.setEnabled(False)
        self.clear_action.setIcon(
            events_view._main_wnd.theme_manager.icon('log/clear_filter_str.svg')
        )
        self.substr_edit.addAction(
            self.clear_action, QLineEdit.ActionPosition.TrailingPosition
        )
        self.clear_action.triggered.connect(
            lambda: self.substr_edit.setText('')
        )

        self.addWidget(self.filter_button)
        self.addAction(events_view.messages_view.clean_action())
        self.addAction(events_view.messages_view.copy_action())
        self.addWidget(self.substr_edit)

        self.filter_menu.installEventFilter(self)

        # noinspection PyUnresolvedReferences
        self.filter_layout.filter_settings_changed.connect(
            self.filter_layout_settings_changed
        )
        self.substr_edit.textChanged.connect(self.filter_substr_changed)

    def filter_layout_settings_changed(self, filter_info: LogFilterInfo):
        filter_info.containing_str = self.substr_edit.text()
        # noinspection PyUnresolvedReferences
        self.filter_settings_changed.emit(filter_info)

    def filter_substr_changed(self):
        self.clear_action.setEnabled(len(self.substr_edit.text()) > 0)
        filter_info = self.filter_layout.log_filter_info()
        filter_info.containing_str = self.substr_edit.text()
        # noinspection PyUnresolvedReferences
        self.filter_settings_changed.emit(filter_info)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and watched == self.filter_menu:
            screen_height = QApplication.primaryScreen().size().height()
            button_pos = self.filter_button.mapToGlobal(
                self.filter_button.pos()
            )
            expected_menu_y = button_pos.y() + 42
            menu_height = self.filter_menu.height()
            if menu_height + expected_menu_y > screen_height:
                self.filter_menu.move(
                    button_pos.x(), button_pos.y() - menu_height - 2
                )
            return True
        return False
