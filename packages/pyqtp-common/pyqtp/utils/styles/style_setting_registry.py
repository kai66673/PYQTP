from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from bcad.utils.styles.style_value_editors import BaseValueEditBehavior


@dataclass(frozen=True)
class StyleSettingDescriptor:
    name: str
    editor_cls: type[BaseValueEditBehavior]
    editor_init_kwargs: dict[str, int | float | str]


@dataclass(frozen=True)
class StyleSettingsGroup:
    name: str
    settings: list[StyleSettingDescriptor]
    getter: Callable[..., list[int | float | str]]
    setter: Callable[[list[int | float | str]], None]
