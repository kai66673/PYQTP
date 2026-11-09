from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pyqtp.controls.project_control.project_control import SCOPE_PROJECT
from pyqtp.controls.project_control.project_settings import SECTION_EDITOR
from pyqtp.controls.settings_control.settings_page import SettingsPage
from pyqtp.web.editor_options import (
    EDITOR_THEME_AUTO,
    EDITOR_THEME_DARK,
    EDITOR_THEME_LIGHT,
    FONT_SIZE_RANGE,
)

if TYPE_CHECKING:
    from pyqtp.controls.doc_control.document_manager import DocumentManager
    from pyqtp.controls.project_control.project_control import ProjectControl
    from pyqtp.main_wnd.ide_main_window import IdeMainWindow


class EditorSettingsPage(SettingsPage):
    """
    Страница «Редактор» (этап 7, области — этап 9.6B2).

    UX-настройки Monaco: мини-карта, сворачивание блоков, зум колесом с
    ctrl, размер шрифта, тема редактора (следовать теме приложения /
    светлая / тёмная).

    Область ``app`` — слой настроек ПРИЛОЖЕНИЯ (QSettings, группа
    ``EditorOptions``); «Применить» пишет его и мгновенно доводит
    эффективные значения до всех открытых редакторов
    (``update_editor_options``), новые редакторы подхватывают их при
    создании. Область ``project`` — перекрытия открытого проекта
    (``.pyqtp/settings.json``, секция ``editor``): в виджетах показываются
    эффективные значения (перекрытие, а где его нет — значения приложения),
    «Применить» пишет весь набор ключей в слой проекта, «Сбросить» снимает
    секцию (значения приложения снова становятся видимыми). Перекрытие
    проекта всегда старше слоя приложения.
    """

    title = 'Редактор'
    order = 10
    scope_applicable = True

    _HINT = (
        'Настройки применяются ко всем открытым редакторам сразу. '
        'Область «Проект» — перекрытия для открытого проекта '
        '(.pyqtp/settings.json), они старше настроек приложения; '
        'область «Приложение» — общие настройки для всех проектов. '
        'Остальное поведение по умолчанию: поиск ctrl+F, замена ctrl+H, '
        'сворачивание ctrl+K, увеличение выделения ctrl+Shift+., умные '
        'переходы alt+стрелки, зум шрифта ctrl+= / ctrl+- / ctrl+0.'
    )

    def __init__(self, doc_ctrl: DocumentManager) -> None:
        super().__init__(key='editor')
        self._doc_ctrl = doc_ctrl
        self._minimap_check: QCheckBox | None = None
        self._folding_check: QCheckBox | None = None
        self._wheel_zoom_check: QCheckBox | None = None
        self._font_size_spin: QSpinBox | None = None
        self._theme_combo: QComboBox | None = None

    def create_page_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(self._HINT, widget)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        self._minimap_check = QCheckBox('Мини-карта', widget)
        form.addRow(self._minimap_check)
        self._folding_check = QCheckBox('Сворачивание блоков', widget)
        form.addRow(self._folding_check)
        self._wheel_zoom_check = QCheckBox('Масштаб шрифта колесом (ctrl+колесо)', widget)
        form.addRow(self._wheel_zoom_check)
        self._font_size_spin = QSpinBox(widget)
        self._font_size_spin.setRange(FONT_SIZE_RANGE[0], FONT_SIZE_RANGE[1])
        self._font_size_spin.setSuffix(' pt')
        form.addRow('Размер шрифта', self._font_size_spin)
        # тема редактора Monaco: только встроенные темы (vs/vs-dark),
        # userData — логические значения настройки из editor_options
        self._theme_combo = QComboBox(widget)
        self._theme_combo.addItem('Как в приложении', EDITOR_THEME_AUTO)
        self._theme_combo.addItem('Светлая', EDITOR_THEME_LIGHT)
        self._theme_combo.addItem('Тёмная', EDITOR_THEME_DARK)
        form.addRow('Тема редактора', self._theme_combo)
        layout.addLayout(form)

        layout.addStretch(1)

        self.load()
        return widget

    def load(self) -> None:
        assert self._minimap_check is not None
        assert self._folding_check is not None
        assert self._wheel_zoom_check is not None
        assert self._font_size_spin is not None
        assert self._theme_combo is not None

        if self.scope == SCOPE_PROJECT:
            # эффективные значения: перекрытие проекта, а где его нет —
            # слой приложения (иначе страница копировала бы перекрытие
            # в настройки приложения — 9.6B)
            options = self._doc_ctrl.editor_ux_options()
        else:
            options = self._doc_ctrl.editor_ux_options_app()
        self._minimap_check.setChecked(bool(options['minimap']))
        self._folding_check.setChecked(bool(options['folding']))
        self._wheel_zoom_check.setChecked(bool(options['mouse_wheel_zoom']))
        self._font_size_spin.setValue(int(options['font_size']))
        theme_idx = self._theme_combo.findData(str(options['theme']))
        self._theme_combo.setCurrentIndex(
            max(theme_idx, 0)
        )  # неизвестное значение -> «Как в приложении»

    def apply(self) -> None:
        assert self._minimap_check is not None
        assert self._folding_check is not None
        assert self._wheel_zoom_check is not None
        assert self._font_size_spin is not None
        assert self._theme_combo is not None

        options: dict[str, Any] = {
            'minimap': self._minimap_check.isChecked(),
            'folding': self._folding_check.isChecked(),
            'mouse_wheel_zoom': self._wheel_zoom_check.isChecked(),
            'font_size': self._font_size_spin.value(),
            'theme': str(self._theme_combo.currentData()),
        }
        # ValueError/TypeError уходит в окно настроек; источник не тронут
        if self.scope == SCOPE_PROJECT:
            # один проход: валидация всех ключей + одна запись файла
            # + одно переприменение слоёв (project_settings_update)
            self._project_ctrl().project_settings_update(
                SECTION_EDITOR, options
            )
        else:
            self._doc_ctrl.apply_editor_ux_options(options)

    def reset_defaults(self) -> None:
        if self.scope == SCOPE_PROJECT:
            # снимает секцию editor из перекрытий проекта
            self._project_ctrl().project_settings_reset(SECTION_EDITOR)
        else:
            self._doc_ctrl.reset_editor_ux_options()
        self.load()

    def _project_ctrl(self) -> ProjectControl:
        # страница живёт в IDE: главное окно здесь всегда IdeMainWindow
        # (cast — чтобы не тянуть рантайм-импорт и не ловить цикл)
        return cast('IdeMainWindow', self.main_wnd).project_ctrl
