"""asyncio-сессия DAP (этап 11, срез D1) — чистой формы, без Qt.

Одна сессия отладки: адаптер ``python -m debugpy.adapter`` (без ``--port`` —
адаптер говорит DAP прямо через stdin/stdout процесса, фрейминг Content-Length,
тот же принцип, что ``lsp_process_wrapper.StdioLspProcess`` для LSP). Никаких
TCP-портов и токенов не нужно: адаптер сам поднимает launcher и debuggee
после запроса ``launch``; разведённость с ``--server-access-token``/
``--adapter-access-token`` — см. план этапа 11 (ручные токены ломают
авторизацию pydevd).

Ключевая особенность протокола (найдено пробами debugpy 1.8.21): адаптер НЕ
отвечает на ``launch``, пока не получит ``configurationDone``. Поэтому здесь
отдельная задача-читатель, раздающая ответы по ``request_seq`` в futures, а
последовательная схема «отправил — прочитал ответ» гарантированно deadlock'нет.
События (``stopped``/``output``/``terminated``/…) уходят в конструкторский
``on_event`` — прямо из потока event-loop, queued-мост на стороне контрола.

Все методы корутины — вызываются только из цикла, в котором запущена сессия.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pyqtp.controls.dap_control.dap_framing import (
    HEADER_TERMINATOR,
    build_request,
    encode_message,
    parse_content_length,
)

if TYPE_CHECKING:
    from collections.abc import Callable

dbg_log = logging.getLogger('bcad.dbg')

# DAP-сообщение не поместилось в очередь записи (читатель/адаптер встал)
_QUEUE_PUT_TIMEOUT = 10.0
# тайм-ауты handshake-запросов ``initialize``/``configurationDone``
_HANDSHAKE_TIMEOUT = 15.0
# ожидание graceful-завершения процесса в ``close``
_GRACEFUL_WAIT = 3.0

# капбилити клиента: адаптер на их наличие решает, что слать (например,
# supportsRunInTerminalRequest у нас нет — console должен быть internalConsole)
CLIENT_CAPABILITIES = {
    'supportsVariableType': True,
    'supportsVariablePaging': False,
    'supportsRunInTerminalRequest': False,
    'supportsMemoryReferences': False,
    'supportsProgressReporting': False,
    'supportsInvalidatedEvent': False,
}


class DapError(RuntimeError):
    """Сбой DAP-запроса (адаптер ответил ``success=False``) или тайм-аут."""


def extract_error_message(resp: dict[str, Any]) -> str:
    """Человеческий текст ошибки из DAP-ответа (``message`` + текст из body)."""
    parts: list[str] = []
    message = resp.get('message')
    if isinstance(message, str) and message:
        parts.append(message)
    body = resp.get('body')
    if isinstance(body, dict):
        output = body.get('output')
        if isinstance(output, str) and output:
            parts.append(output.strip())
    return ': '.join(parts)


class DapSession:
    """DAP-клиент поверх stdio-процесса адаптера (один процесс = одна сессия)."""

    def __init__(
        self,
        command: list[str],
        on_event: Callable[[str, dict[str, Any]], None],
        *,
        cwd: str | None = None,
    ) -> None:
        self.command = list(command)
        self.on_event = on_event
        self.cwd = cwd
        self.seq = 0
        self.initialized_event: asyncio.Event = asyncio.Event()
        self.terminated_event: asyncio.Event = asyncio.Event()
        self.last_error = ''
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        self._out_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._writer_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False
        self._closing = False

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """Процесс адаптера (None — сессия ещё/уже не жива)."""
        return self._proc

    @property
    def alive(self) -> bool:
        """Жив ли процесс адаптера и сессия не закрыта явно."""
        proc = self._proc
        return not self._closed and proc is not None and proc.returncode is None

    @property
    def closed(self) -> bool:
        """Сессия закрыта (явно или после смерти процесса)."""
        return self._closed

    async def start(self) -> None:
        """Запускает процесс адаптера и задачи чтения/записи."""
        if self._proc is not None:
            msg = 'сессия уже запущена'
            raise DapError(msg)
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        dbg_log.info(
            'DAP-адаптер запущен: %s (pid=%s)', ' '.join(self.command), self._proc.pid,
        )
        self._writer_task = asyncio.create_task(self._writer_loop())
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._wait_task = asyncio.create_task(self._watch_process())

    async def handshake(self, launch_arguments: dict[str, Any]) -> str:
        """Канонический пусковой ритуал; возвращает текст ошибки или ``''``.

        Порядок (иначе адаптер зависает): ``initialize`` → запрос ``launch``
        (ответ придёт ПОСЛЕ ``configurationDone``) → дождаться события
        ``initialized`` → ``setBreakpoints`` (хук ``on_breakpoints_ready``) →
        ``configurationDone`` → дождаться ответа ``launch``.
        """
        init_resp = await self.request(
            'initialize',
            {
                'adapterID': 'pyqtp',
                'pathFormat': 'path',
                'linesStartAt1': True,
                'columnsStartAt1': True,
                'supportsStartDebuggingRequest': False,
                **CLIENT_CAPABILITIES,
            },
            timeout=_HANDSHAKE_TIMEOUT,
        )
        caps = (init_resp.get('body') or {}).get('capabilities')
        dbg_log.debug('Capabilities адаптера: %r', sorted(caps or {}))

        launch_seq = self._next_seq()
        launch_fut = self._register(launch_seq)
        await self._write(
            build_request(launch_seq, 'launch', launch_arguments),
        )
        try:
            await asyncio.wait_for(self.initialized_event.wait(), _HANDSHAKE_TIMEOUT)
        except TimeoutError:
            self._pending.pop(launch_seq, None)
            return 'Адаптер не прислал событие initialized'

        await self.on_breakpoints_ready()
        await self.request('configurationDone', {}, timeout=_HANDSHAKE_TIMEOUT)
        try:
            launch_resp = await asyncio.wait_for(launch_fut, _HANDSHAKE_TIMEOUT)
        except TimeoutError:
            return 'Адаптер не ответил на launch после configurationDone'
        if not launch_resp.get('success'):
            return extract_error_message(launch_resp) or 'Запуск отладки не удался'
        return ''

    async def on_breakpoints_ready(self) -> None:
        """Хук handshake: разослать брейкпоинты (переопределяется контролом).

        Попадание в неприведённые пока модули — нормальное состояние debugpy:
        брейкпоинты верифицируются и сработают при импорте модуля.
        """

    async def request(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,  # noqa: ASYNC109 -同步-ожидание ответа адаптера
    ) -> dict[str, Any]:
        """Отправить запрос и дождаться ответа (полный dict-ответ адаптера)."""
        seq = self._next_seq()
        fut = self._register(seq)
        await self._write(build_request(seq, command, arguments or {}))
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            self._pending.pop(seq, None)
            msg = f'Тайм-аут запроса {command}'
            raise DapError(msg) from exc

    async def request_body(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Как ``request``, но бросает ``DapError`` на ``success=False``."""
        resp = await self.request(command, arguments, **kwargs)
        if not resp.get('success'):
            raise DapError(extract_error_message(resp) or f'{command} не выполнен')
        body = resp.get('body')
        return body if isinstance(body, dict) else {}

    async def stack_trace(self, thread_id: int, levels: int = 8) -> list[dict[str, Any]]:
        """Кадр ``stackTrace`` потока (нумерация строк/колонок 1-based)."""
        body = await self.request_body(
            'stackTrace', {'threadId': thread_id, 'startFrame': 0, 'levels': levels},
        )
        frames = body.get('stackFrames')
        return [f for f in frames if isinstance(f, dict)] if isinstance(frames, list) else []

    async def close(self, *, terminate: bool = False) -> None:
        """Закрыть сессию: ``terminate`` (best effort) → завершение адаптера.

        Идемпотентно; живёт и после ``terminated``-события. Адаптер после
        ``terminate`` сам гасит launcher и debuggee; graceful-ожидание с
        переходом на ``kill`` (оставленный адаптер пережил бы IDE).
        """
        if self._closed or self._closing:
            return
        # _closing ставим ДО terminate-запроса (защита от параллельных close),
        # но сам _closed — ПОСЛЕ: _write/alive иначе зарезали бы terminate
        self._closing = True
        if terminate and self.alive:
            try:
                await self.request('terminate', {}, timeout=5.0)
            except (DapError, ConnectionError, OSError):
                dbg_log.debug('terminate не принят адаптером (закрываем жёстко)')
            else:
                # даём адаптеру долить события terminated/exited до закрытия
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.terminated_event.wait(), 3.0)
        self._closed = True
        await self._finalize()

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _register(self, seq: int) -> asyncio.Future[dict[str, Any]]:
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[seq] = fut
        return fut

    async def _write(self, msg: dict[str, Any]) -> None:
        if self._closed or self._proc is None or self._proc.stdin is None:
            err = 'сессия закрыта'
            raise DapError(err)
        try:
            await asyncio.wait_for(
                self._out_queue.put(msg), timeout=_QUEUE_PUT_TIMEOUT,
            )
        except TimeoutError as exc:
            err = 'адаптер не читает запросы'
            raise DapError(err) from exc

    async def _writer_loop(self) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        stdin = self._proc.stdin
        try:
            while True:
                msg = await self._out_queue.get()
                stdin.write(encode_message(msg))
                await stdin.drain()
        except (ConnectionResetError, BrokenPipeError, RuntimeError):
            pass
        except OSError:
            dbg_log.exception('Ошибка записи stdin DAP-адаптера')

    async def _read_loop(self) -> None:
        """Разбирает stdout адаптера: ответы — в futures, события — в on_event."""
        assert self._proc is not None
        assert self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while True:
                header_raw = await stdout.readuntil(HEADER_TERMINATOR)
                length = parse_content_length(header_raw)
                if length is None:
                    dbg_log.error('Некорректный заголовок от DAP-адаптера, стоп')
                    break
                body = await stdout.readexactly(length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    dbg_log.error(
                        'Невалидный JSON от DAP-адаптера: %.200s',
                        body.decode(errors='replace'),
                    )
                    continue
                self._dispatch(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except OSError:
            dbg_log.exception('Ошибка чтения stdout DAP-адаптера')
        dbg_log.info('Поток DAP-адаптера закрыт: %s', ' '.join(self.command))
        self._fail_all('Поток адаптера закрыт')

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if not isinstance(msg, dict):
            return
        msg_type = msg.get('type')
        if msg_type == 'response':
            request_seq = msg.get('request_seq')
            fut = self._pending.pop(request_seq, None) if isinstance(request_seq, int) else None
            if fut is not None and not fut.done():
                fut.set_result(msg)
        elif msg_type == 'event':
            name = str(msg.get('event') or '')
            body = msg.get('body')
            if name == 'terminated':
                self.terminated_event.set()
            if name == 'initialized':
                self.initialized_event.set()
            try:
                self.on_event(name, body if isinstance(body, dict) else {})
            except Exception:  # noqa: BLE001
                dbg_log.exception('Сбой обработчика DAP-события %s', name)

    def _fail_all(self, reason: str) -> None:
        """Все висящие запросы падают с этой причиной (смерть процесса/закрытие)."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(DapError(reason))
        self._pending.clear()

    async def _drain_stderr(self) -> None:
        """Логирует stderr адаптера в лог ``bcad.dbg`` (как LSP-процессы)."""
        assert self._proc is not None
        assert self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            dbg_log.debug('[DAP stderr] %s', line.decode(errors='replace').rstrip())

    async def _watch_process(self) -> None:
        """Страховка: смерть процесса рушит висящие запросы сразу."""
        assert self._proc is not None
        await self._proc.wait()
        if not self._closed:
            self._fail_all(f'Адаптер завершился (код {self._proc.returncode})')

    async def _finalize(self) -> None:
        """Снять задачи и погасить процесс (graceful → kill)."""
        self._fail_all('Сессия отладки закрыта')
        for task in (self._reader_task, self._writer_task, self._stderr_task, self._wait_task):
            if task is not None and not task.done():
                task.cancel()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(asyncio.shield(proc.wait()), _GRACEFUL_WAIT)
            except TimeoutError:
                dbg_log.warning('DAP-адаптер не завершился, убиваю')
                proc.kill()
                with contextlib.suppress(ProcessLookupError, OSError):
                    await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        if proc is not None:
            # закрываем транспорт ПОКА loop жив: иначе __del__ вылезет
            # unraisable-предупреждением уже после закрытия event loop.
            # NB Process.close() есть в рантайме 3.11, но не в его стабах —
            # поэтому getattr, а не прямой вызов
            proc_close = getattr(proc, 'close', None)
            if callable(proc_close):
                with contextlib.suppress(Exception):
                    proc_close()
