from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
