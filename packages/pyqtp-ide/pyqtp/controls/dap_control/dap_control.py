"""Контрол отладчика DAP (этап 11, срез D1).

Владелец сессии отладки: адаптер ``debugpy.adapter`` запускается лениво в
своём asyncio-потоке (тот же паттерн, что ``LspHub._thread_main``), события
сессии приходят из потока event-loop через queued-мост ``_DapSignals``
(паттерн ``_DiagSignals``/``_ScanSignals``). Состояние — стат-машина
``IDLE → RUNNING ⇄ STOPPED → IDLE``; наблюдатели (панель/тесты) — сигнал
``state_changed``.

Брейкпоинты проекта (``.pyqtp/breakpoints.json``, паттерн 9.7) живут здесь
же: переключение (F9/клик по дорожке Monaco), персистентность, раздача строк
открытым редакторам (``set_breakpoints``) и синхронизация в живую сессию
(``setBreakpoints`` + хук ``on_breakpoints_ready`` на handshake).

Треды/корутины: все ``DapSession``-операции живут только в loop-потоке;
GUI-поток дергает их через ``run_coroutine_threadsafe`` и ждёт результат
``future.result(timeout)`` (F5 — блокирующая операция, как «Открыть папку»
у проекта). Поколенческий гейт ``_gen`` отсеивает события остановленной
сессии после остановки/перезапуска.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.dap_control import breakpoints as bp_mod
from pyqtp.controls.dap_control.dap_process import adapter_command
from pyqtp.controls.dap_control.dap_session import DapError, DapSession
from pyqtp.controls.dap_control.launch_config import (
    LaunchConfig,
    default_config,
    launch_arguments,
    read_launch_file,
    resolve_config,
)
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.debug_console_view import DebugConsoleView

if TYPE_CHECKING:
    from collections.abc import Callable

    from pyqtp.controls.dap_control.breakpoints import Breakpoint
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

dbg_log = logging.getLogger('bcad.dbg')

# состояния стат-машины сессии
STATE_IDLE = 'idle'
STATE_RUNNING = 'running'
STATE_STOPPED = 'stopped'

# сколько ждать завершения пусковой корутины в GUI-потоке (F5)
_START_WAIT_S = 30.0
# сколько ждать graceful-ответа команд continue/step в GUI-потоке
_CMD_WAIT_S = 5.0

_MENU_TITLE = 'Отладка'
# после «LSP» (-4), до «Вид» (-5) — priority=-3
_MENU_PRIORITY = -3

# короткая строка состояния в шапке панели «Консоль отладки»
_STATUS_TEXT = {
    STATE_IDLE: 'Не отлаживаем',
    STATE_RUNNING: 'Выполняется',
    STATE_STOPPED: 'Остановлено',
}


class _DapSignals(QObject):
    """queued-мост событий сессии: поток event-loop -> GUI.

    ``dap_event`` — сырое DAP-событие (поколение сессии + имя + body);
    ``stopped_revealed`` — кадр останова из ``stackTrace`` (gen, path, line,
    column), его уже надо показать в GUI; ``session_gone`` — сессия умерла
    (причина '' — штатный ``terminated``).

    NB: имя не ``event`` — ``QObject.event`` это метод Qt-стабов, поле-сигнал
    с таким именем конфликтовал бы с ним (и по типам, и по dispatch).
    """

    dap_event = Signal(int, str, dict)
    stopped_revealed = Signal(int, str, int, int)
    session_gone = Signal(int, str)


@dataclass
class _SessionRuntime:
    """Всё, что живо, пока идёт отладка (только loop-поток трогает сессию)."""

    session: DapSession
    gen: int
    root: Path
    thread_id: int = 1  # debugpy стартовый поток; обновляем из stopped-событий
    # путь текущего кадра и строка останова (для current-line), ''/0 — нет
    current_path: str = ''
    current_line: int = 0


class DapControl(BaseControl):
    """Контрол отладки: сессия DAP + брейкпоинты проекта + консоль."""

    _main_wnd: IdeMainWindow

    state_changed = Signal(str)  # STATE_*

    def __init__(self) -> None:
        super().__init__()
        self.debug_console_view: DebugConsoleView | None = None
        self._signals = _DapSignals()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._loop_failed = ''
        # защита от параллельных ensure_started/shutdown
        self._lifecycle_lock = threading.Lock()

        self._runtime: _SessionRuntime | None = None
        self._gen = 0
        self._state = STATE_IDLE
        # GUI-зеркало строки останова (путь/строка; 0 — подсветки нет)
        self._exec_path = ''
        self._exec_line = 0

        self._breakpoints: list[Breakpoint] = []

        # команды запуска (launch.json): None — файла нет, [] — пустой
        self._configs_cache: list[LaunchConfig] | None = None

        self.action_debug_start = self._make_action(
            'Начать отладку', 'Запустить отлаживаемую программу (F5)', 'F5',
        )
        self.action_debug_stop = self._make_action(
            'Остановить отладку', 'Остановить сессию отладки (Shift+F5)',
            'Shift+F5',
        )
        self.action_debug_toggle_breakpoint = self._make_action(
            'Переключить брейкпоинт',
            'Поставить/снять брейкпоинт на строке каретки (F9)', 'F9',
        )
        self.action_debug_step_over = self._make_action(
            'Шаг с проходом', 'Выполнить строку, не входя в функции (F10)',
            'F10',
        )
        self.action_debug_step_into = self._make_action(
            'Шаг с входом', 'Выполнить строку с входом в функцию (F11)',
            'F11',
        )
        self.action_debug_step_out = self._make_action(
            'Шаг с выходом', 'Выполнить до выхода из текущей функции '
            '(Shift+F11)', 'Shift+F11',
        )

    def _make_action(
        self, title: str, description: str, shortcut: str,
    ) -> QAction:
        """Экшен меню «Отладка» с хоткеем уровня окна.

        ``QAction`` напрямую (не ``pyqtp.utils.action.Action`` — её конструктор
        форсит ApplicationShortcut, а нам нужен WindowShortcut: связки живут
        и когда фокус в редакторе Monaco/webview, см. этап 10.2).
        """
        action = QAction(QIcon(), title, self)
        action.setToolTip(description)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        return action

    # ------------------------------------------------------------------ #
    # Жизненный цикл                                                    #
    # ------------------------------------------------------------------ #

    def setup(self) -> None:
        super().setup()
        self._main_wnd.events_ctrl.events_log_register(
            name=dbg_log.name, group='Отладка',
        )
        self._main_wnd.project_ctrl.project_root_changed.connect(self._on_root)

        self.action_debug_start.triggered.connect(self.start_or_continue)
        self.action_debug_stop.triggered.connect(self.stop_debugging)
        self.action_debug_toggle_breakpoint.triggered.connect(
            self.toggle_breakpoint_active,
        )
        self.action_debug_step_over.triggered.connect(self.step_over)
        self.action_debug_step_into.triggered.connect(self.step_into)
        self.action_debug_step_out.triggered.connect(self.step_out)

        self._signals.dap_event.connect(self._on_dap_event)
        self._signals.stopped_revealed.connect(self._on_stopped_revealed)
        self._signals.session_gone.connect(self._on_session_gone)

        self._main_wnd.api_ctrl.register_variables({
            'debug_start': self.start_debugging,
            'debug_stop': self.stop_debugging,
            'debug_continue': self.continue_execution,
            'debug_step_over': self.step_over,
            'debug_step_into': self.step_into,
            'debug_step_out': self.step_out,
            'debug_status': self.debug_status,
            'debug_breakpoints_show': self.breakpoints_snapshot,
        })
        self._update_actions_enabled()

    def setup_menu(self) -> None:
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            debug_menu = menubar.by_path(_MENU_TITLE)
        except KeyError:
            debug_menu = menubar.addMenu(_MENU_TITLE, priority=_MENU_PRIORITY)

        assert isinstance(debug_menu, PriorityMenu)

        debug_menu.addAction(self.action_debug_start, priority=1)
        debug_menu.addAction(self.action_debug_stop, priority=2)
        debug_menu.addAction(self.action_debug_toggle_breakpoint, priority=3)
        debug_menu.addAction(self.action_debug_step_over, priority=4)
        debug_menu.addAction(self.action_debug_step_into, priority=5)
        debug_menu.addAction(self.action_debug_step_out, priority=6)

    def setup_views(self) -> None:
        self.debug_console_view = view = DebugConsoleView(
            self._main_wnd, key=f'{self.key}_debug_console_view',
        )
        self._main_wnd.view_register(view, widget_area='bottom')
        view.clear_requested.connect(self.clear_console)
        view.set_status_text(_STATUS_TEXT.get(self._state, self._state))

        self._main_wnd.doc_ctrl.document_opened.connect(self._wire_doc_view)

        root = self._main_wnd.project_ctrl.project_root
        if root is not None:
            self._breakpoints = self._load_breakpoints(root)
            self._broadcast_breakpoints()
        self._update_actions_enabled()

    def teardown_views(self) -> None:
        super().teardown_views()
        if self.debug_console_view is not None:
            self._main_wnd.view_deregister(self.debug_console_view.key)
            self.debug_console_view = None

    def on_shutdown(self) -> bool:
        self._stop_session_blocking(terminate=True)
        self._stop_loop()
        return True

    def teardown(self) -> None:
        self._stop_session_blocking(terminate=True)
        self._stop_loop()

    # ------------------------------------------------------------------ #
    # Состояние — только GUI-поток                                      #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        """Текущее состояние стат-машины (STATE_IDLE/RUNNING/STOPPED)."""
        return self._state

    def debug_status(self) -> dict[str, Any]:
        """Снимок для консоли: состояние + кадр останова + число брейкпоинтов."""
        rt = self._runtime
        return {
            'state': self._state,
            'path': rt.current_path if rt else '',
            'line': rt.current_line if rt else 0,
            'breakpoints': len(self._breakpoints),
        }

    def _set_state(self, state: str) -> None:
        """Смена стат-машины (только GUI-поток) + сигнал наблюдателям."""
        if state == self._state:
            return
        self._state = state
        self._update_actions_enabled()
        if self.debug_console_view is not None:
            self.debug_console_view.set_status_text(_STATUS_TEXT.get(state, state))
        self.state_changed.emit(state)

    def _update_actions_enabled(self) -> None:
        """Гейтинг экшенов меню: запуск — с проектом, шаги — в STOPPED."""
        has_project = self._main_wnd.project_ctrl.project_root is not None
        self.action_debug_start.setEnabled(has_project and self._state != STATE_RUNNING)
        self.action_debug_stop.setEnabled(self._state != STATE_IDLE)
        stopped = self._state == STATE_STOPPED
        self.action_debug_step_over.setEnabled(stopped)
        self.action_debug_step_into.setEnabled(stopped)
        self.action_debug_step_out.setEnabled(stopped)
        self.action_debug_toggle_breakpoint.setEnabled(has_project)

    # ------------------------------------------------------------------ #
    # Брейкпоинты проекта                                               #
    # ------------------------------------------------------------------ #

    def breakpoints_snapshot(self) -> list[dict[str, Any]]:
        """Список брейкпоинтов проекта (формат консоли/тестов)."""
        return [{'path': item.path, 'line': item.line} for item in self._breakpoints]

    def breakpoint_lines_for(self, path: str) -> list[int]:
        """Отсортированные строки брейкпоинтов одного файла."""
        return bp_mod.breakpoints_for(self._breakpoints, str(Path(path)))

    def toggle_breakpoint_at(self, path: str, line: int) -> bool:
        """Переключает брейкпоинт (F9/клик по дорожке): файл + редакторы + сессия."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            dbg_log.info('Брейкпоинт: проект не открыт')
            return False
        resolved = str(Path(path))
        if not self._under_root(root, resolved):
            dbg_log.info('Брейкпоинт вне корня проекта: %s', resolved)
            return False
        updated = bp_mod.toggle_breakpoint(self._breakpoints, resolved, int(line))
        try:
            bp_mod.write_breakpoints(root, updated)
        except OSError as exc:
            dbg_log.warning('Не удалось сохранить брейкпоинты проекта: %s', exc)
            return False
        self._breakpoints = updated
        self._broadcast_breakpoints()
        self._sync_breakpoints_to_session(resolved)
        return True

    def toggle_breakpoint_active(self) -> None:
        """F9: брейкпоинт на строке каретки активного документа."""
        if self._main_wnd.project_ctrl.project_root is None:
            dbg_log.info('Брейкпоинт: проект не открыт')
            return
        view = self._main_wnd.doc_ctrl.active_document_get()
        if view is None:
            dbg_log.info('Брейкпоинт: нет активного документа')
            return
        edit_widget = getattr(view, 'edit_widget', None)
        cursor = getattr(edit_widget, 'current_cursor', None)
        line = cursor.get('line') if isinstance(cursor, dict) else None
        self.toggle_breakpoint_at(view.key, line if isinstance(line, int) else 1)

    def _load_breakpoints(self, root: Path) -> list[Breakpoint]:
        """Читает ``.pyqtp/breakpoints.json``, снимает записи мёртвых файлов."""
        stored = bp_mod.read_breakpoints(root)
        kept, skipped = bp_mod.filter_breakpoints(stored)
        for item in skipped:
            dbg_log.info(
                'Файл из брейкпоинтов отсутствует, пропущено: %s (строка %d)',
                item.path, item.line,
            )
        kept.sort(key=lambda item: (item.path, item.line))  # канон, как при записи
        if kept and len(kept) != len(stored):
            try:
                bp_mod.write_breakpoints(root, kept)
            except OSError as exc:
                dbg_log.warning('Не удалось сохранить брейкпоинты проекта: %s', exc)
        if kept:
            dbg_log.info('Брейкпоинты проекта загружены: %d', len(kept))
        return kept

    def _on_root(self, root: Path | None) -> None:
        """Смена/закрытие корня: остановить сессию, перезагрузить брейкпоинты."""
        self._stop_session_blocking(terminate=True)
        self._configs_cache = None
        if root is None:
            self._breakpoints = []
            self._clear_execution_lines()
            self._update_actions_enabled()
            return
        self._breakpoints = self._load_breakpoints(root)
        self._broadcast_breakpoints()
        self._update_actions_enabled()

    @staticmethod
    def _under_root(root: Path, path_str: str) -> bool:
        candidate = Path(path_str)
        return candidate == root or root in candidate.parents

    # ------------------------------------------------------------------ #
    # Раздача строк открытым редакторам (паттерн 9.7)                   #
    # ------------------------------------------------------------------ #

    _WIRED_ATTR = '_pyqtp_breakpoint_wired'

    def _wire_doc_view(self, doc_view: Any) -> None:
        """Свежеоткрытый документ: его строки-брейкпоинты его редактору.

        Плюс клик по дорожке Monaco (``breakpoint_toggle_requested``) — одно
        подключение на вьюху (флаг на объекте вьюхи, как ``_pyqtp_bookmark_wired``).
        """
        self._push_lines_to_view(doc_view.key)
        self._push_exec_line_to_view(doc_view.key)
        edit_widget = getattr(doc_view, 'edit_widget', None)
        toggle_signal = getattr(edit_widget, 'breakpoint_toggle_requested', None)
        connect = getattr(toggle_signal, 'connect', None)
        if connect is None or getattr(doc_view, self._WIRED_ATTR, False):
            return
        setattr(doc_view, self._WIRED_ATTR, True)
        connect(lambda line, key=doc_view.key: self.toggle_breakpoint_at(key, line))

    def _push_lines_to_view(self, path: str) -> None:
        view = self._main_wnd.doc_ctrl.document_view(path)
        if view is None:
            return
        edit_widget = getattr(view, 'edit_widget', None)
        push = getattr(edit_widget, 'set_breakpoints', None)
        if not callable(push):
            return
        push(self.breakpoint_lines_for(path))

    def _broadcast_breakpoints(self) -> None:
        """Раздаёт строки брейкпоинтов всем открытым документам проекта."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            return
        for doc_path in self._main_wnd.doc_ctrl.documents_under(root):
            self._push_lines_to_view(str(doc_path))

    # ------------------------------------------------------------------ #
    # Консоль отладки (read-only, срез D1)                              #
    # ------------------------------------------------------------------ #

    def _console_line(self, text: str) -> None:
        if self.debug_console_view is not None:
            self.debug_console_view.append_line(text)

    def clear_console(self) -> None:
        """Кнопка «Очистить» панели и новый запуск."""
        if self.debug_console_view is not None:
            self.debug_console_view.clear_console()

    # ------------------------------------------------------------------ #
    # Current-line (подсветка строки останова)                          #
    # ------------------------------------------------------------------ #

    def _push_exec_line_to_view(self, path: str) -> None:
        """Шлёт редактору строку останова (0 — снять подсветку)."""
        view = self._main_wnd.doc_ctrl.document_view(path)
        if view is None:
            return
        edit_widget = getattr(view, 'edit_widget', None)
        push = getattr(edit_widget, 'set_execution_line', None)
        if not callable(push):
            return
        line = self._exec_line if path == self._exec_path else 0
        push({'line': int(line)})

    def _broadcast_execution_lines(self) -> None:
        """Пересылает строку останова всем открытым документам проекта."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            return
        for doc_path in self._main_wnd.doc_ctrl.documents_under(root):
            self._push_exec_line_to_view(str(doc_path))

    def _clear_execution_lines(self) -> None:
        """Снимает подсветку останова со всех редакторов."""
        self._exec_path = ''
        self._exec_line = 0
        self._broadcast_execution_lines()

    # ------------------------------------------------------------------ #
    # Команды отладки (GUI-поток; корутины уходят в loop-поток)         #
    # ------------------------------------------------------------------ #

    def start_or_continue(self) -> None:
        """F5: запуск (IDLE) или продолжение выполнения (STOPPED)."""
        if self._state == STATE_STOPPED:
            self.continue_execution()
        elif self._state == STATE_IDLE:
            self.start_debugging()

    def start_debugging(self) -> str:
        """Запуск сессии отладки (F5/console API). Возвращает текст ошибки или ''."""
        if self._state != STATE_IDLE:
            dbg_log.info('Отладка: сессия уже активна')
            return 'Сессия отладки уже активна'
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            dbg_log.info('Отладка: проект не открыт')
            return 'Проект не открыт'
        config, error = self._prepare_launch(root)
        if config is None:
            return error
        try:
            self._ensure_loop()
        except DapError as exc:
            self._console_line(f'[Отладка] {exc}')
            return str(exc)
        error = self._schedule_start(config, root)
        if error:
            return error
        self._set_state(STATE_RUNNING)
        return ''

    def _schedule_start(self, config: LaunchConfig, root: Path) -> str:
        """(GUI) Поставить пусковую корутину в loop-поток и дождаться handshake."""
        self._gen += 1
        gen = self._gen
        self._exec_path = ''
        self._exec_line = 0
        self.clear_console()
        self._console_line(f'[Отладка] Запуск «{config.name}»: {config.program}')
        loop = self._loop
        assert loop is not None
        future = asyncio.run_coroutine_threadsafe(
            self._start_session(config, root, gen, self._breakpoints_initial_payload()),
            loop,
        )
        try:
            error = future.result(_START_WAIT_S)
        except concurrent.futures.TimeoutError:
            # сброшенное поколение отсеивает всё, что добьёт запущенная корутина
            self._gen += 1
            dbg_log.warning('Сессия отладки не поднялась за %s с', _START_WAIT_S)
            self._console_line('[Отладка] Запуск не завершился по тайм-ауту')
            return 'Запуск не завершился по тайм-ауту'
        if error:
            self._console_line(f'[Отладка] {error}')
        return error

    def _resolve_launch_config(self, root: Path) -> LaunchConfig | None:
        """Первая конфигурация ``launch.json``, иначе — активный документ (решение D1)."""
        configs = read_launch_file(root)
        self._configs_cache = configs
        if configs:
            if len(configs) > 1:
                dbg_log.info(
                    'launch.json: %d конфигураций, берётся первая «%s» '
                    '(выбор конфигурации — этап D3)',
                    len(configs), configs[0].name,
                )
            return resolve_config(configs[0], root)
        active = self._main_wnd.doc_ctrl.active_document_get()
        return default_config(root, active.key if active is not None else None)

    def _prepare_launch(self, root: Path) -> tuple[LaunchConfig | None, str]:
        """Конфигурация запуска + проверка файла программы ('' — без ошибок).

        Сообщения об отказе идут в лог и консоль отладки здесь — у вызывающего
        остаётся только текст для API.
        """
        config = self._resolve_launch_config(root)
        if config is None:
            dbg_log.info('Отладка: нет активного документа и конфигураций launch.json')
            self._console_line('[Отладка] Нет активного документа (и нет конфигураций)')
            return None, 'Нет активного документа (и нет конфигураций launch.json)'
        program = Path(config.program)
        if not program.is_file():
            msg = f'Программа не найдена: {program}'
            dbg_log.warning('Отладка: %s', msg)
            self._console_line(f'[Отладка] {msg}')
            return None, msg
        return config, ''

    def _breakpoints_initial_payload(self) -> dict[str, list[int]]:
        """Снимок брейкпоинтов для handshake (только живые файлы)."""
        grouped: dict[str, list[int]] = {}
        for item in self._breakpoints:
            if Path(item.path).is_file():
                grouped.setdefault(item.path, []).append(item.line)
        return {path: sorted(lines) for path, lines in grouped.items()}

    def continue_execution(self) -> str:
        """Продолжить выполнение (F5 из останова / console API)."""
        return self._run_command('continue', 'Продолжение')

    def step_over(self) -> str:
        """Шаг с проходом (F10)."""
        return self._run_command('next', 'Шаг с проходом')

    def step_into(self) -> str:
        """Шаг с входом (F11)."""
        return self._run_command('stepIn', 'Шаг с входом')

    def step_out(self) -> str:
        """Шаг с выходом (Shift+F11)."""
        return self._run_command('stepOut', 'Шаг с выходом')

    def _run_command(self, command: str, label: str) -> str:
        """DAP-команда по потоку остановки (ждёт ответ — команды быстрые)."""
        if self._state != STATE_STOPPED:
            dbg_log.info('%s: не в останове', label)
            return f'{label}: не в останове'
        rt = self._runtime
        loop = self._loop
        if rt is None or loop is None:
            return 'Сессия отладки не активна'

        async def _coro() -> str:
            if self._runtime is not rt:
                return 'Сессия отладки не активна'
            try:
                await rt.session.request(
                    command, {'threadId': rt.thread_id}, timeout=_CMD_WAIT_S,
                )
            except DapError as exc:
                return f'{label}: {exc}'
            return ''

        future = asyncio.run_coroutine_threadsafe(_coro(), loop)
        try:
            error = future.result(_CMD_WAIT_S + 2.0)
        except concurrent.futures.TimeoutError:
            return f'{label}: адаптер не ответил за {_CMD_WAIT_S + 2.0:.0f} с'
        if error:
            self._console_line(f'[Отладка] {error}')
            return error
        self._set_state(STATE_RUNNING)
        return ''

    def stop_debugging(self) -> str:
        """Остановить сессию (Shift+F5 / console API)."""
        if self._state == STATE_IDLE and self._runtime is None:
            return ''
        self._console_line('[Отладка] Остановка по требованию')
        self._stop_session_blocking(terminate=True)
        return ''

    # ------------------------------------------------------------------ #
    # Приём событий сессии (queued-мост -> GUI-поток)                   #
    # ------------------------------------------------------------------ #

    def _on_dap_event(self, gen: int, name: str, body: dict[str, Any]) -> None:
        """Слот queued-моста: сырое DAP-событие живой сессии ``gen``."""
        if gen != self._gen:
            return
        if name == 'output':
            category = str(body.get('category') or 'console')
            text = str(body.get('output') or '')
            if not text:
                return
            if category in ('stdout', 'stderr', 'console'):
                if self.debug_console_view is not None:
                    self.debug_console_view.append_text(text)
            else:
                # telemetry/important — только в лог (debugpy болтлив)
                dbg_log.debug('[DAP output/%s] %s', category, text.strip())
        elif name == 'exited':
            code = body.get('exitCode')
            self._console_line(f'[Отладка] Программа завершилась (код {code})')
        elif name == 'stopped':
            rt = self._runtime
            thread_id = body.get('threadId')
            if rt is not None and isinstance(thread_id, int):
                rt.thread_id = thread_id
            self._request_stopped_info(gen)
        # initialized/process/thread/module/continued/terminated/
        # debugpySockets и прочие сообщения debugpy — терпим молча;
        # разбор останова/завершения — ниже по stackTrace и session_gone

    def _request_stopped_info(self, gen: int) -> None:
        """На «stopped»: кадр ``stackTrace`` в loop-потоке, наружу — queued-сигнал.

        У ``stopped``-события НЕТ строки останова (проверено debugpy 1.8.21) —
        строка/путь/колонка берутся из первого кадра ``stackTrace``.
        """
        rt = self._runtime
        loop = self._loop
        if rt is None or loop is None:
            return

        async def _coro() -> None:
            try:
                frames = await rt.session.stack_trace(rt.thread_id)
            except (DapError, ConnectionError, OSError) as exc:
                dbg_log.warning('stackTrace не снят: %s', exc)
                return
            if not frames:
                return
            frame = frames[0]
            source = frame.get('source')
            path = ''
            if isinstance(source, dict):
                raw_path = source.get('path')
                if isinstance(raw_path, str):
                    path = raw_path
            line = frame.get('line')
            column = frame.get('column')
            rt.current_path = path
            rt.current_line = line if isinstance(line, int) else 0
            self._signals.stopped_revealed.emit(
                gen, path, rt.current_line,
                column if isinstance(column, int) else 1,
            )

        asyncio.run_coroutine_threadsafe(_coro(), loop).add_done_callback(
            _log_future_error,
        )

    def _on_stopped_revealed(
        self, gen: int, path: str, line: int, column: int,
    ) -> None:
        """Останов показан: STOPPED, current-line, переход каретки к кадру."""
        if gen != self._gen or self._state == STATE_IDLE or line < 1:
            return
        self._set_state(STATE_STOPPED)
        self._exec_path = path
        self._exec_line = line
        self._broadcast_execution_lines()
        try:
            uri = Path(path).as_uri()
        except ValueError:
            dbg_log.warning('Кадр останова не превращается в uri: %s', path)
            return
        # тот же маршрут перехода, что LSP-навигация/закладки/проблемы:
        # open-or-activate вкладки + каретка с центрированием
        self._main_wnd.doc_ctrl.open_definition_uri({
            'uri': uri,
            'range': {'startLineNumber': line, 'startColumn': column},
        })

    def _on_session_gone(self, gen: int, reason: str) -> None:
        """Сессия умерла (terminated/смерть адаптера): стат-машина в IDLE."""
        if gen != self._gen:
            return
        rt = self._runtime
        self._runtime = None
        if rt is not None:
            # сессия умерла сама — закрываем транспорт (без terminate),
            # иначе Process.__del__ вылезет unraisable после закрытия loop
            loop = self._loop
            if loop is not None:
                with contextlib.suppress(RuntimeError):
                    asyncio.run_coroutine_threadsafe(
                        rt.session.close(terminate=False), loop,
                    ).add_done_callback(_log_future_error)
        if self._state == STATE_IDLE:
            return
        self._console_line(
            f'[Отладка] {reason}' if reason else '[Отладка] Отладка завершена',
        )
        self._set_state(STATE_IDLE)
        self._clear_execution_lines()

    def _sync_breakpoints_to_session(self, path: str) -> None:
        """Новый набор строк одного файла — в живую сессию (fire-and-forget)."""
        rt = self._runtime
        loop = self._loop
        if rt is None or loop is None or self._state == STATE_IDLE:
            return
        lines = self.breakpoint_lines_for(path)

        async def _coro() -> None:
            if self._runtime is not rt or rt.session.closed:
                return
            if not isinstance(rt.session, _ControlledDapSession):
                return
            try:
                await rt.session.push_file_breakpoints(path, lines)
            except (DapError, ConnectionError, OSError) as exc:
                dbg_log.warning('Брейкпоинты не обновлены в сессии: %s', exc)

        asyncio.run_coroutine_threadsafe(_coro(), loop).add_done_callback(
            _log_future_error,
        )

    # ------------------------------------------------------------------ #
    # Жизненный цикл сессии и loop-потока                               #
    # ------------------------------------------------------------------ #

    def _stop_session_blocking(self, *, terminate: bool) -> None:
        """GUI-поток: сброс поколения + закрытие сессии с ожиданием.

        Идемпотентно (IDLE без сессии — ничего не делаем). Поколение сбрасывается
        ДО закрытия: ``session_gone`` от ``_watch_session`` для этой сессии
        приходит уже «чужим» и игнорируется — всю очистку делаем здесь.
        """
        rt = self._runtime
        loop = self._loop
        self._gen += 1
        if rt is not None and loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._close_runtime(rt, terminate=terminate), loop,
            )
            try:
                future.result(_CMD_WAIT_S + 5.0)
            except concurrent.futures.TimeoutError:
                dbg_log.warning('Сессия отладки не закрылась своевременно')
            except (DapError, OSError, RuntimeError) as exc:
                dbg_log.warning('Сбой при закрытии сессии отладки: %s', exc)
        if self._runtime is rt and rt is not None:
            self._runtime = None
        self._set_state(STATE_IDLE)
        self._clear_execution_lines()

    def _ensure_loop(self) -> None:
        """Ленивый (идемпотентный) подъём потока отладки со своим циклом."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._thread is not None:
                self._thread.join(2.0)
                self._thread = None
            self._loop_ready.clear()
            self._loop_failed = ''
            self._thread = threading.Thread(
                target=self._thread_main, name='DapControl', daemon=True,
            )
            self._thread.start()
            if not self._loop_ready.wait(10.0):
                self._thread.join(2.0)
                self._thread = None
                msg = 'Поток отладки не поднялся за 10 секунд'
                raise DapError(msg)

    def _thread_main(self) -> None:
        """Тело потока отладки: ручной asyncio-цикл до ``loop.stop``."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            # снять оставшиеся задачи (например _watch_session) и закрыть цикл
            self._loop = None
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(
                        asyncio.gather(*tasks, return_exceptions=True),
                    )
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def _stop_loop(self) -> None:
        """Остановить поток отладки (teardown/shutdown)."""
        with self._lifecycle_lock:
            thread = self._thread
            loop = self._loop
            if thread is None:
                return
            if loop is not None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(loop.stop)
            thread.join(10.0)
            if thread.is_alive():
                dbg_log.warning('Поток отладки не завершился корректно')
            self._thread = None
            self._loop = None

    async def _start_session(
        self,
        config: LaunchConfig,
        root: Path,
        gen: int,
        initial_breakpoints: dict[str, list[int]],
    ) -> str:
        """(loop-поток) Запуск адаптера + handshake; '' — успех, иначе текст."""
        session = _ControlledDapSession(
            adapter_command(),
            lambda name, body: self._signals.dap_event.emit(gen, name, body),
            initial_breakpoints=initial_breakpoints,
            cwd=str(root),
        )
        runtime = _SessionRuntime(session=session, gen=gen, root=root)
        try:
            await session.start()
            error = await session.handshake(launch_arguments(config))
        except (DapError, ConnectionError, OSError, TimeoutError) as exc:
            await session.close(terminate=True)
            return f'Сбой запуска адаптера: {exc}'
        if error:
            await session.close(terminate=True)
            return error
        if self._gen != gen:
            # старт отменили (Stop/смена проекта/тайм-аут ожидания в GUI)
            await session.close(terminate=True)
            return 'Запуск отменён'
        self._runtime = runtime
        asyncio.get_running_loop().create_task(self._watch_session(runtime))
        return ''

    async def _close_runtime(
        self, runtime: _SessionRuntime, *, terminate: bool,
    ) -> None:
        """(loop-поток) Закрыть сессию рантайма (гейт: он ещё актуальный)."""
        if self._runtime is not runtime:
            return
        await runtime.session.close(terminate=terminate)
        self._runtime = None

    async def _watch_session(self, runtime: _SessionRuntime) -> None:
        """(loop-поток) Ждём terminated/смерть процесса -> ``session_gone``.

        Отмена задачи (``_thread_main`` при остановке цикла) — штатная:
        сессия уже закрывается контролом, сигнал дублировать не нужно.
        """
        session = runtime.session
        proc = session.process
        waits: list[asyncio.Task[Any]] = [
            asyncio.ensure_future(session.terminated_event.wait()),
        ]
        if proc is not None:
            waits.append(asyncio.ensure_future(proc.wait()))
        _done, pending = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        reason = ''
        if not session.terminated_event.is_set():
            code = proc.returncode if proc is not None else None
            reason = f'Адаптер отладки завершился (код {code})'
        self._signals.session_gone.emit(runtime.gen, reason)


def _log_future_error(future: concurrent.futures.Future) -> None:
    """Логирует проглоченные исключения fire-and-forget корутин."""
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        dbg_log.warning('Необработанная ошибка корутины отладки: %s', exc)


class _ControlledDapSession(DapSession):
    """``DapSession`` с раздачей брейкпоинтов на handshake.

    Хук ``on_breakpoints_ready`` (между ``initialized`` и ``configurationDone``):
    рассылает ``setBreakpoints`` по снимку набора контрола, сделанному до старта
    сессии (дальнейшие правки — отдельным маршрутом ``push_file_breakpoints``).
    ``verified`` — подтверждённые адаптером строки по файлам (в файл проекта НЕ
    пишется: верификация — свойство живой сессии).
    """

    def __init__(
        self,
        command: list[str],
        on_event: Callable[[str, dict[str, Any]], None],
        *,
        initial_breakpoints: dict[str, list[int]],
        cwd: str | None = None,
    ) -> None:
        super().__init__(command, on_event, cwd=cwd)
        self.initial_breakpoints = dict(initial_breakpoints)
        self.verified: dict[str, list[int]] = {}

    async def on_breakpoints_ready(self) -> None:
        for path, lines in self.initial_breakpoints.items():
            try:
                await self.push_file_breakpoints(path, lines)
            except (DapError, ConnectionError, OSError):
                dbg_log.warning('Не удалось раздать брейкпоинты файла: %s', path)

    async def push_file_breakpoints(self, path: str, lines: list[int]) -> None:
        """``setBreakpoints`` одного файла; запоминает верифицированные строки."""
        body = await self.request_body('setBreakpoints', {
            'source': {'path': path},
            'breakpoints': [{'line': int(line)} for line in sorted(set(lines))],
        })
        accepted = body.get('breakpoints')
        verified: list[int] = []
        if isinstance(accepted, list):
            for entry in accepted:
                if not isinstance(entry, dict) or not entry.get('verified'):
                    continue
                line = entry.get('line')
                if isinstance(line, int):
                    verified.append(line)
        self.verified[path] = verified
        unverified = sorted(set(lines) - set(verified))
        if unverified:
            dbg_log.info(
                'Брейкпоинты вне reachability (сработают позже), файл %s: %s',
                path, unverified,
            )
