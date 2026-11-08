from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

# UX-настройки редактора (этап 7, docs/IDE_ROADMAP.md): общий контракт между
# страницей настроек «Редактор», менеджером документов и виджетом редактора.
# Хранятся в QSettings в группе ``EDITOR_OPTIONS_SETTINGS_GROUP`` (значения
# пишутся только когда отличаются от значений по умолчанию).

EDITOR_OPTIONS_SETTINGS_GROUP = 'EditorOptions'

# Тема редактора Monaco: 'auto' — следовать теме приложения (по is_dark),
# 'light'/'dark' — явный выбор; отображается во встроенные темы Monaco
# (своих тем через defineTheme пока нет, высоко-контрастные в UI не открываем)
EDITOR_THEME_AUTO = 'auto'
EDITOR_THEME_LIGHT = 'light'
EDITOR_THEME_DARK = 'dark'
EDITOR_THEME_CHOICES = (EDITOR_THEME_AUTO, EDITOR_THEME_LIGHT, EDITOR_THEME_DARK)

MONACO_THEME_LIGHT = 'vs'
MONACO_THEME_DARK = 'vs-dark'

# Логические значения по умолчанию (Monaco-дефолты, кроме scrollBeyondLastLine
# и multiCursorModifier — они зафиксированы в defaultEditorOptions на JS-стороне)
DEFAULT_EDITOR_UX_OPTIONS: dict[str, Any] = {
    'minimap': True,
    'folding': True,
    'mouse_wheel_zoom': False,
    'font_size': 14,
    'theme': EDITOR_THEME_AUTO,
}

FONT_SIZE_RANGE = (8, 72)


def validate_editor_ux_options(options: dict[str, Any]) -> None:
    """Валидирует набор настроек; бросает ValueError/TypeError с текстом."""
    for key, default in DEFAULT_EDITOR_UX_OPTIONS.items():
        if key not in options:
            msg = f'Настройка редактора не задана: {key}'
            raise ValueError(msg)
        value = options[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                msg = f'Настройка «{key}» должна быть флагом'
                raise TypeError(msg)
        elif key == 'font_size':
            if not isinstance(value, int) or isinstance(value, bool):
                msg = 'Размер шрифта должен быть целым числом'
                raise TypeError(msg)
            if not FONT_SIZE_RANGE[0] <= value <= FONT_SIZE_RANGE[1]:
                msg = (
                    'Размер шрифта должен быть в диапазоне '
                    f'{FONT_SIZE_RANGE[0]}..{FONT_SIZE_RANGE[1]}'
                )
                raise ValueError(msg)
        elif key == 'theme':
            if not isinstance(value, str):
                msg = 'Тема редактора должна быть строкой'
                raise TypeError(msg)
            if value not in EDITOR_THEME_CHOICES:
                msg = (
                    'Тема редактора должна быть одной из: '
                    + ', '.join(EDITOR_THEME_CHOICES)
                )
                raise ValueError(msg)


def resolve_monaco_theme(
    options: dict[str, Any], *, app_is_dark: bool
) -> str:
    """Логическую настройку ``theme`` переводит во имя темы Monaco.

    ``app_is_dark`` — признак тёмной темы приложения (``Theme.is_dark``);
    нужен только для режима ``auto``. Чистая функция (без Qt) — тестируется
    отдельно от GUI.
    """
    theme = options.get('theme', EDITOR_THEME_AUTO)
    if theme == EDITOR_THEME_LIGHT:
        return MONACO_THEME_LIGHT
    if theme == EDITOR_THEME_DARK:
        return MONACO_THEME_DARK
    return MONACO_THEME_DARK if app_is_dark else MONACO_THEME_LIGHT


def to_monaco_options(options: dict[str, Any]) -> dict[str, Any]:
    """Преобразует логические настройки в payload сообщения update_editor_options.

    ``theme`` сюда не входит: тема Monaco применяется отдельным сообщением
    ``theme`` (``Monaco.set_theme``), она зависит ещё и от темы приложения.
    """
    return {
        'minimap': {'enabled': bool(options['minimap'])},
        'folding': bool(options['folding']),
        'mouseWheelZoom': bool(options['mouse_wheel_zoom']),
        'fontSize': int(options['font_size']),
    }


def read_editor_ux_options(settings: QSettings) -> dict[str, Any]:
    """Читает настройки из QSettings с приведением типов и дефолтами."""
    result: dict[str, Any] = {}
    settings.beginGroup(EDITOR_OPTIONS_SETTINGS_GROUP)
    try:
        for key, default in DEFAULT_EDITOR_UX_OPTIONS.items():
            raw = settings.value(key, default)
            if isinstance(default, bool):
                if isinstance(raw, str):
                    result[key] = raw.strip().lower() in ('true', '1', 'yes', 'on')
                else:
                    result[key] = bool(raw)
            elif key == 'theme':
                # строковое значение из списка допустимых, иначе — дефолт
                result[key] = (
                    raw if isinstance(raw, str) and raw in EDITOR_THEME_CHOICES
                    else default
                )
            else:
                if isinstance(raw, (int, float, str, bytes)):
                    try:
                        result[key] = int(raw)
                    except (TypeError, ValueError):
                        result[key] = default
                else:
                    result[key] = default
                if not FONT_SIZE_RANGE[0] <= result[key] <= FONT_SIZE_RANGE[1]:
                    result[key] = default
    finally:
        settings.endGroup()
    return result


def write_editor_ux_options(
    settings: QSettings, options: dict[str, Any]
) -> None:
    """Сохраняет настройки в QSettings (значения, равные дефолтам, удаляются)."""
    validate_editor_ux_options(options)
    settings.beginGroup(EDITOR_OPTIONS_SETTINGS_GROUP)
    try:
        for key, default in DEFAULT_EDITOR_UX_OPTIONS.items():
            value = options[key]
            if value == default:
                settings.remove(key)
            else:
                settings.setValue(key, value)
    finally:
        settings.endGroup()
