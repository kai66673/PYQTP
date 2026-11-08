"""Тесты настройки «тема редактора» без GUI: чистая функция разрешения темы
(``resolve_monaco_theme``), валидация значения и roundtrip через QSettings
(изолированный ini-файл, бой QSettings приложения не трогается).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from pyqtp.web.editor_options import (
    DEFAULT_EDITOR_UX_OPTIONS,
    EDITOR_THEME_AUTO,
    EDITOR_THEME_DARK,
    EDITOR_THEME_LIGHT,
    MONACO_THEME_DARK,
    MONACO_THEME_LIGHT,
    read_editor_ux_options,
    resolve_monaco_theme,
    to_monaco_options,
    validate_editor_ux_options,
    write_editor_ux_options,
)


# --------------------------- разрешение темы -------------------------------


@pytest.mark.parametrize(
    ('theme', 'app_is_dark', 'expected'),
    [
        # auto следует теме приложения
        (EDITOR_THEME_AUTO, False, MONACO_THEME_LIGHT),
        (EDITOR_THEME_AUTO, True, MONACO_THEME_DARK),
        # явные значения игнорируют тему приложения
        (EDITOR_THEME_LIGHT, True, MONACO_THEME_LIGHT),
        (EDITOR_THEME_DARK, False, MONACO_THEME_DARK),
    ],
)
def test_resolve_monaco_theme(
    theme: str, app_is_dark: bool, expected: str,
) -> None:
    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    options['theme'] = theme
    assert resolve_monaco_theme(options, app_is_dark=app_is_dark) == expected


def test_resolve_monaco_theme_defaults_to_auto() -> None:
    """Ключа ``theme`` нет в словаре (ручный вызов без страницы настроек) —
    поведение как у ``auto``."""
    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    del options['theme']
    assert resolve_monaco_theme(options, app_is_dark=False) == MONACO_THEME_LIGHT
    assert resolve_monaco_theme(options, app_is_dark=True) == MONACO_THEME_DARK


def test_theme_not_in_monaco_payload() -> None:
    """Тема Monaco уходит отдельным сообщением ``theme``, в payload
    ``update_editor_options`` ей не место."""
    payload = to_monaco_options(dict(DEFAULT_EDITOR_UX_OPTIONS))
    assert 'theme' not in payload


# ------------------------------ валидация ----------------------------------


def test_theme_missing_rejected() -> None:
    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    del options['theme']
    with pytest.raises(ValueError, match='не задана'):
        validate_editor_ux_options(options)


def test_theme_wrong_type_rejected() -> None:
    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    options['theme'] = 7
    with pytest.raises(TypeError, match='должна быть строкой'):
        validate_editor_ux_options(options)


@pytest.mark.parametrize('bad', ['hc-black', 'light ', 'автоматически', ''])
def test_theme_unknown_value_rejected(bad: str) -> None:
    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    options['theme'] = bad
    with pytest.raises(ValueError, match='должна быть одной из'):
        validate_editor_ux_options(options)


# ------------------------- roundtrip через QSettings -----------------------


@pytest.fixture
def scoped_settings(tmp_path: Path) -> Iterator[QSettings]:
    """QSettings на изолированный ini-файл (не трогает настройки приложения)."""
    settings = QSettings(
        str(tmp_path / 'editor_options.ini'), QSettings.Format.IniFormat
    )
    settings.clear()
    settings.sync()
    yield settings
    settings.clear()
    settings.sync()


def test_theme_roundtrip(scoped_settings: QSettings) -> None:
    """Дефолт — auto; запись/чтение значения проходит, запись дефолта
    очищает ключ; мусор в файле читается как дефолт."""
    assert read_editor_ux_options(scoped_settings)['theme'] == EDITOR_THEME_AUTO

    options = dict(DEFAULT_EDITOR_UX_OPTIONS)
    options['theme'] = EDITOR_THEME_DARK
    write_editor_ux_options(scoped_settings, options)
    scoped_settings.sync()
    assert read_editor_ux_options(scoped_settings)['theme'] == EDITOR_THEME_DARK

    # возврат к дефолту — ключ удаляется, читается снова auto
    write_editor_ux_options(scoped_settings, dict(DEFAULT_EDITOR_UX_OPTIONS))
    scoped_settings.sync()
    scoped_settings.beginGroup('EditorOptions')
    try:
        assert 'theme' not in scoped_settings.allKeys()
    finally:
        scoped_settings.endGroup()
    assert read_editor_ux_options(scoped_settings)['theme'] == EDITOR_THEME_AUTO

    # повреждённое значение из конфига не роняет чтение — откат к дефолту
    scoped_settings.setValue('EditorOptions/theme', 'фиолетовая')
    scoped_settings.sync()
    assert read_editor_ux_options(scoped_settings)['theme'] == EDITOR_THEME_AUTO
