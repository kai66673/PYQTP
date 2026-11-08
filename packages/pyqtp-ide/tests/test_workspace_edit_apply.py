"""Юнит-тесты чистых хелперов workspace_edit (этап 8.2, без Qt/LSP).

Координаты LSP: 0-based строки, колонки в единицах UTF-16 — ровно то, что
применяет python-сторона к закрытым файлам и (после конверсии) Monaco.
"""

from __future__ import annotations

from pathlib import Path

from pyqtp.controls.doc_control.workspace_edit import (
    apply_text_edits_to_text,
    create_file,
    delete_file,
    normalize_document_changes,
    path_from_file_uri,
    rename_file,
)


def _te(start_line: int, start_char: int, end_line: int, end_char: int, new_text: str) -> dict:
    return {
        'range': {
            'start': {'line': start_line, 'character': start_char},
            'end': {'line': end_line, 'character': end_char},
        },
        'newText': new_text,
    }


# --- apply_text_edits_to_text ---

def test_apply_single_insert_at_line_start() -> None:
    text = 'import sys\n\nprint(sys.path)\n'
    result = apply_text_edits_to_text(text, [_te(0, 0, 0, 0, 'import os\n')])
    assert result == 'import os\nimport sys\n\nprint(sys.path)\n'


def test_apply_multiple_non_overlapping_and_reverse_order() -> None:
    # правки в массиве могут идти в любом порядке — применяем с хвоста
    text = 'aaa\nbbb\nccc\n'
    edits = [
        _te(2, 0, 2, 3, 'CCC'),
        _te(0, 0, 0, 3, 'AAA'),
        _te(1, 0, 1, 3, 'BBB'),
    ]
    assert apply_text_edits_to_text(text, edits) == 'AAA\nBBB\nCCC\n'


def test_apply_replacement_and_deletion() -> None:
    text = 'x = calc(1, 2)\ny = calc(3, 4)\n'
    edits = [
        _te(0, 4, 0, 8, 'compute'),  # calc -> compute
        _te(1, 4, 1, 8, ''),  # удаление calc
    ]
    assert apply_text_edits_to_text(text, edits) == (
        'x = compute(1, 2)\ny = (3, 4)\n'
    )


def test_apply_multiline_range() -> None:
    text = 'one\ntwo\nthree\n'
    result = apply_text_edits_to_text(text, [_te(0, 3, 2, 5, '!\n2\n3')])
    assert result == 'one!\n2\n3\n'


def test_apply_empty_edits_returns_text() -> None:
    assert apply_text_edits_to_text('abc\n', []) == 'abc\n'


def test_apply_utf16_columns_with_astral_char() -> None:
    # '🎉' — 2 единицы UTF-16, 1 символ python; basedpyright/clangd считают
    # колонки в единицах UTF-16: 'f' из 'foo' стоит на utf-16 колонке 11
    # (python 10 + 1 лишняя единица суррогатной пары)
    text = 'a = "🎉" + foo()\n'
    result = apply_text_edits_to_text(
        text, [_te(0, 11, 0, 14, 'bar')]
    )
    assert result == 'a = "🎉" + bar()\n'


def test_apply_invalid_edits_dropped() -> None:
    text = 'aaa\nbbb\n'
    edits = [
        {'range': {'start': {'line': 5, 'character': 0},
                   'end': {'line': 6, 'character': 0}}, 'newText': 'X'},
        {'range': {'start': {'line': 0, 'character': 2},
                   'end': {'line': 0, 'character': 1}}, 'newText': 'Y'},
        _te(1, 0, 1, 3, 'BBB'),  # валидная правка остаётся
    ]
    assert apply_text_edits_to_text(text, edits) == 'aaa\nBBB\n'


# --- normalize_document_changes ---

def test_normalize_changes_map_form() -> None:
    changes = normalize_document_changes({'changes': {
        'file:///a.py': [{'range': {}, 'newText': 'x'}]
    }})
    assert len(changes) == 1
    assert changes[0]['textDocument']['uri'] == 'file:///a.py'


def test_normalize_keeps_document_changes_order_and_mixed() -> None:
    doc_changes = [
        {'kind': 3, 'uri': 'file:///gone.py'},
        {'textDocument': {'uri': 'file:///a.py'}, 'edits': []},
        {'kind': 1, 'uri': 'file:///new.py'},
    ]
    changes = normalize_document_changes({'documentChanges': doc_changes})
    assert [c.get('uri') or c.get('textDocument') for c in changes] == [
        'file:///gone.py', {'uri': 'file:///a.py'}, 'file:///new.py'
    ]


def test_normalize_garbage_returns_empty() -> None:
    assert normalize_document_changes(None) == []
    assert normalize_document_changes({}) == []


# --- path_from_file_uri ---

def test_path_from_file_uri_percent_decoding() -> None:
    path = path_from_file_uri('file:///home/kai/%D0%BC%D0%B5%D1%82%D0%BA%D0%B0/a%20b.py')
    assert path == Path('/home/kai/метка/a b.py')


def test_path_from_non_file_uri_is_none() -> None:
    assert path_from_file_uri('untitled://text/1') is None
    assert path_from_file_uri('') is None


# --- файловые операции ---

def test_create_file_makes_parents_and_contents(tmp_path: Path) -> None:
    target = tmp_path / 'pkg' / 'mod.py'
    assert create_file(target, {}, 'print()\n') is True
    assert target.read_text(encoding='utf-8') == 'print()\n'


def test_create_file_respects_overwrite_flags(tmp_path: Path) -> None:
    target = tmp_path / 'a.py'
    target.write_text('old', encoding='utf-8')
    assert create_file(target, {}, 'new') is False  # без overwrite — не трогаем
    assert target.read_text(encoding='utf-8') == 'old'
    assert create_file(target, {'ignoreIfExists': True}, 'new') is False
    assert target.read_text(encoding='utf-8') == 'old'
    assert create_file(target, {'overwrite': True}, 'new') is True
    assert target.read_text(encoding='utf-8') == 'new'


def test_rename_file_moves_and_respects_flags(tmp_path: Path) -> None:
    old = tmp_path / 'old.py'
    new = tmp_path / 'sub' / 'new.py'
    old.write_text('x', encoding='utf-8')
    assert rename_file(old, new, {}) is True
    assert not old.exists() and new.read_text(encoding='utf-8') == 'x'

    # нет источника — False
    assert rename_file(old, new, {'overwrite': True}) is False

    # занятая цель
    src = tmp_path / 'src.py'
    src.write_text('src', encoding='utf-8')
    assert rename_file(src, new, {}) is False
    assert rename_file(src, new, {'ignoreIfExists': True}) is False
    assert src.exists()
    assert rename_file(src, new, {'overwrite': True}) is True
    assert not src.exists() and new.read_text(encoding='utf-8') == 'src'


def test_delete_file_rules(tmp_path: Path) -> None:
    f = tmp_path / 'gone.py'
    f.write_text('x', encoding='utf-8')
    assert delete_file(tmp_path / 'missing.py', {}) is False
    assert delete_file(f, {}) is True
    assert not f.exists()

    d = tmp_path / 'trash'
    (d / 'inner').mkdir(parents=True)
    (d / 'inner' / 'a.py').write_text('x', encoding='utf-8')
    assert delete_file(d, {}) is False  # без recursive — каталог не трогаем
    assert d.exists()
    assert delete_file(d, {'recursive': True}) is True
    assert not d.exists()
