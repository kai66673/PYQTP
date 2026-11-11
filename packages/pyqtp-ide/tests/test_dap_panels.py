"""Этап 11 (срез D2) — панели отладчика: стек вызовов, переменные, потоки.

Юниты без GUI (дефолтный прогон) — чистый ``controls/dap_control/debug_tree.py``:
разбор ответов ``stackTrace``/``threads``/``scopes``/``variables`` (толерантный:
кадры без id/имени, области с ref 0, пустые имена), payload вскрытия кадра
(``frame_reveal_payload``) и подпись строки кадра (``frame_display``).

Сессия без GUI (дефолтный прогон) — ``DapSession`` против фейкового stdio
DAP-адаптера из test_dap_debugger.py (расширенного мультикадром, потоками,
областями и переменными): мультикадр stackTrace, threads, scopes по frameId
(у кадра без файла — только Globals), variables с вложенным items, режим
no_scopes (scopes success=False → DapError с текстом).

Offscreen-GUI без Monaco/WebEngine (дефолтный прогон) — панели
``DebugStackView``/``DebugVariablesView`` и тулбар «Отладка» на ``DapControl``:
заполнение панелей на останове, выбор кадра (scopes/переменные), двойной клик
по кадру (переход + «кадр без файла»), комбик потоков (stackTrace своего
потока без show-перехода), ленивая выгрузка variables по раскрытию, тулбар
(объект/экшены/гейтинг), очистка панелей при останове/новом запуске, console
API ``debug_stack_show``/``debug_threads_show``/``debug_scopes_show``/
``debug_frame_select``.

Live (маркер ``live``) — настоящая сессия debugpy: реальные кадры/потоки/
области/переменные в панелях (программа и брейкпоинт — как в live-тесте D1).

Харнес (окно, фейковые документы с фейковым редактором, фейковый адаптер,
wait_until) переиспользуется импортом из test_dap_debugger.py.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from PySide6.QtWidgets import QToolBar

from pyqtp.controls.dap_control import debug_tree as dt
from pyqtp.controls.dap_control import launch_config as lc
from pyqtp.controls.dap_control.dap_control import (
    STATE_IDLE,
    STATE_STOPPED,
)
from pyqtp.controls.dap_control.dap_process import adapter_command
from pyqtp.controls.dap_control.dap_session import DapError, DapSession
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

# харнес D1 — фикстуры и хелперы того же каталога тестов (pytest-соседство);
# фикстуры импортированы «как есть» — pytest подставляет их по именам аргументов
from test_dap_debugger import (
    clean_dap_env,
    fake_adapter,
    fake_docs,
    fake_edit,
    journal_entries,
    org_app,
    prj_root,
    wait_until,
    wnd,
)

# id кадров/потоков/ссылок фейкового адаптера (D2-часть харнеса D1):
# поток 1 — кадры 100/101 (+102 без источника), поток 2 — 200/201;
# scopes: Locals/Globals потока 1 -> 10/11, потока 2 -> 30/31; items -> 20
_MAIN_FRAMES = [100, 101, 102]
_WORKER_FRAMES = [200, 201]
_LOCALS_T1 = 10
_GLOBALS_T1 = 11
_LOCALS_T2 = 30
_GLOBALS_T2 = 31
_ITEMS_REF = 20
_BUILTIN_FRAME = 102

###########################
# Юниты чистого debug_tree #


def test_frames_from_response_shapes() -> None:
    """Кадр: без id — выбрасываем, без source — путь '', колонка по умолчанию 1."""
    body = {'stackFrames': [
        {'id': 1, 'name': 'f', 'line': 5,
         'source': {'path': '/a.py', 'name': 'a.py'}},
        {'id': 2, 'line': 7, 'column': 9},
        {'name': 'без id', 'line': 1},
        'мусор',
    ], 'totalFrames': 3}
    rows = dt.frames_from_response(body)
    assert [(r.frame_id, r.name, r.path, r.line, r.column) for r in rows] == [
        (1, 'f', '/a.py', 5, 1),
        (2, '???', '', 7, 9),
    ]


def test_threads_from_response_shapes() -> None:
    """Поток без id — пропускаем, имя при отсутствии — фолбэк."""
    body = {'threads': [
        {'id': 1, 'name': 'MainThread'},
        {'name': 'без id'},
        {'id': 'abc'},
        {'id': 2},
    ]}
    rows = dt.threads_from_response(body)
    assert [(r.thread_id, r.name) for r in rows] == [
        (1, 'MainThread'), (2, '???'),
    ]


def test_scopes_from_response_skips_zero_ref() -> None:
    """Область с ref 0/битым ref выпадает; expensive и счётчики подхвачены."""
    body = {'scopes': [
        {'name': 'Locals', 'variablesReference': 10,
         'expensive': True, 'namedVariables': 3, 'indexedVariables': 2},
        {'name': 'Пусто', 'variablesReference': 0},
        {'name': 'Битый ref'},
    ]}
    rows = dt.scopes_from_response(body)
    assert [(r.scope_id, r.name, r.expensive, r.named_variables)
            for r in rows] == [(10, 'Locals', True, 3)]


def test_variables_from_response_fallbacks() -> None:
    """Пустое имя выпадает; evaluateName фолбэкится именем; ref без int — 0."""
    body = {'variables': [
        {'name': 'x', 'value': '1', 'type': 'int', 'variablesReference': 0},
        {'name': 'items', 'variablesReference': 20, 'namedVariables': 3},
        {'value': 'без имени'},
        {'name': 'weird', 'variablesReference': 'abc'},
    ]}
    rows = dt.variables_from_response(body)
    assert [(r.name, r.value, r.children_ref, r.evaluate_name)
            for r in rows] == [
        ('x', '1', 0, 'x'),
        ('items', '', 20, 'items'),
        ('weird', '', 0, 'weird'),
    ]


def test_frame_reveal_payload_paths() -> None:
    """Payload — файл на диске; None для кадра без файла/строки/несуществующего."""
    with tempfile.TemporaryDirectory() as tmp:
        real = str(Path(tmp) / 'mod.py')
        Path(real).write_text('x = 1\n', encoding='utf-8')
        frame = dt.FrameRow(frame_id=1, name='f', path=real, line=4, column=7)
        payload = dt.frame_reveal_payload(frame)
        assert payload is not None
        assert payload['uri'] == Path(real).as_uri()
        assert payload['range'] == {'startLineNumber': 4, 'startColumn': 7}
    assert dt.frame_reveal_payload(
        dt.FrameRow(1, 'b', '', 0, 1),
    ) is None
    assert dt.frame_reveal_payload(
        dt.FrameRow(1, 'f', real, 0, 1),
    ) is None
    assert dt.frame_reveal_payload(
        dt.FrameRow(1, 'f', '/нет-такого-файла-12345.py', 3, 1),
    ) is None


def test_frame_display_relative_and_sourceless() -> None:
    """Подпись: имя — относительный путь:строка; без источника — пометка."""
    root = Path('/proj/pkg')
    inside = dt.FrameRow(1, 'f', '/proj/pkg/mod.py', 4, 1)
    outside = dt.FrameRow(2, 'g', '/other/mod.py', 8, 1)
    assert dt.frame_display(inside, root) == 'f — mod.py:4'
    assert dt.frame_display(outside, root) == 'g — /other/mod.py:8'
    assert dt.frame_display(outside, None) == 'g — /other/mod.py:8'
    builtin = dt.FrameRow(3, 'builtins.range', '', 0, 1)
    assert dt.frame_display(builtin, root) == 'builtins.range [без источника]'


############################################
# Сессия против расширенного фейкового адаптера #


def test_session_frames_threads_scopes_variables(fake_adapter: Path) -> None:
    """Мультикадр/потоки/области/переменные — полный D2-маршрут без GUI."""
    program = '/proj/target.py'

    async def _flow() -> dict[str, Any]:
        session = DapSession(adapter_command(), lambda *_: None)
        await session.start()
        try:
            error = await session.handshake(
                lc.launch_arguments(lc.LaunchConfig(name='тест', program=program)),
            )
            assert error == ''
            stack = dt.frames_from_response(await session.request_body(
                'stackTrace', {'threadId': 1, 'startFrame': 0, 'levels': 32},
            ))
            threads = dt.threads_from_response(
                await session.request_body('threads', {}),
            )
            scopes_main = dt.scopes_from_response(
                await session.request_body('scopes', {'frameId': 100}),
            )
            scopes_builtin = dt.scopes_from_response(
                await session.request_body('scopes', {'frameId': 102}),
            )
            locals_rows = dt.variables_from_response(
                await session.request_body('variables', {'variablesReference': 10}),
            )
            nested = dt.variables_from_response(
                await session.request_body('variables', {'variablesReference': 20}),
            )
            return {'stack': stack, 'threads': threads,
                    'scopes_main': scopes_main, 'scopes_builtin': scopes_builtin,
                    'locals': locals_rows, 'nested': nested}
        finally:
            await session.close(terminate=True)

    got = asyncio.run(_flow())
    stack: list[dt.FrameRow] = got['stack']
    assert [f.frame_id for f in stack] == [100, 101, 102]
    assert stack[0].path == program and stack[0].line == 3
    assert stack[1].line == 1
    assert stack[2].path == ''  # кадр без источника
    assert [(t.thread_id, t.name) for t in got['threads']] == [
        (1, 'MainThread'), (2, 'Worker'),
    ]
    assert [(s.scope_id, s.name) for s in got['scopes_main']] == [
        (_LOCALS_T1, 'Locals'), (_GLOBALS_T1, 'Globals'),
    ]
    assert [(s.scope_id, s.name) for s in got['scopes_builtin']] == [
        (_GLOBALS_T1, 'Globals'),
    ]
    assert [(v.name, v.children_ref) for v in got['locals']] == [
        ('x', 0), ('counter', 0), ('items', 20),
    ]
    assert [v.name for v in got['nested']] == ['0', '1', '2']
    commands = [e['command'] for e in journal_entries(fake_adapter)]
    assert all(name in commands for name in
               ('threads', 'scopes', 'variables'))


def test_session_scopes_failure_message(
    fake_adapter: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Режим no_scopes: scopes success=False → DapError с текстом адаптера."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'no_scopes')

    async def _flow() -> str:
        session = DapSession(adapter_command(), lambda *_: None)
        await session.start()
        try:
            error = await session.handshake({'program': '/proj/target.py'})
            assert error == ''
            with pytest.raises(DapError) as raised:
                await session.request_body('scopes', {'frameId': 100})
            return str(raised.value)
        finally:
            await session.close(terminate=True)

    assert asyncio.run(_flow()) == 'scopes недоступны'


###########################
# Offscreen-GUI панели   #


def _loopy_project(tmp_path: Path) -> Path:
    """Как loop_root из D1: цикл с паузой — брейкпоинт гарантированно сработает."""
    root = tmp_path / 'loopy'
    root.mkdir()
    target = root / 'loopy.py'
    target.write_text(
        'import time\n\nfor i in range(30):\n    time.sleep(0.1)\n',
        encoding='utf-8',
    )
    return target


def _start_to_stopped(
    wnd: IdeMainWindow, root: Path, qtbot: Any,
) -> None:
    """Открыть проект+документ, F5, дождаться останова на фейковом адаптере."""
    doc_a = root / 'a.py'
    wnd.project_ctrl.open_project(root)
    wnd.doc_ctrl.open_document(doc_a)
    assert wnd.dap_ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: wnd.dap_ctrl.state == STATE_STOPPED,
               message='фейковый адаптер не поставил сессию на останов')


def _expand_scope(
    wnd: IdeMainWindow, ref: int, variables_view: Any, qtbot: Any,
) -> None:
    """Раскрытие ячейки переменных (тот же маршрут, что сигнал вьюхи) + ожидание."""
    wnd.dap_ctrl._on_children_requested(ref)  # noqa: SLF001
    wait_until(qtbot, lambda: variables_view.is_loaded(ref),
               message='переменные области не выгружены в панель')


def test_stop_populates_stack_threads_and_scopes(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Останов: кадры/потоки/области в панелях и console API; области ленивы."""
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    stack_view = ctrl.stack_view
    variables_view = ctrl.variables_view
    assert stack_view is not None
    assert variables_view is not None

    assert stack_view.frame_ids() == _MAIN_FRAMES
    assert stack_view.current_frame_id() == 100
    assert any('a.py:3' in text for text in stack_view.frame_texts())
    assert '[без источника]' in stack_view.frame_texts()[2]
    assert stack_view.thread_ids() == [1, 2]
    assert stack_view.current_thread_id() == 1

    assert variables_view.scope_names() == ['Locals', 'Globals']
    assert not variables_view.is_loaded(_LOCALS_T1)  # дети не выгружены (ленивость)
    assert not variables_view.is_empty()

    assert [f['frame_id'] for f in ctrl.stack_snapshot()] == _MAIN_FRAMES
    assert [t['thread_id'] for t in ctrl.threads_snapshot()] == [1, 2]
    wait_until(qtbot, lambda: bool(ctrl.scopes_snapshot()),
               message='scopes не пришли в зеркало контрола')
    assert [s['scope_id'] for s in ctrl.scopes_snapshot()] == [
        _LOCALS_T1, _GLOBALS_T1,
    ]


def test_frame_select_loads_scopes_and_children(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Выбор кадра: scopes кадра; раскрытие области → variables в панель."""
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    variables_view = ctrl.variables_view
    assert variables_view is not None

    assert ctrl.select_frame(101) == ''
    assert ctrl.select_frame(999) == 'Кадр не найден в стеке'

    wait_until(
        qtbot,
        lambda: [s['scope_id'] for s in ctrl.scopes_snapshot()] == [
            _LOCALS_T1, _GLOBALS_T1,
        ],
        message='scopes выбранного кадра не загружены',
    )

    _expand_scope(wnd, _LOCALS_T1, variables_view, qtbot)
    assert variables_view.child_names(_LOCALS_T1) == ['x', 'counter', 'items']
    assert variables_view.child_value(_LOCALS_T1, 'x') == '42'
    assert variables_view.is_loaded(_LOCALS_T1)
    assert not variables_view.is_loaded(_ITEMS_REF)  # вложенный items — снова ленив

    # повторное раскрытие загруженной ячейки — нового запроса нет
    before = [e for e in journal_entries(fake_adapter)
              if e['command'] == 'variables']
    ctrl._on_children_requested(_LOCALS_T1)  # noqa: SLF001
    qtbot.wait(200)
    after = [e for e in journal_entries(fake_adapter)
             if e['command'] == 'variables']
    assert len(after) == len(before)


def test_frame_double_click_reveal_and_sourceless(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Двойной клик: кадр с файлом — переход каретки; кадр без файла — строка в консоли."""
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    edit = fake_edit(wnd, str(doc_a))
    stack_view = ctrl.stack_view
    assert stack_view is not None

    # D1-показ уже поставил каретку на строку останова (frame0 = 3)
    assert edit.cursor_calls[-1] == (3, 1, 'center')

    stack_view.frame_activated.emit(101)  # кадр main на строке 1
    wait_until(qtbot, lambda: edit.cursor_calls[-1] == (1, 1, 'center'),
               message='двойной клик по кадру не передвинул каретку')
    assert stack_view.current_frame_id() == 101

    stack_view.frame_activated.emit(_BUILTIN_FRAME)  # builtins без источника
    assert stack_view.current_frame_id() == _BUILTIN_FRAME
    console = ctrl.debug_console_view
    assert console is not None
    assert 'Кадр без файла — переход недоступен' in console.console_text()
    # кадр без файла не двигает каретку
    assert edit.cursor_calls[-1] == (1, 1, 'center')


def test_thread_combo_switches_stack_without_reveal(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Комбик потоков: stackTrace своего потока, show-переход не трогаем."""
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    stack_view = ctrl.stack_view
    assert stack_view is not None
    assert stack_view.thread_ids() == [1, 2]

    stack_view.thread_selected.emit(2)
    wait_until(qtbot, lambda: stack_view.frame_ids() == _WORKER_FRAMES,
               message='стек потока 2 не сменился')
    assert stack_view.current_frame_id() == 200
    assert stack_view.current_thread_id() == 2
    assert [f['path'] for f in ctrl.stack_snapshot()] == [str(prj_root / 'a.py')] * 2

    # переменные — уже области потока 2
    wait_until(
        qtbot,
        lambda: [s['scope_id'] for s in ctrl.scopes_snapshot()] == [
            _LOCALS_T2, _GLOBALS_T2,
        ],
        message='scopes кадра потока 2 не загружены',
    )
    variables_view = ctrl.variables_view
    assert variables_view is not None
    _expand_scope(wnd, _LOCALS_T2, variables_view, qtbot)
    assert variables_view.child_names(_LOCALS_T2) == ['ticket']

    # show-переход и подсветка останова не тронуты (поток 1, строка 3)
    edit = fake_edit(wnd, str(prj_root / 'a.py'))
    assert edit.cursor_calls[-1] == (3, 1, 'center')
    assert edit.exec_calls[-1] == {'line': 3}

    journal = [e for e in journal_entries(fake_adapter)
               if e['command'] == 'stackTrace']
    assert journal[-1]['threadId'] == 2


def test_debug_toolbar_object_actions_and_gating(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Тулбар «Отладка»: объект, те же экшены, что меню, гейтинг по стат-машине."""
    ctrl = wnd.dap_ctrl
    toolbar = ctrl.debug_toolbar
    assert isinstance(toolbar, QToolBar)
    assert toolbar.objectName() == 'debug_toolbar'
    assert toolbar.toolButtonStyle().name == 'ToolButtonTextOnly'
    assert not toolbar.isMovable()
    actions = toolbar.actions()
    for action in (
        ctrl.action_debug_start, ctrl.action_debug_step_over,
        ctrl.action_debug_step_into, ctrl.action_debug_step_out,
        ctrl.action_debug_stop,
    ):
        assert action in actions

    # без проекта — запуск запрещён
    assert not ctrl.action_debug_start.isEnabled()
    wnd.project_ctrl.open_project(prj_root)
    assert ctrl.action_debug_start.isEnabled()
    assert not ctrl.action_debug_stop.isEnabled()

    doc_a = prj_root / 'a.py'
    wnd.doc_ctrl.open_document(doc_a)
    assert ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='сессия не встала на останов')
    assert ctrl.action_debug_stop.isEnabled()
    assert all(act.isEnabled() for act in (
        ctrl.action_debug_step_over, ctrl.action_debug_step_into,
        ctrl.action_debug_step_out,
    ))

    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE
    assert not ctrl.action_debug_stop.isEnabled()
    assert not ctrl.action_debug_step_over.isEnabled()


def test_panels_cleared_on_stop_and_restart(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Остановка и новый запуск снимают показ панелей; повторный останов — снова кадр."""
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    stack_view = ctrl.stack_view
    variables_view = ctrl.variables_view
    assert stack_view is not None
    assert variables_view is not None
    assert stack_view.frame_ids()

    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE
    assert stack_view.frame_ids() == []
    assert stack_view.thread_ids() == []
    assert stack_view.current_frame_id() is None
    assert variables_view.is_empty()
    assert variables_view.scope_names() == []
    assert ctrl.stack_snapshot() == []
    assert ctrl.scopes_snapshot() == []

    # повторный запуск: панели чистые в момент старта и снова наполнены на останове
    assert ctrl.start_debugging() == ''
    assert ctrl.stack_snapshot() == []
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='повторная сессия не встала на останов')
    assert stack_view.frame_ids() == _MAIN_FRAMES


def test_no_scopes_keeps_stack_and_state(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path,
    monkeypatch: pytest.MonkeyPatch, qtbot: Any
) -> None:
    """scopes success=False: кадры/потоки на месте, переменные пусты, STOPPED."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'no_scopes')
    _start_to_stopped(wnd, prj_root, qtbot)
    ctrl = wnd.dap_ctrl
    stack_view = ctrl.stack_view
    variables_view = ctrl.variables_view
    assert stack_view is not None
    assert variables_view is not None
    assert ctrl.state == STATE_STOPPED
    assert stack_view.frame_ids() == _MAIN_FRAMES
    assert stack_view.thread_ids() == [1, 2]
    assert variables_view.scope_names() == []
    assert variables_view.is_empty()


def test_console_api_and_view_registration(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """console API: выбор кадра вне останова — текст; ключи вьюх на месте."""
    ctrl = wnd.dap_ctrl
    assert ctrl.stack_view is not None
    assert ctrl.variables_view is not None
    assert ctrl.stack_view.key == f'{ctrl.key}_stack_view'
    assert ctrl.variables_view.key == f'{ctrl.key}_variables_view'
    assert ctrl.select_frame(1) == 'Выбор кадра доступен в останове'

    _start_to_stopped(wnd, prj_root, qtbot)
    assert ctrl.select_frame(_BUILTIN_FRAME) == ''
    assert ctrl.stack_view.current_frame_id() == _BUILTIN_FRAME
    wait_until(
        qtbot,
        lambda: [s['name'] for s in ctrl.scopes_snapshot()] == ['Globals'],
        message='scopes кадра без файла не загружены',
    )
    assert [s['scope_id'] for s in ctrl.scopes_snapshot()] == [_GLOBALS_T1]


@pytest.mark.live
def test_live_debugpy_panels_stack_and_variables(
    wnd: IdeMainWindow, tmp_path: Path, clean_dap_env: None, qtbot: Any
) -> None:
    """Живой debugpy: реальные кадры, MainThread, области и переменные в панелях.

    Программа и брейкпоинт — как в live-тесте D1 (останов внутри цикла:
    Locals гарантированно содержит ``i``).
    """
    ctrl = wnd.dap_ctrl
    target = _loopy_project(tmp_path)
    wnd.project_ctrl.open_project(target.parent)
    wnd.doc_ctrl.open_document(target)
    assert ctrl.toggle_breakpoint_at(str(target), 4) is True

    assert ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED, timeout=40.0,
               message='debugpy не остановился на брейкпоинте')

    stack_view = ctrl.stack_view
    variables_view = ctrl.variables_view
    assert stack_view is not None
    assert variables_view is not None
    assert stack_view.frame_ids()
    assert any('loopy.py:4' in text for text in stack_view.frame_texts())
    assert 1 in stack_view.thread_ids()

    wait_until(qtbot, lambda: bool(ctrl.scopes_snapshot()), timeout=20.0,
               message='живые scopes не пришли')
    names = [s['name'] for s in ctrl.scopes_snapshot()]
    assert 'Locals' in names
    locals_ref = next(
        s['scope_id'] for s in ctrl.scopes_snapshot() if s['name'] == 'Locals'
    )
    ctrl._on_children_requested(locals_ref)  # noqa: SLF001
    wait_until(qtbot, lambda: variables_view.is_loaded(locals_ref), timeout=20.0,
               message='живые переменные не выгружены')
    assert 'i' in variables_view.child_names(locals_ref)

    ctrl.stop_debugging()
    assert stack_view.frame_ids() == []
    assert variables_view.is_empty()
