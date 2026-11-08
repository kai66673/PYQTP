"""Тесты поиска по проекту (этап 9.3): сборка argv rg, парсер --json, панель.

Юниты argv/парсера — без GUI и без бинаря rg (парсер кормится записанными
JSON-строками, по приёмке этапа). Живые тесты панели — offscreen-окно
``IdeMainWindow`` с KDD (паттерн фикстур как в ``test_quick_open.py``),
пропускаются, если в системе нет ``rg``.
"""

import shutil
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.project_control.project_search import (
    SearchMatch,
    build_rg_args,
    match_from_event,
    parse_rg_event,
    split_complete_lines,
)
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

###########################
# Сборка аргументов rg (юниты, без GUI)


def test_build_rg_args_defaults() -> None:
    args = build_rg_args('hello world')
    assert args[:3] == ['--json', '--line-number', '--no-messages']
    assert '-i' in args
    assert '--fixed-strings' in args
    assert '-w' not in args
    assert args[-3:] == ['--', 'hello world', '.']


def test_build_rg_args_toggles() -> None:
    args = build_rg_args(
        'x', case_sensitive=True, whole_word=True, regex_mode=True
    )
    assert '-i' not in args
    assert '--fixed-strings' not in args
    assert '-w' in args
    # query с дефисом не трактуется как опция: разделитель '--' всегда на месте
    assert '--' in args


###########################
# Парсер rg --json (юниты, без GUI)


def test_split_complete_lines_keeps_tail() -> None:
    lines, tail = split_complete_lines(b'{"a":1}\n{"b":2}\n{"c"')
    assert lines == [b'{"a":1}', b'{"b":2}']
    assert tail == b'{"c"'
    # перевод строки приедет следующим чанком — строка снова полная
    lines2, tail2 = split_complete_lines(tail + b':3}\n')
    assert lines2 == [b'{"c":3}']
    assert tail2 == b''


def test_parse_rg_event_accepts_and_rejects() -> None:
    assert parse_rg_event(b'{"type":"match","data":{}}') == {
        'type': 'match',
        'data': {},
    }
    assert parse_rg_event(b'') is None
    assert parse_rg_event('не json'.encode()) is None
    assert parse_rg_event(b'[1, 2]') is None


def _match_event(
    path: str, line_text: str, line_number: int, start: int, end: int
) -> dict:
    """Один match-ивент формата rg --json (смещения — байты UTF-8)."""
    return {
        'path': {'text': path},
        'lines': {'text': line_text},
        'line_number': line_number,
        'absolute_offset': 0,
        'submatches': [{'match': {'text': ''}, 'start': start, 'end': end}],
    }


def test_match_from_event_relative_path_and_fields(tmp_path: Path) -> None:
    event = _match_event('./pkg/sub/deep.py', 'z = 3\n', 7, 4, 5)
    match = match_from_event(event, tmp_path)
    assert match is not None
    assert match.path == tmp_path / 'pkg' / 'sub' / 'deep.py'
    assert match.line == 7
    assert match.text == 'z = 3'  # перевод строки срезан
    assert (match.column, match.end_column) == (5, 6)


def test_match_from_event_cyrillic_utf16_columns() -> None:
    # 'привет ' = 13 байт UTF-8 (6 букв x2 + пробел); 'мир' = 13..18 включительно
    event = _match_event('./a.py', 'привет мир\n', 1, 13, 19)
    match = match_from_event(event, Path('/рут'))
    assert match is not None
    assert (match.column, match.end_column) == (8, 11)  # конец эксклюзивен
    assert match.text == 'привет мир'


def test_match_from_event_emoji_utf16_columns() -> None:
    # '😀' = 4 байта UTF-8 = 1 суррогатная пара (2 единицы UTF-16)
    event = _match_event('./a.py', '😀x\n', 1, 4, 5)
    match = match_from_event(event, Path('/рут'))
    assert match is not None
    assert (match.column, match.end_column) == (3, 4)


def test_match_from_event_malformed_returns_none() -> None:
    root = Path('/рут')
    assert match_from_event({}, root) is None
    assert match_from_event({'path': {'text': './a.py'}}, root) is None  # нет строки
    bad = _match_event('./a.py', 'x\n', 1, 0, 1)
    bad['submatches'] = []
    fallback = match_from_event(bad, root)
    assert fallback is not None
    assert (fallback.column, fallback.end_column) == (1, 1)
    bad2 = _match_event('./a.py', 'x\n', 1, 0, 1)
    bad2['submatches'] = [{'match': {'text': ''}, 'start': None, 'end': None}]
    fallback2 = match_from_event(bad2, root)
    assert fallback2 is not None
    assert (fallback2.column, fallback2.end_column) == (1, 1)


###########################
# Живые тесты панели (offscreen GUI, без LSP/WebEngine)

needs_rg = pytest.mark.skipif(
    shutil.which('rg') is None, reason='нужен установленный ripgrep (rg)'
)


@pytest.fixture
def org_app(request: pytest.FixtureRequest) -> Iterator[tuple[str, str]]:
    """Уникальное пространство QSettings на тест (чистим до и после)."""
    app_name = f'TEST_{request.node.name}'
    settings = QSettings('PYQTP_TESTS', app_name)
    settings.clear()
    settings.sync()
    yield 'PYQTP_TESTS', app_name
    settings.clear()
    settings.sync()


@pytest.fixture
def search_root(tmp_path: Path) -> Path:
    """Дерево проекта для живых проверок ripgrep."""
    root = tmp_path / 'proj'
    (root / 'pkg' / 'sub').mkdir(parents=True)
    (root / 'a.py').write_text('привет мир\nx = 0\nМИР на третьей\n', encoding='utf-8')
    (root / 'pkg' / 'sub' / 'deep.py').write_text('target = 1\n', encoding='utf-8')
    (root / 'pkg' / 'word.py').write_text('слов\nприветствие', encoding='utf-8')
    (root / 'pkg' / 'targets.py').write_text('targets = 2\n', encoding='utf-8')
    return root


def make_window(org_app: tuple[str, str]) -> IdeMainWindow:
    """IdeMainWindow c KDD вне launcher'а (launcher форсит xcb/D-Bus).

    Зависимость от ``qapp`` обязательна на уровне вызывающего теста —
    иначе ``QMainWindow.__init__`` qFatal-абортит процесс.
    """
    org, app_name = org_app
    window = IdeMainWindow(
        org_name=org, app_name=app_name, dock_system_type=ViewsManagerType.kdd
    )
    args = window.settings_parse_argv([])
    window.settings_read(**vars(args))
    window.setup()
    window.setup_ui()
    return window


@pytest.fixture
def wnd(org_app: tuple[str, str], qapp: QApplication) -> Iterator[IdeMainWindow]:
    window = make_window(org_app)
    yield window
    window.close()


def status_text(view) -> str:
    return str(view._status_label.text())


def wait_finished(qtbot: QtBot, view) -> None:
    """Ждём завершения поиска: свой finished-хендлер обнуляет ``_proc``."""
    qtbot.waitUntil(
        lambda: view._proc is None and status_text(view) != 'Поиск…',
        timeout=15_000,
    )


@needs_rg
def test_search_action_tracks_project(wnd: IdeMainWindow, search_root: Path) -> None:
    """Ctrl+Shift+F погашен без проекта, активен с проектом, снова погас без него."""
    ctrl = wnd.project_ctrl
    assert ctrl.action_project_search.isEnabled() is False

    assert ctrl.open_project(search_root) is True
    assert ctrl.action_project_search.isEnabled() is True

    assert ctrl.close_project() is True
    assert ctrl.action_project_search.isEnabled() is False


@needs_rg
def test_search_panel_finds_groups_and_status(
    wnd: IdeMainWindow, search_root: Path, qtbot: QtBot
) -> None:
    """Два файла -> два заголовка с относительными путями, статус про число."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(search_root) is True
    view = ctrl.search_view
    assert view is not None

    view._line_edit.setText('target')
    view.start_search()
    wait_finished(qtbot, view)

    assert status_text(view) == '2 совпадений в 2 файлах'
    headers = {
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    }
    assert headers == {'pkg/sub/deep.py', 'pkg/targets.py'}
    deep = next(
        view._tree.topLevelItem(i)
        for i in range(view._tree.topLevelItemCount())
        if view._tree.topLevelItem(i).text(0) == 'pkg/sub/deep.py'
    )
    assert deep.child(0).text(0) == '1: target = 1'


@needs_rg
def test_search_cyrillic_column_and_open(
    wnd: IdeMainWindow,
    search_root: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Клик по совпадению -> open_definition_uri с UTF-16 колонкой (кириллица)."""
    opened: list[dict] = []
    monkeypatch.setattr(
        wnd.doc_ctrl, 'open_definition_uri', lambda payload: opened.append(payload)
    )
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(search_root) is True
    view = ctrl.search_view
    assert view is not None

    # 'привет ' = 13 байт UTF-8; 'мир' — 8-я колонка (юнит это уже проверял)
    view._line_edit.setText('мир')
    view._case_box.setChecked(True)
    view.start_search()
    wait_finished(qtbot, view)

    header = view._tree.topLevelItem(0)
    assert header is not None
    assert header.text(0) == 'a.py'
    assert header.childCount() == 1  # «МИР на второй» — без регистра выключен
    child = header.child(0)
    assert child.text(0) == '1: привет мир'

    view._on_item_activated(child, 0)
    assert len(opened) == 1
    payload = opened[0]
    assert payload['range'] == {'startLineNumber': 1, 'startColumn': 8}
    assert payload['uri'] == QUrl.fromLocalFile(str(search_root / 'a.py')).toString()


@needs_rg
def test_search_regex_and_whole_word_toggles(
    wnd: IdeMainWindow, search_root: Path, qtbot: QtBot
) -> None:
    """Regex включает regex, «Слово целиком» ограничивает совпадения границами."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(search_root) is True
    view = ctrl.search_view
    assert view is not None

    # фиксированная строка: «mis*» как литерал — ничего
    view._line_edit.setText('mis*')
    view.start_search()
    wait_finished(qtbot, view)
    assert status_text(view) == 'Совпадений нет'

    # regex: 'ta.*et' находит deep.py ('target') и targets.py ('targets')
    view._regex_box.setChecked(True)
    view._line_edit.setText('ta.*et')
    view.start_search()
    wait_finished(qtbot, view)
    assert status_text(view) == '2 совпадений в 2 файлах'

    # слово целиком: 'target' не совпадёт внутри 'targets' — остаётся deep.py
    view._regex_box.setChecked(False)
    view._word_box.setChecked(True)
    view._line_edit.setText('target')
    view.start_search()
    wait_finished(qtbot, view)
    assert status_text(view) == '1 совпадений в 1 файлах'


@needs_rg
def test_search_no_match_error_and_close_project(
    wnd: IdeMainWindow, search_root: Path, qtbot: QtBot
) -> None:
    """Нет совпадений — отдельный статус; закрытие проекта чистит панель."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(search_root) is True
    view = ctrl.search_view
    assert view is not None

    view._line_edit.setText('target')
    view.start_search()
    wait_finished(qtbot, view)
    assert view._tree.topLevelItemCount() == 2

    assert ctrl.close_project() is True
    assert view._root is None
    assert view._tree.topLevelItemCount() == 0
    assert status_text(view) == 'Проект не открыт'

    # повторный запуск после закрытия не поднимает процесс
    view.start_search()
    assert view._proc is None
    assert status_text(view) == 'Проект не открыт'


@needs_rg
def test_search_bad_regex_reports_error(
    wnd: IdeMainWindow, search_root: Path, qtbot: QtBot
) -> None:
    """Битый regex: rg ошибается (код >= 2), панель показывает ошибку."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(search_root) is True
    view = ctrl.search_view
    assert view is not None

    view._regex_box.setChecked(True)
    view._line_edit.setText('(((')
    view.start_search()
    wait_finished(qtbot, view)

    assert status_text(view).startswith('Ошибка')
    assert view._tree.topLevelItemCount() == 0


def test_match_dataclass_shape(tmp_path: Path) -> None:
    """SearchMatch — замороженный dataclass с путём и координатами Monaco."""
    match = SearchMatch(path=tmp_path / 'a.py', line=2, column=3, end_column=4, text='x')
    assert match.path.name == 'a.py'
    with pytest.raises(FrozenInstanceError):
        match.line = 5  # type: ignore[misc]
