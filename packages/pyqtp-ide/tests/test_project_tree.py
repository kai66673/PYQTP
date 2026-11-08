"""Живые (offscreen-GUI, без LSP/WebEngine) тесты панели проекта (этап 9.1).

Окно — полный ``IdeMainWindow`` с KDD-доками; фикстура повторяет паттерн
core-теста ``test_settings_window.py`` (уникальное пространство QSettings
на тест + lifecycle без launcher'а). ``DocumentManager.open_document``
подменяется — редакторы не поднимаем.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

# ключ контрола проекта для проверки QSettings (classproperty = qualname)
PROJECT_CONTROL_KEY = 'ProjectControl'


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
def project_root(tmp_path: Path) -> Path:
    """Фиктивное дерево проекта с сервисными/точечными каталогами."""
    root = tmp_path / 'proj'
    (root / 'pkg' / 'sub').mkdir(parents=True)
    (root / 'a.py').write_text('x = 1\n', encoding='utf-8')
    (root / 'b.txt').write_text('', encoding='utf-8')
    (root / 'pkg' / 'sub' / 'deep.py').write_text('z = 3\n', encoding='utf-8')
    (root / '__pycache__').mkdir()
    (root / 'node_modules').mkdir()
    (root / '.venv').mkdir()
    (root / '.hidden.txt').write_text('', encoding='utf-8')
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


def visible_names(wnd: IdeMainWindow) -> set[str]:
    """Имена верхнего уровня, видимые в дереве (через прокси-фильтр)."""
    view = wnd.project_ctrl.tree_view
    assert view is not None
    root_index = view._tree.rootIndex()
    return {
        str(view._proxy.data(view._proxy.index(row, 0, root_index)))
        for row in range(view._proxy.rowCount(root_index))
    }


def test_open_project_populates_tree(
    wnd: IdeMainWindow, project_root: Path, qtbot: QtBot
) -> None:
    """Открытие папки: панель слева, верхний уровень без сервисного мусора."""
    ctrl = wnd.project_ctrl
    assert ctrl.project_root is None
    assert ctrl.action_close_folder.isEnabled() is False

    assert ctrl.open_project(project_root) is True
    assert ctrl.project_root == project_root
    assert ctrl.action_close_folder.isEnabled() is True

    view = ctrl.tree_view
    assert view is not None
    assert view.windowTitle() == 'Проводник'
    # панель зарегистрирована (ключ вешаем на key контрола)
    assert wnd.view_get(f'{ctrl.key}_tree_view') is view

    src_root = view._model.index(str(project_root))
    qtbot.waitUntil(
        lambda: view._model.rowCount(src_root) > 0,
        timeout=10_000,
    )
    assert visible_names(wnd) == {'a.py', 'b.txt', 'pkg'}
    # в исходной модели мусор есть — режет только прокси
    source_names = {
        view._model.fileName(view._model.index(row, 0, src_root))
        for row in range(view._model.rowCount(src_root))
    }
    assert {'__pycache__', 'node_modules'} <= source_names

    # пункт «Открыть папку...» живёт в меню «Файл»
    file_menu = wnd.menuBar().by_path('Файл')
    assert isinstance(file_menu, QMenu)
    assert ctrl.action_open_folder in file_menu.actions()


def test_double_click_file_opens_document(
    wnd: IdeMainWindow,
    project_root: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Двойной клик по файлу в дереве -> DocumentManager.open_document."""
    opened: list[str] = []
    monkeypatch.setattr(
        wnd.doc_ctrl, 'open_document', lambda path: opened.append(str(path))
    )
    assert wnd.project_ctrl.open_project(project_root) is True
    view = wnd.project_ctrl.tree_view
    assert view is not None

    qtbot.waitUntil(
        lambda: visible_names(wnd) == {'a.py', 'b.txt', 'pkg'},
        timeout=10_000,
    )
    root_index = view._tree.rootIndex()

    def child_by_name(name: str) -> object:
        return next(
            view._proxy.index(row, 0, root_index)
            for row in range(view._proxy.rowCount(root_index))
            if str(view._proxy.data(view._proxy.index(row, 0, root_index)))
            == name
        )

    view._tree.activated.emit(child_by_name('a.py'))
    assert opened == [str(project_root / 'a.py')]

    # каталог по двойному клику документом не становится
    view._tree.activated.emit(child_by_name('pkg'))
    assert opened == [str(project_root / 'a.py')]


def test_reveal_selects_nested_file(
    wnd: IdeMainWindow, project_root: Path, qtbot: QtBot
) -> None:
    """reveal_path: раскрыть pkg/sub и выделить deep.py (асинх. заполнение)."""
    assert wnd.project_ctrl.open_project(project_root) is True
    view = wnd.project_ctrl.tree_view
    assert view is not None

    target = project_root / 'pkg' / 'sub' / 'deep.py'
    view.reveal_path(target)

    def current_path() -> str:
        current = view._tree.currentIndex()
        if not current.isValid():
            return ''
        return view._model.filePath(view._proxy.mapToSource(current))

    qtbot.waitUntil(lambda: current_path() == str(target), timeout=15_000)


def test_reveal_outside_project_is_noop(
    wnd: IdeMainWindow, project_root: Path, tmp_path: Path
) -> None:
    """Вне корня проекта reveal — no-op (ошибок нет, выделения нет)."""
    assert wnd.project_ctrl.open_project(project_root) is True
    view = wnd.project_ctrl.tree_view
    assert view is not None
    view.reveal_path(tmp_path / 'other.py')
    assert view._tree.currentIndex().isValid() is False


def test_project_root_persisted(
    org_app: tuple[str, str], project_root: Path, qapp: QApplication
) -> None:
    """Корень проекта переживает перезапуск (QSettings <key>/root_path)."""
    first = make_window(org_app)
    try:
        assert first.project_ctrl.open_project(project_root) is True
    finally:
        first.close()

    settings = QSettings(*org_app)
    assert settings.value(f'{PROJECT_CONTROL_KEY}/root_path') == str(project_root)

    second = make_window(org_app)
    try:
        assert second.project_ctrl.project_root == project_root
        view = second.project_ctrl.tree_view
        assert view is not None
        assert view.root_path == project_root
        assert second.project_ctrl.action_close_folder.isEnabled() is True
    finally:
        second.close()


def test_close_project_clears_tree(
    wnd: IdeMainWindow, project_root: Path, qtbot: QtBot
) -> None:
    """Закрытие проекта: корень снят, панель очищена, действие погашено."""
    assert wnd.project_ctrl.open_project(project_root) is True
    view = wnd.project_ctrl.tree_view
    assert view is not None
    qtbot.waitUntil(lambda: bool(visible_names(wnd)), timeout=10_000)

    assert wnd.project_ctrl.close_project() is True
    assert wnd.project_ctrl.project_root is None
    assert view.root_path is None
    assert visible_names(wnd) == set()
    assert wnd.project_ctrl.action_close_folder.isEnabled() is False
    # повторное закрытие — no-op
    assert wnd.project_ctrl.close_project() is False


def test_open_project_missing_dir_fails(wnd: IdeMainWindow, tmp_path: Path) -> None:
    """Несуществующая папка не становится проектом."""
    assert wnd.project_ctrl.open_project(tmp_path / 'nope') is False
    assert wnd.project_ctrl.project_root is None


def test_stale_saved_root_is_skipped(
    org_app: tuple[str, str], tmp_path: Path, qapp: QApplication
) -> None:
    """Сохранённый, но пропавший корень при старте молча пропускается."""
    org, app_name = org_app
    settings = QSettings(org, app_name)
    settings.setValue(f'{PROJECT_CONTROL_KEY}/root_path', str(tmp_path / 'gone'))
    settings.sync()

    window = make_window(org_app)
    try:
        assert window.project_ctrl.project_root is None
        assert window.project_ctrl.action_close_folder.isEnabled() is False
    finally:
        window.close()
