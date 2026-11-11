"""Этап 11 (срез D1) — отладчик DAP: сессия, брейкпоинты, консоль.

Юниты без GUI (дефолтный прогон) — чистые модули ``controls/dap_control/``:

* ``dap_framing`` — Content-Length-фреймы и конструкторы сообщений DAP;
* ``launch_config`` — толерантный разбор ``.pyqtp/launch.json``, макрос
  ``${workspaceFolder}``, конфигурация по умолчанию (активный документ),
  форма аргументов ``launch`` для debugpy;
* ``breakpoints`` — ``.pyqtp/breakpoints.json`` (тот же контракт, что у
  закладок 9.7: толерантный разбор, каноническая запись, срезка мёртвых
  файлов, соседство с ``session.json``/``settings.json``);
* ``dap_session`` против фейкового stdio DAP-адаптера (``python <скрипт>``
  через env-шов ``PYQTP_DAP_ADAPTER_COMMAND``): порядок ритуала handshake
  (журнал запросов), хук брейкпоинтов, тексты ошибок, смерть адаптера.

Offscreen-GUI без Monaco/WebEngine (дефолтный прогон) — ``DapControl``:
меню «Отладка» и экшены, брейкпоинты (файл проекта + раздача редакторам +
gutter-клик + синхронизация в живую сессию), стат-машина F5/шаги/стоп на
фейковом адаптере, консоль отладки.

Live (маркер ``live``) — настоящий ``python -m debugpy.adapter`` на крошечной
программе (launch.json со ``stopOnEntry``: останова 1→2→3 + вывод print).

WebEngine-пробы (маркер ``webengine``) — метки брейкпоинтов и строка останова
в DOM Monaco (``.pyqtp-breakpoint-glyph``/``.pyqtp-exec-gutter``) и маршрут
клика по дорожке (хук ``qtmonaco.__pyqtp_gutter_click``).

Настоящие документы подменены лёгкой вьюхой с фейковым редактором (свои
классы в этом файле, харнес как в test_bookmarks.py).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

import qtmonaco
from PySide6.QtCore import QEventLoop, QObject, Qt, QSettings, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.dap_control import breakpoints as bp
from pyqtp.controls.dap_control import dap_framing as framing
from pyqtp.controls.dap_control import dap_session as session_mod
from pyqtp.controls.dap_control import launch_config as lc
from pyqtp.controls.dap_control.breakpoints import Breakpoint
from pyqtp.controls.dap_control.dap_control import (
    STATE_IDLE,
    STATE_RUNNING,
    STATE_STOPPED,
)
from pyqtp.controls.dap_control.dap_process import ENV_ADAPTER_COMMAND, adapter_command
from pyqtp.controls.dap_control.dap_session import DapError, DapSession
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.main_wnd.ide_main_window import IdeMainWindow
from pyqtp.web.qtmonaco_editor import QtMonacoEditorWidget

###########################
# Юниты чистого фрейминга #


def test_encode_frame_matches_content_length() -> None:
    """Кадр = ``Content-Length`` + JSON-тело; длина совпадает с телом."""
    msg = framing.build_request(3, 'initialize', {'adapterID': 'pyqtp'})
    raw = framing.encode_message(msg)
    header, sep, body = raw.partition(framing.HEADER_TERMINATOR)
    assert sep == framing.HEADER_TERMINATOR
    assert header.startswith(b'Content-Length: ')
    assert framing.parse_content_length(header + sep) == len(body)
    assert json.loads(body) == msg


def test_parse_content_length_variants() -> None:
    """Регистр имени не важен; без длины/не число — ``None``."""
    assert framing.parse_content_length('Content-Length: 42\r\n\r\n') == 42
    assert framing.parse_content_length(b'content-length: 7\r\n\r\n') == 7
    assert framing.parse_content_length('Content-Type: application/vscode-json\r\n\r\n') is None
    assert framing.parse_content_length('Content-Length: abc\r\n\r\n') is None


def test_build_response_and_event_shapes() -> None:
    """Формы ответов/событий — по спецификации DAP (пустых полей нет)."""
    resp = framing.build_response(
        5, 2, 'launch', {'debuggerStarted': True}, success=False, message='oops',
    )
    assert resp == {
        'seq': 5,
        'type': 'response',
        'request_seq': 2,
        'command': 'launch',
        'success': False,
        'body': {'debuggerStarted': True},
        'message': 'oops',
    }
    ev = framing.build_event(6, 'stopped', {'reason': 'breakpoint', 'threadId': 1})
    assert ev == {
        'seq': 6, 'type': 'event', 'event': 'stopped',
        'body': {'reason': 'breakpoint', 'threadId': 1},
    }
    assert 'body' not in framing.build_event(7, 'initialized')
    assert 'message' not in framing.build_response(8, 1, 'x', {})
    assert 'body' not in framing.build_response(9, 1, 'x', {})
    assert framing.build_request(1, 'next', {})['arguments'] == {}


def test_extract_error_message_parts() -> None:
    """``message`` + ``body.output`` склеиваются через «: », пустые — пропускаются."""
    assert session_mod.extract_error_message({'success': False}) == ''
    assert session_mod.extract_error_message({'message': 'Адаптер упал'}) == 'Адаптер упал'
    assert session_mod.extract_error_message(
        {'message': 'Запуск не удался', 'body': {'output': 'файл не найден\n'}},
    ) == 'Запуск не удался: файл не найден'
    assert session_mod.extract_error_message({'body': {'output': 'только вывод'}}) == 'только вывод'


###########################
# Юниты launch.json      #


def test_parse_launch_json_tolerant() -> None:
    """Битый JSON/не-словарь — ``None``; словарь без конфигураций — пустой список."""
    assert lc.parse_launch_json('{битый') is None
    assert lc.parse_launch_json('[1, 2]') is None
    assert lc.parse_launch_json('{"version": "0.2.0"}') == []


def test_config_from_dict_garbage_filtered() -> None:
    """attach/чужой type/без программы — отбрасываем; args/env чистим."""
    assert lc.config_from_dict('строка') is None
    assert lc.config_from_dict({'program': 'a.py', 'request': 'attach'}) is None
    assert lc.config_from_dict({'program': 'a.py', 'type': 'node'}) is None
    assert lc.config_from_dict({'name': 'нет программы'}) is None
    assert lc.config_from_dict({'program': '   '}) is None
    cfg = lc.config_from_dict({
        'program': '  main.py ',
        'args': ['--x', 3, {'bad': 1}, None],
        'env': {'A': '1', 'B': 2, 'C': None, '': 'x'},
        'stopOnEntry': True,
        'justMyCode': False,
    })
    assert cfg is not None
    assert cfg.name == 'Конфигурация'
    assert cfg.program == 'main.py'
    assert cfg.args == ('--x', '3')
    assert cfg.env == (('A', '1'), ('B', '2'))
    assert cfg.stop_on_entry is True
    assert cfg.just_my_code is False


def test_substitute_and_resolve_paths(tmp_path: Path) -> None:
    """``${workspaceFolder}``` раскрывается, относительные пути — от корня."""
    assert lc.substitute('${workspaceFolder}/x.py', tmp_path) == f'{tmp_path}/x.py'
    cfg = lc.LaunchConfig(
        name='тест',
        program='pkg/../main.py',
        args=('--macro=${workspaceFolder}',),
        cwd='',
        env=(('P', '${workspaceFolder}/lib'),),
    )
    resolved = lc.resolve_config(cfg, tmp_path)
    assert resolved.program == str(tmp_path / 'main.py')
    assert resolved.cwd == str(tmp_path)
    assert resolved.args == (f'--macro={tmp_path}',)
    assert resolved.env == (('P', f'{tmp_path}/lib'),)

    with_cwd = lc.resolve_config(
        lc.LaunchConfig(name='n', program='a.py', cwd='sub'), tmp_path,
    )
    assert with_cwd.cwd == str(tmp_path / 'sub')


def test_default_config_uses_active_document(tmp_path: Path) -> None:
    """Схема по умолчанию: программа — активный документ, cwd — корень."""
    assert lc.default_config(tmp_path, None) is None
    cfg = lc.default_config(tmp_path, tmp_path / 'main.py')
    assert cfg is not None
    assert cfg.name == lc.DEFAULT_CONFIG_NAME
    assert cfg.program == str(tmp_path / 'main.py')
    assert cfg.cwd == str(tmp_path)


def test_launch_arguments_debugpy_form() -> None:
    """Аргументы ``launch``: internalConsole, env-пары, только nonzero-опции."""
    cfg = lc.LaunchConfig(
        name='n', program='/x/main.py', args=('--flag',), cwd='/x',
        env=(('A', '1'),), stop_on_entry=True, just_my_code=False,
    )
    args = lc.launch_arguments(cfg)
    assert args['type'] == 'python'
    assert args['request'] == 'launch'
    assert args['console'] == 'internalConsole'
    assert args['program'] == '/x/main.py'
    assert args['cwd'] == '/x'
    assert args['args'] == ['--flag']
    assert args['env'] == [{'key': 'A', 'value': '1'}]
    assert args['stopOnEntry'] is True
    assert args['justMyCode'] is False

    minimal = lc.launch_arguments(lc.LaunchConfig(name='n', program='/x/main.py'))
    for optional in ('cwd', 'args', 'env', 'stopOnEntry', 'justMyCode'):
        assert optional not in minimal


def test_read_launch_file_missing_and_broken(tmp_path: Path) -> None:
    """Нет файла/битый — ``None``; валидный — список конфигураций."""
    assert lc.read_launch_file(tmp_path) is None
    project_dir = tmp_path / '.pyqtp'
    project_dir.mkdir()
    (project_dir / 'launch.json').write_text('{битый', encoding='utf-8')
    assert lc.read_launch_file(tmp_path) is None
    payload = {'configurations': [{'name': 'Запуск', 'program': 'main.py'}]}
    (project_dir / 'launch.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8',
    )
    configs = lc.read_launch_file(tmp_path)
    assert configs is not None
    assert configs[0].name == 'Запуск'
    assert configs[0].program == 'main.py'


###########################
# Юниты брейкпоинтов     #


def test_parse_breakpoints_tolerant() -> None:
    """Мусор в файле — пропускаем записи, а не падаем."""
    assert bp.parse_breakpoints('нечего разобрать') == []
    assert bp.parse_breakpoints('[1, 2]') == []
    assert bp.parse_breakpoints('{"breakpoints": "не список"}') == []
    text = json.dumps({
        'breakpoints': [
            {'path': '/a.py', 'line': 2},
            {'path': '', 'line': 2},
            {'path': '/a.py', 'line': 0},
            {'path': '/a.py', 'line': '2'},
            {'path': '/a.py', 'line': True},
            {'path': '/b.py', 'line': 7, 'extra': 1},
            'не словарь',
        ]
    })
    assert bp.parse_breakpoints(text) == [
        Breakpoint(path='/a.py', line=2),
        Breakpoint(path='/b.py', line=7),
    ]


def test_serialize_breakpoints_sorts_and_dedups() -> None:
    """Запись канонична: сортировка по (path, line) + дедуп."""
    items = [
        Breakpoint(path='/b.py', line=3),
        Breakpoint(path='/a.py', line=9),
        Breakpoint(path='/a.py', line=9),
        Breakpoint(path='/a.py', line=1),
    ]
    payload = json.loads(bp.serialize_breakpoints(items))
    assert payload == {
        'breakpoints': [
            {'path': '/a.py', 'line': 1},
            {'path': '/a.py', 'line': 9},
            {'path': '/b.py', 'line': 3},
        ]
    }


def test_write_breakpoints_roundtrip_no_temp_leftovers(tmp_path: Path) -> None:
    """Запись атомарна: временных файлов после неё не остаётся."""
    items = [Breakpoint(path=str(tmp_path / 'a.py'), line=4)]
    bp.write_breakpoints(tmp_path, items)
    assert bp.read_breakpoints(tmp_path) == items
    project_dir = tmp_path / '.pyqtp'
    assert [p.name for p in project_dir.iterdir()] == ['breakpoints.json']
    assert bp.breakpoints_file_path(tmp_path) == project_dir / 'breakpoints.json'


def test_write_empty_breakpoints_keeps_neighbours(tmp_path: Path) -> None:
    """Пустой набор снимает файл (и пустой ``.pyqtp/``), но не соседей."""
    project_dir = tmp_path / '.pyqtp'
    project_dir.mkdir()
    session = project_dir / 'session.json'
    session.write_text('{"docs": [], "active": null}', encoding='utf-8')

    bp.write_breakpoints(tmp_path, [Breakpoint(path='/a.py', line=1)])
    assert bp.breakpoints_file_path(tmp_path).is_file()

    bp.write_breakpoints(tmp_path, [])
    assert not bp.breakpoints_file_path(tmp_path).exists()
    assert session.is_file()
    assert project_dir.is_dir()

    session.unlink()
    bp.write_breakpoints(tmp_path, [])
    assert not project_dir.exists()


def test_filter_breakpoints_prunes_dead_files(tmp_path: Path) -> None:
    """Живой файл остаётся, удалённый — уходит в «пропущенные»."""
    alive = tmp_path / 'a.py'
    alive.write_text('x = 1\n', encoding='utf-8')
    items = [
        Breakpoint(path=str(alive), line=1),
        Breakpoint(path=str(tmp_path / 'gone.py'), line=2),
    ]
    kept, skipped = bp.filter_breakpoints(items)
    assert kept == [Breakpoint(path=str(alive), line=1)]
    assert skipped == [Breakpoint(path=str(tmp_path / 'gone.py'), line=2)]


def test_toggle_breakpoint_adds_then_removes() -> None:
    """Переключение: нет — ставим, есть — снимаем; вход не меняется."""
    base = [Breakpoint(path='/a.py', line=2)]
    added = bp.toggle_breakpoint(base, '/a.py', 5)
    assert added == [Breakpoint(path='/a.py', line=2), Breakpoint(path='/a.py', line=5)]
    assert base == [Breakpoint(path='/a.py', line=2)]
    assert bp.toggle_breakpoint(added, '/a.py', 5) == base


def test_breakpoints_for_sorted_lines() -> None:
    """Строки одного файла — отсортированы; чужой файл — пусто."""
    items = [
        Breakpoint(path='/a.py', line=9),
        Breakpoint(path='/a.py', line=2),
        Breakpoint(path='/b.py', line=4),
    ]
    assert bp.breakpoints_for(items, '/a.py') == [2, 9]
    assert bp.breakpoints_for(items, '/c.py') == []


#################################
# Фейковый stdio DAP-адаптер    #

# Скрипт-адаптер (только stdlib): отвечает initialize → initialized → ждёт
# launch (сам ответ launch — только после configurationDone, как debugpy) →
# setBreakpoints (верифицирует все строки) → configurationDone → stopped →
# stackTrace (2 кадра на program из launch: frame0 строка 1 при stopOnEntry
# иначе 3, +1 за каждый шаг; кадр1 — строка 1; у кадров потоков свои id) →
# threads (2 потока) → scopes (Locals/Globals для кадров с файлом, один
# Globals у кадра без источника) → variables (вложенный items) →
# next/step*/continue → terminated/exited → выход 0.
# Режимы (env PYQTP_FAKE_DAP_MODE): no_initialized (не шлёт initialized),
# launch_fail (ответ launch success=False), crash (смерть с кодом 7 после
# первого stopped), dead (умирает сразу, не прочитав ни одного запроса),
# no_frames (stackTrace пустой), no_scopes (scopes с success=False).
# Каждый запрос — строка в JSONL-журнал (env PYQTP_FAKE_DAP_JOURNAL) —
# порядок ритуала проверяется по нему.
FAKE_ADAPTER_SOURCE = r"""
# Фейковый stdio DAP-адаптер (тестовый харнес, только stdlib).
import json
import os
import sys
import time

JOURNAL = os.environ.get('PYQTP_FAKE_DAP_JOURNAL', '')
MODE = os.environ.get('PYQTP_FAKE_DAP_MODE', 'normal')

_seq = 1


def send(msg):
    global _seq
    out = dict(msg)
    out['seq'] = _seq
    _seq += 1
    data = json.dumps(out, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(b'Content-Length: %d\r\n\r\n' % len(data) + data)
    sys.stdout.buffer.flush()


def journal(command, entry=None):
    if not JOURNAL:
        return
    record = {'command': command}
    if entry:
        record.update(entry)
    with open(JOURNAL, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def read_message():
    header = b''
    while not header.endswith(b'\r\n\r\n'):
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk
    length = None
    for line in header.decode('ascii', 'replace').splitlines():
        name, sep, value = line.partition(':')
        if sep and name.strip().lower() == 'content-length':
            try:
                length = int(value.strip())
            except ValueError:
                return None
    if length is None:
        return None
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        return None
    return json.loads(body.decode('utf-8'))


def respond(request_seq, command, body=None, success=True, message=None):
    msg = {
        'type': 'response', 'request_seq': request_seq,
        'command': command, 'success': success,
    }
    if body is not None:
        msg['body'] = body
    if message:
        msg['message'] = message
    send(msg)


def stopped(reason):
    send({'type': 'event', 'event': 'stopped',
          'body': {'reason': reason, 'threadId': 1}})


def finish():
    send({'type': 'event', 'event': 'terminated'})
    send({'type': 'event', 'event': 'exited', 'body': {'exitCode': 0}})
    # tiny grace: client reads terminated before proc.wait resolves
    time.sleep(0.3)


# наборы переменных по variablesReference (Locals потока 1 раскрывается
# вложенным items -> ref 20; Globals — одна строка)
_VARIABLES = {
    10: [
        {'name': 'x', 'value': '42', 'type': 'int', 'variablesReference': 0,
         'evaluateName': 'x'},
        {'name': 'counter', 'value': '3', 'type': 'int',
         'variablesReference': 0, 'evaluateName': 'counter'},
        {'name': 'items', 'value': '[1, 2, 3]', 'type': 'list',
         'variablesReference': 20, 'namedVariables': 3,
         'evaluateName': 'items'},
    ],
    11: [{'name': '__name__', 'value': "'__main__'", 'type': 'str',
          'variablesReference': 0, 'evaluateName': '__name__'}],
    30: [{'name': 'ticket', 'value': "'w2'", 'type': 'str',
          'variablesReference': 0, 'evaluateName': 'ticket'}],
    31: [{'name': '__name__', 'value': "'__main__'", 'type': 'str',
          'variablesReference': 0, 'evaluateName': '__name__'}],
    20: [
        {'name': '0', 'value': '1', 'type': 'int', 'variablesReference': 0,
         'evaluateName': 'items[0]'},
        {'name': '1', 'value': '2', 'type': 'int', 'variablesReference': 0,
         'evaluateName': 'items[1]'},
        {'name': '2', 'value': '3', 'type': 'int', 'variablesReference': 0,
         'evaluateName': 'items[2]'},
    ],
}


def main():
    if MODE == 'dead':
        sys.exit(3)
    launch_seq = None
    program = ''
    # cur_line — строка потока 1 (его и показывает D1-маршрут); шаги +1
    cur_line = 3
    # строки потоков для stackTrace: свои id кадров и строки (D2-панели)
    thread_lines = {2: 7}

    def thread_frames(tid):
        if tid == 1:
            line = cur_line
            base = 100
        else:
            line = thread_lines.get(tid, 7)
            base = 200
        frames = [
            {'id': base, 'name': '<module>', 'line': line, 'column': 1,
             'source': {'name': os.path.basename(program), 'path': program}},
            {'id': base + 1, 'name': 'main', 'line': 1, 'column': 1,
             'source': {'name': os.path.basename(program), 'path': program}},
        ]
        if tid == 1:
            # кадр без источника — проверка «переход недоступен»
            frames.append({'id': base + 2, 'name': 'builtins.range',
                           'line': 0, 'column': 1})
        return frames

    while True:
        msg = read_message()
        if msg is None:
            return 0
        command = msg.get('command') or ''
        args = msg.get('arguments') or {}
        seq = msg.get('seq')
        if command == 'initialize':
            journal('initialize')
            respond(seq, command, {'capabilities': {'supportsSetVariable': True}})
            if MODE != 'no_initialized':
                send({'type': 'event', 'event': 'initialized'})
        elif command == 'launch':
            journal('launch', {'program': args.get('program'),
                               'stopOnEntry': bool(args.get('stopOnEntry'))})
            launch_seq = seq
            program = str(args.get('program') or '')
            if args.get('stopOnEntry'):
                cur_line = 1
        elif command == 'setBreakpoints':
            lines = [b.get('line') for b in args.get('breakpoints') or []]
            journal('setBreakpoints', {
                'path': (args.get('source') or {}).get('path'), 'lines': lines,
            })
            respond(seq, command, {'breakpoints': [
                {'id': i + 1, 'verified': True, 'line': line,
                 'source': args.get('source')}
                for i, line in enumerate(lines)
            ]})
        elif command == 'configurationDone':
            journal('configurationDone')
            respond(seq, command)
            if launch_seq is not None:
                if MODE == 'launch_fail':
                    # настоящий debugpy прикладывает к сбою ещё и message
                    msg = {'type': 'response', 'request_seq': launch_seq,
                           'command': 'launch', 'success': False,
                           'body': {'output': 'Program not found'},
                           'message': 'Unable to start the Debug Session'}
                    send(msg)
                else:
                    respond(launch_seq, 'launch', {'debuggerStarted': True})
                    stopped('entry' if cur_line == 1 else 'breakpoint')
            if MODE == 'crash':
                time.sleep(0.3)
                sys.exit(7)
        elif command == 'stackTrace':
            journal('stackTrace', {'threadId': args.get('threadId')})
            if MODE == 'no_frames':
                respond(seq, command, {'stackFrames': [], 'totalFrames': 0})
            else:
                frames = thread_frames(int(args.get('threadId') or 1))
                respond(seq, command, {'stackFrames': frames,
                                       'totalFrames': len(frames)})
        elif command == 'threads':
            journal('threads')
            respond(seq, command, {'threads': [
                {'id': 1, 'name': 'MainThread'},
                {'id': 2, 'name': 'Worker'},
            ]})
        elif command == 'scopes':
            journal('scopes', {'frameId': args.get('frameId')})
            if MODE == 'no_scopes':
                respond(seq, command, success=False,
                        message='scopes недоступны')
            else:
                fid = int(args.get('frameId') or 0)
                tid = 2 if fid >= 200 else 1
                locals_ref = 10 if tid == 1 else 30
                globals_ref = 11 if tid == 1 else 31
                if fid in (102, 202):
                    # кадр без файла: только Globals
                    respond(seq, command, {'scopes': [
                        {'name': 'Globals', 'variablesReference': globals_ref},
                    ]})
                else:
                    respond(seq, command, {'scopes': [
                        {'name': 'Locals', 'variablesReference': locals_ref,
                         'namedVariables': 3},
                        {'name': 'Globals', 'variablesReference': globals_ref},
                    ]})
        elif command == 'variables':
            ref = int(args.get('variablesReference') or 0)
            journal('variables', {'variablesReference': ref})
            respond(seq, command, {'variables': _VARIABLES.get(ref, [])})
        elif command in ('next', 'stepIn', 'stepOut'):
            journal(command)
            respond(seq, command)
            cur_line += 1
            stopped('step')
        elif command == 'continue':
            journal('continue')
            respond(seq, command, {'allThreadsContinued': True})
            send({'type': 'event', 'event': 'output',
                  'body': {'category': 'stdout',
                           'output': 'Привет из debuggee\n'}})
            finish()
            return 0
        elif command == 'terminate':
            journal('terminate')
            respond(seq, command)
            finish()
            return 0
        else:
            journal(command)
            respond(seq, command)


if __name__ == '__main__':
    sys.exit(main())
"""


@pytest.fixture
def fake_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Кладёт фейковый адаптер и подключает его через env-шов; отдаёт журнал."""
    script = tmp_path / 'fake_dap_adapter.py'
    script.write_text(FAKE_ADAPTER_SOURCE, encoding='utf-8')
    monkeypatch.setenv(ENV_ADAPTER_COMMAND, f"'{sys.executable}' '{script}'")
    journal_path = tmp_path / 'dap_journal.jsonl'
    monkeypatch.setenv('PYQTP_FAKE_DAP_JOURNAL', str(journal_path))
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'normal')
    return journal_path


def journal_entries(journal_path: Path) -> list[dict[str, Any]]:
    """Записи JSONL-журнала запросов фейкового адаптера."""
    lines = journal_path.read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def journal_commands(journal_path: Path) -> list[str]:
    """Только имена запросов в порядке их чтения адаптером."""
    return [entry['command'] for entry in journal_entries(journal_path)]


def test_session_handshake_order_events_and_stack(fake_adapter: Path) -> None:
    """Ритуал: initialize→launch→setBreakpoints→configurationDone; кадр 1-based."""
    events: list[str] = []
    verified: list[int] = []
    program = '/proj/target.py'

    class _PushingSession(DapSession):
        """Хук handshake: свои setBreakpoints перед configurationDone."""

        async def on_breakpoints_ready(self) -> None:
            body = await self.request_body('setBreakpoints', {
                'source': {'path': program},
                'breakpoints': [{'line': 2}, {'line': 5}],
            })
            verified.extend(
                int(b['line']) for b in body.get('breakpoints', [])
                if isinstance(b, dict) and b.get('verified')
            )

    async def _flow() -> tuple[str, list[dict[str, Any]]]:
        session = _PushingSession(adapter_command(), lambda name, body: events.append(name))
        await session.start()
        try:
            error = await session.handshake(
                lc.launch_arguments(lc.LaunchConfig(name='тест', program=program)),
            )
            frames = await session.stack_trace(1)
            return error, frames
        finally:
            await session.close(terminate=True)

    error, frames = asyncio.run(_flow())
    assert error == ''
    assert verified == [2, 5]
    assert journal_commands(fake_adapter)[:4] == [
        'initialize', 'launch', 'setBreakpoints', 'configurationDone',
    ]
    assert 'initialized' in events and 'stopped' in events
    assert frames and frames[0]['line'] == 3
    assert frames[0]['source']['path'] == program


def test_session_initialized_timeout_message(fake_adapter: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Адаптер без ``initialized`` — handshake возвращает текст, не виснет."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'no_initialized')
    monkeypatch.setattr(session_mod, '_HANDSHAKE_TIMEOUT', 1.0)

    async def _flow() -> str:
        session = DapSession(adapter_command(), lambda name, body: None)
        await session.start()
        try:
            return await session.handshake({'program': 'x.py'})
        finally:
            await session.close(terminate=False)

    assert asyncio.run(_flow()) == 'Адаптер не прислал событие initialized'


def test_session_launch_failure_message(fake_adapter: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``launch`` success=False — человеческий текст из message + output."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'launch_fail')

    async def _flow() -> str:
        session = DapSession(adapter_command(), lambda name, body: None)
        await session.start()
        try:
            return await session.handshake({'program': 'x.py'})
        finally:
            await session.close(terminate=False)

    error = asyncio.run(_flow())
    assert error == 'Unable to start the Debug Session: Program not found'


def test_session_dead_adapter_raises(fake_adapter: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Адаптер умер сразу — висящие запросы падают ``DapError``, не тайм-аутом."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'dead')

    async def _flow() -> None:
        session = DapSession(adapter_command(), lambda name, body: None)
        await session.start()
        try:
            with pytest.raises(DapError):
                await session.handshake({'program': 'x.py'})
        finally:
            await session.close()

    asyncio.run(_flow())


def test_session_close_terminates_and_is_idempotent(fake_adapter: Path) -> None:
    """``close(terminate=True)`` шлёт terminate, повтор — noop, сессия closed."""

    async def _flow() -> tuple[bool, bool]:
        session = DapSession(adapter_command(), lambda name, body: None)
        await session.start()
        error = await session.handshake({'program': 'x.py'})
        assert error == ''
        await session.close(terminate=True)
        alive_before = session.alive
        await session.close(terminate=True)
        return session.closed, alive_before

    closed, alive_before = asyncio.run(_flow())
    assert closed is True
    assert alive_before is False
    assert 'terminate' in journal_commands(fake_adapter)


def test_adapter_command_default_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию — stdio-режим debugpy; env-шов разбирается shell-логикой."""
    monkeypatch.delenv(ENV_ADAPTER_COMMAND, raising=False)
    assert adapter_command() == [sys.executable, '-m', 'debugpy.adapter']
    monkeypatch.setenv(ENV_ADAPTER_COMMAND, "'/a b/x.py' --flag")
    assert adapter_command() == ['/a b/x.py', '--flag']


###########################
# Offscreen GUI харнес   #


@pytest.fixture
def org_app(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    """Уникальное пространство QSettings на тест (чистим до и после)."""
    app_name = f'TEST_{request.node.name}'
    settings = QSettings('PYQTP_TESTS', app_name)
    settings.clear()
    settings.sync()
    yield 'PYQTP_TESTS', app_name
    settings.clear()
    settings.sync()


def make_window(org_app: tuple[str, str]) -> IdeMainWindow:
    """IdeMainWindow c KDD вне launcher'а (зависимость от qapp — на вызывающем)."""
    org, app_name = org_app
    window = IdeMainWindow(
        org_name=org, app_name=app_name, dock_system_type=ViewsManagerType.kdd
    )
    args = window.settings_parse_argv([])
    window.settings_read(**vars(args))
    window.setup()
    window.setup_ui()
    return window


class FakeDocument:
    """Минимальная реализация протокола ``Document`` (реальный ввод-вывод не нужен)."""

    def __init__(self, identity: str) -> None:
        self._identity = identity
        self._dirty = False

    def identity(self) -> str:
        return self._identity

    def is_modified(self) -> bool:
        return self._dirty

    def set_modified(self, modified: bool = True) -> None:  # noqa: FBT001, FBT002
        self._dirty = modified

    def do_save(self) -> bool:
        return True

    def save(self) -> bool:
        # копия дефолтной реализации протокола Document (протокол — не база)
        if self.do_save():
            self.set_modified(False)
            return True
        return False


class FakeEditWidget(QObject):
    """Фейковый редактор: рекордеры сообщений этапа 11 + каретка + сигнал дорожки.

    Повторяет контракт ``QtMonacoEditorWidget``: ``set_breakpoints(lines)``,
    ``set_execution_line({'line': N|0})``, ``breakpoint_toggle_requested``
    (клик по глифу брейкпоинта в дорожке Monaco), зеркало ``current_cursor``.
    """

    breakpoint_toggle_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.options_calls: list[dict[str, Any]] = []
        self.theme_calls: list[str] = []
        self.breakpoint_calls: list[list[int]] = []
        self.exec_calls: list[dict[str, int]] = []
        self.cursor_calls: list[tuple[int, int, str]] = []
        self.current_cursor: dict[str, int] = {'line': 1, 'column': 1}

    def update_editor_options(self, options: dict[str, Any]) -> None:
        self.options_calls.append(dict(options))

    def set_theme(self, theme: str) -> None:
        self.theme_calls.append(theme)

    def set_breakpoints(self, lines: list[int]) -> None:
        self.breakpoint_calls.append(list(lines))

    def set_execution_line(self, payload: dict) -> None:
        self.exec_calls.append(dict(payload))

    def set_cursor(
        self, line: int, column: int = 1, *, move_to_position: str = ''
    ) -> None:
        self.cursor_calls.append((line, column, move_to_position))
        self.current_cursor = {'line': line, 'column': column}


class FakeDocView(BaseDocumentView):
    """Документная вьюха без Monaco/WebEngine — с фейковым редактором DAP."""

    def __init__(self, main_wnd, /, *, key: str) -> None:  # noqa: ANN001
        super().__init__(main_wnd, key=key)
        self.focus_child = QWidget(self)
        self.focus_child.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.edit_widget = FakeEditWidget()

    def document(self) -> FakeDocument:
        if not hasattr(self, '_fake_doc'):
            self._fake_doc = FakeDocument(self.key)
        return self._fake_doc


@pytest.fixture
def fake_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_mod, '_document_view_cls_from_identity', lambda _i: FakeDocView)


@pytest.fixture
def wnd(
    org_app: tuple[str, str],
    qapp: QApplication,
    fake_docs: None,
) -> Iterator[IdeMainWindow]:
    window = make_window(org_app)
    yield window
    window.close()


@pytest.fixture
def prj_root(tmp_path: Path) -> Path:
    """Проект с двумя файлами по 6 строк (программы для фейковой сессии)."""
    root = tmp_path / 'proj'
    root.mkdir()
    for name in ('a.py', 'b.py'):
        (root / name).write_text(
            ''.join(f'VALUE_{name[0]}_{i} = {i}\n' for i in range(1, 7)),
            encoding='utf-8',
        )
    return root


def fake_edit(wnd: IdeMainWindow, path: str) -> FakeEditWidget:
    """Фейковый редактор открытого документа (проверка сообщений ему)."""
    view = wnd.doc_ctrl.document_view(path)
    assert isinstance(view, FakeDocView)
    return view.edit_widget


def wait_until(
    qtbot: Any,
    predicate: Any,
    *,
    timeout: float = 15.0,
    poll: float = 0.05,
    message: str = '',
) -> None:
    """Опрашивает ``predicate`` (пропуская события Qt) до ``timeout`` секунд."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message or 'условие не выполнилось за таймаут')
        qtbot.wait(int(poll * 1000))


###########################
# Меню, экшены, консоль  #


def test_debug_menu_chords_and_gating(wnd: IdeMainWindow, prj_root: Path) -> None:
    """Меню «Отладка»: 6 экшенов, связки VS Code, гейтинг по проекту/состоянию."""
    menu = wnd.menuBar().by_path('Отладка')
    assert isinstance(menu, QMenu)
    ctrl = wnd.dap_ctrl
    actions = (
        ctrl.action_debug_start,
        ctrl.action_debug_stop,
        ctrl.action_debug_toggle_breakpoint,
        ctrl.action_debug_step_over,
        ctrl.action_debug_step_into,
        ctrl.action_debug_step_out,
    )
    assert all(action in menu.actions() for action in actions)
    assert {str(a.shortcut().toString()) for a in actions} == {
        'F5', 'Shift+F5', 'F9', 'F10', 'F11', 'Shift+F11',
    }
    # без проекта: старт/брейкпоинт выключены, стоп/шаги — тем более
    assert [a.isEnabled() for a in actions] == [False] * 6

    wnd.project_ctrl.open_project(prj_root)
    assert [a.isEnabled() for a in actions] == [True, False, True, False, False, False]

    wnd.project_ctrl.close_project()
    assert [a.isEnabled() for a in actions] == [False] * 6


def test_console_view_passthrough(wnd: IdeMainWindow) -> None:
    """Панель «Консоль отладки»: строка состояния, показ, кнопка «Очистить»."""
    ctrl = wnd.dap_ctrl
    view = ctrl.debug_console_view
    assert view is not None
    assert view.windowTitle() == 'Консоль отладки'
    assert view.status_text() == 'Не отлаживаем'
    ctrl._console_line('[Отладка] проверка')
    ctrl._console_line('')  # пустая строка — тоже строка вывода
    view.append_text('чанк вывода\nсо второй строки\n')
    assert view.console_text() == '[Отладка] проверка\n\nчанк вывода\nсо второй строки'
    view._clear_button.click()
    assert view.console_text() == ''


def test_start_requires_project(wnd: IdeMainWindow) -> None:
    """Без проекта F5 — отказ с текстом, стат-машина не двигается."""
    ctrl = wnd.dap_ctrl
    assert ctrl.state == STATE_IDLE
    assert ctrl.start_debugging() == 'Проект не открыт'
    assert ctrl.state == STATE_IDLE


def test_start_needs_program(wnd: IdeMainWindow, prj_root: Path) -> None:
    """Проект есть, но нет активного документа и launch.json — понятный отказ."""
    ctrl = wnd.dap_ctrl
    wnd.project_ctrl.open_project(prj_root)
    error = ctrl.start_debugging()
    assert 'Нет активного документа' in error
    assert ctrl.state == STATE_IDLE
    view = ctrl.debug_console_view
    assert view is not None
    assert 'Нет активного документа' in view.console_text()


def test_program_file_checked(wnd: IdeMainWindow, prj_root: Path) -> None:
    """launch.json указывает на несуществующий файл — «Программа не найдена»."""
    project_dir = prj_root / '.pyqtp'
    project_dir.mkdir()
    payload = {'configurations': [{'name': 'Кривая', 'program': 'gone.py'}]}
    (project_dir / 'launch.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8',
    )
    ctrl = wnd.dap_ctrl
    wnd.project_ctrl.open_project(prj_root)
    error = ctrl.start_debugging()
    assert error.startswith('Программа не найдена:')
    assert ctrl.state == STATE_IDLE


################################
# Живой цикл на фейковом адаптере #


def test_full_session_flow(wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any) -> None:
    """F5 без launch.json (активный документ) → останов → шаг → continue → конец."""
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(doc_a)
    edit = fake_edit(wnd, str(doc_a))
    view = ctrl.debug_console_view
    assert view is not None

    assert ctrl.start_debugging() == ''
    assert ctrl.state == STATE_RUNNING
    assert view.status_text() == 'Выполняется'
    assert f'Запуск «{lc.DEFAULT_CONFIG_NAME}»: {doc_a}' in view.console_text()

    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='фейковый адаптер не поставил сессию на останов')
    assert view.status_text() == 'Остановлено'
    assert ctrl.debug_status() == {
        'state': 'stopped', 'path': str(doc_a), 'line': 3, 'breakpoints': 0,
    }
    assert edit.cursor_calls[-1] == (3, 1, 'center')
    assert edit.exec_calls[-1] == {'line': 3}
    launch_entry = next(e for e in journal_entries(fake_adapter) if e['command'] == 'launch')
    assert launch_entry['program'] == str(doc_a)
    assert launch_entry['stopOnEntry'] is False

    assert ctrl.step_over() == ''
    assert ctrl.state == STATE_RUNNING
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='шаг с проходом не привёл к останову')
    assert edit.cursor_calls[-1] == (4, 1, 'center')

    assert ctrl.continue_execution() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_IDLE,
               message='continue не довёл сессию до завершения')
    text = view.console_text()
    assert 'Привет из debuggee' in text
    assert 'Программа завершилась (код 0)' in text
    assert 'Отладка завершена' in text
    assert view.status_text() == 'Не отлаживаем'
    assert edit.exec_calls[-1] == {'line': 0}
    assert ctrl.debug_status()['path'] == ''


def test_launch_json_precedence_and_stop_on_entry(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """launch.json старше активного документа; stopOnEntry → кадр на строке 1."""
    project_dir = prj_root / '.pyqtp'
    project_dir.mkdir()
    payload = {'configurations': [
        {'name': 'Тест', 'program': 'a.py', 'stopOnEntry': True},
        {'name': 'Вторая', 'program': 'b.py'},
    ]}
    (project_dir / 'launch.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8',
    )
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    # открыт другой документ — программа всё равно берётся из launch.json
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(prj_root / 'b.py')
    edit_a = None

    assert ctrl.start_debugging() == ''
    view = ctrl.debug_console_view
    assert view is not None
    assert f'Запуск «Тест»: {doc_a}' in view.console_text()
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='останов по entry не наступил')
    edit_a = fake_edit(wnd, str(doc_a))  # вкладка a.py открылась сама (reveal кадра)
    assert edit_a.cursor_calls[-1] == (1, 1, 'center')
    assert edit_a.exec_calls[-1] == {'line': 1}
    launch_entry = next(e for e in journal_entries(fake_adapter) if e['command'] == 'launch')
    assert launch_entry['program'] == str(doc_a)
    assert launch_entry['stopOnEntry'] is True

    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE
    wait_until(qtbot, lambda: 'terminate' in journal_commands(fake_adapter),
               message='стоп не послал terminate адаптеру')
    assert 'Остановка по требованию' in view.console_text()


def test_crash_adapter_resets_state(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path,
    monkeypatch: pytest.MonkeyPatch, qtbot: Any
) -> None:
    """Смерть адаптера: консоль с кодом, стат-машина обратно в IDLE."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'crash')
    ctrl = wnd.dap_ctrl
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(prj_root / 'a.py')
    assert ctrl.start_debugging() == ''
    view = ctrl.debug_console_view
    assert view is not None
    wait_until(
        qtbot,
        lambda: ctrl.state == STATE_IDLE and 'Адаптер отладки завершился (код 7)' in view.console_text(),
        message='сбой адаптера не вернул контрол в IDLE с текстом в консоли',
    )
    assert ctrl.debug_status()['line'] == 0


def test_empty_stack_keeps_running(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path,
    monkeypatch: pytest.MonkeyPatch, qtbot: Any
) -> None:
    """Пустой stackTrace: оставаться RUNNING, никакого «остановлено»."""
    monkeypatch.setenv('PYQTP_FAKE_DAP_MODE', 'no_frames')
    ctrl = wnd.dap_ctrl
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(prj_root / 'a.py')
    assert ctrl.start_debugging() == ''
    qtbot.wait(400)
    assert ctrl.state == STATE_RUNNING
    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE


################################
# Брейкпоинты контрола          #


def test_breakpoints_file_and_editor_sync(wnd: IdeMainWindow, prj_root: Path) -> None:
    """F9-маршрут: файл проекта, раздача строк редактору, gutter-клик, корень."""
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    doc_b = prj_root / 'b.py'
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(doc_a)
    wnd.doc_ctrl.open_document(doc_b)
    edit_a = fake_edit(wnd, str(doc_a))
    edit_b = fake_edit(wnd, str(doc_b))

    assert ctrl.toggle_breakpoint_at(str(doc_a), 4) is True
    assert ctrl.breakpoints_snapshot() == [{'path': str(doc_a), 'line': 4}]
    assert bp.read_breakpoints(prj_root) == [Breakpoint(path=str(doc_a), line=4)]
    assert edit_a.breakpoint_calls[-1] == [4]
    # чужой файл не задевает: широковещательный набор для него всегда пуст
    assert all(lines == [] for lines in edit_b.breakpoint_calls)

    ctrl.toggle_breakpoint_at(str(doc_a), 2)
    assert edit_a.breakpoint_calls[-1] == [2, 4]
    ctrl.toggle_breakpoint_at(str(doc_a), 2)  # снятие
    assert edit_a.breakpoint_calls[-1] == [4]

    # клик по глифу брейкпоинта в дорожке Monaco (сигнал редактора)
    edit_a.breakpoint_toggle_requested.emit(5)
    assert ctrl.breakpoint_lines_for(str(doc_a)) == [4, 5]

    # за пределами корня проекта — отказ
    assert ctrl.toggle_breakpoint_at(str(prj_root.parent / 'outside.py'), 1) is False
    # закрытие проекта снимает набор из памяти, файл на диске не трогает
    wnd.project_ctrl.close_project()
    assert ctrl.breakpoints_snapshot() == []
    assert bp.read_breakpoints(prj_root) == [
        Breakpoint(path=str(doc_a), line=4), Breakpoint(path=str(doc_a), line=5),
    ]


def test_f9_takes_line_from_caret_mirror(wnd: IdeMainWindow, prj_root: Path) -> None:
    """F9 берёт строку из python-зеркала каретки редактора (как 9.7/10.2)."""
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(doc_a)
    edit = fake_edit(wnd, str(doc_a))
    edit.current_cursor = {'line': 3, 'column': 7}
    ctrl.toggle_breakpoint_active()
    assert ctrl.breakpoints_snapshot() == [{'path': str(doc_a), 'line': 3}]
    # каретка на границе несуществующей строки — тоже брейкпоинт (v1 без проверки)
    edit.current_cursor = {'line': 99, 'column': 1}
    ctrl.toggle_breakpoint_active()
    assert ctrl.breakpoint_lines_for(str(doc_a)) == [3, 99]


def test_open_project_loads_and_prunes_breakpoints(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Открытие проекта: снимок из файла, мёртвые файлы срезаются в файле."""
    project_dir = prj_root / '.pyqtp'
    project_dir.mkdir()
    payload = {'breakpoints': [
        {'path': str(prj_root / 'a.py'), 'line': 3},
        {'path': str(prj_root / 'gone.py'), 'line': 1},
        {'path': str(prj_root / 'a.py'), 'line': 1},
    ]}
    (project_dir / 'breakpoints.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8',
    )
    ctrl = wnd.dap_ctrl
    wnd.project_ctrl.open_project(prj_root)
    assert ctrl.breakpoints_snapshot() == [
        {'path': str(prj_root / 'a.py'), 'line': 1},
        {'path': str(prj_root / 'a.py'), 'line': 3},
    ]
    assert bp.read_breakpoints(prj_root) == [
        Breakpoint(path=str(prj_root / 'a.py'), line=1),
        Breakpoint(path=str(prj_root / 'a.py'), line=3),
    ]


def test_initial_breakpoints_pushed_in_handshake(
    wnd: IdeMainWindow, prj_root: Path, fake_adapter: Path, qtbot: Any
) -> None:
    """Снимок брейкпоинтов уходит адаптеру до configurationDone."""
    ctrl = wnd.dap_ctrl
    doc_a = prj_root / 'a.py'
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(doc_a)
    ctrl.toggle_breakpoint_at(str(doc_a), 2)
    ctrl.toggle_breakpoint_at(str(doc_a), 5)

    assert ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED,
               message='сессия на фейковом адаптере не встала на останов')
    commands = journal_commands(fake_adapter)
    assert commands[:4] == [
        'initialize', 'launch', 'setBreakpoints', 'configurationDone',
    ]
    sbp = next(e for e in journal_entries(fake_adapter) if e['command'] == 'setBreakpoints')
    assert sbp['path'] == str(doc_a)
    assert sbp['lines'] == [2, 5]

    # правка набора в живой сессии — второй setBreakpoints следом
    ctrl.toggle_breakpoint_at(str(doc_a), 6)
    wait_until(
        qtbot,
        lambda: sum(1 for e in journal_entries(fake_adapter) if e['command'] == 'setBreakpoints') >= 2,
        message='изменённый набор не синхронизирован в живую сессию',
    )
    last_sbp = [e for e in journal_entries(fake_adapter) if e['command'] == 'setBreakpoints'][-1]
    assert last_sbp['lines'] == [2, 5, 6]

    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE


###########################
# Live: настоящий debugpy #


@pytest.fixture
def live_root(tmp_path: Path) -> Path:
    """Крошечный проект с launch.json (``stopOnEntry``) для живого прогона."""
    root = tmp_path / 'debuggee'
    root.mkdir()
    (root / 'prog.py').write_text('x = 1\ny = 2\nprint(x + y)\n', encoding='utf-8')
    project_dir = root / '.pyqtp'
    project_dir.mkdir()
    payload = {'configurations': [
        {'name': 'Живой прогон', 'program': 'prog.py', 'stopOnEntry': True},
    ]}
    (project_dir / 'launch.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8',
    )
    return root


@pytest.fixture
def clean_dap_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Никаких фейковых подмен адаптера — говорим с настоящим debugpy."""
    monkeypatch.delenv(ENV_ADAPTER_COMMAND, raising=False)
    monkeypatch.delenv('PYQTP_FAKE_DAP_JOURNAL', raising=False)
    monkeypatch.delenv('PYQTP_FAKE_DAP_MODE', raising=False)


@pytest.mark.live
def test_live_debugpy_entry_steps_and_output(
    wnd: IdeMainWindow, live_root: Path, clean_dap_env: None, qtbot: Any
) -> None:
    """Настоящая сессия: entry-останов, два шага F10, print в консоль, код 0."""
    ctrl = wnd.dap_ctrl
    prog = live_root / 'prog.py'
    wnd.project_ctrl.open_project(live_root)
    assert ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED, timeout=40.0,
               message='debugpy не остановился на entry')
    status = ctrl.debug_status()
    assert Path(status['path']).name == 'prog.py'
    assert status['line'] == 1

    assert ctrl.step_over() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED, timeout=20.0,
               message='первый шаг F10 не дал останов')
    assert ctrl.debug_status()['line'] == 2

    assert ctrl.step_over() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED, timeout=20.0,
               message='второй шаг F10 не дал останов')
    assert ctrl.debug_status()['line'] == 3

    assert ctrl.continue_execution() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_IDLE, timeout=30.0,
               message='continue не довёл живую сессию до завершения')
    view = ctrl.debug_console_view
    assert view is not None
    text = view.console_text()
    assert '3' in text  # print(x + y) — в выводе debuggee
    assert 'Программа завершилась (код 0)' in text
    assert 'Отладка завершена' in text


@pytest.fixture
def loop_root(tmp_path: Path) -> Path:
    """Проект с циклом-паузой: брейкпоинт гарантированно успевает сработать."""
    root = tmp_path / 'loopy'
    root.mkdir()
    (root / 'loopy.py').write_text(
        'import time\n\nfor i in range(30):\n    time.sleep(0.1)\n',
        encoding='utf-8',
    )
    return root


@pytest.mark.live
def test_live_debugpy_breakpoint_hit(
    wnd: IdeMainWindow, loop_root: Path, clean_dap_env: None, qtbot: Any
) -> None:
    """Брейкпоинт до запуска живёт в handshake и реально срабатывает."""
    ctrl = wnd.dap_ctrl
    target = loop_root / 'loopy.py'
    wnd.project_ctrl.open_project(loop_root)
    wnd.doc_ctrl.open_document(target)
    assert ctrl.toggle_breakpoint_at(str(target), 4) is True

    assert ctrl.start_debugging() == ''
    wait_until(qtbot, lambda: ctrl.state == STATE_STOPPED, timeout=40.0,
               message='брейкпоинт в живой сессии не сработал')
    status = ctrl.debug_status()
    assert Path(status['path']).name == 'loopy.py'
    assert status['line'] == 4

    ctrl.stop_debugging()
    assert ctrl.state == STATE_IDLE
    view = ctrl.debug_console_view
    assert view is not None
    assert 'Остановка по требованию' in view.console_text()


#############################
# WebEngine: Monaco probes  #

JS_BUILD_OK = (Path(qtmonaco.__file__).parent / 'js_build' / 'index.html').is_file()

webengine_only = pytest.mark.skipif(
    not JS_BUILD_OK, reason='qtmonaco/js_build не собран (npm run build)',
)

JS_READY = "window.qtmonaco && window.qtmonaco.editor ? 'READY' : 'NOT_READY'"


def run_js(widget: QtMonacoEditorWidget, script: str, *, timeout: float = 20.0) -> Any:
    """Выполняет JS в странице редактора и ждёт результат (локальный event loop)."""
    holder: dict[str, Any] = {}

    def _cb(value: Any) -> None:
        holder['value'] = value

    widget.page().runJavaScript(script, 0, _cb)
    spent, step = 0.0, 0.05
    while 'value' not in holder and spent < timeout:
        loop = QEventLoop()
        QTimer.singleShot(int(step * 1000), loop.quit)
        loop.exec()
        spent += step
    return holder.get('value')


def wait_editor_ready(qtbot: Any, widget: QtMonacoEditorWidget) -> None:
    """Ждёт готовности Monaco и моста в странице редактора (до 2 минут)."""
    wait_until(
        qtbot,
        lambda: run_js(widget, JS_READY) == 'READY',
        timeout=120.0, poll=1.0,
        message='Monaco в странице редактора так и не поднялся',
    )
    wait_until(
        qtbot,
        lambda: (
            widget.bridge_initialized
            and run_js(widget, 'String(window.qtmonaco.initialized)') == 'true'
        ),
        timeout=30.0, poll=0.5,
        message='мост QWebChannel (bridge_initialized) не поднялся',
    )
    qtbot.wait(300)


def dom_count(widget: QtMonacoEditorWidget, css: str) -> str:
    """Число DOM-узлов под селектором (строковый маркер — не булев)."""
    return run_js(widget, f"String(document.querySelectorAll('{css}').length)")


@pytest.fixture
def dap_editor(
    qapp: QApplication, tmp_path: Path
) -> Iterator[QtMonacoEditorWidget]:
    """Редактор без LSP (``lsp_host=[]``) с семистрочным файлом в tmp_path."""
    path = tmp_path / 'dap_file.py'
    path.write_text(
        'def foo():\n    first = 1\n    second = 2\n\n    third = 3\n    return first\n',
        encoding='utf-8',
    )
    widget = QtMonacoEditorWidget(path, lsp_host=[])
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


@pytest.mark.webengine
@webengine_only
def test_webengine_set_breakpoints_paints_glyphs(
    qtbot: Any, dap_editor: QtMonacoEditorWidget
) -> None:
    """``set_breakpoints`` рисует красные глифы в дорожке и чистит их до пустых."""
    wait_editor_ready(qtbot, dap_editor)
    assert dom_count(dap_editor, '.pyqtp-breakpoint-glyph') == '0'
    dap_editor.set_breakpoints([2, 5])
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-breakpoint-glyph') == '2',
               timeout=20.0, poll=0.3,
               message='глифы брейкпоинтов не появились в DOM')
    dap_editor.set_breakpoints([3])
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-breakpoint-glyph') == '1',
               timeout=20.0, poll=0.3,
               message='набор глифов брейкпоинтов не обновился')
    dap_editor.set_breakpoints([])
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-breakpoint-glyph') == '0',
               timeout=20.0, poll=0.3,
               message='глифы брейкпоинтов не снялись')


@pytest.mark.webengine
@webengine_only
def test_webengine_execution_line_decoration(
    qtbot: Any, dap_editor: QtMonacoEditorWidget
) -> None:
    """``set_execution_line`` — стрелка в дорожке + подсветка строки, ``0`` снимает."""
    wait_editor_ready(qtbot, dap_editor)
    assert dom_count(dap_editor, '.pyqtp-exec-gutter') == '0'
    assert dom_count(dap_editor, '.pyqtp-exec-line') == '0'
    dap_editor.set_execution_line({'line': 4})
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-exec-gutter') == '1',
               timeout=20.0, poll=0.3,
               message='стрелка строки останова не появилась в DOM')
    assert dom_count(dap_editor, '.pyqtp-exec-line') == '1'
    dap_editor.set_execution_line({'line': 0})
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-exec-gutter') == '0',
               timeout=20.0, poll=0.3,
               message='подсветка строки останова не снялась')


@pytest.mark.webengine
@webengine_only
def test_webengine_gutter_click_routing(
    qtbot: Any, dap_editor: QtMonacoEditorWidget
) -> None:
    """Глиф брейкпоинта в дорожке — ``breakpoint_toggle_requested`` (9.7 — закладка)."""
    wait_editor_ready(qtbot, dap_editor)
    dap_editor.set_breakpoints([3])
    wait_until(qtbot, lambda: dom_count(dap_editor, '.pyqtp-breakpoint-glyph') == '1',
               timeout=20.0, poll=0.3,
               message='глиф брейкпоинта не подготовлен для пробы клика')

    with qtbot.waitSignal(dap_editor.breakpoint_toggle_requested, timeout=10000) as blocker:
        run_js(dap_editor, "window.qtmonaco.__pyqtp_gutter_click(3, 'breakpoint')")
    assert blocker.args == [3]

    with qtbot.waitSignal(dap_editor.bookmark_toggle_requested, timeout=10000) as blocker:
        run_js(dap_editor, "window.qtmonaco.__pyqtp_gutter_click(5, 'bookmark')")
    assert blocker.args == [5]
