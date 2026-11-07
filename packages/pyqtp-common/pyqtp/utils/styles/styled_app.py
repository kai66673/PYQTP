from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from pyqtp.utils.styles.style_setting_registry import (
    StyleSettingDescriptor,
    StyleSettingsGroup,
)
from pyqtp.utils.styles.style_value_editors import ColorValueEditBehavior


class StyledApplication(QApplication):
    def __init__(self, args: list[str]):
        super().__init__(args)
        self.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)
        self.setStyle('fusion')

        self._style_registry: list[StyleSettingsGroup] = []

        self.register_style_group(
            StyleSettingsGroup(
                name='Палитра',
                settings=[
                    StyleSettingDescriptor(
                        name='Window (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Window (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Window (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='WindowText (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='WindowText (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='WindowText (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Base (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Base (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Base (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='AlternateBase (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='AlternateBase (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='AlternateBase (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipBase (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipBase (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipBase (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipText (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipText (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ToolTipText (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Text (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Text (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Text (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Button (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Button (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Button (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ButtonText (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ButtonText (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='ButtonText (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Link (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Link (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Link (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Highlight (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Highlight (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Highlight (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='HighlightedText (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='HighlightedText (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='HighlightedText (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Light (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Light (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Light (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Midlight (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Midlight (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Midlight (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Dark (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Dark (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Dark (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Mid (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Mid (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Mid (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='BrightText (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='BrightText (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='BrightText (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Shadow (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Shadow (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Shadow (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='NoRole (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='NoRole (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='NoRole (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='PLaceHolder (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='PLaceHolder (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='PLaceHolder (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Accent (Active)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Accent (Inactive)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                    StyleSettingDescriptor(
                        name='Accent (Disabled)',
                        editor_cls=ColorValueEditBehavior,
                        editor_init_kwargs={},
                    ),
                ],
                getter=self.get_palette_color_names,
                setter=self.set_palette_color_names,
            )
        )

    def register_style_group(self, style_group: StyleSettingsGroup):
        self._style_registry.append(style_group)

    def get_palette_color_names(self) -> list[int | float | str]:
        palette = self.palette()

        return [
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Window
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Window
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.WindowText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Base
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Base
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.AlternateBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.AlternateBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.AlternateBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ToolTipBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.ToolTipBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ToolTipBase
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ToolTipText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.ToolTipText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ToolTipText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Text
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Text
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Button
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Button
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.ButtonText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Link
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Link
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Link
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Light
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Light
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Light
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Midlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Midlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Midlight
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Dark
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Dark
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Dark
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Mid
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Mid
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Mid
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.BrightText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.BrightText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.BrightText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Shadow
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Shadow
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Shadow
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.NoRole
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.NoRole
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.NoRole
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.PlaceholderText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.PlaceholderText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.PlaceholderText
            ).name(),
            palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.Accent
            ).name(),
            palette.color(
                QPalette.ColorGroup.Inactive, QPalette.ColorRole.Accent
            ).name(),
            palette.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Accent
            ).name(),
        ]

    def set_palette_color_names(  # noqa: PLR0915
        self, colors: list[int | float | str]
    ) -> None:
        assert len(colors) == 21 * 3

        palette = QPalette()

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Window,
            QColor(colors[0]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Window,
            QColor(colors[1]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Window,
            QColor(colors[2]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.WindowText,
            QColor(colors[3]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.WindowText,
            QColor(colors[4]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor(colors[5]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Base,
            QColor(colors[6]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Base,
            QColor(colors[7]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Base,
            QColor(colors[8]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.AlternateBase,
            QColor(colors[9]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.AlternateBase,
            QColor(colors[10]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.AlternateBase,
            QColor(colors[11]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ToolTipBase,
            QColor(colors[12]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ToolTipBase,
            QColor(colors[13]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ToolTipBase,
            QColor(colors[14]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ToolTipText,
            QColor(colors[15]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ToolTipText,
            QColor(colors[16]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ToolTipText,
            QColor(colors[17]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Text,
            QColor(colors[18]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Text,
            QColor(colors[19]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(colors[20]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Button,
            QColor(colors[21]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Button,
            QColor(colors[22]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Button,
            QColor(colors[23]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ButtonText,
            QColor(colors[24]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ButtonText,
            QColor(colors[25]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor(colors[26]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Link,
            QColor(colors[27]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Link,
            QColor(colors[28]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Link,
            QColor(colors[29]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Highlight,
            QColor(colors[30]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Highlight,
            QColor(colors[31]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Highlight,
            QColor(colors[32]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.HighlightedText,
            QColor(colors[33]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.HighlightedText,
            QColor(colors[34]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor(colors[35]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Light,
            QColor(colors[36]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Light,
            QColor(colors[37]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Light,
            QColor(colors[38]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Midlight,
            QColor(colors[39]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Midlight,
            QColor(colors[40]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Midlight,
            QColor(colors[41]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Dark,
            QColor(colors[42]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Dark,
            QColor(colors[43]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Dark,
            QColor(colors[44]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Mid,
            QColor(colors[45]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Mid,
            QColor(colors[46]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Mid,
            QColor(colors[47]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.BrightText,
            QColor(colors[48]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.BrightText,
            QColor(colors[49]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.BrightText,
            QColor(colors[50]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Shadow,
            QColor(colors[51]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Shadow,
            QColor(colors[52]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Shadow,
            QColor(colors[53]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.NoRole,
            QColor(colors[54]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.NoRole,
            QColor(colors[55]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.NoRole,
            QColor(colors[56]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.PlaceholderText,
            QColor(colors[57]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.PlaceholderText,
            QColor(colors[58]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.PlaceholderText,
            QColor(colors[59]),
        )

        palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Accent,
            QColor(colors[60]),
        )
        palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Accent,
            QColor(colors[61]),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Accent,
            QColor(colors[62]),
        )

        self.setPalette(palette)

    @property
    def style_registry(self) -> list[StyleSettingsGroup]:
        return self._style_registry


class DarkStyleApplication(StyledApplication):
    def __init__(self, args: list[str]):  # noqa: PLR0915
        super().__init__(args)

        dark_palette = QPalette()

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Window,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Window,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Window,
            QColor('#353535'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.WindowText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.WindowText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor('#808080'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Base,
            QColor('#191919'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Base,
            QColor('#191919'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Base,
            QColor('#191919'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.AlternateBase,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.AlternateBase,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.AlternateBase,
            QColor('#353535'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ToolTipBase,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ToolTipBase,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ToolTipBase,
            QColor('#006699'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ToolTipText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ToolTipText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ToolTipText,
            QColor('#FFFFFF'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Text,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Text,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor('#808080'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Button,
            QColor('#404040'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Button,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Button,
            QColor('#353535'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.ButtonText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.ButtonText,
            QColor('#FFFFFF'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor('#808080'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Link,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Link,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Link,
            QColor('#006699'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Highlight,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Highlight,
            QColor('#006699'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Highlight,
            QColor('#006699'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.HighlightedText,
            QColor('#000000'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.HighlightedText,
            QColor('#000000'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor('#000000'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Light,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Light,
            QColor('#353535'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Light,
            QColor('#353535'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Midlight,
            QColor('#ff5e5c5b'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Midlight,
            QColor('#ff5e5c5b'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Midlight,
            QColor('#ff5e5c5b'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Dark,
            QColor('#ff302f2e'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Dark,
            QColor('#ff302f2e'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Dark,
            QColor('#ff302f2e'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Mid,
            QColor('#ff4a4947'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Mid,
            QColor('#ff4a4947'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Mid,
            QColor('#ff4a4947'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.BrightText,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.BrightText,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.BrightText,
            QColor('#ffffffff'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Shadow,
            QColor('#ffe7e4e0'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Shadow,
            QColor('#ffe7e4e0'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Shadow,
            QColor('#ffe7e4e0'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.NoRole,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.NoRole,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.NoRole,
            QColor('#ffffffff'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.PlaceholderText,
            QColor('#80ffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.PlaceholderText,
            QColor('#80ffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.PlaceholderText,
            QColor('#80ffffff'),
        )

        dark_palette.setColor(
            QPalette.ColorGroup.Active,
            QPalette.ColorRole.Accent,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Inactive,
            QPalette.ColorRole.Accent,
            QColor('#ffffffff'),
        )
        dark_palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Accent,
            QColor('#ffffffff'),
        )

        self.setPalette(dark_palette)
