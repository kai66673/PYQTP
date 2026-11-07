"""stdio-процесс LSP-сервера с фреймингом Content-Length.

Чтение stdout — корутиной в цикле хаба (asyncio.StreamReader), а не
отдельным тредом: весь хаб живёт в одном asyncio-цикле (см. lsp_hub.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

srv_log = logging.getLogger('bcad.lsp')


class StdioLspProcess:
    """Запускает сервер как stdio-подпроцесс и читает его сообщения."""

    def __init__(self, command: list[str], cwd: Path) -> None:
        self.command = command
        self.cwd = cwd
        self.out_queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        srv_log.info(
            'LSP-процесс запущен: %s (cwd=%s, pid=%s)',
            ' '.join(self.command),
            self.cwd,
            self._proc.pid,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def send(self, msg: dict) -> None:
        """Пишет сообщение в stdin процесса с Content-Length-фреймингом."""
        if self._proc is None or self._proc.stdin is None:
            return
        body = json.dumps(msg, separators=(',', ':')).encode('utf-8')
        header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
        self._proc.stdin.write(header + body)
        await self._proc.stdin.drain()

    async def stop(self) -> None:
        """Останавливает процесс (graceful terminate с переходом на kill)."""
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        except TimeoutError:
            srv_log.warning(
                'LSP-процесс не завершился, убиваю: %s', ' '.join(self.command)
            )
            self._proc.kill()
            await self._proc.wait()

    async def _read_loop(self) -> None:
        """Разбирает поток Content-Length и раскладывает сообщения в очередь."""
        assert self._proc is not None
        assert self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while True:
                header_raw = await stdout.readuntil(b'\r\n\r\n')
                length = self._parse_content_length(header_raw)
                if length is None:
                    srv_log.error('Некорректный заголовок от LSP-процесса, стоп')
                    break
                body = await stdout.readexactly(length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    srv_log.error(
                        'Невалидный JSON от LSP-процесса: %.200s',
                        body.decode(errors='replace'),
                    )
                    continue
                await self.out_queue.put(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except OSError:
            srv_log.exception('Ошибка чтения stdout LSP-процесса')
        srv_log.info('Поток LSP-процесса закрыт: %s', ' '.join(self.command))
        await self.out_queue.put(None)

    @staticmethod
    def _parse_content_length(header_raw: bytes) -> int | None:
        for line in header_raw.decode('ascii', errors='replace').splitlines():
            name, sep, value = line.partition(':')
            if sep and name.strip().lower() == 'content-length':
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    async def _drain_stderr(self) -> None:
        """Логирует stderr сервера в лог ``bcad.lsp``."""
        assert self._proc is not None
        assert self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            srv_log.debug('[LSP stderr] %s', line.decode(errors='replace').rstrip())
