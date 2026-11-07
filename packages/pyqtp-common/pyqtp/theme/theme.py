from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtGui import QColor, QPalette


@dataclass
class Theme:
    name: str
    parent_name: str
    is_dark: bool

    icon_color_normal: str
    icon_color_disabled: str
    icon_color_active: str
    icon_color_selected: str

    icon_color_normal_checked: str
    icon_color_disabled_checked: str
    icon_color_active_checked: str
    icon_color_selected_checked: str

    palette: list[list[str]]

    main_qss: str

    meta: dict[str, Any]

    @staticmethod
    def from_dict(obj: dict) -> Theme:
        return Theme(
            name=obj['name'],
            parent_name=obj.get('parent_name', ''),
            is_dark=obj['is_dark'],
            icon_color_normal=obj['icon_color_normal'],
            icon_color_disabled=obj['icon_color_disabled'],
            icon_color_active=obj['icon_color_active'],
            icon_color_selected=obj['icon_color_selected'],
            icon_color_normal_checked=obj.get(
                'icon_color_normal_checked', obj['icon_color_normal']
            ),
            icon_color_disabled_checked=obj.get(
                'icon_color_disabled_checked', obj['icon_color_disabled']
            ),
            icon_color_active_checked=obj.get(
                'icon_color_active_checked', obj['icon_color_active']
            ),
            icon_color_selected_checked=obj.get(
                'icon_color_selected_checked', obj['icon_color_selected']
            ),
            palette=obj['palette'],
            main_qss=obj.get('main_qss', ''),
            meta=obj.get('meta', {}),
        )

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'parent_name': self.parent_name,
            'is_dark': self.is_dark,
            'icon_color_normal': self.icon_color_normal,
            'icon_color_disabled': self.icon_color_disabled,
            'icon_color_active': self.icon_color_active,
            'icon_color_selected': self.icon_color_selected,
            'icon_color_normal_checked': self.icon_color_normal_checked,
            'icon_color_disabled_checked': self.icon_color_disabled_checked,
            'icon_color_active_checked': self.icon_color_active_checked,
            'icon_color_selected_checked': self.icon_color_selected_checked,
            'palette': self.palette,
            'main_qss': self.main_qss,
            'meta': self.meta,
        }

    def create_palette(self) -> QPalette:
        palette = QPalette()
        for color_role_index in range(QPalette.ColorRole.NColorRoles.value):
            color_role = QPalette.ColorRole(color_role_index)
            palette.setColor(
                QPalette.ColorGroup.Active,
                color_role,
                QColor(self.palette[color_role_index][0]),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled,
                color_role,
                QColor(self.palette[color_role_index][1]),
            )
            palette.setColor(
                QPalette.ColorGroup.Inactive,
                color_role,
                QColor(self.palette[color_role_index][2]),
            )
        return palette
