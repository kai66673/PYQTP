from __future__ import annotations

import bisect
from collections import namedtuple

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMenuBar, QWidget

PriorityRecord = namedtuple("PriorityRecord", ["priority", "item"])     # noqa: PYI024


def _find_predecessor(menu: QMenu | QMenuBar, priority: int) -> QAction | None:
    # Ищем куда добавлять запись
    priorities = [a.property("order_priority") for a in menu.actions()]
    i = bisect.bisect(priorities, priority)
    try:
        before_action = menu.actions()[i]
    except IndexError:
        before_action = None
    return before_action


class PriorityMenu(QMenu):
    priority_menu_children: list[PriorityMenu | QAction]

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        self.priority_menu_children = []

    def addMenu(    # type:ignore[reportIncompatibleMethodOverride]
        self, menu_name: str, *, priority: int = 0
    ) -> PriorityMenu:
        # Создаем меню
        _name, menu = menu_name, PriorityMenu(menu_name, self)
        # Ищем куда вставлять
        predecessor = _find_predecessor(self, priority)
        # Вставляем в позицию
        super().insertMenu(predecessor, menu)   # type:ignore[reportArgumentType]
        # Задаем приоритет
        menu.menuAction().setProperty("order_priority", priority)
        # Сохранение ссылки на объект
        self.priority_menu_children.append(menu)
        return menu

    def addAction(      # type:ignore[reportIncompatibleMethodOverride]
            self, obj: str | QAction, *, priority: int = 0
    ) -> QAction:
        # Создаем действие, если необходимо
        if isinstance(obj, str):
            action = QAction(obj, self)
        elif isinstance(obj, QAction):
            action = obj
        else:
            msg = "Unsupported type!"
            raise ValueError(msg)
        # Ищем куда вставлять
        predecessor = _find_predecessor(self, priority)
        # Вставляем в позицию
        super().insertAction(predecessor, action)   # type:ignore[reportArgumentType]
        # Задаем приоритет
        action.setProperty("order_priority", priority)
        # Сохранение ссылки на объект
        self.priority_menu_children.append(action)
        return action

    def removeAction(self, action: QAction) -> None:
        super().removeAction(action)
        self.priority_menu_children.remove(action)

    def remove_menu(self, menu: PriorityMenu) -> None:
        super().removeAction(menu.menuAction())
        self.priority_menu_children.remove(menu)

    def addSeparator(self, *, priority: int = 0) -> QAction:
        action = QAction(self)
        action.setSeparator(True)
        self.addAction(action, priority=priority)
        return action


class PriorityMenuBar(QMenuBar):
    priority_menu_children: list[PriorityMenu | QAction]

    def __init__(self, parent = None):
        super().__init__(parent)
        self.priority_menu_children = []

    def addMenu(    # type:ignore[reportIncompatibleMethodOverride]
        self, menu_name: str, *, priority: int = 0
    ) -> PriorityMenu:
        # Создаем меню
        _name, menu = menu_name, PriorityMenu(menu_name, self)
        # Ищем куда вставлять
        predecessor = _find_predecessor(self, priority)
        # Вставляем в позицию
        super().insertMenu(predecessor, menu)   # type:ignore[reportArgumentType]
        # Задаем приоритет
        menu.menuAction().setProperty("order_priority", priority)
        # Сохранение ссылки на меню
        self.priority_menu_children.append(menu.menuAction())
        return menu

    def addAction(      # type:ignore[reportIncompatibleMethodOverride]
            self, obj: str | QAction, *, priority: int = 0
    ) -> QAction:
        # Создаем действие, если необходимо
        if isinstance(obj, str):
            action = QAction(obj, self)
        elif isinstance(obj, QAction):
            action = obj
        else:
            msg = "Unsupported type!"
            raise ValueError(msg)
        # Ищем куда вставлять
        predecessor = _find_predecessor(self, priority)
        # Вставляем в позицию
        super().insertAction(predecessor, action)   # type:ignore[reportArgumentType]
        # Задаем приоритет
        action.setProperty("order_priority", priority)
        # Сохранение ссылки на объект
        self.priority_menu_children.append(action)
        return action

    def removeAction(self, action: QAction) -> None:
        super().removeAction(action)
        self.priority_menu_children.remove(action)

    def addSeparator(self, *, priority: int = 0) -> QAction:
        action = QAction(self)
        action.setSeparator(True)
        self.addAction(action, priority=priority)
        return action

    def by_path(self, path: str) -> QAction | QMenu | None:
        current, rest = self, [path]
        while rest:
            name, *rest = rest[0].split("/", 1)
            try:
                current = next(iter(a for a in current.actions() if a.text() == name))  # type:ignore[reportAttributeAccessIssue]
            except StopIteration:
                raise KeyError(path) from None
            if current.menu():
                current = current.menu()
        return current  # type:ignore[reportReturnType]

    def ensure_path(self, path: str) -> PriorityMenu:
        current, rest = self, [path]
        while rest:
            name, *rest = rest[0].split("/", 1)
            try:
                current = next(iter(a for a in current.actions()    # type:ignore[reportAttributeAccessIssue]
                                    if a.text() == name)).menu()
            except StopIteration:
                current = current.addMenu(name)     # type:ignore[reportAttributeAccessIssue]
        return current      # type:ignore[reportReturnType]


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication([])

    main_wnd = QMainWindow()
    menubar = PriorityMenuBar()
    main_wnd.setMenuBar(menubar)

    p1 = menubar.addMenu("p1", priority=1)
    p1_1 = p1.addAction(QAction("p1_1", menubar), priority=3)
    p1_2 = p1.addAction(QAction("p1_2", menubar), priority=2)
    p1_3 = p1.addMenu("p1_3")
    p1_3_4 = p1_3.addAction("p1_3_4")
    p0 = menubar.addMenu("p0", priority=0)
    main_wnd.show()

    assert menubar.by_path("p1") is p1
    assert menubar.by_path("p1/p1_1") is p1_1
    assert menubar.by_path("p1/p1_2") is p1_2
    assert menubar.by_path("p1/p1_3") is p1_3
    assert menubar.by_path("p1/p1_3/p1_3_4") is p1_3_4
    assert menubar.by_path("p0") is p0

    app.exec()
