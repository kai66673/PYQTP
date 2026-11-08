"""Применение LSP WorkspaceEdit на python-стороне (этап 8.2).

Monaco-овский ``StandaloneBulkEditService.apply`` умеет править только модели,
которые уже загружены в этом же редакторе («bad edit - model not found» на
чужом ресурсе), и совсем не умеет ресурсные операции (Create/Rename/Delete).
Поэтому JS-сторона (``splitWorkspaceEdit`` в qtmonaco backend) режет ответ
сервера на локальную часть (текущая модель — применяет сам Monaco) и чужую,
которая приходит сюда как ``{'origin', 'sourceUri', 'workspaceEdit':
{'documentChanges': [...]}}``.

Здесь — чистые хелперы без Qt: разбор порядка documentChanges, применение
TextEdit к строке (UTF-16 колонки LSP) и файловые операции с опциями
``overwrite``/``ignoreIfExists``/``recursive``. Оркестрация (открытый документ
-> сообщение его редактору, закрытый -> правка диска) — в
``DocumentManager.on_workspace_edit_requested``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

# kind'и ресурсных операций LSP 3.16 (AnnotatedTextEdit/ResourceOperation)
CREATE_FILE = 1
RENAME_FILE = 2
DELETE_FILE = 3

# верхняя граница BMP: символы выше — суррогатные пары (2 единицы UTF-16)
_MAX_BMP_CODEPOINT = 0xFFFF


def path_from_file_uri(uri: str) -> Path | None:
    """file:// uri -> локальный путь (None для не-файловых схем)."""
    parsed = urlparse(str(uri))
    if parsed.scheme != 'file' or not parsed.path:
        return None
    return Path(unquote(parsed.path))


def normalize_document_changes(workspace_edit: Any) -> list[dict]:
    """Список documentChanges из WorkspaceEdit.

    JS-сторона всегда отдаёт ``documentChanges`` (форма ``changes`` там уже
    нормализована), но хелпер принимает и «сырую» форму ``changes`` — на случай
    прямых вызовов из тестов/скриптов; порядок documentChanges сохраняется
    как есть (он осмысленен: сервер мог создать файл и сразу его отредактировать).
    """
    if not isinstance(workspace_edit, dict):
        return []
    changes: list[dict] = []
    raw_changes = workspace_edit.get('changes')
    if isinstance(raw_changes, dict):
        for uri, edits in raw_changes.items():
            if isinstance(edits, list):
                changes.append({'textDocument': {'uri': uri}, 'edits': edits})
    document_changes = workspace_edit.get('documentChanges')
    if isinstance(document_changes, list):
        changes.extend(c for c in document_changes if isinstance(c, dict))
    return changes


def is_text_document_edit(change: dict) -> bool:
    """TextDocumentEdit (в т.ч. AnnotatedTextEdit — аннотации игнорируем)."""
    return (
        isinstance(change.get('textDocument'), dict)
        and isinstance(change.get('edits'), list)
    )


def _utf16_col_to_py(line: str, unit_col: int) -> int:
    """Колонка LSP (UTF-16 кодовые единицы, 0-based) -> индекс в python-строке.

    Python считает символы кодовыми точками, LSP — единицами UTF-16: для
    символов за пределами BMP (эмодзи) колонки расходятся. Проход по символам
    с подсчётом их UTF-16 длин; попадание в середину суррогатной пары
    округляется назад (безопаснее, чем резать пару).
    """
    if unit_col <= 0:
        return 0
    units = 0
    for py_index, char in enumerate(line):
        units += 2 if ord(char) > _MAX_BMP_CODEPOINT else 1
        if units >= unit_col:
            return py_index + 1
    return len(line)


def apply_text_edits_to_text(text: str, edits: list[dict]) -> str:
    """Применить список LSP TextEdit ({range:{start,end}, newText}) к тексту.

    Координаты 0-based, колонки в единицах UTF-16. Правки внутри одного
    документа по спецификации не пересекаются; применяем с хвоста к началу,
    чтобы смещения уже применённых правок не сдвигали позиции последующих.
    Некорректные (перевёрнутые/выходящие за границы) правки молча отбрасываются:
    частичное применение лучше отказа всей операции (список ограничений v1 —
    в docs/IDE_ROADMAP.md).
    """
    if not edits:
        return text

    lines = text.split('\n')

    def to_offset(line_index: int, unit_col: int) -> int | None:
        if not 0 <= line_index < len(lines):
            return None
        line = lines[line_index]
        py_col = _utf16_col_to_py(line, unit_col)
        if py_col > len(line):
            return None
        return sum(len(prev) + 1 for prev in lines[:line_index]) + py_col

    prepared: list[tuple[int, int, str]] = []
    for edit in edits:
        rng = edit.get('range') or {}
        start = rng.get('start') or {}
        end = rng.get('end') or {}
        if not all(isinstance(v, int) for v in (start.get('line'), start.get('character'), end.get('line'), end.get('character'))):
            continue
        start_offset = to_offset(start['line'], start['character'])
        end_offset = to_offset(end['line'], end['character'])
        if start_offset is None or end_offset is None or end_offset < start_offset:
            continue
        prepared.append((start_offset, end_offset, str(edit.get('newText', ''))))

    # с хвоста к началу; при равенстве начала — с более длинным диапазоном
    for start_offset, end_offset, new_text in sorted(prepared, reverse=True):
        text = text[:start_offset] + new_text + text[end_offset:]
    return text


def create_file(path: Path, options: dict | None, contents: str | None = None) -> bool:
    """CreateFile: создать файл (опционально с содержимым).

    Возвращает True, если файл реально создан/перезаписан. ``ignoreIfExists``
    пропускает существующий, без него существующий перезаписывается только с
    ``overwrite``.
    """
    options = options or {}
    exists = path.exists()
    if exists:
        if options.get('ignoreIfExists'):
            return False
        if not options.get('overwrite'):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents if contents is not None else '', encoding='utf-8')
    return True


def rename_file(old_path: Path, new_path: Path, options: dict | None) -> bool:
    """RenameFile: переместить файл (с опциями overwrite/ignoreIfExists)."""
    options = options or {}
    if not old_path.is_file():
        return False
    if new_path.exists():
        if options.get('ignoreIfExists'):
            return False
        if not options.get('overwrite'):
            return False
        new_path.unlink()
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.replace(new_path)
    return True


def delete_file(path: Path, options: dict | None) -> bool:
    """DeleteFile: удалить файл (options.recursive для не пустых каталогов)."""
    options = options or {}
    if path.is_dir():
        if not options.get('recursive'):
            return False
        shutil.rmtree(path, ignore_errors=False)
        return True
    if not path.exists():
        return False
    path.unlink()
    return True
