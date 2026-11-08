"""Этап 9.6B1 — настройки проекта: ``<root>/.pyqtp/settings.json``.

Юнит-тесты чистого модуля ``project_settings`` (без GUI: толерантный разбор
оверлея ``{секция: {ключ: значение}}``, атомарная запись, очистка каталога
``.pyqtp/``) и хелперов ``editor_options`` (coerce/merge).

Offscreen-GUI: двухслойность настроек — перекрытие проекта старше настроек
приложения, при этом правки приложения не «пробивают» перекрытие, а запись
настроек приложения не «утаскивает» проектные значения в QSettings.
Настоящие документы (Monaco/QWebEngine) подменены лёгкой вьюхой с
рекордером сообщений редактору — так тесты остаются в дефолтном прогоне.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator, cast

import pytest

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QSpinBox, QWidget

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.base.docking_system.base_views_manager import ViewsManagerType
from pyqtp.controls.doc_control import document_manager as doc_mod
from pyqtp.controls.lsp_control.lsp_registry import LspRegistry
from pyqtp.controls.project_control.project_session import PROJECT_DIR_NAME
from pyqtp.controls.project_control.project_settings import (
    KEY_REGISTRY_JSON,
    KEY_THEME_NAME,
    PROJECT_SETTINGS_FILE_NAME,
    SECTION_APPEARANCE,
    SECTION_EDITOR,
    SECTION_LSP,
    clear_project_settings,
    deserialize_overlay,
    overlay_set,
    parse_overlay,
    read_project_settings,
    serialize_overlay,
    settings_file,
    write_project_settings,
)
from pyqtp.main_wnd.ide_main_window import IdeMainWindow
from pyqtp.web.editor_options import (
    coerce_editor_ux_value,
    merge_editor_ux_options,
)

###########################
# Юниты: формат и файлы #


def test_settings_file_in_project_dir(tmp_path: Path) -> None:
    """Файл настроек живёт в каталоге проекта рядом с сессией."""
    assert (
        settings_file(tmp_path)
        == tmp_path / PROJECT_DIR_NAME / PROJECT_SETTINGS_FILE_NAME
    )


def test_parse_overlay_lenient() -> None:
    """Не-словари выбрасываются, пустые секции вычищаются, неизвестные — живут."""
    overlay = parse_overlay({
        'appearance': {'theme_name': 'Темная'},
        'editor': 'это не словарь',
        'lsp': {},
        'будущая_секция': {'что-то': 1},
        42: {'нет строкового ключа'},
    })
    assert overlay == {
        'appearance': {'theme_name': 'Темная'},
        'будущая_секция': {'что-то': 1},
    }


def test_deserialize_overlay_broken_gives_empty() -> None:
    """Битый JSON и не-объект верхнего уровня — пустой оверлей."""
    assert deserialize_overlay('{ это не json') == {}
    assert deserialize_overlay('[1, 2]') == {}
    assert deserialize_overlay('{}') == {}


def test_serialize_overlay_roundtrip_utf8_and_pruning() -> None:
    overlay = {
        'appearance': {KEY_THEME_NAME: 'Темная'},
        'editor': {'font_size': 20, 'minimap': False},
        'lsp': {},
    }
    text = serialize_overlay(overlay)
    assert 'Темная' in text  # ensure_ascii=False
    assert '"lsp"' not in text  # пустые секции не пишутся
    assert deserialize_overlay(text) == {
        'appearance': {KEY_THEME_NAME: 'Темная'},
        'editor': {'font_size': 20, 'minimap': False},
    }


def test_read_missing_or_broken_gives_empty(tmp_path: Path) -> None:
    assert read_project_settings(tmp_path) == {}
    settings_file(tmp_path).parent.mkdir(parents=True)
    settings_file(tmp_path).write_text('{ битый', encoding='utf-8')
    assert read_project_settings(tmp_path) == {}


def test_write_atomic_creates_dir_no_leftovers(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    write_project_settings(root, {SECTION_EDITOR: {'font_size': 20}})
    assert read_project_settings(root) == {SECTION_EDITOR: {'font_size': 20}}
    leftovers = [p.name for p in (root / PROJECT_DIR_NAME).iterdir()]
    assert leftovers == [PROJECT_SETTINGS_FILE_NAME]  # tmp-файлов не остаётся


def test_write_empty_removes_file_but_keeps_other_files(tmp_path: Path) -> None:
    """Пустой оверлей не оставляет мусора, но чужие файлы ``.pyqtp/`` живы."""
    root = tmp_path / 'proj'
    project_dir = root / PROJECT_DIR_NAME
    project_dir.mkdir(parents=True)
    session_file = project_dir / 'session.json'
    session_file.write_text('{}', encoding='utf-8')
    write_project_settings(root, {SECTION_EDITOR: {'font_size': 20}})
    write_project_settings(root, {})
    assert not settings_file(root).exists()
    assert session_file.exists()  # каталог с чужим файлом не сносим


def test_clear_project_settings_keeps_non_empty_dir(tmp_path: Path) -> None:
    root = tmp_path / 'proj'
    clear_project_settings(root)  # файла нет — не падает
    project_dir = root / PROJECT_DIR_NAME
    project_dir.mkdir(parents=True)
    (project_dir / 'session.json').write_text('{}', encoding='utf-8')
    write_project_settings(root, {SECTION_APPEARANCE: {KEY_THEME_NAME: 'Темная'}})
    clear_project_settings(root)
    assert not settings_file(root).exists()
    assert (project_dir / 'session.json').exists()


def test_overlay_set_add_remove_copy() -> None:
    original: dict[str, dict[str, Any]] = {SECTION_EDITOR: {'font_size': 20}}
    added = overlay_set(original, SECTION_APPEARANCE, KEY_THEME_NAME, 'Темная')
    assert added[SECTION_APPEARANCE] == {KEY_THEME_NAME: 'Темная'}
    assert added[SECTION_EDITOR] == {'font_size': 20}
    assert original == {SECTION_EDITOR: {'font_size': 20}}  # исходник не тронут
    removed = overlay_set(added, SECTION_EDITOR, 'font_size', None)
    assert SECTION_EDITOR not in removed  # пустая секция выбрасывается
    assert removed[SECTION_APPEARANCE] == {KEY_THEME_NAME: 'Темная'}


def test_coerce_editor_ux_value() -> None:
    assert coerce_editor_ux_value('minimap', True) is True
    assert coerce_editor_ux_value('minimap', 'no') is False
    with pytest.raises(TypeError):
        coerce_editor_ux_value('minimap', 42)
    assert coerce_editor_ux_value('font_size', '16') == 16
    with pytest.raises(TypeError):  # не число — TypeError
        coerce_editor_ux_value('font_size', 'abc')
    with pytest.raises(ValueError):  # вне диапазона — ValueError
        coerce_editor_ux_value('font_size', 999)
    assert coerce_editor_ux_value('theme', 'dark') == 'dark'
    with pytest.raises(ValueError):
        coerce_editor_ux_value('theme', 'несуществующая')


def test_merge_editor_ux_options_lenient() -> None:
    """Известный ключ перекрывается, мусор/неизвестное — молча игнорируются."""
    options: dict[str, Any] = {
        'minimap': True,
        'folding': True,
        'mouse_wheel_zoom': False,
        'font_size': 14,
        'theme': 'auto',
    }
    merge_editor_ux_options(options, {
        'minimap': False,
        'font_size': 'abc',  # битое значение — остаётся значение слоя приложения
        'неизвестная_опция': 1,
    })
    assert options['minimap'] is False
    assert options['font_size'] == 14
    assert 'неизвестная_опция' not in options

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


class FakeEditWidget:
    """Запись сообщений редактору (``update_editor_options``/``set_theme``).

    ``DocumentManager._apply_editor_ux_options`` берёт редактор вьюхи через
    ``getattr(view, 'edit_widget', None)`` — фейк с двумя рекордерами
    позволяет проверять ЭФФЕКТИВные payload без Monaco/WebEngine.
    """

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


def wait_for(predicate: Callable[[], object], timeout: float = 5.0) -> bool:
    """Локальный опрос с processEvents (у pytest-qt 4.5 нет retry_interval)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def fake_edit(wnd: IdeMainWindow, path: str) -> FakeEditWidget:
    """Редактор-рекордер открытого документа (проверка Эффективных payload)."""
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


def test_startup_overlay_from_persisted_root(
    org_app: tuple[str, str], qapp: QApplication, fake_docs: None, prj_root: Path
) -> None:
    """Старт с персистнутым корнем: оверлей применён до restore сессии."""
    write_project_settings(prj_root, {
        SECTION_APPEARANCE: {KEY_THEME_NAME: 'Темная'},
        SECTION_EDITOR: {'font_size': 20},
        SECTION_LSP: {KEY_REGISTRY_JSON: SOLO_REGISTRY_JSON},
    })
    org, app_name = org_app
    seed = QSettings(org, app_name)
    seed.setValue('ProjectControl/root_path', str(prj_root))
    seed.sync()

    window = make_window(org_app)
    try:
        assert window.project_ctrl.project_root == prj_root.resolve()
        # все три секции оверлея стали эффективными на старте
        assert window.theme_manager.current_theme.name == 'Темная'
        assert window.theme_app_name == 'Светлая'
        assert window.doc_ctrl.editor_ux_options()['font_size'] == 20
        assert window.doc_ctrl.editor_ux_options_app()['font_size'] == 14
        assert python_server_names(
            LspRegistry.from_json(window.lsp_hub.registry_json())
        ) == ['solo-server']
        # открытые после старта документы получают эффективные payload
        docs = open_docs(window, prj_root, 'a.py')
        edit = fake_edit(window, docs[0])
        assert edit.options_calls[-1]['fontSize'] == 20
    finally:
        window.close()


def test_theme_override_open_close_cycle(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Открыли проект с перекрытием темы -> она эффективна; закрыли -> откат."""
    write_project_settings(prj_root, {SECTION_APPEARANCE: {KEY_THEME_NAME: 'Темная'}})
    assert wnd.theme_manager.current_theme.name == 'Светлая'

    wnd.project_ctrl.open_project(prj_root)
    assert wnd.theme_manager.current_theme.name == 'Темная'
    assert wnd.theme_app_name == 'Светлая'  # слой приложения не тронут
    assert wnd.theme_switch_toolbar.theme_combo_box.currentText() == 'Темная'

    wnd.project_ctrl.close_project()
    assert wnd.theme_manager.current_theme.name == 'Светлая'  # откат к приложению
    # файл настроек на диске живёт с проектом и при закрытии не трогается
    assert read_project_settings(prj_root) == {
        SECTION_APPEARANCE: {KEY_THEME_NAME: 'Темная'}
    }

    wnd.project_ctrl.open_project(prj_root)
    assert wnd.theme_manager.current_theme.name == 'Темная'


def test_app_theme_edit_does_not_punch_through(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Правки слоя приложения не пробивают перекрытие и не утекают в QSettings."""
    write_project_settings(prj_root, {SECTION_APPEARANCE: {KEY_THEME_NAME: 'Светлая'}})
    wnd.theme_set_app('Темная')
    assert wnd.theme_manager.current_theme.name == 'Темная'

    wnd.project_ctrl.open_project(prj_root)
    assert wnd.theme_manager.current_theme.name == 'Светлая'  # перекрытие старше

    wnd.theme_set_app('Темная (Windows)')  # правка слоя приложения
    assert wnd.theme_app_name == 'Темная (Windows)'
    assert wnd.theme_manager.current_theme.name == 'Светлая'  # не пробило

    wnd.settings_write()
    raw = cast('str', wnd.settings.value('theme_name', '', str))
    assert raw == 'Темная (Windows)'  # в QSettings ушёл слой приложения
    assert read_project_settings(prj_root) == {
        SECTION_APPEARANCE: {KEY_THEME_NAME: 'Светлая'}
    }  # файл проекта не тронут правками приложения

    wnd.project_ctrl.close_project()
    assert wnd.theme_manager.current_theme.name == 'Темная (Windows)'


def test_theme_combo_routes_by_current_source(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Комбик темы правит тот слой, который держит эффективное значение."""
    wnd.project_ctrl.open_project(prj_root)
    wnd.project_ctrl.project_settings_set(
        SECTION_APPEARANCE, KEY_THEME_NAME, 'Темная'
    )
    assert wnd.theme_manager.current_theme.name == 'Темная'

    # перекрытие активно -> комбик пишет в слой проекта, приложение не тронто
    wnd.handle_theme_switch('Темная (Windows)')
    assert wnd.theme_manager.current_theme.name == 'Темная (Windows)'
    assert wnd.theme_app_name == 'Светлая'
    disk = read_project_settings(prj_root)
    assert disk[SECTION_APPEARANCE][KEY_THEME_NAME] == 'Темная (Windows)'

    # сняли перекрытие -> эффективной стала тема приложения...
    wnd.project_ctrl.project_settings_reset(SECTION_APPEARANCE)
    assert wnd.project_ctrl.theme_provided_by_project() is False
    assert wnd.theme_manager.current_theme.name == 'Светлая'
    # ...теперь комбик правит слой приложения, файла проекта больше нет
    wnd.handle_theme_switch('Темная')
    assert wnd.theme_app_name == 'Темная'
    assert wnd.theme_manager.current_theme.name == 'Темная'
    assert not settings_file(prj_root).exists()


def test_editor_options_layers_and_runtime_edit(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Поключевой merge оверлея, битые значения игнорируются, runtime — broadcast."""
    write_project_settings(prj_root, {
        SECTION_EDITOR: {'minimap': False, 'font_size': 'abc'}  # битое значение
    })
    wnd.project_ctrl.open_project(prj_root)

    docs = open_docs(wnd, prj_root, 'a.py')
    edit = fake_edit(wnd, docs[0])
    last = edit.options_calls[-1]
    assert last['minimap'] == {'enabled': False}  # валидное перекрытие сработало
    assert last['fontSize'] == 14  # битое — значение слоя приложения
    assert edit.theme_calls[-1] == 'vs'  # тема приложения светлая

    prj = wnd.project_ctrl
    prj.project_settings_set(SECTION_EDITOR, 'font_size', 20)  # runtime-правка
    assert fake_edit(wnd, docs[0]).options_calls[-1]['fontSize'] == 20
    disk = read_project_settings(prj_root)[SECTION_EDITOR]
    assert disk == {'minimap': False, 'font_size': 20}
    assert wnd.doc_ctrl.editor_ux_options()['font_size'] == 20
    assert wnd.doc_ctrl.editor_ux_options_app()['font_size'] == 14

    prj.project_settings_reset(SECTION_EDITOR)
    reverted = fake_edit(wnd, docs[0]).options_calls[-1]
    assert reverted['minimap'] == {'enabled': True}
    assert reverted['fontSize'] == 14
    assert not settings_file(prj_root).exists()


def test_editor_page_loads_app_layer(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Страница «Редактор» читает/пишет слой приложения, эффективное — оверлей."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    docs = open_docs(wnd, prj_root, 'a.py')
    prj.project_settings_set(SECTION_EDITOR, 'font_size', 20)

    page = wnd.settings_ctrl.page_get('editor')
    assert page is not None
    spin = page.widget.findChild(QSpinBox)
    # в PySide6 6.6 стабы findChild не generic — сужаем через isinstance
    assert isinstance(spin, QSpinBox)
    page.load()
    assert spin.value() == 14  # слой приложения, а не эффективные 20

    spin.setValue(11)
    page.apply()
    assert wnd.doc_ctrl.editor_ux_options_app()['font_size'] == 11
    assert wnd.doc_ctrl.editor_ux_options()['font_size'] == 20  # оверлей старше
    assert fake_edit(wnd, docs[0]).options_calls[-1]['fontSize'] == 20


def test_lsp_registry_layers(wnd: IdeMainWindow, prj_root: Path) -> None:
    """Реестр LSP: проектное перекрытие старше слоя приложения, без утечек."""
    hub = wnd.lsp_hub
    prj = wnd.project_ctrl
    assert python_server_names(
        LspRegistry.from_json(hub.registry_app_json())
    ) == python_server_names(LspRegistry.default())

    prj.open_project(prj_root)
    prj.project_settings_set(SECTION_LSP, 'registry_json', SOLO_REGISTRY_JSON)
    assert python_server_names(LspRegistry.from_json(hub.registry_json())) == [
        'solo-server'
    ]

    hub.registry_apply_json(json.dumps({
        'python': {
            'extensions': ['.py'],
            'servers': [{'name': 'app-server', 'command': ['app-langserver']}],
        }
    }))
    assert python_server_names(
        LspRegistry.from_json(hub.registry_app_json())
    ) == ['app-server']
    assert python_server_names(
        LspRegistry.from_json(hub.registry_json())
    ) == ['solo-server']  # правка приложения не пробила перекрытие

    hub.registry_reset()
    assert hub.registry_app_json() == LspRegistry.default().to_json()
    assert python_server_names(
        LspRegistry.from_json(hub.registry_json())
    ) == ['solo-server']  # сброс приложения перекрытие не снимает

    hub.settings_write()
    raw = cast('str', wnd.settings.value('LspHub/registry_json', '', str))
    assert raw == ''  # перекрытие не утекло в QSettings

    prj.project_settings_reset(SECTION_LSP)
    assert python_server_names(
        LspRegistry.from_json(hub.registry_json())
    ) == python_server_names(LspRegistry.default())


def test_settings_api_requires_open_project(wnd: IdeMainWindow) -> None:
    """Без открытого проекта API настроек проекта недоступно."""
    prj = wnd.project_ctrl
    assert prj.theme_provided_by_project() is False
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_EDITOR, 'minimap', True)
    with pytest.raises(ValueError):
        prj.project_settings_reset()
    assert prj.project_settings_get() == {}
    assert prj.project_settings_section('nesuscestvuyushtaya') == {}


def test_settings_api_value_validation(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """Строгая запись: неизвестные секции/ключи и битые значения — отказ."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    with pytest.raises(ValueError):
        prj.project_settings_set('nesuscestvuyushtaya', 'x', 1)
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_APPEARANCE, 'poprosim', 'Темная')
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_APPEARANCE, KEY_THEME_NAME, 'НетТакойТемы')
    with pytest.raises(TypeError):
        prj.project_settings_set(SECTION_EDITOR, 'minimap', 42)
    with pytest.raises(TypeError):
        prj.project_settings_set(SECTION_EDITOR, 'font_size', 'abc')
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_EDITOR, 'font_size', 999)
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_LSP, KEY_REGISTRY_JSON, '{ битый')
    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_LSP, 'poprosim', '{}')
    assert prj.project_settings_get() == {}
    assert not settings_file(prj_root).exists()


def test_settings_remove_key_signal_and_copy(
    wnd: IdeMainWindow, prj_root: Path
) -> None:
    """``None`` снимает ключ; сигнал только на успех; get — копия."""
    prj = wnd.project_ctrl
    prj.open_project(prj_root)
    fired: list[int] = []
    prj.project_settings_changed.connect(lambda: fired.append(1))

    with pytest.raises(ValueError):
        prj.project_settings_set(SECTION_EDITOR, 'font_size', 999)
    assert fired == []  # неудачная запись не эмитит и не пишет

    prj.project_settings_set(SECTION_EDITOR, 'minimap', False)
    assert fired == [1]
    assert settings_file(prj_root).is_file()

    got = prj.project_settings_get()
    assert got == {'editor': {'minimap': False}}
    got['editor']['minimap'] = True  # копия — внутреннее состояние не портим
    assert prj.project_settings_section(SECTION_EDITOR) == {'minimap': False}

    prj.project_settings_set(SECTION_EDITOR, 'minimap', None)  # снятие ключа
    assert prj.project_settings_get() == {}
    assert not settings_file(prj_root).exists()  # пустой оверлей — файл убран
    assert len(fired) == 2
