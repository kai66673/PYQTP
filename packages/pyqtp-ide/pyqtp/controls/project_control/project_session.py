"""Проектная сессия (этап 9.6): открытые вкладки + активный документ.

Чистый модуль (без GUI и без Qt): сериализация/разбор сессии и размещение
её файла в каталоге проекта ``.pyqtp/session.json``. Настройки проекта при
этом отделяются от настроек приложения — сессия относится к проекту и
живёт рядом с проектом, а не в QSettings.

Формат файла (JSON, разбор толерантный):

.. code-block:: json

   {"docs": ["/abs/path/a.py", "/abs/path/b.py"], "active": "/abs/path/a.py"}

Список ``docs`` — порядок открытия вкладок; ``active`` — путь активной
вкладки (``None`` — не задан). Лишние поля файла игнорируются, мусорные
записи пропускаются, нечитаемый файл воспринимается как пустая сессия.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

# каталог проектных данных (сессия, проектные настройки) — этап 9.6
PROJECT_DIR_NAME = '.pyqtp'
SESSION_FILE_NAME = 'session.json'


@dataclass
class SessionData:
    """Снимок сессии проекта: открытые документы и активный из них."""

    docs: list[str] = field(default_factory=list)
    active: str | None = None


def session_file(root: str | Path) -> Path:
    """Путь файла сессии для корня проекта."""
    return Path(root) / PROJECT_DIR_NAME / SESSION_FILE_NAME


def serialize_session(data: SessionData) -> str:
    """JSON-текст сессии (active вне списка вкладок не пишется)."""
    active = data.active if data.active in data.docs else None
    return json.dumps(
        {'docs': list(data.docs), 'active': active},
        ensure_ascii=False,
        indent=2,
    ) + '\n'


def parse_session(raw: object) -> SessionData:
    """Толерантный разбор decoded-JSON: пропускаем мусор, а не падаем.

    - не-словарь -> пустая сессия;
    - ``docs`` — только непустые строки, дедупликация (первое вхождение);
    - ``active`` — строка из ``docs``, иначе ``None``.
    """
    if not isinstance(raw, dict):
        return SessionData()
    raw_docs = raw.get('docs')
    docs: list[str] = []
    if isinstance(raw_docs, list):
        for item in raw_docs:
            if isinstance(item, str) and item and item not in docs:
                docs.append(item)
    active = raw.get('active')
    if not (isinstance(active, str) and active in docs):
        active = None
    return SessionData(docs=docs, active=active)


def deserialize_session(text: str) -> SessionData:
    """Разбор JSON-текста; битый JSON -> пустая сессия."""
    try:
        raw = json.loads(text)
    except ValueError:
        return SessionData()
    return parse_session(raw)


def read_session(root: str | Path) -> SessionData:
    """Читает сессию из ``<root>/.pyqtp/session.json`` (нет/битый — пустая)."""
    path = session_file(root)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return SessionData()
    except OSError as exc:
        message = f'Не удалось прочитать сессию проекта: {exc}'
        raise OSError(message) from exc
    return deserialize_session(text)


def write_session(root: str | Path, data: SessionData) -> None:
    """Пишет сессию в ``<root>/.pyqtp/session.json`` (атомарно, через tmp).

    Пустой список вкладок очищает каталог ``.pyqtp/`` целиком — при
    закрытии проекта без открытых документов рядом с проектом мусора
    оставаться не должно.
    """
    if not data.docs:
        clear_session(root)
        return
    path = session_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f'.{SESSION_FILE_NAME}.', suffix='.tmp'
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(serialize_session(data))
        tmp_path.replace(path)
    except OSError:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def clear_session(root: str | Path) -> None:
    """Убирает файл сессии (и каталог ``.pyqtp/``, если он стал пуст)."""
    with suppress(FileNotFoundError):
        session_file(root).unlink()
    project_dir = Path(root) / PROJECT_DIR_NAME
    with suppress(OSError):
        # каталог не пуст или отсутствует — так и задумано
        project_dir.rmdir()


def filter_session(data: SessionData) -> tuple[SessionData, list[str]]:
    """Оставляет только реально существующие файлы (перед восстановлением).

    Возвращает (отфильтрованная сессия, список пропущенных путей);
    ``active`` переживает фильтрацию только если сам файл на месте.
    Пути в сессии всегда абсолютные (снапшот берётся из ключей вьюх).
    """
    missing = [doc for doc in data.docs if not Path(doc).is_file()]
    docs = [doc for doc in data.docs if doc not in missing]
    active = data.active if data.active in docs else None
    return SessionData(docs=docs, active=active), missing
