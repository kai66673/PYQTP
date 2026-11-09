from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from pyqtp.base.pluggable_main_window import PluggableMainWindow

# ключ области настроек по умолчанию (слой приложения, QSettings)
SCOPE_APP = 'app'


class SettingsPage:
    """
    Базовый класс страницы настроек.

    Страницу создаёт и регистрирует контрол-владелец (см.
    ``SettingsControl.page_register``). Страница НЕ является ни вьюшкой,
    ни виджетом -- это обёртка, описывающий контракт между окном настроек
    и конкретным наполнением страницы:

      - ``create_page_widget()`` -- виджет с элементами управления
        (вызывается лениво, при первом открытии страницы, чтобы значения
        всегда читались из актуальных настроек);
      - ``load()`` -- загрузить значения из источника (QSettings / модели
        контрола) в элементы управления;
      - ``apply()`` -- перенести значения из элементов управления в
        источник и применить их немедленно; при ошибке должен бросать
        ``ValueError`` (или ``TypeError``) с человекочитаемым текстом --
        окно настроек покажет ошибку, источник останется нетронутым;
      - ``reset_defaults()`` -- сбросить настройки страницы к значениям
        по умолчанию.

    Страницы мгновенного действия (меняющие настройки сразу в своих
    элементах управления) могут не реализовывать ``apply()``, тогда кнопка
    «Применить» на этой странице недоступна.

    Страницы с ``scope_applicable = True`` работают в двух областях:
    ``'app'`` (настройки приложения) и ``'project'`` (настройки открытого
    проекта, см. ``SettingsControl.scope_register``). Такая страница в
    ``load()``/``apply()``/``reset_defaults()`` сама смотрит на
    ``self.scope`` и читает/пишет соответствующий слой.
    """

    title: ClassVar[str] = 'Настройка'
    order: ClassVar[int] = 0
    # страница умеет переключать область (Приложение/Проект)? окно настроек
    # показывает селектор области только для таких страниц
    scope_applicable: ClassVar[bool] = False

    def __init__(self, key: str | None = None) -> None:
        self._key = key
        self._main_wnd: PluggableMainWindow | None = None
        self._widget: QWidget | None = None
        # текущая область страницы ('app' — настройки приложения)
        self._scope: str = SCOPE_APP

    @property
    def key(self) -> str:
        """Уникальный ключ страницы (по умолчанию -- имя класса)."""
        return self._key or type(self).__name__

    @property
    def main_wnd(self) -> PluggableMainWindow:
        """Главное окно приложения (доступно после регистрации)."""
        assert self._main_wnd is not None
        return self._main_wnd

    @property
    def is_created(self) -> bool:
        """Создан ли уже виджет страницы."""
        return self._widget is not None

    @property
    def widget(self) -> QWidget:
        """Виджет страницы; создаётся лениво при первом обращении."""
        if self._widget is None:
            self._widget = self.create_page_widget()
        return self._widget

    ####################
    # Контракт страницы #
    ####################

    def create_page_widget(self) -> QWidget:
        """Создаёт виджет страницы (реализуется конкретными страницами)."""
        raise NotImplementedError

    def load(self) -> None:
        """Загружает текущие значения настроек в элементы управления."""

    def apply(self) -> None:
        """Применяет значения из элементов управления к настройкам."""

    def reset_defaults(self) -> None:
        """Сбрасывает настройки страницы к значениям по умолчанию."""

    @property
    def scope(self) -> str:
        """Текущая область страницы ('app' — настройки приложения)."""
        return self._scope

    def scope_set(self, scope: str) -> None:
        """Переключает область страницы (окно зовёт перед ``load()``)."""
        self._scope = scope

    ###############################
    # Служебное API для окна настроек #
    ###############################

    @property
    def supports_apply(self) -> bool:
        """Реализована ли у страницы кнопка «Применить»."""
        return type(self).apply is not SettingsPage.apply

    @property
    def supports_reset(self) -> bool:
        """Реализована ли у страницы кнопка «Сбросить по умолчанию»."""
        return (
            type(self).reset_defaults is not SettingsPage.reset_defaults
        )
