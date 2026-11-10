from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, QKeySequence, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolBar

from pyqtp.base.base_document_view import BaseDocumentView
from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.doc_control.editor_settings_page import EditorSettingsPage
from pyqtp.controls.doc_control.workspace_edit import (
    CREATE_FILE,
    DELETE_FILE,
    RENAME_FILE,
    apply_text_edits_to_text,
    create_file,
    delete_file,
    is_text_document_edit,
    normalize_document_changes,
    path_from_file_uri,
    rename_file,
)
from pyqtp.controls.project_control.project_session import SessionData
from pyqtp.controls.project_control.project_settings import SECTION_EDITOR
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.monaco_document_view import MonacoDocumentView
from pyqtp.web.editor_options import (
    DEFAULT_EDITOR_UX_OPTIONS,
    merge_editor_ux_options,
    read_editor_ux_options,
    resolve_monaco_theme,
    to_monaco_options,
    validate_editor_ux_options,
    write_editor_ux_options,
)

if TYPE_CHECKING:
    from pyqtp.base.docking_system.base_dock_widget import BaseDockWidget
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow
    from pyqtp.theme.theme import Theme


def _document_view_cls_from_identity(_identity: str) -> type[BaseDocumentView]:
    return MonacoDocumentView


med_log = logging.getLogger('bcad.med')


class DocumentManager(BaseControl):
    _main_wnd: IdeMainWindow
    toolbar: QToolBar

    _last_open_dir: Path
    _opened_docs: dict[str, BaseDocumentView]
    _active_doc_key: str | None

    document_opened = Signal(BaseDocumentView)
    document_closed = Signal(BaseDocumentView)
    # этап 10.2: смена активной вкладки (фильтр «только текущий файл» в
    # панели «Проблемы» и F8-навигация смотрят на неё)
    document_activated = Signal(BaseDocumentView)

    def __init__(self) -> None:
        super().__init__()

        self._last_open_dir = Path.home()
        self._opened_docs = {}
        self._last_activated_key = None

        self.action_open_doc = Action(
            icon=QIcon(),
            title='Открыть Файл',
            parent=self,
            description='Открыть существующий документ',
            shortcut=QKeySequence.StandardKey.Open,
        )

        self.action_save_doc = Action(
            icon=QIcon(),
            title='Сохранить Файл',
            parent=self,
            description='Сохранить текущий документ',
            shortcut=QKeySequence.StandardKey.Save,
        )

        self.action_open_doc.triggered.connect(
            self._open_document
        )

    def setup_relations(self) -> None:
        super().setup_relations()

        # Регистрируем журнал редактора в панели событий
        # (bcad.med пишет QtMonacoEditorWidget из pyqtp/web/qtmonaco_editor.py,
        # qtmonaco -- сама vendored-библиотека, её дочерние логгеры
        # перехватываются по наследованию)
        self._main_wnd.events_ctrl.events_log_register(
            name=med_log.name, group='Редактор'
        )
        self._main_wnd.events_ctrl.events_log_register(
            name='qtmonaco', group='Редактор'
        )

        # тема редактора (этап 7): подписка на смену темы приложения — в
        # режиме «Как в приложении» тема Monaco реально меняется, при явной
        # светлой/тёмной остаётся прежней (переприменение идемпотентно)
        self._main_wnd.theme_manager.theme_changed.connect(
            self._on_app_theme_changed
        )

    def setup(self) -> None:
        super().setup()

        self.action_open_doc.setIcon(
            self._main_wnd.theme_manager.icon('io/open.svg')
        )
        self.action_save_doc.setIcon(
            self._main_wnd.theme_manager.icon('io/save.svg')
        )

        # этап 7: страница «Редактор» в окне настроек приложения
        self._main_wnd.settings_page_register(EditorSettingsPage(self))

    def setup_toolbars(self) -> None:
        self.toolbar = toolbar = QToolBar('Документы')
        toolbar.setObjectName('DocumentsToolBar')
        toolbar.addAction(self.action_open_doc)
        toolbar.addAction(self.action_save_doc)
        self._main_wnd.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def setup_menu(self) -> None:
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            self.file_menu = menubar.by_path('Файл')
        except KeyError:
            self.file_menu = menubar.addMenu('Файл', priority=-10)

        assert isinstance(self.file_menu, PriorityMenu)

        self.file_menu.addAction(self.action_open_doc, priority=5)
        self.file_menu.addAction(self.action_save_doc, priority=6)

    # --- UX-настройки редактора (этап 7, страница «Редактор»; два слоя
    #     с этапом 9.6B: QSettings приложения + проектное перекрытие
    #     .pyqtp/settings.json) ---

    def editor_ux_options_app(self) -> dict[str, Any]:
        """UX-настройки редактора в СЛОЕ НАСТРОЕК ПРИЛОЖЕНИЯ (QSettings,
        без проектных перекрытий) — для страницы «Редактор».
        """
        return read_editor_ux_options(self._main_wnd.settings)

    def editor_ux_options(self) -> dict[str, Any]:
        """ЭФФЕКТИВНЫЕ UX-настройки редактора: слой приложения + поключевой
        merge проектного перекрытия (этап 9.6B, для открытий/применений).
        """
        options = read_editor_ux_options(self._main_wnd.settings)
        merge_editor_ux_options(
            options,
            self._main_wnd.project_ctrl.project_settings_section(SECTION_EDITOR),
        )
        return options

    def apply_editor_ux_options(self, options: dict[str, Any]) -> None:
        """Валидирует, сохраняет в слой приложения (QSettings) и применяет
        к открытым редакторам.

        При некорректных значениях бросает ValueError/TypeError (окно
        настроек покажет ошибку, источник не будет тронут). Открытым
        редакторам доводятся ЭФФЕКТИВНЫЕ значения — правка слоя приложения
        не «пробивает» проектное перекрытие (этап 9.6B).
        """
        validate_editor_ux_options(options)  # ValueError/TypeError до записи
        write_editor_ux_options(self._main_wnd.settings, options)
        self._main_wnd.settings.sync()
        self.broadcast_effective_editor_ux_options()

    def broadcast_effective_editor_ux_options(self) -> None:
        """Переприменяет ЭФФЕКТИВНЫЕ UX-настройки всем открытым редакторам
        (смена/снятие проектного перекрытия в рантайме, этап 9.6B).
        """
        options = self.editor_ux_options()
        monaco_payload = to_monaco_options(options)
        monaco_theme = resolve_monaco_theme(
            options, app_is_dark=self._main_wnd.theme_manager.current_theme.is_dark
        )
        for view in self._opened_docs.values():
            self._apply_editor_ux_options(view, monaco_payload, monaco_theme)

    def reset_editor_ux_options(self) -> None:
        """Сбрасывает настройки редактора к значениям по умолчанию."""
        self.apply_editor_ux_options(dict(DEFAULT_EDITOR_UX_OPTIONS))

    def _on_app_theme_changed(self, theme: Theme) -> None:
        """Смена темы приложения -> переприменяем тему Monaco открытым
        документам (нужно для режима «Как в приложении»).
        """
        monaco_theme = resolve_monaco_theme(
            self.editor_ux_options(), app_is_dark=theme.is_dark
        )
        for view in self._opened_docs.values():
            edit_widget = getattr(view, 'edit_widget', None)
            if edit_widget is not None:
                edit_widget.set_theme(monaco_theme)

    def _apply_editor_ux_options(
        self,
        view: BaseDocumentView,
        payload: dict[str, Any] | None = None,
        monaco_theme: str | None = None,
    ) -> None:
        """Доводит UX-настройки Monaco до редактора конкретной вьюхи (этап 7).

        ``payload`` — уже готовые опции Monaco (чтобы не перечитывать QSettings
        на каждую вьюху при массовом применении); по умолчанию — текущие
        сохранённые настройки. ``monaco_theme`` — имя темы Monaco (те же
        соображения при массовом применении). Вьюхи без Monaco (plain text)
        пропускаются.

        Сообщения, посланные до инициализации моста, буферизуются
        ``qtmonaco.Connector`` и уходят в JS сразу после неё, поэтому настройки
        применимы сразу после создания вьюхи.
        """
        edit_widget = getattr(view, 'edit_widget', None)
        if edit_widget is None:
            return
        if payload is None:
            payload = to_monaco_options(self.editor_ux_options())
        edit_widget.update_editor_options(payload)
        if monaco_theme is None:
            monaco_theme = resolve_monaco_theme(
                self.editor_ux_options(),
                app_is_dark=self._main_wnd.theme_manager.current_theme.is_dark,
            )
        edit_widget.set_theme(monaco_theme)

    def _open_document(self):
        # Выбираем файл
        identity = QFileDialog.getOpenFileName(
            parent=self._main_wnd,
            caption='Открыть файл',
            dir=str(self._last_open_dir),
            filter='Все файлы (*.*)',
            options=QFileDialog.Option.DontUseNativeDialog,
        )[0]

        # Открываем файл
        if identity:
            self._last_open_dir = Path(identity).parent
            self.open_document(identity)

    def open_document(self, identity: str | Path) -> BaseDocumentView:
        """Открывает документ (или активирует уже открытый); путь — ключ вьюхи."""
        identity = str(Path(identity))
        doc_view = self._opened_docs.get(identity)
        if doc_view is not None:
            self._note_activated(identity)
            doc_view.ensure_visible()
            return doc_view

        new_view = _document_view_cls_from_identity(identity)(
            self._main_wnd, key=identity
        )
        # заметка активной вкладки (9.6): любой ensure_visible по этой вьюхе
        # (открытие, повторный клик, LSP-переход) обновляет «последнюю
        # активированную» — сигнал instance-level, вьюхи не мешают друг другу
        new_view.ensure_visible_requested.connect(
            lambda _=None, view_=new_view: self._note_activated(view_.key)
        )
        self._apply_editor_ux_options(new_view)
        self._main_wnd.view_central_register(
            new_view, close_cb=self._handle_doc_view_close_requested
        )
        self._opened_docs[identity] = new_view
        new_view.ensure_visible()
        self.document_opened.emit(new_view)
        return new_view

    # --- активная вкладка и снимок сессии (этап 9.6) ---
    #
    # Активная вкладка documents-зоны в python-биндингах KDD (2.x,
    # frontend-reverse) напрямую недоступна: ``isCurrentTabChanged`` при
    # смене вкладки не выстреливает (проверено зондом), ``isCurrentTab()``
    # не экспортируется. Поэтому учёт гибридный:
    #   1) «последний активированный» — обновляем на каждом ``ensure_visible``
    #      (открытие вкладки, клик по дереву/поиску, LSP-переход);
    #   2) при снимке сессии перепроверяем по Qt-фокусу: если фокус внутри
    #      открытой вьюхи — активна она (ловит пользовательские клики по
    #      вкладкам и редакторам);
    #   3) при закрытии активной вкладки активной делаем соседа по порядку
    #      вкладок и реально его активируем (как в браузере).

    def _note_activated(self, view_key: str) -> None:
        """Вьюха стала последней активированной (слот ``ensure_visible_requested``).

        При реальной смене (та же вкладка повторно не «активируется») —
        сигнал ``document_activated`` (этап 10.2: панель «Проблемы»).
        """
        if view_key == self._last_activated_key:
            return
        self._last_activated_key = view_key
        view = self._opened_docs.get(view_key)
        if view is not None:
            self.document_activated.emit(view)

    def _focused_doc_view(self) -> BaseDocumentView | None:
        """Открытая вьюха, внутри которой сейчас фокус главного окна.

        Берём ``focusWidget()`` самого окна, не ``QApplication.focusWidget``:
        Qt хранит фокус по top-level (он переживает переключение приложения,
        работает и на неактивном окне — на этом держатся offscreen-тесты),
        а подъём по цепочке родителей проваливается в webview редактора.
        """
        widget = self._main_wnd.focusWidget()
        if widget is None or not self._opened_docs:
            return None
        views = set(self._opened_docs.values())
        while widget is not None:
            if widget in views:
                return widget  # type: ignore[return-value]
            widget = widget.parentWidget()
        return None

    def _activate_sibling_on_close(
        self, closed_key: str, position: int | None = None
    ) -> None:
        """После закрытия вкладки активируем соседа по порядку (если вкладка была активной).

        ``position`` — позиция закрытой вкладки в порядке открытия (для
        выбора соседа); если не передана — берём 0 (реестра уже нет).
        """
        siblings = [key for key in self._opened_docs if key != closed_key]
        if not siblings:
            self._last_activated_key = None
            return
        neighbour = siblings[min(position or 0, len(siblings) - 1)]
        self._opened_docs[neighbour].ensure_visible()
        self._last_activated_key = neighbour

    def _sibling_position(self, view_key: str) -> int:
        """Позиция вкладки в порядке открытия (снимается до удаления из реестра)."""
        try:
            return list(self._opened_docs).index(view_key)
        except ValueError:
            return 0

    def open_documents(self) -> list[str]:
        """Пути открытых документов в порядке их открытия (сессия)."""
        return list(self._opened_docs)

    def active_document_get(self) -> BaseDocumentView | None:
        """Активная вкладка documents-зоны (None — вкладок нет)."""
        focused = self._focused_doc_view()
        if focused is not None:
            return focused
        return self._opened_docs.get(self._last_activated_key or '')

    def session_snapshot(self) -> SessionData:
        """Снимок сессии: открытые документы + активный из них."""
        docs = self.open_documents()
        focused = self._focused_doc_view()
        active = (
            focused.key
            if focused is not None and focused.key in docs
            else self._last_activated_key
        )
        return SessionData(docs=docs, active=active)

    def open_definition_uri(self, payload: dict) -> None:
        """Этап 6: LSP-навигация «переход к определению» в другой файл.

        Ответ редактора (``QtMonacoEditorWidget.open_uri_requested``):
        ``{'uri': 'file:///...', 'range': {'startLineNumber': ...,
        'startColumn': ...} | None}`` — нумерация Monaco (1-based).
        Открывает (или активирует) документ и ставит каретку на определение.
        """
        uri = str(payload.get('uri') or '')
        parsed = urlparse(uri)
        if parsed.scheme != 'file' or not parsed.path:
            med_log.warning('Пропущен переход по не-файловому uri: %s', uri)
            return
        path = Path(unquote(parsed.path))
        if not path.is_file():
            med_log.warning('Пропущен переход к несуществующему файлу: %s', path)
            return

        view = self.open_document(path)
        line = (payload.get('range') or {}).get('startLineNumber')
        if not isinstance(line, int):
            return
        column = (payload.get('range') or {}).get('startColumn')
        edit_widget = getattr(view, 'edit_widget', None)
        if edit_widget is not None:
            # set_cursor буферизуется коннектором до инициализации моста JS —
            # для только что открытого документа каретка встанет после загрузки
            edit_widget.set_cursor(
                line, column if isinstance(column, int) else 1,
                move_to_position='center',
            )

    # --- применение чужой части WorkspaceEdit (этап 8.2) ---

    def on_workspace_edit_requested(self, payload: dict) -> None:
        """Этап 8.2: правки в файлах вне того, где запущена LSP-операция.

        Ответ редактора (``QtMonacoEditorWidget.workspace_edit_requested``):
        ``{'origin': 'rename'|'codeAction', 'sourceUri': ...,
        'workspaceEdit': {'documentChanges': [...]}}`` — JS-сторона уже вырезала
        локальную часть (её применяет сам Monaco) и нормализовала форму
        ``changes``. Здесь, в порядке массива documentChanges:

        - TextDocumentEdit в открытый документ -> сообщение ``apply_lsp_text_edits``
          его редактору (диск не трогаем — документ dirty, save перепишет;
          didChange серверу уйдёт сам через слушателя модели);
        - TextDocumentEdit в закрытый файл -> прочитать диск, применить
          TextEdit (UTF-16 колонки), записать обратно;
        - CreateFile/RenameFile/DeleteFile (kind 1/2/3) -> файловые операции с
          опциями overwrite/ignoreIfExists/recursive.

        Ограничение v1: ресурсная операция над ОТКРЫТЫМ документом
        (например переименование файла с открытой вкладкой) выполняется на диске,
        вкладки не переименовываются/не закрываются — об этом предупреждаем.
        """
        origin = str(payload.get('origin') or 'LSP')
        changes = normalize_document_changes(payload.get('workspaceEdit'))
        if not changes:
            med_log.warning('Пустой WorkspaceEdit от %s', origin)
            return

        docs_count = 0
        disk_count = 0
        fs_count = 0
        for change in changes:
            if is_text_document_edit(change):
                target = self._apply_foreign_text_edit(change, origin)
                if target == 'doc':
                    docs_count += 1
                elif target == 'disk':
                    disk_count += 1
            elif self._apply_file_operation(change, origin):
                fs_count += 1

        med_log.info(
            'WorkspaceEdit (%s): применено — редакторы: %d, закрытые файлы: %d, '
            'файловые операции: %d', origin, docs_count, disk_count, fs_count,
        )

    def _apply_foreign_text_edit(self, change: dict, origin: str) -> str:
        """TextDocumentEdit чужого файла: 'doc' | 'disk' | '' (пропущено)."""
        uri = str((change.get('textDocument') or {}).get('uri') or '')
        path = path_from_file_uri(uri)
        if path is None:
            med_log.warning(
                'Пропущен workspace edit по не-файловому uri: %s (%s)', uri, origin
            )
            return ''
        view = self._opened_docs.get(str(path))
        edit_widget = getattr(view, 'edit_widget', None) if view else None
        if edit_widget is not None and hasattr(edit_widget, 'apply_lsp_text_edits'):
            # открытый документ — правим его редактор (координаты сервер видит
            # через этот же документ, offset prepended-заголовка вычитает сама
            # JS-сторона; диск не трогаем — документ dirty, save перепишет)
            edit_widget.apply_lsp_text_edits(uri, list(change['edits']))
            return 'doc'
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            med_log.exception(
                'WorkspaceEdit (%s): не удалось прочитать %s', origin, path
            )
            return ''
        new_text = apply_text_edits_to_text(text, list(change['edits']))
        try:
            path.write_text(new_text, encoding='utf-8')
        except OSError:
            med_log.exception(
                'WorkspaceEdit (%s): не удалось записать %s', origin, path
            )
            return ''
        return 'disk'

    def _apply_file_operation(self, change: dict, origin: str) -> bool:
        """Ресурсная операция (CreateFile/RenameFile/DeleteFile) на диске."""
        kind = change.get('kind')
        if kind == CREATE_FILE:
            path = path_from_file_uri(str(change.get('uri') or ''))
            if path is not None:
                return create_file(path, change.get('options'), change.get('contents'))
        elif kind == RENAME_FILE:
            old_path = path_from_file_uri(str(change.get('oldUri') or ''))
            new_path = path_from_file_uri(str(change.get('newUri') or ''))
            if old_path is not None and new_path is not None:
                if (
                    str(old_path) in self._opened_docs
                    or str(new_path) in self._opened_docs
                ):
                    med_log.warning(
                        'WorkspaceEdit (%s): переименование %s -> %s затрагивает '
                        'открытый документ — вкладки не переименовываются (v1)',
                        origin, old_path, new_path,
                    )
                return rename_file(old_path, new_path, change.get('options'))
        elif kind == DELETE_FILE:
            path = path_from_file_uri(str(change.get('uri') or ''))
            if path is not None:
                if str(path) in self._opened_docs:
                    med_log.warning(
                        'WorkspaceEdit (%s): удаляемый файл %s открыт в редакторе '
                        '— вкладка останется без файла (v1)', origin, path,
                    )
                return delete_file(path, change.get('options'))
        else:
            med_log.warning(
                'WorkspaceEdit (%s): неизвестная операция kind=%r', origin, kind
            )
        return False

    def _handle_doc_view_close_requested(
        self, dock: BaseDockWidget, view_key: str
    ):
        view = self._opened_docs.get(view_key)
        if view is None:  # уже закрыт (например, повторный клик по крестику)
            return
        if view.document().is_modified():
            message_box = QMessageBox(parent=self._main_wnd)
            message_box.setText(
                f'Документ "{view_key}" был изменен!\nСохранить изменения?'
            )
            message_box.setInformativeText('Сохранить изменения?')
            message_box.setWindowTitle(self._main_wnd.base_window_title)

            buttons = (
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Cancel
                | QMessageBox.StandardButton.Discard
            )

            message_box.setStandardButtons(buttons)
            message_box.setDefaultButton(QMessageBox.StandardButton.Save)

            message_box.button(QMessageBox.StandardButton.Save).setText(
                'Сохранить'
            )
            message_box.button(QMessageBox.StandardButton.Discard).setText(
                'Не сохранять'
            )
            message_box.button(QMessageBox.StandardButton.Cancel).setText(
                'Отмена'
            )

            result = message_box.exec()

            if result == QMessageBox.StandardButton.Save:
                if not view.document().save():
                    return
            elif result != QMessageBox.StandardButton.Discard:
                return

        # central-доки KDD — «голые» kdd.DockWidget: у них в python-биндингах
        # нет ads-совместимого closeDockWidget, есть только forceClose
        # (закрывает core-состояние дока без повторного QCloseEvent —
        # рекурсии в фильтр закрытия из view_central_register не будет)
        self._force_close_doc_dock(view, dock, view_key)

    def _force_close_doc_dock(
        self, view: BaseDocumentView, dock: BaseDockWidget, view_key: str
    ) -> None:
        """Хвост закрытия документа: forceClose дока + снятие учёта."""
        # getattr, а не прямой вызов: у «голых» kdd.DockWidget метода
        # closeDockWidget нет (ruff B009 / pyright об Protocol)
        close_dock = getattr(dock, 'closeDockWidget', None) or getattr(dock, 'forceClose')  # noqa: B009
        close_dock()
        self.document_closed.emit(view)
        # была активной — позиция нужна ДО удаления из реестра,
        # после удаления сделаем активной соседку по порядку
        was_active = self._last_activated_key == view_key
        position = self._sibling_position(view_key)
        del self._opened_docs[view_key]
        if was_active:
            self._activate_sibling_on_close(view_key, position)

    # --- программные операции с открытыми документами (этап 9.5) ---

    def has_document(self, identity: str | Path) -> bool:
        """Открыт ли документ с таким путём-ключом."""
        return str(Path(identity)) in self._opened_docs

    def document_view(self, identity: str | Path) -> BaseDocumentView | None:
        """Вьюха открытого документа (None — документ не открыт)."""
        return self._opened_docs.get(str(Path(identity)))

    def document_is_modified(self, identity: str | Path) -> bool:
        """Есть ли несохранённые правки (для закрытого — False)."""
        view = self._opened_docs.get(str(Path(identity)))
        return bool(view is not None and view.document().is_modified())

    def documents_under(self, path: str | Path) -> list[Path]:
        """Открытые документы, совпадающие с ``path`` или лежащие внутри."""
        base = Path(path)
        return [
            Path(key)
            for key in self._opened_docs
            if Path(key) == base or base in Path(key).parents
        ]

    def close_document(self, identity: str | Path) -> bool:
        """Закрыть документ без диалогов (этап 9.5, переименование).

        True — закрыт или уже не открыт; False — есть несохранённые правки
        (молча отказываем: диалог сохранения — задача инициатора, как и в
        ``_handle_doc_view_close_requested``).
        """
        view_key = str(Path(identity))
        view = self._opened_docs.get(view_key)
        if view is None:
            return True
        if view.document().is_modified():
            return False
        # central-доки живут в documents_dock у KDD-менеджера (в _docks их
        # нет — view_close на них KeyError); нет атрибута — закрываем только
        # учёт, чтобы операция над диском не встала из-за доковой тонкости
        docks = getattr(self._main_wnd.views_manager, 'documents_dock', None)
        dock = docks.get(view_key) if isinstance(docks, dict) else None
        if dock is None:
            was_active = self._last_activated_key == view_key
            self.document_closed.emit(view)
            del self._opened_docs[view_key]
            if was_active:
                self._last_activated_key = None
            return True
        self._force_close_doc_dock(view, dock, view_key)
        return True
