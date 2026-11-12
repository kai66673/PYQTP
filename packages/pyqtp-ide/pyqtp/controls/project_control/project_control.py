from __future__ import annotations

import itertools
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.lsp_control.lsp_registry import LspRegistry
from pyqtp.controls.project_control.project_fileops import (
    ProjectOpError,
    create_entry,
    move_to_trash,
    renamed_path,
)
from pyqtp.controls.project_control.project_session import (
    SessionData,
    filter_session,
    read_session,
    write_session,
)
from pyqtp.controls.project_control.project_settings import (
    KEY_BREAK_ON_RAISED,
    KEY_REGISTRY_JSON,
    KEY_THEME_NAME,
    KNOWN_SECTIONS,
    SECTION_APPEARANCE,
    SECTION_DEBUG,
    SECTION_EDITOR,
    SECTION_LSP,
    overlay_set,
    read_project_settings,
    write_project_settings,
)
from pyqtp.controls.project_control.quick_open import iter_project_files
from pyqtp.controls.project_control.quick_open_dialog import QuickOpenDialog
from pyqtp.controls.project_control.workspace_symbol_dialog import (
    WorkspaceSymbolDialog,
)
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.project_search_view import ProjectSearchView
from pyqtp.views.project_tree_view import ProjectTreeView
from pyqtp.web.editor_options import (
    DEFAULT_EDITOR_UX_OPTIONS,
    coerce_editor_ux_value,
)

if TYPE_CHECKING:
    from pyqtp.controls.project_control.project_search import SearchMatch
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

prj_log = logging.getLogger('bcad.project')

# ключ проектной области окна настроек (этап 9.6B2, см. SettingsControl)
SCOPE_PROJECT = 'project'


class _ScanSignals(QObject):
    """Мост «фоновый поток -> поток GUI» для результатов скана файлов."""

    finished = Signal(int, list)  # поколение скана + относительные пути


class _RenameSignals(QObject):
    """Мост «поток event-loop хаба -> поток GUI» для ответа willRenameFiles."""

    answered = Signal(int, object, object)  # токен + edit dict|None + err str|None


class ProjectControl(BaseControl):
    """Контрол проекта (этапы 9.1–9.3 + workspace/symbol): папка-корень, панель
    «Проводник», quick open, поиск, переход к символу проекта.

    Хранит текущий корень проекта, сохраняет его в QSettings
    (``<key>/root_path``), создаёт докируемую панель дерева слева
    (``widget_area='left'``), прокидывает двойной клик по файлу в
    ``DocumentManager.open_document`` и держит фоновый кэш файлов проекта
    для диалога «Быстрый переход» (Ctrl+P). Панель «Поиск» (Ctrl+Shift+F,
    ``widget_area='bottom'``) запускает ripgrep и открывает совпадения
    через ``DocumentManager.open_definition_uri``.
    """

    # аннотация ради plug_compatibility_check и типизированного доступа
    # к doc_ctrl/project_ctrl IdeMainWindow (runtime — строка из-за
    # `from __future__ import annotations`, цикл импортов нет)
    _main_wnd: IdeMainWindow

    tree_view: ProjectTreeView | None
    search_view: ProjectSearchView | None

    project_root_changed = Signal(object)  # Path | None
    # настройки проекта (этап 9.6B): оверлей был загружен/сменён/снят и
    # слои уже переприменены
    project_settings_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._project_root: Path | None = None
        # загруженный оверлей настроек проекта {секция: {ключ: значение}}
        # (пустой — проект без .pyqtp/settings.json или проект не открыт)
        self._project_settings: dict[str, dict[str, Any]] = {}
        self.tree_view = None

        self.action_open_folder = Action(
            icon=QIcon(),
            title='Открыть папку...',
            parent=self,
            description='Открыть папку как проект',
            shortcut=QKeySequence('Ctrl+Shift+O'),
        )
        self.action_close_folder = Action(
            icon=QIcon(),
            title='Закрыть папку',
            parent=self,
            description='Закрыть текущий проект',
        )
        self.action_quick_open = Action(
            icon=QIcon(),
            title='Быстрый переход по файлам...',
            parent=self,
            description='Поиск файла проекта по имени (fuzzy)',
            shortcut=QKeySequence('Ctrl+P'),
        )
        self.action_project_search = Action(
            icon=QIcon(),
            title='Поиск по проекту...',
            parent=self,
            description='Полнотекстовый поиск по файлам проекта (ripgrep)',
            shortcut=QKeySequence('Ctrl+Shift+F'),
        )
        self.action_workspace_symbols = Action(
            icon=QIcon(),
            title='Переход к символу проекта...',
            parent=self,
            description='Поиск символов во всём проекте (LSP workspace/symbol)',
            shortcut=QKeySequence('Ctrl+T'),
        )
        self.action_open_folder.triggered.connect(self._choose_folder)
        self.action_close_folder.triggered.connect(self.close_project)
        self.action_quick_open.triggered.connect(self._show_quick_open)
        self.action_project_search.triggered.connect(self._show_project_search)
        self.action_workspace_symbols.triggered.connect(
            self._show_workspace_symbols
        )
        self.action_close_folder.setEnabled(False)
        self.action_quick_open.setEnabled(False)
        self.action_project_search.setEnabled(False)
        self.action_workspace_symbols.setEnabled(False)

        # кэш файлов проекта (быстрый переход) и фоновый скан
        self._scan_signals = _ScanSignals()
        self._scan_signals.finished.connect(self._on_scan_ready)
        self._scan_gen = 0
        self._quick_files: list[str] = []
        self._quick_dialog: QuickOpenDialog | None = None
        self._symbol_dialog: WorkspaceSymbolDialog | None = None

        # файловые операции «Проводника» (этап 9.5): переименование ждёт
        # ответа willRenameFiles в потоке GUI по токену; устаревшие ответы
        # (смена корня/teardown) отбрасываются очисткой карты
        self._rename_signals = _RenameSignals()
        self._rename_signals.answered.connect(self._on_rename_answered)
        self._rename_seq = itertools.count(1)
        self._pending_renames: dict[int, dict[str, Any]] = {}

        # проектная сессия (этап 9.6): снимок, восстановленный из
        # <root>/.pyqtp/session.json при открытии проекта (расходится
        # при смене/закрытии корня)
        self._loaded_session: SessionData | None = None

    ###############
    # Настройки #

    def settings_read(self, **kwargs: Any) -> None:
        super().settings_read(**kwargs)
        raw = str(self._main_wnd.settings.value(f'{self.key}/root_path', '') or '')
        if not raw:
            return
        path = Path(raw)
        if path.is_dir():
            self._project_root = path
        else:
            prj_log.warning(
                'Сохранённая папка проекта недоступна и пропущена: %s', path
            )

    def settings_write(self) -> None:
        super().settings_write()
        self._persist_root()

    def on_shutdown(self) -> bool:
        # закрываемся с открытым проектом — сохраняем его сессию, пока
        # вкладки документов ещё живы (до teardown'а контролов)
        if self._project_root is not None:
            self._save_project_session()
        return True

    ####################
    # Жизненный цикл #

    def setup(self) -> None:
        super().setup()
        self.action_open_folder.setIcon(
            self._main_wnd.theme_manager.icon('io/open.svg')
        )
        self.project_root_changed.connect(self._on_root_for_scan)
        # область «Проект» в окне настроек (этап 9.6B2): доступна, пока
        # открыт проект; окно пересобирает селектор на смене корня
        self._main_wnd.settings_scope_register(
            SCOPE_PROJECT, 'Проект', lambda: self._project_root is not None
        )
        self.project_root_changed.connect(
            lambda _root: self._main_wnd.settings_scopes_refresh()
        )
        # API проекта для Python-консоли (этап 9.1; расширение — этап 12)
        self._main_wnd.api_ctrl.register_variables(
            {
                'project_root': lambda: self._project_root,
                'project_open': self.open_project,
                'project_close': self.close_project,
                'quick_open_show': self._show_quick_open,
                'project_search_show': self._show_project_search,
                'workspace_symbols_show': self._show_workspace_symbols,
                # снимок сессии (пустой, если проект не открыт) — этап 9.6
                'project_session_show': self.session_snapshot,
                # настройки проекта (слой перекрытий, этап 9.6B)
                'project_settings_show': self.project_settings_get,
                'project_settings_set': self.project_settings_set,
                'project_settings_reset': self.project_settings_reset,
            }
        )

    def setup_menu(self) -> None:
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            file_menu = menubar.by_path('Файл')
        except KeyError:
            file_menu = menubar.addMenu('Файл', priority=-10)

        assert isinstance(file_menu, PriorityMenu)

        file_menu.addAction(self.action_open_folder, priority=4)
        file_menu.addAction(self.action_quick_open, priority=7)
        file_menu.addAction(self.action_workspace_symbols, priority=8)
        file_menu.addAction(self.action_project_search, priority=9)
        file_menu.addAction(self.action_close_folder, priority=10)

    def setup_views(self) -> None:
        self.tree_view = view = ProjectTreeView(
            self._main_wnd, key=f'{self.key}_tree_view'
        )
        self._main_wnd.view_register(view, widget_area='left')

        view.file_open_requested.connect(self._open_file_from_tree)
        view.create_file_requested.connect(self._on_create_file_requested)
        view.create_folder_requested.connect(self._on_create_folder_requested)
        view.rename_requested.connect(self._on_rename_requested)
        view.delete_requested.connect(self._on_delete_requested)
        self.project_root_changed.connect(view.set_project_root)

        self.search_view = search_view = ProjectSearchView(
            self._main_wnd, key=f'{self.key}_search_view'
        )
        self._main_wnd.view_register(search_view, widget_area='bottom')
        search_view.match_activated.connect(self._open_search_match)
        self.project_root_changed.connect(search_view.set_project_root)

        if self._project_root is not None:
            view.set_project_root(self._project_root)
            search_view.set_project_root(self._project_root)
            self.action_close_folder.setEnabled(True)
            self._start_scan()
            # настройки проекта (этап 9.6B): поднимаем оверлей ДО restore
            # сессии и до эмита корня — тема/опции редакторов должны быть
            # уже эффективными к моменту открытия вкладок
            self._load_project_settings(self._project_root)
            self._apply_project_layers()
            # сессия проекта (этап 9.6): восстанавливаем вкладки, сохранённые
            # при последнем закрытии этого проекта
            self._restore_project_session(self._project_root)

        # подсветка открытого документа в дереве (смену активной вкладки
        # пока не отслеживаем — сигнала об активации в core нет)
        self._main_wnd.doc_ctrl.document_opened.connect(
            lambda doc_view: view.reveal_path(doc_view.key)
        )

    def teardown_views(self) -> None:
        super().teardown_views()
        self._scan_gen += 1  # сброс: запоздалые результаты скана не нужны
        self._loaded_session = None
        self._pending_renames.clear()  # чужие ответы willRenameFiles не нужны
        if self._symbol_dialog is not None:
            self._symbol_dialog.drop_pending()
        if self.search_view is not None:
            self.search_view.stop_search()
            self._main_wnd.view_deregister(self.search_view.key)
            self.search_view = None
        if self.tree_view is not None:
            self._main_wnd.view_deregister(self.tree_view.key)
            self.tree_view = None

    #####################
    # Публичный API     #

    @property
    def project_root(self) -> Path | None:
        return self._project_root

    def open_project(self, path: str | Path) -> bool:
        """Открывает папку как проект (``False`` — папки нет)."""
        candidate = Path(path).expanduser()
        if not candidate.is_dir():
            prj_log.warning('Не папка проекта, пропущено: %s', candidate)
            return False
        resolved = candidate.resolve()
        if resolved == self._project_root:
            return True
        if self._project_root is not None:
            # переключение проекта: сначала проводы старого (сохранение
            # сессии + закрытие его вкладок) — этап 9.6
            self.close_project()
        self._project_root = resolved
        self.action_close_folder.setEnabled(True)
        self._persist_root()
        # настройки проекта (этап 9.6B): читаем оверлей и переприменяем слои
        # до эмита корня — restore сессии в _on_root_for_scan откроет вкладки
        # уже с проектными настройками
        self._load_project_settings(resolved)
        self._apply_project_layers()
        self.project_root_changed.emit(resolved)
        prj_log.info('Открыт проект: %s', resolved)
        return True

    def close_project(self) -> bool:
        """Закрывает текущий проект (``False`` — открывать нечего)."""
        if self._project_root is None:
            return False
        # сначала сохраняем сессию уходящего проекта (этап 9.6)
        self._save_project_session()
        # настройки проекта (этап 9.6B): снимаем оверлей и откатываем слои
        # к настройкам приложения (файл .pyqtp/settings.json на диске живёт
        # с проектом и не трогается)
        self._project_settings = {}
        self._apply_project_layers()
        root = self._project_root
        self._project_root = None
        self.action_close_folder.setEnabled(False)
        self._persist_root()
        self.project_root_changed.emit(None)
        # затем закрываем вкладки проекта; документы с несохранёнными
        # правками остаются открытыми (сессия уже сохранена, данные не
        # выбрасываем)
        self._close_project_documents(root)
        prj_log.info('Проект закрыт: %s', root)
        return True

    def _close_project_documents(self, root: Path) -> None:
        """Закрывает вкладки документов внутри ``root`` (грязные остаются)."""
        doc_ctrl = self._main_wnd.doc_ctrl
        for path in doc_ctrl.documents_under(root):
            if not doc_ctrl.close_document(path):
                prj_log.warning(
                    'Документ с несохранёнными правками оставлен открытым: %s',
                    path,
                )

    #####################
    # Внутреннее        #

    def _choose_folder(self) -> None:
        start = str(self._project_root or Path.home())
        dir_name = QFileDialog.getExistingDirectory(
            parent=self._main_wnd,
            caption='Открыть папку проекта',
            dir=start,
            options=QFileDialog.Option.ShowDirsOnly
            | QFileDialog.Option.DontUseNativeDialog,
        )
        if dir_name:
            self.open_project(dir_name)

    def _open_file_from_tree(self, path_str: str) -> None:
        self._main_wnd.doc_ctrl.open_document(path_str)

    #####################
    # Quick open (9.2)  #

    def _on_root_for_scan(self, root: Path | None) -> None:
        """Смена корня проекта: старт/сброс фонового кэша файлов + экшен поиска."""
        if root is None:
            self._scan_gen += 1
            self._pending_renames.clear()  # чужие ответы willRenameFiles не нужны
            self._loaded_session = None  # снятая сессия не должна вернуться
            self._quick_files = []
            self.action_quick_open.setEnabled(False)
            self.action_project_search.setEnabled(False)
            self.action_workspace_symbols.setEnabled(False)
            if self._quick_dialog is not None:
                self._quick_dialog.set_index(None, [])
            if self._symbol_dialog is not None:
                self._symbol_dialog.set_project(None)
            return
        self._start_scan()
        # смена корня в рантайме (open_project из меню/консоли) — поднимаем
        # сессию проекта (при старте restore делает setup_views, сигнал там
        # не эмится — двойного восстановления нет)
        self._restore_project_session(root)

    def _start_scan(self) -> None:
        """Фоновый обход проекта; результат — по поколению скана."""
        if self._project_root is None:
            return
        self._scan_gen += 1
        gen = self._scan_gen
        self.action_quick_open.setEnabled(True)
        self.action_project_search.setEnabled(True)
        self.action_workspace_symbols.setEnabled(True)
        root = self._project_root
        if self._symbol_dialog is not None and root is not None:
            self._symbol_dialog.set_project(root)
        signals = self._scan_signals

        def _work() -> None:
            try:
                files = iter_project_files(root)
            except OSError as exc:
                prj_log.warning('Скан файлов проекта сорван: %s', exc)
                files = []
            # emit из python-потока: PySide доставляет queued-сигналом в GUI
            signals.finished.emit(gen, files)

        threading.Thread(target=_work, name='pyqtp-project-scan', daemon=True).start()

    def _on_scan_ready(self, gen: int, files: list) -> None:
        if gen != self._scan_gen:
            return  # проект уже сменился/закрыт
        self._quick_files = list(files)
        prj_log.debug('Кэш файлов проекта: %d', len(self._quick_files))
        if self._quick_dialog is not None:
            self._quick_dialog.set_index(self._project_root, self._quick_files)

    def _show_quick_open(self) -> None:
        if self._project_root is None:
            prj_log.info('Быстрый переход: проект не открыт')
            return
        if self._quick_dialog is None:
            self._quick_dialog = QuickOpenDialog(self._main_wnd)
            self._quick_dialog.file_selected.connect(self._open_file_from_tree)
        self._quick_dialog.set_index(self._project_root, self._quick_files)
        self._quick_dialog.show_for_use()

    #####################
    # Поиск             #

    def _show_project_search(self) -> None:
        if self._project_root is None:
            prj_log.info('Поиск по проекту: проект не открыт')
            return
        if self.search_view is None:
            return
        self._main_wnd.view_show(self.search_view.key)
        self.search_view.focus_query()

    def _open_search_match(self, match: SearchMatch) -> None:
        """Совпадение из панели «Поиск» -> открыть файл и каретка на месте."""
        self._main_wnd.doc_ctrl.open_definition_uri(
            {
                'uri': QUrl.fromLocalFile(str(match.path)).toString(),
                'range': {
                    'startLineNumber': match.line,
                    'startColumn': match.column,
                },
            }
        )

    ##############################
    # Символы проекта (workspace) #

    def _show_workspace_symbols(self) -> None:
        if self._project_root is None:
            prj_log.info('Переход к символу: проект не открыт')
            return
        if self._symbol_dialog is None:
            self._symbol_dialog = WorkspaceSymbolDialog(
                self._main_wnd, self._main_wnd.lsp_hub
            )
            self._symbol_dialog.symbol_selected.connect(
                self._open_symbol_payload
            )
        self._symbol_dialog.set_project(self._project_root)
        self._symbol_dialog.show_for_use()

    def _open_symbol_payload(self, payload: dict) -> None:
        """Символ из диалога -> открыть файл и каретка на месте определения."""
        self._main_wnd.doc_ctrl.open_definition_uri(payload)

    ###############################
    # Сессия проекта (этап 9.6)   #

    def session_snapshot(self) -> SessionData:
        """Снимок текущей сессии (пустой, если проект не открыт)."""
        if self._project_root is None:
            return SessionData()
        return self._main_wnd.doc_ctrl.session_snapshot()

    def _save_project_session(self) -> None:
        """Пишет ``<root>/.pyqtp/session.json`` по текущим вкладкам."""
        if self._project_root is None:
            return
        try:
            write_session(
                self._project_root,
                self._main_wnd.doc_ctrl.session_snapshot(),
            )
        except OSError as exc:
            prj_log.warning('Не удалось сохранить сессию проекта: %s', exc)

    def _restore_project_session(self, root: Path) -> None:
        """Открывает вкладки из ``<root>/.pyqtp/session.json``.

        Несуществующие файлы пропускаем (сообщаем в журнал); активной
        становится сохранённая вкладка, а если её нет — последняя
        восстановленная.
        """
        try:
            stored = read_session(root)
        except OSError as exc:
            prj_log.warning('Не удалось прочитать сессию проекта: %s', exc)
            return
        if not stored.docs:
            return
        session, missing = filter_session(stored)
        for skipped in missing:
            prj_log.info('Файл из сессии отсутствует, пропущен: %s', skipped)
        if not session.docs:
            return
        self._loaded_session = session
        for doc in session.docs:
            self._main_wnd.doc_ctrl.open_document(doc)
        if session.active is not None:
            view = self._main_wnd.doc_ctrl.document_view(session.active)
            if view is not None:
                view.ensure_visible()
        prj_log.info(
            'Сессия проекта восстановлена: %d документ(ов), активный: %s',
            len(session.docs), session.active or '—',
        )

    ######################################################
    # Настройки проекта (этап 9.6B, .pyqtp/settings.json) #

    def project_settings_get(self) -> dict[str, dict[str, Any]]:
        """Копия текущего загруженного оверлея проекта (пустой — нет слоя)."""
        return {
            name: dict(values) for name, values in self._project_settings.items()
        }

    def project_settings_section(self, name: str) -> dict[str, Any]:
        """Копия перекрытий одной секции (пустой — нет перекрытий).

        Точка входа для владельцев настроек: опции редактора читают
        секцию ``editor`` из DocumentManager, тема и LSP-реестр применяются
        централизованно в ``_apply_project_layers``.
        """
        values = self._project_settings.get(name)
        return dict(values) if values else {}

    def theme_provided_by_project(self) -> bool:
        """Держит ли проектный оверлей эффективную тему приложения."""
        values = self._project_settings.get(SECTION_APPEARANCE)
        if not values:
            return False
        theme_name = values.get(KEY_THEME_NAME)
        return (
            isinstance(theme_name, str)
            and theme_name in self._main_wnd.theme_manager.theme_names
        )

    def project_settings_set(
        self, section: str, key: str, value: Any
    ) -> None:
        """Задаёт (``value is None`` — снимает) один ключ проектного оверлея.

        ``ValueError``/``TypeError`` при открытом отсутствии проекта,
        неизвестной секции/ключе и испорченном значении (запись строгая,
        чтение недоедливое); после успеха — запись файла, перечитывание
        состояния, переприменение всех слоёв и emit
        ``project_settings_changed``.
        """
        if self._project_root is None:
            error_message = 'Проект не открыт — настройки проекта недоступны'
            raise ValueError(error_message)
        self._validate_project_setting(section, key, value)
        updated = overlay_set(self._project_settings, section, key, value)
        try:
            write_project_settings(self._project_root, updated)
        except OSError as exc:
            error_message = f'Не удалось записать настройки проекта: {exc}'
            raise ValueError(error_message) from exc
        self._project_settings = updated
        self._apply_project_layers()
        self.project_settings_changed.emit()

    def project_settings_update(
        self, section: str, values: dict[str, Any]
    ) -> None:
        """Задаёт (``value is None`` — снимает) набор ключей одной секции.

        Тот же контракт, что у ``project_settings_set``, но валидация всех
        ключей и запись файла — один раз (страница «Редактор» применяет
        сразу 5 ключей; при ошибке любого из них источник не тронут).
        """
        if self._project_root is None:
            error_message = 'Проект не открыт — настройки проекта недоступны'
            raise ValueError(error_message)
        for key, value in values.items():
            self._validate_project_setting(section, key, value)
        updated = self._project_settings
        for key, value in values.items():
            updated = overlay_set(updated, section, key, value)
        try:
            write_project_settings(self._project_root, updated)
        except OSError as exc:
            error_message = f'Не удалось записать настройки проекта: {exc}'
            raise ValueError(error_message) from exc
        self._project_settings = updated
        self._apply_project_layers()
        self.project_settings_changed.emit()

    def project_settings_reset(self, section: str | None = None) -> None:
        """Снимает оверлей проекта целиком или одну секцию.

        ``ValueError`` при незакрытом проекте/неизвестной секции; файл на
        диске при пустом результате удаляется (см. ``write_project_settings``).
        """
        if self._project_root is None:
            error_message = 'Проект не открыт — настройки проекта недоступны'
            raise ValueError(error_message)
        if section is not None and section not in KNOWN_SECTIONS:
            error_message = f'Неизвестная секция настроек: {section}'
            raise ValueError(error_message)
        updated: dict[str, dict[str, Any]] = {
            name: dict(values)
            for name, values in self._project_settings.items()
            if section is None or name != section
        }
        try:
            write_project_settings(self._project_root, updated)
        except OSError as exc:
            error_message = f'Не удалось записать настройки проекта: {exc}'
            raise ValueError(error_message) from exc
        self._project_settings = updated
        self._apply_project_layers()
        self.project_settings_changed.emit()

    def _validate_project_setting(
        self, section: str, key: str, value: Any
    ) -> None:
        """Валидация записываемого значения (для console-API/окна настроек).

        Бросает ``ValueError``/``TypeError`` с человекочитаемым текстом.
        """
        if section not in KNOWN_SECTIONS:
            error_message = f'Неизвестная секция настроек: {section}'
            raise ValueError(error_message)
        if value is None:  # снятие перекрытия — значение любое
            return
        if section == SECTION_APPEARANCE:
            self._validate_appearance_setting(section, key, value)
        elif section == SECTION_EDITOR:
            self._validate_editor_setting(section, key, value)
        elif section == SECTION_LSP:
            self._validate_lsp_setting(section, key, value)
        elif section == SECTION_DEBUG:
            self._validate_debug_setting(section, key, value)

    def _validate_appearance_setting(
        self, section: str, key: str, value: Any
    ) -> None:
        if key != KEY_THEME_NAME:
            error_message = f'Неизвестный ключ секции «{section}»: {key}'
            raise ValueError(error_message)
        names = self._main_wnd.theme_manager.theme_names
        if not (isinstance(value, str) and value in names):
            error_message = 'Тема должна быть одной из: ' + ', '.join(names)
            raise ValueError(error_message)

    def _validate_editor_setting(
        self, section: str, key: str, value: Any
    ) -> None:
        if key not in DEFAULT_EDITOR_UX_OPTIONS:
            error_message = f'Неизвестный ключ секции «{section}»: {key}'
            raise ValueError(error_message)
        coerce_editor_ux_value(key, value)  # ValueError/TypeError с текстом

    def _validate_lsp_setting(self, section: str, key: str, value: Any) -> None:
        if key != KEY_REGISTRY_JSON:
            error_message = f'Неизвестный ключ секции «{section}»: {key}'
            raise ValueError(error_message)
        if not isinstance(value, str):
            error_message = 'LSP-реестр задаётся JSON-строкой'
            raise ValueError(error_message)  # noqa: TRY004 — контракт API
        try:
            LspRegistry.from_json(value)
        except (TypeError, ValueError) as exc:
            error_message = f'Некорректный JSON LSP-реестра: {exc}'
            raise ValueError(error_message) from exc

    def _validate_debug_setting(
        self, section: str, key: str, value: Any
    ) -> None:
        # этап 11 D3: фильтры исключений отладчика
        if key != KEY_BREAK_ON_RAISED:
            error_message = f'Неизвестный ключ секции «{section}»: {key}'
            raise ValueError(error_message)
        if not isinstance(value, bool):
            error_message = (
                'Настройка «Останавливаться на исключениях» должна быть '
                'флагом (True/False)'
            )
            raise ValueError(error_message)  # noqa: TRY004 — контракт API

    def _load_project_settings(self, root: Path) -> None:
        """Читает оверлей ``<root>/.pyqtp/settings.json`` в память.

        Нет файла/битый JSON — пустой оверлей (проект без настроек живёт на
        настройках приложения); ошибки ФС — только лог.
        """
        try:
            self._project_settings = read_project_settings(root)
        except OSError as exc:
            prj_log.warning('Не удалось прочитать настройки проекта: %s', exc)
            self._project_settings = {}

    def _apply_project_layers(self) -> None:
        """Делает текущий оверлей эффективным для всех владельцев настроек.

        - тема: проектный ``theme_name``, если он известен, иначе — слой
          приложения (откат при смене/закрытии проекта);
        - редактор: широкое переприменение ЭФФЕКТИВНЫХ UX-опций открытым
          редакторам (новые подхватят их при открытии сами);
        - LSP: секция ``lsp.registry_json`` как проектное перекрытие
          реестра (``None`` — слой приложения).
        """
        wnd = self._main_wnd
        appearance = self._project_settings.get(SECTION_APPEARANCE) or {}
        theme_name = appearance.get(KEY_THEME_NAME)
        if (
            isinstance(theme_name, str)
            and theme_name in wnd.theme_manager.theme_names
        ):
            wnd.theme_apply(theme_name)
        else:
            wnd.theme_apply(wnd.theme_app_name)
        wnd.doc_ctrl.broadcast_effective_editor_ux_options()
        lsp_values = self._project_settings.get(SECTION_LSP) or {}
        raw_registry = lsp_values.get(KEY_REGISTRY_JSON)
        wnd.lsp_hub.registry_set_project_overlay(
            raw_registry if isinstance(raw_registry, str) else None
        )

    ############################################
    # Файловые операции «Проводника» (этап 9.5) #

    def _on_create_file_requested(self, dir_str: str) -> None:
        self._create_entry(dir_str, is_dir=False)

    def _on_create_folder_requested(self, dir_str: str) -> None:
        self._create_entry(dir_str, is_dir=True)

    def _create_entry(self, dir_str: str, *, is_dir: bool) -> None:
        """Диалог имени -> ``project_fileops.create_entry`` -> reveal в дереве."""
        if self._project_root is None:
            return
        title = 'Новая папка' if is_dir else 'Новый файл'
        name, ok = QInputDialog.getText(self._main_wnd, title, 'Имя:')
        if not ok:
            return
        try:
            created = create_entry(Path(dir_str), name, is_dir=is_dir)
        except ProjectOpError as exc:
            self._file_op_warning(str(exc))
            return
        prj_log.info('%s создан(а): %s', title, created)
        if self.tree_view is not None:
            # QFileSystemModel замечает изменение каталога сам через watcher;
            # reveal дождётся наполнения строками шагами (как в 9.1)
            self.tree_view.reveal_path(created)

    def _on_rename_requested(self, path_str: str) -> None:
        """Диалог имени -> willRenameFiles -> правки -> дисковый rename.

        Документ открыт c правками — отказ (пусть пользователь сохранит);
        открытый чистый закрываем заранее, чтобы сервер правил диск, а
        каретка/вкладка вернулись уже на новый путь. LSP-сбой не блокирует
        переименование — только предупреждение в журнал.
        """
        if self._project_root is None:
            return
        target = Path(path_str)
        new_name, ok = QInputDialog.getText(
            self._main_wnd,
            'Переименовать',
            'Новое имя:',
            QLineEdit.EchoMode.Normal,
            target.name,
        )
        if not ok:
            return
        try:
            new_path = renamed_path(target, new_name)
        except ProjectOpError as exc:
            self._file_op_warning(str(exc))
            return
        doc_ctrl = self._main_wnd.doc_ctrl
        was_open = doc_ctrl.has_document(target)
        if was_open and doc_ctrl.document_is_modified(target):
            self._file_op_warning(
                'Файл открыт в редакторе с несохранёнными правками — '
                'сначала сохраните его (Ctrl+S).'
            )
            return
        if was_open and not doc_ctrl.close_document(target):
            self._file_op_warning('Не удалось закрыть документ — повторите позже.')
            return
        token = next(self._rename_seq)
        self._pending_renames[token] = {
            'old': target,
            'new': new_path,
            'was_open': was_open,
        }
        root = self._project_root
        signals = self._rename_signals

        def _cb(edit: Any, error: Any) -> None:
            # хаб зовёт колбэк из своего потока — в GUI доставляем queued
            signals.answered.emit(token, edit, error)

        try:
            self._main_wnd.lsp_hub.will_rename_files_async(
                root, target.as_uri(), new_path.as_uri(), cb=_cb,
            )
        except (RuntimeError, OSError) as exc:
            # хаб не поднялся — обходимся без LSP-правок
            prj_log.warning('willRenameFiles недоступен: %s', exc)
            self._pending_renames.pop(token, None)
            self._finish_rename(target, new_path, None, None, was_open=was_open)

    def _on_rename_answered(self, token: int, edit: object, error: object) -> None:
        """Ответ willRenameFiles из потока хаба (queued-мост ``_RenameSignals``)."""
        ctx = self._pending_renames.pop(token, None)
        if ctx is None:
            return  # проект уже сменился/закрыт — запоздалый ответ
        self._finish_rename(
            ctx['old'], ctx['new'], edit, error, was_open=ctx['was_open']
        )

    def _finish_rename(
        self,
        old: Path,
        new: Path,
        edit: object,
        error: object,
        *,
        was_open: bool,
    ) -> None:
        """Применить LSP-правки, переименовать на диске, вернуть вкладку."""
        doc_ctrl = self._main_wnd.doc_ctrl
        if isinstance(edit, dict) and edit:
            try:
                doc_ctrl.on_workspace_edit_requested(
                    {'origin': 'rename', 'workspaceEdit': edit}
                )
            except OSError as exc:
                # правки чужих файлов могли не лечь (диск/конкурентная правка) —
                # само переименование это не отменяет
                prj_log.warning('Применение LSP-правок переименования: %s', exc)
        if error:
            prj_log.warning('LSP не сопроводил переименование: %s', error)
        if new.exists():
            # пока ждали ответ сервера, имя мог занять кто-то другой
            self._file_op_warning(
                f'«{new.name}» уже существует — переименование отменено.'
            )
            if was_open:
                doc_ctrl.open_document(old)
            return
        try:
            old.rename(new)
        except OSError as exc:
            self._file_op_warning(f'Не удалось переименовать: {exc}')
            if was_open:
                doc_ctrl.open_document(old)
            return
        prj_log.info('Переименовано: %s -> %s', old, new)
        if self._project_root is not None:
            try:
                self._main_wnd.lsp_hub.did_rename_files(
                    self._project_root, old.as_uri(), new.as_uri()
                )
            except RuntimeError as exc:
                prj_log.debug('didRenameFiles пропущен: %s', exc)
        if was_open:
            doc_ctrl.open_document(new)
        if self.tree_view is not None:
            self.tree_view.reveal_path(new)

    def _on_delete_requested(self, path_str: str) -> None:
        """Подтверждение -> закрытие связанных документов -> корзина (QFile)."""
        if self._project_root is None:
            return
        target = Path(path_str)
        if not target.exists():
            self._file_op_warning('Запись уже пропала с диска.')
            return
        answer = QMessageBox.question(
            self._main_wnd,
            'Удаление',
            f'Переместить «{target.name}» в корзину?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        doc_ctrl = self._main_wnd.doc_ctrl
        affected = doc_ctrl.documents_under(target)
        if any(doc_ctrl.document_is_modified(path) for path in affected):
            self._file_op_warning(
                'Среди удаляемых файлов есть открытые с несохранёнными '
                'правками — сначала сохраните их (Ctrl+S).'
            )
            return
        for path in affected:
            doc_ctrl.close_document(path)
        if not move_to_trash(target):
            self._file_op_warning(
                f'Не удалось переместить «{target.name}» в корзину '
                '(возможно, на этом томе корзины нет).'
            )
            return
        prj_log.info('Перемещено в корзину: %s', target)

    def _file_op_warning(self, text: str) -> None:
        """Предупреждение об отказе файловой операции (журнал + диалог)."""
        prj_log.warning('Операция с файлом: %s', text)
        QMessageBox.warning(self._main_wnd, 'Операция с файлом', text)

    def _persist_root(self) -> None:
        settings = self._main_wnd.settings
        key = f'{self.key}/root_path'
        if self._project_root is None:
            settings.remove(key)
        else:
            settings.setValue(key, str(self._project_root))
        settings.sync()
