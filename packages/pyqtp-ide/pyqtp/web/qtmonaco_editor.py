from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
from qtmonaco.monaco import Monaco

from pyqtp.web.monaco_languages import LANGUAGES_REGISTRY

if TYPE_CHECKING:
    from pathlib import Path

med_log = logging.getLogger('bcad.med')


def monaco_lang_for(path: Path) -> str:
    """Возвращает Monaco language id по расширению файла (fallback: plaintext)."""
    return LANGUAGES_REGISTRY.get(path.suffix.lower(), 'plaintext')


class QtMonacoEditorWidget(Monaco):
    """Виджет редактора Monaco на базе библиотеки qtmonaco.

    Замечания:
    - LSP обеспечивает хаб ``pyqtp.controls.lsp_control.lsp_hub``: редактор
      подключается к нему по адресам из ``lsp_host`` — одиночный адрес
      (``lsp_url``) или стек серверов языка (``lsp_stack``, этап 3.1:
      basedpyright + ruff);
    - ``get_text()`` возвращает синхронную питоновскую зеркальную копию текста,
      которая обновляется сигналом ``text_changed`` из JS-стороны;
    - диагностики приходят через ``publishDiagnostics`` и показываются
      маркерами Monaco (owner по имени сервера).
    """

    modification_changed = Signal(bool)
    open_uri_requested = Signal(dict)  # этап 6: Monaco-навигация на другой ресурс
    # этап 8.2: чужая (по текущей модели) часть LSP WorkspaceEdit
    # (rename/quickfix затронул другие файлы)
    workspace_edit_requested = Signal(dict)
    # этап 9.7: клик по дорожке закладок в gutter — переключение строки ``line``
    bookmark_toggle_requested = Signal(int)
    # этап 11 (D1): клик по дорожке брейкпоинтов — переключение строки ``line``
    breakpoint_toggle_requested = Signal(int)
    # этап 11 (D3): правый клик по глифу брейкпоинта — контекстное меню строки
    breakpoint_context_requested = Signal(int)
    # этап 11 (D3): hover-запрос значения выражения
    # состав payload: requestId, expression, line
    hover_value_requested = Signal(dict)

    def __init__(
        self, file_path: Path, /, *, lsp_host: str | list[dict] | None = None
    ) -> None:
        super().__init__(lsp_host=lsp_host)
        self._file_path = file_path
        try:
            initial_text = file_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            med_log.exception('Не удалось прочитать файл для редактора: %s', file_path)
            initial_text = ''

        self._saved_text = initial_text
        self.text_changed.connect(self._handle_text_changed)

        language = monaco_lang_for(file_path)
        if initial_text:
            self.set_text(
                initial_text,
                language=language,
                uri=file_path.as_uri(),
            )
        else:
            self.set_language(language)

    @property
    def file_path(self) -> Path:
        """Путь к файлу, закреплённому за этим редактором."""
        return self._file_path

    def mark_saved(self) -> None:
        """Фиксирует текущий текст как сохранённый (сброс флага изменённости)."""
        self._saved_text = self.get_text()
        unmodified = False
        self.modification_changed.emit(unmodified)

    def _handle_text_changed(self, value: str) -> None:
        modified = value != self._saved_text
        self.modification_changed.emit(modified)

    def update_editor_options(self, options: dict[str, Any]) -> None:
        """Шлёт набор опций в Monaco (сообщение ``update_editor_options``).

        Сообщения, отправленные до инициализации моста, буферизуются
        ``Connector`` и доставляются сразу после инициализации — поэтому
        настройки можно применять сразу при создании виджета.
        """
        self._connector.send('update_editor_options', options)

    def _open_uri_request(self, value: dict) -> None:
        """Обработчик `_open_uri_request` из JS-стороны (этап 6).

        Monaco вызывает opener при навигации на ресурс вне текущей модели
        (переход к определению/ссылкам в другом файле); сам qtmonaco чужие
        ресурсы не открывает — прокидываем запрос наверх через
        ``open_uri_requested`` (см. ``DocumentManager.open_definition_uri``).
        """
        if not isinstance(value, dict) or not value.get('uri'):
            med_log.warning('Monaco прислал запрос открытия без uri: %r', value)
            return
        self.open_uri_requested.emit(value)

    def _apply_workspace_edit_request(self, value: dict) -> None:
        """Обработчик ``_apply_workspace_edit_request`` из JS-стороны (этап 8.2).

        rename/quickfix затронул файлы вне текущей модели: JS разрезал ответ
        сервера (локальную часть применил сам), чужую часть шлёт сюда в форме
        ``{'origin', 'sourceUri', 'workspaceEdit': {'documentChanges': [...]}}``.
        Сам редактор чужие файлы не правит — прокидываем запрос наверх через
        ``workspace_edit_requested`` (см.
        ``DocumentManager.on_workspace_edit_requested``).
        """
        if not isinstance(value, dict) or not isinstance(
            value.get('workspaceEdit'), dict
        ):
            med_log.warning('Monaco прислал workspace edit без workspaceEdit: %r', value)
            return
        self.workspace_edit_requested.emit(value)

    def set_bookmarks(self, lines: list[int]) -> None:
        """Прислать в Monaco номера строк-закладок этого документа (этап 9.7).

        Состояние (набор закладок проекта) живёт на python-стороне в
        ``.pyqtp/bookmarks.json``; редактору сообщаются только строки его
        файла — JS-сторона перекрашивает дорожку в gutter (своя
        decorations-коллекция, см. ``applyBookmarks`` в main.ts). Сообщение
        буферизуется ``Connector`` до инициализации моста — можно слать сразу
        после создания виджета.
        """
        self._connector.send('set_bookmarks', sorted({int(line) for line in lines}))

    def _toggle_bookmark_request(self, value: dict) -> None:
        """Обработчик ``_toggle_bookmark_request`` из JS-стороны (этап 9.7).

        Клик по дорожке закладок (справа от номеров строк) в gutter Monaco;
        сам редактор состояние не ведёт — прокидываем намерение наверх через
        ``bookmark_toggle_requested`` (см. ``BookmarkControl.toggle_at``).
        """
        line = value.get('line') if isinstance(value, dict) else None
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            med_log.warning('Monaco прислал запрос переключения закладки без строки: %r', value)
            return
        self.bookmark_toggle_requested.emit(line)

    def set_breakpoints(self, lines: list[int | dict[str, Any]]) -> None:
        """Прислать в Monaco брейкпоинты этого документа (этап 11, срез D3).

        Как ``set_bookmarks``: состояние живёт на python-стороне
        (``.pyqtp/breakpoints.json`` + ``DapControl``), редактору сообщаются
        только данные его файла — JS перекрашивает дорожку (своя
        ``breakpointCollection`` в main.ts). Буферизуется ``Connector`` до
        инициализации моста.

        ``lines`` — записи ``{'line': N[, 'condition']}`` (D3) или просто
        строки (обратная совместимость: webengine-тесты D1 и фейки в тестах
        9.x присылают int); на выход всегда уходят нормализованные записи,
        отсортированные по строке, по одной записи на строку.
        """
        by_line: dict[int, dict[str, Any]] = {}
        for item in lines:
            if isinstance(item, dict):
                line = item.get('line')
                condition = item.get('condition')
                if not isinstance(line, int) or isinstance(line, bool):
                    continue
                entry: dict[str, Any] = {'line': line}
                if isinstance(condition, str) and condition:
                    entry['condition'] = condition
                by_line[line] = entry
            elif isinstance(item, int) and not isinstance(item, bool):
                by_line[item] = {'line': item}
        self._connector.send(
            'set_breakpoints', [by_line[line] for line in sorted(by_line)],
        )

    def set_execution_line(self, payload: dict) -> None:
        """Подсветка строки останова (current-line, этап 11).

        ``{'line': N}`` — кадр останова в этом документе, ``{'line': 0}`` —
        снять подсветку. JS-сторона: отдельная ``execLineCollection``
        (глиф-стрелка в gutter + подсветка строки).
        """
        line = payload.get('line') if isinstance(payload, dict) else None
        self._connector.send('set_execution_line', {'line': int(line or 0)})

    def _toggle_breakpoint_request(self, value: dict) -> None:
        """Обработчик ``_toggle_breakpoint_request`` из JS-стороны (этап 11).

        Клик по дорожке брейкпоинтов Monaco (рядом с существующим брейкпоинтом
        или в режиме брейкпоинт-дорожки); сам редактор состояние не ведёт —
        прокидываем намерение наверх (см. ``DapControl.toggle_breakpoint_at``).
        """
        line = value.get('line') if isinstance(value, dict) else None
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            med_log.warning(
                'Monaco прислал запрос переключения брейкпоинта без строки: %r', value,
            )
            return
        self.breakpoint_toggle_requested.emit(line)

    def _breakpoint_context_request(self, value: dict) -> None:
        """Обработчик ``_breakpoint_context_request`` из JS-стороны (D3).

        Правый клик по глифу брейкпоинта в дорожке Monaco (нативное контекстное
        меню Monaco при этом перехвачено и подавлено) — намерение показать
        меню условий/удаления (см. ``DapControl.breakpoint_context_menu``).
        """
        line = value.get('line') if isinstance(value, dict) else None
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            med_log.warning(
                'Monaco прислал запрос контекстного меню брейкпоинта без строки: %r',
                value,
            )
            return
        self.breakpoint_context_requested.emit(line)

    def _hover_value_request(self, value: dict) -> None:
        """Обработчик ``_hover_value_request`` из JS-стороны (D3).

        Свой hover-провайдер Monaco наводит блок значения выражения: запрос
        ``{'requestId', 'expression', 'line'}`` уходит наверх, ответ придёт
        обратно сообщением ``hover_value_reply`` (см.
        ``DapControl._on_hover_requested``).
        """
        if (
            not isinstance(value, dict)
            or not isinstance(value.get('requestId'), str)
            or not isinstance(value.get('expression'), str)
        ):
            med_log.warning('Monaco прислал hover-запрос не по форме: %r', value)
            return
        self.hover_value_requested.emit(dict(value))

    def reply_hover_value(self, payload: dict) -> None:
        """Ответ на hover-запрос: ``{'requestId', 'found', 'text'}`` (D3).

        JS-сторона подменяет содержимое hover-блока, если запрос ещё жив
        (свой 2-сехундный тайм-аут на JS не даёт зависшему ответу вылезти
        late); ``found=False`` — блок значения не показывается.
        """
        self._connector.send('hover_value_reply', dict(payload))

    def apply_lsp_text_edits(self, uri: str, edits: list[dict]) -> None:
        """Применить TextEdit'ы к модели этого редактора (этап 8.2).

        Так DocumentManager доставляет правки в файл, который ОТКРЫТ в другом
        редакторе: JS-сторона проверяет uri модели, правит через
        ``model.applyEdits`` (undo-группа через pushStackElement) и отвечает
        ``_workspace_edit_applied``. Сообщение буферизуется ``Connector`` до
        инициализации моста — можно слать сразу после открытия документа.
        """
        self._connector.send('apply_lsp_text_edits', {'uri': uri, 'edits': edits})

    def _workspace_edit_applied(self, value: dict) -> None:
        """Ответ JS о применении чужих правок (``apply_lsp_text_edits``)."""
        if not isinstance(value, dict):
            return
        if not value.get('ok'):
            med_log.warning(
                'Monaco не применил workspace edit к %s: %s',
                value.get('uri'),
                value.get('error'),
            )
