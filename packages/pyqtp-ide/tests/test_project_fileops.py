"""Тесты файловых операций «Проводника» (этап 9.5).

Юниты ``project_fileops``/``file_rename`` — без GUI и без LSP-процессов.
Контекстное меню дерева и обработчики ProjectControl — offscreen
``IdeMainWindow`` с KDD (паттерн фикстур как в ``test_workspace_symbol.py``);
диалоги и хаб подменяются (реальный ``willRenameFiles`` — живой тест с
маркером ``live``). Корзина: если в окружении её нет — ``skip``.
"""

import shutil
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QPoint, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.lsp_control.file_rename import (
    is_empty_rename_result,
    merge_will_rename_results,
    rename_files_params,
    supports_will_rename,
)
from pyqtp.controls.lsp_control.lsp_hub import (
    HUB_CAPABILITIES,
    LspHub,
)
from pyqtp.controls.project_control import project_control as pc_mod
from pyqtp.controls.project_control.project_fileops import (
    ProjectOpError,
    create_entry,
    move_to_trash,
    renamed_path,
    validate_entry_name,
)
from pyqtp.main_wnd.ide_main_window import IdeMainWindow
from pyqtp.views.project_tree_view import ProjectTreeView

####################################
# project_fileops (юниты, без GUI)


def test_validate_entry_name_basic() -> None:
    assert validate_entry_name('  mod.py  ') == 'mod.py'
    assert validate_entry_name('новая папка') == 'новая папка'
    with pytest.raises(ProjectOpError):
        validate_entry_name('   ')
    with pytest.raises(ProjectOpError):
        validate_entry_name('.')
    with pytest.raises(ProjectOpError):
        validate_entry_name('..')
    with pytest.raises(ProjectOpError):
        validate_entry_name('a/b')
    with pytest.raises(ProjectOpError):
        validate_entry_name('a\\b')


def test_create_entry_file_and_collisions(tmp_path: Path) -> None:
    created = create_entry(tmp_path, 'new.txt', is_dir=False)
    assert created == tmp_path / 'new.txt'
    assert created.is_file()
    with pytest.raises(ProjectOpError):  # перезапись — не задача создания
        create_entry(tmp_path, 'new.txt', is_dir=False)
    with pytest.raises(ProjectOpError):  # родитель обязан существовать
        create_entry(tmp_path / 'нет-такой', 'x.txt', is_dir=False)
    with pytest.raises(ProjectOpError):  # невалидное имя
        create_entry(tmp_path, '..', is_dir=False)


def test_create_entry_dir(tmp_path: Path) -> None:
    created = create_entry(tmp_path, 'пакет', is_dir=True)
    assert created.is_dir()
    with pytest.raises(ProjectOpError):
        create_entry(tmp_path, 'пакет', is_dir=True)


def test_renamed_path_basic(tmp_path: Path) -> None:
    target = tmp_path / 'mod.py'
    target.write_text('x = 1\n', encoding='utf-8')
    assert renamed_path(target, ' helper.py ') == tmp_path / 'helper.py'
    with pytest.raises(ProjectOpError):  # то же имя
        renamed_path(target, 'mod.py')
    (tmp_path / 'busy.py').touch()
    with pytest.raises(ProjectOpError):  # имя занято
        renamed_path(target, 'busy.py')


def test_move_to_trash_missing_returns_false() -> None:
    assert move_to_trash(Path('/нет/такого/пути-95')) is False


def test_move_to_trash_real(tmp_path: Path) -> None:
    target = tmp_path / 'trash_me.txt'
    target.write_text('мусор\n', encoding='utf-8')
    if not move_to_trash(target):
        pytest.skip('в этом окружении нет доступной корзины')
    assert not target.exists()


#################################
# file_rename (юниты, без GUI)


def test_supports_will_rename_both_spellings() -> None:
    assert supports_will_rename(
        {'capabilities': {'workspace': {'willRenameFiles': True}}}
    )
    assert supports_will_rename(
        {'capabilities': {'workspace': {'willRenameFiles': {}}}}
    )
    # basedpyright/pyright объявляют в своей расширенной подсекции
    assert supports_will_rename(
        {'capabilities': {'workspace': {'fileOperations': {'willRename': {}}}}}
    )
    assert supports_will_rename(
        {'capabilities': {'workspace': {'fileOperations': {'willRename': True}}}}
    )


def test_supports_will_rename_negatives() -> None:
    assert not supports_will_rename(
        {'capabilities': {'workspace': {'willRenameFiles': False}}}
    )
    assert not supports_will_rename(
        {'capabilities': {'workspace': {'fileOperations': {'willRename': False}}}}
    )
    assert not supports_will_rename(
        {'capabilities': {'workspace': {'workspaceFolders': True}}}
    )
    assert not supports_will_rename({'capabilities': {}})
    assert not supports_will_rename({})
    assert not supports_will_rename(None)


def test_rename_files_params_shape() -> None:
    assert rename_files_params('file:///a', 'file:///b') == {
        'files': [{'oldUri': 'file:///a', 'newUri': 'file:///b'}]
    }


def test_is_empty_rename_result() -> None:
    assert is_empty_rename_result(None)
    assert is_empty_rename_result({})
    assert is_empty_rename_result({'changes': {}, 'documentChanges': []})
    assert is_empty_rename_result('мусор')
    assert not is_empty_rename_result(
        {'changes': {'file:///a.py': [{'range': {}, 'newText': 'x'}]}}
    )
    assert not is_empty_rename_result(
        {'documentChanges': [
            {'textDocument': {'uri': 'file:///a.py'}, 'edits': [{}]}
        ]}
    )
    # ресурсные операции (Create/Rename/Delete) — тоже непустой ответ
    assert not is_empty_rename_result(
        {'documentChanges': [{'kind': 3, 'uri': 'file:///a.py'}]}
    )


def test_merge_will_rename_results() -> None:
    empty = merge_will_rename_results([None, {}, {'changes': {}}])
    assert empty is None

    merged = merge_will_rename_results([
        {'changes': {'file:///a.py': [{'newText': 'a'}]}},
        {'documentChanges': [
            {'textDocument': {'uri': 'file:///b.py'}, 'edits': [{'newText': 'b'}]},
        ]},
        {},
    ])
    assert merged is not None
    changes = merged['documentChanges']
    # форма changes нормализована тем же хелпером, что применяет этап 8.2;
    # порядок — за списком ответов
    assert [c['textDocument']['uri'] for c in changes] == [
        'file:///a.py', 'file:///b.py',
    ]


def test_hub_declares_will_rename_files_capability() -> None:
    """Хаб обязан заявить серверу willRenameFiles (иначе basedpyright правки
    импортов не шлёт — regression-guard на манер test_hub_caps)."""
    ws_caps = HUB_CAPABILITIES['workspace']
    assert ws_caps['willRenameFiles'] == {'dynamicRegistration': False}

###########################
# Offscreen GUI-фикстуры


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


@pytest.fixture
def prj_root(tmp_path: Path) -> Path:
    """Небольшой проект для меню/диалогов (без LSP-смысла)."""
    root = tmp_path / 'proj'
    (root / 'pkg').mkdir(parents=True)
    (root / 'pkg' / 'mod.py').write_text('class Helper:\n    pass\n', encoding='utf-8')
    (root / 'main.py').write_text(
        'from pkg.mod import Helper\n', encoding='utf-8'
    )
    return root


class _FakeInput:
    """Замена QInputDialog: всегда отдаёт заранее подготовленный ответ."""

    def __init__(self, answer: tuple[str, bool]) -> None:
        self.answer = answer
        self.asked: list[tuple] = []

    def getText(self, *args: object) -> tuple[str, bool]:  # noqa: N802
        self.asked.append(args)
        return self.answer


class _FakeMessageBox:
    """Замена QMessageBox: question отдаёт ответ, warning собираем."""

    StandardButton = QMessageBox.StandardButton

    def __init__(self, answer: QMessageBox.StandardButton) -> None:
        self.answer = answer
        self.warnings: list[str] = []

    def question(self, *args: object) -> QMessageBox.StandardButton:
        return self.answer

    def warning(self, _parent: object, _title: str, text: str) -> None:
        self.warnings.append(text)


def _wait_tree_rows(qtbot: QtBot, ctrl: pc_mod.ProjectControl, count: int) -> None:
    view = ctrl.tree_view
    assert view is not None
    qtbot.waitUntil(
        lambda: view._proxy.rowCount(view._tree.rootIndex()) >= count,
        timeout=10_000,
    )


#####################################
# Контекстное меню дерева (offscreen)


def test_tree_context_menu_actions(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Файл: открыть/переименовать/удалить; каталог: + создание; пустая
    область: только создание в корне; всё — сигналы с нужными путями."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(prj_root) is True
    _wait_tree_rows(qtbot, ctrl, 2)
    view = ctrl.tree_view
    assert view is not None

    root = ctrl.project_root
    assert root is not None

    # триггеры пунктов меню доходят и до штатных обработчиков ProjectControl —
    # гасим их безопасными заглушками (сами обработчики — отдельные тесты)
    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('', False)))
    monkeypatch.setattr(pc_mod, 'QMessageBox', _FakeMessageBox(
        _FakeMessageBox.StandardButton.No
    ))
    monkeypatch.setattr(wnd.doc_ctrl, 'open_document', lambda _p: None)

    # без проекта меню нет (отдельная проверка — на новом представлении)
    bare = ProjectTreeView(wnd, key='TEST_bare_tree')
    assert bare._build_context_menu(QPoint(1, 1)) is None
    wnd.view_register(bare, widget_area='right')

    # --- файл ---
    row = view._find_row(view._tree.rootIndex(), 'main.py')
    assert row >= 0
    file_index = view._proxy.index(row, 0, view._tree.rootIndex())
    monkeypatch.setattr(view._tree, 'indexAt', lambda _pos: file_index)
    menu = view._build_context_menu(QPoint(10, 10))
    assert menu is not None
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ['Открыть', 'Переименовать', 'Удалить в корзину']

    spy_open: list[str] = []
    spy_rename: list[str] = []
    spy_delete: list[str] = []
    view.file_open_requested.connect(spy_open.append)
    view.rename_requested.connect(spy_rename.append)
    view.delete_requested.connect(spy_delete.append)
    by_text = {a.text(): a for a in menu.actions()}
    by_text['Открыть'].trigger()
    by_text['Переименовать'].trigger()
    by_text['Удалить в корзину'].trigger()
    assert spy_open == [str(root / 'main.py')]
    assert spy_rename == [str(root / 'main.py')]
    assert spy_delete == [str(root / 'main.py')]

    # --- каталог ---
    row = view._find_row(view._tree.rootIndex(), 'pkg')
    assert row >= 0
    dir_index = view._proxy.index(row, 0, view._tree.rootIndex())
    monkeypatch.setattr(view._tree, 'indexAt', lambda _pos: dir_index)
    menu = view._build_context_menu(QPoint(10, 10))
    assert menu is not None
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == [
        'Новый файл', 'Новая папка', 'Переименовать', 'Удалить в корзину',
    ]

    # --- пустая область: только создание в корне ---
    monkeypatch.setattr(view._tree, 'indexAt', lambda _pos: QModelIndex())
    menu = view._build_context_menu(QPoint(10, 4000))
    assert menu is not None
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ['Новый файл', 'Новая папка']

    assert ctrl.close_project() is True
    wnd.view_deregister(bare.key)


########################################
# Обработчики ProjectControl (offscreen)


def test_create_file_and_folder(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(prj_root) is True
    _wait_tree_rows(qtbot, ctrl, 2)
    root = ctrl.project_root
    assert root is not None

    fake_box = _FakeMessageBox(_FakeMessageBox.StandardButton.No)
    monkeypatch.setattr(pc_mod, 'QMessageBox', fake_box)
    monkeypatch.setattr(
        pc_mod, 'QInputDialog', _FakeInput(('новая.txt', True))
    )
    ctrl._on_create_file_requested(str(root / 'pkg'))
    assert (root / 'pkg' / 'новая.txt').is_file()

    monkeypatch.setattr(
        pc_mod, 'QInputDialog', _FakeInput(('подпапка', True))
    )
    ctrl._on_create_folder_requested(str(root))
    assert (root / 'подпапка').is_dir()

    # коллизия — предупреждение, ничего не меняется
    monkeypatch.setattr(
        pc_mod, 'QInputDialog', _FakeInput(('новая.txt', True))
    )
    ctrl._on_create_file_requested(str(root / 'pkg'))
    assert fake_box.warnings, 'ожидалось предупреждение о коллизии'
    assert (root / 'pkg' / 'новая.txt').is_file()

    # отмена диалога — никакой реакции
    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('что-угодно', False)))
    ctrl._on_create_file_requested(str(root))
    assert not (root / 'что-угодно').exists()

    assert ctrl.close_project() is True


def test_rename_no_lsp_applies_disk_edit_and_moves(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный путь без вкладок: фейковый willRenameFiles чинит импорт на
    диске, файл переименован, хаб уведомлён, устаревшие ответы игнорируются."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(prj_root) is True
    _wait_tree_rows(qtbot, ctrl, 2)
    root = ctrl.project_root
    assert root is not None
    mod = root / 'pkg' / 'mod.py'

    edit = {
        'documentChanges': [{
            'textDocument': {'uri': (root / 'main.py').as_uri()},
            'edits': [{
                # 'mod' в 'from pkg.mod import Helper' — UTF-16 колонки 9..12
                'range': {
                    'start': {'line': 0, 'character': 9},
                    'end': {'line': 0, 'character': 12},
                },
                'newText': 'helper',
            }],
        }],
    }
    seen: list[tuple[str, str]] = []

    def fake_will(
        _root: Path, old_uri: str, new_uri: str,
        cb: Callable[[dict, str | None], None] | None = None,
        timeout: float = 5.0,
    ) -> None:
        seen.append((old_uri, new_uri))
        assert cb is not None
        cb(edit, None)  # same-thread emit -> queued-мост доставляет напрямую

    did: list[tuple[str, str]] = []
    monkeypatch.setattr(
        wnd.lsp_hub, 'will_rename_files_async', fake_will
    )
    monkeypatch.setattr(
        wnd.lsp_hub, 'did_rename_files',
        lambda _root, old_uri, new_uri: did.append((old_uri, new_uri)),
    )
    monkeypatch.setattr(pc_mod, 'QMessageBox', _FakeMessageBox(
        _FakeMessageBox.StandardButton.Yes
    ))
    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('helper.py', True)))

    ctrl._on_rename_requested(str(mod))

    helper = root / 'pkg' / 'helper.py'
    assert helper.is_file()
    assert not mod.exists()
    assert helper.read_text(encoding='utf-8') == 'class Helper:\n    pass\n'
    assert (root / 'main.py').read_text(encoding='utf-8') == (
        'from pkg.helper import Helper\n'
    )
    assert seen == [(mod.as_uri(), helper.as_uri())]
    assert did == [(mod.as_uri(), helper.as_uri())]
    assert ctrl._pending_renames == {}

    assert ctrl.close_project() is True


def test_rename_guards_modified_refuses_and_reopens(
    wnd: IdeMainWindow, prj_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ при несохранённых правках; закрытый+закрытый чистый — закрыть до
    операции и переоткрыть после; сбой хаба не блокирует переименование."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(prj_root) is True
    root = ctrl.project_root
    assert root is not None
    mod = root / 'pkg' / 'mod.py'
    helper = root / 'pkg' / 'helper.py'

    closed: list[str] = []
    opened: list[str] = []
    doc_ctrl = wnd.doc_ctrl
    monkeypatch.setattr(doc_ctrl, 'has_document', lambda p: str(p) == str(mod))
    monkeypatch.setattr(doc_ctrl, 'document_is_modified', lambda p: True)
    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('helper.py', True)))
    fake_box = _FakeMessageBox(_FakeMessageBox.StandardButton.Yes)
    monkeypatch.setattr(pc_mod, 'QMessageBox', fake_box)

    ctrl._on_rename_requested(str(mod))
    assert fake_box.warnings
    assert mod.exists() and not helper.exists()

    # чистый открытый: закрываем до, переоткрываем после; хаб падает — всё
    # равно обходимся без правок
    monkeypatch.setattr(doc_ctrl, 'document_is_modified', lambda p: False)
    monkeypatch.setattr(
        doc_ctrl, 'close_document',
        lambda p: closed.append(str(p)) or True,
    )
    monkeypatch.setattr(
        doc_ctrl, 'open_document', lambda p: opened.append(str(p))
    )

    def broken_will(*_args: object, **_kwargs: object) -> None:
        msg = 'LSP hub остановлен'
        raise RuntimeError(msg)

    monkeypatch.setattr(wnd.lsp_hub, 'will_rename_files_async', broken_will)
    monkeypatch.setattr(
        wnd.lsp_hub, 'did_rename_files', lambda *_a: None
    )

    ctrl._on_rename_requested(str(mod))
    assert closed == [str(mod)]
    assert opened == [str(helper)]
    assert helper.is_file() and not mod.exists()

    # коллизия имени на старте — предупреждение, файл на месте
    (root / 'taken.py').write_text('занято\n', encoding='utf-8')
    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('taken.py', True)))
    before = len(fake_box.warnings)
    ctrl._on_rename_requested(str(root / 'main.py'))
    assert len(fake_box.warnings) == before + 1
    assert (root / 'main.py').exists()

    assert ctrl.close_project() is True


def test_delete_flows(
    wnd: IdeMainWindow, prj_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ (No) оставляет файл; подтверждение закрывает документы и метит в
    корзину (или предупреждает, если корзины нет); изменённый документ — отказ."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(prj_root) is True
    root = ctrl.project_root
    assert root is not None
    victim = root / 'main.py'
    mod = root / 'pkg' / 'mod.py'
    doc_ctrl = wnd.doc_ctrl

    # No — ничего не происходит
    monkeypatch.setattr(pc_mod, 'QMessageBox', _FakeMessageBox(
        _FakeMessageBox.StandardButton.No
    ))
    monkeypatch.setattr(doc_ctrl, 'documents_under', lambda p: (
        [mod] if Path(p) == mod else [victim, mod]
    ))
    ctrl._on_delete_requested(str(victim))
    assert victim.exists()

    # несохранённые правки под удаляемым путём — отказ
    box = _FakeMessageBox(_FakeMessageBox.StandardButton.Yes)
    monkeypatch.setattr(pc_mod, 'QMessageBox', box)
    monkeypatch.setattr(doc_ctrl, 'document_is_modified', lambda p: p == mod)
    ctrl._on_delete_requested(str(root / 'pkg'))
    assert box.warnings
    assert mod.exists()

    # чистые документы закрываются перед корзиной
    closed: list[str] = []
    before = len(box.warnings)
    monkeypatch.setattr(doc_ctrl, 'document_is_modified', lambda _p: False)
    monkeypatch.setattr(
        doc_ctrl, 'close_document',
        lambda p: closed.append(str(p)) or True,
    )
    ctrl._on_delete_requested(str(mod))
    assert closed == [str(mod)]
    if mod.exists():
        # корзина недоступна — обязаны предупредить и не удалять навсегда
        assert len(box.warnings) == before + 1 and 'корзину' in box.warnings[-1]
    else:
        assert len(box.warnings) == before

    assert ctrl.close_project() is True

###########################
# Живой хаб: willRenameFiles (реальный basedpyright)

needs_bp = pytest.mark.skipif(
    shutil.which('basedpyright-langserver') is None,
    reason='нужен basedpyright-langserver в PATH',
)


@pytest.fixture
def hub() -> Iterator[LspHub]:
    instance = LspHub()
    yield instance
    instance.shutdown()


@pytest.fixture
def bp_rename_root(tmp_path: Path) -> Path:
    """python-проект с импортом, который basedpyright правит при rename."""
    root = tmp_path / 'proj'
    (root / 'pkg').mkdir(parents=True)
    (root / 'pyproject.toml').write_text(
        '[project]\nname = "rn"\n', encoding='utf-8'
    )
    (root / 'pkg' / 'mod.py').write_text(
        'class Helper:\n    pass\n', encoding='utf-8'
    )
    (root / 'main.py').write_text(
        'from pkg.mod import Helper\n\nh = Helper()\n', encoding='utf-8'
    )
    return root


@needs_bp
@pytest.mark.live
def test_live_will_rename_returns_import_edit(
    hub: LspHub, bp_rename_root: Path
) -> None:
    """basedpyright отвечает documentChanges с правкой импорта в main.py
    (прогрев холодного ответа встроен в хаб)."""
    hub.ensure_started()
    old = bp_rename_root / 'pkg' / 'mod.py'
    new = bp_rename_root / 'pkg' / 'helper.py'
    edit, error = hub.will_rename_files_async(
        bp_rename_root, old.as_uri(), new.as_uri()
    ).result(timeout=90)
    assert error is None, error
    assert edit is not None
    uris = {
        str((c.get('textDocument') or {}).get('uri') or '')
        for c in edit.get('documentChanges', [])
    }
    assert (bp_rename_root / 'main.py').as_uri() in uris, uris


@needs_bp
@pytest.mark.live
def test_live_will_rename_cb_always_fires(
    hub: LspHub, bp_rename_root: Path
) -> None:
    """cb приходит из потока хаба даже при пустом стеке серверов (txt)."""
    hub.ensure_started()
    calls: list[tuple[object, object]] = []
    done = threading.Event()

    def cb(edit: object, error: object) -> None:
        calls.append((edit, error))
        done.set()

    txt = bp_rename_root / 'notes.txt'
    txt.write_text('привет\n', encoding='utf-8')
    hub.will_rename_files_async(
        bp_rename_root, txt.as_uri(), (bp_rename_root / 'notes2.txt').as_uri(),
        cb=cb,
    )
    assert done.wait(30)
    assert calls == [(None, None)] or (
        len(calls) == 1 and calls[0][0] is None
    )


@needs_bp
@pytest.mark.live
def test_live_control_rename_end_to_end(
    wnd: IdeMainWindow,
    bp_rename_root: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приёмка 9.5 целиком: диалог имени -> живой willRenameFiles -> правка
    импорта на диске -> переименование -> didRenameFiles."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(bp_rename_root) is True
    root = ctrl.project_root
    assert root is not None
    mod = root / 'pkg' / 'mod.py'

    monkeypatch.setattr(pc_mod, 'QInputDialog', _FakeInput(('helper.py', True)))
    monkeypatch.setattr(pc_mod, 'QMessageBox', _FakeMessageBox(
        _FakeMessageBox.StandardButton.Yes
    ))

    ctrl._on_rename_requested(str(mod))

    helper = root / 'pkg' / 'helper.py'
    qtbot.waitUntil(
        lambda: helper.is_file()
        and not mod.exists()
        and 'pkg.helper' in (root / 'main.py').read_text(encoding='utf-8'),
        timeout=90_000,
    )
    assert 'from pkg.mod' not in (root / 'main.py').read_text(encoding='utf-8')
    assert ctrl._pending_renames == {}

    assert ctrl.close_project() is True

