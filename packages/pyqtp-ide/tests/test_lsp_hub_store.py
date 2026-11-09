"""Этап 10.1 — панель «Проблемы»: чистый модуль и стор диагностик хаба.

Юниты без GUI и без процессов (дефолтный прогон) — чистый модуль
``pyqtp/controls/problems_control/problems.py``: толерантный разбор
``publishDiagnostics`` (LSP 0-based -> Monaco 1-based, severity по умолчанию,
``code`` int -> str, отсев мусора и не-файловых uri), сортировка, счётчики и
подписи; и python-стор диагностик ``LspHub`` (публикация/слияние вкладов
серверов по uri, снятие ключа пустой публикацией, подписчики, смерть сервера,
остановка хаба).

Живые тесты (маркер ``live``) — трафик ``publishDiagnostics`` через настоящий
ws-хаб: детерминированный фейковый LSP-сервер (``FakeStdioServer``,
``python <файл>``) и настоящий basedpyright (skipif без бинаря).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from typing import TYPE_CHECKING

import pytest
from websockets.sync.client import connect

from pyqtp.controls.lsp_control.lsp_hub import LspHub
from pyqtp.controls.problems_control import problems as prob

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from websockets.sync.client import ClientConnection


# ===========================
# Чистый модуль problems.py   #
# ===========================


def _diag(
    line: int,
    char: int,
    message: str,
    **extra: object,
) -> dict:
    """Одна запись Diagnostic в LSP-координатах (0-based)."""
    return {
        'range': {
            'start': {'line': line, 'character': char},
            'end': {'line': line, 'character': char + 1},
        },
        'message': message,
        **extra,
    }


def test_diagnostics_from_publish_coords_and_sort() -> None:
    """0-based -> 1-based, сортировка (строка, колонка), мусорные записи — мимо."""
    uri = 'file:///proj/a.py'
    got_uri, items = prob.diagnostics_from_publish(
        {
            'uri': uri,
            'diagnostics': [
                _diag(3, 4, 'позже строкой', severity=2, code=501, source='ruff'),
                _diag(0, 7, 'без severity'),
                _diag(0, 2, 'hint раньше колонкой', severity=4, code='E999'),
                {'message': 'нет range'},
                {'range': {'start': {}, 'end': {}}, 'message': ''},
                'не словарь',
                None,
            ],
        },
        owner='basedpyright',
    )
    assert got_uri == uri
    assert [(p.line, p.col) for p in items] == [(1, 3), (1, 8), (4, 5)]
    hint, default, later = items
    # отсутствие severity по спецификации = Ошибка; owner проставляется разборщиком
    assert default.severity == prob.SEVERITY_ERROR
    assert default.owner == 'basedpyright'
    # code: integer -> строка; source пробрасывается
    assert later.code == '501'
    assert later.source == 'ruff'
    # hint-серьёзность сохраняется; range.missing -> start; пустой source — ''
    assert hint.severity == prob.SEVERITY_HINT
    assert (hint.end_line, hint.end_col) == (1, 4)
    assert hint.code == 'E999'
    assert hint.source == ''


def test_diagnostics_from_publish_bad_uris_and_params() -> None:
    """Не-файловый/битый uri и мусор в params -> «пустая публикация»."""
    assert prob.diagnostics_from_publish(
        {'uri': 'untitled:Untitled-1', 'diagnostics': [_diag(0, 0, 'х')]}, 'x',
    ) == ('', [])
    assert prob.diagnostics_from_publish(
        {'uri': 'no-scheme/path', 'diagnostics': []}, 'x',
    ) == ('', [])
    assert prob.diagnostics_from_publish({'diagnostics': []}, 'x') == ('', [])
    assert prob.diagnostics_from_publish(None, 'x') == ('', [])
    # валидный uri без поля diagnostics — штатная пустота (очистка)
    assert prob.diagnostics_from_publish({'uri': 'file:///a.py'}, 'x') == (
        'file:///a.py', [],
    )


def test_file_path_from_uri() -> None:
    assert prob.file_path_from_uri('file:///proj/%D1%8E.py') == '/proj/ю.py'
    assert prob.file_path_from_uri('file:///proj/%25.py') == '/proj/%.py'
    assert prob.file_path_from_uri('http://example.com/a.py') is None
    assert prob.file_path_from_uri('') is None


def test_severity_counts_and_summaries() -> None:
    items = [
        _problem(1, 1, prob.SEVERITY_ERROR),
        _problem(2, 1, prob.SEVERITY_ERROR),
        _problem(3, 1, prob.SEVERITY_WARNING),
    ]
    assert prob.severity_counts(items) == {
        prob.SEVERITY_ERROR: 2, prob.SEVERITY_WARNING: 1,
    }
    assert prob.summarize_severities(items) == 'Ошибок: 2, Предупреждений: 1'
    assert prob.summarize_severities([]) == ''
    assert prob.severity_label(prob.SEVERITY_HINT) == 'Подсказка'
    # неизвестный код подписывается как «Ошибка» (как и при разборе)
    assert prob.severity_label(99) == 'Ошибка'


def test_problem_label_formats() -> None:
    base = _problem(12, 5, prob.SEVERITY_ERROR, message='текст')
    assert prob.problem_label(base) == '12:5 Ошибка: текст'
    with_code = _problem(12, 5, prob.SEVERITY_ERROR, message='текст', code='E1')
    assert prob.problem_label(with_code) == '12:5 Ошибка: текст (E1)'
    with_source = _problem(
        1, 1, prob.SEVERITY_WARNING, message='м', source='ruff',
    )
    assert prob.problem_label(with_source) == '1:1 Предупреждение: м [ruff]'


def _problem(
    line: int,
    col: int,
    severity: int,
    *,
    message: str = 'м',
    code: str = '',
    source: str = '',
    owner: str = '',
) -> prob.Problem:
    return prob.Problem(
        line=line, col=col, end_line=line, end_col=col, severity=severity,
        message=message, code=code, source=source, owner=owner,
    )


# ==========================
# Стор диагностик в LspHub  #
# ==========================


@pytest.fixture
def hub() -> Iterator[LspHub]:
    """LspHub без главного окна (стор живёт без жизненного цикла окна)."""
    instance = LspHub()
    yield instance
    instance.shutdown()


def test_hub_store_merges_owners_sorted(hub: LspHub) -> None:
    """Basedpyright и ruff публикуют в один uri — в снимке слитный порядок."""
    hub._record_diagnostics(  # python-API стора без подъёма процессов
        'basedpyright', 'file:///p/a.py', (_problem(5, 1, 1), _problem(1, 1, 2)),
    )
    hub._record_diagnostics(
        'ruff', 'file:///p/a.py', (_problem(3, 1, 2, code='F401'),),
    )
    snap = hub.diagnostics_snapshot()
    assert list(snap) == ['file:///p/a.py']
    got = snap['file:///p/a.py']
    assert [(p.line, p.owner) for p in got] == [
        (1, 'basedpyright'), (3, 'ruff'), (5, 'basedpyright'),
    ]
    # повторная публикация сервера заменяет его вклад, а не дополняет
    hub._record_diagnostics('ruff', 'file:///p/a.py', (_problem(9, 2, 1),))
    got = hub.diagnostics_snapshot()['file:///p/a.py']
    assert [(p.line, p.owner) for p in got] == [
        (1, 'basedpyright'), (5, 'basedpyright'), (9, 'ruff'),
    ]


def test_hub_store_empty_publish_clears_key(hub: LspHub) -> None:
    uri = 'file:///p/a.py'
    hub._record_diagnostics('basedpyright', uri, (_problem(1, 1, 1),))
    assert uri in hub.diagnostics_snapshot()
    hub._record_diagnostics('basedpyright', uri, ())
    assert hub.diagnostics_snapshot() == {}
    # чужой вклад не пострадал: чистим только пару (uri, сервер)
    hub._record_diagnostics('basedpyright', uri, (_problem(1, 1, 1),))
    hub._record_diagnostics('ruff', 'file:///p/b.py', (_problem(2, 1, 1),))
    hub._record_diagnostics('basedpyright', uri, ())
    assert list(hub.diagnostics_snapshot()) == ['file:///p/b.py']


def test_hub_store_observers(hub: LspHub) -> None:
    """Подписчика зовём на изменение; дубликат не добавляем; снятие — молча."""
    calls: list[int] = []

    def cb() -> None:
        calls.append(1)

    hub.add_diagnostics_observer(cb)
    hub.add_diagnostics_observer(cb)  # дубликат
    hub._record_diagnostics('s', 'file:///p/a.py', (_problem(1, 1, 1),))
    hub._record_diagnostics('s', 'file:///p/a.py', ())
    assert len(calls) == 2
    hub.remove_diagnostics_observer(cb)
    hub.remove_diagnostics_observer(cb)
    hub._record_diagnostics('s', 'file:///p/a.py', (_problem(1, 1, 1),))
    assert len(calls) == 2


def test_hub_store_dead_server_purge(hub: LspHub) -> None:
    """Смерть процесса сервера снимает весь его вклад (панель без призраков)."""
    hub._record_diagnostics('ruff', 'file:///p/a.py', (_problem(1, 1, 1),))
    hub._record_diagnostics('ruff', 'file:///p/b.py', (_problem(2, 1, 2),))
    hub._record_diagnostics('basedpyright', 'file:///p/a.py', (_problem(4, 1, 1),))
    called: list[int] = []
    hub.add_diagnostics_observer(lambda: called.append(1))
    hub._drop_diagnostics_of('ruff')
    snap = hub.diagnostics_snapshot()
    assert [(p.line, p.owner) for p in snap['file:///p/a.py']] == [
        (4, 'basedpyright'),
    ]
    assert 'file:///p/b.py' not in snap
    assert called == [1]
    hub._drop_diagnostics_of('ruff')  # повтор — тихо, без оповещения
    assert called == [1]


def test_hub_shutdown_clears_store_and_notifies(hub: LspHub) -> None:
    """Остановленному хабу нечего показывать: стор пуст, подписчик уведомлён."""
    hub.ensure_started()
    hub._record_diagnostics('s', 'file:///p/a.py', (_problem(1, 1, 1),))
    called: list[int] = []
    hub.add_diagnostics_observer(lambda: called.append(1))
    hub.shutdown()
    assert hub.diagnostics_snapshot() == {}
    assert called == [1]


# ============================
# Живой трафик через ws-хаб   #
# ============================


def wait_for(
    predicate: Callable[[], bool], timeout: float = 10.0
) -> bool:
    """Опрос условия до истечения таймаута (стор пишет поток event-loop хаба)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


FAKE_SERVER_SRC = '''"""Фейковый LSP-сервер для тестов stора: stdio, Content-Length фрейминг.

На initialize отвечает пустыми capabilities; на каждый textDocument/didOpen
публикует фиксированный набор диагностик для этого uri (для uri с ``_empty``
в имени — пустой список, «файл чист»).
"""
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.partition(b':')
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get(b'content-length', b'0'))
    if not length:
        return None
    return json.loads(sys.stdin.buffer.read(length))


def send(message):
    data = json.dumps(message).encode('utf-8')
    sys.stdout.buffer.write(
        b'Content-Length: ' + str(len(data)).encode('ascii') + b'\\r\\n\\r\\n' + data
    )
    sys.stdout.buffer.flush()


def main():
    while True:
        msg = read_message()
        if msg is None:
            return
        method = msg.get('method')
        if method == 'initialize':
            send({'jsonrpc': '2.0', 'id': msg['id'],
                  'result': {'capabilities': {}}})
        elif method == 'textDocument/didOpen':
            uri = msg['params']['textDocument']['uri']
            if '_empty' in uri:
                diags = []
            else:
                diags = [
                    {'range': {'start': {'line': 4, 'character': 3},
                               'end': {'line': 4, 'character': 8}},
                     'message': '«x» не определено', 'severity': 1, 'code': 'E1',
                     'source': 'fakeserver'},
                    {'range': {'start': {'line': 0, 'character': 0},
                               'end': {'line': 0, 'character': 1}},
                     'message': 'лишнее сообщение', 'severity': 4},
                ]
            send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics',
                  'params': {'uri': uri, 'diagnostics': diags}})
        elif method == 'shutdown':
            send({'jsonrpc': '2.0', 'id': msg['id'], 'result': None})


main()
'''


@pytest.fixture
def fake_server_script(tmp_path: Path) -> Path:
    script = tmp_path / 'fake_lsp_server.py'
    script.write_text(FAKE_SERVER_SRC, encoding='utf-8')
    return script


def fake_registry_json(script: Path) -> str:
    """Реестр: только python, единственный сервер — ``sys.executable <script>``."""
    return json.dumps({
        'python': {
            'extensions': ['.py'],
            'servers': [{
                'name': 'fake',
                'command': [sys.executable, str(script)],
                'enabled': True,
            }],
        },
    })


def client_open(ws: ClientConnection, uri: str, text: str) -> None:
    """Хендшейк клиента + didOpen (то, что делает JS-сторона QtMonaco)."""
    ws.send(json.dumps({
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'capabilities': {}},
    }))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        msg = json.loads(ws.recv(timeout=20))
        if msg.get('id') == 1:
            break
    else:
        pytest.fail('initialize-ответ не пришёл')
    ws.send(json.dumps({
        'jsonrpc': '2.0', 'method': 'textDocument/didOpen',
        'params': {'textDocument': {
            'uri': uri, 'languageId': 'python', 'version': 1, 'text': text,
        }},
    }))


@pytest.fixture
def fake_hub(hub: LspHub, fake_server_script: Path) -> LspHub:
    """Живой хаб с фейковым сервером в реестре (поднят до первого подключения)."""
    hub.registry_apply_json(fake_registry_json(fake_server_script))
    hub.ensure_started()
    return hub


@pytest.mark.live
def test_live_publish_flows_into_store(fake_hub: LspHub, tmp_path: Path) -> None:
    """PublishDiagnostics от сервера -> стор хаба (1-based, owner, сортировка)."""
    path_a = tmp_path / 'a.py'
    path_a.write_text('print(1)\n', encoding='utf-8')
    url = next(
        h for h in fake_hub.ws_hosts_for(path_a.as_uri()) if h.endswith('/fake')
    )
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        client_open(ws, path_a.as_uri(), 'print(1)\n')
        # первая публикация (подключение уже подняло процесс и сделал initialize)
        ok = wait_for(
            lambda: path_a.as_uri() in fake_hub.diagnostics_snapshot(),
        )
        assert ok, 'diag-стор не пополнился'
        got = fake_hub.diagnostics_snapshot()[path_a.as_uri()]
        assert [(p.line, p.col) for p in got] == [(1, 1), (5, 4)]  # 0-based -> 1-based
        assert {p.owner for p in got} == {'fake'}
        assert {p.severity for p in got} == {
            prob.SEVERITY_ERROR, prob.SEVERITY_HINT,
        }
        assert got[1].code == 'E1'
        assert got[1].source == 'fakeserver'

        # второй файл с «пустой» публикацией — снимает вклад (ключа нет)
        path_b = tmp_path / '_empty_b.py'
        path_b.write_text('x = 1\n', encoding='utf-8')
        client_open(ws, path_b.as_uri(), 'x = 1\n')
        assert wait_for(
            lambda: path_a.as_uri() in fake_hub.diagnostics_snapshot()
            and path_b.as_uri() not in fake_hub.diagnostics_snapshot(),
        )


@pytest.mark.live
def test_live_shutdown_of_hub_purges_store(
    fake_hub: LspHub, tmp_path: Path
) -> None:
    """Остановка хаба гасит процессы серверов: стор пустеет, подписчик уведомлён."""
    path = tmp_path / 'a.py'
    path.write_text('print(1)\n', encoding='utf-8')
    url = next(
        h for h in fake_hub.ws_hosts_for(path.as_uri()) if h.endswith('/fake')
    )
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        client_open(ws, path.as_uri(), 'print(1)\n')
        assert wait_for(lambda: path.as_uri() in fake_hub.diagnostics_snapshot())

    called: list[int] = []
    fake_hub.add_diagnostics_observer(lambda: called.append(1))
    fake_hub.shutdown()
    assert fake_hub.diagnostics_snapshot() == {}
    assert called == [1]


needs_bp = pytest.mark.skipif(
    shutil.which('basedpyright-langserver') is None,
    reason='нужен basedpyright-langserver в PATH',
)


@needs_bp
@pytest.mark.live
def test_live_basedpyright_diagnostics_in_store(hub: LspHub, tmp_path: Path) -> None:
    """Диагностики от basedpyright проходят в python-стор хаба через ws."""
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'pyproject.toml').write_text(
        '[project]\nname = "prob"\n', encoding='utf-8',
    )
    bad = project / 'bad.py'
    bad.write_text('lst = []\nlst.apend(1)\n', encoding='utf-8')
    hub.ensure_started()
    url = next(
        h for h in hub.ws_hosts_for(bad.as_uri())
        if h.endswith('/basedpyright')
    )
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        client_open(ws, bad.as_uri(), bad.read_text(encoding='utf-8'))
        ok = wait_for(
            lambda: any(
                p.severity == 1
                for p in hub.diagnostics_snapshot().get(bad.as_uri(), ())
            ),
            timeout=90,
        )
        assert ok, f'basedpyright не опубликовал ошибку: {hub.diagnostics_snapshot()}'
    got = hub.diagnostics_snapshot()[bad.as_uri()]
    error = next(p for p in got if p.severity == 1)
    # 'lst.apend(1)' — 2-я строка; basedpyright ругается на неизвестный атрибут
    assert error.line == 2
    assert 'apend' in error.message
    assert error.code == 'reportAttributeAccessIssue'
    assert error.source == 'basedpyright'
    assert error.owner == 'basedpyright'
