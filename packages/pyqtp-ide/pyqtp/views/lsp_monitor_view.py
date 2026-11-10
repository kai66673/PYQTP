from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.lsp_control.lsp_monitor import (
    DIRECTIONS,
    KINDS,
    MonitorFilter,
    TrafficRecord,
    filter_records,
    format_time,
)
from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# пустой itemData в комбиках = «без ограничения» (значение MonitorFilter '')
_ALL_DATA = ''

_COLUMNS = ('Время', 'Сервер', 'Направление', 'Тип', 'Метод', 'Id', 'Детали')


class LspMonitorView(BaseMainWindowView):
    """Панель «Монитор LSP» (этап 10.3): строки протокола хаба с серверами.

    Вьюха пассивная (паттерн панелей 9.x/10.x): ``LspMonitorControl``
    перекладывает сюда снимки кольца ``TrafficRing`` через ``set_traffic``.
    Фильтры (текст/сервер/направление/тип/нотификации) применяются в самой
    вьюхе чистой ``filter_records`` — контрол ничего о фильтрах не знает.

    «Пауза»: пока включена, новые снимки запоминаются, но дерево не
    перестраивается (у шквала completion/hover-запросов можно разобрать
    конкретный момент). «Очистить» просит контрол снять кольцо
    ``clear_requested`` (хаб — единственный владелец истории).
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
        self.setWindowTitle('Монитор LSP')

        self._status_label = QLabel('Нет записей')
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText('Фильтр по методу/деталям/id…')
        self._filter_edit.textChanged.connect(self._rebuild_tree)

        self._server_combo = QComboBox()
        self._direction_combo = QComboBox()
        self._kind_combo = QComboBox()
        for combo, label, values in (
            (self._server_combo, 'Сервер:', ()),
            (self._direction_combo, 'Направление:', DIRECTIONS),
            (self._kind_combo, 'Тип:', KINDS),
        ):
            combo.addItem(f'все {label.lower()}', _ALL_DATA)
            for value in values:
                combo.addItem(value, value)
            combo.currentIndexChanged.connect(self._rebuild_tree)

        self._notifications_check = QCheckBox('Нотификации')
        self._notifications_check.setToolTip(
            'Показывать уведомительный шум (didOpen/didChange/publishDiagnostics…)'
        )
        self._notifications_check.toggled.connect(self._rebuild_tree)

        self._pause_button = QPushButton('Пауза')
        self._pause_button.setCheckable(True)
        self._pause_button.setToolTip(
            'Заморозить дерево: новые записи приходят в кольцо, '
            'но панель не перестраивается'
        )
        self._pause_button.toggled.connect(self._on_pause_toggled)
        self._clear_button = QPushButton('Очистить')
        self._clear_button.clicked.connect(self._on_clear_clicked)

        header = QHBoxLayout()
        header.addWidget(self._status_label)
        header.addWidget(self._filter_edit, stretch=1)
        header.addWidget(self._server_combo)
        header.addWidget(self._direction_combo)
        header.addWidget(self._kind_combo)
        header.addWidget(self._notifications_check)
        header.addWidget(self._pause_button)
        header.addWidget(self._clear_button)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(False)
        header_view = self._tree.header()
        assert header_view is not None
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(
            len(_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch
        )

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(header)
        layout.addWidget(self._tree)
        self.set_widget(container)

        # последний снимок контрола + итоговое число записей (кольцо)
        self._records: tuple[TrafficRecord, ...] = ()
        self._total_seen = 0

    # --- снимок трафика (пишет контрол) ---

    def set_traffic(
        self,
        records: Sequence[TrafficRecord],
        servers: Sequence[str],
        total_seen: int,
    ) -> None:
        """Запомнить снимок кольца, синхронизировать комбики, перестроить.

        ``records`` — плоский снимок кольца от старых к новым. При
        включённой паузе снимок только запоминается — дерево остаётся
        каким было (снятие с паузы перестроит его по актуальному снимку).
        """
        self._records = tuple(records)
        self._total_seen = total_seen
        self._sync_server_combo(servers)
        if self._pause_button.isChecked():
            self._update_status()
            return
        self._rebuild_tree()

    def _sync_server_combo(self, servers: Sequence[str]) -> None:
        """Пересобрать комбик серверов, не трогая текущий выбор."""
        current = self._server_combo.currentData()
        names = [
            self._server_combo.itemData(i)
            for i in range(1, self._server_combo.count())
        ]
        if list(servers) == names:
            return
        self._server_combo.blockSignals(True)  # noqa: FBT003
        self._server_combo.clear()
        self._server_combo.addItem('все серверы', _ALL_DATA)
        for name in servers:
            self._server_combo.addItem(name, name)
        index = self._server_combo.findData(
            current if current is not None else _ALL_DATA
        )
        self._server_combo.setCurrentIndex(max(index, 0))
        self._server_combo.blockSignals(False)  # noqa: FBT003

    # --- фильтры ---

    def _filter(self) -> MonitorFilter:
        return MonitorFilter(
            text=self._filter_edit.text(),
            server=str(self._server_combo.currentData() or ''),
            direction=str(self._direction_combo.currentData() or ''),
            kind=str(self._kind_combo.currentData() or ''),
            show_notifications=self._notifications_check.isChecked(),
        )

    def _on_pause_toggled(self) -> None:
        """Снятие с паузы перестраивает дерево по актуальному снимку."""
        if not self._pause_button.isChecked():
            self._rebuild_tree()

    def _on_clear_clicked(self) -> None:
        # локальную картинку чистим сразу (даже в паузе), историю кольца
        # снимает хаб по сигналу — он её владелец
        self._records = ()
        self._tree.clear()
        self._update_status()
        self.clear_requested.emit()

    def _visible_records(self) -> tuple[TrafficRecord, ...]:
        return filter_records(self._records, self._filter())

    def _rebuild_tree(self) -> None:
        """Перестроить плоские строки из отфильтрованного снимка.

        Автоскролл «по-умному»: вниз только если до перестройки ползунок
        уже был у конца (иначе разобрать средний ряд при шквале запросов
        невозможно).
        """
        was_at_bottom = self._is_at_bottom()
        visible = self._visible_records()
        self._tree.clear()
        for rec in visible:
            item = QTreeWidgetItem([
                format_time(rec.ts),
                rec.server,
                rec.direction,
                rec.kind,
                rec.method,
                '' if rec.msg_id is None else str(rec.msg_id),
                rec.summary,
            ])
            item.setToolTip(
                len(_COLUMNS) - 1,
                f'{rec.method or rec.kind} {rec.summary}'.strip(),
            )
            self._tree.addTopLevelItem(item)
        self._update_status(len(visible))
        if was_at_bottom:
            self._tree.scrollToBottom()

    def _is_at_bottom(self) -> bool:
        bar = self._tree.verticalScrollBar()
        if bar is None:
            return True
        return bar.value() >= bar.maximum() - 4

    def _update_status(self, visible_count: int | None = None) -> None:
        """Строка итога: показано/в кольце/всего (кольцо вытесняет старые)."""
        if visible_count is None:
            visible_count = len(self._visible_records())
        ring = len(self._records)
        text = f'Записей: {visible_count} из {ring}'
        if self._total_seen > ring:
            text += f' (всего было {self._total_seen})'
        self._status_label.setText(text)
