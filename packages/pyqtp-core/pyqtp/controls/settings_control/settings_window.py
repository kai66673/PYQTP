from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QShowEvent

    from pyqtp.base.pluggable_main_window import PluggableMainWindow
    from pyqtp.controls.settings_control.settings_control import (
        SettingsControl,
    )
    from pyqtp.controls.settings_control.settings_page import SettingsPage

dev_log = logging.getLogger('bcad.dev')


class SettingsWindow(QDialog):
    """
    Немодальное окно настроек со списком страниц слева и стеком виджетов
    справа. Создаётся лениво контролом ``SettingsControl`` и переживает
    закрытие (закрытие окна -- это скрытие с сохранением состояния).
    """

    def __init__(
        self, control: SettingsControl, main_wnd: PluggableMainWindow
    ) -> None:
        super().__init__(main_wnd)
        self._control = control
        self._main_wnd = main_wnd
        self._settings_prefix = control.key
        self._stacked: dict[str, QWidget] = {}

        self.setWindowTitle('Настройки')
        self.resize(880, 600)

        root_layout = QVBoxLayout(self)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        root_layout.addWidget(self._splitter, stretch=1)

        # Левая панель: поиск + список страниц
        left = QWidget(self._splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._search_edit = QLineEdit(left)
        self._search_edit.setPlaceholderText('Поиск настройки...')
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self._search_edit)
        self._pages_list = QListWidget(left)
        self._pages_list.currentRowChanged.connect(self._on_row_selected)
        left_layout.addWidget(self._pages_list, stretch=1)
        self._pages_list.setMinimumWidth(180)

        # Правая панель: стек виджетов страниц
        self._stack = QStackedWidget(self._splitter)
        self._splitter.addWidget(left)
        self._splitter.addWidget(self._stack)
        self._splitter.setStretchFactor(1, 1)

        # Нижняя панель: статус + кнопки применения
        bottom = QHBoxLayout()
        root_layout.addLayout(bottom)
        self._status_label = QLabel('', self)
        self._status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bottom.addWidget(self._status_label, stretch=1)
        self._apply_btn = QPushButton('Применить', self)
        self._apply_btn.clicked.connect(self._on_apply)
        bottom.addWidget(self._apply_btn)
        self._revert_btn = QPushButton('Откатить', self)
        self._revert_btn.clicked.connect(self._on_revert)
        bottom.addWidget(self._revert_btn)
        self._reset_btn = QPushButton('Сбросить по умолчанию', self)
        self._reset_btn.clicked.connect(self._on_reset_defaults)
        bottom.addWidget(self._reset_btn)
        self._close_btn = QPushButton('Закрыть', self)
        self._close_btn.clicked.connect(self._hide_window)
        bottom.addWidget(self._close_btn)

        self._restore_window_state()
        self.reload_pages()

    ###################
    # Синхронизация   #
    ###################

    def reload_pages(self, *, keep_selection: bool = True) -> None:
        """Перестраивает список страниц; виджеты страниц не уничтожаются."""
        selected_key = None
        if keep_selection:
            selected_key = self._current_page_key()
        self._pages_list.blockSignals(True)  # noqa: FBT003
        self._pages_list.clear()
        for page in self._control.pages_sorted():
            item = QListWidgetItem(page.title, self._pages_list)
            item.setData(Qt.ItemDataRole.UserRole, page.key)
        self._pages_list.blockSignals(False)  # noqa: FBT003
        self._apply_filter(self._search_edit.text())
        # Убираем из стека виджеты удалённых страниц
        alive = {page.key for page in self._control.pages_sorted()}
        for key in [k for k in self._stacked if k not in alive]:
            widget = self._stacked.pop(key)
            self._stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        # Восстанавливаем выбор (сигнал сам подтянет виджет в стек)
        row = self._row_for_key(selected_key)
        row = max(row, 0)
        if self._pages_list.count():
            self._pages_list.setCurrentRow(row)
        else:
            self._on_row_selected(-1)

    def sync_pages_values(self) -> None:
        """Прогоняет ``load()`` по всем уже созданным страницам."""
        for page in self._control.pages_sorted():
            if page.is_created:
                page.load()

    ###################
    # Выбор страницы  #
    ###################

    def _row_for_key(self, key: str | None) -> int:
        if key is None:
            return -1
        for row in range(self._pages_list.count()):
            item = self._pages_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                return row
        return -1

    def _current_page_key(self) -> str | None:
        item = self._pages_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _current_page(self) -> SettingsPage | None:
        key = self._current_page_key()
        if key is None:
            return None
        return self._control.page_get(key)

    @Slot(int)
    def _on_row_selected(self, row: int) -> None:
        page = self._current_page() if row >= 0 else None
        if page is None:
            self._apply_btn.setEnabled(False)
            self._revert_btn.setEnabled(False)
            self._reset_btn.setEnabled(False)
            return
        widget = page.widget  # ленивое создание виджета страницы
        if self._stack.indexOf(widget) < 0:
            self._stack.addWidget(widget)
            self._stacked[page.key] = widget
        self._stack.setCurrentWidget(widget)
        self._apply_btn.setEnabled(page.supports_apply)
        self._revert_btn.setEnabled(page.supports_apply)
        self._reset_btn.setEnabled(page.supports_reset)
        self._set_status('')

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self._pages_list.count()):
            item = self._pages_list.item(row)
            if item is None:
                continue
            title = item.text().casefold()
            item.setHidden(bool(needle) and needle not in title)

    ###########
    # Кнопки  #
    ###########

    @Slot()
    def _on_apply(self) -> None:
        page = self._current_page()
        if page is None:
            return
        try:
            page.apply()
        except (ValueError, TypeError) as exc:
            dev_log.error(
                'Ошибка применения настроек страницы "%s": %s',
                page.title, exc,
            )
            self._set_status(f'<span style="color:darkred">Ошибка: {html.escape(str(exc))}</span>')
            return
        self._set_status('Изменения применены')

    @Slot()
    def _on_revert(self) -> None:
        page = self._current_page()
        if page is None:
            return
        page.load()
        self._set_status('Значения страницы восстановлены из настроек')

    @Slot()
    def _on_reset_defaults(self) -> None:
        page = self._current_page()
        if page is None:
            return
        try:
            page.reset_defaults()
        except (ValueError, TypeError) as exc:
            dev_log.error(
                'Ошибка сброса страницы "%s": %s', page.title, exc,
            )
            self._set_status(f'<span style="color:darkred">Ошибка: {html.escape(str(exc))}</span>')
            return
        page.load()
        self._set_status('Страница сброшена к значениям по умолчанию')

    def _set_status(self, rich_text: str) -> None:
        self._status_label.setText(rich_text)

    ####################
    # Состояние окна   #
    ####################

    def _restore_window_state(self) -> None:
        settings = self._main_wnd.settings
        self.restoreGeometry(
            settings.value(f'{self._settings_prefix}/geometry', QByteArray())  # type: ignore[reportArgumentType]
        )
        self._splitter.restoreState(
            settings.value(f'{self._settings_prefix}/splitter', QByteArray())  # type: ignore[reportArgumentType]
        )
        self._saved_page_key = settings.value(
            f'{self._settings_prefix}/current_page', '', str
        )

    def _save_window_state(self) -> None:
        settings = self._main_wnd.settings
        self._main_wnd.settings.setValue(
            f'{self._settings_prefix}/geometry', self.saveGeometry()
        )
        settings.setValue(
            f'{self._settings_prefix}/splitter', self._splitter.saveState()
        )
        settings.setValue(
            f'{self._settings_prefix}/current_page',
            self._current_page_key() or '',
        )

    def showEvent(self, arg__1: QShowEvent) -> None:  # noqa: N802
        super().showEvent(arg__1)
        saved = getattr(self, '_saved_page_key', '')
        if saved:
            row = self._row_for_key(saved)
            if row >= 0:
                self._pages_list.setCurrentRow(row)
        self._saved_page_key = ''

    ##################
    # Закрытие окна  #
    ##################

    def _hide_window(self) -> None:
        self._save_window_state()
        self.hide()

    def reject(self) -> None:
        # Esc не должен «съедать» окно -- только скрытие
        self._hide_window()

    def closeEvent(self, arg__1: QCloseEvent) -> None:
        if not arg__1.spontaneous():
            # Закрытие вместе с главным окном приложения
            super().closeEvent(arg__1)
            return
        arg__1.ignore()
        self._hide_window()
