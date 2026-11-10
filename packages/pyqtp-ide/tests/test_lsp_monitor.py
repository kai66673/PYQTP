"""Этап 10.3 — «Монитор LSP»: кольцо трафика хаба и панель с фильтрами.

Юниты без GUI и без процессов (дефолтный прогон) — чистый модуль
``pyqtp/controls/lsp_control/lsp_monitor.py`` (кольцо, сводки, фабрики
событий, фильтр) и точки записи монитора в ``LspHub``: API кольца
(наблюдатели, очистка, жизнь после ``shutdown``) и ``_ServerInstance`` с
фейковыми процессом/сокетом — маршрутизация клиента, ответы/запросы/нотифи-
кации сервера, смерть процесса и локальные запросы хаба.

Offscreen-GUI (без WebEngine, харнес переиспользуется импортом из
``test_problems_panel.py``) — панель ``LspMonitorView``: регистрация,
снимок кольца через queued-мост контрола (коалесцируемый таймером), фильтры
(текст/сервер/направление/тип/нотификации), «Пауза», «Очистить», console-API.

Живые тесты (маркер ``live``) — реальный трафик через ws-хаб с детермини-рованным фейковым LSP-сервером (харнес ``test_lsp_hub_store.py``): строки
на обоих направлениях, сводка didOpen без текста документа, кольцо пережи-
вает остановку хаба.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from websockets.sync.client import connect

# переиспользуем live-харнес этапа 10.1: фейковый LSP-сервер в реестре хаба
from test_lsp_hub_store import (
    client_open,
    fake_registry_json,
    fake_server_script,
    wait_for,
)
# переиспользуем offscreen-харнес панели «Проблемы» (окно IDE с фейк-доками)
from test_problems_panel import (
    fake_docs,
    org_app,
    wnd,
)

from pyqtp.controls.lsp_control import lsp_monitor as mon
from pyqtp.controls.lsp_control.lsp_hub import (
    DiagHooks,
    LspHub,
    _ClientConnection,
    _ServerInstance,
)
from pyqtp.controls.lsp_control.lsp_registry import ServerSpec

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytestqt.qtbot import QtBot

    from pyqtp.main_wnd.ide_main_window import IdeMainWindow
    from pyqtp.views.lsp_monitor_view import LspMonitorView


# ============================
# Чистый модуль lsp_monitor   #
# ============================


def test_ring_sequential_ids_and_clear_keeps_numbering() -> None:
    ring = mon.TrafficRing()
    ev = mon.TrafficEvent('s', mon.DIRECTION_TO, mon.KIND_REQUEST, 'm', 1, '')
    first = ring.add_event(ev, ts=100.0)
    second = ring.add_event(ev, ts=101.0)
    assert (first.seq, second.seq) == (1, 2)
    assert ring.total_seen() == 2
    assert [r.ts for r in ring.records()] == [100.0, 101.0]
    ring.clear()
    assert ring.records() == ()
    # нумерация продолжается — id записей уникальны на всё время жизни хаба
    assert ring.add_event(ev, ts=102.0).seq == 3
    assert ring.total_seen() == 3


def test_ring_capacity_evicts_oldest() -> None:
    ring = mon.TrafficRing(capacity=3)
    ev = mon.TrafficEvent('s', mon.DIRECTION_TO, mon.KIND_REQUEST, 'm', None, '')
    for i in range(5):
        ring.add_event(ev, ts=float(i))
    assert [r.seq for r in ring.records()] == [3, 4, 5]
    assert ring.total_seen() == 5


def test_ring_is_thread_safe() -> None:
    """add_event пишет из потока event-loop хаба — без гонок на кольце."""
    ring = mon.TrafficRing(capacity=100)
    ev = mon.TrafficEvent('s', mon.DIRECTION_TO, mon.KIND_REQUEST, 'm', None, '')

    def spin() -> None:
        for _ in range(200):
            ring.add_event(ev, ts=0.0)

    threads = [threading.Thread(target=spin) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ring.records()) == 100
    assert ring.total_seen() == 800
    # все seq уникальны (блокировка защищает счётчик)
    assert len({r.seq for r in ring.records()}) == 100


def test_summarize_params_text_heavy_and_clipping() -> None:
    # didOpen/didChange не дампим — текст документа огромен
    params = {'textDocument': {
        'uri': 'file:///a.py', 'version': 3, 'text': 'x' * 10_000,
    }}
    assert mon.summarize_params('textDocument/didOpen', params) == (
        'file:///a.py v3'
    )
    # без version — только uri
    assert mon.summarize_params('textDocument/didClose', {
        'textDocument': {'uri': 'file:///a.py'},
    }) == json.dumps({'textDocument': {'uri': 'file:///a.py'}}, ensure_ascii=False)
    # обычные params — компактный json, длинные — с многоточием
    assert mon.summarize_params('textDocument/hover', {
        'textDocument': {'uri': 'file:///a.py'}, 'position': {'line': 1, 'character': 2},
    }) == json.dumps(
        {'textDocument': {'uri': 'file:///a.py'},
         'position': {'line': 1, 'character': 2}},
        ensure_ascii=False,
    )
    long_summary = mon.summarize_params('x/y', {'q': 'ю' * 500})
    assert len(long_summary) == mon.SUMMARY_MAX_CHARS
    assert long_summary.endswith('…')
    assert 'ююю' in long_summary
    assert mon.summarize_params('m', None) == ''


def test_summarize_error_and_result() -> None:
    assert mon.summarize_error(
        {'code': -32601, 'message': 'unsupported'},
    ) == '-32601 unsupported'
    assert mon.summarize_error('сбой') == 'сбой'
    assert mon.summarize_result(None) == ''
    assert mon.summarize_result([1, 2, 3]) == 'массив: 3 эл.'
    assert mon.summarize_result({'capabilities': {}, 'serverInfo': {}}) == (
        'объект: {capabilities, serverInfo}'
    )
    assert mon.summarize_result(42) == '42'


def test_event_factories_directions_and_kinds() -> None:
    req = mon.ev_request('fake', 'c1:2', 'textDocument/hover', {'position': {}},
                         from_hub=True)
    assert (req.direction, req.kind, req.server, req.msg_id) == (
        mon.DIRECTION_TO, mon.KIND_HUB_REQUEST, 'fake', 'c1:2',
    )
    plain = mon.ev_request('fake', 'c1:2', 'textDocument/hover', None)
    assert plain.kind == mon.KIND_REQUEST
    assert plain.summary == ''

    notif = mon.ev_notification_to('fake', 'textDocument/didOpen', {
        'textDocument': {'uri': 'file:///a.py', 'version': 1, 'text': 'x'},
    })
    assert (notif.direction, notif.kind, notif.msg_id) == (
        mon.DIRECTION_TO, mon.KIND_NOTIFICATION, None,
    )
    assert notif.summary == 'file:///a.py v1'

    srv_req = mon.ev_from_server({
        'jsonrpc': '2.0', 'id': 7, 'method': 'workspace/configuration',
        'params': {'items': [{}]},
    })
    assert (srv_req.direction, srv_req.kind, srv_req.method) == (
        mon.DIRECTION_FROM, mon.KIND_SERVER_REQUEST, 'workspace/configuration',
    )
    srv_notif = mon.ev_from_server({
        'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics',
        'params': {'uri': 'file:///a.py', 'diagnostics': []},
    })
    assert srv_notif.kind == mon.KIND_SERVER_NOTIFICATION

    # ответы подписывает вызывающий (в ответе метода нет)
    resp = mon.ev_from_server({'jsonrpc': '2.0', 'id': 1, 'result': [1]},
                              method='textDocument/references')
    assert (resp.kind, resp.method, resp.summary) == (
        mon.KIND_RESPONSE, 'textDocument/references', 'массив: 1 эл.',
    )
    err = mon.ev_from_server({
        'jsonrpc': '2.0', 'id': 1, 'error': {'code': -1, 'message': 'бум'},
    }, method='textDocument/completion')
    assert (err.kind, err.summary) == (mon.KIND_ERROR, '-1 бум')

    to_client = mon.ev_hub_reply_to_client(
        'fake', 'initialize', 1, 'объект: {capabilities}',
    )
    assert (to_client.direction, to_client.kind) == (
        mon.DIRECTION_HUB, mon.KIND_HUB_REPLY,
    )
    to_server = mon.ev_hub_reply_to_server(
        'fake', 'workspace/configuration', 7, 'массив: 1 эл.',
    )
    # ответ на запрос сервера уходит процессу — это проводной трафик
    assert to_server.direction == mon.DIRECTION_TO
    assert to_server.kind == mon.KIND_HUB_REPLY

    hub_err = mon.ev_hub_error(
        'fake', 'c1:9', {'code': -32000, 'message': 'dead'}, method='textDocument/hover',
    )
    assert (hub_err.direction, hub_err.kind, hub_err.summary) == (
        mon.DIRECTION_HUB, mon.KIND_ERROR, '-32000 dead',
    )
    cli_req = mon.ev_client_request('fake', 'shutdown', 3, None)
    assert (cli_req.direction, cli_req.kind) == (
        mon.DIRECTION_HUB, mon.KIND_CLIENT_REQUEST,
    )


def _rec(seq: int, server: str, kind: str, method: str, summary: str = '',
         msg_id: int | str | None = None) -> mon.TrafficRecord:
    direction = (mon.DIRECTION_FROM if '←' in kind
                 else mon.DIRECTION_HUB if kind in (
                     mon.KIND_CLIENT_REQUEST, mon.KIND_HUB_REPLY)
                 else mon.DIRECTION_TO)
    return mon.TrafficRecord(
        seq=seq, ts=1000.0 + seq, server=server, direction=direction,
        kind=kind, method=method, msg_id=msg_id, summary=summary,
    )


def test_filter_records_default_hides_notifications() -> None:
    records = (
        _rec(1, 'fake', mon.KIND_REQUEST, 'textDocument/hover', msg_id='c1:1'),
        _rec(2, 'fake', mon.KIND_NOTIFICATION, 'textDocument/didChange'),
        _rec(3, 'fake', mon.KIND_SERVER_NOTIFICATION,
             'textDocument/publishDiagnostics', 'file:///a.py'),
        _rec(4, 'ruff', mon.KIND_RESPONSE, 'textDocument/completion',
             'массив: 5 эл.', 'c1:2'),
    )
    default = mon.filter_records(records, mon.MonitorFilter())
    assert [r.seq for r in default] == [1, 4]
    both = mon.filter_records(records, mon.MonitorFilter(show_notifications=True))
    assert [r.seq for r in both] == [1, 2, 3, 4]

    by_server = mon.filter_records(records, mon.MonitorFilter(server='ruff'))
    assert [r.seq for r in by_server] == [4]
    by_direction = mon.filter_records(
        records, mon.MonitorFilter(direction=mon.DIRECTION_HUB),
    )
    assert by_direction == ()
    by_kind = mon.filter_records(
        records, mon.MonitorFilter(kind=mon.KIND_SERVER_NOTIFICATION),
    )
    assert [r.seq for r in by_kind] == [3]
    # текст ищет по методу+деталям+серверу+id, регистронезависимо
    assert [r.seq for r in mon.filter_records(
        records, mon.MonitorFilter(text='completion', show_notifications=True),
    )] == [4]
    assert [r.seq for r in mon.filter_records(
        records, mon.MonitorFilter(text='C1:1'),
    )] == [1]
    assert mon.filter_records((), mon.MonitorFilter(text='что-то')) == ()


def test_format_time_shape() -> None:
    ts = time.time()
    text = mon.format_time(ts)
    assert len(text) == 12 and text[2] == ':' and text[-4] == '.'


# ================================
# Кольцо монитора внутри LspHub   #
# ================================


@pytest.fixture
def hub() -> Iterator[LspHub]:
    """LspHub без главного окна (кольцо живёт без жизненного цикла окна)."""
    instance = LspHub()
    yield instance
    instance.shutdown()


def test_hub_monitor_api_record_clear_observers(hub: LspHub) -> None:
    """_record_traffic пишет в кольцо и зовёт наблюдателя; clear снимает."""
    calls: list[int] = []

    def cb() -> None:
        calls.append(1)

    hub.add_monitor_observer(cb)
    hub.add_monitor_observer(cb)  # дубликат не добавляем
    hub._record_traffic(mon.ev_request('s', 1, 'm', None))
    hub._record_traffic(mon.ev_notification_to('s', 'nt', None))
    assert len(calls) == 2
    records = hub.monitor_records()
    assert [(r.server, r.method, r.kind) for r in records] == [
        ('s', 'm', mon.KIND_REQUEST), ('s', 'nt', mon.KIND_NOTIFICATION),
    ]
    assert records[0].seq == 1 and records[1].seq == 2
    assert hub.monitor_total_seen() == 2

    hub.monitor_clear()
    assert hub.monitor_records() == ()
    assert len(calls) == 3  # очистка тоже оповещает (панель чистим следом)
    # нумерация продолжается
    hub._record_traffic(mon.ev_request('s', 2, 'm2', None))
    assert hub.monitor_records()[0].seq == 3
    assert hub.monitor_total_seen() == 3

    hub.remove_monitor_observer(cb)
    hub.remove_monitor_observer(cb)  # повтор — тихо
    hub._record_traffic(mon.ev_request('s', 3, 'm3', None))
    assert len(calls) == 4  # 3-й наблюдатель не сработал (2 записи + clear + m2)


def test_hub_monitor_ring_survives_shutdown(hub: LspHub) -> None:
    """Историю разбираемой сессии удобно смотреть после останова хаба."""
    hub.ensure_started()
    hub._record_traffic(mon.ev_request('s', 1, 'initialize', None))
    hub.shutdown()
    assert hub.monitor_records() != ()
    assert hub.monitor_total_seen() >= 1


# ==================================================
# Точки записи монитора в _ServerInstance (без процессов)
# ==================================================


class _FakeProc:
    """Фейк StdioLspProcess: сообщения на «провод» только пишутся в лог."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.cwd = Path('/')

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)

    async def stop(self) -> None:
        return None


class _FakeWs:
    """Фейк ServerConnection: снятые редактором сообщения в лог."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _make_inst() -> tuple[LspHub, _ServerInstance, list[mon.TrafficEvent]]:
    """Инстанс с фейковым процессом + хаб-хозяин; хук traffic пишет в список.

    Настоящий StdioLspProcess в конструкторе не поднимается (процесс
    стартует только start()), но send у фейка прозрачнее — виден весь
    ушедший на провод payload. Маршрутизация клиента (_route_from_client/
    _detach) живёт на хабе — он отдаётся вместе с инстансом.
    """
    hub = LspHub()
    events: list[mon.TrafficEvent] = []

    def sink(ev: mon.TrafficEvent) -> None:
        events.append(ev)
        hub._record_traffic(ev)  # noqa: SLF001

    spec = ServerSpec(name='fake', command=('fake-lsp',), language='python')
    inst = _ServerInstance(
        ('fake-lsp', 'file:///'), spec, 'file:///', ['fake-lsp'],
        diag=DiagHooks(
            publish=lambda *a: None,
            purge=lambda _name: None,
            state_gone=lambda _key: None,
            traffic=sink,
        ),
    )
    inst.proc = _FakeProc()  # type: ignore[assignment]
    return hub, inst, events


def _conn(inst: _ServerInstance) -> _ClientConnection:
    conn = _ClientConnection(ws=_FakeWs(), seq=1)  # type: ignore[arg-type]
    inst.clients.append(conn)
    return conn


def _fake(inst: _ServerInstance) -> _FakeProc:
    """Фейковый процесс инстанса (атрибут объявлен настоящим StdioLspProcess)."""
    proc = inst.proc
    assert isinstance(proc, _FakeProc)
    return proc


def _fake_ws(conn: _ClientConnection) -> _FakeWs:
    """Фейковый сокет подключения (атрибут объявлен настоящим ServerConnection)."""
    ws = conn.ws
    assert isinstance(ws, _FakeWs)
    return ws


def test_hub_live_server_names() -> None:
    """Комбик серверов: имена живых инстансов (умирающие — мимо)."""
    hub, inst, _events = _make_inst()
    hub._instances[inst.key] = inst
    try:
        assert hub.live_server_names() == ['fake']
        inst.dying = True
        assert hub.live_server_names() == []
    finally:
        hub._instances.clear()
        hub.shutdown()


def test_route_initialize_shutdown_hub_side() -> None:
    """initialize/shutdown клиента: пары «запрос клиента + ответ хаба» (↔), на
    провод они не уходили."""
    hub, inst, events = _make_inst()
    conn = _conn(inst)
    inst.init_result = {'capabilities': {}}

    async def run() -> None:
        await hub._route_from_client(  # noqa: SLF001
            inst, conn, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                         'params': {'capabilities': {}}})
        await hub._route_from_client(  # noqa: SLF001
            inst, conn, {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'})

    try:
        asyncio.run(run())
    finally:
        hub.shutdown()
    assert [(e.direction, e.kind, e.method) for e in events] == [
        (mon.DIRECTION_HUB, mon.KIND_CLIENT_REQUEST, 'initialize'),
        (mon.DIRECTION_HUB, mon.KIND_HUB_REPLY, 'initialize'),
        (mon.DIRECTION_HUB, mon.KIND_CLIENT_REQUEST, 'shutdown'),
        (mon.DIRECTION_HUB, mon.KIND_HUB_REPLY, 'shutdown'),
    ]
    # процесс в это время не видел ничего
    assert _fake(inst).sent == []
    # редактору ушли ответы с исходными id
    ids = [json.loads(m)['id'] for m in _fake_ws(conn).sent]
    assert ids == [1, 2]


def test_route_request_pending_id_and_forwarded_notif() -> None:
    """Пересылаемый запрос: строка с переписанным id на проводе; didOpen —
    нотификация; проглоченные (initialized) — не пишутся."""
    hub, inst, events = _make_inst()
    conn = _conn(inst)
    try:
        async def run() -> None:
            await hub._route_from_client(  # noqa: SLF001
                inst, conn, {'jsonrpc': '2.0', 'id': 5,
                             'method': 'textDocument/hover',
                             'params': {'position': {}}})
            await hub._route_from_client(  # noqa: SLF001
                inst, conn, {'jsonrpc': '2.0', 'method': 'textDocument/didOpen',
                             'params': {'textDocument': {
                                 'uri': 'file:///a.py', 'version': 1,
                                 'text': 'x = 1'}}})
            await hub._route_from_client(  # noqa: SLF001
                inst, conn, {'jsonrpc': '2.0', 'method': 'initialized',
                             'params': {}})
            await hub._route_from_client(  # noqa: SLF001
                inst, conn, {'jsonrpc': '2.0', 'method': '$/cancelRequest',
                             'params': {'id': 5}})

        asyncio.run(run())
        kinds = [(e.direction, e.kind, e.method, e.msg_id) for e in events]
        assert kinds == [
            # id подписан уже переписанным значением — как он и лёг на провод
            (mon.DIRECTION_TO, mon.KIND_REQUEST, 'textDocument/hover', 'c1:5'),
            (mon.DIRECTION_TO, mon.KIND_NOTIFICATION, 'textDocument/didOpen',
             None),
            # initialized проглочен хабом — сервер его не получил, строки нет
            (mon.DIRECTION_TO, mon.KIND_NOTIFICATION, '$/cancelRequest', None),
        ]
        assert events[0].summary == '{"position": {}}'
        assert events[1].summary == 'file:///a.py v1'  # без дампа текста
        assert inst.pending['c1:5'] == (conn, 5, 'textDocument/hover')

        async def run2() -> None:
            # ответ сервера — ← c подписью метода из pending, id клиенту возвращён
            await inst._handle_server_response(  # noqa: SLF001
                {'jsonrpc': '2.0', 'id': 'c1:5', 'result': {'contents': []}})

        asyncio.run(run2())
        back = events[-1]
        assert (back.direction, back.kind, back.method, back.msg_id) == (
            mon.DIRECTION_FROM, mon.KIND_RESPONSE, 'textDocument/hover', 'c1:5',
        )
        assert back.summary == 'объект: {contents}'
        assert json.loads(_fake_ws(conn).sent[-1])['id'] == 5
    finally:
        hub.shutdown()


def test_server_side_notification_request_response_dead() -> None:
    """Нотификация сервера (←), запрос сервера + ответ хаба (← и →),
    смерть процесса — ошибка хаба на каждый висящий запрос."""
    hub, inst, events = _make_inst()
    conn = _conn(inst)
    inst.pending['c1:9'] = (conn, 9, 'textDocument/completion')

    try:
        async def run() -> None:
            await inst._handle_server_notification({  # noqa: SLF001
                'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics',
                'params': {'uri': 'file:///a.py', 'diagnostics': []}})
            await inst._handle_server_notification({  # noqa: SLF001
                'jsonrpc': '2.0', 'method': 'window/logMessage',
                'params': {'type': 1, 'message': 'привет'}})
            await inst._handle_server_request({  # noqa: SLF001
                'jsonrpc': '2.0', 'id': 77, 'method': 'workspace/configuration',
                'params': {'items': [{}]}})

        asyncio.run(run())
        kinds = [(e.direction, e.kind, e.method) for e in events]
        assert kinds == [
            (mon.DIRECTION_FROM, mon.KIND_SERVER_NOTIFICATION,
             'textDocument/publishDiagnostics'),
            # проглоченные хабом нотификации сервера процесс РЕАЛЬНО прислал —
            # строка есть, просто она не ушла редактору
            (mon.DIRECTION_FROM, mon.KIND_SERVER_NOTIFICATION,
             'window/logMessage'),
            (mon.DIRECTION_FROM, mon.KIND_SERVER_REQUEST,
             'workspace/configuration'),
            # ответ хаба на запрос сервера уходит процессу на провод
            (mon.DIRECTION_TO, mon.KIND_HUB_REPLY, 'workspace/configuration'),
        ]
        assert events[2].summary == '{"items": [{}]}'
        assert events[3].summary == 'массив: 1 эл.'
        assert _fake(inst).sent[-1] == {
            'jsonrpc': '2.0', 'id': 77, 'result': [None],
        }

        async def run_dead() -> None:
            await inst._on_process_dead()  # noqa: SLF001

        asyncio.run(run_dead())
        err = events[-1]
        assert (err.direction, err.kind, err.method) == (
            mon.DIRECTION_HUB, mon.KIND_ERROR, 'textDocument/completion',
        )
        assert '-32000' in err.summary and 'terminated' in err.summary
        assert inst.pending == {}
        assert json.loads(_fake_ws(conn).sent[-1])['id'] == 9
    finally:
        hub.shutdown()


def test_request_local_hub_pair() -> None:
    """Локальный запрос хаба: «→ запрос хаба» при отправке, «← ответ хаба»
    по приходу ответа (в _handle_server_response локальные id не пишутся)."""
    hub, inst, events = _make_inst()
    try:
        async def run() -> dict:
            task = asyncio.create_task(
                inst.request_local('workspace/symbol', {'query': 'x'}),  # noqa: SLF001
            )
            while not _fake(inst).sent:
                await asyncio.sleep(0.01)
            mid = _fake(inst).sent[-1]['id']
            await inst._handle_server_response(  # noqa: SLF001
                {'jsonrpc': '2.0', 'id': mid, 'result': []})
            return await task

        response = asyncio.run(run())
        assert 'result' in response
        pairs = [(e.direction, e.kind, e.method) for e in events]
        assert pairs == [
            (mon.DIRECTION_TO, mon.KIND_HUB_REQUEST, 'workspace/symbol'),
            (mon.DIRECTION_FROM, mon.KIND_HUB_REPLY, 'workspace/symbol'),
        ]
        assert events[0].summary == '{"query": "x"}'
        assert events[1].summary == 'массив: 0 эл.'
        assert events[0].msg_id == events[1].msg_id == 'hub1'
    finally:
        hub.shutdown()


def test_detach_records_forced_didclose() -> None:
    """Отстыковка редактора: принудительные didClose видны на проводе."""
    hub, inst, events = _make_inst()
    try:
        conn = _conn(inst)
        conn.opened_uris.add('file:///a.py')

        async def run() -> None:
            await hub._detach(inst, conn)  # noqa: SLF001

        asyncio.run(run())
        closes = [
            e for e in events
            if e.method == 'textDocument/didClose'
            and e.direction == mon.DIRECTION_TO
        ]
        assert len(closes) == 1
        assert closes[0].server == 'fake'
        assert 'file:///a.py' in closes[0].summary
        assert conn not in inst.clients
    finally:
        hub.shutdown()


# ==========================
# Панель монитора (offscreen GUI)
# ==========================


def monitor_view(wnd: IdeMainWindow) -> LspMonitorView:
    view = wnd.lsp_monitor_ctrl.monitor_view
    assert view is not None
    return view


def traffic(wnd: IdeMainWindow, *events: mon.TrafficEvent) -> None:
    """Публикация строк в кольцо (путь потокового вызова из event-loop)."""
    for ev in events:
        wnd.lsp_hub._record_traffic(ev)  # noqa: SLF001


def rows(view: LspMonitorView) -> list[list[str]]:
    """Снимок дерева панели: списки текстов по всем колонкам."""
    tree = view._tree  # noqa: SLF001
    width = tree.columnCount()
    return [
        [tree.topLevelItem(i).text(c) for c in range(width)]
        for i in range(tree.topLevelItemCount())
    ]


def request_row(server: str = 'fake') -> mon.TrafficEvent:
    return mon.ev_request(server, 'c1:1', 'textDocument/hover',
                          {'position': {'line': 1, 'character': 2}})


def did_open_row(server: str = 'fake') -> mon.TrafficEvent:
    return mon.ev_notification_to(server, 'textDocument/didOpen', {
        'textDocument': {'uri': 'file:///a.py', 'version': 1, 'text': 'x = 1'},
    })


def test_monitor_view_registered(wnd: IdeMainWindow) -> None:
    """Контрол зарегистрирован и поставил панель в нижнюю область."""
    view = monitor_view(wnd)
    assert view.key == 'LspMonitorControl_monitor_view'
    assert wnd.view_get('LspMonitorControl_monitor_view') is view


def test_monitor_panel_empty_start(wnd: IdeMainWindow) -> None:
    """Стартовый снимок пуст — пустое дерево и нулевой итог."""
    view = monitor_view(wnd)
    assert rows(view) == []
    assert view._status_label.text().startswith('Записей: 0')  # noqa: SLF001


def test_monitor_panel_traffic_and_tree(
    wnd: IdeMainWindow, qtbot: QtBot
) -> None:
    """Кольцо -> queued-мост -> коалесцированный сброс контрола в дерево."""
    traffic(wnd, request_row(), did_open_row('ruff'))

    view = monitor_view(wnd)

    def ready() -> bool:
        # нотификации по умолчанию скрыты — в дереве только запрос
        return len(rows(view)) == 1

    qtbot.waitUntil(ready, timeout=5000)
    row = rows(view)[0]
    # Время, Сервер, Направление, Тип, Метод, Id, Детали
    assert row[1] == 'fake'
    assert row[2] == mon.DIRECTION_TO
    assert row[3] == mon.KIND_REQUEST
    assert row[4] == 'textDocument/hover'
    assert row[5] == 'c1:1'
    assert '"line": 1' in row[6]
    assert view._status_label.text() == 'Записей: 1 из 2'  # noqa: SLF001
    # комбик серверов пополнился именами из записей
    assert view._server_combo.findData('ruff') >= 0  # noqa: SLF001


def test_monitor_panel_filters(
    wnd: IdeMainWindow, qtbot: QtBot
) -> None:
    """Фильтры панели: нотификации, текст, сервер, направление, тип."""
    traffic(
        wnd,
        mon.ev_request('fake', 'c1:1', 'textDocument/hover', None),
        did_open_row('fake'),
        mon.ev_from_server({
            'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics',
            'params': {'uri': 'file:///a.py', 'diagnostics': []}},
        ),
        mon.ev_request('ruff', 'c1:2', 'textDocument/completion', None),
    )
    view = monitor_view(wnd)
    qtbot.waitUntil(lambda: len(rows(view)) == 2, timeout=5000)

    # нотификации (включая серверные) — чекбокс
    view._notifications_check.setChecked(True)  # noqa: SLF001
    assert len(rows(view)) == 4
    view._notifications_check.setChecked(False)  # noqa: SLF001
    assert len(rows(view)) == 2

    # текстовый фильтр ищет по методу/деталям/id
    view._filter_edit.setText('completion')  # noqa: SLF001
    assert [r[4] for r in rows(view)] == ['textDocument/completion']
    view._filter_edit.setText('')  # noqa: SLF001

    # сервер/направление/тип — комбиками (itemData)
    view._server_combo.setCurrentIndex(  # noqa: SLF001
        view._server_combo.findData('fake'))
    assert [r[4] for r in rows(view)] == ['textDocument/hover']
    view._server_combo.setCurrentIndex(0)  # noqa: SLF001

    view._kind_combo.setCurrentIndex(  # noqa: SLF001
        view._kind_combo.findData(mon.KIND_SERVER_NOTIFICATION))
    assert [r[4] for r in rows(view)] == ['textDocument/publishDiagnostics']
    view._kind_combo.setCurrentIndex(0)  # noqa: SLF001

    view._direction_combo.setCurrentIndex(  # noqa: SLF001
        view._direction_combo.findData(mon.DIRECTION_HUB))
    assert rows(view) == []
    view._direction_combo.setCurrentIndex(0)  # noqa: SLF001
    assert len(rows(view)) == 2


def test_monitor_panel_pause(wnd: IdeMainWindow, qtbot: QtBot) -> None:
    """«Пауза»: кольцо пишется, дерево заморожено; снятие — досвежить."""
    traffic(wnd, request_row())
    view = monitor_view(wnd)
    qtbot.waitUntil(lambda: len(rows(view)) == 1, timeout=5000)

    view._pause_button.click()  # noqa: SLF001
    assert view._pause_button.isChecked()
    traffic(wnd, request_row('ruff'))
    # коалесцированный сброс контрола дойдёт, но дерево не шевельнётся
    qtbot.waitUntil(
        lambda: len(view._records) == 2, timeout=5000,  # noqa: SLF001
    )
    assert len(rows(view)) == 1

    view._pause_button.click()  # noqa: SLF001
    assert len(rows(view)) == 2


def test_monitor_panel_clear_button(wnd: IdeMainWindow, qtbot: QtBot) -> None:
    """«Очистить»: локальная картинка сразу, кольцо снимает хаб (он владелец)."""
    traffic(wnd, request_row())
    view = monitor_view(wnd)
    qtbot.waitUntil(lambda: len(rows(view)) == 1, timeout=5000)

    view._clear_button.click()  # noqa: SLF001
    assert rows(view) == []
    assert wnd.lsp_hub.monitor_records() == ()
    # нумерация кольца НЕ обнулилась (clear не сбрасывает seq)
    assert wnd.lsp_hub.monitor_total_seen() >= 1
    # и кольцо, и панель синхронны: после новой строки она снова видна
    traffic(wnd, request_row())
    qtbot.waitUntil(lambda: len(rows(view)) == 1, timeout=5000)


def test_monitor_console_api(wnd: IdeMainWindow, qtbot: QtBot) -> None:
    """Console-API: ``lsp_monitor_records`` словарями, ``lsp_monitor_open``;
    очистка из консоли чистит и панель (кольцо — единственный источник)."""
    env: dict[str, Any] = wnd.api_ctrl.env
    assert 'lsp_monitor_records' in env
    assert callable(env['lsp_monitor_open'])

    traffic(wnd, request_row('ruff'))
    snap = env['lsp_monitor_records']()
    assert len(snap) == 1
    assert snap[0]['server'] == 'ruff'
    assert snap[0]['method'] == 'textDocument/hover'
    assert snap[0]['kind'] == mon.KIND_REQUEST
    assert ':' in snap[0]['time']

    view = monitor_view(wnd)
    qtbot.waitUntil(lambda: len(rows(view)) == 1, timeout=5000)
    wnd.lsp_monitor_ctrl.monitor_clear()
    qtbot.waitUntil(lambda: len(rows(view)) == 0, timeout=5000)


# ==============================
# Живой трафик через ws-хаб     #
# ==============================


@pytest.fixture
def fake_hub(hub: LspHub, fake_server_script: Path) -> LspHub:
    """Живой хаб с фейковым сервером в реестре (поднят до подключения)."""
    hub.registry_apply_json(fake_registry_json(fake_server_script))
    hub.ensure_started()
    return hub


def _rows_of(hub: LspHub) -> list[tuple[str, str, str, str]]:
    """Снимок кольца кортежами (сервер, направление, тип, метод)."""
    return [
        (r.server, r.direction, r.kind, r.method) for r in hub.monitor_records()
    ]


@pytest.mark.live
def test_live_traffic_roundtrip_in_monitor(
    fake_hub: LspHub, tmp_path: Path
) -> None:
    """Реальный ws-раунд: handshake хаб↔редактор, провод didOpen/publish,
    сводка без текста документа."""
    path = tmp_path / 'a.py'
    path.write_text('print(x)\n', encoding='utf-8')
    url = next(
        h for h in fake_hub.ws_hosts_for(path.as_uri()) if h.endswith('/fake')
    )
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        client_open(ws, path.as_uri(), 'print(x)\n')

        def ready() -> bool:
            rows = _rows_of(fake_hub)
            return (
                ('fake', mon.DIRECTION_HUB, mon.KIND_CLIENT_REQUEST,
                 'initialize') in rows
                and ('fake', mon.DIRECTION_TO, mon.KIND_NOTIFICATION,
                     'initialized') in rows
                and ('fake', mon.DIRECTION_TO, mon.KIND_NOTIFICATION,
                     'textDocument/didOpen') in rows
                and ('fake', mon.DIRECTION_FROM, mon.KIND_SERVER_NOTIFICATION,
                     'textDocument/publishDiagnostics') in rows
            )

        assert wait_for(ready, timeout=20), f'монитор не пополнился: {_rows_of(fake_hub)}'

        by_method = {
            r.method: r for r in fake_hub.monitor_records()
        }
        # initialize клиенту отвечал сам хаб (процесс его не видел)
        init_reply = by_method['initialize']
        assert init_reply.direction == mon.DIRECTION_HUB
        assert init_reply.kind == mon.KIND_HUB_REPLY
        # init_result процесса — в сводке ответа
        assert 'capabilities' in init_reply.summary
        # провод didOpen: uri+version, без дампа текста документа
        did_open = by_method['textDocument/didOpen']
        assert did_open.summary == f'{path.as_uri()} v1'
        assert 'print' not in did_open.summary
        # входящая публикация сервера подписана и с uri в деталях
        pub = by_method['textDocument/publishDiagnostics']
        assert pub.direction == mon.DIRECTION_FROM
        assert path.as_uri() in pub.summary


@pytest.mark.live
def test_live_monitor_history_survives_disconnect_and_shutdown(
    fake_hub: LspHub, tmp_path: Path
) -> None:
    """Отключение редактора (didClose) и остановка хаба (didClose/shutdown/
    exit процессов) дописываются в кольцо; история доступна после shutdown."""
    path = tmp_path / 'a.py'
    path.write_text('print(1)\n', encoding='utf-8')
    url = next(
        h for h in fake_hub.ws_hosts_for(path.as_uri()) if h.endswith('/fake')
    )
    with connect(f'ws://{url}', open_timeout=10, close_timeout=2) as ws:
        client_open(ws, path.as_uri(), 'print(1)\n')
        assert wait_for(
            lambda: any(
                m == 'textDocument/didOpen'
                for _s, _d, _k, m in _rows_of(fake_hub)
            ),
            timeout=20,
        )
    # _detach отправил процессу принудительный didClose
    assert wait_for(
        lambda: ('fake', mon.DIRECTION_TO, mon.KIND_NOTIFICATION,
                 'textDocument/didClose') in _rows_of(fake_hub),
        timeout=10,
    )

    fake_hub.shutdown()
    rows = _rows_of(fake_hub)
    # корректное гашение процесса видно post-mortem
    assert ('fake', mon.DIRECTION_TO, mon.KIND_NOTIFICATION, 'shutdown') in rows
    assert ('fake', mon.DIRECTION_TO, mon.KIND_NOTIFICATION, 'exit') in rows
    assert fake_hub.monitor_total_seen() == len(rows)
    # и его можно разобрать из консоли после остановки (история не purge’ится)
    assert fake_hub.monitor_records()[-1].method in {'shutdown', 'exit'}
