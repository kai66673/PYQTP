"""Панель «Проблемы» (этап 10.1): агрегация диагностик LSP из стора хаба.

Источник данных — стор последних ``publishDiagnostics`` внутри ``LspHub``
(хаб видит весь трафик диагностик всех серверов; модель маркеров Monaco из
Python не читается — см. план этапа 10 в ``docs/IDE_ROADMAP.md``). Изменения
стора приходят из потока event-loop хаба через queued-мост ``_DiagSignals``
(тот же паттерн, что ``_ScanSignals``/``_RenameSignals`` у ProjectControl).

Границы v1: проблемы есть только для файлов, которые хоть раз открывались в
редакторе (диагностики публикуются только для didOpen-документов — ровно та
же область покрытия, что у маркеров Monaco); закрытые вкладки проблем НЕ
теряют (как в VS Code). Счётчики в статус-баре и F8-навигация — этап 10.2.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.problems_control.problems import (
    Problem,
    file_path_from_uri,
)
from pyqtp.views.problems_view import ProblemsView

if TYPE_CHECKING:
    from pathlib import Path

    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

prob_log = logging.getLogger('bcad.prob')


class _DiagSignals(QObject):
    """queued-мост: стор диагностик хаба изменился (поток event-loop -> GUI)."""

    diagnostics_changed = Signal()


class ProblemsControl(BaseControl):
    """Контрол панели «Проблемы»: снимок стора диагностик -> панель -> переход."""

    _main_wnd: IdeMainWindow

    problems_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.problems_view: ProblemsView | None = None
        self._diag_signals = _DiagSignals()
        # один и тот же callable для add/remove наблюдателя хаба
        self._diag_notify = self._diag_signals.diagnostics_changed.emit

    #####################
    # Публичный API #

    def problems_by_path(self) -> dict[str, list[Problem]]:
        """Снимок «локальный путь -> проблемы» из стора хаба."""
        by_path: dict[str, list[Problem]] = {}
        snapshot = self._main_wnd.lsp_hub.diagnostics_snapshot()
        for uri, problems in snapshot.items():
            path = file_path_from_uri(uri)
            if path is None:  # стор и так файловый — страховка от чужих uri
                continue
            by_path[path] = problems
        return by_path

    def problems_snapshot(self) -> list[dict[str, Any]]:
        """Снимок проблем для Python-консоли (путь + поля проблемы)."""
        return [
            {'path': path, **asdict(problem)}
            for path, problems in sorted(self.problems_by_path().items())
            for problem in problems
        ]

    ####################
    # Жизненный цикл #

    def setup(self) -> None:
        super().setup()
        self._main_wnd.events_ctrl.events_log_register(
            name=prob_log.name, group='LSP'
        )
        self._main_wnd.api_ctrl.register_variables(
            {'problems_show': self.problems_snapshot}
        )
        self._diag_signals.diagnostics_changed.connect(self._refresh)
        self._main_wnd.project_ctrl.project_root_changed.connect(self._on_root)
        self._main_wnd.lsp_hub.add_diagnostics_observer(self._diag_notify)

    def teardown(self) -> None:
        self._main_wnd.lsp_hub.remove_diagnostics_observer(self._diag_notify)

    def setup_views(self) -> None:
        self.problems_view = view = ProblemsView(
            self._main_wnd, key=f'{self.key}_problems_view'
        )
        self._main_wnd.view_register(view, widget_area='bottom')
        view.problem_activated.connect(
            self._main_wnd.doc_ctrl.open_definition_uri
        )
        self._refresh()

    def teardown_views(self) -> None:
        super().teardown_views()
        if self.problems_view is not None:
            self._main_wnd.view_deregister(self.problems_view.key)
            self.problems_view = None

    #####################
    # Внутреннее        #

    def _on_root(self, _root: Path | None) -> None:
        """Смена/закрытие корня проекта: пути подписи панелей пересобираем."""
        self._refresh()

    def _refresh(self) -> None:
        """GUI-поток: очередной снимок стора — в панель (через queued-мост)."""
        if self.problems_view is None:
            return
        self.problems_view.set_problems(
            self.problems_by_path(),
            self._main_wnd.project_ctrl.project_root,
        )
        self.problems_changed.emit()
