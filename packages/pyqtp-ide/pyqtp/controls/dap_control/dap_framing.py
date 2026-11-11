"""Фрейминг и конструкторы сообщений DAP (этап 11, срез D1).

Debug Adapter Protocol ходит по тем же Content-Length-фреймам, что и LSP
(``lsp_process_wrapper.StdioLspProcess`` — тот же принцип для stdio-процесса
сервера). Здесь — чистые хелперы без asyncio и без Qt: кодирование/декодирование
фрейма и сборка типовых сообщений (запрос/ответ/событие/ответ хаба).

Формы сообщений (спецификация DAP):

* запрос:      ``{'seq', 'type': 'request', 'command', 'arguments'?}``
* событие:     ``{'seq', 'type': 'event', 'event', 'body'?}``
* ответ:       ``{'seq', 'type': 'response', 'request_seq', 'command',
  'success', 'message'?, 'body'?}``
"""

from __future__ import annotations

import json
from typing import Any

HEADER_TERMINATOR = b'\r\n\r\n'


def build_request(seq: int, command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Собрать DAP-запрос (``arguments`` по специкации обязателен, пустой dict ок)."""
    return {
        'seq': seq,
        'type': 'request',
        'command': command,
        'arguments': dict(arguments),
    }


def build_response(  # noqa: PLR0913 - полный набор полей DAP-ответа
    seq: int,
    request_seq: int,
    command: str,
    body: dict[str, Any],
    *,
    success: bool = True,
    message: str = '',
) -> dict[str, Any]:
    """Собрать DAP-ответ на запрос (для фейковых серверов в тестах)."""
    msg: dict[str, Any] = {
        'seq': seq,
        'type': 'response',
        'request_seq': request_seq,
        'command': command,
        'success': success,
    }
    if body:
        msg['body'] = dict(body)
    if message:
        msg['message'] = message
    return msg


def build_event(
    seq: int, event: str, body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать DAP-событие (для фейковых серверов в тестах)."""
    msg: dict[str, Any] = {'seq': seq, 'type': 'event', 'event': event}
    if body:
        msg['body'] = dict(body)
    return msg


def encode_message(msg: dict[str, Any]) -> bytes:
    """Сериализовать сообщение в один фрейм ``Content-Length`` + JSON-тело."""
    body = json.dumps(msg, separators=(',', ':')).encode('utf-8')
    header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
    return header + body


def parse_content_length(header_raw: bytes | str) -> int | None:
    """Длина тела из заголовочного блока; ``None`` — заголовков/длины нет.

    Регистр имени заголовка не важен; нечисловое значение — тоже ``None``
    (читающей стороне это сигнал остановить разбор потока).
    """
    if isinstance(header_raw, bytes):
        text = header_raw.decode('ascii', errors='replace')
    else:
        text = header_raw
    for line in text.splitlines():
        name, sep, value = line.partition(':')
        if sep and name.strip().lower() == 'content-length':
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None
