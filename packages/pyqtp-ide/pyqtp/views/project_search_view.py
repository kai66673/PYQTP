from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.project_control.project_search import (
    MAX_MATCHES,
    SearchMatch,
    build_rg_args,
    match_from_event,
    parse_rg_event,
    split_complete_lines,
)
from pyqtp.views.base_main_window_view import BaseMainWindowView

if TYPE_CHECKING:
    from pathlib import Path

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# Длина строки совпадения в тексте строки панели (остальное — многоточие).
_PREVIEW_CHARS = 160

# Код возврата rg, означающий ошибку (1 — «совпадений нет», это не ошибка).
_RG_ERROR_EXIT = 2


class ProjectSearchView(BaseMainWindowView):
    """Панель «Поиск» по проекту (этап 9.3): вывод ripgrep в дереве совпадений.

    Источник данных живёт в самой вьюхе (тот же паттерн, что у
    ``ProjectTreeView`` с ``QFileSystemModel``): ``QProcess`` с ``rg --json``,
    вывод разбирается инкрементно. Двойной клик/Enter по строке совпадения
    эмитит ``match_activated`` (``SearchMatch``); группировка — файл -> строки.
    """

    match_activated = Signal(object)  # SearchMatch

    def __init__(
        self,
        main_wnd: PluggableMainWindow,
        /,
        *,
        key: str | None = None,
    ) -> None:
        super().__init__(main_wnd, key=key)
        self.setWindowTitle('Поиск')

        self._root: Path | None = None
        self._proc: QProcess | None = None
        self._search_gen = 0  # отбор «своих» сигналов процесса при перезапуске
        self._buffer = b''
        self._stderr_tail = ''
        self._match_count = 0
        self._file_count = 0
        self._truncated = False
        self._file_headers: dict[str, QTreeWidgetItem] = {}

        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText(
            'Текст или regex — Enter для запуска поиска'
        )
        self._line_edit.setClearButtonEnabled(True)
        self._line_edit.returnPressed.connect(self.start_search)

        self._case_box = QCheckBox('Регистр')
        self._word_box = QCheckBox('Слово целиком')
        self._regex_box = QCheckBox('Regex')
        options = QWidget()
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.addWidget(self._case_box)
        options_layout.addWidget(self._word_box)
        options_layout.addWidget(self._regex_box)
        options_layout.addStretch()
        self._status_label = QLabel('', options)
        options_layout.addWidget(self._status_label)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.itemActivated.connect(self._on_item_activated)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._line_edit)
        layout.addWidget(options)
        layout.addWidget(self._tree)
        self.set_widget(container)

    # --- корень проекта и запуск ---

    def set_project_root(self, path: Path | None) -> None:
        """Задать корень поиска; смена/закрытие корня обрывают поиск."""
        self.stop_search()
        self._root = path
        self._clear_results()
        if path is None:
            self._set_status('Проект не открыт')

    def focus_query(self) -> None:
        """Фокус в поле ввода с выделением текущего запроса."""
        self._line_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._line_edit.selectAll()

    def start_search(self) -> None:
        """Запустить (или перезапустить) поиск по текущему полю и галочкам."""
        if self._root is None:
            self._set_status('Проект не открыт')
            return
        query = self._line_edit.text()
        if not query:
            self._set_status('Пустой запрос')
            return
        self.stop_search()
        self._clear_results()

        proc = QProcess(self)
        proc.setProgram('rg')
        proc.setArguments(
            build_rg_args(
                query,
                case_sensitive=self._case_box.isChecked(),
                whole_word=self._word_box.isChecked(),
                regex_mode=self._regex_box.isChecked(),
            )
        )
        proc.setWorkingDirectory(str(self._root))
        gen = self._search_gen
        proc.readyReadStandardOutput.connect(lambda gen=gen: self._on_stdout(gen))
        proc.readyReadStandardError.connect(lambda gen=gen: self._on_stderr(gen))
        proc.errorOccurred.connect(self._error_handler(proc, gen))
        proc.finished.connect(self._finished_handler(proc, gen))
        self._proc = proc
        self._set_status('Поиск…')
        proc.start()

    def _error_handler(self, proc: QProcess, gen: int):
        """Сигнальный хендлер, привязанный к своему процессу и поколению."""

        def _handler(error: QProcess.ProcessError) -> None:
            self._on_proc_error(error, gen, proc)

        return _handler

    def _finished_handler(self, proc: QProcess, gen: int):
        """Сигнальный хендлер завершения, привязанный к процессу и поколению."""

        def _handler(exit_code: int, _status: QProcess.ExitStatus) -> None:
            self._on_finished(exit_code, gen, proc)

        return _handler

    def stop_search(self) -> None:
        """Снять работающий процесс поиска (его finished отброшен по поколению)."""
        self._search_gen += 1
        proc = self._proc
        self._proc = None
        if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()

    def teardown(self) -> None:
        self.stop_search()
        super().teardown()

    # --- инкрементный разбор вывода ---

    def _on_stdout(self, gen: int) -> None:
        if gen != self._search_gen or self._proc is None:
            return
        self._buffer += bytes(self._proc.readAllStandardOutput().data())
        lines, self._buffer = split_complete_lines(self._buffer)
        for raw in lines:
            event = parse_rg_event(raw)
            if event is None:
                continue
            if event.get('type') == 'match':
                self._add_match(event.get('data'))

    def _add_match(self, data: Any) -> None:
        if self._root is None or not isinstance(data, dict):
            return
        match = match_from_event(data, self._root)
        if match is None:
            return
        if self._match_count >= MAX_MATCHES:
            self._truncated = True
            self.stop_search()
            self._set_status(
                f'Показаны первые {MAX_MATCHES} совпадений — уточните запрос'
            )
            return
        path_key = str(match.path)
        header = self._file_headers.get(path_key)
        if header is None:
            header = QTreeWidgetItem(self._tree, [self._display_path(match.path)])
            header.setExpanded(True)
            self._file_headers[path_key] = header
            self._file_count += 1
        preview = match.text.strip()
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS] + '…'
        child = QTreeWidgetItem(header, [f'{match.line}: {preview}'])
        child.setData(0, Qt.ItemDataRole.UserRole, match)
        self._match_count += 1

    def _display_path(self, path: Path) -> str:
        """Путь совпадения для показа — относительный к корню проекта."""
        if self._root is not None:
            try:
                return str(path.relative_to(self._root))
            except ValueError:
                pass
        return str(path)

    def _on_stderr(self, gen: int) -> None:
        if gen != self._search_gen or self._proc is None:
            return
        text = bytes(self._proc.readAllStandardError().data()).decode(errors='replace')
        self._stderr_tail = (self._stderr_tail + text)[-400:]

    # --- завершение поиска ---

    def _on_proc_error(
        self, error: QProcess.ProcessError, gen: int, proc: QProcess
    ) -> None:
        if gen != self._search_gen or error != QProcess.ProcessError.FailedToStart:
            return
        self._proc = None
        proc.deleteLater()
        self._set_status('Не найден ripgrep (rg) — он не установлен?')

    def _on_finished(self, exit_code: int, gen: int, proc: QProcess) -> None:
        proc.deleteLater()
        if gen != self._search_gen:
            return  # процесс сняли перезапуском/сменой корня/teardown
        self._proc = None
        if self._truncated:
            return  # статус уже выставлен при срезке
        if exit_code >= _RG_ERROR_EXIT:
            reasons = self._stderr_tail.strip().splitlines()
            detail = reasons[-1] if reasons else f'ripgrep завершился с кодом {exit_code}'
            self._set_status(f'Ошибка: {detail}')
            return
        if self._match_count == 0:
            self._set_status('Совпадений нет')
        else:
            self._set_status(
                f'{self._match_count} совпадений в {self._file_count} файлах'
            )

    # --- выбор совпадения ---

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        match = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(match, SearchMatch):
            self.match_activated.emit(match)

    # --- мелочь ---

    def _clear_results(self) -> None:
        self._tree.clear()
        self._file_headers.clear()
        self._buffer = b''
        self._stderr_tail = ''
        self._match_count = 0
        self._file_count = 0
        self._truncated = False

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
