"""Проектные брейкпоинты (этап 11, срез D1) — чистый модуль без Qt.

Тот же подход, что у закладок (``bookmark_control/bookmarks.py``): набор
живёт в файле проекта ``.pyqtp/breakpoints.json``, координаты Monaco
(1-based), разбор толерантный, запись атомарная, пустой набор снимает файл и
пустой ``.pyqtp/`` (не трогая ``session.json``/``settings.json``/``launch.json``).

Формат файла (JSON):

.. code-block:: json

   {"breakpoints": [{"path": "/abs/path/a.py", "line": 12}]}

Список отсортирован по ``(path, line)`` и без дублей; лишние поля записей
игнорируются, мусорные записи пропускаются, нечитаемый файл воспринимается
как пустой набор. Состояние верификации (ответ ``setBreakpoints``) в файл НЕ
пишется — это свойство живой сессии, а не проекта.

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

PROJECT_DIR_NAME = '.pyqtp'
BREAKPOINTS_FILE_NAME = 'breakpoints.json'


@dataclass(frozen=True)
class Breakpoint:
    """Один брейкпоинт: абсолютный путь файла и строка (1-based, Monaco)."""

    path: str
    line: int


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
        items.append(Breakpoint(path=path, line=line))
    return items


def serialize_breakpoints(items: list[Breakpoint]) -> str:
    """JSON канонического набора (сортировка по ``(path, line)`` + дедуп)."""
    unique = sorted({(item.path, item.line) for item in items})
    payload = {'breakpoints': [{'path': path, 'line': line} for path, line in unique]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + '\n'


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
    """Добавляет/снимает брейкпоинт ``(path, line)`` (возвращает новый набор)."""
    entry = Breakpoint(path=path, line=int(line))
    if entry in items:
        return [item for item in items if item != entry]
    return [*items, entry]


def breakpoints_for(items: list[Breakpoint], path: str) -> list[int]:
    """Отсортированные строки брейкпоинтов одного файла (для Monaco)."""
    return sorted(item.line for item in items if item.path == path)
