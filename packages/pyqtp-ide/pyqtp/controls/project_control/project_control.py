from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import QFileDialog

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.project_control.quick_open import iter_project_files
from pyqtp.controls.project_control.quick_open_dialog import QuickOpenDialog
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.project_search_view import ProjectSearchView
from pyqtp.views.project_tree_view import ProjectTreeView

if TYPE_CHECKING:
    from pyqtp.controls.project_control.project_search import SearchMatch
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

prj_log = logging.getLogger('bcad.project')


class _ScanSignals(QObject):
    """Мост «фоновый поток -> поток GUI» для результатов скана файлов."""

    finished = Signal(int, list)  # поколение скана + относительные пути


class ProjectControl(BaseControl):
    """Контрол проекта (этапы 9.1–9.3): папка-корень, панель «Проводник», quick open, поиск.

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

    def __init__(self) -> None:
        super().__init__()
        self._project_root: Path | None = None
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
        self.action_open_folder.triggered.connect(self._choose_folder)
        self.action_close_folder.triggered.connect(self.close_project)
        self.action_quick_open.triggered.connect(self._show_quick_open)
        self.action_project_search.triggered.connect(self._show_project_search)
        self.action_close_folder.setEnabled(False)
        self.action_quick_open.setEnabled(False)
        self.action_project_search.setEnabled(False)

        # кэш файлов проекта (быстрый переход) и фоновый скан
        self._scan_signals = _ScanSignals()
        self._scan_signals.finished.connect(self._on_scan_ready)
        self._scan_gen = 0
        self._quick_files: list[str] = []
        self._quick_dialog: QuickOpenDialog | None = None

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

    ####################
    # Жизненный цикл #

    def setup(self) -> None:
        super().setup()
        self.action_open_folder.setIcon(
            self._main_wnd.theme_manager.icon('io/open.svg')
        )
        self.project_root_changed.connect(self._on_root_for_scan)
        # API проекта для Python-консоли (этап 9.1; расширение — этап 12)
        self._main_wnd.api_ctrl.register_variables(
            {
                'project_root': lambda: self._project_root,
                'project_open': self.open_project,
                'project_close': self.close_project,
                'quick_open_show': self._show_quick_open,
                'project_search_show': self._show_project_search,
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
        file_menu.addAction(self.action_project_search, priority=8)
        file_menu.addAction(self.action_close_folder, priority=9)

    def setup_views(self) -> None:
        self.tree_view = view = ProjectTreeView(
            self._main_wnd, key=f'{self.key}_tree_view'
        )
        self._main_wnd.view_register(view, widget_area='left')

        view.file_open_requested.connect(self._open_file_from_tree)
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

        # подсветка открытого документа в дереве (смену активной вкладки
        # пока не отслеживаем — сигнала об активации в core нет)
        self._main_wnd.doc_ctrl.document_opened.connect(
            lambda doc_view: view.reveal_path(doc_view.key)
        )

    def teardown_views(self) -> None:
        super().teardown_views()
        self._scan_gen += 1  # сброс: запоздалые результаты скана не нужны
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
        self._project_root = resolved
        self.action_close_folder.setEnabled(True)
        self._persist_root()
        self.project_root_changed.emit(resolved)
        prj_log.info('Открыт проект: %s', resolved)
        return True

    def close_project(self) -> bool:
        """Закрывает текущий проект (``False`` — открывать нечего)."""
        if self._project_root is None:
            return False
        self._project_root = None
        self.action_close_folder.setEnabled(False)
        self._persist_root()
        self.project_root_changed.emit(None)
        prj_log.info('Проект закрыт')
        return True

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
            self._quick_files = []
            self.action_quick_open.setEnabled(False)
            self.action_project_search.setEnabled(False)
            if self._quick_dialog is not None:
                self._quick_dialog.set_index(None, [])
            return
        self._start_scan()

    def _start_scan(self) -> None:
        """Фоновый обход проекта; результат — по поколению скана."""
        if self._project_root is None:
            return
        self._scan_gen += 1
        gen = self._scan_gen
        self.action_quick_open.setEnabled(True)
        self.action_project_search.setEnabled(True)
        root = self._project_root
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

    def _persist_root(self) -> None:
        settings = self._main_wnd.settings
        key = f'{self.key}/root_path'
        if self._project_root is None:
            settings.remove(key)
        else:
            settings.setValue(key, str(self._project_root))
        settings.sync()
