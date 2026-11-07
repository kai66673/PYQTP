from __future__ import annotations

from typing import Any, TypeAlias

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QSpinBox,
    QStyleOptionViewItem,
    QWidget,
)

from pyqtp.utils.styles.qss_edit_dialog import QssEditDialog

SettingValue: TypeAlias = int | float | str


class BaseValueEditBehavior:
    @staticmethod
    def inline_editor_enabled() -> bool:
        return True

    @staticmethod
    def create_inline_editor(parent: QWidget, **kwargs) -> QWidget:
        raise NotImplementedError

    @staticmethod
    def set_inline_editor_value(editor: QWidget, value: Any) -> None:
        raise NotImplementedError

    @staticmethod
    def get_inline_editor_value(editor: QWidget) -> Any:
        raise NotImplementedError

    @staticmethod
    def edit_value(value: SettingValue, parent: QWidget) -> SettingValue | None:
        raise NotImplementedError

    @staticmethod
    def paint_value(
        value: SettingValue,  # noqa: ARG004
        painter: QPainter,  # noqa: ARG004
        option: QStyleOptionViewItem,  # noqa: ARG004
    ) -> bool:
        return False


class IntValueEditBehavior(BaseValueEditBehavior):
    @staticmethod
    def create_inline_editor(parent: QWidget, **kwargs) -> QWidget:
        editor = QSpinBox(parent)
        min_value = kwargs.get('min_value')
        if min_value is not None:
            editor.setMinimum(min_value)
        max_value = kwargs.get('max_value')
        if max_value is not None:
            editor.setMaximum(max_value)
        return editor

    @staticmethod
    def set_inline_editor_value(editor: QWidget, value: Any) -> None:
        if isinstance(value, int):
            assert isinstance(editor, QSpinBox)
            editor.setValue(value)

    @staticmethod
    def get_inline_editor_value(editor: QWidget) -> Any:
        assert isinstance(editor, QSpinBox)
        return editor.value()

    @staticmethod
    def edit_value(value: SettingValue, parent: QWidget) -> SettingValue | None:  # noqa: ARG004
        error_message = 'Should be unreachable...'
        raise RuntimeError(error_message)


class ColorValueEditBehavior(BaseValueEditBehavior):
    @staticmethod
    def inline_editor_enabled() -> bool:
        return False

    @staticmethod
    def create_inline_editor(parent: QWidget, **kwargs) -> QWidget:  # noqa: ARG004
        error_message = 'Should be unreachable...'
        raise RuntimeError(error_message)

    @staticmethod
    def edit_value(value: SettingValue, parent: QWidget) -> SettingValue | None:
        assert isinstance(value, str)
        new_color = QColorDialog.getColor(initial=QColor(value), parent=parent)
        if new_color.isValid():
            return new_color.name()
        return None

    @staticmethod
    def paint_value(
        value: SettingValue, painter: QPainter, option: QStyleOptionViewItem
    ) -> bool:
        if not isinstance(value, str):
            return False
        fill_color = QColor(value)
        painter.save()
        painter.fillRect(option.rect, fill_color)  # type:ignore[reportAttributeAccessIssue]
        bright = (
            fill_color.redF() + fill_color.greenF() + fill_color.blueF()
        ) / 3.0
        bright_constant = 0.5
        fg_color = (
            QColor('#000000') if bright > bright_constant else QColor('#FFFFFF')
        )
        painter.setPen(fg_color)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, value)  # type:ignore[reportAttributeAccessIssue]

        painter.restore()

        return True


class QssStringValueEditBehavior(BaseValueEditBehavior):
    @staticmethod
    def inline_editor_enabled() -> bool:
        return False

    @staticmethod
    def create_inline_editor(parent: QWidget, **kwargs) -> QWidget:  # noqa: ARG004
        error_message = 'Should be unreachable...'
        raise RuntimeError(error_message)

    @staticmethod
    def edit_value(value: SettingValue, parent: QWidget) -> SettingValue | None:
        assert isinstance(value, str)
        qss_dialog = QssEditDialog(value, parent)
        if qss_dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return qss_dialog.get_qss()
