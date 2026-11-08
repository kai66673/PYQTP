"""Живые тесты UX редактора (этап 7): find/replace, folding, smart select,
переходы back/forward, зум шрифта, опции из Python.

LSP здесь не нужен — виджет поднимается с пустым стеком серверов
(``lsp_host=[]``), поэтому все проверки ходят только через JS-мост Monaco.
Помечены маркером ``webengine`` — по умолчанию не запускаются (см. addopts
в корневом pyproject.toml).

Харнес повторяет ``test_lsp_editor_stack`` (run_js/wait_for): общие
хелперы держим внутри одного тестового файла — кросс-файловые импорты
тестовых модулей здесь не практикуются. Все JS-предикаты возвращают
строковые маркеры (PySide6 6.6 отдаёт JS-булевы как пустую строку).

Клавиатурные связки (ctrl+F/H, ctrl+=, ctrl+Shift+., alt+стрелки) в
offscreen-режиме WebEngine не доставляются в Chromium, поэтому проверяется
то, что от клавиш не зависит: наличие связок в keybinding-сервисе Monaco и
исполнение соответствующих команд через ``editor.trigger``.
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import qtmonaco
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from pyqtp.web.qtmonaco_editor import QtMonacoEditorWidget

# js_build не лежит в git — без него редактор не поднимется
JS_BUILD_OK = (Path(qtmonaco.__file__).parent / 'js_build' / 'index.html').is_file()

pytestmark = [
    pytest.mark.webengine,
    pytest.mark.skipif(
        not JS_BUILD_OK, reason='qtmonaco/js_build не собран (npm run build)',
    ),
]

JS_READY = "window.qtmonaco && window.qtmonaco.editor ? 'READY' : 'NOT_READY'"


# ------------------------------ помощники ---------------------------------


def run_js(widget: QtMonacoEditorWidget, script: str, *, timeout: float = 20.0) -> Any:
    """Выполняет JS в странице редактора и ждёт результат (локальный event loop)."""
    holder: dict[str, Any] = {}

    def _cb(value: Any) -> None:
        holder['value'] = value

    widget.page().runJavaScript(script, 0, _cb)
    spent, step = 0.0, 0.05
    while 'value' not in holder and spent < timeout:
        loop = QEventLoop()
        QTimer.singleShot(int(step * 1000), loop.quit)
        loop.exec()
        spent += step
    return holder.get('value')


def json_from_js(raw: Any) -> Any:
    """Разбор JSON-строки из ``run_js`` (не-строка/пусто -> None)."""
    if not isinstance(raw, str) or not raw:
        return None
    return json.loads(raw)


def wait_for(
    qtbot: Any,
    predicate: Any,
    *,
    timeout: float = 30.0,
    poll: float = 0.5,
    message: str = '',
) -> None:
    """Опрашивает ``predicate`` с интервалом ``poll`` секунд до ``timeout``."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message or 'условие не выполнилось за таймаут')
        qtbot.wait(int(poll * 1000))


def wait_editor_ready(qtbot: Any, widget: QtMonacoEditorWidget) -> None:
    """Ждёт готовности Monaco и моста в странице редактора (до 2 минут)."""
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_READY) == 'READY',
        timeout=120.0,
        poll=1.0,
        message='Monaco в странице редактора так и не поднялся',
    )
    # Монка окно существует уже на загрузке скрипта, а мост поднимается
    # следом: JS шлёт Python 'bridge_initialized', Connector по этому сигналу
    # сбрасывает стартовый буфер (set_text файла, опции). Ждём обе стороны —
    # иначе отложенные сообщения буфера перетирают прямые JS-команды теста.
    wait_for(
        qtbot,
        lambda: (
            widget.bridge_initialized
            and run_js(widget, 'String(window.qtmonaco.initialized)') == 'true'
        ),
        timeout=30.0,
        poll=0.5,
        message='мост QWebChannel (bridge_initialized) не поднялся',
    )
    qtbot.wait(300)


def editor_option(widget: QtMonacoEditorWidget, option: str) -> Any:
    """Значение опции редактора строкой (строго — булевы теряются PySide6)."""
    script = (
        "String(window.qtmonaco.editor.getOption("
        f"window.qtmonaco.monaco.editor.EditorOption.{option}))"
    )
    return run_js(widget, script)


def trigger_command(widget: QtMonacoEditorWidget, command: str) -> None:
    """Вызывает команду Monaco извне (тот же путь, что у клавиатурных связок)."""
    run_js(
        widget,
        "window.qtmonaco.editor.trigger('test',"
        f" '{command}', {{}}); 'TRIGGERED'",
    )


def editor_position_line(widget: QtMonacoEditorWidget) -> Any:
    position = json_from_js(
        run_js(widget, 'JSON.stringify(window.qtmonaco.editor.getPosition())')
    )
    if not isinstance(position, dict):
        return None
    return position.get('lineNumber')


# Вычисляемый фон холста — надёжный маркер активной темы Monaco: в бандле
# 0.55 нет getActiveTheme/onDidThemeChange/EditorOption.theme (пробовано),
# а цвета тем фиксированы: vs-dark #1e1e1e, vs #fffffe
JS_EDITOR_BG = (
    "(function(){var el=document.querySelector('.monaco-editor "
    ".monaco-editor-background');"
    "return el?getComputedStyle(el).backgroundColor:'NO_EL';})()"
)
BG_VS_DARK = 'rgb(30, 30, 30)'
BG_VS = 'rgb(255, 255, 254)'


# ------------------------------- фикстура ----------------------------------


@pytest.fixture
def bare_editor(
    qapp: QApplication, tmp_path: Path, request: pytest.FixtureRequest,
) -> Iterator[QtMonacoEditorWidget]:
    """Редактор без LSP (``lsp_host=[]``): файл пишется в tmp_path.

    Текст файла берётся из маркера ``request.param`` (по умолчанию —
    четырёхстрочный python).
    """
    text: str = getattr(request, 'param', 'def foo():\n    return 1\n\nfoo()\n')
    path = tmp_path / 'ux_file.py'
    path.write_text(text, encoding='utf-8')
    widget = QtMonacoEditorWidget(path, lsp_host=[])
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


# -------------------------------- тесты ------------------------------------


def test_stage7_defaults_in_js(bare_editor, qtbot) -> None:
    """Дефолты из defaultEditorOptions (этап 7): multiCursorModifier=alt,
    scrollBeyondLastLine=false."""
    wait_editor_ready(qtbot, bare_editor)

    assert editor_option(bare_editor, 'scrollBeyondLastLine') == 'false'
    # MultiCursorModifier — строковый enum Monaco ('ctrlKey' | 'altKey'),
    # ждём alt (как в VS Code)
    assert editor_option(bare_editor, 'multiCursorModifier') == 'altKey'
    # NB: autoDetectHighContrast:false (тема редактора) в рантайме не
    # проверяем — ключа нет в публичном enum EditorOption (monaco 0.55)


def test_stage7_keybindings_registered(bare_editor, qtbot) -> None:
    """Связки этапа 7 зарегистрированы в keybinding-сервисе редактора:
    зум шрифта, smart select (ctrl+Shift+./,), история переходов (alt+стрелки).
    """
    wait_editor_ready(qtbot, bare_editor)
    raw = json_from_js(run_js(bare_editor, """
(() => {
  const svc = window.qtmonaco.editor._standaloneKeybindingService;
  if (!svc) return 'NO_KB_SERVICE';
  const wanted = ['editor.action.fontZoomIn', 'editor.action.fontZoomOut',
    'editor.action.fontZoomReset', 'editor.action.smartSelect.expand',
    'editor.action.smartSelect.shrink', 'qtmonaco.editor.navigation.goBack',
    'qtmonaco.editor.navigation.goForward'];
  const out = {};
  for (const item of svc.getKeybindings()) {
    if (item.command && wanted.indexOf(item.command) >= 0) {
      out[item.command] = out[item.command] || [];
      out[item.command].push((item.chords || []).join('+'));
    }
  }
  return JSON.stringify(out);
})()
"""))
    assert isinstance(raw, dict), raw
    # наши новые правила (ctrl+= / ctrl+Shift+. / alt+Arrow) присутствуют
    assert any('=' in c for c in raw.get('editor.action.fontZoomIn', [])), raw
    assert any('.' in c for c in raw.get('editor.action.smartSelect.expand', [])), raw
    assert any(',' in c for c in raw.get('editor.action.smartSelect.shrink', [])), raw
    assert any('LeftArrow' in c for c in raw.get('qtmonaco.editor.navigation.goBack', [])), raw
    assert any('RightArrow' in c for c in raw.get('qtmonaco.editor.navigation.goForward', [])), raw


def test_stage7_options_from_python(bare_editor, qtbot) -> None:
    """update_editor_options до готовности буферизуется Connector'ом и
    применяется; повторная отправка после готовности меняет живой редактор."""
    bare_editor.update_editor_options({
        'minimap': {'enabled': False},
        'fontSize': 20,
        'mouseWheelZoom': True,
        'folding': True,
    })
    wait_editor_ready(qtbot, bare_editor)

    assert editor_option(bare_editor, 'fontSize') == '20'
    assert editor_option(bare_editor, 'mouseWheelZoom') == 'true'
    minimap_enabled = run_js(
        bare_editor,
        "String(window.qtmonaco.editor.getOption("
        "window.qtmonaco.monaco.editor.EditorOption.minimap).enabled)",
    )
    assert minimap_enabled == 'false'

    # живое применение (путь страницы настроек — broadcast в открытые редакторы)
    bare_editor.update_editor_options({'minimap': {'enabled': True}})
    wait_for(
        qtbot,
        lambda: run_js(
            bare_editor,
            "String(window.qtmonaco.editor.getOption("
            "window.qtmonaco.monaco.editor.EditorOption.minimap).enabled)",
        ) == 'true',
        message='minimap не включился живым сообщением update_editor_options',
    )


def test_stage7_find_replace(bare_editor, qtbot) -> None:
    """Поиск и замена: команды связок Ctrl+F/Ctrl+H открывают find-виджет,
    команда замены дополнительно включает pane замены."""
    wait_editor_ready(qtbot, bare_editor)

    JS_FIND_STATE = """
(() => {
  const w = document.querySelector('.find-widget');
  if (!w) return 'NO_WIDGET';
  return 'visible:' + w.classList.contains('visible')
    + ' replace:' + w.classList.contains('replaceToggled');
})()
"""
    trigger_command(bare_editor, 'actions.find')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_FIND_STATE) == 'visible:true replace:false',
        timeout=15.0,
        message='find-виджет не открылся по actions.find',
    )

    trigger_command(bare_editor, 'editor.action.startFindReplaceAction')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_FIND_STATE) == 'visible:true replace:true',
        timeout=15.0,
        message='замена не открылась по editor.action.startFindReplaceAction',
    )


# folding в standalone: у python без LSP синтаксического провайдера нет,
# Monaco откатывается на индент-фолды (foldingStrategy 'auto') — на них и
# проверяем механику сворачивания (файл фикстуры — python)
PY_FOLDS_SRC = (
    'class A:\n'
    '    def one(self):\n'
    '        return 1\n'
    '\n'
    '    def two(self):\n'
    '        return 2\n'
    '\n'
    'x = 1\n'
)

JS_FOLD_KICK = """
(() => {
  const c = window.qtmonaco.editor.getContribution('editor.contrib.folding');
  if (!c) { window.__fold = {model: false}; return 'KICK'; }
  window.__fold = null;
  Promise.resolve(c.getFoldingModel()).then((m) => {
    if (!m) { window.__fold = {model: false}; return; }
    const r = m.regions;
    let collapsed = 0;
    for (let i = 0; i < r.length; i++) {
      if (r.isCollapsed(i)) collapsed++;
    }
    window.__fold = {model: true, n: r.length, collapsed: collapsed};
  }, () => { window.__fold = {error: true}; });
  return 'KICK';
})()
"""


def _fold_state(widget: QtMonacoEditorWidget, qtbot: Any) -> dict | None:
    """Снимок фолдов: kick промиса вычисления + чтение окна ``__fold``."""
    run_js(widget, JS_FOLD_KICK)
    qtbot.wait(400)
    state = json_from_js(run_js(widget, 'JSON.stringify(window.__fold)'))
    if not isinstance(state, dict) or not state.get('model'):
        return None
    return state


def test_stage7_folding(bare_editor, qtbot) -> None:
    """Сворачивание блоков: фолды вычисляются, foldAll/unfoldAll работают."""
    wait_editor_ready(qtbot, bare_editor)

    # setValue внутри предиката — стартовый буфер Connector (set_text файла)
    # долетает до JS асинхронно и может перетереть одиночную установку текста
    def load_text() -> bool:
        run_js(
            bare_editor,
            'window.qtmonaco.editor.getModel().setValue('
            + json.dumps(PY_FOLDS_SRC) + ')',
        )
        # 8 строк + пустая строка после завершающего перевода
        return run_js(
            bare_editor,
            'String(window.qtmonaco.editor.getModel().getLineCount())',
        ) == '9'

    wait_for(qtbot, load_text, timeout=30.0, poll=0.5,
             message='текст с блоками не лёг в модель')

    def folds() -> dict | None:
        return _fold_state(bare_editor, qtbot)

    wait_for(
        qtbot,
        lambda: bool((state := folds()) and state.get('n', 0) > 0),
        timeout=30.0,
        poll=1.0,
        message='folding так и не вычислил индент-фолды',
    )

    trigger_command(bare_editor, 'editor.foldAll')
    wait_for(
        qtbot,
        lambda: (
            (state := folds()) is not None
            and state.get('n', 0) > 0
            and state.get('collapsed') == state.get('n')
        ),
        timeout=15.0,
        message='editor.foldAll не свернул фолды',
    )
    # свёрнутый блок скрывает строки из отрисовки
    visible_lines = run_js(
        bare_editor,
        "String(document.querySelectorAll('.view-lines .view-line').length)",
    )
    assert visible_lines is not None and int(visible_lines) < 8

    trigger_command(bare_editor, 'editor.unfoldAll')
    wait_for(
        qtbot,
        lambda: (
            (state := folds()) is not None and state.get('collapsed') == 0
        ),
        timeout=15.0,
        message='editor.unfoldAll не развернул фолды',
    )


def test_stage7_smart_select(bare_editor, qtbot) -> None:
    """Умное выделение (команды из новых связок ctrl+Shift+./,): расширение
    сворачивается в выделение слова 'foo' и обратно."""
    wait_editor_ready(qtbot, bare_editor)

    place = run_js(
        bare_editor,
        "(() => { const ed = window.qtmonaco.editor; ed.focus();"
        ' ed.setPosition({ lineNumber: 1, column: 5 }); return "PLACED"; })()',
    )
    assert place == 'PLACED'

    JS_SELECTION_TEXT = """
JSON.stringify({ text: window.qtmonaco.editor.getModel().getValueInRange(
  window.qtmonaco.editor.getSelection()) })
"""

    trigger_command(bare_editor, 'editor.action.smartSelect.expand')
    data = json_from_js(run_js(bare_editor, JS_SELECTION_TEXT))
    assert isinstance(data, dict)
    assert str(data.get('text', '')).strip() == 'foo', data

    trigger_command(bare_editor, 'editor.action.smartSelect.shrink')
    data = json_from_js(run_js(bare_editor, JS_SELECTION_TEXT))
    assert isinstance(data, dict)
    assert len(str(data.get('text', ''))) <= 1, data


def test_stage7_navigation_history(bare_editor, qtbot) -> None:
    """Своя история переходов: крупные прыжки записываются, goBack/goForward
    ходят по стеку (команды qtmonaco.editor.navigation.* — тот же id,
    что у связок alt+стрелки)."""
    wait_editor_ready(qtbot, bare_editor)

    big = ''.join(f'line {i}\n' for i in range(1, 41))

    # setValue внутри предиката — на случай позднего старта LSP-догружки/буфера
    def load_text() -> bool:
        run_js(
            bare_editor,
            'window.qtmonaco.editor.getModel().setValue(' + json.dumps(big) + ')',
        )
        # 40 строк + пустая строка после завершающего перевода
        return run_js(
            bare_editor,
            'String(window.qtmonaco.editor.getModel().getLineCount())',
        ) == '41'

    wait_for(qtbot, load_text, timeout=30.0, poll=0.5,
             message='текст 40 строк не лёг в модель')

    def place(line: int) -> None:
        assert run_js(
            bare_editor,
            '(() => { const ed = window.qtmonaco.editor; ed.focus();'
            f' ed.setPosition({{ lineNumber: {line}, column: 1 }}); return "OK";'
            ' })()',
        ) == 'OK'

    # маршрут каретки 1 -> 2 (мелкий сдвиг, не пишется) -> 30 (пишет (2,1))
    # -> 10 (пишет (30,1)); back: 30, 2; forward обратно: 30, затем 10
    place(2)
    place(30)
    place(10)

    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goBack')
    assert editor_position_line(bare_editor) == 30
    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goBack')
    assert editor_position_line(bare_editor) == 2

    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goForward')
    assert editor_position_line(bare_editor) == 30
    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goForward')
    assert editor_position_line(bare_editor) == 10


def test_stage7_navigation_first_jump_recorded(bare_editor, qtbot) -> None:
    """Регресс (живая проверка IDE): первый крупный переход после загрузки текста
    тоже записывается — стартовая позиция каретки сидит в ``navLastPos``
    (``seedNavHistory`` после подстановки модели/редактора), иначе Alt+← после
    первого же jump молчит (так и было: jump в открытый документ -> back без эффекта).
    """
    wait_editor_ready(qtbot, bare_editor)

    big = ''.join(f'line {i}\n' for i in range(1, 41))

    def load_text() -> bool:
        run_js(
            bare_editor,
            'window.qtmonaco.editor.getModel().setValue(' + json.dumps(big) + ')',
        )
        return run_js(
            bare_editor,
            'String(window.qtmonaco.editor.getModel().getLineCount())',
        ) == '41'

    wait_for(qtbot, load_text, timeout=30.0, poll=0.5,
             message='текст 40 строк не лёг в модель')

    # единственный прыжок в истории: (1,1) -> (30,1); back должен вернуть на 1
    assert run_js(
        bare_editor,
        '(() => { const ed = window.qtmonaco.editor; ed.focus();'
        ' ed.setPosition({ lineNumber: 30, column: 1 }); return "OK"; })()',
    ) == 'OK'
    assert editor_position_line(bare_editor) == 30

    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goBack')
    assert editor_position_line(bare_editor) == 1

    trigger_command(bare_editor, 'qtmonaco.editor.navigation.goForward')
    assert editor_position_line(bare_editor) == 30


def test_stage7_font_zoom_commands(bare_editor, qtbot) -> None:
    """Зум шрифта (команды из новых связок ctrl+= / ctrl+- / ctrl+0):
    эффективный размер шрифта редактора меняется."""
    wait_editor_ready(qtbot, bare_editor)

    def font_size() -> Any:
        # EditorZoom — глобальный на JS-мир синглтон (его задевают редакторы из
        # других тестов общего процесса), а level в API редактора не читается;
        # зум применяется к вычисляемой опции fontInfo (fontSize *=
        # 1 + 0.1*zoomLevel) — её и проверяем
        return run_js(
            bare_editor,
            'String(window.qtmonaco.editor.getOption('
            'window.qtmonaco.monaco.editor.EditorOption.fontInfo).fontSize)',
        )

    # fontZoomIn/Out двигают level на 1 => fontSize на 10% за шаг
    base = float(font_size())
    assert base > 0, font_size
    prev = base
    for command in (
        'editor.action.fontZoomIn',
        'editor.action.fontZoomIn',
        'editor.action.fontZoomOut',
    ):
        trigger_command(bare_editor, command)
        current = float(font_size())
        if command == 'editor.action.fontZoomOut':
            assert current < prev, command
        else:
            assert current > prev, command
        prev = current

    trigger_command(bare_editor, 'editor.action.fontZoomReset')
    assert float(font_size()) == pytest.approx(base)


def test_editor_theme_light_dark(bare_editor, qtbot) -> None:
    """Тема Monaco из Python (``set_theme`` = путь DocumentManager): старт
    с vs-dark, переключение в светлую и обратно меняет фон холста."""
    wait_editor_ready(qtbot, bare_editor)

    # JS-дефолт defaultEditorOptions — vs-dark (Python тему ещё не применял)
    assert run_js(bare_editor, JS_EDITOR_BG) == BG_VS_DARK

    bare_editor.set_theme('vs')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_EDITOR_BG) == BG_VS,
        message='светлая тема vs не применилась живым сообщением theme',
    )
    assert bare_editor.get_theme() == 'vs'

    bare_editor.set_theme('vs-dark')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_EDITOR_BG) == BG_VS_DARK,
        message='тема vs-dark не вернулась',
    )


def test_editor_theme_survives_diff_mode(bare_editor, qtbot) -> None:
    """vendored-патч appliedTheme: тема не теряется при пересоздании
    редактора — diff-режим наследует её, возврат к standalone (set_text)
    сохраняет."""
    wait_editor_ready(qtbot, bare_editor)

    bare_editor.set_theme('vs')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_EDITOR_BG) == BG_VS,
        message='светлая тема не стала базовой для теста diff',
    )

    bare_editor.open_diffs(original='a = 1\n', modified='a = 2\n')
    qtbot.wait(500)

    # возврат к standalone: case set_text -> switchToStandaloneEditor,
    # редактор пересоздаётся — без патча тема откатилась бы к vs-dark
    bare_editor.set_text('def foo():\n    return 1\n')
    wait_for(
        qtbot,
        lambda: run_js(bare_editor, JS_EDITOR_BG) == BG_VS,
        message='тема потерялась после возврата из diff-режима',
    )
