"""Тесты quick open (этап 9.2): fuzzy-scorer, скан файлов, диалог Ctrl+P.

Юниты scorer'а и сканера — без GUI (по приёмке этапа). Диалог — живой
offscreen-окно ``IdeMainWindow`` c KDD (паттерн фикстур как в
``test_project_tree.py``; хелперы держим в одном файле).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.project_control.quick_open import fuzzy_score, iter_project_files
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

###############
# Скоринг (юниты, без GUI)


def test_fuzzy_rejects_non_subsequence() -> None:
    assert fuzzy_score('az', 'deep.py') is None
    assert fuzzy_score('pd', 'deep.py') is None  # порядок символов важен
    assert fuzzy_score('toolong', 'ab') is None


def test_fuzzy_empty_query_is_zero() -> None:
    assert fuzzy_score('', 'что угодно') == 0


def test_fuzzy_case_insensitive_equal() -> None:
    assert fuzzy_score('DEEP', 'deep.py') == fuzzy_score('deep', 'deep.py')


def test_fuzzy_prefix_beats_middle() -> None:
    best = fuzzy_score('de', 'deep.py')
    worse = fuzzy_score('ep', 'deep.py')
    assert best is not None and worse is not None
    assert best > worse


def test_fuzzy_consecutive_beats_split() -> None:
    # «dp» подряд (dp.py) ценится выше, чем «dp» с разрывом (deep.py)
    glued = fuzzy_score('dp', 'dp.py')
    split = fuzzy_score('dp', 'deep.py')
    assert glued is not None and split is not None
    assert glued > split


def test_fuzzy_separator_boundary_bonus() -> None:
    # совпадение после границы «слов» ценится выше середины слова
    assert fuzzy_score('t', 'a/t') == 8 + 1
    assert fuzzy_score('t', 'at') == 1
    at_sep = fuzzy_score('ex', 'path/ext.py')
    mid_word = fuzzy_score('ex', 'next.py')
    assert at_sep is not None and mid_word is not None
    assert at_sep > mid_word


def test_fuzzy_start_bonus() -> None:
    assert fuzzy_score('a', 'alpha.py') == 8 + 1  # старт цели
    assert fuzzy_score('a', 'zalpha.py') == 1  # та же буква в середине слова


###############
# Скан проекта (юниты, без GUI)


def test_scan_prunes_hidden_and_service(project_root: Path) -> None:
    files = iter_project_files(project_root)
    assert files == sorted(files)
    assert 'a.py' in files
    assert 'pkg/sub/deep.py' in files
    assert not any('__pycache__' in f for f in files)
    assert not any('node_modules' in f for f in files)
    assert not any('secret.txt' in f for f in files)
    assert not any('.venv' in f for f in files)
    # пустые каталоги в кэш файлов не попадают вообще


def test_scan_relative_paths_use_forward_slash(project_root: Path) -> None:
    assert all('\\' not in f for f in iter_project_files(project_root))


################
# Живые тесты диалога (offscreen GUI, без LSP/WebEngine)


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
    (root / 'app' / '__pycache__').mkdir(parents=True)
    (root / 'a.py').write_text('x = 1\n', encoding='utf-8')
    (root / 'b.txt').write_text('', encoding='utf-8')
    (root / 'pkg' / 'sub' / 'deep.py').write_text('z = 3\n', encoding='utf-8')
    (root / 'app' / 'alpha.py').write_text('', encoding='utf-8')
    (root / 'app' / '__pycache__' / 'a.pyc').write_text('', encoding='utf-8')
    (root / 'node_modules').mkdir()
    (root / '.venv').mkdir()
    (root / '.secret.txt').write_text('secret', encoding='utf-8')
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


def test_quick_open_action_tracks_project(
    wnd: IdeMainWindow, project_root: Path
) -> None:
    """Ctrl+P погашен без проекта, активен с проектом, снова погас без него."""
    ctrl = wnd.project_ctrl
    assert ctrl.action_quick_open.isEnabled() is False

    assert ctrl.open_project(project_root) is True
    assert ctrl.action_quick_open.isEnabled() is True

    assert ctrl.close_project() is True
    assert ctrl.action_quick_open.isEnabled() is False


def test_quick_open_search_and_open(
    wnd: IdeMainWindow,
    project_root: Path,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кэш скана -> диалог: fuzzy-строка, Enter открывает файл в документах."""
    opened: list[str] = []
    monkeypatch.setattr(
        wnd.doc_ctrl, 'open_document', lambda path: opened.append(str(path))
    )
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(project_root) is True

    # фоновый скан (python-поток + queued-сигнал) должен наполнить кэш
    qtbot.waitUntil(lambda: bool(ctrl._quick_files), timeout=10_000)
    assert 'pkg/sub/deep.py' in ctrl._quick_files
    assert not any('__pycache__' in f for f in ctrl._quick_files)

    ctrl._show_quick_open()
    dlg = ctrl._quick_dialog
    assert dlg is not None
    qtbot.waitUntil(lambda: dlg.isVisible(), timeout=5_000)

    dlg._edit.setText('deep')
    qtbot.waitUntil(lambda: dlg._list.count() > 0, timeout=5_000)
    assert dlg._list.item(0).text() == 'pkg/sub/deep.py'

    dlg.accept_current()
    assert opened == [str(project_root / 'pkg' / 'sub' / 'deep.py')]
    assert not dlg.isVisible()  # выбор закрывает диалог


def test_quick_open_filters_by_query(
    wnd: IdeMainWindow, project_root: Path, qtbot: QtBot
) -> None:
    """Набор строк диалога — ровно fuzzy-совпадения, у «deep» первый deep.py."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(project_root) is True
    qtbot.waitUntil(lambda: bool(ctrl._quick_files), timeout=10_000)

    ctrl._show_quick_open()
    dlg = ctrl._quick_dialog
    assert dlg is not None
    dlg._edit.setText('a')
    qtbot.waitUntil(lambda: dlg._list.count() > 0, timeout=5_000)
    texts = {dlg._list.item(row).text() for row in range(dlg._list.count())}
    assert texts == {'a.py', 'app/alpha.py'}

    dlg._edit.setText('t')
    qtbot.waitUntil(lambda: dlg._list.count() == 1, timeout=5_000)
    assert dlg._list.item(0).text() == 'b.txt'


def test_quick_open_close_project_clears_cache(
    wnd: IdeMainWindow, project_root: Path, qtbot: QtBot
) -> None:
    """Закрытие проекта обнуляет кэш и гасит диалог-поиск."""
    ctrl = wnd.project_ctrl
    assert ctrl.open_project(project_root) is True
    qtbot.waitUntil(lambda: bool(ctrl._quick_files), timeout=10_000)
    ctrl._show_quick_open()
    dlg = ctrl._quick_dialog
    assert dlg is not None

    assert ctrl.close_project() is True
    assert ctrl._quick_files == []
    assert str(dlg._status_label.text()) == 'Проект не открыт'

    # запоздалый результат скана старого проекта отброшен по поколению
    ctrl._on_scan_ready(ctrl._scan_gen + 1, ['ghost.py'])
    assert ctrl._quick_files == []
