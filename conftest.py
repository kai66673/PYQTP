"""Общая инфраструктура тестов: Qt-окружение и session-приложение.

Запускать из корня репозитория: ``uv run pytest`` (детерминированные тесты)
и ``uv run pytest -m 'live or webengine'`` (живые LSP-серверы / QWebEngine).

Env-переменные ставятся на импорте этого файла, до загрузки PySide6:
- offscreen-платформа по умолчанию (не полагаться на launcher.py, он форсит
  xcb и single-instance по D-Bus);
- отключённая песочница QtWebEngine.

``qapp`` переопределён как session-scoped фикстура (политика DESTROY_ON_POLICY
в pytest-qt убивала бы QApplication между тестами).

Хук ``pytest_sessionfinish`` завершает процесс через ``os._exit``: Qt/C++
деструкторы виджетов (главное окно, KDDockWidgets-обёртки) отложены до
мусоросборщика конца сессии и на интерпретаторе работают по уже невалидным
C++-указателям — heap corruption/сегфолт ПОСЛЕ того, как все тесты прошли
(результаты и коды выхода к этому моменту полностью сформированы и
выписаны). Тот же граф в standalone-харнесе живёт без проблем.

Циклический GC отключён на всю сессию (на импорте этого файла): тот же граф
утёкших обёрток (PluggableMainWindow/KDD, пойманных в циклы ссылок) иначе
финализируется коллектором СЕРЕДИ сессии — gen2-сборка в момент, когда C++
граф уже частично уничтожен, даёт SIGTRAP в QWebEngineCore (подтверждено
core dump: gc_collect_main -> DockWidgetWrapper::~ -> QWebEnginePage::~).
Не-циклические объекты это не задевает (refcounting работает), а финал
сессии всё равно ``os._exit``. Отладочный рубильник: ``PYQTP_GC=1``.
"""

import gc
import os
import sys
from typing import cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

if not os.environ.get('PYQTP_GC'):
    gc.disable()  # см. докстринг модуля (защита от GC-финализации Qt-графа)

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_app: QApplication | None = None


@pytest.fixture(scope='session')
def qapp() -> QApplication:
    """Один QApplication на всю сессию (с учётом требований QtWebEngine)."""
    global _app  # noqa: PLW0603
    if _app is None:
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True,
        )
        _app = cast('QApplication', QApplication.instance() or QApplication([]))
    return _app


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Жёстко завершает процесс после окончания сессии (см. докстринг модуля).

    trylast — чтобы отработали pytest-cov и прочие плагины, выписывающие
    свои артефакты на sessionfinish.

    Для отладки падений (нужен обычный терминальный отчёт с трейсеками,
    который печатается уже ПОСЛЕ этого хука) можно задать
    ``PYQTP_NO_OS_EXIT=1`` — тогда процесс завершается штатно и может
    упасть уже в деструкторах Qt (результаты тестов при этом корректны).
    """
    if os.environ.get('PYQTP_NO_OS_EXIT'):
        return
    sys.stdout.flush()
    sys.stderr.flush()
    # осознанный обход уничтожения Qt-графа интерпретатором
    os._exit(int(exitstatus))
