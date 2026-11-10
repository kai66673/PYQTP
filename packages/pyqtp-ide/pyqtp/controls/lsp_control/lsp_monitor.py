"""Монитор LSP (этап 10.3): ring-buffer трафика запросов/ответов LspHub.

Чистый модуль: только stdlib, без Qt и без websockets — пишется из потока
event-loop хаба под блокировкой, читается из GUI-потока.

Что пишется (схема «LSP-поток» из старого iide):
- строки протокола между хабом и процессом сервера: направления
  ``DIRECTION_TO`` (хаб отправил в процесс) и ``DIRECTION_FROM`` (процесс
  прислал в хаб);
- сообщения, которые породил сам хаб (ответы на initialize/shutdown клиента,
  ответы на запросы сервера вроде workspace/configuration, ошибки хаба) —
  направление ``DIRECTION_HUB``: процесс их не видел, это трафик «хаб ↔ редактор».

Проглоченные хабом клиентские нотификации (didChange и т.п.) НЕ пишутся —
сервер их не получил; вместо пишется реальная отправка, если хаб что-то выпустил.
params нотификаций didOpen/didChange содержат полный текст документа — в
сводке остаётся только uri+version.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

#: Хаб отправил сообщение процессу сервера.
DIRECTION_TO = '→'
#: Процесс сервера отправил сообщение хабу.
DIRECTION_FROM = '←'
#: Сообщение породил сам хаб (ответ клиенту/серверу), процесс его не видел.
DIRECTION_HUB = '↔'

#: Направления для комбика фильтрации в панели.
DIRECTIONS = (DIRECTION_TO, DIRECTION_FROM, DIRECTION_HUB)

KIND_REQUEST = 'запрос'
KIND_HUB_REQUEST = 'запрос хаба'
KIND_SERVER_REQUEST = 'запрос сервера'
KIND_CLIENT_REQUEST = 'запрос клиента'
KIND_RESPONSE = 'ответ'
KIND_HUB_REPLY = 'ответ хаба'
KIND_ERROR = 'ошибка'
KIND_NOTIFICATION = 'нотификация'
KIND_SERVER_NOTIFICATION = 'нотификация сервера'

#: Типы для комбика фильтрации в панели.
KINDS = (
    KIND_REQUEST,
    KIND_HUB_REQUEST,
    KIND_SERVER_REQUEST,
    KIND_CLIENT_REQUEST,
    KIND_RESPONSE,
    KIND_HUB_REPLY,
    KIND_ERROR,
    KIND_NOTIFICATION,
    KIND_SERVER_NOTIFICATION,
)

#: Типы, которые считаются «шумными» нотификациями и по умолчанию скрыты.
NOTIFICATION_KINDS = frozenset({KIND_NOTIFICATION, KIND_SERVER_NOTIFICATION})

#: Максимальное число записей в кольце (по умолчанию).
RING_CAPACITY = 1000

#: Максимальная длина строки «Детали» в панели.
SUMMARY_MAX_CHARS = 160

#: Методы, чьи params содержат полный текст документа — их не дампим.
_TEXT_HEAVY_METHODS = frozenset({
    'textDocument/didOpen',
    'textDocument/didChange',
    'textDocument/willSave',
    'textDocument/willSaveWaitUntil',
    'textDocument/didSave',
})


def _clip(text: str) -> str:
    """Срезает строку до SUMMARY_MAX_CHARS с многоточием."""
    if len(text) > SUMMARY_MAX_CHARS:
        return text[:SUMMARY_MAX_CHARS - 1] + '…'
    return text


@dataclass(frozen=True)
class TrafficEvent:
    """Сырое событие трафика до попадания в кольцо (без seq/ts)."""

    server: str
    direction: str
    kind: str
    method: str
    msg_id: int | str | None
    summary: str


@dataclass(frozen=True)
class TrafficRecord:
    """Запись в кольце монитора (что видит панель/консоль)."""

    seq: int
    ts: float
    server: str
    direction: str
    kind: str
    method: str
    msg_id: int | str | None
    summary: str

    def as_dict(self) -> dict[str, Any]:
        """Снимок для console-API."""
        return {
            'seq': self.seq,
            'time': format_time(self.ts),
            'server': self.server,
            'direction': self.direction,
            'kind': self.kind,
            'method': self.method,
            'msg_id': self.msg_id,
            'summary': self.summary,
        }


class TrafficRing:
    """Потокобезопасное кольцо записей монитора.

    add_event вызывается из потока event-loop хаба, records/clear — из GUI.
    """

    def __init__(self, capacity: int = RING_CAPACITY) -> None:
        self._events: deque[TrafficRecord] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_seq = 1

    def add_event(self, ev: TrafficEvent, ts: float | None = None) -> TrafficRecord:
        """Добавляет событие в кольцо (время снимается при добавлении)."""
        with self._lock:
            record = TrafficRecord(
                seq=self._next_seq,
                ts=ts if ts is not None else time.time(),
                server=ev.server,
                direction=ev.direction,
                kind=ev.kind,
                method=ev.method,
                msg_id=ev.msg_id,
                summary=ev.summary,
            )
            self._next_seq += 1
            self._events.append(record)
            return record

    def records(self) -> tuple[TrafficRecord, ...]:
        """Снимок всех записей от старых к новым."""
        with self._lock:
            return tuple(self._events)

    def total_seen(self) -> int:
        """Сколько всего записей появилось с момента создания кольца."""
        with self._lock:
            return self._next_seq - 1

    def clear(self) -> None:
        """Очищает кольцо (нумерация seq при этом продолжается)."""
        with self._lock:
            self._events.clear()


# ---------------------------------------------------------------- сводки

def summarize_params(method: str, params: Any) -> str:
    """Короткая сводка params (без дампа полного текста документа)."""
    if params is None:
        return ''
    if method in _TEXT_HEAVY_METHODS and isinstance(params, dict):
        doc = params.get('textDocument')
        if isinstance(doc, dict):
            uri = str(doc.get('uri', ''))
            version = doc.get('version')
            return f'{uri} v{version}' if version is not None else uri
    try:
        dumped = json.dumps(params, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        dumped = str(params)
    return _clip(dumped)


def summarize_error(error: Any) -> str:
    """Сводка ошибки JSON-RPC (code + message)."""
    if isinstance(error, dict):
        code = error.get('code')
        message = error.get('message')
        parts = [str(part) for part in (code, message) if part is not None]
        return _clip(' '.join(parts))
    return _clip(str(error))


def summarize_result(result: Any) -> str:
    """Короткая сводка результата ответа (без полного дампа)."""
    if result is None:
        return ''
    if isinstance(result, list):
        return f'массив: {len(result)} эл.'
    if isinstance(result, dict):
        keys = sorted(str(key) for key in result)
        return _clip('объект: {' + ', '.join(keys) + '}')
    if isinstance(result, (str, int, float, bool)):
        return _clip(str(result))
    return _clip(f'{type(result).__name__}: {result!r}')


# ------------------------------------------------- фабрики событий по точкам

def ev_request(server: str, msg_id: int | str | None, method: str, params: Any,
               *, from_hub: bool = False) -> TrafficEvent:
    """Исходящий запрос процессу: от имени клиента или локальный запрос хаба."""
    return TrafficEvent(
        server=server,
        direction=DIRECTION_TO,
        kind=KIND_HUB_REQUEST if from_hub else KIND_REQUEST,
        method=method,
        msg_id=msg_id,
        summary=summarize_params(method, params),
    )


def ev_notification_to(server: str, method: str, params: Any) -> TrafficEvent:
    """Исходящая нотификация процессу (реально выпущенная хабу на провод)."""
    return TrafficEvent(
        server=server,
        direction=DIRECTION_TO,
        kind=KIND_NOTIFICATION,
        method=method,
        msg_id=None,
        summary=summarize_params(method, params),
    )


def ev_from_server(msg: dict, method: str = '',
                   kind: str = '') -> TrafficEvent:
    """Входящее сообщение от процесса сервера (по ключам JSON-RPC).

    ``method`` для ответов передаёт вызывающий (из таблицы pending-запросов) —
    в ответе метода нет, а подписать строку надо. ``kind`` переопределяет тип
    (локальный запрос хаба помечается «ответ хаба», а не «ответ»).
    """
    msg_method = str(msg.get('method', '')) or method
    msg_id = msg.get('id')
    if msg.get('method'):
        if msg_id is not None:
            kind = kind or KIND_SERVER_REQUEST
        else:
            kind = kind or KIND_SERVER_NOTIFICATION
        summary = summarize_params(msg_method, msg.get('params'))
    elif msg.get('error') is not None:
        kind = kind or KIND_ERROR
        summary = summarize_error(msg['error'])
    else:
        kind = kind or KIND_RESPONSE
        summary = summarize_result(msg.get('result'))
    return TrafficEvent(
        server='',
        direction=DIRECTION_FROM,
        kind=kind,
        method=msg_method,
        msg_id=msg_id,
        summary=summary,
    )


def ev_hub_reply_to_server(server: str, method: str, msg_id: int | str | None,
                           summary: str = '') -> TrafficEvent:
    """Ответ хаба на запрос сервера (workspace/configuration и т.п.) — уходит
    процессу на провод, поэтому направление «хаб → процесс».
    """
    return TrafficEvent(
        server=server,
        direction=DIRECTION_TO,
        kind=KIND_HUB_REPLY,
        method=method,
        msg_id=msg_id,
        summary=summary,
    )


def ev_hub_reply_to_client(server: str, method: str, msg_id: int | str | None,
                           summary: str = '') -> TrafficEvent:
    """Ответ, порождённый самим хабом для редактора (initialize/shutdown) —
    процесс его не видел, направление «хаб ↔ редактор».
    """
    return TrafficEvent(
        server=server,
        direction=DIRECTION_HUB,
        kind=KIND_HUB_REPLY,
        method=method,
        msg_id=msg_id,
        summary=summary,
    )


def ev_client_request(server: str, method: str, msg_id: int | str | None,
                      params: Any) -> TrafficEvent:
    """Запрос клиента, который хаб обслужил сам (на провод не уходил)."""
    return TrafficEvent(
        server=server,
        direction=DIRECTION_HUB,
        kind=KIND_CLIENT_REQUEST,
        method=method,
        msg_id=msg_id,
        summary=summarize_params(method, params),
    )


def ev_hub_error(server: str, msg_id: int | str | None, error: Any,
                 method: str = '') -> TrafficEvent:
    """Ошибка, порождённая самим хабом (сбой запуска сервера и т.п.)."""
    return TrafficEvent(
        server=server,
        direction=DIRECTION_HUB,
        kind=KIND_ERROR,
        method=method,
        msg_id=msg_id,
        summary=summarize_error(error),
    )


def format_time(ts: float) -> str:
    """Время записи для колонки «Время» (ЧЧ:ММ:СС.дсс)."""
    local = time.localtime(ts)
    return (f'{local.tm_hour:02d}:{local.tm_min:02d}:{local.tm_sec:02d}'
            f'.{int((ts % 1) * 1000):03d}')


# --------------------------------------------------------------- фильтр

@dataclass(frozen=True)
class MonitorFilter:
    """Фильтр записей монитора (пустые значения = без ограничения)."""

    text: str = ''
    server: str = ''
    direction: str = ''
    kind: str = ''
    show_notifications: bool = False


def filter_records(records: tuple[TrafficRecord, ...] | list[TrafficRecord],
                   flt: MonitorFilter) -> tuple[TrafficRecord, ...]:
    """Чистая фильтрация записей — используют и панель, и тесты."""
    needle = flt.text.casefold()
    result = []
    explicit_notifications = flt.kind in NOTIFICATION_KINDS
    for rec in records:
        if (not flt.show_notifications and not explicit_notifications
                and rec.kind in NOTIFICATION_KINDS):
            # явный выбор «нотификация» в комбике типа перекрывает галку
            continue
        if flt.server and rec.server != flt.server:
            continue
        if flt.direction and rec.direction != flt.direction:
            continue
        if flt.kind and rec.kind != flt.kind:
            continue
        if needle:
            haystack = f'{rec.method} {rec.summary} {rec.server} {rec.msg_id}'
            if needle not in haystack.casefold():
                continue
        result.append(rec)
    return tuple(result)
