"""Этап 10.1 — панель «Проблемы» в окне IDE (offscreen-GUI, без WebEngine).

Живой ``IdeMainWindow`` c KDD, настоящие ``LspHub`` (ws-поднят, процессы
серверов не поднимаем — диагностики заводим напрямую в стор ``_record_diag-
nostics``, тот же путь, что пишет поток event-loop) и ``ProblemsControl``.
Настоящие документы подменены лёгкой вьюхой с фейковым редактором — классы
переиспользуются из ``test_bookmarks.py`` (офскрин-харнес этапа 9.7).

Проверяем: панель зарегистрирована, снимок стора доходит до дерева через
queued-мост, относительные пути внутри проекта, переход двойным «кликом»
(``open_definition_uri`` -> фейковый редактор), очистка пустой публикацией
и остановкой хаба, console-API ``problems_show``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from PySide6.QtCore import QSettings

# переиспользуем офскрин-харнес закладок: фейковые документы/редакторы
from test_bookmarks import FakeDocView, fake_edit

from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.controls.problems_control import problems as prob
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtWidgets import QApplication
    from pytestqt.qtbot import QtBot

    from pyqtp.views.problems_view import ProblemsView


# =====================
# Offscreen harness   #
# =====================


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
def fake_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doc_mod, '_document_view_cls_from_identity', lambda _i: FakeDocView,
    )


@pytest.fixture
def wnd(
    org_app: tuple[str, str],
    qapp: QApplication,
    fake_docs: None,
) -> Iterator[IdeMainWindow]:
    """IdeMainWindow c KDD и ProblemsControl (зависимость от qapp — на фикстуре)."""
    org, app_name = org_app
    window = IdeMainWindow(
        org_name=org, app_name=app_name, dock_system_type=ViewsManagerType.kdd,
    )
    args = window.settings_parse_argv([])
    window.settings_read(**vars(args))
    window.setup()
    window.setup_ui()
    yield window
    window.close()


@pytest.fixture
def prj_root(tmp_path: Path) -> Path:
    """Проект с двумя файлами (пути нужны для относительных подписей дерева)."""
    root = tmp_path / 'proj'
    root.mkdir()
    (root / 'a.py').write_text('x = 1\n', encoding='utf-8')
    (root / 'b.py').write_text('y = 2\n', encoding='utf-8')
    return root


def panel_view(wnd: IdeMainWindow) -> ProblemsView:
    view = wnd.problems_ctrl.problems_view
    assert view is not None
    return view


def panel_tree(view: ProblemsView) -> list[tuple[str, list[str]]]:
    """Снимок панели «файл -> проблемы» (тексты элементов дерева)."""
    tree = view._tree
    result: list[tuple[str, list[str]]] = []
    for i in range(tree.topLevelItemCount()):
        header = tree.topLevelItem(i)
        rows = [header.child(j).text(0) for j in range(header.childCount())]
        result.append((header.text(0), rows))
    return result


def publish(
    wnd: IdeMainWindow,
    owner: str,
    path: Path,
    items: tuple[prob.Problem, ...],
) -> None:
    """Публикация в стор хаба (путь потокового вызова из event-loop LspHub)."""
    wnd.lsp_hub._record_diagnostics(  # python-API стора (тот же путь, что event-loop)
        owner, path.as_uri(), items,
    )


def error_problem(line: int = 2, col: int = 1, *, code: str = 'E1') -> prob.Problem:
    return prob.Problem(
        line=line, col=col, end_line=line, end_col=col + 1,
        severity=prob.SEVERITY_ERROR, message='так нельзя', code=code,
        owner='basedpyright',
    )


# =============
# Тесты       #
# =============


def test_view_registered_bottom(wnd: IdeMainWindow) -> None:
    """Контрол зарегистрирован и поставил панель в нижнюю область."""
    view = panel_view(wnd)
    assert view.key == 'ProblemsControl_problems_view'
    assert wnd.view_get('ProblemsControl_problems_view') is view


def test_panel_empty_without_project(wnd: IdeMainWindow) -> None:
    """Пустой стор — пустое дерево и лейбл «Нет проблем»."""
    view = panel_view(wnd)
    assert panel_tree(view) == []
    assert view._status_label.text() == 'Нет проблем'


def test_panel_shows_store_update(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Стор хаба (two owners, two files) -> дерево «файл -> проблемы» + счётчики."""
    publish(wnd, 'basedpyright', prj_root / 'a.py', (error_problem(2),))
    publish(
        wnd, 'ruff', prj_root / 'a.py',
        (prob.Problem(
            line=1, col=1, end_line=1, end_col=2, severity=prob.SEVERITY_WARNING,
            message='не используется', code='F401', source='ruff', owner='ruff',
        ),),
    )
    publish(
        wnd, 'ruff', Path('/outside/foreign.py'),
        (prob.Problem(
            line=9, col=3, end_line=9, end_col=4, severity=prob.SEVERITY_HINT,
            message='подсказка', owner='ruff',
        ),),
    )

    view = panel_view(wnd)

    def ready() -> bool:
        return len(panel_tree(view)) == 2

    qtbot.waitUntil(ready, timeout=5000)
    tree = panel_tree(view)
    # порядок заголовков — по пути ('/outside' раньше '/tmp'); внутри файла — сортировка
    assert tree[0][0] == '/outside/foreign.py'
    assert tree[0][1] == ['9:3 Подсказка: подсказка']
    assert tree[1][0] == str(prj_root / 'a.py')  # проект не открыт — abs-пути
    assert tree[1][1] == [
        # code в подписи приоритетнее source (в юнитах problem_label — оба варианта)
        '1:1 Предупреждение: не используется (F401)',
        '2:1 Ошибка: так нельзя (E1)',
    ]
    assert view._status_label.text() == (
        'Проблем: 3 (Ошибок: 1, Предупреждений: 1, Подсказок: 1)'
    )


def test_panel_relative_paths_with_project(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Открытый проект — заголовки файлов относительные (как в панели закладок)."""
    publish(wnd, 'basedpyright', prj_root / 'a.py', (error_problem(),))
    wnd.project_ctrl.open_project(prj_root)

    view = panel_view(wnd)

    def ready() -> bool:
        tree = panel_tree(view)
        return len(tree) == 1 and tree[0][0] == 'a.py'

    qtbot.waitUntil(ready, timeout=5000)


def test_jump_opens_document_and_moves_caret(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Активация проблемы -> open_definition_uri: вкладка + каретка (center)."""
    path_a = prj_root / 'a.py'
    publish(wnd, 'basedpyright', path_a, (error_problem(5, col=4),))

    view = panel_view(wnd)

    def ready() -> bool:
        return panel_tree(view) != []

    qtbot.waitUntil(ready, timeout=5000)
    child = view._tree.topLevelItem(0).child(0)
    view._on_item_activated(child, 0)

    assert wnd.doc_ctrl.has_document(str(path_a))
    assert fake_edit(wnd, str(path_a)).cursor_calls == [(5, 4, 'center')]


def test_empty_publish_clears_file(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Пустая публикация сервера — файл уходит из панели."""
    path_a = prj_root / 'a.py'
    publish(wnd, 'basedpyright', path_a, (error_problem(),))
    view = panel_view(wnd)
    qtbot.waitUntil(lambda: panel_tree(view) != [], timeout=5000)

    publish(wnd, 'basedpyright', path_a, ())
    qtbot.waitUntil(lambda: panel_tree(view) == [], timeout=5000)
    assert view._status_label.text() == 'Нет проблем'


def test_hub_shutdown_clears_panel(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Остановка хаба: стор пустеет и панель перерисовывается (без призраков)."""
    publish(wnd, 'basedpyright', prj_root / 'a.py', (error_problem(),))
    view = panel_view(wnd)
    qtbot.waitUntil(lambda: panel_tree(view) != [], timeout=5000)

    wnd.lsp_hub.shutdown()
    qtbot.waitUntil(lambda: panel_tree(view) == [], timeout=5000)


def test_console_api(wnd: IdeMainWindow, prj_root: Path) -> None:
    """``problems_show`` в Python-консоли: снимок стора списком словарей."""
    env: dict[str, Any] = wnd.api_ctrl.env
    assert 'problems_show' in env
    publish(wnd, 'ruff', prj_root / 'b.py', (error_problem(3, code='F821'),))
    snap = env['problems_show']()
    assert snap == [
        {
            'path': str(prj_root / 'b.py'),
            'line': 3, 'col': 1, 'end_line': 3, 'end_col': 2,
            'severity': 1, 'message': 'так нельзя', 'code': 'F821',
            'source': '', 'owner': 'ruff',
        },
    ]
