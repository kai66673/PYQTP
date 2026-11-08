from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import QFileSystemModel, QTreeView

from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# Что отсекается из дерева проекта сверх точечных записей
# (имена с точкой в начале фильтр убирает всегда).
_PRUNE_NAMES = frozenset({'__pycache__', 'node_modules'})

# Сколько раз шаг раскрытия может ждать подгрузки каталога (защита от
# «пропал путь» во время reveal).
_REVEAL_RETRIES_LIMIT = 40


class _TreePruneProxy(QSortFilterProxyModel):
    """Прикрывает точечные записи и служебные каталоги проекта.

    Пока проект не открыт (``_enabled = False``), не пропускает вообще
    ничего — иначе ``QFileSystemModel`` без корня показывает всё ФС от ``/``.
    """

    def __init__(self, source: QFileSystemModel, parent: ProjectTreeView) -> None:
        super().__init__(parent)
        self._source = source
        self._enabled = False
        self.setSourceModel(source)

    def set_enabled(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self.invalidateFilter()

    def filterAcceptsRow(  # noqa: N802
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if not self._enabled:
            return False
        name = self._source.fileName(self._source.index(source_row, 0, source_parent))
        if not name or name.startswith('.'):
            return False
        return name not in _PRUNE_NAMES


class ProjectTreeView(BaseMainWindowView):
    """Панель «Проводник»: дерево файлов открытой папки-проекта (этап 9.1).

    Модель — ``QFileSystemModel`` (ленивая подгрузка каталогов через
    ``fetchMore``), поверх — ``_TreePruneProxy`` (точечные записи и
    ``__pycache__``/``node_modules`` из вида скрыты). Двойной клик/Enter по
    файлу эмитит ``file_open_requested`` (путь-строка — тот же ключ, что у
    ``DocumentManager.open_document``).
    """

    file_open_requested = Signal(str)

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Проводник')

        self._root_path: Path | None = None
        # шаги раскрытия reveal_path: сегменты каталогов от корня к цели
        # (QFileSystemModel наполняется асинхронно — идём шагами через таймер)
        self._reveal_dirs: list[str] = []
        self._reveal_name: str | None = None
        self._reveal_retries = 0

        self._model = QFileSystemModel(self)
        self._proxy = _TreePruneProxy(self._model, self)

        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setUniformRowHeights(True)
        self._tree.setHeaderHidden(True)
        self._tree.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        for column in range(1, self._model.columnCount()):
            self._tree.hideColumn(column)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._tree.activated.connect(self._on_activated)
        self.set_widget(self._tree)

    # --- корень проекта ---

    @property
    def root_path(self) -> Path | None:
        return self._root_path

    def set_project_root(self, path: Path | None) -> None:
        """Показать дерево папки ``path`` (``None`` — очистить панель)."""
        self._cancel_reveal()
        if path is None:
            self._root_path = None
            self._proxy.set_enabled(enabled=False)
            self._model.setRootPath('')
            self._tree.setRootIndex(QModelIndex())
            return
        self._root_path = Path(path)
        self._proxy.set_enabled(enabled=True)
        self._model.setRootPath(str(self._root_path))
        source_root = self._model.index(str(self._root_path))
        self._tree.setRootIndex(self._proxy.mapFromSource(source_root))
        # Первый уровень подгружаем сами (пока панель не показана,
        # представление canFetchMore не опрашивает).
        self._model.fetchMore(source_root)

    # --- проводка представления ---

    def _on_activated(self, index: QModelIndex) -> None:
        """Двойной клик/Enter: каталог раскрывает дерево, файл — открываем."""
        if not index.isValid():
            return
        source = self._proxy.mapToSource(index)
        if not source.isValid() or self._model.isDir(source):
            return
        self.file_open_requested.emit(self._model.filePath(source))

    # --- раскрытие к файлу (reveal) ---

    def reveal_path(self, path: str | Path) -> None:
        """Раскрыть предков и выделить файл внутри проекта (иначе — no-op).

        ``QFileSystemModel`` наполняет каталоги асинхронно, поэтому цепочка
        проходится шагами через ``QTimer``: шаг спускается по уже видимым
        строкам прокси, при нехватке строк — ``fetchMore`` текущего каталога
        и повтор шага.
        """
        if self._root_path is None:
            return
        target = Path(path)
        try:
            relative = target.relative_to(self._root_path)
        except ValueError:
            return
        if not relative.parts:
            return
        self._reveal_dirs = list(relative.parts[:-1])
        self._reveal_name = relative.parts[-1]
        self._reveal_retries = 0
        self._expand_next_step()

    def _cancel_reveal(self) -> None:
        self._reveal_dirs = []
        self._reveal_name = None
        self._reveal_retries = 0

    def _expand_next_step(self) -> None:
        """Один шаг раскрытия: от корня вниз по ``_reveal_dirs`` до файла."""
        if self._root_path is None or self._reveal_name is None:
            return
        parent = self._tree.rootIndex()
        for part in self._reveal_dirs:
            row = self._find_row(parent, part)
            if row < 0:
                self._retry_fetch(parent)
                return
            parent = self._proxy.index(row, 0, parent)
            self._tree.setExpanded(parent, True)  # noqa: FBT003
            self._model.fetchMore(self._proxy.mapToSource(parent))
        row = self._find_row(parent, self._reveal_name)
        if row < 0:
            self._retry_fetch(parent)
            return
        target_index = self._proxy.index(row, 0, parent)
        self._tree.setCurrentIndex(target_index)
        self._tree.scrollTo(target_index)
        self._cancel_reveal()

    def _retry_fetch(self, proxy_parent: QModelIndex) -> None:
        """Родительский каталог ещё не наполнен — fetchMore и повтор шага."""
        if self._reveal_retries >= _REVEAL_RETRIES_LIMIT:
            self._cancel_reveal()
            return
        self._reveal_retries += 1
        self._model.fetchMore(self._proxy.mapToSource(proxy_parent))
        QTimer.singleShot(0, self._expand_next_step)

    def _find_row(self, proxy_parent: QModelIndex, name: str) -> int:
        """Строка ``name`` в дочерних строках ``proxy_parent`` (-1 если нет)."""
        for row in range(self._proxy.rowCount(proxy_parent)):
            child = self._proxy.index(row, 0, proxy_parent)
            if str(self._proxy.data(child, Qt.ItemDataRole.DisplayRole)) == name:
                return row
        return -1
