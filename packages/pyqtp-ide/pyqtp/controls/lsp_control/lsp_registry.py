"""Реестр LSP-серверов хаба и правила выбора корня рабочей папки.

Реестр настраиваемый (этап 3.2 дорожной карты ``docs/LSP_PLAN.md``): значения
по умолчанию зашиты в ``DEFAULT_LANGUAGES`` и могут быть переопределены
JSON-строкой в QSettings контрола ``LspHub`` (ключ ``<key>/registry_json``);
GUI-редактор стека — этап 3.4.

Формат конфигурации (languageId -> конфиг языка):

.. code-block:: json

    {
      "python": {
        "extensions": [".py", ".pyi"],
        "root_markers": ["pyproject.toml", ".git"],
        "servers": [
          {
            "name": "basedpyright",
            "command": ["basedpyright-langserver", "--stdio"],
            "enabled": true
          },
          {
            "name": "ruff",
            "command": ["ruff", "server"],
            "enabled": true
          }
        ]
      },
      "cpp": {
        "extensions": [".cpp", ".h"],
        "root_markers": ["compile_commands.json", "CMakeLists.txt", ".git"],
        "servers": [
          {"name": "clangd", "command": ["clangd", "--background-index"]}
        ]
      }
    }

Порядок серверов в списке — приоритет: первый используется для старых адресов
хаба без имени сервера и задаёт главного в JS-стороне (signature help,
сниппеты). ``root_markers`` можно не указывать — тогда используются маркеры
по умолчанию (``DEFAULT_ROOT_MARKERS``).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

srv_log = logging.getLogger('bcad.lsp')

# Маркеры корня рабочей папки по умолчанию: ближайший предок файла, содержащий
# любой из них (правило зафиксировано в docs/LSP_PLAN.md; для cpp свои маркеры
# задаёт запись языка в DEFAULT_LANGUAGES).
DEFAULT_ROOT_MARKERS = (
    'pyproject.toml',
    'setup.py',
    'setup.cfg',
    'pyrightconfig.json',
    'basedpyrightconfig.json',
    '.git',
)


@dataclass(frozen=True)
class ServerSpec:
    """Описание запускаемого LSP-сервера."""

    name: str
    command: tuple[str, ...]
    language: str
    enabled: bool = True


@dataclass(frozen=True)
class LanguageConfig:
    """Конфиг языка: расширения, маркеры корня и стек серверов (по приоритету)."""

    language: str
    extensions: tuple[str, ...] = ()
    root_markers: tuple[str, ...] = DEFAULT_ROOT_MARKERS
    servers: tuple[ServerSpec, ...] = ()


PYTHON_SERVER = ServerSpec(
    name='basedpyright',
    command=('basedpyright-langserver', '--stdio'),
    language='python',
)

RUFF_SERVER = ServerSpec(
    name='ruff',
    # `ruff server` по умолчанию общается по stdio (ruff >= 0.6)
    command=('ruff', 'server'),
    language='python',
)

CLANGD_SERVER = ServerSpec(
    name='clangd',
    # clangd говорит по stdio по умолчанию (явный --stdio удалён из clangd 23+);
    # --background-index строит индекс для межфайловых переходов, compdb clangd
    # находит сам (поиск от каталога файла вверх), корень хаба — только точка
    # отсчёта. --compile-commands-dir — опция реестра (см. docs/LSP_PLAN.md,
    # этап 4), в дефолт не входит.
    command=('clangd', '--background-index'),
    language='cpp',
)

# Значения реестра по умолчанию (languageId -> конфиг языка): несколько серверов
# на язык — этап 3.1, конфиг из настроек — этап 3.2, C/C++ (clangd) — этап 4.
DEFAULT_LANGUAGES: dict[str, LanguageConfig] = {
    'python': LanguageConfig(
        language='python',
        extensions=('.py', '.pyi'),
        servers=(PYTHON_SERVER, RUFF_SERVER),
    ),
    'cpp': LanguageConfig(
        language='cpp',
        # единый languageId для всего C/C++ семейства — clangd сам определяет
        # диалект по compdb/расширению, подсветка Monaco 'cpp' покрывает и C
        extensions=('.c', '.h', '.hpp', '.cpp', '.cc', '.cxx', '.hxx'),
        root_markers=(
            'compile_commands.json',
            'compile_flags.txt',
            'CMakeLists.txt',
            '.git',
        ),
        servers=(CLANGD_SERVER,),
    ),
}


@dataclass(frozen=True)
class _ParsedEntry:
    """Результат разбора одного элемента ``servers`` в JSON."""

    spec: ServerSpec
    warnings: tuple[str, ...] = ()


def _parse_server(language: str, raw: Any) -> _ParsedEntry | str:
    """Разбирает запись сервера из JSON; возвращает ``_ParsedEntry`` или строку
    с описанием ошибки.
    """
    if not isinstance(raw, dict):
        return f'запись сервера языка {language!r} не объект JSON'
    name = raw.get('name')
    command = raw.get('command')
    if not isinstance(name, str) or not name.strip():
        return f'сервер языка {language!r}: отсутствует/некорректен "name"'
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return f'сервер {name!r}: "command" должен быть непустым списком строк'
    enabled = raw.get('enabled', True)
    if not isinstance(enabled, bool):
        return f'сервер {name!r}: "enabled" должен быть true/false'
    warnings = tuple(
        f'сервер {name!r}: неизвестное поле {key!r} проигнорировано'
        for key in raw
        if key not in ('name', 'command', 'enabled')
    )
    return _ParsedEntry(
        spec=ServerSpec(
            name=name.strip(),
            command=tuple(command),
            language=language,
            enabled=enabled,
        ),
        warnings=warnings,
    )


class LspRegistry:
    """Набор конфигов языков. Неизменяем после построения; «переключение»
    реестра — это замена объекта целиком (см. ``LspHub.registry_apply_json``).
    """

    def __init__(self, languages: dict[str, LanguageConfig]) -> None:
        self.languages = dict(languages)
        self._ext_lang: dict[str, str] = {
            ext: language
            for language, cfg in self.languages.items()
            for ext in cfg.extensions
        }

    @classmethod
    def default(cls) -> LspRegistry:
        """Реестр со значениями по умолчанию (этот экземпляр никто не чинит —
        LanguageConfig заморожен).
        """
        return cls(DEFAULT_LANGUAGES)

    @classmethod
    def from_dict(cls, data: Any) -> LspRegistry:
        """Строит реестр из разобранных JSON-данных; некорректные записи
        отбрасываются с предупреждением, не-объект верхнего уровня — ``TypeError``.
        """
        if not isinstance(data, dict):
            msg = 'корень конфигурации LSP-реестра должен быть объектом JSON'
            raise TypeError(msg)
        languages: dict[str, LanguageConfig] = {}
        for language, raw in data.items():
            if not isinstance(language, str) or not isinstance(raw, dict):
                srv_log.warning('LSP реестр: конфиг языка %r не объект — пропущен',
                                language)
                continue
            servers: list[ServerSpec] = []
            for item in raw.get('servers', []):
                parsed = _parse_server(language, item)
                if isinstance(parsed, str):
                    srv_log.warning('LSP реестр: %s — запись пропущена', parsed)
                    continue
                for warning in parsed.warnings:
                    srv_log.warning('LSP реестр: %s', warning)
                servers.append(parsed.spec)
            extensions = tuple(
                ext
                for ext in raw.get('extensions', [])
                if isinstance(ext, str) and ext.startswith('.')
            )
            root_markers = raw.get('root_markers')
            if not isinstance(root_markers, list) or not root_markers:
                root_markers_t = DEFAULT_ROOT_MARKERS
            else:
                root_markers_t = tuple(
                    marker
                    for marker in root_markers
                    if isinstance(marker, str) and marker
                )
            if not extensions and language in DEFAULT_LANGUAGES:
                extensions = DEFAULT_LANGUAGES[language].extensions
            languages[language] = LanguageConfig(
                language=language,
                extensions=extensions,
                root_markers=root_markers_t,
                servers=tuple(servers),
            )
        return cls(languages)

    @classmethod
    def from_json(cls, text: str) -> LspRegistry:
        """Строит реестр из JSON-строки конфигурации (``ValueError`` при
        битом JSON).
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f'битый JSON конфигурации LSP-реестра: {exc}'
            raise ValueError(msg) from exc
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Сериализация в JSON-совместимый словарь (тот же формат, что и
        ``from_dict``).
        """
        return {
            language: {
                'extensions': list(cfg.extensions),
                'root_markers': list(cfg.root_markers),
                'servers': [
                    {
                        'name': spec.name,
                        'command': list(spec.command),
                        'enabled': spec.enabled,
                    }
                    for spec in cfg.servers
                ],
            }
            for language, cfg in self.languages.items()
        }

    def to_json(self) -> str:
        """Конфигурация реестра в виде JSON-строки (для QSettings/GUI)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def language_for_uri(self, file_uri: str) -> str | None:
        """Определяет languageId по расширению uri файла (None, если языка нет)."""
        path = urlparse(file_uri).path
        if not path:
            return None
        return self._ext_lang.get(Path(unquote(path)).suffix.lower())

    def servers_for_uri(self, file_uri: str) -> tuple[ServerSpec, ...]:
        """Все включённые серверы для uri файла в порядке приоритета
        (пустой кортеж, если языка нет в реестре).
        """
        language = self.language_for_uri(file_uri)
        if language is None:
            return ()
        cfg = self.languages.get(language)
        if cfg is None:
            return ()
        return tuple(spec for spec in cfg.servers if spec.enabled)

    def root_uri_for(self, file_uri: str) -> str:
        """Корень рабочей папки для файла: ближайший предок с маркером проекта
        языка, иначе — каталог самого файла.
        """
        parsed = urlparse(file_uri)
        if parsed.scheme != 'file' or not parsed.path:
            return ''
        language = self.language_for_uri(file_uri)
        cfg = self.languages.get(language) if language else None
        markers = cfg.root_markers if cfg else DEFAULT_ROOT_MARKERS
        file_dir = Path(unquote(parsed.path)).parent
        for parent in (file_dir, *file_dir.parents):
            if any((parent / marker).exists() for marker in markers):
                return parent.as_uri()
        return file_dir.as_uri()


def resolve_command(command: tuple[str, ...]) -> list[str] | None:
    """Резолвит команду сервера в PATH (venv/bin попадает в PATH при запуске
    через ``uv run``).
    """
    exe = shutil.which(command[0])
    if exe is None:
        srv_log.error('LSP-сервер не найден в PATH: %s', command[0])
        return None
    return [exe, *command[1:]]
