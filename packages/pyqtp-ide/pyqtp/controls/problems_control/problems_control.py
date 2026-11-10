"""Панель «Проблемы» (этапы 10.1–10.2): агрегация диагностик LSP из стора хаба.

Источник данных — стор последних ``publishDiagnostics`` внутри ``LspHub``
(хаб видит весь трафик диагностик всех серверов; модель маркеров Monaco из
Python не читается — см. план этапа 10 в ``docs/IDE_ROADMAP.md``). Изменения
стора приходят из потока event-loop хаба через queued-мост ``_DiagSignals``
(тот же паттерн, что ``_ScanSignals``/``_RenameSignals`` у ProjectControl).

Этап 10.2:

* статус-бар справа — кликабельные счётчики ошибок/предупреждений
  (``ProblemsCountWidget``, клик открывает панель) и индикатор
  инициализации LSP (``LspInitWidget``, спиннер пока у хаба есть процесс в
  состоянии «запуск» — второй стор ``LspHub.server_states()``);
* F8 / Shift+F8 — циклическая навигация по проблемам (тот же маршрут
  перехода, что панель: ``open_definition_uri``; каретка берётся из
  python-зеркала ``edit_widget.current_cursor``, как у закладок 9.7);
* фильтр «только текущий файл» в панели (чекбокс вьюхи) — F8-набор
  уважает его; активная вкладка отслеживается сигналом
  ``DocumentManager.document_activated`` + ``document_closed``.

Границы v1: проблемы есть только для файлов, которые хоть раз открывались в
редакторе (диагностики публикуются только для didOpen-документов — ровно та
же область покрытия, что у маркеров Monaco); закрытые вкладки проблем НЕ
теряют (как в VS Code). Смена активной вкладки «ручным» кликом по вкладке
KDD отдельного сигнала не даёт (python-привязки 2.x его не эмитят) — на
момент F8 активный документ переснимается через ``active_document_get()``
с focus-обходом.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.problems_control.problems import (
    Problem,
    file_path_from_uri,
    next_problem,
    severity_counts,
)
from pyqtp.controls.problems_control.status_bar_widgets import (
    LspInitWidget,
    ProblemsCountWidget,
)
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.problems_view import ProblemsView

if TYPE_CHECKING:
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

prob_log = logging.getLogger('bcad.prob')

# точка проблемы для F8-цикла: (путь, строка, колонка)
_Entry = tuple[str, int, int]


class _DiagSignals(QObject):
    """queued-мост: стор диагностик/состояний хаба изменился (поток event-loop -> GUI)."""

    diagnostics_changed = Signal()
    server_state_changed = Signal()


class ProblemsControl(BaseControl):
    """Контрол «Проблем»: снимок стора хаба -> панель/статус-бар -> переход/F8."""

    _main_wnd: IdeMainWindow

    problems_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.problems_view: ProblemsView | None = None
        self._diag_signals = _DiagSignals()
        # один и тот же callable для add/remove наблюдателя хаба
        self._diag_notify = self._diag_signals.diagnostics_changed.emit
        self._state_notify = self._diag_signals.server_state_changed.emit
        # виджеты статус-бара (создаются в setup_statusbar)
        self._count_widget: ProblemsCountWidget | None = None
        self._lsp_widget: LspInitWidget | None = None

        self.action_next_problem = Action(
            icon=QIcon(),
            title='Следующая проблема',
            parent=self,
            description='Перейти к следующей проблеме (с циклом)',
            shortcut=QKeySequence('F8'),
        )
        self.action_prev_problem = Action(
            icon=QIcon(),
            title='Предыдущая проблема',
            parent=self,
            description='Перейти к предыдущей проблеме (с циклом)',
            shortcut=QKeySequence('Shift+F8'),
        )
        # F8 — общесистемная клавиша (в редакторских вебвью она своя);
        # привязка уровня окна ловит её, когда фокус не в Monaco
        for action in (self.action_next_problem, self.action_prev_problem):
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.action_next_problem.triggered.connect(
            lambda: self.goto_problem(forward=True)
        )
        self.action_prev_problem.triggered.connect(
            lambda: self.goto_problem(forward=False)
        )
        self._set_actions_enabled(enabled=False)

    #####################
    # Публичный API #

    @property
    def status_counts_widget(self) -> ProblemsCountWidget | None:
        """Счётчики проблем в статус-баре (None — до setup_statusbar)."""
        return self._count_widget

    @property
    def status_lsp_widget(self) -> LspInitWidget | None:
        """Индикатор инициализации LSP в статус-баре (None — до setup_statusbar)."""
        return self._lsp_widget

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

    def problem_entries(self) -> list[_Entry]:
        """Набор F8-цикла: (путь, строка, колонка) с учётом фильтра панели.

        Порядок — как в дереве панели: файлы по пути, внутри файла — по
        координатам. При включённом «Только текущий файл» без активной
        вкладки набор пуст (панель в таком же состоянии).
        """
        return self._entries(self._current_doc_path())

    def _entries(self, current: str | None) -> list[_Entry]:
        """Набор F8 при заданном активном файле (см. ``problem_entries``)."""
        by_path = self.problems_by_path()
        view = self.problems_view
        only_current = view is not None and view.is_current_file_only()
        entries: list[_Entry] = []
        for path in sorted(by_path):
            if only_current and path != current:
                continue
            entries.extend(
                (path, problem.line, problem.col)
                for problem in sorted(by_path[path])
            )
        return entries

    def goto_problem(self, *, forward: bool = True) -> None:
        """F8/Shift+F8: циклический переход к следующей/предыдущей проблеме.

        Переход — тем же маршрутом, что двойной клик по панели
        (``open_definition_uri``): открывает/активирует вкладку и ставит
        каретку; повторный F8 продолжает от новой каретки (зеркало
        ``current_cursor``).
        """
        entries = self.problem_entries()
        if not entries:
            return
        target = next_problem(entries, self._cursor_position(), forward=forward)
        if target is None:
            return
        self._main_wnd.doc_ctrl.open_definition_uri(
            {
                'uri': Path(target[0]).as_uri(),
                'range': {'startLineNumber': target[1], 'startColumn': target[2]},
            }
        )

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
        self._diag_signals.server_state_changed.connect(self._refresh_lsp_state)
        self._main_wnd.project_ctrl.project_root_changed.connect(self._on_root)
        self._main_wnd.doc_ctrl.document_activated.connect(self._on_doc_activated)
        self._main_wnd.doc_ctrl.document_closed.connect(self._on_doc_closed)
        self._main_wnd.lsp_hub.add_diagnostics_observer(self._diag_notify)
        self._main_wnd.lsp_hub.add_server_state_observer(self._state_notify)

    def teardown(self) -> None:
        self._main_wnd.lsp_hub.remove_diagnostics_observer(self._diag_notify)
        self._main_wnd.lsp_hub.remove_server_state_observer(self._state_notify)

    def setup_menu(self) -> None:
        """Меню «Проблемы» (между «Закладками» и «Вид», этап 10.2)."""
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            problems_menu = menubar.by_path('Проблемы')
        except KeyError:
            problems_menu = menubar.addMenu('Проблемы', priority=-6)

        assert isinstance(problems_menu, PriorityMenu)

        problems_menu.addAction(self.action_next_problem, priority=1)
        problems_menu.addAction(self.action_prev_problem, priority=2)

    def setup_statusbar(self) -> None:
        """Счётчики проблем + индикатор инициализации LSP (правый край)."""
        statusbar = self._main_wnd.statusBar()
        self._count_widget = ProblemsCountWidget()
        statusbar.addPermanentWidget(self._count_widget)
        self._lsp_widget = LspInitWidget()
        statusbar.addPermanentWidget(self._lsp_widget)
        # клик по счётчикам — открыть панель «Проблемы» (как в VS Code)
        self._count_widget.clicked.connect(self._show_problems_panel)
        # стартовая синхронизация: подписки уже висят (setup раньше setup_ui),
        # но снимок придёт только на следующем изменении стора
        self._refresh_status_counts()
        self._refresh_lsp_state()

    def setup_views(self) -> None:
        self.problems_view = view = ProblemsView(
            self._main_wnd, key=f'{self.key}_problems_view'
        )
        self._main_wnd.view_register(view, widget_area='bottom')
        view.problem_activated.connect(
            self._main_wnd.doc_ctrl.open_definition_uri
        )
        view.current_file_only_changed.connect(self._update_actions_enabled)
        view.set_current_document(self._current_doc_path())
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

    def _on_doc_activated(self, view: Any) -> None:
        """Вкладка реально активирована — берём ключ прямо из сигнала.

        В момент эмита Qt-фокус ещё может быть в прошлой вкладке (смена
        таба и подъём фокуса KDD идут в том же эмите ``ensure_visible``,
        но слотом ПОСЛЕ нашего) — ``active_document_get()`` в этот миг
        врёт, поэтому панель получает авторитетный ключ из сигнала.
        """
        if self.problems_view is not None:
            self.problems_view.set_current_document(view.key)
        self._update_actions_enabled(current=view.key)

    def _on_doc_closed(self, _view: Any) -> None:
        """Вкладка закрыта — переснимаем активного позже дока.

        Сосед за закрытой активной вкладкой активируется синхронно сразу
        после эмита (свой ``document_activated`` его тоже обновляет), но
        закрытие неактивной вкладки ничего не эмитит — снимок через
        ``singleShot`` приводит всё в соответствие в любом случае.
        """
        QTimer.singleShot(0, self._sync_current_doc)

    def _sync_current_doc(self) -> None:
        if self.problems_view is not None:
            self.problems_view.set_current_document(self._current_doc_path())
        self._update_actions_enabled()

    def _current_doc_path(self) -> str | None:
        """Ключ-путь активной вкладки (None — вкладок нет)."""
        doc_view = self._main_wnd.doc_ctrl.active_document_get()
        return doc_view.key if doc_view is not None else None

    def _cursor_position(self) -> _Entry | None:
        """Положение каретки активного документа (python-зеркало Monaco)."""
        path = self._current_doc_path()
        if path is None:
            return None
        doc_view = self._main_wnd.doc_ctrl.document_view(path)
        if doc_view is None:
            return None
        cursor = getattr(getattr(doc_view, 'edit_widget', None), 'current_cursor', None)
        if not isinstance(cursor, dict):
            return None
        line = cursor.get('line')
        col = cursor.get('column')
        if isinstance(line, bool) or not isinstance(line, int):
            return None
        if isinstance(col, bool) or not isinstance(col, int):
            return None
        return path, line, col

    def _refresh(self) -> None:
        """GUI-поток: очередной снимок стора — в панель и статус-бар."""
        by_path = self.problems_by_path()
        if self.problems_view is not None:
            self.problems_view.set_problems(
                by_path,
                self._main_wnd.project_ctrl.project_root,
            )
        self._refresh_status_counts(by_path)
        self._update_actions_enabled()
        self.problems_changed.emit()

    def _refresh_status_counts(
        self, by_path: dict[str, list[Problem]] | None = None
    ) -> None:
        """Счётчики в статус-баре — ВСЕГДА по полному набору (фильтр панели
        на них не влияет).
        """
        if self._count_widget is None:
            return
        if by_path is None:
            by_path = self.problems_by_path()
        all_problems = [p for problems in by_path.values() for p in problems]
        self._count_widget.set_counts(severity_counts(all_problems))

    def _refresh_lsp_state(self) -> None:
        """Индикатор инициализации LSP — по стору состояний хаба (queued-мост)."""
        if self._lsp_widget is not None:
            self._lsp_widget.set_states(self._main_wnd.lsp_hub.server_states())

    def _update_actions_enabled(self, current: str | None = None) -> None:
        """F8-экшены активны, если есть куда прыгать (снимок при ``current``.

        ``current=None`` — снимать активного hybrid-обходом (свежий вызов);
        в обработчике ``document_activated`` передаём ключ из сигнала —
        гибрид в момент эмита показывает старую вкладку (фокус ещё не
        переехал).
        """
        if current is None:
            self._set_actions_enabled(enabled=bool(self.problem_entries()))
        else:
            self._set_actions_enabled(enabled=bool(self._entries(current)))

    def _set_actions_enabled(self, *, enabled: bool) -> None:
        self.action_next_problem.setEnabled(enabled)
        self.action_prev_problem.setEnabled(enabled)

    def _show_problems_panel(self) -> None:
        if self.problems_view is not None:
            self.problems_view.ensure_visible()
