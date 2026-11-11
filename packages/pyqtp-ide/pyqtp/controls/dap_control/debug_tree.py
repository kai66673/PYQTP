"""Чистые модели панелей отладки (этап 11, срез D2): стек/переменные/потоки.

Разбор ответов DAP ``stackTrace``/``scopes``/``variables``/``threads`` в
неизменяемые строки для панелей («Стек вызовов»/«Переменные»). Модуль без Qt
и процессов — координаты Monaco/LSP 1-based как в остальном IDE, разбор
толерантный (кривые поля адаптера не роняют панель).

``variablesReference`` — уникальный идентификатор деревa переменных на время
сессии (спека DAP); для листовой переменной равен 0 — «раскрывать нечего».
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# имя кадра/переменной при битых полях ответа
_FALLBACK_NAME = '???'


@dataclass(frozen=True)
class FrameRow:
    """Кадр ``stackTrace``: id + имя + файл/строка (path '' — без файла)."""

    frame_id: int
    name: str
    path: str
    line: int
    column: int


@dataclass(frozen=True)
class ThreadRow:
    """Поток из ``threads``: id + имя."""

    thread_id: int
    name: str


@dataclass(frozen=True)
class ScopeRow:
    """Область видимости из ``scopes`` (лист дерева переменных)."""

    scope_id: int  # variablesReference области — ключ запроса variables
    name: str
    expensive: bool
    named_variables: int
    indexed_variables: int


@dataclass(frozen=True)
class VariableRow:
    """Переменная из ``variables``; ``children_ref`` 0 — листовая."""

    name: str
    value: str
    type_name: str
    children_ref: int
    evaluate_name: str
    named_variables: int
    indexed_variables: int


def _int(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _str(value: Any, default: str = '') -> str:
    return value if isinstance(value, str) else default


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def frames_from_response(body: dict[str, Any]) -> list[FrameRow]:
    """``StackFramesResponse`` -> строки кадра; порядок адаптера сохранён."""
    rows: list[FrameRow] = []
    for item in _dict_list(body.get('stackFrames')):
        frame_id = item.get('id')
        if not isinstance(frame_id, int):
            continue  # кадр без id нельзя выбрать/обновить — пропускаем
        source = item.get('source')
        path = _str(source.get('path')) if isinstance(source, dict) else ''
        rows.append(FrameRow(
            frame_id=frame_id,
            name=_str(item.get('name'), _FALLBACK_NAME),
            path=path,
            line=_int(item.get('line')),
            column=_int(item.get('column'), 1),
        ))
    return rows


def threads_from_response(body: dict[str, Any]) -> list[ThreadRow]:
    """``ThreadsResponse`` -> строки потоков."""
    rows: list[ThreadRow] = []
    for item in _dict_list(body.get('threads')):
        thread_id = item.get('id')
        if not isinstance(thread_id, int):
            continue
        rows.append(ThreadRow(thread_id=thread_id,
                              name=_str(item.get('name'), _FALLBACK_NAME)))
    return rows


def scopes_from_response(body: dict[str, Any]) -> list[ScopeRow]:
    """``ScopesResponse`` -> строки областей (без дочерних переменных)."""
    rows: list[ScopeRow] = []
    for item in _dict_list(body.get('scopes')):
        ref = item.get('variablesReference')
        if not isinstance(ref, int) or ref == 0:
            continue  # области без содержимого в дереве показывать нечего
        rows.append(ScopeRow(
            scope_id=ref,
            name=_str(item.get('name'), _FALLBACK_NAME),
            expensive=bool(item.get('expensive')),
            named_variables=_int(item.get('namedVariables')),
            indexed_variables=_int(item.get('indexedVariables')),
        ))
    return rows


def variables_from_response(body: dict[str, Any]) -> list[VariableRow]:
    """``VariablesResponse`` -> строки переменных одной родительской ячейки."""
    rows: list[VariableRow] = []
    for item in _dict_list(body.get('variables')):
        name = _str(item.get('name'))
        if not name:
            continue  # безымянную переменную ни показать, ни запросить нельзя
        ref = item.get('variablesReference')
        rows.append(VariableRow(
            name=name,
            value=_str(item.get('value')),
            type_name=_str(item.get('type')),
            children_ref=ref if isinstance(ref, int) else 0,
            # evaluateName адаптер даёт не всегда — имя годится для примитивов
            evaluate_name=_str(item.get('evaluateName'), name),
            named_variables=_int(item.get('namedVariables')),
            indexed_variables=_int(item.get('indexedVariables')),
        ))
    return rows


def frame_reveal_payload(frame: FrameRow) -> dict[str, Any] | None:
    """Payload для ``open_definition_uri`` (тот же формат, что LSP/10.2).

    ``None`` — кадр без файла или файла нет на диске (систные кадры debugpy,
    decompiled-источники): вскрывать нечего.
    """
    if not frame.path or frame.line < 1:
        return None
    path = Path(frame.path)
    if not path.is_file():
        return None
    try:
        uri = path.as_uri()
    except ValueError:
        return None
    return {
        'uri': uri,
        'range': {'startLineNumber': frame.line, 'startColumn': frame.column},
    }


def frame_display(frame: FrameRow, root: Path | None) -> str:
    """Строка кадра для панели: ``имя — файл:строка`` (путь relativно корня)."""
    if not frame.path:
        return f'{frame.name} [без источника]'
    label = frame.path
    if root is not None:
        try:
            label = str(Path(frame.path).relative_to(root))
        except ValueError:
            label = frame.path
    return f'{frame.name} — {label}:{frame.line}'
