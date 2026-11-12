"""Настройки проекта (этап 9.6B): слой перекрытий поверх настроек приложения.

Чистый модуль (без GUI и без Qt): файл ``<root>/.pyqtp/settings.json`` лежит
рядом с файлом сессии и содержит ТОЛЬКО перекрываемые проектом значения —
настройки приложения остаются значениями по умолчанию (и для режима «без
проекта», и для впервые открытой папки без каталога ``.pyqtp/``).

Формат файла (JSON, разбор толерантный):

.. code-block:: json

   {
     "appearance": {"theme_name": "Темная"},
     "editor": {"font_size": 16, "minimap": false},
     "lsp": {"registry_json": "{...JSON-строка реестра серверов...}"},
     "debug": {"break_on_raised": true}
   }

Секции — известные (``appearance``/``editor``/``lsp``/``debug``), внутри —
отдельные ключи: перекрытие работает поштучно, остальное берётся из
настроек приложения. Неизвестные секции сохраняются при правках и
игнорируются при применении; битый JSON воспринимается как пустой слой.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from pyqtp.controls.project_control.project_session import PROJECT_DIR_NAME

# файл проектных настроек внутри общего каталога .pyqtp/ (см. project_session)
PROJECT_SETTINGS_FILE_NAME = 'settings.json'

# известные секции перекрытий (расширение — новые секции без смены формата)
SECTION_APPEARANCE = 'appearance'
SECTION_EDITOR = 'editor'
SECTION_LSP = 'lsp'
SECTION_DEBUG = 'debug'  # этап 11 D3: опции отладчика (DapControl/debug_options)
KNOWN_SECTIONS = (SECTION_APPEARANCE, SECTION_EDITOR, SECTION_LSP, SECTION_DEBUG)

# ключи секции appearance
KEY_THEME_NAME = 'theme_name'
# ключи секции lsp
KEY_REGISTRY_JSON = 'registry_json'
# ключи секции debug (этап 11 D3; тот же ключ и в QSettings-слое, см. dap_control.debug_options)
KEY_BREAK_ON_RAISED = 'break_on_raised'


def settings_file(root: str | Path) -> Path:
    """Путь файла настроек проекта для корня проекта."""
    return Path(root) / PROJECT_DIR_NAME / PROJECT_SETTINGS_FILE_NAME


def parse_overlay(raw: object) -> dict[str, dict[str, Any]]:
    """Толерантный разбор decoded-JSON в оверлей ``{секция: {ключ: значение}}``.

    - не-словарь верхнего уровня -> пустой оверлей;
    - не-словарь в значении секции -> секция выбрасывается;
    - пустые секции выбрасываются (файл не должен хранить мусор).
    """
    if not isinstance(raw, dict):
        return {}
    overlay: dict[str, dict[str, Any]] = {}
    for section, values in raw.items():
        if not isinstance(section, str) or not isinstance(values, dict):
            continue
        if values:
            overlay[section] = dict(values)
    return overlay


def deserialize_overlay(text: str) -> dict[str, dict[str, Any]]:
    """Разбор JSON-текста; битый JSON -> пустой оверлей."""
    try:
        raw = json.loads(text)
    except ValueError:
        return {}
    return parse_overlay(raw)


def serialize_overlay(overlay: dict[str, dict[str, Any]]) -> str:
    """JSON-текст оверлея (пустые секции не пишутся)."""
    pruned = {
        section: dict(values)
        for section, values in overlay.items()
        if isinstance(values, dict) and values
    }
    return json.dumps(pruned, ensure_ascii=False, indent=2) + '\n'


def read_project_settings(root: str | Path) -> dict[str, dict[str, Any]]:
    """Читает оверлей из ``<root>/.pyqtp/settings.json`` (нет/битый — пустой)."""
    path = settings_file(root)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    except OSError as exc:
        message = f'Не удалось прочитать настройки проекта: {exc}'
        raise OSError(message) from exc
    return deserialize_overlay(text)


def write_project_settings(
    root: str | Path, overlay: dict[str, dict[str, Any]]
) -> None:
    """Пишет оверлей в ``<root>/.pyqtp/settings.json`` (атомарно, через tmp).

    Пустой оверлей удаляет файл (и каталог ``.pyqtp/``, если он стал пуст):
    без проектных перекрытий рядом с проектом не должно оставаться мусора;
    чужие файлы каталога (session.json) при этом не трогаются.
    """
    path = settings_file(root)
    pruned = {
        section: dict(values)
        for section, values in overlay.items()
        if isinstance(values, dict) and values
    }
    if not pruned:
        with suppress(FileNotFoundError):
            path.unlink()
        with suppress(OSError):
            # каталог не пуст или отсутствует — так и задумано
            path.parent.rmdir()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f'.{PROJECT_SETTINGS_FILE_NAME}.',
        suffix='.tmp',
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(serialize_overlay(pruned))
        tmp_path.replace(path)
    except OSError:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def clear_project_settings(root: str | Path) -> None:
    """Убирает файл настроек проекта (и каталог ``.pyqtp/``, если он пуст)."""
    with suppress(FileNotFoundError):
        settings_file(root).unlink()
    with suppress(OSError):
        settings_file(root).parent.rmdir()


def overlay_set(
    overlay: dict[str, dict[str, Any]], section: str, key: str, value: Any
) -> dict[str, dict[str, Any]]:
    """Копия оверлея с установленным/снятым (``value is None``) значением.

    Пустые секции в результате выбрасываются; исходный оверлей не меняется.
    """
    result = {
        name: dict(values) for name, values in overlay.items()
        if isinstance(values, dict)
    }
    if value is None:
        values = result.get(section)
        if values is not None:
            values.pop(key, None)
            if not values:
                del result[section]
    else:
        result.setdefault(section, {})[key] = value
    return result
