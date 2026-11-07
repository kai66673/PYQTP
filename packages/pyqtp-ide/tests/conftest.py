"""Фикстуры живых тестов редактора (QWebEngine + реальный LspHub)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pyqtp.controls.lsp_control.lsp_hub import LspHub
from pyqtp.web.qtmonaco_editor import QtMonacoEditorWidget


@pytest.fixture
def editor_pair(
    qapp: QApplication, tmp_path: Path,
) -> Iterator[Callable[..., tuple[LspHub, QtMonacoEditorWidget]]]:
    """Фабрика пары ``(hub, editor)``: файл + редактор QtMonaco поверх живого хаба.

    ``make(file_text, suffix='.py', registry_json=None) -> (hub, widget)``;
    ``registry_json`` — опциональная замена реестра хаба (до старта ws-сервера),
    например чтобы оставить в стеке один сервер. Очистка (закрытие редактора и
    shutdown хаба) — в финализаторе. Каждый редактор получает изолированный
    каталог-«проект», поэтому тесты не мешают друг другу процессами серверов
    (пул хаба ключуется ``(command, root_uri)``).
    """
    created: list[tuple[LspHub, QtMonacoEditorWidget]] = []

    def make(
        file_text: str, suffix: str = '.py', registry_json: str | None = None,
    ) -> tuple[LspHub, QtMonacoEditorWidget]:
        project = tmp_path / f'project{len(created)}'
        project.mkdir(parents=True, exist_ok=True)
        path = project / f'test_file{suffix}'
        path.write_text(file_text, encoding='utf-8')
        hub = LspHub()
        if registry_json:
            hub.registry_apply_json(registry_json)
        hub.ensure_started()
        widget = QtMonacoEditorWidget(path, lsp_host=hub.lsp_stack_for(path.as_uri()))
        widget.resize(900, 600)
        widget.show()
        created.append((hub, widget))
        return hub, widget

    yield make
    for hub, widget in created:
        widget.close()
        hub.shutdown()
