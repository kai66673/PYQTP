"""Контрол «Монитор LSP» (этап 10.3): трафик запросов/ответов в панель.

Источник — кольцо ``TrafficRing`` внутри ``LspHub`` (хаб видит весь проводной
трафик своих процессов + порождённые им ответы; дизайн — ``lsp_monitor``).
Изменения кольца приходят из потока event-loop хаба через queued-мост
``_MonitorSignals`` (паттерн ``_DiagSignals`` из ProblemsControl).

Часть запросов идёт шквалом (hover/completion на каждое движение каретки),
поэтому перестройка панели КОАЛЕСЦИРУЕТСЯ: строки пишутся в кольцо без
задержек, а GUI перестраивается один раз на ``_FLUSH_MS``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction

from pyqtp.controls.base_control import BaseControl
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.lsp_monitor_view import LspMonitorView

if TYPE_CHECKING:
    from pyqtp.controls.lsp_control.lsp_monitor import TrafficRecord
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

mon_log = logging.getLogger('bcad.mon')

# период коалесциации перестройки панели (мс)
_FLUSH_MS = 200


class _MonitorSignals(QObject):
    """queued-мост: кольцо трафика хаба изменилось (поток event-loop -> GUI)."""

    traffic_changed = Signal()


class LspMonitorControl(BaseControl):
    """Контрол «Монитор LSP»: кольцо трафика хаба -> панель с фильтрами."""

    _main_wnd: IdeMainWindow

    def __init__(self) -> None:
        super().__init__()
        self.monitor_view: LspMonitorView | None = None
        self._monitor_signals = _MonitorSignals()
        # один и тот же callable для add/remove наблюдателя хаба
        self._traffic_notify = self._monitor_signals.traffic_changed.emit
        # флаг «перестройка уже запланирована» (коалесцируем шквал строк)
        self._pending_refresh = False

        self.action_monitor = QAction('Монитор LSP', self)
        self.action_monitor.triggered.connect(self._show_monitor_panel)

    #####################
    # Публичный API #

    def monitor_records(self) -> list[dict[str, Any]]:
        """Снимок кольца монитора для Python-консоли (словари записей)."""
        return [
            record.as_dict()
            for record in self._main_wnd.lsp_hub.monitor_records()
        ]

    def monitor_total_seen(self) -> int:
        """Сколько всего записей породил хаб (кольцо вытесняет старые)."""
        return self._main_wnd.lsp_hub.monitor_total_seen()

    def monitor_clear(self) -> None:
        """Очищает кольцо монитора (из консоли — как кнопка «Очистить»)."""
        self._main_wnd.lsp_hub.monitor_clear()

    ####################
    # Жизненный цикл #

    def setup(self) -> None:
        super().setup()
        self._main_wnd.events_ctrl.events_log_register(
            name=mon_log.name, group='LSP'
        )
        self._main_wnd.api_ctrl.register_variables({
            'lsp_monitor_records': self.monitor_records,
            'lsp_monitor_open': self._show_monitor_panel,
        })
        self._monitor_signals.traffic_changed.connect(self._on_traffic)
        self._main_wnd.lsp_hub.add_monitor_observer(self._traffic_notify)

    def teardown(self) -> None:
        self._main_wnd.lsp_hub.remove_monitor_observer(self._traffic_notify)

    def setup_menu(self) -> None:
        """Меню «LSP» (после «Вид», priority=-4)."""
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            lsp_menu = menubar.by_path('LSP')
        except KeyError:
            lsp_menu = menubar.addMenu('LSP', priority=-4)

        assert isinstance(lsp_menu, PriorityMenu)

        lsp_menu.addAction(self.action_monitor, priority=1)

    def setup_views(self) -> None:
        self.monitor_view = view = LspMonitorView(
            self._main_wnd, key=f'{self.key}_monitor_view'
        )
        self._main_wnd.view_register(view, widget_area='bottom')
        # хаб — владелец истории, кнопка «Очистить» в панели просит его снять
        view.clear_requested.connect(self._main_wnd.lsp_hub.monitor_clear)
        self._refresh()

    def teardown_views(self) -> None:
        super().teardown_views()
        if self.monitor_view is not None:
            self._main_wnd.view_deregister(self.monitor_view.key)
            self.monitor_view = None

    #####################
    # Внутреннее        #

    def _on_traffic(self) -> None:
        """GUI-поток (через queued-мост): коалесцируем перестройку панели.

        Строки пишутся в кольцо немедленно, но шквал запросов (completion/
        hover) не должен гонять QTreeWidget на каждую строку — сброс один
        раз в ``_FLUSH_MS``.
        """
        if self._pending_refresh:
            return
        self._pending_refresh = True
        QTimer.singleShot(_FLUSH_MS, self._flush)

    def _flush(self) -> None:
        self._pending_refresh = False
        self._refresh()

    def _refresh(self) -> None:
        """GUI-поток: очередной снимок кольца — в панель."""
        if self.monitor_view is None:
            return
        hub = self._main_wnd.lsp_hub
        records: tuple[TrafficRecord, ...] = hub.monitor_records()
        servers = sorted({rec.server for rec in records if rec.server})
        for name in hub.live_server_names():
            if name not in servers:
                servers.append(name)
        self.monitor_view.set_traffic(records, servers, hub.monitor_total_seen())

    def _show_monitor_panel(self) -> None:
        if self.monitor_view is not None:
            self.monitor_view.ensure_visible()
