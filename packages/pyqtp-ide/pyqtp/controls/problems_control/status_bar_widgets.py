"""Виджеты статус-бара панели «Проблемы» (этап 10.2).

Оба виджета — пассивные (данные пишет ``ProblemsControl`` при каждом
изменении стора диагностик / состояний LSP-процессов). Проверка их
состояния в тестах — программная (тексты лейблов, ``isVisibleTo``,
``isEnabled``) — без какой-либо визуальной оценки.

Символы счётчиков текстовые (✕/⚠) и наследуют цвет палитры темы — виджеты
темобезопасны без иконочной подложки.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.lsp_control.lsp_hub import SERVER_STATE_STARTING
from pyqtp.controls.problems_control.problems import (
    SEVERITY_COUNT_LABELS,
    SEVERITY_ERROR,
    SEVERITY_ORDER,
    SEVERITY_WARNING,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class ProblemsCountWidget(QToolButton):
    """Счётчики «✕ ошибок ⚠ предупреждений» в статус-баре (кликабельный).

    Кнопка без рамки; клик — намерение открыть панель «Проблемы»
    (сигнал ``clicked`` уже есть у ``QAbstractButton``). Нули показываем
    всегда — позиция не «прыгает» при появлении проблем.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.set_counts({})

    def set_counts(self, counts: Mapping[int, int]) -> None:
        """Обновить подпись по счётчикам серьёзности (коды LSP)."""
        errors = counts.get(SEVERITY_ERROR, 0)
        warnings = counts.get(SEVERITY_WARNING, 0)
        self.setText(f'\u2715 {errors}  \u26a0 {warnings}')
        total = sum(counts.values())
        if total:
            summary = ', '.join(
                f'{SEVERITY_COUNT_LABELS[severity]}: {counts[severity]}'
                for severity in SEVERITY_ORDER
                if counts.get(severity)
            )
            self.setToolTip(f'Проблем: {total} ({summary})')
        else:
            self.setToolTip('Проблем нет — открыть панель «Проблемы»')
        self.setEnabled(total > 0)


class LspInitWidget(QWidget):
    """Индикатор инициализации LSP: спиннер, пока есть идущий хендшейк.

    Виден только при процессах в состоянии «запуск» (этап 10.2 из статуса
    ``LspHub``); готовых серверов в статус-баре не показываем — чистый
    правый край, как в VS Code после прогрева.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel('LSP: инициализация…')
        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)  # busy-режим: бесконечная анимация
        self._spinner.setFixedSize(110, 12)
        self._spinner.setTextVisible(False)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        grid.addWidget(self._label, 0, 0)
        grid.addWidget(self._spinner, 1, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addLayout(grid)

        self._spinner.setVisible(False)
        self.setVisible(False)
        # число процессов в состоянии «запуск» (0 — индикатор погашен);
        # отдельное поле — чтобы тесты проверяли состояние программно,
        # не полагаясь на Qt-видимость в offscreen
        self._starting = 0

    def active_count(self) -> int:
        """Сколько LSP-процессов сейчас в состоянии «запуск»."""
        return self._starting

    def set_states(self, states: Mapping[tuple[str, str], str]) -> None:
        """Обновить индикатор по стору состояний ``LspHub.server_states()``."""
        starting = sum(1 for state in states.values() if state == SERVER_STATE_STARTING)
        self._starting = starting
        if starting <= 0:
            self._spinner.setVisible(False)
            self.setVisible(False)
            return
        total = len(states)
        suffix = f' ({starting}/{total})' if total > 1 else ''
        self._label.setText(f'LSP: инициализация{suffix}…')
        self._spinner.setVisible(True)
        self.setVisible(True)
