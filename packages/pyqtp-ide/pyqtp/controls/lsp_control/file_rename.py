"""``workspace/willRenameFiles``/``didRenameFiles`` (этап 9.5): чистые хелперы.

Спецификация LSP 3.16: перед переименованием файлов клиент шлёт
``workspace/willRenameFiles`` (параметры ``{'files': [{'oldUri','newUri'}]}``),
сервер отвечает ``WorkspaceEdit`` с правками по всему воркспейсу (например,
обновления импортов), клиент применяет правки и сам выполняет переименование
на диске, после чего шлёт нотификацию ``workspace/didRenameFiles``.

Модуль не завязан на GUI и транспорт хаба — покрывается юнит-тестами.
"""

from __future__ import annotations

from typing import Any

from pyqtp.controls.doc_control.workspace_edit import normalize_document_changes

# Спаренная форма объявления серверной возможности: по LSP 3.16 она живёт
# в секции workspace под ключом willRenameFiles (boolean или WorkspaceOptions),
# а basedpyright (и pyright) объявляют её в своей расширенной подсекции
# workspace.fileOperations.willRename — принимаем обе формы.
_CAPABILITY_SPELLINGS: tuple[tuple[str, ...], ...] = (
    ('willRenameFiles',),
    ('fileOperations', 'willRename'),
)


def supports_will_rename(init_result: Any) -> bool:
    """Объявил ли сервер поддержку ``workspace/willRenameFiles``.

    Значение — ``boolean | WorkspaceOptions``, поэтому пустой объект ``{}``
    тоже означает «поддерживает»; ``False``/отсутствие — нет.
    """
    if not isinstance(init_result, dict):
        return False
    caps = init_result.get('capabilities')
    if not isinstance(caps, dict):
        return False
    workspace = caps.get('workspace')
    if not isinstance(workspace, dict):
        return False
    for path in _CAPABILITY_SPELLINGS:
        holder: Any = workspace
        for key in path:
            if not isinstance(holder, dict):
                holder = None
                break
            holder = holder.get(key)
        if holder is not None and holder is not False:
            return True
    return False


def rename_files_params(old_uri: str, new_uri: str) -> dict[str, Any]:
    """Параметры will/didRenameFiles (одинаковой формы, 3.16)."""
    return {'files': [{'oldUri': old_uri, 'newUri': new_uri}]}


def is_empty_rename_result(result: Any) -> bool:
    """Пустой ли ответ сервера (нет ни правок, ни файловых операций).

    Холодный pyright во время первичной индексации отвечает ``{}``/списком
    без правок — это не ошибка, а «ещё не готово» (прогрев повторяет хаб).
    """
    if not result:
        return True
    if not isinstance(result, dict):
        return True
    return not normalize_document_changes(result)


def merge_will_rename_results(results: list[Any]) -> dict[str, Any] | None:
    """Слить ответы нескольких серверов в один WorkspaceEdit.

    Нормализуем в форму ``documentChanges`` (тот же хелпер, что применяет
    этапу 8.2), порядок ответов — за списком серверов. ``None`` — правок
    нет ни у кого.
    """
    changes: list[dict] = []
    for result in results:
        changes.extend(normalize_document_changes(result))
    if not changes:
        return None
    return {'documentChanges': changes}
