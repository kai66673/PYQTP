"""``workspace/symbol`` (остаток этапа 8, пункт остатка этапа 9): разбор ответа.

Модуль не завязан на GUI и на транспорт хаба — ``parse_workspace_symbols``
кормится «сырым» ``result`` ответа сервера и покрывается юнит-тестами без
LSP-процессов.

Спецификация LSP допускает две формы элементов списка:

- ``SymbolInformation`` (3.16 и serverless-ответы 3.17): ``location =
  {uri, range}`` + необязательная ``containerName``;
- ``WorkspaceSymbol`` (3.17): ``location = {uri[, range]}``, устаревшие
  ``range``/``selectionRange`` на верхнем уровне элемента.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Числовые коды SymbolKind из LSP 3.17 — тексты для подписей списка
# (числа 1..26; JS-сторона Monaco использует свой enum, здесь только текст).
SYMBOL_KIND_NAMES: dict[int, str] = {
    1: 'файл', 2: 'модуль', 3: 'пространство имён', 4: 'пакет', 5: 'класс',
    6: 'метод', 7: 'свойство', 8: 'поле', 9: 'конструктор', 10: 'перечисление',
    11: 'интерфейс', 12: 'функция', 13: 'переменная', 14: 'постоянная',
    15: 'строка', 16: 'число', 17: 'булево', 18: 'массив', 19: 'объект',
    20: 'ключ', 21: 'null', 22: 'элемент перечисления', 23: 'структура',
    24: 'событие', 25: 'оператор', 26: 'параметр типа',
}


@dataclass(frozen=True)
class WorkspaceSymbol:
    """Один символ из ответа ``workspace/symbol``.

    ``line``/``column`` — уже в координатах Monaco (1-based), как у
    ``DocumentManager.open_definition_uri``; LSP-ные 0-based смещения
    конвертирует ``parse_workspace_symbols``.
    """

    name: str
    kind: int
    container: str
    file_uri: str
    line: int
    column: int


def parse_workspace_symbols(result: Any) -> list[WorkspaceSymbol]:
    """Разбирает ``result`` ответа ``workspace/symbol`` в список символов.

    Обе допустимые формы (``SymbolInformation`` и ``WorkspaceSymbol``), а
    также устаревшие ``range``/``selectionRange`` на верхнем уровне;
    битые/неполные элементы молча пропускаются, ``None``-ответ -> пустой
    список.
    """
    if not isinstance(result, list):
        return []
    symbols: list[WorkspaceSymbol] = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        location = entry.get('location')
        if not isinstance(name, str) or not isinstance(location, dict):
            continue
        file_uri = location.get('uri')
        if not isinstance(file_uri, str) or not file_uri:
            continue
        line, column = _range_start(entry, location)
        kind = entry.get('kind')
        container = entry.get('containerName')
        symbols.append(
            WorkspaceSymbol(
                name=name,
                kind=kind if isinstance(kind, int) else 0,
                container=container if isinstance(container, str) else '',
                file_uri=file_uri,
                line=line,
                column=column,
            )
        )
    return symbols


def _range_start(entry: dict, location: dict) -> tuple[int, int]:
    """Начало местоположения символа в Monaco-координатах (1-based).

    ``location.range`` приоритетнее устаревших верхнеуровневых
    ``range``/``selectionRange`` (3.17); без валидного range — (1, 1),
    переход всё равно возможен в начало файла.
    """
    for holder in (location.get('range'), entry.get('range'),
                   entry.get('selectionRange')):
        if not isinstance(holder, dict):
            continue
        start = holder.get('start')
        if not isinstance(start, dict):
            continue
        line, character = start.get('line'), start.get('character')
        if isinstance(line, int) and isinstance(character, int):
            return line + 1, character + 1
    return 1, 1


def spec_supports_workspace_symbol(init_result: Any) -> bool:
    """Объявил ли сервер ``workspaceSymbolProvider`` в InitializeResult.

    Поле — ``boolean | WorkspaceSymbolOptions``, поэтому пустой объект
    ``{}`` тоже означает «поддерживает»; ``False``/отсутствие — нет.
    """
    if not isinstance(init_result, dict):
        return False
    caps = init_result.get('capabilities')
    if not isinstance(caps, dict):
        return False
    provider = caps.get('workspaceSymbolProvider')
    return provider is not None and provider is not False


def open_payload(symbol: WorkspaceSymbol) -> dict[str, Any]:
    """Payload для ``DocumentManager.open_definition_uri`` (Monaco-координаты)."""
    return {
        'uri': symbol.file_uri,
        'range': {
            'startLineNumber': symbol.line,
            'startColumn': symbol.column,
        },
    }
