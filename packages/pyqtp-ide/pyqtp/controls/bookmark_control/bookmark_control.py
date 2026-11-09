from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, QKeySequence

from pyqtp.controls.base_control import BaseControl
from pyqtp.controls.bookmark_control.bookmarks import (
    Bookmark,
    bookmarks_for,
    filter_bookmarks,
    next_bookmark_line,
    read_bookmarks,
    toggle_bookmark,
    write_bookmarks,
)
from pyqtp.utils.action import Action
from pyqtp.utils.priority_menu import PriorityMenu
from pyqtp.views.bookmarks_view import BookmarksView

if TYPE_CHECKING:
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

book_log = logging.getLogger('bcad.book')

# флаг «редактор этой вьюхи уже подключен к переключению закладок» (одно
# подключение на вьюху; живёт на объекте вьюхи, как ``_pyqtp_close_filter`` у доков)
_WIRED_ATTR = '_pyqtp_bookmark_wired'


class BookmarkControl(BaseControl):
    """Закладки проекта (этап 9.7): строки-закладки, панель и переходы.

    Состояние живёт в файле ``<root>/.pyqtp/bookmarks.json`` (см. чистый
    модуль ``bookmarks.py``) — закладки относятся к проекту и переживают
    перезапуск. Рисование — decorations Monaco (``set_bookmarks`` редактору
    каждого открытого документа), переключение — экшены/панель/клик по
    дорожке в gutter; состояние меняет только этот контрол.

    Границы v1: закладки привязаны к строке и не «едут» за текстовыми
    правками; при переименовании файлов (этап 9.5) не обновляются — такие
    записи снимаются фильтром несуществующих файлов при следующем открытии
    проекта.
    """

    _main_wnd: IdeMainWindow

    bookmarks_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        # закладки загруженного проекта, отсортированные (path, line)
        self._bookmarks: list[Bookmark] = []
        self.bookmarks_view: BookmarksView | None = None

        self.action_toggle_bookmark = Action(
            icon=QIcon(),
            title='Поставить/снять закладку',
            parent=self,
            description='Переключить закладку на строке каретки',
            shortcut=QKeySequence('Ctrl+Alt+L'),
        )
        self.action_next_bookmark = Action(
            icon=QIcon(),
            title='Следующая закладка',
            parent=self,
            description='Перейти к следующей закладке этого файла (с циклом)',
            shortcut=QKeySequence('Ctrl+Alt+K'),
        )
        self.action_prev_bookmark = Action(
            icon=QIcon(),
            title='Предыдущая закладка',
            parent=self,
            description='Перейти к предыдущей закладке этого файла (с циклом)',
            shortcut=QKeySequence('Ctrl+Alt+J'),
        )
        self.action_toggle_bookmark.triggered.connect(self.toggle_active)
        self.action_next_bookmark.triggered.connect(
            lambda: self._goto_bookmark(forward=True)
        )
        self.action_prev_bookmark.triggered.connect(
            lambda: self._goto_bookmark(forward=False)
        )
        # экшены живут вместе с проектом (закладки — проектный файл)
        self.action_toggle_bookmark.setEnabled(False)
        self.action_next_bookmark.setEnabled(False)
        self.action_prev_bookmark.setEnabled(False)

    ###############
    # Публичный API #

    def bookmarks_snapshot(self) -> list[dict[str, Any]]:
        """Снимок закладок проекта (для консоли/отладки)."""
        return [asdict(item) for item in self._bookmarks]

    def bookmarks_list(self) -> list[Bookmark]:
        """Копия текущего набора закладок (отсортированный)."""
        return list(self._bookmarks)

    def toggle_at(self, path: str, line: int) -> bool:
        """Переключает закладку в файле проекта; ``False`` — отказ.

        Отказы: проект не открыт, файл вне корня проекта, файла нет, сбой
        записи файла (файл пишется сразу — закладки переживают перезапуск).
        """
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            book_log.info('Закладка: проект не открыт')
            return False
        resolved = str(Path(path))
        if not self._under_root(root, resolved):
            book_log.info('Закладка вне корня проекта, пропущено: %s', resolved)
            return False
        if not Path(resolved).is_file():
            book_log.info('Закладка в несуществующем файле, пропущено: %s', resolved)
            return False
        updated = toggle_bookmark(self._bookmarks, resolved, int(line))
        try:
            write_bookmarks(root, updated)
        except OSError as exc:
            book_log.warning('Не удалось сохранить закладки проекта: %s', exc)
            return False
        self._apply_new_bookmarks(updated)
        return True

    def remove_at(self, path: str, line: int) -> bool:
        """Снимает конкретную закладку (намерение из панели)."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            return False
        entry = Bookmark(path=str(Path(path)), line=int(line))
        updated = [item for item in self._bookmarks if item != entry]
        if len(updated) == len(self._bookmarks):
            return False
        try:
            write_bookmarks(root, updated)
        except OSError as exc:
            book_log.warning('Не удалось сохранить закладки проекта: %s', exc)
            return False
        self._apply_new_bookmarks(updated)
        return True

    def clear_project_bookmarks(self) -> bool:
        """Снимает все закладки проекта (намерение из панели)."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None or not self._bookmarks:
            return False
        try:
            write_bookmarks(root, [])
        except OSError as exc:
            book_log.warning('Не удалось сохранить закладки проекта: %s', exc)
            return False
        self._apply_new_bookmarks([])
        return True

    ####################
    # Жизненный цикл #

    def setup(self) -> None:
        super().setup()
        self._main_wnd.events_ctrl.events_log_register(
            name=book_log.name, group='Проект'
        )
        self._main_wnd.project_ctrl.project_root_changed.connect(self._on_root)
        # API для Python-консоли (этап 12 расширение)
        self._main_wnd.api_ctrl.register_variables(
            {'bookmarks_show': self.bookmarks_snapshot}
        )

    def setup_menu(self) -> None:
        super().setup_menu()

        menubar = self._main_wnd.menuBar()
        try:
            bookmarks_menu = menubar.by_path('Закладки')
        except KeyError:
            bookmarks_menu = menubar.addMenu('Закладки', priority=-7)

        assert isinstance(bookmarks_menu, PriorityMenu)

        bookmarks_menu.addAction(self.action_toggle_bookmark, priority=1)
        bookmarks_menu.addAction(self.action_next_bookmark, priority=2)
        bookmarks_menu.addAction(self.action_prev_bookmark, priority=3)

    def setup_views(self) -> None:
        self.bookmarks_view = view = BookmarksView(
            self._main_wnd, key=f'{self.key}_bookmarks_view'
        )
        self._main_wnd.view_register(view, widget_area='right')
        view.navigate_requested.connect(self._main_wnd.doc_ctrl.open_definition_uri)
        view.remove_requested.connect(self.remove_at)
        view.clear_all_requested.connect(self.clear_project_bookmarks)
        # строки-закладки — свежеоткрытому документу, плюс общий маршрут
        # переключения: клик по дорожке закладок в gutter редактора (сигнал
        # ``bookmark_toggle_requested``) разворачивается в ``toggle_at`` этого файла
        self._main_wnd.doc_ctrl.document_opened.connect(self._wire_doc_view)

        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            view.set_bookmarks(None, [])
            return
        self._bookmarks = self._load_bookmarks(root)
        self._set_actions_enabled(enabled=True)
        view.set_bookmarks(root, self._bookmarks)
        self._broadcast_to_docs()

    def teardown_views(self) -> None:
        super().teardown_views()
        if self.bookmarks_view is not None:
            self._main_wnd.view_deregister(self.bookmarks_view.key)
            self.bookmarks_view = None

    #####################
    # Внутреннее        #

    @staticmethod
    def _under_root(root: Path, path_str: str) -> bool:
        """Лежит ли путь внутри корня проекта (или совпадает с ним)."""
        candidate = Path(path_str)
        return candidate == root or root in candidate.parents

    def _load_bookmarks(self, root: Path) -> list[Bookmark]:
        """Читает ``.pyqtp/bookmarks.json`` и снимает записи мёртвых файлов."""
        try:
            stored = read_bookmarks(root)
        except OSError as exc:
            book_log.warning('Не удалось прочитать закладки проекта: %s', exc)
            return []
        kept, skipped = filter_bookmarks(stored)
        for item in skipped:
            book_log.info(
                'Файл из закладок отсутствует, пропущено: %s (строка %d)',
                item.path, item.line,
            )
        if kept and len(kept) != len(stored):
            # снятые записи сразу переписываем в файл
            try:
                write_bookmarks(root, kept)
            except OSError as exc:
                book_log.warning('Не удалось сохранить закладки проекта: %s', exc)
        if kept:
            book_log.info('Закладки проекта загружены: %d', len(kept))
        return kept

    def _on_root(self, root: Path | None) -> None:
        """Смена/закрытие корня проекта: перечитать снимок, раздать редакторам."""
        if root is None:
            self._bookmarks = []
            self._set_actions_enabled(enabled=False)
            if self.bookmarks_view is not None:
                self.bookmarks_view.set_bookmarks(None, [])
            return
        self._bookmarks = self._load_bookmarks(root)
        self._set_actions_enabled(enabled=True)
        if self.bookmarks_view is not None:
            self.bookmarks_view.set_bookmarks(root, self._bookmarks)
        self._broadcast_to_docs()

    def _set_actions_enabled(self, *, enabled: bool) -> None:
        self.action_toggle_bookmark.setEnabled(enabled)
        self.action_next_bookmark.setEnabled(enabled)
        self.action_prev_bookmark.setEnabled(enabled)

    def _wire_doc_view(self, doc_view: Any) -> None:
        """Свежеоткрытый документ: строки его файла — его редактору.

        Плюс общий маршрут переключения: редактор шлёт ``bookmark_toggle_requested``
        на клик по дорожке закладок в gutter — подключаем его один раз на вьюху
        (флаг на самой вьюхе, чтобы при повторном ``document_opened`` не
        подключать второй раз).
        """
        self._push_lines_to_view(doc_view.key)
        edit_widget = getattr(doc_view, 'edit_widget', None)
        toggle_signal = getattr(edit_widget, 'bookmark_toggle_requested', None)
        connect = getattr(toggle_signal, 'connect', None)
        if connect is None or getattr(doc_view, _WIRED_ATTR, False):
            return
        setattr(doc_view, _WIRED_ATTR, True)
        connect(lambda line, key=doc_view.key: self.toggle_at(key, line))

    def _push_lines_to_view(self, path: str) -> None:
        """Шлёт строки-закладки одного файла его открытому редактору."""
        view = self._main_wnd.doc_ctrl.document_view(path)
        if view is None:
            return
        edit_widget = getattr(view, 'edit_widget', None)
        push = getattr(edit_widget, 'set_bookmarks', None)
        if not callable(push):
            return
        push(bookmarks_for(self._bookmarks, str(Path(path))))

    def _broadcast_to_docs(self) -> None:
        """Раздаёт закладки всем открытым документам текущего проекта."""
        root = self._main_wnd.project_ctrl.project_root
        if root is None:
            return
        for doc_path in self._main_wnd.doc_ctrl.documents_under(root):
            self._push_lines_to_view(str(doc_path))

    def _apply_new_bookmarks(self, updated: list[Bookmark]) -> None:
        """Состояние изменено (toggle/remove/clear): файл уже записан."""
        self._bookmarks = updated
        root = self._main_wnd.project_ctrl.project_root
        if self.bookmarks_view is not None and root is not None:
            self.bookmarks_view.set_bookmarks(root, self._bookmarks)
        # открытые редакторы перерисовываем все разом — набор строк одного файла
        # меняется, а где именно открыт этот файл, знаем не всегда (панель)
        self._broadcast_to_docs()
        self.bookmarks_changed.emit()

    def toggle_active(self) -> None:
        """Переключает закладку на строке каретки активного документа."""
        if self._main_wnd.project_ctrl.project_root is None:
            book_log.info('Закладка: проект не открыт')
            return
        view = self._main_wnd.doc_ctrl.active_document_get()
        if view is None:
            book_log.info('Закладка: нет активного документа')
            return
        edit_widget = getattr(view, 'edit_widget', None)
        cursor = getattr(edit_widget, 'current_cursor', None)
        line = cursor.get('line') if isinstance(cursor, dict) else None
        self.toggle_at(view.key, line if isinstance(line, int) else 1)

    def _goto_bookmark(self, *, forward: bool) -> None:
        """Каретка к следующей/предыдущей закладке активного файла (с циклом)."""
        if self._main_wnd.project_ctrl.project_root is None:
            book_log.info('Переход к закладке: проект не открыт')
            return
        view = self._main_wnd.doc_ctrl.active_document_get()
        if view is None:
            book_log.info('Переход к закладке: нет активного документа')
            return
        lines = bookmarks_for(self._bookmarks, view.key)
        if not lines:
            book_log.info('В активном документе нет закладок')
            return
        edit_widget = getattr(view, 'edit_widget', None)
        cursor = getattr(edit_widget, 'current_cursor', None)
        current = cursor.get('line') if isinstance(cursor, dict) else None
        if not isinstance(current, int) or isinstance(current, bool):
            current = 1
        target = next_bookmark_line(lines, current, forward=forward)
        set_cursor = getattr(edit_widget, 'set_cursor', None)
        if target is None or not callable(set_cursor):
            return
        view.ensure_visible()
        set_cursor(target, 1, move_to_position='center')
        book_log.debug(
            'Переход к закладке %s: %s:%d', 'вперёд' if forward else 'назад',
            view.key, target,
        )
