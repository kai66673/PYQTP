"""Этап 10.2 — статус-бар, F8-навигация и фильтр «только текущий файл».

Часть без GUI: юниты чистого ``next_problem`` (цикл F8) и стора состояний
``LspHub`` (индикатор инициализации) на голом хабе без окна.

Offscreen-GUI (харнес переиспользуется из ``test_problems_panel`` — живой
``IdeMainWindow`` c KDD, настоящий ``LspHub`` без процессов серверов, фейко-
вые документы из ``test_bookmarks``):

* счётчики в статус-баре (тексты/тултип/enabled, клик открывает панель,
  фильтр панели на счётчики не влияет);
* индикатор LSP — прямые переходы ``_set_server_state``/``_drop_server_state``
  через queued-мост контрола;
* F8/Shift+F8 цикл по двум файлам через ``open_definition_uri`` (каретка
  из зеркала ``current_cursor`` фейкового редактора);
* сигнал ``document_activated`` (повторная активация той же вкладки — без
  сигнала, закрытие активной — активация соседа).

Проверка виджетов — только программная (тексты, ``isEnabled``, счётчики) —
без какой-либо визуальной оценки.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

# харнес панели «Проблемы» (фикстуры + хелперы) — тесты одного каталога
from test_bookmarks import fake_edit
from test_problems_panel import (
    error_problem,
    fake_docs,
    org_app,
    panel_tree,
    panel_view,
    prj_root,
    publish,
    wnd,
)

from pyqtp.controls.lsp_control.lsp_hub import (
    SERVER_STATE_READY,
    SERVER_STATE_STARTING,
    LspHub,
)
from pyqtp.controls.problems_control import problems as prob

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot

    from pyqtp.main_wnd.ide_main_window import IdeMainWindow

_Entry = tuple[str, int, int]


# =============
# Юниты (без GUI) #
# =============


def test_next_problem_units() -> None:
    """Циклический F8-цикл: вперёд/назад, промахи каретки, точно на точке."""
    entries: list[_Entry] = [('a', 2, 1), ('a', 7, 5), ('b', 3, 1)]
    assert prob.next_problem([], None) is None
    assert prob.next_problem([], None, forward=False) is None
    # каретки нет — вход с начала/с конца
    assert prob.next_problem(entries, None) == ('a', 2, 1)
    assert prob.next_problem(entries, None, forward=False) == ('b', 3, 1)
    # каретка между точками — следующая строго после/перед предыдущая строго перед
    assert prob.next_problem(entries, ('a', 3, 0)) == ('a', 7, 5)
    assert prob.next_problem(entries, ('a', 3, 0), forward=False) == ('a', 2, 1)
    # каретка точно на точке: вперёд — к следующей, назад — к предыдущей (не к себе)
    assert prob.next_problem(entries, ('a', 7, 5)) == ('b', 3, 1)
    assert prob.next_problem(entries, ('a', 7, 5), forward=False) == ('a', 2, 1)
    # выход за границы — цикл
    assert prob.next_problem(entries, ('b', 9, 9)) == ('a', 2, 1)
    assert prob.next_problem(entries, ('a', 1, 1), forward=False) == ('b', 3, 1)
    # чужой файл каретки (в наборе своих файлов нет) — лексикографический сосед
    assert prob.next_problem(entries, ('c', 1, 1)) == ('a', 2, 1)


def test_hub_server_state_store() -> None:
    """Стор состояний на голом хабе: запись/снятие/гейт дублей/очистка."""
    hub = LspHub()
    try:
        key = ('cmd --stdio', 'file:///r/')
        assert hub.server_states() == {}

        calls: list[int] = []

        def cb() -> None:
            calls.append(1)

        hub.add_server_state_observer(cb)
        hub.add_server_state_observer(cb)  # дубликат не добавляется

        hub._set_server_state(key, SERVER_STATE_STARTING)
        assert hub.server_states() == {key: SERVER_STATE_STARTING}
        hub._set_server_state(key, SERVER_STATE_STARTING)  # без изменения — без оповещения
        assert len(calls) == 1

        hub._set_server_state(key, SERVER_STATE_READY)
        assert hub.server_states() == {key: SERVER_STATE_READY}
        assert len(calls) == 2

        hub._drop_server_state(key)
        hub._drop_server_state(key)  # несуществующая запись — молча
        assert hub.server_states() == {}
        assert len(calls) == 3

        hub._set_server_state(key, SERVER_STATE_STARTING)
        hub._clear_server_states()
        hub._clear_server_states()  # пустой стор — без лишнего оповещения
        assert hub.server_states() == {}
        assert len(calls) == 5

        hub.remove_server_state_observer(cb)
        hub.remove_server_state_observer(cb)
        hub._set_server_state(key, SERVER_STATE_STARTING)
        assert len(calls) == 5
    finally:
        hub.shutdown()


# =====================
# Offscreen-GUI тесты #
# =====================


def test_statusbar_counts_text_and_toggle(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Счётчики: нули всегда и disabled; после публикаций — тексты и enabled."""
    count = wnd.problems_ctrl.status_counts_widget
    assert count is not None
    assert count.text() == '\u2715 0  \u26a0 0'
    assert not count.isEnabled()

    publish(
        wnd, 'basedpyright', prj_root / 'a.py',
        (error_problem(2), error_problem(4, code='E2')),
    )
    publish(
        wnd, 'ruff', prj_root / 'a.py',
        (prob.Problem(
            line=1, col=1, end_line=1, end_col=2, severity=prob.SEVERITY_WARNING,
            message='не используется', code='F401', owner='ruff',
        ),),
    )

    def ready() -> bool:
        return count.isEnabled()

    qtbot.waitUntil(ready, timeout=5000)
    assert count.text() == '\u2715 2  \u26a0 1'
    assert count.toolTip() == (
        'Проблем: 3 (Ошибок: 2, Предупреждений: 1)'
    )

    publish(wnd, 'basedpyright', prj_root / 'a.py', ())
    publish(wnd, 'ruff', prj_root / 'a.py', ())
    qtbot.waitUntil(lambda: not count.isEnabled(), timeout=5000)
    assert count.text() == '\u2715 0  \u26a0 0'


def test_counter_click_opens_panel(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Клик по счётчикам — намерение показать панель (ensure_visible_requested)."""
    count = wnd.problems_ctrl.status_counts_widget
    assert count is not None
    publish(wnd, 'basedpyright', prj_root / 'a.py', (error_problem(2),))
    # по выключенной (нулевой) кнопке клик не работает — ждём включения
    qtbot.waitUntil(count.isEnabled, timeout=5000)
    view = panel_view(wnd)
    with qtbot.waitSignal(view.ensure_visible_requested, timeout=1000):
        count.click()


def test_lsp_indicator_from_hub_states(
    wnd: IdeMainWindow, qtbot: QtBot
) -> None:
    """Индикатор LSP: «запуск» показывается (счётчик при 2+), «готов» — гаснет."""
    lsp = wnd.problems_ctrl.status_lsp_widget
    assert lsp is not None
    assert lsp.active_count() == 0

    hub = wnd.lsp_hub
    key1 = ('cmd one', 'file:///r1/')
    key2 = ('cmd two', 'file:///r2/')

    hub._set_server_state(key1, SERVER_STATE_STARTING)

    def started() -> bool:
        return lsp.active_count() == 1

    qtbot.waitUntil(started, timeout=5000)
    assert 'LSP: инициализация…' in lsp._label.text()

    hub._set_server_state(key2, SERVER_STATE_STARTING)
    qtbot.waitUntil(lambda: lsp.active_count() == 2, timeout=5000)
    assert '(2/2)' in lsp._label.text()

    hub._set_server_state(key1, SERVER_STATE_READY)
    qtbot.waitUntil(lambda: lsp.active_count() == 1, timeout=5000)

    hub._set_server_state(key2, SERVER_STATE_READY)
    qtbot.waitUntil(lambda: lsp.active_count() == 0, timeout=5000)

    hub._drop_server_state(key1)
    hub._drop_server_state(key2)
    assert lsp.active_count() == 0


def test_f8_navigation_cycle_between_files(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """F8 цикл по двум файлам: переход через open_definition_uri, каретка из зеркала."""
    path_a = str(prj_root / 'a.py')
    path_b = str(prj_root / 'b.py')
    ctrl = wnd.problems_ctrl

    assert not ctrl.action_next_problem.isEnabled()

    publish(
        wnd, 'basedpyright', Path(path_a),
        (error_problem(2), error_problem(7, col=5)),
    )
    publish(wnd, 'ruff', Path(path_b), (error_problem(3, code='F821'),))

    expected: list[_Entry] = [
        (path_a, 2, 1), (path_a, 7, 5), (path_b, 3, 1),
    ]
    qtbot.waitUntil(lambda: ctrl.problem_entries() == expected, timeout=5000)
    assert ctrl.action_next_problem.isEnabled()
    assert ctrl.action_next_problem.shortcut() == QKeySequence('F8')
    assert ctrl.action_next_problem.shortcutContext() == Qt.ShortcutContext.WindowShortcut
    assert ctrl.action_prev_problem.shortcut() == QKeySequence('Shift+F8')

    # первый F8 — вход с начала: открывает a.py и ставит каретку
    ctrl.goto_problem(forward=True)
    qtbot.waitUntil(
        lambda: fake_edit(wnd, path_a).cursor_calls == [(2, 1, 'center')],
        timeout=5000,
    )
    # зеркало каретки (2,1) — следующий F8 идёт к (a,7,5)
    ctrl.goto_problem(forward=True)
    qtbot.waitUntil(
        lambda: fake_edit(wnd, path_a).cursor_calls[-1] == (7, 5, 'center'),
        timeout=5000,
    )
    # затем чужой файл — b.py открывается и получает каретку
    ctrl.goto_problem(forward=True)
    qtbot.waitUntil(
        lambda: fake_edit(wnd, path_b).cursor_calls == [(3, 1, 'center')],
        timeout=5000,
    )
    # цикл вперёд — возврат в начало
    ctrl.goto_problem(forward=True)
    qtbot.waitUntil(
        lambda: fake_edit(wnd, path_a).cursor_calls[-1] == (2, 1, 'center'),
        timeout=5000,
    )
    # Shift+F8 из (a,2,1) — цикл назад, в конец списка
    ctrl.goto_problem(forward=False)
    qtbot.waitUntil(
        lambda: fake_edit(wnd, path_b).cursor_calls[-1] == (3, 1, 'center'),
        timeout=5000,
    )


def test_filter_scope_changes_f8_set(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """Фильтр «только текущий файл»: панель, набор F8 и доступность экшенов."""
    path_a = str(prj_root / 'a.py')
    path_b = str(prj_root / 'b.py')
    path_c = str(prj_root / 'c.py')
    (prj_root / 'c.py').write_text('z = 3\n', encoding='utf-8')

    ctrl = wnd.problems_ctrl
    view = panel_view(wnd)

    publish(wnd, 'basedpyright', Path(path_a), (error_problem(2),))
    publish(wnd, 'ruff', Path(path_b), (error_problem(3, code='F821'),))

    all_entries = [(path_a, 2, 1), (path_b, 3, 1)]
    qtbot.waitUntil(lambda: ctrl.problem_entries() == all_entries, timeout=5000)

    view._filter_check.setChecked(True)
    # активной вкладки нет — набор пуст, экшены выключены, панель пуста
    qtbot.waitUntil(lambda: ctrl.problem_entries() == [], timeout=5000)
    assert not ctrl.action_next_problem.isEnabled()
    assert panel_tree(view) == []
    assert 'только текущий файл' in view._status_label.text()

    # открываем a — активная вкладка приходит сигналом, фильтр её подхватывает
    doc_ctrl = wnd.doc_ctrl
    doc_ctrl.open_document(path_a)
    doc_ctrl.open_document(path_b)
    qtbot.waitUntil(
        lambda: ctrl.problem_entries() == [(path_b, 3, 1)], timeout=5000,
    )
    assert ctrl.action_next_problem.isEnabled()
    tree = panel_tree(view)
    # проект не открыт — заголовки абсолютные; в панели только активный файл
    assert len(tree) == 1 and tree[0][0] == path_b

    # текущий файл без проблем — набор снова пуст (фильтр + активный c.py)
    doc_ctrl.open_document(path_c)
    qtbot.waitUntil(lambda: ctrl.problem_entries() == [], timeout=5000)
    assert not ctrl.action_next_problem.isEnabled()

    # счётчики статус-бара фильтр НЕ уважают — всегда по полному набору
    count = ctrl.status_counts_widget
    assert count is not None
    assert count.text() == '\u2715 2  \u26a0 0'

    # закрываем c (он был активен) — сосед по порядку (b) активируется сигналом,
    # фильтр показывает его проблемы
    doc_ctrl.close_document(path_c)

    def current_back() -> bool:
        return ctrl.problem_entries() == [(path_b, 3, 1)]

    qtbot.waitUntil(current_back, timeout=5000)


def test_document_activated_signal(
    wnd: IdeMainWindow, prj_root: Path, qtbot: QtBot
) -> None:
    """``document_activated``: только реальная смена; закрытие активной — сосед."""
    doc_ctrl = wnd.doc_ctrl
    activated: list[str] = []
    doc_ctrl.document_activated.connect(lambda view: activated.append(view.key))

    path_a = str(prj_root / 'a.py')
    path_b = str(prj_root / 'b.py')

    doc_ctrl.open_document(path_a)  # новый документ -> ensure_visible
    doc_ctrl.open_document(path_b)
    doc_ctrl.open_document(path_a)  # уже открыт — активация
    assert activated == [path_a, path_b, path_a]

    # повторная активация той же вкладки — без сигнала
    doc_ctrl._note_activated(path_a)
    assert activated == [path_a, path_b, path_a]

    # закрытие активной — сигналом активируется сосед по порядку
    assert doc_ctrl.close_document(path_a)
    assert activated == [path_a, path_b, path_a, path_b]

    # закрытие последней вкладки (соседей нет) — сигнала об активации нет
    assert doc_ctrl.close_document(path_b)
    assert activated == [path_a, path_b, path_a, path_b]
