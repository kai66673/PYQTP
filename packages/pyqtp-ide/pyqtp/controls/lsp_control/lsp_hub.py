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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, unquote, urlparse

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.lsp_control.file_rename import (
    is_empty_rename_result,
    merge_will_rename_results,
    rename_files_params,
    supports_will_rename,
)
from pyqtp.controls.lsp_control.lsp_process_wrapper import StdioLspProcess
from pyqtp.controls.lsp_control.lsp_registry import (
    LspRegistry,
    ServerSpec,
    resolve_command,
)
from pyqtp.controls.lsp_control.lsp_settings_page import (
    LspRegistrySettingsPage,
)
from pyqtp.controls.lsp_control.workspace_symbol import (
    parse_workspace_symbols,
    spec_supports_workspace_symbol,
)
from pyqtp.controls.problems_control.problems import (
    Problem,
    diagnostics_from_publish,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

    from pyqtp.controls.lsp_control.workspace_symbol import WorkspaceSymbol

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
        # поиск символов по корню проекта (остаток этапа 8): запрос исходит
        # от хаба, динамическая регистрация не нужна
        'workspaceSymbol': {'dynamicRegistration': False},
        # переименование файлов из панели проекта (этап 9.5): запрос тоже
        # исходит от хаба; без этой возможности basedpyright правки импортов
        # не шлёт
        'willRenameFiles': {'dynamicRegistration': False},
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

# Повторная пауза для «холодного» пустого ответа локальных запросов хаба
# (первичная индексация проекта сервером; измерено на basedpyright — индекс
# готов примерно через 2 с после handshake на маленьком проекте; одинаково
# для workspace/symbol и workspace/willRenameFiles)
_COLD_RETRY_DELAY = 2.0


@dataclass
class _ClientConnection:
    """Одно WebSocket-подключение редактора к хабу."""

    ws: ServerConnection
    seq: int
    opened_uris: set[str] = field(default_factory=set)


@dataclass
class DiagHooks:
    """Крюки стора диагностик хаба для экземпляра сервера (этап 10.1).

    Оба колбэка вызываются из потока event-loop хаба: ``publish`` пишет
    очередной ``publishDiagnostics`` сервера в стор, ``purge`` снимает весь
    вклад сервера (смерть процесса).
    """

    publish: Callable[[str, str, tuple[Problem, ...]], None]
    purge: Callable[[str], None]


class _ServerInstance:
    """Экземпляр LSP-процесса и подключения редакторов к нему."""

    def __init__(
        self,
        key: tuple[str, str],
        spec: ServerSpec,
        root_uri: str,
        command: list[str],
        diag: DiagHooks | None = None,
    ) -> None:
        self.key = key
        self.spec = spec
        self.root_uri = root_uri
        # этап 10.1: стор диагностик хаба (публикация/снимание вклада сервера)
        self.diag = diag
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
        # хендшейк (initialize) завершён — событие ставим в finally start();
        # параллельные _ensure_instance ждут его, а не плодят второй процесс
        self._ready = asyncio.Event()
        # методы «холодого» прогрева (см. request_local_warm) уже проверены
        # на этом процессе: повторный пустой ответ — это ответ, а не недогрев
        self.cold_checked: set[str] = set()
        # методы, чей прогрев прямо сейчас исполняет другой запрос: параллельные
        # пустые ответы не должны «съедать» общий бюджет — второй ждёт чужой
        # прогрев и переспрашивает сам (гонка из test_live_workspace_symbols_cb)
        self.cold_inflight: set[str] = set()

    async def start(self) -> None:
        try:
            await self._start_inner()
        finally:
            self._ready.set()

    async def _start_inner(self) -> None:
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

    async def request_local_warm(
        self,
        method: str,
        params: Any,
        reply_timeout: float,
        is_empty: Callable[[Any], bool],
    ) -> dict:
        """``request_local`` + один «холодный» прогрев на пару (метод, процесс).

        Холодные basedpyright/clangd во время построения первичного индекса
        отвечают пусто (а не ждут) — пустой ответ до прогрева получает второй
        шанс через ``_COLD_RETRY_DELAY``; прогрев — один раз на метод, но
        параллельные запросы того же метода тоже дожидаются чужого прогрева и
        переспрашивают (иначе запрос, который вот-вот станет устаревшим,
        «сжигает» бюджет, а актуальный остаётся с пустым ответом).
        Непустой ответ — индекс уже прогрет, метод сразу считается тёплым.
        Таймауты самого запроса здесь не глотаются — решает вызывающий код.
        """
        response = await asyncio.wait_for(
            self.request_local(method, params), reply_timeout
        )
        if 'error' in response:
            return response
        if not is_empty(response.get('result')):
            self.cold_checked.add(method)
            return response
        if method in self.cold_checked:
            return response  # прогрев уже был — пустой ответ это ответ
        first = method not in self.cold_inflight
        self.cold_inflight.add(method)
        try:
            await asyncio.sleep(_COLD_RETRY_DELAY)
            return await asyncio.wait_for(
                self.request_local(method, params), reply_timeout
            )
        except (TimeoutError, ConnectionError, OSError):
            # прогрев сорвался — отдаём исходный (пустой, но валидный) ответ
            return response
        finally:
            if first:
                self.cold_inflight.discard(method)
                self.cold_checked.add(method)

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
            if (method == 'textDocument/publishDiagnostics'
                    and self.diag is not None):
                # этап 10.1: тот же payload — в python-стор хаба (панель
                # «Проблемы»); пустой список снимает вклад этого сервера;
                # не-файловые uri парсер отсекает
                uri, problems = diagnostics_from_publish(params, self.spec.name)
                if uri:
                    self.diag.publish(self.spec.name, uri, tuple(problems))
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
        if self.diag is not None:
            # этап 10.1: диагностики мёртвого сервера протухли — снимаем его
            # вклад (панель не должна показывать призраков)
            self.diag.purge(self.spec.name)


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
        # два слоя реестра (этап 9.6B): _registry_app_json — значение из
        # настроек приложения (QSettings, '' — дефолт), _registry_project_json —
        # проектное перекрытие из .pyqtp/settings.json (None — нет);
        # _registry — эффективный. settings_write пишет слой приложения, а не
        # эффективный реестр, чтобы перекрытие проекта не «утекало» в QSettings
        self._registry_app_json = ''
        self._registry_project_json: str | None = None
        # workspace/symbol (остаток этапа 8): поколение запроса — запоздалые
        # ответы старых запросов не доходят до колбэка; кэш образцовых файлов
        # root_uri -> [(spec, sample_uri)] (сбрасывается при смене реестра)
        self._wsym_gen = 0
        self._wsym_samples: dict[str, list[tuple[ServerSpec, str]]] = {}
        # стор диагностик (этап 10.1): последние publishDiagnostics каждого
        # сервера по каждому uri — ключ (uri, имя сервера), как owner маркеров
        # Monaco (basedpyright и ruff публикуют в один uri независимо). Пустой
        # список — снять ключ («файл чист»). Пишется из потока event-loop
        # хаба; читается/подписывается из GUI-потока под тем же замком.
        self._diag_lock = threading.Lock()
        self._diag_store: dict[tuple[str, str], tuple[Problem, ...]] = {}
        self._diag_observers: list[Callable[[], None]] = []

    ###############################
    # Жизненный цикл контрола #

    def settings_read(self, **kwargs: Any) -> None:
        super().settings_read(**kwargs)
        self._registry_app_json = cast('str', self._main_wnd.settings.value(
            f'{self.key}/registry_json', '', str
        ))
        self._reapply_effective_registry()
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
        # Пишем слой настроек приложения, а НЕ эффективный реестр: проектное
        # перекрытие (.pyqtp/settings.json) не должно «утекать» в QSettings.
        # Чистим ключ, если слой пуст/равен дефолту/битый (не мусорить).
        text = self._registry_app_json
        if not text:
            self._main_wnd.settings.remove(f'{self.key}/registry_json')
            return
        try:
            parsed = LspRegistry.from_json(text)
        except (TypeError, ValueError):
            self._main_wnd.settings.remove(f'{self.key}/registry_json')
            return
        if parsed.to_dict() == LspRegistry.default().to_dict():
            self._main_wnd.settings.remove(f'{self.key}/registry_json')
        else:
            self._main_wnd.settings.setValue(
                f'{self.key}/registry_json', text
            )

    ###############################
    # Реестр серверов: два слоя (этап 9.6B) #

    def _effective_registry(self) -> LspRegistry:
        """Эффективный реестр: проектное перекрытие > слой приложения > дефолт.

        Битый JSON слоя логируется и раскрывается в слой ниже (применение
        настроек проекта не должно ронять IDE).
        """
        for source, label in (
            (self._registry_project_json, 'проектное перекрытие'),
            (self._registry_app_json, 'настройки приложения'),
        ):
            if not source:
                continue
            try:
                return LspRegistry.from_json(source)
            except (TypeError, ValueError) as exc:
                srv_log.error(
                    'Некорректная конфигурация LSP-реестра (%s) — '
                    'используем слой ниже: %s', label, exc,
                )
        return LspRegistry.default()

    def _reapply_effective_registry(self) -> None:
        """Пересобирает эффективный реестр из слоёв (кэш образцовых файлов
        workspace/symbol держит ссылки на старые специ — сбрасываем).
        """
        self._registry = self._effective_registry()
        self._wsym_samples.clear()

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
        """ЭФФЕКТИВНАЯ конфигурация реестра серверов (с учётом проектного
        перекрытия; JSON в формате ``lsp_registry``). Для редактирования
        слоя приложения используй ``registry_app_json``.
        """
        return self._registry.to_json()

    def registry_app_json(self) -> str:
        """Конфигурация реестра в СЛОЕ НАСТРОЕК ПРИЛОЖЕНИЯ (без проектных
        перекрытий; пустой ключ раскрывается в дефолтный JSON).
        """
        if self._registry_app_json:
            return self._registry_app_json
        return LspRegistry.default().to_json()

    def registry_apply_json(self, text: str) -> None:
        """Заменить реестр серверов в СЛОЕ ПРИЛОЖЕНИЯ из JSON-строки
        (``ValueError`` при битом конфиге, слой при этом не меняется).
        Проектное перекрытие, если оно задано, остаётся старше. Применяется
        к новым подключениям редакторов; уже поднятые процессы серверов
        живут до ``shutdown`` IDE.
        """
        LspRegistry.from_json(text)  # валидация до мутации состояния
        self._registry_app_json = text
        self._reapply_effective_registry()
        srv_log.info(
            'LSP реестр применён (слой приложения): %s',
            ', '.join(self._registry.languages),
        )

    def registry_reset(self) -> None:
        """Вернуть реестр серверов по умолчанию в СЛОЕ ПРИЛОЖЕНИЯ
        (проектное перекрытие, если оно есть, остаётся в силе).
        """
        self._registry_app_json = ''
        self._reapply_effective_registry()
        srv_log.info(
            'LSP реестр сброшен на значения по умолчанию (слой приложения)'
        )

    def registry_set_project_overlay(self, text: str | None) -> None:
        """Проектное перекрытие реестра из ``.pyqtp/settings.json``
        (``None`` — перекрытия нет, эффективный реестр = слой приложения).
        Битое значение логируется, используется слой ниже.
        """
        if text is not None and not isinstance(text, str):
            srv_log.error(
                'Проектное перекрытие LSP-реестра должно быть JSON-строкой — '
                'игнорируем'
            )
            text = None
        self._registry_project_json = text
        self._reapply_effective_registry()

    ##################################
    # Стор диагностик (этап 10.1) #

    def add_diagnostics_observer(self, cb: Callable[[], None]) -> None:
        """Подписка на изменения стора диагностик.

        ``cb`` вызывается ИЗ ПОТОКА event-loop хаба (GUI-стороне цеплять
        свой queued-мост, паттерн ``_ScanSignals``); дубликат не добавляет.
        """
        with self._diag_lock:
            if cb not in self._diag_observers:
                self._diag_observers.append(cb)

    def remove_diagnostics_observer(self, cb: Callable[[], None]) -> None:
        """Снимает подписку (несуществующая — молча)."""
        with self._diag_lock:
            if cb in self._diag_observers:
                self._diag_observers.remove(cb)

    def diagnostics_snapshot(self) -> dict[str, list[Problem]]:
        """Последние диагностики: uri -> слитые проблемы всех серверов.

        Списки отсортированы (строка, колонка, ...); «чистых» файлов в
        снимке нет — пустой вклад ключа снимается при публикации.
        """
        with self._diag_lock:
            merged: dict[str, list[Problem]] = {}
            for (uri, _owner), problems in self._diag_store.items():
                merged.setdefault(uri, []).extend(problems)
        for items in merged.values():
            items.sort()
        return merged

    def _record_diagnostics(
        self, owner: str, uri: str, problems: tuple[Problem, ...]
    ) -> None:
        """Запись очередной публикации сервера (поток event-loop хаба).

        ``owner`` проставляется каждой проблеме — панель и статус-бар должны
        видеть, какой сервер опубликовал диагностику (basedpyright и ruff
        публикуют в один uri независимо).
        """
        if not uri:
            return
        with self._diag_lock:
            if problems:
                self._diag_store[(uri, owner)] = tuple(
                    p if p.owner == owner else replace(p, owner=owner)
                    for p in problems
                )
            else:
                self._diag_store.pop((uri, owner), None)
        self._notify_diag_observers()

    def _drop_diagnostics_of(self, owner: str) -> None:
        """Снимает весь вклад сервера (смерть процесса, поток event-loop)."""
        with self._diag_lock:
            stale = [key for key in self._diag_store if key[1] == owner]
            for key in stale:
                del self._diag_store[key]
        if stale:
            self._notify_diag_observers()

    def _notify_diag_observers(self) -> None:
        with self._diag_lock:
            observers = list(self._diag_observers)
        for cb in observers:
            with contextlib.suppress(Exception):
                cb()

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

    def workspace_symbols_async(
        self,
        project_root: Path,
        query: str,
        cb: Callable[[list[WorkspaceSymbol], str | None], None] | None = None,
        timeout: float = 5.0,
    ) -> Future[tuple[list[WorkspaceSymbol], str | None]]:
        """``workspace/symbol`` по корню проекта (остаток этапа 8, этап 9).

        Вызывается из GUI-потока; запрос исполняется в event-loop потока
        хаба. Выбор сервера: по образцовому файлу каждого языка реестра
        поднимается (или переиспользуется) процесс из общего пула
        ``(command, root_uri)``, сервер берётся по очереди приоритета —
        первый, объявивший ``workspaceSymbolProvider``.

        ``cb(symbols, error)`` вызывается в потоке хаба (GUI-стороне цеплять
        queued-сигнал), но только если запрос не устарел — более новый
        запрос ``workspace_symbols_async`` гасит чужие запоздалые ответы.
        Тот же результат возвращает ``concurrent.futures.Future``
        (исключение — только сбой самого хаба).
        """
        self.ensure_started()
        loop = self._loop
        if loop is None:  # ensure_started уже кинул, но loop гаснет на shutdown
            msg = 'LSP hub остановлен'
            raise RuntimeError(msg)
        self._wsym_gen += 1
        root_uri = Path(project_root).resolve().as_uri()
        fut = asyncio.run_coroutine_threadsafe(
            self._gather_workspace_symbols(root_uri, query, timeout), loop,
        )
        if cb is None:
            return fut
        gen = self._wsym_gen

        def _done(fut_: Future[tuple[list[WorkspaceSymbol], str | None]]) -> None:
            if fut_.cancelled() or gen != self._wsym_gen:
                return
            try:
                symbols, error = fut_.result()
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                srv_log.exception('workspace/symbol упал в потоке хаба')
                return
            with contextlib.suppress(Exception):
                cb(symbols, error)

        fut.add_done_callback(_done)
        return fut

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
        # этап 10.1: процессов серверов больше нет — стор диагностик гасим
        # вместе с хабом, чтобы в панели не оставалось призраков
        with self._diag_lock:
            had_diags = bool(self._diag_store)
            self._diag_store.clear()
        if had_diags:
            self._notify_diag_observers()

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
            if not inst._ready.is_set():
                # параллельный хендшейк другой сопрограммы (два редактора или
                # GUI-запрос workspace/symbol): ждём его, а не поднимаем второй
                # процесс на тот же ключ
                await inst._ready.wait()
                if not inst.init_result:
                    msg = (
                        f'LSP-сервер {spec.name} не поднялся '
                        '(параллельный старт завершился ошибкой)'
                    )
                    raise RuntimeError(msg)
            return inst
        command = resolve_command(spec.command)
        if command is None:
            msg = f'LSP-сервер не найден: {spec.command[0]}'
            raise RuntimeError(msg)
        inst = _ServerInstance(
            key,
            spec,
            root_uri,
            command,
            diag=DiagHooks(
                publish=self._record_diagnostics,
                purge=self._drop_diagnostics_of,
            ),
        )
        self._instances[key] = inst
        try:
            await inst.start()
        except (OSError, RuntimeError):
            self._instances.pop(key, None)
            await inst.proc.stop()
            raise
        return inst

    ###############################
    # Внутренности: поиск символов #

    async def _gather_workspace_symbols(
        self, root_uri: str, query: str, reply_timeout: float
    ) -> tuple[list[WorkspaceSymbol], str | None]:
        """Рабочая лошадка запроса; живёт в потоке event-loop хаба."""
        try:
            return await self._workspace_symbols(root_uri, query, reply_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            srv_log.exception('workspace/symbol (%r) упал', query)
            return [], 'внутренняя ошибка хаба (подробности в журнале LSP)'

    async def _workspace_symbols(
        self, root_uri: str, query: str, reply_timeout: float
    ) -> tuple[list[WorkspaceSymbol], str | None]:
        """Обход стеков всех языков реестра с образцовыми файлами проекта.

        Первый сервер, объявивший ``workspaceSymbolProvider``, отвечает;
        остальные (и чужие ошибки) — следующий по приоритету. ``error`` —
        последняя неудача, если символов так и не дождались.
        """
        samples = self._wsym_samples.get(root_uri)
        if samples is None:
            samples = self._collect_samples(root_uri)
            self._wsym_samples[root_uri] = samples
        last_error: str | None = None
        for spec, _sample_uri in samples:
            try:
                inst = await self._ensure_instance(spec, root_uri)
            except (OSError, RuntimeError) as exc:
                last_error = str(exc)
                continue
            if inst.dying:
                last_error = f'{spec.name}: процесс завершается'
                continue
            if not spec_supports_workspace_symbol(inst.init_result):
                continue
            try:
                response = await inst.request_local_warm(
                    'workspace/symbol',
                    {'query': query},
                    reply_timeout,
                    lambda res: not parse_workspace_symbols(res),
                )
            except (TimeoutError, ConnectionError, OSError):
                last_error = (
                    f'{spec.name}: ответ не получен (сервер не готов или '
                    'процесс завершился)'
                )
                continue
            if 'error' in response:
                message = (response.get('error') or {}).get('message')
                last_error = f'{spec.name}: {message or "ошибка запроса"}'
                continue
            symbols = parse_workspace_symbols(response.get('result'))
            srv_log.info(
                '%s: workspace/symbol %r -> %d символов',
                spec.name, query, len(symbols),
            )
            return symbols, None
        if last_error is None:
            last_error = (
                'ни один сервер реестра не поддерживает workspace/symbol'
            )
        return [], last_error

    def _collect_samples(self, root_uri: str) -> list[tuple[ServerSpec, str]]:
        """(Сервер, uri образцового файла) для всех языков реестра по корню.

        Порядок — за обходом реестра (для python это в первую очередь
        basedpyright — он и отвечает за ``workspace/symbol``).
        """
        root = Path(unquote(urlparse(root_uri).path))
        samples: list[tuple[ServerSpec, str]] = []
        for language in self._registry.languages:
            sample = self._registry.sample_file_for(root, language)
            if sample is None:
                continue
            sample_uri = sample.as_uri()
            samples.extend(
                (spec, sample_uri)
                for spec in self._registry.servers_for_uri(sample_uri)
            )
        return samples

    ################################
    # Файловые операции (этап 9.5) #

    def will_rename_files_async(
        self,
        project_root: Path,
        old_uri: str,
        new_uri: str,
        cb: Callable[[dict[str, Any] | None, str | None], None] | None = None,
        timeout: float = 5.0,
    ) -> Future[tuple[dict[str, Any] | None, str | None]]:
        """``workspace/willRenameFiles`` перед переименованием файла на диске.

        Вызывается из GUI-потока; запрос исполняется в event-loop потока
        хаба. Серверы берутся из стека языка переименовываемого файла
        (``servers_for_uri(old_uri)``), процессы — из общего пула
        ``(command, root_uri)``. В отличие от ``workspace/symbol`` здесь
        сливаются ответы ВСЕХ серверов, объявивших поддержку: каждый правит
        «свои» ссылки (pyright — импорты, clangd — inclusion'ы).

        ``cb(edit, error)`` вызывается в потоке хаба (GUI-стороне цеплять
        queued-сигнал) ВСЕГДА — порождённые ответы не гасятся (поколение
        фильтрует сама вызвавшая сторона, см. ProjectControl). ``edit`` —
        слитый WorkspaceEdit (форма ``documentChanges``) или ``None``, если
        правок нет ни у кого; ``error`` — информационное: переименование
        из-за сбоя LSP не отменяется. Тот же результат возвращает
        ``concurrent.futures.Future``.
        """
        self.ensure_started()
        loop = self._loop
        if loop is None:  # ensure_started уже кинул, но loop гаснет на shutdown
            msg = 'LSP hub остановлен'
            raise RuntimeError(msg)
        root_uri = Path(project_root).resolve().as_uri()
        fut = asyncio.run_coroutine_threadsafe(
            self._gather_will_rename(root_uri, old_uri, new_uri, timeout),
            loop,
        )
        if cb is None:
            return fut

        def _done(
            fut_: Future[tuple[dict[str, Any] | None, str | None]],
        ) -> None:
            if fut_.cancelled():
                return
            try:
                edit, error = fut_.result()
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                srv_log.exception(
                    'workspace/willRenameFiles упал в потоке хаба',
                )
                edit, error = None, (
                    'внутренняя ошибка хаба (подробности в журнале LSP)'
                )
            with contextlib.suppress(Exception):
                cb(edit, error)

        fut.add_done_callback(_done)
        return fut

    def did_rename_files(
        self, project_root: Path, old_uri: str, new_uri: str
    ) -> None:
        """Нотификация ``workspace/didRenameFiles`` всем живым процессам корня.

        После дискового переименования серверам (basedpyright/clangd) нужно
        обновить индекс; ответа не ждём — fire-and-forget. Если хаба нет
        (ещё не стартовал или уже гаснет) — молча уходим, поднимать хаб
        ради нотификации не за чем.
        """
        loop = self._loop
        if loop is None:
            return
        root_uri = Path(project_root).resolve().as_uri()
        params = rename_files_params(old_uri, new_uri)

        async def _notify() -> None:
            for inst in list(self._instances.values()):
                if (
                    inst.root_uri != root_uri
                    or inst.dying
                    or not inst.init_result
                ):
                    continue
                try:
                    await inst.proc.send({
                        'jsonrpc': '2.0',
                        'method': 'workspace/didRenameFiles',
                        'params': params,
                    })
                except (ConnectionResetError, BrokenPipeError, OSError):
                    continue

        fut = asyncio.run_coroutine_threadsafe(_notify(), loop)

        def _done(fut_: Future[None]) -> None:
            if fut_.cancelled():
                return
            with contextlib.suppress(Exception):
                fut_.result()

        fut.add_done_callback(_done)

    async def _gather_will_rename(
        self, root_uri: str, old_uri: str, new_uri: str, reply_timeout: float
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Рабочая лошадка запроса; живёт в потоке event-loop хаба."""
        try:
            return await self._will_rename(root_uri, old_uri, new_uri, reply_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            srv_log.exception(
                'workspace/willRenameFiles (%s -> %s) упал', old_uri, new_uri,
            )
            return None, 'внутренняя ошибка хаба (подробности в журнале LSP)'

    async def _will_rename(
        self, root_uri: str, old_uri: str, new_uri: str, reply_timeout: float
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Опрос стека серверов языка файла; ответы сливаются.

        ``error`` — последняя неудача, если правок не принесли, но не
        отказ: переименование всё равно исполняется без правок LSP.
        """
        params = rename_files_params(old_uri, new_uri)
        results: list[Any] = []
        last_error: str | None = None
        for spec in self._registry.servers_for_uri(old_uri):
            try:
                inst = await self._ensure_instance(spec, root_uri)
            except (OSError, RuntimeError) as exc:
                last_error = str(exc)
                continue
            if inst.dying:
                last_error = f'{spec.name}: процесс завершается'
                continue
            if not supports_will_rename(inst.init_result):
                continue
            try:
                response = await inst.request_local_warm(
                    'workspace/willRenameFiles',
                    params,
                    reply_timeout,
                    is_empty_rename_result,
                )
            except (TimeoutError, ConnectionError, OSError):
                last_error = (
                    f'{spec.name}: ответ не получен (сервер не готов или '
                    'процесс завершился)'
                )
                continue
            if 'error' in response:
                message = (response.get('error') or {}).get('message')
                last_error = f'{spec.name}: {message or "ошибка запроса"}'
                continue
            results.append(response.get('result'))
        edit = merge_will_rename_results(results)
        count = sum(len(c) for c in (edit or {}).get('documentChanges', []))
        srv_log.info(
            'willRenameFiles %s -> %s: %d правок от %d серверов',
            old_uri, new_uri, count, len(results),
        )
        if edit is None:
            return None, last_error
        return edit, None

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
