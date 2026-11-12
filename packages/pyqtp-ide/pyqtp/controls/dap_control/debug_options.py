"""Опции отладчика (этап 11, срез D3) — чистый модуль без GUI.

Тот же двухслойный контракт, что у настроек редактора (этап 7/9.6B): слой
приложения хранится в QSettings в группе ``DAP_OPTIONS_SETTINGS_GROUP``
(значения пишутся только когда отличаются от дефолтов), проектное перекрытие —
в ``.pyqtp/settings.json`` в секции ``debug`` (ключ ``break_on_raised``).
Проектное перекрытие СТАРШЕ слоя приложения; правки слоя приложения его не
«пробивают» (общее правило двухслойности 9.6B).

Единственная опция сейчас — ``break_on_raised``: останавливаться ли на ВСЕХ
исключениях (фильтр DAP ``raised``), а не только на необработанных. Остановка
на необработанных (``uncaught``) включена всегда и не настраивается.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyqtp.controls.project_control.project_settings import KEY_BREAK_ON_RAISED

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

# группа QSettings для опций отладки (слой приложения; сам ключ — общий с
# проектной секцией ``debug``, см. project_settings.KEY_BREAK_ON_RAISED)
DAP_OPTIONS_SETTINGS_GROUP = 'DapOptions'

DEFAULT_DEBUG_OPTIONS: dict[str, Any] = {
    KEY_BREAK_ON_RAISED: False,
}


def coerce_break_on_raised(raw: object) -> bool:
    """Приводит сырое значение флага ``break_on_raised`` к bool.

    ``TypeError`` — значение недопустимо (не флаг и не строка-флаг);
    вызывающий решает, чем его заменить (дефолтом или значением нижнего
    слоя). Строковые формы («true»/«1»/«yes»/«on») — как в
    ``editor_options.coerce_editor_ux_value``.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ('true', '1', 'yes', 'on')
    msg = 'Настройка «break_on_raised» должна быть флагом'
    raise TypeError(msg)


def validate_break_on_raised(value: object) -> None:
    """Строгая проверка ЗАПИСЫВАЕМОГО значения (для console-API/страницы).

    Только настоящий ``bool``; бросает ``TypeError`` с текстом.
    """
    if not isinstance(value, bool):
        msg = 'Настройка «break_on_raised» должна быть флагом (True/False)'
        raise TypeError(msg)


def read_debug_options(settings: QSettings) -> dict[str, Any]:
    """Читает слой приложения из QSettings с приведением типов и дефолтами."""
    result: dict[str, Any] = {}
    settings.beginGroup(DAP_OPTIONS_SETTINGS_GROUP)
    try:
        for key, default in DEFAULT_DEBUG_OPTIONS.items():
            raw = settings.value(key, default)
            try:
                result[key] = coerce_break_on_raised(raw)
            except (TypeError, ValueError):
                result[key] = default
    finally:
        settings.endGroup()
    return result


def write_debug_options(
    settings: QSettings, options: dict[str, Any],
) -> None:
    """Сохраняет слой приложения в QSettings (равные дефолтам значения удаляются)."""
    settings.beginGroup(DAP_OPTIONS_SETTINGS_GROUP)
    try:
        for key, default in DEFAULT_DEBUG_OPTIONS.items():
            value = options.get(key, default)
            if value == default:
                settings.remove(key)
            else:
                settings.setValue(key, value)
    finally:
        settings.endGroup()


def merge_debug_options(
    options: dict[str, Any], overlay: dict[str, Any],
) -> None:
    """Поключевой merge проектной секции ``debug`` поверх слоя приложения.

    Как ``merge_editor_ux_options``: мутирует ``options``; неизвестные ключи
    и испорченные значения секции молча игнорируются.
    """
    for key, raw in overlay.items():
        if key not in DEFAULT_DEBUG_OPTIONS:
            continue
        try:
            options[key] = coerce_break_on_raised(raw)
        except (TypeError, ValueError):
            continue


def exception_filters(options: dict[str, Any]) -> list[str]:
    """Набор фильтров ``setExceptionBreakpoints`` по опциям.

    ``uncaught`` (только необработанные) включён ВСЕГДА; ``raised`` (все
    исключения, включая перехваченные) добавляется при ``break_on_raised``.
    Идентификаторы — из capability ``exceptionBreakpointFilters`` debugpy
    (``debugpy/adapter/clients.py``).
    """
    filters = ['uncaught']
    if options.get(KEY_BREAK_ON_RAISED):
        filters.insert(0, 'raised')
    return filters
