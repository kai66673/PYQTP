"""Файловые операции панели «Проводник» (этап 9.5): проверка и исполнение.

Модуль не завязан на виджеты — только ``Path`` и ``QFile`` (для корзины);
покрывается юнит-тестами без QApplication. Ошибки — ``ProjectOpError``
с человекочитым русским текстом (UI показывает его как есть).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QFile, QFileInfo

if TYPE_CHECKING:
    from pathlib import Path

# Имена, которые нельзя создавать/переходить напрямую.
_RESERVED_NAMES = frozenset({'.', '..'})

# Разделители путей, запрещённые в «новом имени» (диалог вводит имя внутри
# каталога, а не путь).
_FORBIDDEN_SEPARATORS = frozenset(
    sep for sep in ('/', '\\', os.sep, os.altsep) if sep
)


class ProjectOpError(ValueError):
    """Отказ файловой операции с человекочитым текстом для UI."""


def validate_entry_name(name: str) -> str:
    """Нормализовать «новое имя» (ошибки — ProjectOpError).

    Пустое имя, ``.``/``..`` и любые разделители пути — отказ: операции
    панели работают с одним сегментом внутри выбранного каталога.
    """
    cleaned = name.strip()
    if not cleaned:
        msg = 'Имя не может быть пустым.'
        raise ProjectOpError(msg)
    if cleaned in _RESERVED_NAMES:
        msg = f'Имя «{cleaned}» зарезервировано.'
        raise ProjectOpError(msg)
    if _FORBIDDEN_SEPARATORS & set(cleaned):
        msg = f'Имя «{cleaned}» не должно содержать разделители пути.'
        raise ProjectOpError(msg)
    return cleaned


def create_entry(parent: Path, name: str, *, is_dir: bool) -> Path:
    """Создать файл/каталог ``parent/name`` (ошибки — ProjectOpError).

    Родительский каталог обязан существовать (в дереве проекта это всегда
    так), цель — не существовать (перезапись — не задача создания).
    """
    cleaned = validate_entry_name(name)
    if not parent.is_dir():
        msg = f'Каталог «{parent}» не существует.'
        raise ProjectOpError(msg)
    target = parent / cleaned
    if target.exists():
        kind = 'каталог' if is_dir else 'файл'
        msg = f'{kind} «{target.name}» уже существует.'
        raise ProjectOpError(msg)
    try:
        if is_dir:
            target.mkdir()
        else:
            target.touch()
    except OSError as exc:
        msg = f'Не удалось создать «{target}»: {exc}'
        raise ProjectOpError(msg) from exc
    return target


def renamed_path(target: Path, new_name: str) -> Path:
    """Целевой путь переименования ``target`` на ``new_name`` (ошибки — ProjectOpError)."""
    cleaned = validate_entry_name(new_name)
    new_path = target.parent / cleaned
    if new_path == target:
        msg = 'Новое имя совпадает с текущим.'
        raise ProjectOpError(msg)
    if new_path.exists():
        msg = f'«{cleaned}» уже существует в этом каталоге.'
        raise ProjectOpError(msg)
    return new_path


def move_to_trash(path: Path) -> bool:
    """Переместить файл/каталог в корзину ОС (False — не удалось).

    ``QFile.moveToTrash`` отказывает, когда корзины в системе нет или путь
    пропал; молча удалять навсегда мы не умеем — это решение пользователя.
    """
    if not QFileInfo(str(path)).exists():
        return False
    moved = QFile.moveToTrash(str(path))
    if isinstance(moved, tuple):
        # стабы PySide6 объявляют статический moveToTrash парой
        # (bool, finalPathInTrash), а рантайм 6.6 возвращает просто bool
        return bool(moved[0])
    return bool(moved)
