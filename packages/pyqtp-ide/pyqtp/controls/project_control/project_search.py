"""Поиск по проекту (этап 9.3): сборка argv ripgrep и разбор его ``--json``.

Модуль не завязан на GUI — обе части покрываются юнит-тестами без
QApplication (и без бинаря rg: парсер кормится записанными JSON-строками).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

# Максимум совпадений, которые показывает панель (защита от «поиск по всему
# дереву одной буквой»): дальше процесс снимается, статус это помечает.
MAX_MATCHES = 2000


@dataclass(frozen=True)
class SearchMatch:
    """Одно совпадение: абсолютный путь + координаты Monaco (1-based)."""

    path: Path
    line: int
    column: int
    end_column: int
    text: str  # строка целиком, без перевода строки


def build_rg_args(
    query: str,
    *,
    case_sensitive: bool = False,
    whole_word: bool = False,
    regex_mode: bool = False,
) -> list[str]:
    """Аргументы ``rg`` для поиска ``query`` в текущем каталоге (``.``).

    ``--json`` — машинный вывод; по умолчанию регистронезависимый поиск и
    фиксированная строка (не regex). Скрытые файлы и ``.gitignore`` (в git
    репозитории) rg не отдаёт по умолчанию — как в привычных IDE.
    """
    args = ['--json', '--line-number', '--no-messages']
    if not case_sensitive:
        args.append('-i')
    if whole_word:
        args.append('-w')
    if not regex_mode:
        args.append('--fixed-strings')
    args.extend(['--', query, '.'])
    return args


def split_complete_lines(buffer: bytes) -> tuple[list[bytes], bytes]:
    r"""Разобрать ``buffer`` на полные строки (по ``\n``).

    Возвращает ``(полные строки, хвост до последнего перевода)`` — хвост
    возвращается в буфер: JSON-строка может приехать из процесса частями.
    """
    parts = buffer.split(b'\n')
    return parts[:-1], parts[-1]


def parse_rg_event(raw: bytes) -> dict[str, Any] | None:
    """Разобрать одну JSON-строку вывода ``rg --json`` (``None`` — мусор)."""
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _byte_col_to_utf16_col(line_text: str, byte_offset: int) -> int:
    """Байтовое смещение внутри строки -> колонка Monaco (UTF-16, 1-based).

    ``rg`` отдаёт смещения в байтах UTF-8, редактор живёт на UTF-16
    (см. ``workspace_edit.py`` этапа 8.2) — считаем длину префикса в
    кодовых единицах UTF-16.
    """
    prefix = line_text.encode('utf-8')[:byte_offset]
    utf16_len = len(prefix.decode('utf-8', 'replace').encode('utf-16-le'))
    return utf16_len // 2 + 1


def match_from_event(event_data: dict[str, Any], root: Path) -> SearchMatch | None:
    """Один ``match``-ивент ``rg --json`` -> ``SearchMatch`` (``None`` — битый).

    Пути rg относительные (поиск идёт из корня проекта с аргументом ``.``) и
    с префиксом ``./`` — приводим к абсолютному пути под ``root``.
    """
    path_text = str((event_data.get('path') or {}).get('text') or '')
    if not path_text:
        return None
    rel = path_text.removeprefix('./')

    line_number = event_data.get('line_number')
    if not isinstance(line_number, int):
        return None
    line_text = str((event_data.get('lines') or {}).get('text') or '')

    sub = (event_data.get('submatches') or [{}])[0]
    raw_start = sub.get('start')
    raw_end = sub.get('end')
    start = raw_start if isinstance(raw_start, int) else 0
    end = raw_end if isinstance(raw_end, int) else start
    return SearchMatch(
        path=root / rel,
        line=line_number,
        column=_byte_col_to_utf16_col(line_text, start),
        # конец submatch в rg эксклюзивный (байт после совпадения) — конверт
        # той же формулой: получаем эксклюзивную границу и в UTF-16
        end_column=_byte_col_to_utf16_col(line_text, end),
        text=line_text.rstrip('\r\n'),
    )
