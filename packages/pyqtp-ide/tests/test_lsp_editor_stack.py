"""Живые тесты редактора: WebEngine + Monaco + реальные LSP-серверы.

Перенос харнесов /tmp/opencode/smoke_diag.py, smoke_stack.py, smoke_cpp.py.
Цепочки QTimer заменены на последовательные опросы (``wait_for`` +
синхронный ``run_js``). Помечены маркерами ``live`` и ``webengine`` — по
умолчанию не запускаются (см. addopts в корневом pyproject.toml).

Две особенности окружения, учтённые в помощниках ниже:

* ``run_js`` — синхронный wrapper над асинхронным ``runJavaScript``: крутит
  локальный Qt event loop до возврата значения, что позволяет писать
  линейные тесты вместо цепочек QTimer из исходных харнесов;
* PySide6 (6.6) возвращает JS-булевы результаты пустой строкой, поэтому все
  JS-предикаты в тестах возвращают строковые маркеры (``'READY'`` и т. п.),
  а условия проверяются на стороне Python.
"""

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import qtmonaco
from PySide6.QtCore import QEventLoop, QTimer
from qtmonaco import Monaco

# js_build не лежит в git — без него редактор не поднимется
JS_BUILD_OK = (Path(qtmonaco.__file__).parent / 'js_build' / 'index.html').is_file()

pytestmark = [
    pytest.mark.live,
    pytest.mark.webengine,
    pytest.mark.skipif(
        not JS_BUILD_OK, reason='qtmonaco/js_build не собран (npm run build)',
    ),
]

JS_READY = "window.qtmonaco && window.qtmonaco.editor ? 'READY' : 'NOT_READY'"

JS_MARKERS = (
    'JSON.stringify(window.qtmonaco.monaco.editor.getModelMarkers({}).map('
    'm => ({owner: m.owner, severity: m.severity, message: m.message, '
    'code: m.code ?? null, source: m.source ?? null, line: m.startLineNumber})))'
)


# ------------------------------ помощники ---------------------------------


def run_js(widget: Monaco, script: str, *, timeout: float = 20.0) -> Any:
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


def read_markers(widget: Monaco) -> list[dict]:
    """Модельные маркеры Monaco (диагностики от всех серверов стека)."""
    return json_from_js(run_js(widget, JS_MARKERS)) or []


def wait_for(
    qtbot,
    predicate: Any,
    *,
    timeout: float = 60.0,
    poll: float = 0.5,
    message: str = '',
) -> None:
    """Опрашивает ``predicate`` с интервалом ``poll`` секунд до ``timeout``.

    ``qtbot.wait`` между попытками крутит событийный цикл (QtWebEngine и
    LSP-поток хаба продолжают работать). Собственный цикл вместо
    ``qtbot.waitUntil`` — чтобы задавать разумный интервал: дорогие предикаты
    делают JS-запросы и ждут ответов серверов.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(message or 'условие не выполнилось за таймаут')
        qtbot.wait(int(poll * 1000))


def wait_editor_ready(qtbot, widget: Monaco) -> None:
    """Ждёт готовности Monaco в странице редактора (до 2 минут)."""
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_READY) == 'READY',
        timeout=120.0,
        poll=1.0,
        message='Monaco в странице редактора так и не поднялся',
    )
    # ещё чуть-чуть на установку ws-подключений стека к хабу
    qtbot.wait(1500)


# ------------------------------- тесты ------------------------------------

# 8 == monaco MarkerSeverity.Error

JS_SUGGEST = """
(() => {
  const ed = window.qtmonaco.editor;
  const c = ed.getContribution('editor.contrib.suggestController');
  if (!c) return 'NO_CONTRIB';
  const lazy = c.widget;
  if (!lazy || !lazy.isInitialized) return 'WIDGET_NOT_CREATED';
  const sw = lazy.value;
  const model = sw._completionModel;
  const items = model ? model.items : [];
  return JSON.stringify({
    items: items.length,
    labels: items.slice(0, 120).map((s) => String(s.completion.label)),
  });
})()
"""

JS_POS = 'JSON.stringify(window.qtmonaco.editor.getPosition())'
JS_VALUE = 'window.qtmonaco.editor.getValue()'
JS_ACCEPT = "window.qtmonaco.editor.trigger('test', 'acceptSelectedSuggestion', {})"


def _focus_and_suggest(widget, line: int) -> None:
    """Ставит каретку в конец ``line``-й строки модели и зовёт suggest."""
    script = f"""
(() => {{
  const ed = window.qtmonaco.editor;
  ed.focus();
  const model = ed.getModel();
  const last = model.getLineContent({line});
  ed.setPosition({{ lineNumber: {line}, column: last.length + 1 }});
  ed.trigger('test', 'editor.action.triggerSuggest', {{}});
  return 'TRIGGERED';
}})()
"""
    assert run_js(widget, script) == 'TRIGGERED'


def _suggest_labels(widget) -> dict:
    raw = run_js(widget, JS_SUGGEST)
    data = json_from_js(raw)
    return data if isinstance(data, dict) else {'items': 0, 'labels': []}


def _wait_markers(
    qtbot, widget, predicate, *,
    timeout: float = 60.0, keep: bool = True, message: str = 'маркеры так и не появились',
) -> list[dict]:
    """Копит маркеры и ждёт, пока ``predicate`` исполнится над ними.

    ``keep=False`` — предикат проверяет актуальный снимок (например «маркеров
    больше нет»), а не накопленный за проходы список.
    """
    collected: dict[str, list[dict]] = {'markers': []}

    def _poll() -> bool:
        current = read_markers(widget)
        if current or not keep:
            collected['markers'] = current
        return bool(predicate(collected['markers']))

    wait_for(qtbot, _poll, timeout=timeout, poll=1.0, message=message)
    return collected['markers']


# --- smoke_diag: диагностики basedpyright через LspHub -> маркеры Monaco ---


def test_diagnostics_lifecycle(editor_pair, qtbot) -> None:
    _hub, widget = editor_pair('import os\nprint(os.getcwd())\n')
    wait_editor_ready(qtbot, widget)

    def bp(markers: list[dict]) -> list[dict]:
        return [m for m in markers if m.get('source') == 'basedpyright']

    # фаза 1: даём стеку устояться — на чистом файле маркеров быть не должно
    qtbot.wait(6000)
    assert not bp(read_markers(widget))

    # фаза 2: вносим ошибку -> дожидаемся error-маркера
    widget.insert_text('x = undefined_call(\n')
    markers = _wait_markers(
        qtbot,
        widget,
        lambda ms: any(m.get('severity') == 8 for m in bp(ms)),
        message='basedpyright не выдал ошибку по undefined_call',
    )
    assert any('undefined_call' in (m.get('message') or '') for m in bp(markers))

    # фаза 3: перезаписываем файл корректно (тот же uri -> didChange) — маркеры уходят
    widget.set_text(
        'import os\nprint(os.getcwd())\nx = os.path.join(os.curdir, "a")\n',
    )
    _wait_markers(
        qtbot,
        widget,
        lambda ms: not bp(ms),
        timeout=30.0,
        keep=False,
    )


# --- smoke_stack: стек basedpyright + ruff на одном редакторе ---

JS_COMPOSITE_ACCEPT = """
(() => {
  const ed = window.qtmonaco.editor;
  ed.getModel().setValue('import os\\nos.lis\\n');
  return 'COMPOSITE';
})()
"""


def test_python_stack_diagnostics_and_completion(editor_pair, qtbot) -> None:
    hub, widget = editor_pair(
        'import os\n'
        'print(os.listdir("."))\n'
        'x = undefined_name\n'
        '\n'
        'def f() -> int:\n'
        '    return "строка"\n'
    )
    wait_editor_ready(qtbot, widget)

    def bp(ms: list[dict]) -> list[dict]:
        return [m for m in ms if m.get('owner') == 'basedpyright']

    def rf(ms: list[dict]) -> list[dict]:
        return [m for m in ms if m.get('owner') == 'ruff']

    # фаза 1: маркеры от двух серверов с разными owner
    markers = _wait_markers(
        qtbot, widget, lambda ms: bool(bp(ms)) and bool(rf(ms)), timeout=90.0,
    )
    assert any(m.get('severity') == 8 for m in bp(markers))  # F821/ошибка типа
    assert any(
        m.get('code') == 'F821' or m.get('source') == 'ruff' for m in rf(markers)
    )

    # пул процессов: оба сервера заведены под общий корень
    pool = {key[0] for key in hub._instances}
    assert 'basedpyright-langserver --stdio' in pool
    assert 'ruff server' in pool

    # фаза 2: basedpyright-сайджест по os.lis -> принять listdir
    def _completion_round() -> bool:
        run_js(widget, JS_COMPOSITE_ACCEPT)
        qtbot.wait(1500)  # флеш дебаунса didChange (300 мс) + переработка серверами
        _focus_and_suggest(widget, 2)
        qtbot.wait(3000)
        data = _suggest_labels(widget)
        labels = [str(label) for label in data.get('labels', [])]
        if not (data.get('items', 0) > 0
                and any(lab.lower().startswith('listdir') for lab in labels)):
            run_js(widget, JS_ACCEPT)
            qtbot.wait(500)
            return False
        run_js(widget, JS_ACCEPT)
        value = run_js(widget, JS_VALUE) or ''
        return 'listdir' in str(value)

    wait_for(
        qtbot,
        _completion_round,
        timeout=180.0,
        poll=2.0,
        message='basedpyright не дал listdir в completion на стеке',
    )


# --- smoke_cpp: clangd (диагностика, переходы, completion) ---

CPP_SOURCE = (
    '#include <string>\n'
    '\n'
    'int add(int a, int b) { return a + b; }\n'
    '\n'
    'int main() {\n'
    '    int r = add(2, 3);\n'
    '    std::string s = "abc";\n'
    '    nosuchfunction(r);\n'
    '    return static_cast<int>(s.size());\n'
    '}\n'
)

JS_GOTO = """
(() => {
  const ed = window.qtmonaco.editor;
  ed.focus();
  ed.setPosition({ lineNumber: 6, column: 14 });
  ed.trigger('test', 'editor.action.revealDefinition', {});
  return 'GOTO_TRIGGERED';
})()
"""

JS_CPP_SET_COMPLETION = """
(() => {
  window.qtmonaco.editor.getModel().setValue(
    '#include <string>\\nint main() {\\n    std::string s = "abc";\\n    s.si\\n}');
  return 'SET_VALUE';
})()
"""


def test_clangd_diagnostics_goto_completion(editor_pair, qtbot) -> None:
    if not shutil.which('clangd'):
        pytest.skip('clangd не найден в PATH')
    hub, widget = editor_pair(CPP_SOURCE, suffix='.cpp')
    wait_editor_ready(qtbot, widget)

    assert run_js(widget, 'window.qtmonaco.editor.getModel().getLanguageId()') == 'cpp'

    # фаза 1: диагностика clangd про несуществующую функцию
    def cd(ms: list[dict]) -> list[dict]:
        return [m for m in ms if m.get('owner') == 'clangd']

    _wait_markers(
        qtbot,
        widget,
        lambda ms: any(
            m.get('severity') == 8 and 'nosuchfunction' in (m.get('message') or '')
            for m in cd(ms)
        ),
        timeout=150.0,
        message='clangd не сообщил про nosuchfunction',
    )

    # фаза 2: переход на определение add() (строка 3 исходного файла)
    assert run_js(widget, JS_GOTO) == 'GOTO_TRIGGERED'

    def _on_definition() -> bool:
        pos = json_from_js(run_js(widget, JS_POS)) or {}
        return bool(isinstance(pos, dict) and pos.get('lineNumber') == 3)

    wait_for(
        qtbot,
        _on_definition,
        timeout=40.0,
        poll=1.0,
        message='каретка не встала на определение add()',
    )

    # фаза 3: completion членов std::string (s.si -> size())
    def _cpp_completion_round() -> bool:
        run_js(widget, JS_CPP_SET_COMPLETION)
        qtbot.wait(2500)  # дебаунс didChange + переиндексация clangd
        _focus_and_suggest(widget, 4)
        qtbot.wait(5000)
        data = _suggest_labels(widget)
        # clangd кладёт сигнатуру прямо в label (' size() -> size_type')
        return data.get('items', 0) > 0 and any(
            str(label).strip().lower().startswith('size')
            for label in data.get('labels', [])
        )

    wait_for(
        qtbot,
        _cpp_completion_round,
        timeout=180.0,
        poll=3.0,
        message='clangd не дал size() в completion по s.si',
    )

    # пул процессов: clangd заведен
    assert any('clangd' in cmd for cmd in {key[0] for key in hub._instances})


# --- semanticTokens (этап 5): full-токены basedpyright -> раскраска view DOM ---

# стек из одного basedpyright — единственный претендент на единственный
# semanticTokens-провайдер языка (claimProvider), без гонок с ruff
BP_ONLY_REGISTRY = json.dumps({
    'python': {
        'extensions': ['.py', '.pyi'],
        'servers': [{
            'name': 'basedpyright',
            'command': ['basedpyright-langserver', '--stdio'],
        }],
    },
})

SEM_SOURCE = 'si = []\nsi.apend(1)\nprint(si.lenght)\n'

# 'si' как semantic-'variable' красится правилом 'variable' темы vs-dark
# (#74B0DF); синтаксически идентификаторы в Monaco — дефолтный foreground
# (#D4D4D4). Ищем span с текстом 'si', покрашенный именно в цвет переменной.
JS_SEM_VARIABLE_BLUE = """
(() => {
  const lines = document.querySelectorAll('.view-lines .view-line');
  if (!lines.length) return 'NO_LINES';
  for (const line of lines) {
    for (const span of line.querySelectorAll('span')) {
      if (span.children.length || !span.textContent) continue;
      if (span.textContent.trim() !== 'si') continue;
      if (getComputedStyle(span).color === 'rgb(116, 176, 223)') return 'SEM_OK';
    }
  }
  return 'NOT_YET';
})()
"""


def test_semantic_tokens_highlighting(editor_pair, qtbot) -> None:
    """Семантическая подсветка: capabilities хаба -> провайдер JS -> Monaco."""
    hub, widget = editor_pair(
        SEM_SOURCE, registry_json=BP_ONLY_REGISTRY,
    )
    wait_editor_ready(qtbot, widget)

    wait_for(
        qtbot,
        lambda: run_js(widget, JS_SEM_VARIABLE_BLUE) == 'SEM_OK',
        timeout=90.0,
        poll=1.0,
        message='семантическая раскраска basedpyright так и не появилась в DOM',
    )

    # пул процессов: basedpyright заведен (запрос full шёл через хаб)
    assert any(
        'basedpyright' in cmd for cmd in {key[0] for key in hub._instances}
    )


# --- междокументный переход к определению (этап 6) ---

HELPER_SRC = 'def finx():\n    return 1\n'
CROSS_SRC = 'from helper import finx\n\nfinx()\n'

# каретка на 'finx' в вызове (строка 3) -> revealDefinition: локация в helper.py
JS_GOTO_CROSS = """
(() => {
  const ed = window.qtmonaco.editor;
  ed.focus();
  ed.setPosition({ lineNumber: 3, column: 1 });
  ed.trigger('test', 'editor.action.revealDefinition', {});
  return 'GOTO_TRIGGERED';
})()
"""


def test_cross_document_definition_open_request(editor_pair, qtbot) -> None:
    """F12 на импортированном символе: JS-opener -> open_uri_requested.

    Цепочка: basedpyright отдаёт Location в helper.py -> Monaco зовёт opener
    (ресурс вне текущей модели) -> `_open_uri_request` в Python -> сигнал
    ``open_uri_requested``. Дальше документ открывает DocumentManager IDE —
    здесь проверяется только сам запрос (uri + диапазон Monaco, 1-based).
    """
    hub, widget = editor_pair(
        CROSS_SRC,
        registry_json=BP_ONLY_REGISTRY,
        extra_files={'helper.py': HELPER_SRC},
    )
    wait_editor_ready(qtbot, widget)

    hits: list[dict] = []
    widget.open_uri_requested.connect(hits.append)

    def _goto_round() -> bool:
        run_js(widget, JS_GOTO_CROSS)
        qtbot.wait(2000)  # basedpyright lazily индексирует helper при запросе
        return bool(hits)

    wait_for(
        qtbot,
        _goto_round,
        timeout=90.0,
        poll=1.0,
        message='JS так и не прислал open_uri_requested по F12 в другой файл',
    )

    helper_uri = (widget.file_path.parent / 'helper.py').as_uri()
    payload = hits[-1]
    assert payload.get('uri') == helper_uri, payload
    rng = payload.get('range') or {}
    # 'def finx():' — определение на строке 1, символ 'f' с колонки 5
    assert rng.get('startLineNumber') == 1, payload
    assert rng.get('startColumn') == 5, payload
