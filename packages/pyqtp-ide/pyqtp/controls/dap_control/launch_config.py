"""Конфигурации запуска отладки (этап 11, срез D1) — чистый модуль без Qt.

Файл проекта ``<root>/.pyqtp/launch.json`` — в той же папке, что сессия
(``project_session.py``), закладки (``bookmarks.py``) и настройки проекта
(``project_settings.py``). Схема VS Code:

.. code-block:: json

   {
     "version": "0.2.0",
     "configurations": [
       {
         "name": "Запуск проекта",
         "type": "python",
         "request": "launch",
         "program": "main.py",
         "args": ["--verbose"],
         "cwd": ".",
         "env": {"PYTHONUNBUFFERED": "1"},
         "stopOnEntry": false,
         "justMyCode": true
       }
     ]
   }

Разбор толерантный (тот же подход, что у остальных файлов ``.pyqtp/``): битый
JSON/не-словарь верхнего уровня → ``None`` («файла как бы нет»), мусорные
записи вычищаются, у записи без ``name``/``program`` имя подставляется, а сама
запись отбрасывается. ``request=attach`` и чужие ``type`` вычищаются —
_attach в v1 не поддерживаем_. Макрос ``${workspaceFolder}`` раскрывает
``resolve_config``; относительные ``program``/``cwd`` считаются от корня
проекта.

Конфигурации по умолчанию (когда файла нет или в нём пусто): программа —
путь активного документа, cwd — корень проекта (``default_config``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# каталог и имя файла конфигураций (рядом с session.json/bookmarks.json)
PROJECT_DIR_NAME = '.pyqtp'
LAUNCH_FILE_NAME = 'launch.json'
WORKSPACE_FOLDER_MACRO = '${workspaceFolder}'

DEFAULT_CONFIG_NAME = 'Текущий файл'
DEFAULT_TYPE = 'python'


@dataclass(frozen=True)
class LaunchConfig:
    """Одна конфигурация запуска (program/cwd — как записаны в файле)."""

    name: str
    program: str
    args: tuple[str, ...] = ()
    cwd: str = ''
    env: tuple[tuple[str, str], ...] = ()
    stop_on_entry: bool = False
    just_my_code: bool = True


def launch_file_path(root: str | Path) -> Path:
    """Путь ``<root>/.pyqtp/launch.json``."""
    return Path(root) / PROJECT_DIR_NAME / LAUNCH_FILE_NAME


def read_launch_file(root: str | Path) -> list[LaunchConfig] | None:
    """Конфигурации из файла проекта; ``None`` — файла нет/не читается/битый."""
    text: str | None
    try:
        text = launch_file_path(root).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return None
    return parse_launch_json(text)


def parse_launch_json(text: str) -> list[LaunchConfig] | None:
    """Разбор JSON-текста launch.json; ``None`` — битый JSON/не-словарь."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    entries = raw.get('configurations')
    if not isinstance(entries, list):
        return []
    configs: list[LaunchConfig] = []
    for entry in entries:
        config = config_from_dict(entry)
        if config is not None:
            configs.append(config)
    return configs


def config_from_dict(entry: Any) -> LaunchConfig | None:
    """Одна запись ``configurations[]``; ``None`` — запись отбрасываем."""
    if not isinstance(entry, dict):
        return None
    if str(entry.get('request') or 'launch') != 'launch':
        return None
    if str(entry.get('type') or DEFAULT_TYPE) != DEFAULT_TYPE:
        return None
    program = entry.get('program')
    if not isinstance(program, str) or not program.strip():
        return None
    raw_args = entry.get('args')
    args = tuple(
        str(item) for item in raw_args if isinstance(item, (str, int, float))
    ) if isinstance(raw_args, list) else ()
    env = _normalize_env(entry.get('env'))
    cwd = entry.get('cwd')
    name = entry.get('name')
    return LaunchConfig(
        name=name if isinstance(name, str) and name.strip() else 'Конфигурация',
        program=program.strip(),
        args=args,
        cwd=cwd if isinstance(cwd, str) else '',
        env=env,
        stop_on_entry=bool(entry.get('stopOnEntry')),
        just_my_code=bool(entry.get('justMyCode', True)),
    )


def _normalize_env(raw: Any) -> tuple[tuple[str, str], ...]:
    """``env``-словарь в канонические пары; не-строки/None вычищаются."""
    if not isinstance(raw, dict):
        return ()
    pairs: list[tuple[str, str]] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if value is None:
            continue
        pairs.append((key, str(value)))
    return tuple(pairs)


def substitute(value: str, root: str | Path) -> str:
    """Раскрыть ``${workspaceFolder}`` (прочие макросы v1 не трогаем)."""
    return value.replace(WORKSPACE_FOLDER_MACRO, str(Path(root)))


def default_config(root: str | Path, active_document: str | Path | None) -> LaunchConfig | None:
    """Схема по умолчанию: программа — активный документ, cwd — корень проекта."""
    if active_document is None:
        return None
    path = Path(active_document)
    if not str(path):
        return None
    return LaunchConfig(
        name=DEFAULT_CONFIG_NAME,
        program=str(path),
        cwd=str(Path(root)),
    )


def resolve_config(config: LaunchConfig, root: str | Path) -> LaunchConfig:
    """Макросы + путь ``program``/``cwd`` в абсолютные (относительные — от корня).

    Существование ``program`` здесь не проверяется — это делает контрол при
    запуске (чтобы сообщение об ошибке было понятным).
    """
    root_path = Path(root)

    def _abs(value: str) -> str:
        expanded = Path(substitute(value, root_path).replace('\\', '/')).expanduser()
        candidate = expanded
        if not candidate.is_absolute():
            candidate = root_path / candidate
        return str(Path(os.path.normpath(candidate)))

    return LaunchConfig(
        name=config.name,
        program=_abs(config.program),
        args=tuple(substitute(item, root_path) for item in config.args),
        cwd=_abs(config.cwd) if config.cwd.strip() else str(root_path),
        env=tuple((key, substitute(value, root_path)) for key, value in config.env),
        stop_on_entry=config.stop_on_entry,
        just_my_code=config.just_my_code,
    )


def launch_arguments(config: LaunchConfig) -> dict[str, Any]:
    """Аргументы DAP-запроса ``launch`` для debugpy (console=internalConsole).

        ``integratedTerminal`` требует клиентской капбилити
    ``supportsRunInTerminalRequest`` — её у нас нет, debuggee печатает в
    ``output``-события (``category='stdout'``). ``env`` подаётся списком пар
    (форма debugpy), ``stopOnEntry``/``justMyCode`` — только когда отличаются
    от дефолтов адаптера.
    """
    args: dict[str, Any] = {
        'type': DEFAULT_TYPE,
        'request': 'launch',
        'name': config.name,
        'program': config.program,
        'console': 'internalConsole',
    }
    if config.cwd:
        args['cwd'] = config.cwd
    if config.args:
        args['args'] = list(config.args)
    if config.env:
        args['env'] = [{'key': key, 'value': value} for key, value in config.env]
    if config.stop_on_entry:
        args['stopOnEntry'] = True
    if not config.just_my_code:
        args['justMyCode'] = False
    return args
