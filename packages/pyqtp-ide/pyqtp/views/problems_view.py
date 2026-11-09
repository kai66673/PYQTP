from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.problems_control.problems import (
    Problem,
    problem_label,
    summarize_severities,
)
from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# в UserRole child-элементов лежит тройка (путь файла, строка, колонка) —
# из неё собирается payload перехода; у заголовков файлов — None
_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_ENTRY_TRIPLE_LEN = 3


def _entry_of(item: QTreeWidgetItem | None) -> tuple[str, int, int] | None:
    """Тройка (путь, строка, колонка) из элемента дерева (None — не проблема)."""
    if item is None:
        return None
    data = item.data(0, _ENTRY_ROLE)
    if not isinstance(data, tuple) or len(data) != _ENTRY_TRIPLE_LEN:
        return None
    return str(data[0]), int(data[1]), int(data[2])


class ProblemsView(BaseMainWindowView):
    """Панель «Проблемы» (этап 10.1): диагностики всех документов деревом
    «файл -> проблемы».

    Вьюха пассивная (паттерн панелей 9.x): источник — стор диагностик
    ``LspHub``, который наблюдает за всем трафиком ``publishDiagnostics``;
    ``ProblemsControl`` перекладывает сюда очередной снимок через
    ``set_problems``. Двойной клик/Enter по проблеме эмитит
    ``problem_activated`` (payload для ``DocumentManager.open_definition_uri``
    — тот же маршрут, что LSP-переходы и панель закладок).
    """

    problem_activated = Signal(dict)

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Проблемы')

        self._status_label = QLabel('Нет проблем')

        self._tree = QTreeWidget()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.itemActivated.connect(self._on_item_activated)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._status_label)
        layout.addWidget(self._tree)
        self.set_widget(container)

    # --- снимок проблем (пишет контрол при каждом изменении стора) ---

    def set_problems(
        self,
        by_path: Mapping[str, Sequence[Problem]],
        root: Path | None = None,
    ) -> None:
        """Перестроить дерево по снимку «путь -> проблемы».

        ``root`` — корень проекта: пути внутри показываются относительно
        него (как в панели закладок); без проекта — абсолютные.
        """
        self._tree.clear()
        all_problems = [
            problem for problems in by_path.values() for problem in problems
        ]
        for path in sorted(by_path):
            header = QTreeWidgetItem([self._display_path(path, root)])
            header.setToolTip(0, path)
            for problem in sorted(by_path[path]):
                child = QTreeWidgetItem([problem_label(problem)])
                child.setToolTip(
                    0,
                    problem.message
                    + (f' [{problem.owner}]' if problem.owner else ''),
                )
                child.setData(0, _ENTRY_ROLE, (path, problem.line, problem.col))
                header.addChild(child)
            self._tree.addTopLevelItem(header)
            header.setExpanded(True)
        summary = summarize_severities(all_problems)
        if not all_problems:
            self._status_label.setText('Нет проблем')
        else:
            self._status_label.setText(f'Проблем: {len(all_problems)} ({summary})')

    def _display_path(self, path: str, root: Path | None) -> str:
        """Относительный путь внутри проекта (чужой путь/нет корня — как есть)."""
        if root is None:
            return path
        candidate = Path(path)
        try:
            return str(candidate.relative_to(root))
        except ValueError:
            return path

    # --- намерения пользователя ---

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        entry = _entry_of(item)
        if entry is None:
            return
        path, line, col = entry
        self.problem_activated.emit(
            {
                'uri': QUrl.fromLocalFile(path).toString(),
                'range': {'startLineNumber': line, 'startColumn': col},
            }
        )
