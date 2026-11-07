from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from pyqtp.controls.base_control import BaseControl

if TYPE_CHECKING:
    from collections.abc import Callable

dev_log = logging.getLogger('bcad.dev')

API_MARK = '_API_EXPORT_NS'


def public_api(namespace: str | None = None) -> Callable:
    """Помечает метод для экспорта в Python API."""
    if namespace is not None and not isinstance(namespace, str):
        error_message = 'Invalid use of mark_api_export decorator!'
        raise ValueError(error_message)

    def inner(function) -> Callable:
        setattr(function, API_MARK, namespace)
        return function

    return inner


class APIControl(BaseControl):
    def __init__(self) -> None:
        super().__init__()
        self._env = {}

    ################################
    # API окружения Python консоли #
    ################################

    @property
    def env(self) -> dict:
        return self._env

    env_updated_signal = Signal(dict)

    def register_variables(self, variable_dict: dict) -> None:
        """Регистрируем словарь в пространстве имён Python API."""
        self._env.update(variable_dict)
        self.env_updated_signal.emit(variable_dict)

    def register_object_api_exports(self, obj: object) -> None:
        """
        Инспектируем объект и экспортирует помеченные методы в пространство
        имён Python API.
        """
        # Ищем методы для экспорта
        methods = [
            (name, method)
            for name, method in inspect.getmembers(
                obj.__class__, predicate=inspect.isfunction
            )
            if hasattr(method, API_MARK)
        ]

        # Экспортируем методы
        for method_name, method in methods:
            binded_method = getattr(obj, method_name)
            # Общее пространство имён
            if (ns_name := method._api_export) is None:  # type:ignore[reportAttributeAccessIssue]
                self._env[method_name] = binded_method
            # Выделенное пространство имён
            else:
                ns = self._env.setdefault(ns_name, SimpleNamespace())
                setattr(ns, method_name, binded_method)
