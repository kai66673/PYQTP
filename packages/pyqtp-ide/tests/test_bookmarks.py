"""Этап 9.7 — закладки проекта.

Юниты без GUI (дефолтный прогон) — чистый модуль
``pyqtp/controls/bookmark_control/bookmarks.py``: толерантный разбор,
переключение/переходы, атомарная запись, срежка мёртвых файлов и соседство
файла закладок с ``session.json``/``settings.json``.

Offscreen-GUI без Monaco/WebEngine (дефолтный прогон) — ``BookmarkControl`` и
панель ``BookmarksView``: живость экшенов вместе с проектом, загрузка снимка
при открытии проекта, раздача строк открытым редакторам, переключение сигналом
редактора (gutter-клик), переходы next/prev, намерения панели.

WebEngine-пробы (маркер ``webengine``) — мост и decorations Monaco:
``set_bookmarks`` рисует метки в дорожке, набор переживает смену модели,
каретка из JS прилетает в python-зеркало, ``_toggle_bookmark_request``
прокидывает сигнал наверх.

Настоящие документы подменены лёгкой вьюхой с фейковым редактором (как в
test_settings_scope.py; хелперы живут в одном файле).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

import qtmonaco
from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.bookmark_control import bookmarks as bm
from pyqtp.controls.bookmark_control.bookmarks import Bookmark
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.main_wnd.ide_main_window import IdeMainWindow
from pyqtp.web.qtmonaco_editor import QtMonacoEditorWidget

##########################
# Юниты чистого модуля  #


def test_parse_bookmarks_tolerant() -> None:
    """Мусор в файле — не причина падать: пропускаем, а не выбрасываем."""
    assert bm.parse_bookmarks(None) == []
    assert bm.parse_bookmarks({'bookmarks': 'не список'}) == []
    raw = {
        'bookmarks': [
            {'path': '/a.py', 'line': 2},
            {'path': '', 'line': 2},               # пустой путь
            {'path': '/a.py', 'line': 0},          # строки 1-based
            {'path': '/a.py', 'line': '2'},        # не число
            {'path': '/a.py', 'line': True},       # bool — не номер строки
            {'path': '/b.py', 'line': 7, 'extra': 1},
            'не словарь',
            {'path': '/a.py', 'line': 2},          # дубль
        ]
    }
    assert bm.parse_bookmarks(raw) == [
        Bookmark(path='/a.py', line=2),
        Bookmark(path='/b.py', line=7),
    ]


def test_deserialize_and_read_broken_inputs(tmp_path: Path) -> None:
    """Битый JSON и отсутствующий файл — пустой набор, не исключение."""
    assert bm.deserialize_bookmarks('{мусор') == []
    assert bm.read_bookmarks(tmp_path) == []
    bm.bookmarks_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    bm.bookmarks_file(tmp_path).write_text('[][]', encoding='utf-8')
    assert bm.read_bookmarks(tmp_path) == []


def test_serialize_sorts_and_dedups() -> None:
    """Порядок и дубли на записи: список канонический по (path, line)."""
    items = [
        Bookmark(path='/b.py', line=3),
        Bookmark(path='/a.py', line=9),
        Bookmark(path='/a.py', line=9),
        Bookmark(path='/a.py', line=1),
    ]
    payload = json.loads(bm.serialize_bookmarks(items))
    assert payload == {
        'bookmarks': [
            {'path': '/a.py', 'line': 1},
            {'path': '/a.py', 'line': 9},
            {'path': '/b.py', 'line': 3},
        ]
    }


def test_write_read_roundtrip_no_temp_leftovers(tmp_path: Path) -> None:
    """Запись атомарна: временных файлов после неё не остаётся."""
    items = [Bookmark(path=str(tmp_path / 'a.py'), line=4)]
    bm.write_bookmarks(tmp_path, items)
    assert bm.read_bookmarks(tmp_path) == items
    project_dir = tmp_path / '.pyqtp'
    leftovers = [p.name for p in project_dir.iterdir()]
    assert leftovers == ['bookmarks.json']


def test_write_empty_clears_but_keeps_neighbours(tmp_path: Path) -> None:
    """Пустой набор снимает файл (и пустой ``.pyqtp/``), но не соседей."""
    project_dir = tmp_path / '.pyqtp'
    project_dir.mkdir()
    session = project_dir / 'session.json'
    session.write_text('{"docs": [], "active": null}', encoding='utf-8')

    bm.write_bookmarks(tmp_path, [Bookmark(path='/a.py', line=1)])
    assert bm.bookmarks_file(tmp_path).is_file()

    bm.write_bookmarks(tmp_path, [])
    assert not bm.bookmarks_file(tmp_path).exists()
    assert session.is_file()  # сессию проекта не трогаем
    assert project_dir.is_dir()

    session.unlink()
    bm.clear_bookmarks(tmp_path)
    assert not project_dir.exists()  # пустой каталог проекта убираем


def test_filter_bookmarks_prunes_dead_files(tmp_path: Path) -> None:
    """Живой файл остаётся, удалённый — уходит в «пропущенные»."""
    alive = tmp_path / 'a.py'
    alive.write_text('x = 1\n', encoding='utf-8')
    items = [
        Bookmark(path=str(alive), line=1),
        Bookmark(path=str(tmp_path / 'gone.py'), line=2),
    ]
    kept, skipped = bm.filter_bookmarks(items)
    assert kept == [Bookmark(path=str(alive), line=1)]
    assert skipped == [Bookmark(path=str(tmp_path / 'gone.py'), line=2)]


def test_toggle_bookmark_adds_then_removes() -> None:
    """Переключение: нет — ставим, есть — снимаем; вход не меняется."""
    base = [Bookmark(path='/a.py', line=2)]
    added = bm.toggle_bookmark(base, '/a.py', 5)
    assert added == [Bookmark(path='/a.py', line=2), Bookmark(path='/a.py', line=5)]
    assert base == [Bookmark(path='/a.py', line=2)]
    assert bm.toggle_bookmark(added, '/a.py', 5) == base


def test_bookmarks_for_and_navigation_wraps() -> None:
    """Строки одного файла + переходы next/prev с циклом."""
    items = [
        Bookmark(path='/a.py', line=9),
        Bookmark(path='/a.py', line=2),
        Bookmark(path='/b.py', line=4),
    ]
    lines = bm.bookmarks_for(items, '/a.py')
    assert lines == [2, 9]
    assert bm.bookmarks_for(items, '/c.py') == []

    assert bm.next_bookmark_line(lines, 1) == 2
    assert bm.next_bookmark_line(lines, 2) == 9       # равна — не годится
    assert bm.next_bookmark_line(lines, 9) == 2       # цикл в начало
    assert bm.next_bookmark_line(lines, 9, forward=False) == 2
    assert bm.next_bookmark_line(lines, 2, forward=False) == 9  # цикл в конец
    assert bm.next_bookmark_line([], 5) is None


###########################
# Offscreen GUI harness #


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


def make_window(org_app: tuple[str, str]) -> IdeMainWindow:
    """IdeMainWindow c KDD вне launcher'а (зависимость от qapp — на вызывающем)."""
    org, app_name = org_app
    window = IdeMainWindow(
        org_name=org, app_name=app_name, dock_system_type=ViewsManagerType.kdd
    )
    args = window.settings_parse_argv([])
    window.settings_read(**vars(args))
    window.setup()
    window.setup_ui()
    return window


class FakeDocument:
    """Минимальная реализация протокола ``Document`` (реальный ввод-вывод не нужен)."""

    def __init__(self, identity: str) -> None:
        self._identity = identity
        self._dirty = False

    def identity(self) -> str:
        return self._identity

    def is_modified(self) -> bool:
        return self._dirty

    def set_modified(self, modified: bool = True) -> None:  # noqa: FBT001, FBT002
        self._dirty = modified

    def do_save(self) -> bool:
        return True

    def save(self) -> bool:
        # копия дефолтной реализации протокола Document (протокол — не база)
        if self.do_save():
            self.set_modified(False)
            return True
        return False


class FakeEditWidget(QObject):
    """Фейковый редактор: рекордеры сообщений + зеркало каретки и сигнал дорожки.

    ``bookmark_toggle_requested`` повторяет маршрут реального
    ``QtMonacoEditorWidget`` (клик по дорожке закладок в gutter Monaco).
    """

    bookmark_toggle_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.options_calls: list[dict[str, Any]] = []
        self.theme_calls: list[str] = []
        self.bookmark_calls: list[list[int]] = []
        self.cursor_calls: list[tuple[int, int, str]] = []
        self.current_cursor: dict[str, int] = {'line': 1, 'column': 1}

    def update_editor_options(self, options: dict[str, Any]) -> None:
        self.options_calls.append(dict(options))

    def set_theme(self, theme: str) -> None:
        self.theme_calls.append(theme)

    def set_bookmarks(self, lines: list[int]) -> None:
        self.bookmark_calls.append(list(lines))

    def set_cursor(
        self, line: int, column: int = 1, *, move_to_position: str = ''
    ) -> None:
        self.cursor_calls.append((line, column, move_to_position))
        self.current_cursor = {'line': line, 'column': column}


class FakeDocView(BaseDocumentView):
    """Документная вьюха без Monaco/WebEngine — с фейковым редактором закладок."""

    def __init__(self, main_wnd, /, *, key: str) -> None:  # noqa: ANN001
        super().__init__(main_wnd, key=key)
        self.focus_child = QWidget(self)
        self.focus_child.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.edit_widget = FakeEditWidget()

    def document(self) -> FakeDocument:
        if not hasattr(self, '_fake_doc'):
            self._fake_doc = FakeDocument(self.key)
        return self._fake_doc


@pytest.fixture
def fake_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_mod, '_document_view_cls_from_identity', lambda _i: FakeDocView)


@pytest.fixture
def wnd(
    org_app: tuple[str, str],
    qapp: QApplication,
    fake_docs: None,
) -> Iterator[IdeMainWindow]:
    window = make_window(org_app)
    yield window
    window.close()


@pytest.fixture
def prj_root(tmp_path: Path) -> Path:
    """Проект с двумя файлами по 6 строк (номера строк — 1-based, как в Monaco)."""
    root = tmp_path / 'proj'
    root.mkdir()
    for name in ('a.py', 'b.py'):
        (root / name).write_text(
            ''.join(f'VALUE_{name[0]}_{i} = {i}\n' for i in range(1, 7)),
            encoding='utf-8',
        )
    return root


def fake_edit(wnd: IdeMainWindow, path: str) -> FakeEditWidget:
    """Фейковый редактор открытого документа (проверка сообщений ему)."""
    view = wnd.doc_ctrl.document_view(path)
    assert isinstance(view, FakeDocView)
    return view.edit_widget


def panel_tree(view: Any) -> list[tuple[str, list[str]]]:
    """Снимок панели «файл -> строки» (тексты элементов дерева)."""
    tree = view._tree
    result: list[tuple[str, list[str]]] = []
    for i in range(tree.topLevelItemCount()):
        header = tree.topLevelItem(i)
        rows = [header.child(j).text(0) for j in range(header.childCount())]
        result.append((header.text(0), rows))
    return result


def write_bookmark_file(root: Path, entries: list[tuple[str, int]]) -> None:
    """Черновик ``.pyqtp/bookmarks.json`` руками (проверка загрузки)."""
    project_dir = root / '.pyqtp'
    project_dir.mkdir(parents=True, exist_ok=True)
    payload = {'bookmarks': [{'path': path, 'line': line} for path, line in entries]}
    (project_dir / 'bookmarks.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8'
    )


def stored_bookmarks(root: Path) -> list[Bookmark]:
    """Что реально лежит в файле закладок проекта."""
    return bm.read_bookmarks(root)


def press_delete(tree: Any) -> None:
    """Клавиша Delete в дереве панели (как у пользователя)."""
    tree.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )


#######################
# Экшены и проект     #


def test_actions_follow_project(wnd: IdeMainWindow, prj_root: Path) -> None:
    """Без проекта закладки недоступны; с проектом — включены."""
    ctrl = wnd.bookmark_ctrl
    trio = (
        ctrl.action_toggle_bookmark,
        ctrl.action_next_bookmark,
        ctrl.action_prev_bookmark,
    )
    assert [action.isEnabled() for action in trio] == [False, False, False]

    wnd.project_ctrl.open_project(prj_root)
    assert [action.isEnabled() for action in trio] == [True, True, True]

    wnd.project_ctrl.close_project()
    assert [action.isEnabled() for action in trio] == [False, False, False]


def test_chords_match_vs_code(wnd: IdeMainWindow) -> None:
    """Связки — как штатные закладки VS Code (Ctrl+Alt+L/K/J)."""
    chords = {
        str(action.shortcut().toString())
        for action in (
            wnd.bookmark_ctrl.action_toggle_bookmark,
            wnd.bookmark_ctrl.action_next_bookmark,
            wnd.bookmark_ctrl.action_prev_bookmark,
        )
    }
    assert chords == {'Ctrl+Alt+L', 'Ctrl+Alt+K', 'Ctrl+Alt+J'}


def test_open_project_loads_and_prunes_dead(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Снимок из файла при открытии проекта; мёртвые файлы срезаются в файле."""
    dead = prj_root / 'gone.py'
    write_bookmark_file(
        prj_root,
        [
            (str(prj_root / 'b.py'), 5),
            (str(prj_root / 'a.py'), 3),
            (str(dead), 1),
            (str(prj_root / 'a.py'), 1),
        ],
    )
    wnd.project_ctrl.open_project(prj_root)

    ctrl = wnd.bookmark_ctrl
    assert ctrl.bookmarks_list() == [
        Bookmark(path=str(prj_root / 'a.py'), line=1),
        Bookmark(path=str(prj_root / 'a.py'), line=3),
        Bookmark(path=str(prj_root / 'b.py'), line=5),
    ]
    # срезка сразу уходит обратно в файл проекта
    assert stored_bookmarks(prj_root) == ctrl.bookmarks_list()
    assert panel_tree(ctrl.bookmarks_view) == [
        ('a.py', ['Строка 1', 'Строка 3']),
        ('b.py', ['Строка 5']),
    ]
    panel = ctrl.bookmarks_view
    assert panel is not None
    assert panel._status_label.text() == 'Закладок: 3'


def test_open_document_gets_only_its_lines(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Открытый документ получает строки-закладки только своего файла."""
    write_bookmark_file(
        prj_root,
        [
            (str(prj_root / 'a.py'), 2),
            (str(prj_root / 'a.py'), 6),
            (str(prj_root / 'b.py'), 4),
        ],
    )
    wnd.project_ctrl.open_project(prj_root)

    path_a = str(prj_root / 'a.py')
    wnd.doc_ctrl.open_document(path_a)
    edit = fake_edit(wnd, path_a)
    assert edit.bookmark_calls == [[2, 6]]

    path_b = str(prj_root / 'b.py')
    wnd.doc_ctrl.open_document(path_b)
    assert fake_edit(wnd, path_b).bookmark_calls == [[4]]


def test_project_switch_reloads_state(
    wnd: IdeMainWindow, prj_root: Path, tmp_path: Path
) -> None:
    """Смена проекта — свой снимок; файл старого проекта на диске цел."""
    old_bookmarks = [(str(prj_root / 'a.py'), 3)]
    write_bookmark_file(prj_root, old_bookmarks)
    wnd.project_ctrl.open_project(prj_root)
    path_a = str(prj_root / 'a.py')
    wnd.doc_ctrl.open_document(path_a)
    assert fake_edit(wnd, path_a).bookmark_calls == [[3]]

    other = tmp_path / 'other'
    other.mkdir()
    path_c = str(other / 'c.py')
    (other / 'c.py').write_text('VALUE_c = 1\n', encoding='utf-8')
    # смена корня закрывает вкладки старого проекта (9.6) — в них уже не смотрим
    wnd.project_ctrl.open_project(other)

    ctrl = wnd.bookmark_ctrl
    assert ctrl.bookmarks_list() == []
    assert panel_tree(ctrl.bookmarks_view) == []
    panel = ctrl.bookmarks_view
    assert panel is not None
    assert panel._status_label.text() == 'Нет закладок'

    wnd.doc_ctrl.open_document(path_c)
    assert fake_edit(wnd, path_c).bookmark_calls == [[]]

    # возвращаемся — закладки старого проекта снова на месте (файл не тронут);
    # вкладку вернула сессия проекта: первой ей пришла пустая раздача (проект
    # ещё «other»), финальные строки доносит broadcast контрола после загрузки
    # снимка — порядок «сессия раньше закладок» принят по дизайну
    wnd.project_ctrl.open_project(prj_root)
    assert ctrl.bookmarks_list() == [
        Bookmark(path=str(prj_root / 'a.py'), line=3)
    ]
    edit_a = fake_edit(wnd, path_a)
    assert edit_a.bookmark_calls == [[], [3]]
    wnd.doc_ctrl.open_document(path_a)  # повторный клик — новой раздачи не нужно
    assert edit_a.bookmark_calls == [[], [3]]
    assert panel_tree(ctrl.bookmarks_view) == [('a.py', ['Строка 3'])]


def test_close_project_keeps_file_clears_view(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Закрытие проекта снимает состояние и панель, файл остаётся на диске."""
    entries = [(str(prj_root / 'a.py'), 4)]
    write_bookmark_file(prj_root, entries)
    wnd.project_ctrl.open_project(prj_root)
    assert wnd.bookmark_ctrl.bookmarks_list()

    wnd.project_ctrl.close_project()
    assert wnd.bookmark_ctrl.bookmarks_list() == []
    assert panel_tree(wnd.bookmark_ctrl.bookmarks_view) == []
    assert stored_bookmarks(prj_root) == [
        Bookmark(path=str(prj_root / 'a.py'), line=4)
    ]


###########################
# Переключение закладок  #


def test_toggle_at_gating(wnd: IdeMainWindow, prj_root: Path, tmp_path: Path) -> None:
    """Отказы: нет проекта, путь вне корня, файла нет; иначе — запись в файл."""
    ctrl = wnd.bookmark_ctrl
    path_a = str(prj_root / 'a.py')
    assert ctrl.toggle_at(path_a, 2) is False
    assert not bm.bookmarks_file(prj_root).exists()

    wnd.project_ctrl.open_project(prj_root)
    outside = tmp_path / 'outside.py'
    outside.write_text('x = 1\n', encoding='utf-8')
    assert ctrl.toggle_at(str(outside), 1) is False
    assert ctrl.toggle_at(str(prj_root / 'missing.py'), 1) is False
    assert stored_bookmarks(prj_root) == []

    assert ctrl.toggle_at(path_a, 2) is True
    assert ctrl.toggle_at(path_a, 2) is True  # повторное — снятие
    assert stored_bookmarks(prj_root) == []
    assert panel_tree(ctrl.bookmarks_view) == []

    assert ctrl.toggle_at(path_a, 5) is True
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_a, line=5)]


def test_gutter_signal_toggles_everything(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Клик по дорожке (тот же сигнал редактора) — файл, панель и сам редактор."""
    path_a = str(prj_root / 'a.py')
    path_b = str(prj_root / 'b.py')
    write_bookmark_file(prj_root, [(path_b, 4)])
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(path_a)
    wnd.doc_ctrl.open_document(path_b)
    ctrl = wnd.bookmark_ctrl
    edit_a = fake_edit(wnd, path_a)
    edit_b = fake_edit(wnd, path_b)
    assert edit_a.bookmark_calls == [[]]
    assert edit_b.bookmark_calls == [[4]]

    view_a = wnd.doc_ctrl.document_view(path_a)
    assert view_a is not None
    # клик по дорожке в редакторе a.py — строка уходит в контрол этого файла
    edit_a.bookmark_toggle_requested.emit(3)
    assert stored_bookmarks(prj_root) == [
        Bookmark(path=path_a, line=3),
        Bookmark(path=path_b, line=4),
    ]
    assert edit_a.bookmark_calls == [[], [3]]
    assert edit_b.bookmark_calls == [[4], [4]]  # чужой файл не перерисован зря?
    assert panel_tree(ctrl.bookmarks_view) == [
        ('a.py', ['Строка 3']),
        ('b.py', ['Строка 4']),
    ]

    # повторный клик по той же строке — снятие
    edit_a.bookmark_toggle_requested.emit(3)
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_b, line=4)]
    assert edit_a.bookmark_calls[-1] == []

    # повторной проводки одного и того же редактора нет (флаг на вьюхе)
    ctrl._wire_doc_view(view_a)
    edit_a.bookmark_toggle_requested.emit(6)
    assert stored_bookmarks(prj_root) == [
        Bookmark(path=path_a, line=6),
        Bookmark(path=path_b, line=4),
    ]


def test_toggle_active_uses_cursor_mirror(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Экшен Ctrl+Alt+L берёт строку каретки из зеркала редактора."""
    path_a = str(prj_root / 'a.py')
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(path_a)
    edit = fake_edit(wnd, path_a)
    edit.current_cursor = {'line': 4, 'column': 3}

    wnd.bookmark_ctrl.action_toggle_bookmark.trigger()
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_a, line=4)]

    # каретка уехала на другую строку — та же клавиша ставит вторую закладку
    edit.current_cursor = {'line': 1, 'column': 1}
    wnd.bookmark_ctrl.action_toggle_bookmark.trigger()
    assert stored_bookmarks(prj_root) == [
        Bookmark(path=path_a, line=1),
        Bookmark(path=path_a, line=4),
    ]

    # снятие той же строки
    wnd.bookmark_ctrl.action_toggle_bookmark.trigger()
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_a, line=4)]


def test_next_prev_bookmarks_cycle(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Переходы next/prev по закладкам активного файла, с циклом."""
    path_a = str(prj_root / 'a.py')
    write_bookmark_file(prj_root, [(path_a, 2), (path_a, 5)])
    wnd.project_ctrl.open_project(prj_root)
    wnd.doc_ctrl.open_document(path_a)
    edit = fake_edit(wnd, path_a)
    ctrl = wnd.bookmark_ctrl

    edit.current_cursor = {'line': 1, 'column': 1}
    ctrl.action_next_bookmark.trigger()
    assert edit.cursor_calls == [(2, 1, 'center')]

    edit.current_cursor = {'line': 5, 'column': 1}
    ctrl.action_next_bookmark.trigger()  # за последней — цикл на первую
    assert edit.cursor_calls[-1] == (2, 1, 'center')

    edit.current_cursor = {'line': 3, 'column': 1}
    ctrl.action_prev_bookmark.trigger()
    assert edit.cursor_calls[-1] == (2, 1, 'center')

    # в файле без закладок переход молчит
    path_b = str(prj_root / 'b.py')
    wnd.doc_ctrl.open_document(path_b)
    edit_b = fake_edit(wnd, path_b)
    edit_b.current_cursor = {'line': 1, 'column': 1}
    ctrl.action_next_bookmark.trigger()
    assert edit_b.cursor_calls == []


def test_no_active_document_is_not_an_error(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Без активной вкладки экшены закладок просто молча отказывают."""
    wnd.project_ctrl.open_project(prj_root)
    ctrl = wnd.bookmark_ctrl
    ctrl.action_toggle_bookmark.trigger()
    ctrl.action_next_bookmark.trigger()
    assert stored_bookmarks(prj_root) == []


######################
# Панель закладок   #


def test_panel_navigates_to_bookmark(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Двойной клик по строке панели — открыть документ и поставить каретку."""
    path_a = str(prj_root / 'a.py')
    write_bookmark_file(prj_root, [(path_a, 3)])
    wnd.project_ctrl.open_project(prj_root)
    view = wnd.bookmark_ctrl.bookmarks_view
    assert view is not None

    tree = view._tree
    child = tree.topLevelItem(0).child(0)
    view._on_item_activated(child, 0)

    assert wnd.doc_ctrl.has_document(path_a)
    assert fake_edit(wnd, path_a).cursor_calls == [(3, 1, 'center')]


def test_panel_delete_key_and_clear_all(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Delete по строке панели снимает закладку, «Удалить все» — весь набор."""
    path_a = str(prj_root / 'a.py')
    path_b = str(prj_root / 'b.py')
    write_bookmark_file(prj_root, [(path_a, 2), (path_b, 5)])
    wnd.project_ctrl.open_project(prj_root)
    view = wnd.bookmark_ctrl.bookmarks_view
    assert view is not None
    tree = view._tree

    child = tree.topLevelItem(0).child(0)
    tree.setCurrentItem(child)
    tree.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_b, line=5)]
    assert panel_tree(view) == [('b.py', ['Строка 5'])]

    # заголовки файлов Delete не снимают
    tree.setCurrentItem(tree.topLevelItem(0))
    press_delete(tree)
    assert stored_bookmarks(prj_root) == [Bookmark(path=path_b, line=5)]

    view.clear_all_requested.emit()
    assert stored_bookmarks(prj_root) == []
    assert panel_tree(view) == []
    assert view._status_label.text() == 'Нет закладок'


###########################
# WebEngine: Monaco и мост #

# js_build не лежит в git — без него редактор не поднимется
JS_BUILD_OK = (Path(qtmonaco.__file__).parent / 'js_build' / 'index.html').is_file()

webengine_only = pytest.mark.skipif(
    not JS_BUILD_OK, reason='qtmonaco/js_build не собран (npm run build)',
)

JS_READY = "window.qtmonaco && window.qtmonaco.editor ? 'READY' : 'NOT_READY'"


def run_js(widget: QtMonacoEditorWidget, script: str, *, timeout: float = 20.0) -> Any:
    """Выполняет JS в странице редактора и ждёт результат (локальный event loop)."""
    holder: dict[str, Any] = {}

    def _cb(value: Any) -> None:
        holder['value'] = value

    widget.page().runJavaScript(script, 0, _cb)
    spent, step = 0.0, 0.05
    while 'value' not in holder and spent < timeout:
        loop = QEventLoop()
        QTimer.singleShot(int(step * 1000), loop.quit)
        loop.exec()
        spent += step
    return holder.get('value')


def wait_for(
    qtbot: Any,
    predicate: Any,
    *,
    timeout: float = 30.0,
    poll: float = 0.4,
    message: str = '',
) -> None:
    """Опрашивает ``predicate`` с интервалом ``poll`` секунд до ``timeout``."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message or 'условие не выполнилось за таймаут')
        qtbot.wait(int(poll * 1000))


def wait_editor_ready(qtbot: Any, widget: QtMonacoEditorWidget) -> None:
    """Ждёт готовности Monaco и моста в странице редактора (до 2 минут)."""
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_READY) == 'READY',
        timeout=120.0,
        poll=1.0,
        message='Monaco в странице редактора так и не поднялся',
    )
    wait_for(
        qtbot,
        lambda: (
            widget.bridge_initialized
            and run_js(widget, 'String(window.qtmonaco.initialized)') == 'true'
        ),
        timeout=30.0,
        poll=0.5,
        message='мост QWebChannel (bridge_initialized) не поднялся',
    )
    qtbot.wait(300)


def bookmark_glyph_count(widget: QtMonacoEditorWidget) -> str:
    """Число меток-закладок в дорожке Monaco (DOM-маркер, не булев)."""
    return run_js(
        widget,
        "String(document.querySelectorAll('.pyqtp-bookmark-glyph').length)",
    )


@pytest.fixture
def bm_editor(
    qapp: QApplication, tmp_path: Path
) -> Iterator[QtMonacoEditorWidget]:
    """Редактор без LSP (``lsp_host=[]``) с семистрочным файлом в tmp_path."""
    path = tmp_path / 'bm_file.py'
    path.write_text(
        'def foo():\n    first = 1\n    second = 2\n\n    third = 3\n    return first\n',
        encoding='utf-8',
    )
    widget = QtMonacoEditorWidget(path, lsp_host=[])
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


@pytest.mark.webengine
@webengine_only
def test_webengine_set_bookmarks_paints_glyphs(
    qtbot: Any, bm_editor: QtMonacoEditorWidget
) -> None:
    """``set_bookmarks`` рисует метки в дорожке; строки за текстом — в мусор."""
    wait_editor_ready(qtbot, bm_editor)
    assert bookmark_glyph_count(bm_editor) == '0'

    bm_editor.set_bookmarks([2, 4])
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '2',
        message='метки закладок не появились в дорожке',
    )
    # набор на JS-стороне — ровно переданный (для последующих проверок)
    assert run_js(bm_editor, 'JSON.stringify(window.qtmonaco.bookmarkLines)') == (
        '[2,4]'
    )

    bm_editor.set_bookmarks([5, 999])
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '1',
        message='строка за пределами текста не проигнорирована',
    )

    bm_editor.set_bookmarks([])
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '0',
        message='метки не снялись при пустом наборе',
    )


@pytest.mark.webengine
@webengine_only
def test_webengine_bookmarks_survive_model_swap(
    qtbot: Any, bm_editor: QtMonacoEditorWidget, tmp_path: Path
) -> None:
    """Смена модели (``set_text`` с новым uri) — закладки переприменяются."""
    wait_editor_ready(qtbot, bm_editor)
    bm_editor.set_bookmarks([2, 3])
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '2',
        message='метки закладок не появились в дорожке',
    )

    other = tmp_path / 'other_file.py'
    other.write_text('x = 1\ny = 2\nz = 3\n', encoding='utf-8')
    bm_editor.set_text(other.read_text(encoding='utf-8'), language='python', uri=str(other))
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '2',
        message='после смены модели метки закладок пропали',
    )

    # короткий файл — за пределами текста меток не остаётся
    tiny = tmp_path / 'tiny_file.py'
    tiny.write_text('only one line\n', encoding='utf-8')
    bm_editor.set_text(tiny.read_text(encoding='utf-8'), language='python', uri=str(tiny))
    wait_for(
        qtbot,
        lambda: bookmark_glyph_count(bm_editor) == '0',
        message='метки вне нового текста не убрались',
    )


@pytest.mark.webengine
@webengine_only
def test_webengine_cursor_is_mirrored_to_python(
    qtbot: Any, bm_editor: QtMonacoEditorWidget
) -> None:
    """Каретка из Monaco прилетает в python-зеркало (нужна для toggle/next/prev)."""
    wait_editor_ready(qtbot, bm_editor)
    run_js(
        bm_editor,
        'window.qtmonaco.editor.setPosition({lineNumber: 6, column: 3}); "MOVED"',
    )
    wait_for(
        qtbot,
        lambda: (bm_editor.current_cursor or {}).get('line') == 6,
        message='каретка не отразилась в python-зеркале',
    )
    assert (bm_editor.current_cursor or {}).get('column') == 3


@pytest.mark.webengine
@webengine_only
def test_webengine_gutter_request_becomes_signal(
    qtbot: Any, bm_editor: QtMonacoEditorWidget
) -> None:
    """``_toggle_bookmark_request`` из JS — сигнал ``bookmark_toggle_requested``."""
    wait_editor_ready(qtbot, bm_editor)

    received: list[int] = []
    bm_editor.bookmark_toggle_requested.connect(received.append)

    # так же, как это делает dispatch моста (hasattr + вызов метода)
    bm_editor.on_new_data_received('_toggle_bookmark_request', '{"line": 4}')
    assert received == [4]

    bm_editor.on_new_data_received('_toggle_bookmark_request', '{"line": 0}')
    bm_editor.on_new_data_received('_toggle_bookmark_request', 'null')
    assert received == [4]
