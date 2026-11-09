"""Проектные закладки (этап 9.7): строки-закладки в файлах проекта.

Чистый модуль (без GUI и без Qt): сериализация/разбор набора закладок и
размещение их файла в каталоге проекта ``.pyqtp/bookmarks.json`` — закладки
относятся к проекту и живут рядом с проектом, а не в QSettings (тот же
подход, что у сессии ``project_session.py`` и настроек ``project_settings.py``).

Формат файла (JSON, разбор толерантный):

.. code-block:: json

   {"bookmarks": [{"path": "/abs/path/a.py", "line": 12}]}

Список отсортирован по ``(path, line)``; координаты Monaco (``line`` —
1-based). Лишние поля файла игнорируются, мусорные записи пропускаются,
нечитаемый файл воспринимается как пустой набор закладок.

Ограничение v1: номер строки закладки не «едет» вместе с правками текста
(вставка/удаление строк сдвигает текст под закладкой) и не обновляется при
переименовании файлов проекта.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from pyqtp.controls.project_control.project_session import PROJECT_DIR_NAME

BOOKMARKS_FILE_NAME = 'bookmarks.json'


@dataclass(frozen=True, order=True)
class Bookmark:
    """Одна закладка: файл проекта и 1-based номер строки (Monaco)."""

    path: str
    line: int


def bookmarks_file(root: str | Path) -> Path:
    """Путь файла закладок для корня проекта."""
    return Path(root) / PROJECT_DIR_NAME / BOOKMARKS_FILE_NAME


def serialize_bookmarks(items: list[Bookmark]) -> str:
    """JSON-текст набора (сортировка и дедупликация — на записи)."""
    entries = [{'path': item.path, 'line': item.line} for item in sorted(set(items))]
    return json.dumps({'bookmarks': entries}, ensure_ascii=False, indent=2) + '\n'


def parse_bookmarks(raw: object) -> list[Bookmark]:
    """Толерантный разбор decoded-JSON: пропускаем мусор, а не падаем.

    - не-словарь/не-список -> пустой набор;
    - запись — только словарь с непустой строкой ``path`` и натуральным
      (>=1) числом ``line``;
    - дедупликация и сортировка по ``(path, line)``.
    """
    if not isinstance(raw, dict):
        return []
    raw_items = raw.get('bookmarks')
    if not isinstance(raw_items, list):
        return []
    items: set[Bookmark] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        path = entry.get('path')
        line = entry.get('line')
        if not (isinstance(path, str) and path):
            continue
        # bool — подкласс int в Python, как закладка line=-1/0 — мусор
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            continue
        items.add(Bookmark(path=path, line=line))
    return sorted(items)


def deserialize_bookmarks(text: str) -> list[Bookmark]:
    """Разбор JSON-текста; битый JSON -> пустой набор."""
    try:
        raw = json.loads(text)
    except ValueError:
        return []
    return parse_bookmarks(raw)


def read_bookmarks(root: str | Path) -> list[Bookmark]:
    """Читает закладки из ``<root>/.pyqtp/bookmarks.json`` (нет/битый — пусто)."""
    path = bookmarks_file(root)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return []
    except OSError as exc:
        message = f'Не удалось прочитать закладки проекта: {exc}'
        raise OSError(message) from exc
    return deserialize_bookmarks(text)


def write_bookmarks(root: str | Path, items: list[Bookmark]) -> None:
    """Пишет закладки в ``<root>/.pyqtp/bookmarks.json`` (атомарно, через tmp).

    Пустой набор снимает файл (и каталог ``.pyqtp/``, если он стал пуст) —
    при закрытии последнего закладочного проекта рядом с проектом мусора
    оставаться не должно; соседние файлы проекта (session.json,
    settings.json) при этом не трогаются.
    """
    if not items:
        clear_bookmarks(root)
        return
    path = bookmarks_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{BOOKMARKS_FILE_NAME}.', suffix='.tmp'
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(serialize_bookmarks(items))
        tmp_path.replace(path)
    except OSError:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def clear_bookmarks(root: str | Path) -> None:
    """Убирает файл закладок (и каталог ``.pyqtp/``, если он стал пуст)."""
    with suppress(FileNotFoundError):
        bookmarks_file(root).unlink()
    project_dir = Path(root) / PROJECT_DIR_NAME
    with suppress(OSError):
        # каталог не пуст (session.json/settings.json) или отсутствует —
        # так и задумано
        project_dir.rmdir()


def filter_bookmarks(items: list[Bookmark]) -> tuple[list[Bookmark], list[Bookmark]]:
    """Оставляет закладки реально существующих файлов.

    Возвращает ``(оставшиеся, пропущенные)`` — пропущенные сохраняют
    формат записей, чтобы их можно было показать в журнале.
    """
    kept = [item for item in items if Path(item.path).is_file()]
    skipped = [item for item in items if not Path(item.path).is_file()]
    return kept, skipped


def toggle_bookmark(items: list[Bookmark], path: str, line: int) -> list[Bookmark]:
    """Переключает закладку: есть — снимает, нет — ставит.

    Возвращает новый список (отсортированный и без дублей); вход не меняется.
    """
    entry = Bookmark(path=path, line=line)
    updated = [item for item in items if item != entry]
    if len(updated) == len(items):
        updated.append(entry)
    return sorted(updated)


def bookmarks_for(items: list[Bookmark], path: str) -> list[int]:
    """Отсортированные строки закладок одного файла."""
    return sorted(item.line for item in items if item.path == path)


def next_bookmark_line(
    lines: list[int], current: int, *, forward: bool = True
) -> int | None:
    """Следующая (``forward=False`` — предыдущая) строка закладки с циклом.

    ``current`` — строка каретки; за ``(None,)`` уходит пустой набор строк.
    Если за текущей строкой ничего нет — заворачиваемся на первое/последнее.
    """
    if not lines:
        return None
    if forward:
        for line in lines:
            if line > current:
                return line
        return lines[0]
    for line in reversed(lines):
        if line < current:
            return line
    return lines[-1]
