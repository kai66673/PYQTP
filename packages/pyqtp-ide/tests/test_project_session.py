"""Этап 9.6 — проектная сессия: ``.pyqtp/session.json`` + активная вкладка.

Юнит-тесты чистого модуля ``project_session`` (без GUI: сериализация,
толерантный разбор, атомарная запись/очистка каталога ``.pyqtp/``).
Offscreen-GUI: отслеживание активной вкладки documents-зоны через
``isCurrentTabChanged`` central-доков KDD, снимок сессии и закрытый цикл
«закрыли проект -> открыли заново -> вкладки восстановлены».

Настоящие документы (Monaco/QWebEngine) в GUI-тестах подменены лёгкой
вьюхой: сессия работает с путями-ключами и вкладками KDD, сам редактор
для неё не значим — так тесты остаются в дефолтном прогоне без webengine.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Iterator

import pytest

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QWidget

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.controls.project_control.project_session import (
    PROJECT_DIR_NAME,
    SESSION_FILE_NAME,
    SessionData,
    clear_session,
    deserialize_session,
    filter_session,
    parse_session,
    read_session,
    serialize_session,
    session_file,
    write_session,
)
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

###########################
# Юниты: формат и файлы #


def test_session_file_in_project_dir(tmp_path: Path) -> None:
    """Файл сессии живёт в каталоге проекта, а не в QSettings."""
    assert session_file(tmp_path) == tmp_path / PROJECT_DIR_NAME / SESSION_FILE_NAME


def test_serialize_empty_and_active_not_in_docs() -> None:
    data = SessionData(docs=['/a.py'], active='/вне_списка.py')
    raw = json.loads(serialize_session(data))
    assert raw['docs'] == ['/a.py']
    assert raw['active'] is None


def test_parse_lenient_skips_garbage() -> None:
    raw = {
        'docs': ['/a.py', '', 42, None, '/b.py', '/a.py'],
        'active': '/b.py',
        'мусор': [1, 2],
    }
    session = parse_session(raw)
    assert session.docs == ['/a.py', '/b.py']  # дедуп и пропуск не-строк
    assert session.active == '/b.py'


def test_parse_not_dict_gives_empty() -> None:
    assert parse_session([1, 2]).docs == []
    assert deserialize_session('{ это не json').docs == []


def test_serialize_roundtrip_utf8() -> None:
    data = SessionData(
        docs=['/проект/файл_🙂.py', '/b.py'], active='/b.py'
    )
    text = serialize_session(data)
    assert 'файл_🙂' in text  # ensure_ascii=False
    assert deserialize_session(text) == data


def test_read_missing_gives_empty(tmp_path: Path) -> None:
    assert read_session(tmp_path) == SessionData()


def test_write_atomic_creates_dir(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    write_session(root, SessionData(docs=['/a.py'], active='/a.py'))
    assert read_session(root) == SessionData(docs=['/a.py'], active='/a.py')
    leftovers = [p.name for p in (root / PROJECT_DIR_NAME).iterdir()]
    assert leftovers == [SESSION_FILE_NAME]  # tmp-файлов не остаётся


def test_write_empty_clears_project_dir(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    write_session(root, SessionData(docs=['/a.py']))
    write_session(root, SessionData())
    assert not (root / PROJECT_DIR_NAME).exists()


def test_clear_keeps_non_empty_project_dir(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    project_dir = root / PROJECT_DIR_NAME
    project_dir.mkdir(parents=True)
    (project_dir / 'settings.json').write_text('{}', encoding='utf-8')
    clear_session(root)  # файла сессии нет — не падает
    assert (project_dir / 'settings.json').exists()


def test_filter_session_drops_missing(tmp_path: Path) -> None:
    real = tmp_path / 'real.py'
    real.write_text('x = 1\n', encoding='utf-8')
    ghost = str(tmp_path / 'ghost.py')
    session, missing = filter_session(
        SessionData(docs=[str(real), ghost], active=str(real))
    )
    assert session.docs == [str(real)]
    assert missing == [ghost]
    assert session.active == str(real)
    # ghost активен — после фильтрации активной нет
    session2, missing2 = filter_session(
        SessionData(docs=[str(real), ghost], active=ghost)
    )
    assert session2.active is None
    assert missing2 == [ghost]


def test_read_session_oserror_wrapped(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    (root / PROJECT_DIR_NAME).mkdir(parents=True)
    (root / PROJECT_DIR_NAME / SESSION_FILE_NAME).mkdir()  # каталог вместо файла
    with pytest.raises(OSError):
        read_session(root)

######################
# Offscreen GUI      #


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


class FakeDocView(BaseDocumentView):
    """Документная вьюха без Monaco/WebEngine — вкладки и сессия те же.

    Реальный редактор держит Qt-фокус внутри себя (webview), поэтому у
    фейка есть фокусируемый ребёнок — на нём и проверяется обход фокуса
    (у самой контейнерной вьюхи ``NoFocus``, ``setFocus`` на неё Qt
    игнорирует).
    """

    def __init__(self, main_wnd, /, *, key: str) -> None:  # noqa: ANN001
        super().__init__(main_wnd, key=key)
        self.focus_child = QWidget(self)
        self.focus_child.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

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
    root = tmp_path / 'proj'
    root.mkdir()
    for name in ('a.py', 'b.py', 'c.py'):
        (root / name).write_text(f'VALUE_{name[0]} = 1\n', encoding='utf-8')
    return root


def wait_for(predicate: Callable[[], object], timeout: float = 5.0) -> bool:
    """Локальный опрос с processEvents (у pytest-qt 4.5 нет retry_interval)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def fake_view(wnd: IdeMainWindow, path: str) -> FakeDocView:
    """Вьюха открытого документа как FakeDocView (фокус-ребёнок для тестов)."""
    view = wnd.doc_ctrl.document_view(path)
    assert isinstance(view, FakeDocView)
    return view


def open_docs(wnd: IdeMainWindow, root: Path, *names: str) -> list[str]:
    paths = [str(root / name) for name in names]
    for path in paths:
        wnd.doc_ctrl.open_document(path)
    return paths


def test_active_tab_tracking_and_snapshot(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Гибридный учёт активной вкладки: ensure_visible + Qt-фокус + сосед при закрытии."""
    docs = open_docs(wnd, prj_root, 'a.py', 'b.py', 'c.py')
    assert wnd.doc_ctrl.open_documents() == docs
    assert wnd.doc_ctrl.session_snapshot().active == docs[2]
    assert wnd.doc_ctrl.active_document_get() is wnd.doc_ctrl.document_view(docs[2])

    # Qt-фокус внутри другой вкладки перекрывает «последний активированный»
    # (берём фокус по окну — setFocus работает и на неактивном окне, Qt
    # хранит его per-top-level)
    fake_view(wnd, docs[0]).focus_child.setFocus()
    QApplication.processEvents()
    assert wnd.doc_ctrl.session_snapshot().active == docs[0]
    assert wnd.doc_ctrl.active_document_get() is wnd.doc_ctrl.document_view(docs[0])

    # ensure_visible на другой вкладке перетягивает и вкладку, и фокус
    fake_view(wnd, docs[1]).ensure_visible()
    QApplication.processEvents()
    snapshot = wnd.doc_ctrl.session_snapshot()
    assert snapshot.docs == docs
    assert snapshot.active == docs[1]

    # закрытие активной вкладки — активной становится соседка по порядку
    assert wnd.doc_ctrl.close_document(docs[1])
    QApplication.processEvents()
    assert wnd.doc_ctrl.session_snapshot().active == docs[2]
    assert wnd.doc_ctrl.active_document_get() is wnd.doc_ctrl.document_view(docs[2])


def test_session_cycle_close_reopen(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Закрыли проект -> session.json; открыли заново -> вкладки восстановлены."""
    wnd.project_ctrl.open_project(prj_root)
    docs = open_docs(wnd, prj_root, 'a.py', 'b.py')

    wnd.project_ctrl.close_project()
    stored = read_session(prj_root)
    assert stored.docs == docs
    assert stored.active == docs[1]
    assert wnd.doc_ctrl.session_snapshot() == SessionData()  # проект закрыт

    wnd.project_ctrl.open_project(prj_root)
    assert wait_for(
        lambda: sorted(wnd.doc_ctrl.open_documents()) == sorted(docs)
    )
    # восстановленная активная — не просто «последняя открытая по порядку»
    assert wnd.doc_ctrl.session_snapshot().active == docs[1]
    assert (prj_root / PROJECT_DIR_NAME / SESSION_FILE_NAME).is_file()


def test_session_skips_missing_files(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Файлы из сессии, которых уже нет, пропускаются без падения."""
    b_path = str(prj_root / 'b.py')
    ghost = str(prj_root / 'ghost.py')
    write_session(
        prj_root, SessionData(docs=[ghost, b_path], active=b_path)
    )
    wnd.project_ctrl.open_project(prj_root)
    assert wait_for(lambda: wnd.doc_ctrl.open_documents() == [b_path])
    assert wnd.doc_ctrl.session_snapshot().active == b_path


def test_session_cleared_when_all_docs_closed(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Закрытие проекта без вкладок не оставляет каталог ``.pyqtp/``."""
    wnd.project_ctrl.open_project(prj_root)
    docs = open_docs(wnd, prj_root, 'a.py')
    wnd.project_ctrl.close_project()
    assert (prj_root / PROJECT_DIR_NAME / SESSION_FILE_NAME).is_file()

    wnd.project_ctrl.open_project(prj_root)
    assert wait_for(lambda: wnd.doc_ctrl.open_documents() == docs)
    assert wnd.doc_ctrl.close_document(docs[0])
    wnd.project_ctrl.close_project()
    assert not (prj_root / PROJECT_DIR_NAME).exists()


def test_session_saved_on_window_close(
    org_app: tuple[str, str], qapp: QApplication, fake_docs: None, prj_root: Path
) -> None:
    """Выход из приложения с открытым проектом сохраняет сессию, старт восстанавливает."""
    first = make_window(org_app)
    try:
        first.project_ctrl.open_project(prj_root)
        docs = open_docs(first, prj_root, 'a.py', 'b.py')
        assert first.project_ctrl.project_root == prj_root.resolve()
    finally:
        first.close()

    stored = read_session(prj_root)
    assert stored.docs == docs
    assert stored.active == docs[1]

    second = make_window(org_app)
    try:
        assert second.project_ctrl.project_root == prj_root.resolve()
        assert wait_for(
            lambda: sorted(second.doc_ctrl.open_documents()) == sorted(docs)
        )
        assert second.doc_ctrl.session_snapshot().active == docs[1]
    finally:
        second.close()
