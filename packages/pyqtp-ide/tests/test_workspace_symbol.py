"""Тесты workspace/symbol (остаток этапа 8, пункт остатка этапа 9).

Юниты разбора ответа/реестра — без GUI и без LSP-процессов (парсер кормится
записанными формами ответа). Живые тесты хаба — реальный basedpyright
(маркер ``live``, ``skipif`` без бинаря); живые тесты диалога — offscreen
``IdeMainWindow`` с KDD (паттерн фикстур как в ``test_project_search.py``).
"""

import shutil
import threading
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.lsp_control.lsp_hub import (
    HUB_CAPABILITIES,
    LspHub,
)
from pyqtp.controls.lsp_control.lsp_registry import LspRegistry
from pyqtp.controls.lsp_control.workspace_symbol import (
    WorkspaceSymbol,
    open_payload,
    parse_workspace_symbols,
    spec_supports_workspace_symbol,
)
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

###########################
# Разбор ответа (юниты, без GUI)


def test_parse_symbol_information_form() -> None:
    """SymbolInformation (3.16): location.range, containerName, 0-based координаты."""
    result = [{
        'name': 'MyClass',
        'kind': 5,
        'containerName': 'pkg',
        'location': {
            'uri': 'file:///проект/pkg/mod.py',
            'range': {
                'start': {'line': 2, 'character': 6},
                'end': {'line': 2, 'character': 13},
            },
        },
    }]
    symbols = parse_workspace_symbols(result)
    assert len(symbols) == 1
    sym = symbols[0]
    assert sym.name == 'MyClass'
    assert sym.kind == 5
    assert sym.container == 'pkg'
    assert sym.file_uri == 'file:///проект/pkg/mod.py'
    assert (sym.line, sym.column) == (3, 7)  # LSP 0-based -> Monaco 1-based


def test_parse_workspace_symbol_317_forms() -> None:
    """WorkspaceSymbol 3.17: location без range + устаревшие range/selectionRange."""
    symbols = parse_workspace_symbols([
        {  # location.range — приоритетная форма
            'name': 'A',
            'kind': 12,
            'location': {
                'uri': 'file:///a.py',
                'range': {'start': {'line': 0, 'character': 4}},
            },
        },
        {  # location только uri, range на верхнем уровне (deprecated)
            'name': 'B',
            'kind': 13,
            'location': {'uri': 'file:///b.py'},
            'range': {'start': {'line': 9, 'character': 0}},
        },
        {  # ни там ни там — fallback (1, 1), переход в начало файла
            'name': 'C',
            'kind': 13,
            'location': {'uri': 'file:///c.py'},
            'selectionRange': {'start': {'line': 5, 'character': 2}},
        },
    ])
    assert [(s.line, s.column) for s in symbols] == [(1, 5), (10, 1), (6, 3)]
    assert symbols[1].container == ''


def test_parse_skips_malformed_and_accepts_none() -> None:
    assert parse_workspace_symbols(None) == []
    assert parse_workspace_symbols({'не': 'список'}) == []
    symbols = parse_workspace_symbols([
        None,
        {'name': 42, 'location': {'uri': 'file:///a.py'}},   # имя не строка
        {'name': 'x', 'location': 'мусор'},                   # location не объект
        {'name': 'x', 'location': {'range': {}}},             # uri нет
        {'name': 'ok', 'location': {'uri': 'file:///ok.py'}},
    ])
    assert [s.name for s in symbols] == ['ok']
    assert symbols[0].kind == 0  # нет kind -> 0 («неизвестно»)


def test_spec_supports_workspace_symbol_gate() -> None:
    assert spec_supports_workspace_symbol(
        {'capabilities': {'workspaceSymbolProvider': True}}
    )
    # boolean | WorkspaceSymbolOptions: пустой объект — тоже «поддерживает»
    assert spec_supports_workspace_symbol(
        {'capabilities': {'workspaceSymbolProvider': {}}}
    )
    assert not spec_supports_workspace_symbol(
        {'capabilities': {'workspaceSymbolProvider': False}}
    )
    assert not spec_supports_workspace_symbol({'capabilities': {}})
    assert not spec_supports_workspace_symbol({})
    assert not spec_supports_workspace_symbol(None)


def test_open_payload_monaco_shape() -> None:
    """Payload — тот же формат, что ждёт ``DocumentManager.open_definition_uri``."""
    sym = WorkspaceSymbol(
        name='f', kind=12, container='mod',
        file_uri='file:///a.py', line=7, column=5,
    )
    assert open_payload(sym) == {
        'uri': 'file:///a.py',
        'range': {'startLineNumber': 7, 'startColumn': 5},
    }


def test_symbol_dataclass_shape(tmp_path: Path) -> None:
    """WorkspaceSymbol — замороженный dataclass."""
    sym = WorkspaceSymbol(
        name='x', kind=13, container='',
        file_uri=tmp_path.as_uri(), line=1, column=1,
    )
    with pytest.raises(FrozenInstanceError):
        sym.line = 2  # type: ignore[misc]


def test_hub_declares_workspace_symbol_capability() -> None:
    """Хаб обязан заявить серверу workspaceSymbol (иначе сервер не включает
    поддержку — regression-guard на манер test_hub_caps_semantic_tokens)."""
    ws_caps = HUB_CAPABILITIES['workspace']
    assert ws_caps['workspaceSymbol'] == {'dynamicRegistration': False}


###########################
# Реестр: образцовый файл языка (юниты, без GUI)


def test_sample_file_for_picks_first_sorted(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    (root / 'pkg').mkdir(parents=True)
    (root / 'zz.py').write_text('x = 1\n', encoding='utf-8')
    (root / 'pkg' / 'a.py').write_text('y = 2\n', encoding='utf-8')
    reg = LspRegistry.default()
    # язык нет в корневом слое — обход находит вложенный (top-down)
    assert reg.sample_file_for(root, 'python') == root / 'zz.py'


def test_sample_file_for_prunes_and_dotfiles(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    (root / 'node_modules').mkdir(parents=True)
    (root / 'node_modules' / 'only.py').write_text('x = 1\n', encoding='utf-8')
    (root / '.hidden.py').write_text('x = 1\n', encoding='utf-8')
    reg = LspRegistry.default()
    assert reg.sample_file_for(root, 'python') is None
    (root / 'real.py').write_text('x = 1\n', encoding='utf-8')
    assert reg.sample_file_for(root, 'python') == root / 'real.py'


def test_sample_file_for_unknown_language(tmp_path: Path) -> None:
    (tmp_path / 'a.py').write_text('x = 1\n', encoding='utf-8')
    reg = LspRegistry.default()
    assert reg.sample_file_for(tmp_path, 'fortran') is None  # языка нет
    assert reg.sample_file_for(tmp_path, 'cpp') is None      # файлов нет


###########################
# Живой хаб: workspace_symbols_async (реальный basedpyright)

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
def bp_root(tmp_path: Path) -> Path:
    """Проект с маркером корня и символами для workspace/symbol."""
    root = tmp_path / 'proj'
    (root / 'pkg').mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname = "ws"\n', encoding='utf-8')
    (root / 'pkg' / 'mod.py').write_text(
        'import os\n\n\nclass MyClass:\n'
        '    def my_method(self) -> None:\n'
        '        print(os.getcwd())\n\n\ndef my_helper() -> int:\n'
        '    return 1\n',
        encoding='utf-8',
    )
    return root


@needs_bp
@pytest.mark.live
def test_live_workspace_symbols_result(hub: LspHub, bp_root: Path) -> None:
    """Запрос через хаб: сервер выбран по образцовому файлу, разбор 1-based."""
    hub.ensure_started()
    fut = hub.workspace_symbols_async(bp_root, 'MyCl')
    symbols, error = fut.result(timeout=90)
    assert error is None, error
    by_name = {s.name: s for s in symbols}
    assert 'MyClass' in by_name, sorted(by_name)
    sym = by_name['MyClass']
    assert sym.file_uri == (bp_root / 'pkg' / 'mod.py').as_uri()
    assert sym.line == 4  # 'class MyClass:' — 4-я строка (1-based, PEP8 отступы)
    assert sym.column == 7  # на ident 'MyClass' (LSP character 6 -> Monaco 7)
    payload = open_payload(sym)
    assert payload['range']['startLineNumber'] == 4
    # kind (класс=5) и uri в payload — переход возможен в тот же файл
    assert payload['uri'] == sym.file_uri


@needs_bp
@pytest.mark.live
def test_live_workspace_symbols_cb_and_stale(
    hub: LspHub, bp_root: Path
) -> None:
    """cb приходит с результатом; запрос, помеченный устаревшим, до cb не доживает."""
    hub.ensure_started()

    calls: list[tuple[list[WorkspaceSymbol], str | None]] = []
    done = threading.Event()

    def cb(symbols: list[WorkspaceSymbol], error: str | None) -> None:
        calls.append((symbols, error))
        done.set()

    fut = hub.workspace_symbols_async(bp_root, 'helper', cb=cb)
    # имитация более нового запроса: снимает cb этого запроса (запоздалый ответ
    # не должен дойти до GUI) — future при этом продолжает резолвиться
    fut2 = hub.workspace_symbols_async(bp_root, 'MyCl', cb=cb)
    assert done.wait(90)  # дошёл только актуальный (поколение 2)
    assert len(calls) == 1
    names = {s.name for s in calls[0][0]}
    assert 'MyClass' in names, names
    fut2.result(timeout=90)
    # сам старый future резолвится штатно — просто не зовёт cb
    symbols1, error1 = fut.result(timeout=90)
    assert error1 is None, error1
    assert 'my_helper' in {s.name for s in symbols1}
    assert len(calls) == 1


@needs_bp
@pytest.mark.live
def test_live_workspace_symbols_unsupported_language(
    hub: LspHub, tmp_path: Path
) -> None:
    """Корень без файлов известных языков — понятная ошибка, не зависание."""
    hub.ensure_started()
    empty = tmp_path / 'empty'
    empty.mkdir()
    symbols, error = hub.workspace_symbols_async(empty, 'x').result(timeout=30)
    assert symbols == []
    assert error  # не пусто: «...не поддерживает workspace/symbol» или нет сервера


###########################
# Живой диалог (offscreen GUI + реальный basedpyright)


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


def test_symbol_action_tracks_project(wnd: IdeMainWindow, bp_root: Path) -> None:
    """Ctrl+T погашен без проекта, активен с проектом, снова погас без него."""
    ctrl = wnd.project_ctrl
    assert ctrl.action_workspace_symbols.isEnabled() is False

    assert ctrl.open_project(bp_root) is True
    assert ctrl.action_workspace_symbols.isEnabled() is True

    assert ctrl.close_project() is True
    assert ctrl.action_workspace_symbols.isEnabled() is False


@needs_bp
@pytest.mark.live
def test_symbol_dialog_results_and_open(
    wnd: IdeMainWindow,
    bp_root: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Диалог: дебаунс -> живой запрос -> список символов -> payload открытия."""
    opened: list[dict] = []
    monkeypatch.setattr(
        wnd.doc_ctrl, 'open_definition_uri', lambda payload: opened.append(payload)
    )
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(bp_root) is True

    ctrl._show_workspace_symbols()
    dialog = ctrl._symbol_dialog
    assert dialog is not None
    assert dialog._root == bp_root.resolve()

    dialog._edit.setText('MyCl')
    # статус «Поиск…» сразу (дебаунс ещё не истёк)
    assert str(dialog._status_label.text()) == 'Поиск…'

    # cold start: поднимается процесс basedpyright + первый символ-запрос
    qtbot.waitUntil(
        lambda: 'Совпадений:' in str(dialog._status_label.text())
        and dialog._list.count() > 0,
        timeout=90_000,
    )
    item = dialog._list.item(0)
    assert item is not None
    assert 'MyClass' in item.text()
    assert dialog._list.currentRow() == 0

    dialog._accept_item(item)
    assert len(opened) == 1
    payload = opened[0]
    assert payload['uri'] == QUrl.fromLocalFile(
        str(bp_root / 'pkg' / 'mod.py')
    ).toString()
    assert payload['range']['startLineNumber'] == 4

    assert ctrl.close_project() is True
    assert str(dialog._status_label.text()) == 'Проект не открыт'
    assert dialog._list.count() == 0
