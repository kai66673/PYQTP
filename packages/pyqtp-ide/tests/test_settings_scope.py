"""Этап 9.6B2 — селектор области «Приложение/Проект» в окне настроек.

Offscreen-GUI (без Monaco/WebEngine, в дефолтном прогоне):

  - реестр областей ``SettingsControl.scope_register`` и доступность
    «Проекта» вместе с открытым проектом;
  - строка «Область» показывается только для scope-applicable страниц;
  - страницы «Редактор»/«Внешний вид»/«LSP» в проектной области грузят
    эффективные значения, пишут перекрытие проекта и сбрасывают секцию;
  - закрытие проекта при открытом окне откатывает область страницы на
    «Приложение» с перезагрузкой значений;
  - пакетный API ``ProjectControl.project_settings_update`` (валидация
    всех ключей до записи, одна запись файла, один сигнал).

Настоящие документы подменены лёгкой вьюхой с рекордером сообщений
редактору (как в test_project_settings.py; хелперы живут в одном файле).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QPlainTextEdit,
    QSpinBox,
    QWidget,
)

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.controls.lsp_control.lsp_registry import LspRegistry
from pyqtp.controls.project_control.project_control import SCOPE_PROJECT
from pyqtp.controls.project_control.project_settings import (
    KEY_REGISTRY_JSON,
    KEY_THEME_NAME,
    SECTION_APPEARANCE,
    SECTION_EDITOR,
    SECTION_LSP,
    read_project_settings,
    settings_file,
)
from pyqtp.controls.settings_control.settings_page import SCOPE_APP, SettingsPage
from pyqtp.controls.settings_control.settings_window import SettingsWindow
from pyqtp.main_wnd.appearance_scope_page import AppearanceScopePage
from pyqtp.main_wnd.ide_main_window import IdeMainWindow

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


class FakeEditWidget:
    """Запись сообщений редактору (``update_editor_options``/``set_theme``)."""

    def __init__(self) -> None:
        self.options_calls: list[dict[str, Any]] = []
        self.theme_calls: list[str] = []

    def update_editor_options(self, options: dict[str, Any]) -> None:
        self.options_calls.append(dict(options))

    def set_theme(self, theme: str) -> None:
        self.theme_calls.append(theme)


class FakeDocView(BaseDocumentView):
    """Документная вьюха без Monaco/WebEngine — с фейковым редактором настроек."""

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
    root = tmp_path / 'proj'
    root.mkdir()
    for name in ('a.py', 'b.py', 'c.py'):
        (root / name).write_text(f'VALUE_{name[0]} = 1\n', encoding='utf-8')
    return root


def fake_edit(wnd: IdeMainWindow, path: str) -> FakeEditWidget:
    """Редактор-рекордер открытого документа (проверка эффективных payload)."""
    view = wnd.doc_ctrl.document_view(path)
    assert isinstance(view, FakeDocView)
    return view.edit_widget


def open_docs(wnd: IdeMainWindow, root: Path, *names: str) -> list[str]:
    paths = [str(root / name) for name in names]
    for path in paths:
        wnd.doc_ctrl.open_document(path)
    return paths


def python_server_names(registry: LspRegistry) -> list[str]:
    """Имена включённых python-серверов реестра."""
    cfg = registry.languages.get('python')
    if cfg is None:
        return []
    return [spec.name for spec in cfg.servers if spec.enabled]


SOLO_REGISTRY_JSON = json.dumps(
    {
        'python': {
            'extensions': ['.py'],
            'servers': [{'name': 'solo-server', 'command': ['solo-langserver']}],
        }
    },
    ensure_ascii=False,
)

ALSO_REGISTRY_JSON = json.dumps(
    {
        'python': {
            'extensions': ['.py'],
            'servers': [{'name': 'also-server', 'command': ['also-langserver']}],
        }
    },
    ensure_ascii=False,
)

##############################
# Хелперы окна настроек     #


def open_settings_window(wnd: IdeMainWindow) -> SettingsWindow:
    """Открывает окно настроек и возвращает его (оно ленивое у контрола)."""
    wnd.settings_ctrl.settings_window_show()
    window = wnd.settings_ctrl._window
    assert window is not None
    return window


def select_page(window: SettingsWindow, key: str) -> None:
    row = window._row_for_key(key)
    assert row >= 0, f'страница {key!r} не найдена в окне'
    window._pages_list.setCurrentRow(row)


def select_scope(window: SettingsWindow, scope: str) -> None:
    """Переключение области через селектор (как это делает пользователь)."""
    combo = window._scope_combo
    index = combo.findData(scope)
    assert index >= 0, f'область {scope!r} нет в селекторе'
    combo.setCurrentIndex(index)


def scope_items(window: SettingsWindow) -> list[tuple[str, str]]:
    """Содержимое селектора области: ``(key, label)`` по порядку."""
    combo = window._scope_combo
    return [
        (str(combo.itemData(i)), combo.itemText(i)) for i in range(combo.count())
    ]


class PlainSettingsPage(SettingsPage):
    """Обычная страница без областей — строка «Область» для неё прячется."""

    title = 'Простая страница'
    order = 999

    def create_page_widget(self) -> QWidget:
        return QWidget()


####################
# Реестр областей #


def test_scope_registry_and_availability(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Ядро объявляет «Приложение», IDE — «Проект»; доступность ленивая."""
    options = wnd.settings_ctrl.scope_options()
    assert [(key, ok) for key, _label, ok in options] == [
        (SCOPE_APP, True),
        (SCOPE_PROJECT, False),
    ]
    assert [label for _key, label, _ok in options] == ['Приложение', 'Проект']

    wnd.project_ctrl.open_project(prj_root)
    assert [
        (key, ok) for key, _label, ok in wnd.settings_ctrl.scope_options()
    ] == [(SCOPE_APP, True), (SCOPE_PROJECT, True)]

    wnd.project_ctrl.close_project()
    assert [
        (key, ok) for key, _label, ok in wnd.settings_ctrl.scope_options()
    ] == [(SCOPE_APP, True), (SCOPE_PROJECT, False)]

    # повторная регистрация области — отказ (как у страниц)
    with pytest.raises(ValueError):
        wnd.settings_scope_register(SCOPE_APP, 'Дубликат')


def test_scope_row_only_for_scope_pages(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Строка «Область» — только у scope-страниц; наполнение по доступности."""
    window = open_settings_window(wnd)

    select_page(window, 'editor')
    assert window._scope_row.isHidden() is False
    # проект закрыт — доступна только область приложения
    assert scope_items(window) == [(SCOPE_APP, 'Приложение')]

    plain = wnd.settings_page_register(PlainSettingsPage())
    try:
        select_page(window, plain.key)
        assert window._scope_row.isHidden() is True
    finally:
        wnd.settings_page_unregister(plain.key)

    # вернуться на scope-страницу — строка снова видна
    select_page(window, 'editor')
    assert window._scope_row.isHidden() is False

    # открыли проект при открытом окне — селектор пересобран сам (сигнал корня)
    wnd.project_ctrl.open_project(prj_root)
    assert scope_items(window) == [
        (SCOPE_APP, 'Приложение'),
        (SCOPE_PROJECT, 'Проект'),
    ]
    assert window._scope_combo.currentData() == SCOPE_APP


##################
# Страница       #
# «Редактор»     #


def test_editor_page_project_scope_load_apply_reset(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Проектная область: грузим эффективное, пишем перекрытие, сброс — секция."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    docs = open_docs(wnd, prj_root, 'a.py')
    prj.project_settings_set(SECTION_EDITOR, 'font_size', 20)

    window = open_settings_window(wnd)
    select_page(window, 'editor')
    page = wnd.settings_ctrl.page_get('editor')
    assert page is not None
    assert page.scope == SCOPE_APP

    spin = page.widget.findChild(QSpinBox)
    assert isinstance(spin, QSpinBox)
    assert spin.value() == 14  # область «Приложение» — слой приложения

    select_scope(window, SCOPE_PROJECT)
    assert page.scope == SCOPE_PROJECT
    assert spin.value() == 20  # эффективное значение с перекрытием

    spin.setValue(11)
    page.apply()
    # запись — пакетная: весь набор ключей в слой проекта, слой приложения цел
    disk = read_project_settings(prj_root)[SECTION_EDITOR]
    assert disk == {
        'minimap': True,
        'folding': True,
        'mouse_wheel_zoom': False,
        'font_size': 11,
        'theme': 'auto',
    }
    assert wnd.doc_ctrl.editor_ux_options_app()['font_size'] == 14
    assert wnd.doc_ctrl.editor_ux_options()['font_size'] == 11
    # эффективные значения доведены до открытых редакторов
    assert fake_edit(wnd, docs[0]).options_calls[-1]['fontSize'] == 11

    page.reset_defaults()  # снимает секцию editor из перекрытий
    assert not settings_file(prj_root).exists()
    assert page.scope == SCOPE_PROJECT  # область не сбрасывается самим сбросом
    assert spin.value() == 14  # перекрытия нет — видно слой приложения
    assert fake_edit(wnd, docs[0]).options_calls[-1]['fontSize'] == 14


def test_scope_fallback_when_project_closes(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Закрыли проект при открытом окне — область откатана на «Приложение»."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    prj.project_settings_set(SECTION_EDITOR, 'font_size', 20)

    window = open_settings_window(wnd)
    select_page(window, 'editor')
    select_scope(window, SCOPE_PROJECT)
    page = wnd.settings_ctrl.page_get('editor')
    assert page is not None
    spin = page.widget.findChild(QSpinBox)
    assert isinstance(spin, QSpinBox)
    assert spin.value() == 20

    prj.close_project()
    assert page.scope == SCOPE_APP  # «Проект» недоступен -> откат
    assert scope_items(window) == [(SCOPE_APP, 'Приложение')]
    assert window._scope_combo.currentData() == SCOPE_APP
    assert spin.value() == 14  # значения перезагружены из слоя приложения

    # снова открыли — область сама не переключается, но селектор живой
    prj.open_project(prj_root)
    assert page.scope == SCOPE_APP
    assert scope_items(window) == [
        (SCOPE_APP, 'Приложение'),
        (SCOPE_PROJECT, 'Проект'),
    ]


####################
# «Внешний вид»    #


def test_appearance_page_replaced_by_scope_page(wnd: IdeMainWindow) -> None:
    """В IDE core-страницу темы заменила scope-aware версия."""
    page = wnd.settings_ctrl.page_get('appearance')
    assert isinstance(page, AppearanceScopePage)
    assert page.scope_applicable is True
    assert page.order == 0


def test_appearance_page_project_scope(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Тема в проектной области: применить — перекрытие, сброс — снять его."""
    wnd.project_ctrl.open_project(prj_root)

    window = open_settings_window(wnd)
    select_page(window, 'appearance')
    select_scope(window, SCOPE_PROJECT)
    page = wnd.settings_ctrl.page_get('appearance')
    assert isinstance(page, AppearanceScopePage)

    combo = page.widget.findChild(QComboBox)
    assert isinstance(combo, QComboBox)
    # перекрытия нет — показываем эффективную тему приложения
    assert combo.currentText() == 'Светлая'

    combo.setCurrentText('Темная')
    page.apply()
    assert (
        read_project_settings(prj_root)[SECTION_APPEARANCE][KEY_THEME_NAME]
        == 'Темная'
    )
    assert wnd.theme_app_name == 'Светлая'  # слой приложения не тронут
    assert wnd.theme_manager.current_theme.name == 'Темная'  # перекрытие живо

    page.reset_defaults()
    assert not settings_file(prj_root).exists()
    assert wnd.theme_manager.current_theme.name == 'Светлая'
    assert combo.currentText() == 'Светлая'


###############
# «LSP»       #


def test_lsp_page_project_scope(wnd: IdeMainWindow, prj_root: Path) -> None:
    """LSP-страница: project scope грузит эффективный реестр и пишет overlay."""
    hub = wnd.lsp_hub
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    prj.project_settings_set(SECTION_LSP, KEY_REGISTRY_JSON, SOLO_REGISTRY_JSON)

    window = open_settings_window(wnd)
    select_page(window, 'lsp')
    page = wnd.settings_ctrl.page_get('lsp')
    assert page is not None

    editor = page.widget.findChild(QPlainTextEdit)
    assert isinstance(editor, QPlainTextEdit)
    # область приложения — реестр приложения, а не эффективный (с overlay)
    assert python_server_names(
        LspRegistry.from_json(editor.toPlainText())
    ) == python_server_names(LspRegistry.default())

    select_scope(window, SCOPE_PROJECT)
    assert page.scope == SCOPE_PROJECT
    assert python_server_names(
        LspRegistry.from_json(editor.toPlainText())
    ) == ['solo-server']  # эффективный реестр

    editor.setPlainText(ALSO_REGISTRY_JSON)
    page.apply()
    disk = read_project_settings(prj_root)[SECTION_LSP][KEY_REGISTRY_JSON]
    assert python_server_names(LspRegistry.from_json(disk)) == ['also-server']
    assert python_server_names(
        LspRegistry.from_json(hub.registry_app_json())
    ) == python_server_names(LspRegistry.default())  # слой приложения цел

    page.reset_defaults()  # снимает перекрытие — реестр приложения живёт
    assert not settings_file(prj_root).exists()
    assert python_server_names(
        LspRegistry.from_json(editor.toPlainText())
    ) == python_server_names(LspRegistry.default())
    assert python_server_names(
        LspRegistry.from_json(hub.registry_json())
    ) == python_server_names(LspRegistry.default())


#################################
# Пакетный API настроек проекта #


def test_project_settings_update_requires_open_project(
    wnd: IdeMainWindow,
) -> None:
    """Без открытого проекта пакетная запись недоступна."""
    with pytest.raises(ValueError):
        wnd.project_ctrl.project_settings_update(
            SECTION_EDITOR, {'font_size': 20}
        )


def test_project_settings_update_batch(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Валидация всех ключей до записи: либо все, либо ничего; один сигнал."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    fired: list[int] = []
    prj.project_settings_changed.connect(lambda: fired.append(1))

    # один битый ключ — не записан ни он, ни валидные соседи
    with pytest.raises(ValueError):
        prj.project_settings_update(SECTION_EDITOR, {
            'font_size': 20,
            'poprosim': 1,
        })
    assert fired == []
    assert not settings_file(prj_root).exists()
    assert prj.project_settings_get() == {}

    # валидный пакет — одна запись файла, один сигнал
    prj.project_settings_update(SECTION_EDITOR, {
        'font_size': 20,
        'minimap': False,
    })
    assert fired == [1]
    assert read_project_settings(prj_root) == {
        SECTION_EDITOR: {'font_size': 20, 'minimap': False}
    }

    # None снимает ключ, остальные в секции живы
    prj.project_settings_update(SECTION_EDITOR, {'font_size': None})
    assert len(fired) == 2
    assert read_project_settings(prj_root) == {
        SECTION_EDITOR: {'minimap': False}
    }
