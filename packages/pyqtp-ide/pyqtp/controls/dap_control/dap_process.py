"""Команда запуска DAP-адаптера debugpy (этап 11, срез D1).

Каноническая топология (найдено пробами debugpy 1.8.21, см. план этапа 11):
клиент (IDE) подключается к адаптеру ``python -m debugpy.adapter`` — без
``--port`` адаптер говорит DAP прямо через свои stdin/stdout; сам адаптер
поднимает launcher и debuggee после запроса ``launch`` и сам же внедряет
``--adapter-access-token`` дочернему debuggee.

Чего делать НЕЛЬЗЯ (поломанные пробы задокументированы в плане):

* заводить TCP-порт и коннектиться к debuggee, запущенному вручную через
  ``python -m debugpy --listen … --wait-for-client`` — тот встраивает свой
  адаптер и ждёт сервер (``debugpyWaitingForServer``);
* передавать ``--server-access-token``/``--adapter-access-token`` вручную —
  адаптер генерирует токен сам, ручные токены дают
  ``pydevdAuthorize … Client not authenticated`` и ``exitCode 247``;
* добавлять адаптеру позиционный аргумент endpoint — argparse его не знает.

ENV-хук ``PYQTP_DAP_ADAPTER_COMMAND`` (аргументы команды в shell-синтаксисе,
разбираются ``shlex.split``) позволяет тестам подменить адаптер на фейковый.
"""

from __future__ import annotations

import os
import shlex
import sys

ENV_ADAPTER_COMMAND = 'PYQTP_DAP_ADAPTER_COMMAND'


def adapter_command() -> list[str]:
    """Команда запуска адаптера (по умолчанию — stdio-режим debugpy)."""
    override = os.environ.get(ENV_ADAPTER_COMMAND, '').strip()
    if override:
        return shlex.split(override)
    return [sys.executable, '-m', 'debugpy.adapter']
