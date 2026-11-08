"""WebSocket-хаб LSP (этап 2 дорожной карты ``docs/LSP_PLAN.md``).

Хаб крутит asyncio-сервер ``websockets`` на свободном порту 127.0.0.1 в
отдельном потоке. Каждый редактор подключается по адресу
``ws://127.0.0.1:<port>/<percent-encoded file URI>[/<server-name>]`` — uri
файла в пути позволяет хабу выбрать серверы по расширению и вычислить
``root_uri``; опциональный сегмент ``<server-name>`` выбирает конкретный
сервер стека языка (этап 3.1), без него — первый сервер реестра
(обратная совместимость с адресами этапа 2).

Схема мультиплексирования:
  - процессы серверов пула по ключу ``(command, root_uri)``;
  - ``initialize`` клиента хабу отвечает закэшированным InitializeResult
    реального хендшейка хаба с процессом;
  - id клиентских запросов переписываются в ``c{conn}:{id}``, ответы
    возвращаются адресату;
  - нотификации ``publishDiagnostics`` рассылаются всем подключениям пула
    (JS фильтрует по uri модели);
  - запросы сервера (``workspace/configuration`` и т.п.) обрабатывает сам
    хаб, в редактор они не пропускаются;
  - процесс переживает закрытие редактора (закрываются только его документы)
    и убивается при завершении IDE.

Реестр серверов настраивается JSON-строкой в QSettings (ключ
``<key>/registry_json``, формат — см. ``lsp_registry``; этап 3.2 дорожной
карты). Смена реестра применяется к новым подключениям/редакторам; уже
поднятые процессы серверов живут до ``shutdown`` IDE.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote, urlparse

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.lsp_control.lsp_process_wrapper import StdioLspProcess
from pyqtp.controls.lsp_control.lsp_registry import (
    LspRegistry,
    ServerSpec,
    resolve_command,
)
from pyqtp.controls.lsp_control.lsp_settings_page import (
    LspRegistrySettingsPage,
)

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection

    from pyqtp.base.base_main_window import BaseMainWindow

srv_log = logging.getLogger('bcad.lsp')

# Легенда семантических токенов в client capabilities. Реально наши серверы
# клиентскую легенду игнорируют: и basedpyright, и clangd кодируют токены
# собственным legend, который объявляют в своём InitializeResult (проверено
# зондами); его и использует JS-провайдер (этап 5). Набор полей ниже — гибрид
# спецификаций LSP: поля 3.17 (legend/tokenFormat) нужны по спецификации, а
# поля черновика 3.16 (tokenTypes/tokenModifiers/formats) требует ruff на
# tower-lsp — без них он роняется на initialize ('missing field tokenTypes').
STANDARD_SEMANTIC_TOKEN_TYPES = [
    'namespace', 'type', 'class', 'enum', 'interface', 'struct', 'typeParameter',
    'parameter', 'variable', 'property', 'enumMember', 'event', 'function',
    'method', 'macro', 'keyword', 'modifier', 'comment', 'string', 'number',
    'regexp', 'operator', 'decorator',
]
STANDARD_SEMANTIC_TOKEN_MODIFIERS = [
    'documentation', 'declaration', 'definition', 'static', 'deprecated',
    'abstract', 'async', 'modification', 'readonly', 'default', 'imported',
    'public', 'private', 'protected',
]

# Статические capabilities хаба (без dynamic registration), по ним процесс
# сервера получает всё нужное для диагностик/комплий/ховера.
HUB_CAPABILITIES: dict[str, Any] = {
    'textDocument': {
        'publishDiagnostics': {
            'relatedInformation': True,
            'versionSupport': False,
            'tagSupport': {'valueSet': [1, 2]},
        },
        'synchronization': {'dynamicRegistration': False, 'didSave': True},
        'completion': {
            'contextSupport': True,
            'completionItem': {
                'snippetSupport': False,
                'documentationFormat': ['markdown', 'plaintext'],
                'deprecatedSupport': True,
                'preselectSupport': True,
                'tagSupport': {'valueSet': [1]},
                'resolveSupport': {
                    'properties': [
                        'documentation', 'detail', 'additionalTextEdits'
                    ]
                },
            },
            'completionItemKind': {'valueSet': list(range(1, 26))},
        },
        'hover': {
            'dynamicRegistration': False,
            'contentFormat': ['markdown', 'plaintext'],
        },
        'signatureHelp': {
            'contextSupport': True,
            'signatureInformation': {
                'documentationFormat': ['markdown', 'plaintext'],
                'parameterInformation': {'labelOffsetSupport': True},
            },
            'activeParameterSupport': True,
        },
        'definition': {'linkSupport': True},
        'references': {},
        'documentSymbol': {'hierarchicalDocumentSymbolSupport': True},
        'documentHighlight': {},
        # Семантическая подсветка (этап 5): набор полей — гибрид 3.17 и
        # черновика 3.16 (см. комментарий у STANDARD_SEMANTIC_TOKEN_*);
        # серверы кодируют токены собственным legend из InitializeResult —
        # его же использует JS-провайдер (см. lsp.ts/providers.ts)
        'semanticTokens': {
            'dynamicRegistration': False,
            'requests': {'range': False, 'full': {'delta': False}},
            'multilineTokenSupport': False,
            'overlappingTokenSupport': False,
            'serverCancelSupport': False,
            'tokenFormat': 'relative',
            'legend': {
                'tokenTypes': STANDARD_SEMANTIC_TOKEN_TYPES,
                'tokenModifiers': STANDARD_SEMANTIC_TOKEN_MODIFIERS,
            },
            'tokenTypes': STANDARD_SEMANTIC_TOKEN_TYPES,
            'tokenModifiers': STANDARD_SEMANTIC_TOKEN_MODIFIERS,
            'formats': ['relative'],
        },
        'codeAction': {
            'dynamicRegistration': False,
            'codeActionLiteralSupport': {
                'codeActionKind': {
                    'valueSet': [
                        'quickfix', 'refactor', 'refactor.extract',
                        'refactor.inline', 'refactor.rewrite', 'source',
                        'source.organizeImports', 'source.fixAll',
                        'source.unusedImports',
                    ]
                }
            },
            'resolveSupport': {'properties': ['edit']},
            # basedpyright/generic pyright не отдаёт фиксы без roundtrip
            # diagnostic.data и без isPreferred/disabled в литеральной форме
            'dataSupport': True,
            'isPreferredSupport': True,
            'disabledSupport': True,
            'honorsChangeAnnotations': False,
        },
        # этап 8.2: переименование (F2) и форматирование — JS-клиент объявляет
        # эти возможности сам, хаб обязан транслировать их серверу так же
        'rename': {'dynamicRegistration': False, 'prepareProvider': True},
        'formatting': {'dynamicRegistration': False},
        'rangeFormatting': {'dynamicRegistration': False},
    },
    'workspace': {
        'workspaceFolders': True,
        'configuration': True,
        'didChangeConfiguration': {'dynamicRegistration': False},
        # чужие WorkspaceEdit применяем сами (doc_control/workspace_edit.py):
        # серверу отвечаем, что applyEdit у клиента нет, чтобы он не присылал
        # workspace/applyEdit и включал documentChanges/ресурсные операции
        'applyEdit': False,
    },
    'window': {'workDoneProgress': False, 'showMessage': {}},
    'general': {'positionEncodings': ['utf-16']},
}

# Нотификации сервера, которые рассылаются всем подключениям пула
BROADCAST_NOTIFICATIONS = frozenset({'textDocument/publishDiagnostics'})
# Нотификации сервера, которые хаб проглатывает (у JS-клиента нет обработчиков)
SERVER_NOTIFICATIONS_SWALLOW = frozenset({
    'window/logMessage',
    'window/showMessage',
    '$/progress',
    '$/logTrace',
    '$/language/status',
    'telemetry/event',
})
# Нотификации клиента, которые не пересылаются процессу (хендшейк делает хаб)
CLIENT_NOTIFICATIONS_SWALLOW = frozenset({'initialized', 'exit', '$/setTrace'})


@dataclass
class _ClientConnection:
    """Одно WebSocket-подключение редактора к хабу."""

    ws: ServerConnection
    seq: int
    opened_uris: set[str] = field(default_factory=set)


class _ServerInstance:
    """Экземпляр LSP-процесса и подключения редакторов к нему."""

    def __init__(
        self,
        key: tuple[str, str],
        spec: ServerSpec,
        root_uri: str,
        command: list[str],
    ) -> None:
        self.key = key
        self.spec = spec
        self.root_uri = root_uri
        self.proc = StdioLspProcess(
            command, Path(unquote(urlparse(root_uri).path))
        )
        self.init_result: dict[str, Any] = {}
        self.clients: list[_ClientConnection] = []
        # new_id -> (client, original_id): маршрутизация ответов сервера
        self.pending: dict[str, tuple[_ClientConnection, Any]] = {}
        self.dying = False
        self._local_futures: dict[str, asyncio.Future[dict]] = {}
        self._id_seq = itertools.count(1)
        self._dispatch_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.proc.start()
        self._dispatch_task = asyncio.create_task(self._dispatch())
        root_name = Path(self.proc.cwd).name or 'root'
        params = {
            'processId': os.getpid(),
            'clientInfo': {'name': 'pyqtp-lsp-hub', 'version': '1.0'},
            'rootUri': self.root_uri,
            'workspaceFolders': [{'uri': self.root_uri, 'name': root_name}],
            'capabilities': HUB_CAPABILITIES,
        }
        response = await self.request_local('initialize', params)
        if 'result' not in response:
            msg = f'LSP-сервер {self.spec.name} отверг initialize: {response!r}'
            raise RuntimeError(msg)
        self.init_result = response['result']
        await self.proc.send(
            {'jsonrpc': '2.0', 'method': 'initialized', 'params': {}}
        )
        srv_log.info(
            'LSP-сервер %s готов (root=%s)', self.spec.name, self.root_uri
        )

    async def stop(self) -> None:
        self.dying = True
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
        for uri in sorted({u for c in self.clients for u in c.opened_uris}):
            try:
                await self.proc.send({
                    'jsonrpc': '2.0',
                    'method': 'textDocument/didClose',
                    'params': {'textDocument': {'uri': uri}},
                })
            except (ConnectionResetError, BrokenPipeError):
                break
        try:
            await self.proc.send(
                {'jsonrpc': '2.0', 'method': 'shutdown', 'params': {}}
            )
            await self.proc.send({'jsonrpc': '2.0', 'method': 'exit'})
        except (ConnectionResetError, BrokenPipeError):
            pass
        await self.proc.stop()

    async def request_local(self, method: str, params: Any) -> dict:
        """Запрос хаб -> процесс; ответ ждём в futures."""
        mid = f'hub{next(self._id_seq)}'
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._local_futures[mid] = fut
        try:
            await self.proc.send(
                {'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params}
            )
            return await fut
        finally:
            self._local_futures.pop(mid, None)

    def client_request_id(self, conn: _ClientConnection, original: Any) -> str:
        return f'c{conn.seq}:{original}'

    async def _dispatch(self) -> None:
        try:
            while True:
                msg = await self.proc.out_queue.get()
                if msg is None:
                    break
                if 'method' in msg and 'id' in msg:
                    await self._handle_server_request(msg)
                elif 'method' in msg:
                    await self._handle_server_notification(msg)
                elif 'id' in msg:
                    await self._handle_server_response(msg)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            srv_log.exception('Диспетчер LSP-процесса %s упал', self.spec.name)
        await self._on_process_dead()

    async def _handle_server_notification(self, msg: dict) -> None:
        method = msg.get('method')
        if method in BROADCAST_NOTIFICATIONS:
            text = json.dumps(msg)
            params = msg.get('params') or {}
            srv_log.info(
                '%s: broadcast %s %s (%s шт.)', self.spec.name, method,
                params.get('uri'), len(params.get('diagnostics', [])),
            )
            for client in list(self.clients):
                with contextlib.suppress(ConnectionClosed):
                    await client.ws.send(text)
        elif method not in SERVER_NOTIFICATIONS_SWALLOW:
            srv_log.debug(
                '%s: проглочена нотификация %s', self.spec.name, method
            )

    async def _handle_server_request(self, msg: dict) -> None:
        """Запросы сервера обрабатывает хаб; в редактор они не идут."""
        method = msg.get('method')
        mid = msg.get('id')
        if method == 'workspace/configuration':
            items = (msg.get('params') or {}).get('items', [])
            response: dict[str, Any] = {
                'jsonrpc': '2.0', 'id': mid, 'result': [None] * len(items),
            }
        elif method in (
            'client/registerCapability', 'window/workDoneProgress/create'
        ):
            response = {'jsonrpc': '2.0', 'id': mid, 'result': None}
        elif method == 'workspace/applyEdit':
            # не должен приходить (в HUB_CAPABILITIES applyEdit: False); если
            # сервер всё же прислал — own-side применение чужих правок идёт через
            # DocumentManager.on_workspace_edit_requested, здесь отказ
            response = {
                'jsonrpc': '2.0', 'id': mid, 'result': {'applied': False}
            }
        else:
            response = {
                'jsonrpc': '2.0', 'id': mid,
                'error': {
                    'code': -32601, 'message': f'unsupported by hub: {method}'
                },
            }
        srv_log.debug('%s: хаб ответил на запрос %s', self.spec.name, method)
        await self.proc.send(response)

    async def _handle_server_response(self, msg: dict) -> None:
        mid = msg['id']
        if mid in self._local_futures:
            self._local_futures[mid].set_result(msg)
            return
        target = self.pending.pop(mid, None)
        if target is None:
            srv_log.debug('%s: безадресный ответ %s', self.spec.name, mid)
            return
        conn, original = target
        await conn.ws.send(json.dumps({**msg, 'id': original}))

    async def _on_process_dead(self) -> None:
        if self.dying:
            return
        self.dying = True
        srv_log.error(
            'LSP-процесс %s (root=%s) завершился неожиданно',
            self.spec.name, self.root_uri,
        )
        error_response = {
            'jsonrpc': '2.0',
            'error': {'code': -32000, 'message': 'LSP server process terminated'},
        }
        for mid, (conn, original) in list(self.pending.items()):
            self.pending.pop(mid, None)
            with contextlib.suppress(ConnectionClosed):
                await conn.ws.send(json.dumps({**error_response, 'id': original}))


class LspHub(BaseControl):
    """Контрол: WebSocket-хаб LSP на весь IDE."""

    _main_wnd: BaseMainWindow

    def __init__(self) -> None:
        super().__init__()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bound = threading.Event()
        self._port: int | None = None
        self._stop_event = asyncio.Event()
        self._instances: dict[tuple[str, str], _ServerInstance] = {}
        self._conn_seq = itertools.count(1)
        self._registry: LspRegistry = LspRegistry.default()

    ###############################
    # Жизненный цикл контрола #

    def settings_read(self, **kwargs: Any) -> None:
        super().settings_read(**kwargs)
        raw = cast('str', self._main_wnd.settings.value(
            f'{self.key}/registry_json', '', str
        ))
        if not raw:
            return
        try:
            self._registry = LspRegistry.from_json(raw)
        except (TypeError, ValueError) as exc:
            srv_log.error(
                'Некорректная конфигурация LSP-реестра в настройках — '
                'используются значения по умолчанию: %s', exc,
            )
            return
        summary = '; '.join(
            f'{language}: '
            + (
                ', '.join(
                    spec.name for spec in cfg.servers if spec.enabled
                )
                or '(нет включённых серверов)'
            )
            for language, cfg in self._registry.languages.items()
        ) or '(пусто)'
        srv_log.info('LSP реестр загружен из настроек: %s', summary)

    def settings_write(self) -> None:
        super().settings_write()
        # Пишем конфиг только если он отличается от дефолтного (не мусорить
        # в настройках); при сбросе на дефолт — чистим ключ.
        if self._registry.to_dict() != LspRegistry.default().to_dict():
            self._main_wnd.settings.setValue(
                f'{self.key}/registry_json', self._registry.to_json()
            )
        else:
            self._main_wnd.settings.remove(f'{self.key}/registry_json')

    def setup_relations(self) -> None:
        super().setup_relations()

        # Регистрируем журнал LSP в панели событий (как это делает
        # UserPluginControl для журнала 'Система')
        self._main_wnd.events_ctrl.events_log_register(
            name=srv_log.name, group='LSP'
        )

    def setup(self) -> None:
        super().setup()
        self.ensure_started()

        # Страница настроек стека серверов в окне настроек IDE
        self._main_wnd.settings_page_register(LspRegistrySettingsPage(self))

    def on_shutdown(self) -> bool:
        self.shutdown()
        return True

    def teardown(self) -> None:
        self.shutdown()

    ###############################
    # Публичный API для контролов/вью #

    def registry_json(self) -> str:
        """Текущая конфигурация реестра серверов (JSON в формате ``lsp_registry``,
        он же лежит в QSettings по ключу ``<key>/registry_json``).
        """
        return self._registry.to_json()

    def registry_apply_json(self, text: str) -> None:
        """Заменить реестр серверов из JSON-строки (``ValueError`` при битом
        конфиге, реестр при этом не меняется). Применяется к новым
        подключениям редакторов; уже поднятые процессы серверов живут до
        ``shutdown`` IDE.
        """
        self._registry = LspRegistry.from_json(text)
        srv_log.info('LSP реестр применён: %s', ', '.join(self._registry.languages))

    def registry_reset(self) -> None:
        """Вернуть реестр серверов по умолчанию."""
        self._registry = LspRegistry.default()
        srv_log.info('LSP реестр сброшен на значения по умолчанию')

    def ws_host_for(self, file_uri: str) -> str:
        """Хост+путь для WebSocket-подключения к хабу по uri файла (первый
        сервер реестра, без имени — для старых одиночных клиентов).
        Для стека серверов (этап 3.1) используйте ``lsp_stack_for``.
        """
        self.ensure_started()
        assert self._port is not None
        return f'127.0.0.1:{self._port}/{quote(file_uri, safe="")}'

    def ws_hosts_for(self, file_uri: str) -> list[str]:
        """Адреса всех подключений хабу для uri файла (в порядке приоритета
        реестра; в пути — имя сервера). Пусто, если языка нет в реестре.
        """
        self.ensure_started()
        assert self._port is not None
        base = f'127.0.0.1:{self._port}/{quote(file_uri, safe="")}'
        return [
            f'{base}/{spec.name}'
            for spec in self._registry.servers_for_uri(file_uri)
        ]

    def lsp_stack_for(self, file_uri: str) -> list[dict[str, Any]]:
        """LSP-стек файла для JS-стороны (сообщение ``lsp_stack``): пустой
        список, если для языка нет сервера в реестре.
        """
        specs = self._registry.servers_for_uri(file_uri)
        return [
            {
                'url': url,
                'owner': spec.name,
                'priority': priority,
                'languageId': spec.language,
            }
            for priority, (url, spec) in enumerate(
                zip(self.ws_hosts_for(file_uri), specs, strict=True)
            )
        ]

    def ensure_started(self) -> None:
        """Ленивый (идемпотентный) запуск сервера хаба."""
        if self._thread is not None and self._bound.is_set():
            return
        if self._thread is not None:
            # прошлая попытка старта упала — поднимаем заново
            self._thread.join(2.0)
            self._thread = None
        self._bound.clear()
        self._stop_event = asyncio.Event()
        self._thread = threading.Thread(
            target=self._thread_main, name='LspHub', daemon=True
        )
        self._thread.start()
        if not self._bound.wait(10.0):
            self._thread.join(2.0)
            self._thread = None
            msg = 'LSP hub не поднялся за 10 секунд'
            raise RuntimeError(msg)

    def shutdown(self) -> None:
        """Останавливает хаб и убивает все процессы серверов."""
        if self._thread is None:
            return
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join(10.0)
        if self._thread.is_alive():
            srv_log.warning('Поток LSP hub не завершился корректно')
        self._thread = None
        self._port = None

    ###############################
    # Внутренности: отдельный asyncio-поток #

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except OSError:
            srv_log.exception('LSP hub упал')
        finally:
            self._loop = None
            self._bound.clear()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        async with serve(
            self._client_handler, '127.0.0.1', 0, close_timeout=2
        ) as server:
            self._port = server.sockets[0].getsockname()[1]
            srv_log.info('LSP hub слушает 127.0.0.1:%s', self._port)
            self._bound.set()
            await self._stop_event.wait()
            srv_log.info('LSP hub останавливается')
            # закрываем соединения редакторов (параллельно, с коротким
            # close_timeout), чтобы выход из async with не ждал по 10 с
            conns = [c for inst in self._instances.values()
                     for c in inst.clients]
            await asyncio.gather(
                *(conn.ws.close(code=1001) for conn in conns),
                return_exceptions=True,
            )
        await self._kill_instances()

    async def _kill_instances(self) -> None:
        for inst in list(self._instances.values()):
            try:
                await inst.stop()
            except Exception:  # noqa: BLE001
                srv_log.exception(
                    'Ошибка остановки LSP-процесса %s', inst.spec.name
                )
        self._instances.clear()

    async def _client_handler(self, ws: ServerConnection) -> None:
        request = ws.request
        # uri кодируется с safe='', поэтому голый '/' в пути отделяет только
        # необязательное имя сервера: /<enc-uri>[/<server-name>]
        path = (getattr(request, 'path', '') or '').lstrip('/')
        enc_uri, sep, enc_name = path.partition('/')
        file_uri = unquote(enc_uri)
        server_name = unquote(enc_name) if sep else None
        specs = self._registry.servers_for_uri(file_uri)
        if not specs:
            srv_log.debug(
                'LSP hub: нет серверов в реестре для %s — заглушка', file_uri
            )
            await self._stub_handler(ws)
            return
        if server_name:
            spec = next((s for s in specs if s.name == server_name), None)
            if spec is None:
                srv_log.warning(
                    'LSP hub: сервер %r нет в стеке %s — заглушка',
                    server_name, file_uri,
                )
                await self._stub_handler(ws)
                return
        else:
            spec = specs[0]  # старый адрес без имени -> первый сервер
        root_uri = self._registry.root_uri_for(file_uri)
        if not root_uri:
            srv_log.warning(
                'LSP hub: не удалось определить корень для %s', file_uri
            )
            await ws.close()
            return
        try:
            inst = await self._ensure_instance(spec, root_uri)
        except (OSError, RuntimeError):
            srv_log.exception(
                'LSP hub: сервер %s не поднялся для %s', spec.name, root_uri
            )
            await ws.close()
            return
        if inst.dying:
            await ws.close()
            return
        conn = _ClientConnection(ws=ws, seq=next(self._conn_seq))
        inst.clients.append(conn)
        srv_log.info(
            'LSP hub: подключение %s -> %s (root=%s)',
            file_uri, inst.spec.name, inst.root_uri,
        )
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict):
                    await self._route_from_client(inst, conn, msg)
        except ConnectionClosed:
            pass
        finally:
            await self._detach(inst, conn)

    async def _stub_handler(self, ws: ServerConnection) -> None:
        """Для языков без сервера в реестре: initialize с пустыми
        capabilities, всё остальное игнорируется.
        """
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (isinstance(msg, dict) and 'method' in msg and 'id' in msg
                        and msg['method'] == 'initialize'):
                    await ws.send(json.dumps({
                        'jsonrpc': '2.0', 'id': msg['id'],
                        'result': {'capabilities': {}},
                    }))
        except ConnectionClosed:
            pass

    async def _ensure_instance(
        self, spec: ServerSpec, root_uri: str
    ) -> _ServerInstance:
        key = (' '.join(spec.command), root_uri)
        inst = self._instances.get(key)
        if inst is not None and not inst.dying:
            return inst
        command = resolve_command(spec.command)
        if command is None:
            msg = f'LSP-сервер не найден: {spec.command[0]}'
            raise RuntimeError(msg)
        inst = _ServerInstance(key, spec, root_uri, command)
        self._instances[key] = inst
        try:
            await inst.start()
        except (OSError, RuntimeError):
            self._instances.pop(key, None)
            await inst.proc.stop()
            raise
        return inst

    async def _route_from_client(
        self, inst: _ServerInstance, conn: _ClientConnection, msg: dict
    ) -> None:
        method = msg.get('method')
        mid = msg.get('id')
        if method is not None and mid is not None:  # запрос клиента
            if method == 'initialize':
                await conn.ws.send(json.dumps({
                    'jsonrpc': '2.0', 'id': mid, 'result': inst.init_result
                }))
                return
            if method == 'shutdown':
                # общий процесс не гасим, отвечаем клиенту напрямую
                await conn.ws.send(
                    json.dumps({'jsonrpc': '2.0', 'id': mid, 'result': None})
                )
                return
            new_id = inst.client_request_id(conn, mid)
            inst.pending[new_id] = (conn, mid)
            await inst.proc.send({**msg, 'id': new_id})
            return
        if method is None and mid is not None:  # ответ клиента (не ожидаем)
            srv_log.debug('LSP hub: неожиданный ответ клиента %s', mid)
            return
        # нотификация
        if method in CLIENT_NOTIFICATIONS_SWALLOW:
            return
        if method == '$/cancelRequest':
            params = msg.get('params') or {}
            await inst.proc.send({
                'jsonrpc': '2.0', 'method': method,
                'params': {'id': inst.client_request_id(conn, params.get('id'))},
            })
            return
        if method == 'textDocument/didOpen':
            uri = ((msg.get('params') or {}).get('textDocument') or {}).get('uri')
            if uri:
                conn.opened_uris.add(uri)
                srv_log.info('%s: didOpen %s (от conn#%s)',
                             inst.spec.name, uri, conn.seq)
        elif method == 'textDocument/didClose':
            uri = ((msg.get('params') or {}).get('textDocument') or {}).get('uri')
            if uri:
                conn.opened_uris.discard(uri)
                srv_log.info('%s: didClose %s (conn#%s)',
                             inst.spec.name, uri, conn.seq)
        await inst.proc.send(msg)

    async def _detach(
        self, inst: _ServerInstance, conn: _ClientConnection
    ) -> None:
        """Закрытие подключения редактора: закрываем его документы в процессе."""
        for uri in sorted(conn.opened_uris):
            try:
                await inst.proc.send({
                    'jsonrpc': '2.0',
                    'method': 'textDocument/didClose',
                    'params': {'textDocument': {'uri': uri}},
                })
            except (ConnectionResetError, BrokenPipeError):
                break
        conn.opened_uris.clear()
        if conn in inst.clients:
            inst.clients.remove(conn)
        for mid, (target, _original) in list(inst.pending.items()):
            if target is conn:
                inst.pending.pop(mid, None)
