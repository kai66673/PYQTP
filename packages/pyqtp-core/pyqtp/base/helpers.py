from __future__ import annotations

import gettext
import importlib
import logging
import platform
import re
import subprocess
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QUrl, SignalInstance, Slot
from PySide6.QtGui import QDesktopServices

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def setup_logging(
    debug_loggers: list[str] | None = None,
    info_loggers: list[str] | None = None,
    fmt: str = '%(levelname)-.1s [%(name)s]: %(message)s',
    datefmt: str = '%H:%M:%S',
) -> None:
    """
    Настройка журналирования для вывода в stdout.
    """
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    logging.basicConfig(format=fmt, datefmt=datefmt, handlers=[stream_handler])

    def setup_logger(name: str, level: int) -> None:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        stream_handler.setLevel(level)

    for logger_name in info_loggers or []:
        setup_logger(logger_name, logging.INFO)

    for logger_name in debug_loggers or []:
        setup_logger(logger_name, logging.DEBUG)


@contextmanager
def slot_disconnected(*, signal: SignalInstance, slot: Slot) -> Iterator[None]:
    """Контекстный менеджер для временного отключения слота от сигнала."""
    try:
        signal.disconnect(slot)  # type: ignore[reportAttributeAccessIssue]
    except TypeError:
        yield
    else:
        yield
        signal.connect(slot)  # type: ignore[reportAttributeAccessIssue]


def slot_single_shot(
    callback: Callable, signal: SignalInstance, /, *args: Any, **kwargs: Any
) -> None:
    @Slot()
    def handler() -> None:
        signal.disconnect(handler)  # type: ignore[reportAttributeAccessIssue]
        callback(*args, **kwargs)

    signal.connect(handler)  # type: ignore[reportAttributeAccessIssue]


def argparse_patch_gettext() -> None:
    translation_table = {
        'usage: ': 'Использование: ',
        'positional arguments': 'Основные позиционные параметры',
        'optional arguments': 'Основные параметры',
        'show this help message and exit': 'показать эту справку',
        'unrecognized arguments: %s': 'нераспознанные аргументы: %s',
        'not allowed with argument %s': 'не разрешён с аргументом %s',
        'ignored explicit argument %r': 'игнорируемые явные аргументы %r',
        'the following arguments are required: %s': 'перечисленные аргументы являются обязательными: %s',
        'one of the arguments %s is required': 'хотя бы один из аргументов %s является обязательным',
        'expected one argument': 'ожидался один аргумент',
        'expected at most one argument': 'ожидалось не более одного аргумента',
        'expected at least one argument': 'ожидалось не менее одного аргумента',
        'ambiguous option: %(option)s could match %(matches)s': 'неоднозначный параметр: %(option)s подходит для %(matches)s',
        'unexpected option string: %s': 'нераспознанныей параметр: %s',
        '%r is not callable': '%r не функция',
        'invalid %(type)s value: %(value)r': 'неверное значение %(value)r для типа %(type)s value: ',
        'invalid choice: %(value)r (choose from %(choices)s)': 'некорректное значение:  %(value)r (выберите одно из %(choices)s)',
        '%(prog)s: error: %(message)s\n': '%(prog)s: ошибка: %(message)s\n',
    }

    def argparse_gettext(s: str) -> str:
        return translation_table.get(s, s)

    prev = gettext.gettext
    gettext.gettext = argparse_gettext
    # noinspection PyUnresolvedReferences
    gettext.gettext = prev


def transliterate(s: str) -> str:
    symbols = (
        'абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ',
        'abvgdeejzijklmnoprstufhzcss_y_euaABVGDEEJZIJKLMNOPRSTUFHZCSS_Y_EUA',
    )

    tr = {ord(a): ord(b) for a, b in zip(*symbols, strict=False)}
    return s.translate(tr)


def resolve_name(import_path: str) -> type:
    module_path, cls_name = import_path.rsplit('.', 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


class classproperty(property):  # noqa: N801
    def __get__(self, obj: Any, type: type | None = None) -> Any:  # noqa: A002
        return self.fget(type)  # type: ignore[reportOptionalCall]


@lru_cache
def get_main_wnd_annotation(cls: type) -> str:
    _annotations = {}
    for base in reversed(cls.__mro__):
        _annotations.update(getattr(base, '__annotations__', {}))
    return _annotations['_main_wnd']


def xdg_open(path: str | Path) -> None:
    path = Path(path).expanduser().resolve()

    if platform.system() == 'Darwin':
        subprocess.Popen(('open', f'{path}'))  # noqa: S603, S607
    else:
        subprocess.Popen(('xdg-open', f'{path}'))  # noqa: S603, S607


def file_open(path: str | Path) -> None:
    url = QUrl.fromLocalFile(str(path))
    QDesktopServices.openUrl(url)


def parse_memory_size_str(size_str: str) -> int:
    """Преобразует строку размера памяти в число байтов."""
    units = {
        'B': 1,
        'kB': 10**3,
        'MB': 10**6,
        'GB': 10**9,
        'TB': 10**12,
        'KiB': 2**10,
        'MiB': 2**20,
        'GiB': 2**30,
        'TiB': 2**40,
        'Б': 2**0,
        'КиБ': 2**10,
        'МиБ': 2**20,
        'ГиБ': 2**30,
        'ТиБ': 2**40,
        'байт': 1,
        'Кбайт': 10**3,
        'Мбайт': 10**6,
        'Гбайт': 10**9,
        'Tбайт': 10**12,
        'Кб': 10**3,
        'Мб': 10**6,
        'Гб': 10**9,
        'Tб': 10**12,
    }
    number, *unit = (string.strip() for string in size_str.split())
    if len(unit) == 1:
        lower_units = {k.lower(): v for k, v in units.values()}  # type: ignore[reportGeneralTypeIssues]
        return int(float(number) * lower_units[unit[0]])
    if len(unit) == 0:
        return int(number)
    error_message = (
        f'Неверный формат памяти, доступные приставки: {list(units)}'
    )
    raise ValueError(error_message)


BLOCK_URI_RE = re.compile(r'"(block:\/\/[a-zA-Z0-9-_\/]+)"', flags=re.MULTILINE)


def parse_block_uri(msg: str) -> str:
    def block_uri_converter(match):
        block_uri = match.group(1)
        block_id = block_uri.rsplit('/', 1)[-1]
        return f'<a href="{block_uri}">&quot;#{block_id}&quot;</a>'

    return BLOCK_URI_RE.sub(block_uri_converter, msg)
