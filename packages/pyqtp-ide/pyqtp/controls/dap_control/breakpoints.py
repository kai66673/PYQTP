"""Проектные брейкпоинты (этап 11, срез D1) — чистый модуль без Qt.

Тот же подход, что у закладок (``bookmark_control/bookmarks.py``): набор
живёт в файле проекта ``.pyqtp/breakpoints.json``, координаты Monaco
(1-based), разбор толерантный, запись атомарная, пустой набор снимает файл и
пустой ``.pyqtp/`` (не трогая ``session.json``/``settings.json``/``launch.json``).

Формат файла (JSON):

.. code-block:: json

   {"breakpoints": [{"path": "/abs/path/a.py", "line": 12, "condition": "i == 3"}]}

Список отсортирован по ``(path, line)`` и без дублей; ключ ``condition``
(этап 11, срез D3) пишется только при непустом условии, при разборе
не-строка заменяется на ``''``. Лишние поля записей игнорируются, мусорные
записи пропускаются, нечитаемый файл воспринимается как пустой набор.
Состояние верификации (ответ ``setBreakpoints``) в файл НЕ пишется — это
свойство живой сессии, а не проекта.

Ограничение v1: номер строки брейкпоинта не «едет» вместе с правками текста
и не обновляется при переименовании файлов проекта (как у закладок 9.7).
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_DIR_NAME = '.pyqtp'
BREAKPOINTS_FILE_NAME = 'breakpoints.json'


@dataclass(frozen=True)
class Breakpoint:
    """Один брейкпоинт: путь файла, строка (1-based, Monaco) и условие.

    ``condition`` — выражение Python для условного брейкпоинта (этап 11 D3);
    пустая строка — брейкпоинт без условия.
    """

    path: str
    line: int
    condition: str = ''


def breakpoints_file_path(root: str | Path) -> Path:
    """Путь ``<root>/.pyqtp/breakpoints.json``."""
    return Path(root) / PROJECT_DIR_NAME / BREAKPOINTS_FILE_NAME


def read_breakpoints(root: str | Path) -> list[Breakpoint]:
    """Читает файл проекта; битый/нечитаемый — пустой набор."""
    try:
        text = breakpoints_file_path(root).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return []
    return parse_breakpoints(text)


def parse_breakpoints(text: str) -> list[Breakpoint]:
    """Толерантный разбор JSON-текста; мусорные записи пропускаются."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, dict):
        return []
    entries = raw.get('breakpoints')
    if not isinstance(entries, list):
        return []
    items: list[Breakpoint] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get('path')
        line = entry.get('line')
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            continue
        condition = entry.get('condition')
        if not isinstance(condition, str):
            condition = ''
        items.append(Breakpoint(path=path, line=line, condition=condition))
    return items


def serialize_breakpoints(items: list[Breakpoint]) -> str:
    """JSON канонического набора (сортировка по ``(path, line)`` + дедуп).

    Дедуп по ``(path, line)`` сохраняет ПЕРВОЕ условие пары (разные условия
    на одной строке — аномалия файла, второе теряется молча); ключ
    ``condition`` пишется только при непустом условии.
    """
    first: dict[tuple[str, int], str] = {}
    for item in items:
        first.setdefault((item.path, item.line), item.condition)
    entries = []
    for (path, line), condition in sorted(first.items()):
        entry: dict[str, object] = {'path': path, 'line': line}
        if condition:
            entry['condition'] = condition
        entries.append(entry)
    return json.dumps({'breakpoints': entries}, ensure_ascii=False, indent=2) + '\n'


def write_breakpoints(root: str | Path, items: list[Breakpoint]) -> None:
    """Атомарно пишет набор; пустой набор снимает файл и пустой ``.pyqtp/``."""
    directory = Path(root) / PROJECT_DIR_NAME
    if not items:
        with suppress(OSError):
            breakpoints_file_path(root).unlink()
        with suppress(OSError):
            directory.rmdir()
        return
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix='.breakpoints-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(serialize_breakpoints(items))
        Path(tmp_name).replace(breakpoints_file_path(root))
    except BaseException:
        with suppress(OSError):
            Path(tmp_name).unlink()
        raise


def filter_breakpoints(items: list[Breakpoint]) -> tuple[list[Breakpoint], list[Breakpoint]]:
    """Разделяет на «файл есть» и «файл пропал» (срезка при открытии проекта)."""
    kept: list[Breakpoint] = []
    skipped: list[Breakpoint] = []
    for item in items:
        if Path(item.path).is_file():
            kept.append(item)
        else:
            skipped.append(item)
    return kept, skipped


def toggle_breakpoint(items: list[Breakpoint], path: str, line: int) -> list[Breakpoint]:
    """Добавляет/снимает брейкпоинт ``(path, line)`` (возвращает новый набор).

    Пары сравниваются по ``(path, line)`` без условия (этап 11 D3): снятие
    убирает брейкпоинт вместе с его условием.
    """
    key = (str(path), int(line))
    if any((item.path, item.line) == key for item in items):
        return [item for item in items if (item.path, item.line) != key]
    return [*items, Breakpoint(path=key[0], line=key[1])]


def set_breakpoint_condition(
    items: list[Breakpoint], path: str, line: int, condition: str,
) -> list[Breakpoint]:
    """Ставит/заменяет условие брейкпоинта ``(path, line)`` (новый набор).

    ``condition=''`` на существующем брейкпоинте СНИМАЕТ условие (сам
    брейкпоинт остаётся); брейкпоинта нет + пустое условие — без изменений;
    брейкпоинта нет + непустое условие — добавляет с условием.
    """
    key = (str(path), int(line))
    condition = str(condition)
    if not any((item.path, item.line) == key for item in items) and not condition:
        return list(items)
    updated = [item for item in items if (item.path, item.line) != key]
    updated.append(Breakpoint(path=key[0], line=key[1], condition=condition))
    return updated


def remove_breakpoint(items: list[Breakpoint], path: str, line: int) -> list[Breakpoint]:
    """Снимает брейкпоинт ``(path, line)`` (нет — без изменений)."""
    key = (str(path), int(line))
    return [item for item in items if (item.path, item.line) != key]


def breakpoints_for(items: list[Breakpoint], path: str) -> list[int]:
    """Отсортированные строки брейкпоинтов одного файла (для Monaco)."""
    return sorted(item.line for item in items if item.path == path)


def breakpoint_entries_for(items: list[Breakpoint], path: str) -> list[dict[str, Any]]:
    """Записи брейкпоинтов одного файла для Monaco/``setBreakpoints`` (D3).

    ``[{'line': N}, ...]`` c ключом ``condition`` только при непустом условии
    (debugpy не ждёт пустых условий); строки отсортированы.
    """
    entries = []
    for item in items:
        if item.path != path:
            continue
        entry: dict[str, Any] = {'line': item.line}
        if item.condition:
            entry['condition'] = item.condition
        entries.append(entry)
    return sorted(entries, key=lambda entry: int(entry['line']))
