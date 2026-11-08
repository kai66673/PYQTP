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
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import qtmonaco
from PySide6.QtCore import QEventLoop, QTimer
from qtmonaco import Monaco

from pyqtp.controls.doc_control.workspace_edit import (
    apply_text_edits_to_text,
    is_text_document_edit,
    normalize_document_changes,
)

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
    // slice: members 'os.' — ~417 пунктов, срез должен покрывать полный список
    labels: items.slice(0, 500).map((s) => String(s.completion.label)),
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


# --- completion по триггерному символу '.' (фикс гонки didChange) ---

JS_DOT_SYNC = """
(() => {
  const ed = window.qtmonaco.editor;
  ed.getModel().setValue('import os\\nos');
  ed.focus();
  ed.setPosition({ lineNumber: 2, column: 3 });
  return 'SYNCED';
})()
"""

JS_DOT_TYPE = """
(() => {
  // source='keyboard' принципиален: codeEditorWidget._type() fire'ит
  // onDidType только при source==='keyboard' (Monaco 0.55), а SuggestModel
  // перезапускает completion по триггерному символу '.' именно по onDidType;
  // trigger('type') с произвольным source реальную клавиатуру НЕ имитирует
  window.qtmonaco.editor.trigger('keyboard', 'type', { text: '.' });
  return 'TYPED';
})()
"""


def test_completion_member_after_typing_dot(editor_pair, qtbot) -> None:
    """Атрибуты модуля при вводе '.': 'os' + набор '.' -> members os.

    Регресс на гонку: Monaco дёргает completion по триггеру '.' быстрее
    дебаунса didChange (300 мс). Без сброса дебаунса (flushDocumentChanges в
    LspClient) запрос уходит серверу с текстом БЕЗ точки — basedpyright
    считает позиции по устаревшему документу и members не отдаёт; больше
    пересобрать список нечему (новых нажатий нет), список остаётся неверным.
    """
    _hub, widget = editor_pair(  # noqa: RUF059 — hub живёт в fixture до конца теста
        'import os\nos\n', registry_json=BP_ONLY_REGISTRY,
    )
    wait_editor_ready(qtbot, widget)

    assert run_js(widget, JS_DOT_SYNC) == 'SYNCED'
    qtbot.wait(3000)  # сервер уже получил 'os' (дебаунс сработал в тишине)

    assert run_js(widget, JS_DOT_TYPE) == 'TYPED'

    def _member_list_ready() -> bool:
        data = _suggest_labels(widget)
        labels = [str(label) for label in data.get('labels', [])]
        return data.get('items', 0) > 0 and any(
            lab.lower().startswith('listdir') for lab in labels
        )

    wait_for(
        qtbot,
        _member_list_ready,
        timeout=45.0,
        poll=1.5,
        message='basedpyright не дал os.listdir при вводе точки (гонка didChange)',
    )
    # список именно members: среди лейблов есть listdir и нет имени модуля os
    labels = [str(label) for label in _suggest_labels(widget).get('labels', [])]
    assert 'os' not in labels, f'похоже на completion не по точке: {labels[:20]}'


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


# --- этап 8.1: references / documentSymbol / documentHighlight (IDE_ROADMAP) ---

# Раскладка строф зафиксирована числами в тестах: 4 calc / 8 shadow / 12
# second — определения; 9-10 локальный x в shadow(); 18 x = calc(1, 2);
# 19 y = calc(3, 4); 20 finx(); 21 print — use-site модульного x
STAGE81_SRC = (
    'from helper import finx\n'
    '\n\n'
    'def calc(a, b):\n'
    '    total = a + b\n'
    '    return total\n'
    '\n'
    'def shadow():\n'
    '    x = 0\n'
    '    return x\n'
    '\n'
    'def second():\n'
    '    t2 = 0\n'
    '    for i in range(5):\n'
    '        t2 += i\n'
    '    return t2\n'
    '\n'
    'x = calc(1, 2)\n'
    'y = calc(3, 4)\n'
    'finx()\n'
    'print(x, y, second(), shadow())\n'
) + '\n# pad\n' * 30


def _trigger(widget, command: str, line: int, col: int) -> None:
    """Каретка на (line, col) + команда через editor.trigger (offscreen-клавиши
    в Monaco не доходят — см. note в доке модуля; паттерн этапа 7)."""
    run_js(
        widget,
        '(() => { const ed = window.qtmonaco.editor; ed.focus();'
        f'  ed.setPosition({{ lineNumber: {line}, column: {col} }});'
        f"  ed.trigger('test', '{command}', {{}}); return 'T'; }})()",
    )


def _refs_controller_dump(widget) -> str:
    """Модель referencesController: refs (файл:строка:колонка) + состояние peek."""
    return run_js(widget, """
(() => {
  try {
    const c = window.qtmonaco.editor.getContribution(
      'editor.contrib.referencesController');
    if (!c || !c._model) return '';
    const m = c._model;
    return JSON.stringify({
      refs: m.references.map(r => ({
        file: (r.uri.path || '').split('/').pop(),
        line: r.range.startLineNumber, col: r.range.startColumn,
      })),
      widget: !!c._widget,
      dom: !!document.querySelector('.reference-zone-widget'),
      rows: document.querySelectorAll(
        '.reference-zone-widget .monaco-list-row').length,
    });
  } catch (e) { return 'ERR: ' + (e && e.message || e); }
})()
""")


def test_stage81_references_peek_and_cross_document(editor_pair, qtbot) -> None:
    """textDocument/references: peek-виджет + переход из peek в чужой файл.

    Цепочка: basedpyright -> провайдер этапа 8.1 -> встроенная команда
    editor.action.goToReferences (связка shift+F12 от contrib'а) -> при 3
    результатах Monaco открывает peek referenceSearch; openReference по
    чужому файлу идёт через opener этапа 6 -> ``open_uri_requested``.
    """
    hub, widget = editor_pair(
        STAGE81_SRC,
        registry_json=BP_ONLY_REGISTRY,
        extra_files={'helper.py': HELPER_SRC},
    )
    wait_editor_ready(qtbot, widget)

    # связка Shift+F12 зарегистрирована (contrib регистрирует её сам, без наших
    # addKeybindingRules — проверяем, что она не потерямась)
    kb = json_from_js(run_js(widget, """
(() => {
  const svc = window.qtmonaco.editor._standaloneKeybindingService;
  if (!svc) return '';
  const out = [];
  for (const item of svc.getKeybindings()) {
    if ((item.command || '').includes('goToReferences')) {
      out.push({command: item.command, chords: (item.chords || []).join('+')});
    }
  }
  return JSON.stringify(out);
})()
""")) or []
    got = {item['command']: item['chords'] for item in kb}
    assert got.get('editor.action.goToReferences', '').lower() == 'shift+f12', got

    hits: list[dict] = []
    widget.open_uri_requested.connect(hits.append)

    # фаза 1: references на 'calc' в x = calc(1, 2) (18,5) -> declaration + 2 use
    _trigger(widget, 'editor.action.goToReferences', 18, 5)

    def _refs_dump() -> dict | None:
        raw = _refs_controller_dump(widget)
        if not isinstance(raw, str) or not raw.startswith('{'):
            return None
        return json.loads(raw)

    holder: dict = {}

    def _refs_ready() -> bool:
        d = _refs_dump()
        if not d or not d.get('refs'):
            return False
        holder.clear()
        holder.update(d)
        return True

    wait_for(
        qtbot,
        _refs_ready,
        timeout=60.0,
        poll=1.0,
        message='referencesController так и не построил модель',
    )
    data = holder
    main_name = widget.file_path.name  # фикстура пишет основной файл test_file.py
    assert all(
        (r['file'], r['line'], r['col']) in {
            (main_name, 4, 5),    # declaration def calc
            (main_name, 18, 5),   # x = calc(1, 2)
            (main_name, 19, 5),   # y = calc(3, 4)
        } for r in data['refs']
    ), data['refs']
    assert len(data['refs']) == 3, data['refs']
    # peek открыт (3 результата -> referenceSearch-виджет в DOM) — ждём
    # досовмещения peek после построения модели
    peek: dict = {}

    def _peek_ready() -> bool:
        d = _refs_dump()
        if not d or not d.get('dom') or not d.get('rows'):
            return False
        peek.clear()
        peek.update(d)
        return True

    wait_for(
        qtbot,
        _peek_ready,
        timeout=30.0,
        poll=1.0,
        message='peek referenceSearch так и не появился в DOM',
    )
    assert peek['widget'], peek
    assert peek['rows'] == 3, peek

    # закрытие peek перед второй фазой
    run_js(widget, """
(() => {
  const c = window.qtmonaco.editor.getContribution(
    'editor.contrib.referencesController');
  if (c && c.closeWidget) c.closeWidget();
  return 'CLOSED';
})()
""")
    qtbot.wait(500)

    # фаза 2: references на 'finx' в вызове (20,1) -> use в main.py + def в helper.py
    _trigger(widget, 'editor.action.goToReferences', 20, 1)

    foreign_box: list[dict] = []

    def _foreign_ready() -> bool:
        d = _refs_dump() or {}
        found = [r for r in d.get('refs', []) if r['file'] == 'helper.py']
        if not found:
            return False
        foreign_box.clear()
        foreign_box.extend(found)
        return True

    wait_for(
        qtbot,
        _foreign_ready,
        timeout=90.0,
        poll=1.0,
        message='basedpyright так и не вернул references в helper.py',
    )
    foreign = foreign_box
    assert (foreign[0]['line'], foreign[0]['col']) == (1, 5), foreign

    # клик по чужому файлу из peek -> opener этапа 6 -> open_uri_requested
    run_js(widget, """
(() => {
  const c = window.qtmonaco.editor.getContribution(
    'editor.contrib.referencesController');
  const ref = c && c._model && c._model.references.find(
    r => (r.uri.path || '').endsWith('helper.py'));
  if (ref && c.openReference) { c.openReference(ref, false, false); return 'OPENED'; }
  return 'NO_REF';
})()
""")
    wait_for(
        qtbot,
        lambda: bool(hits),
        timeout=30.0,
        poll=0.5,
        message='openReference по чужому файлу не породил open_uri_requested',
    )
    helper_uri = (widget.file_path.parent / 'helper.py').as_uri()
    payload = hits[-1]
    assert payload.get('uri') == helper_uri, payload
    rng = payload.get('range') or {}
    # 'def finx():' — 'finx' на строке 1 с колонки 5 по 9
    assert rng.get('startLineNumber') == 1, payload
    assert rng.get('startColumn') == 5, payload
    assert rng.get('endColumn') == 9, payload


def test_stage81_document_symbols_and_sticky_scroll(editor_pair, qtbot) -> None:
    """textDocument/documentSymbol: иерархия символов + sticky scroll из неё.

    Проверка двух уровней: (1) внутренняя команда Monaco ``_executeDocumentSymbolProvider``
    строит outline-модель через наш провайдер (basedpyright отдаёт иерархию,
    SymbolKind LSP=Function(12)/Variable(13) -> Monaco 11/12 со сдвигом -1);
    (2) встроенный sticky scroll (defaultModel=outlineModel) рисует заголовок
    ``def calc`` когда верх вьюпорта внутри тела calc.
    """
    hub, widget = editor_pair(STAGE81_SRC, registry_json=BP_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    run_js(widget, """
(() => {
  const ed = window.qtmonaco.editor;
  const p = ed._commandService.executeCommand(
    '_executeDocumentSymbolProvider', ed.getModel().uri);
  const store = (v) => {
    window.__syms = JSON.stringify((v || []).map(s => ({
      name: s.name, kind: s.kind,
      line: (s.selectionRange || s.range).startLineNumber,
      children: (s.children || []).map(c => c.name),
    })));
  };
  p.then(store, e => { window.__syms = 'ERR:' + (e && e.message || e); });
  return 'PENDING';
})()
""")
    syms: list[dict] = []

    def _syms_ready() -> bool:
        raw = run_js(widget, "window.__syms || ''")
        if not isinstance(raw, str) or not raw:
            return False
        if raw.startswith('ERR'):
            raise AssertionError(raw)
        syms.clear()
        syms.extend(json.loads(raw))
        return True

    wait_for(
        qtbot,
        _syms_ready,
        timeout=60.0,
        poll=1.0,
        message='documentSymbol-провайдер так и не отдал символы документа',
    )
    top = {s['name']: s for s in syms}
    # верхний уровень: три функции + два модульных имени (порядок — из LSP)
    assert {'calc', 'shadow', 'second', 'x', 'y'} <= set(top), syms
    assert top['calc']['kind'] == 11, top['calc']  # monaco Function
    assert top['x']['kind'] == 12, top['x']  # monaco Variable
    assert top['calc']['line'] == 4, top['calc']
    # иерархия: у calc параметры-дети (basedpyright отдаёт children)
    child_names = [c for c in top['calc'].get('children', [])]
    assert 'a' in child_names and 'b' in child_names, top['calc']

    # sticky scroll: каретка в теле calc, вьюпорт ниже заголовка (строка 4
    # уезжает за верх) -> прилипший заголовок 'def calc(a, b):'
    run_js(widget, """
(() => {
  const ed = window.qtmonaco.editor;
  ed.setPosition({ lineNumber: 5, column: 5 });
  ed.setScrollTop(95);
  return 'SCROLLED';
})()
""")
    sticky: list[str] = []

    def _sticky_ready() -> bool:
        raw = run_js(widget, """
(() => {
  const w = document.querySelector('.sticky-widget');
  if (!w) return '';
  return JSON.stringify([...w.querySelectorAll('.sticky-line-content')]
    .map(e => e.textContent.trim()));
})()
""")
        if not isinstance(raw, str) or not raw.startswith('['):
            return False
        sticky.clear()
        # Monaco рисует отступы/пробелы кода через &nbsp; (\xa0)
        sticky.extend(t.replace('\xa0', ' ') for t in json.loads(raw))
        return bool(sticky)

    wait_for(
        qtbot,
        _sticky_ready,
        timeout=30.0,
        poll=1.0,
        message='sticky scroll не показал ни одного прилипшего заголовка',
    )
    assert any('def calc' in t for t in sticky), sticky


def test_stage81_document_highlight_semantic(editor_pair, qtbot) -> None:
    """textDocument/documentHighlight: семантическая подсветка вхождений.

    Каретка на модульном ``x`` (18,1): basedpyright отдаёт write-подсветку
    самого присваивания и read в print (21) — и НЕ трогает локальный ``x``
    в shadow() (9-10). Встроенный текстовый wordHighlighter подсветил бы
    строки 9-10 тоже, поэтому набор строк {18, 21} доказывает, что Monaco
    выбрал наш LSP-провайдер (classes wordHighlightStrong=write /
    wordHighlight=read / wordHighlightText=text).
    """
    hub, widget = editor_pair(STAGE81_SRC, registry_json=BP_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    run_js(widget, """
(() => {
  const ed = window.qtmonaco.editor;
  ed.focus();
  ed.setPosition({ lineNumber: 18, column: 1 });
  return 'POS_SET';
})()
""")
    hl: list[str] = []

    def _hl_ready() -> bool:
        raw = run_js(widget, """
(() => {
  const ed = window.qtmonaco.editor;
  const ds = ed.getModel().getAllDecorations().filter(
    d => d.options && d.options.className
      && d.options.className.includes('wordHighlight'));
  return ds.length
    ? JSON.stringify([...new Set(ds.map(d => d.range.startLineNumber + ':'
        + d.range.startColumn + ':' + d.options.className))])
    : '';
})()
""")
        if not isinstance(raw, str) or not raw.startswith('['):
            return False
        hl.clear()
        hl.extend(json.loads(raw))
        return True

    wait_for(
        qtbot,
        _hl_ready,
        timeout=45.0,
        poll=1.0,
        message='wordHighlight- decorations от LSP-провайдера так и не появились',
    )
    parsed = [e.split(':', 2) for e in hl]
    lines = {int(p[0]) for p in parsed}
    kinds = {int(p[0]): p[2] for p in parsed}
    # только use-site модульного x, без текстовых совпадений в shadow()
    assert lines == {18, 21}, hl
    assert kinds[18] == 'wordHighlightStrong', hl  # write: x = calc(...)
    assert kinds[21] in ('wordHighlight', 'wordHighlightText'), hl  # read: print(x
    assert 9 not in lines and 10 not in lines, hl


# --- этап 8.2: rename / formatting / codeAction + WorkspaceEdit (IDE_ROADMAP) ---

# стек из одного ruff — форматтер только у него (у basedpyright
# documentFormattingProvider нет); single-owner claim «formatting» прозрачен
RUFF_ONLY_REGISTRY = json.dumps({
    'python': {
        'extensions': ['.py', '.pyi'],
        'servers': [{'name': 'ruff', 'command': ['ruff', 'server']}],
    },
})

# --- rename (editor.action.rename, встроенный виджет Monaco) ---

RENAME_SRC = 'def greet():\n    return 1\n\ngreet()\ngreet()\n'
# getValue() сохраняет хвостовой EOL модели (источник кончается '\n')
RENAME_EXPECTED = 'def howdy():\n    return 1\n\nhowdy()\nhowdy()\n'


def _js_rename_trigger(line: int, col: int) -> str:
    """Каретка в (line, col) + вызов команды rename.

    РЕДАКТОР НЕ В ФОКУСЕ: слушатель ``onDidBlurEditorWidget`` виджета rename
    отменяет ввод при ``!ownerDocument.hasFocus()`` — в offscreen QWebEngine
    Qt-фокус у виджета не держится, фокусировка DOM-редактора guarantee-ит
    blur-отмену сразу после открытия виджета.
    """
    return (
        '(() => { const ed = window.qtmonaco.editor;'
        f'  ed.setPosition({{ lineNumber: {line}, column: {col} }});'
        "  ed.trigger('test', 'editor.action.rename', {}); return 'T'; })()"
    )


def _js_rename_probe(expect_prefill: str) -> str:
    """Состояние rename-виджета: 'READY' + предзаполненное имя, 'NOT_YET' иначе."""
    return (
        '(() => {'
        "  const c = window.qtmonaco.editor.getContribution("
        "'editor.contrib.renameController');"
        '  const w = c && c._renameWidget;'
        '  if (!w || !w._currentAcceptInput) return \'NOT_YET\';'
        '  const v = w._inputWithButton.input.value;'
        f"  return v === '{expect_prefill}' ? 'READY' : 'WRONG:' + v; }}"
        ')()'
    )


def _js_rename_accept(new_name: str) -> str:
    """Заменить текст во поле ввода и принять (RenameAction.acceptRenameInput)."""
    return (
        '(() => {'
        "  const c = window.qtmonaco.editor.getContribution("
        "'editor.contrib.renameController');"
        '  const w = c && c._renameWidget;'
        '  if (!w || !w._currentAcceptInput) return \'GONE\';'
        f"  w._inputWithButton.input.value = '{new_name}';"
        '  c.acceptRenameInput(false);'
        "  return 'ACCEPTED'; })()"
    )


def _rename_round(
    widget: Monaco, line: int, col: int, prefill: str, new_name: str,
) -> Any:
    """Один шаг rename: виджет готов -> задать имя и принять; иначе — заново
    поднять виджет. Возвращает 'ACCEPTED' либо '' (нужен ещё заход)."""
    probe = run_js(widget, _js_rename_probe(prefill))
    if probe != 'READY':
        run_js(widget, _js_rename_trigger(line, col))
        return ''
    return run_js(widget, _js_rename_accept(new_name)) == 'ACCEPTED'


def test_stage82_rename_same_document(editor_pair, qtbot) -> None:
    """F2 на своём символе: prepareRename + textDocument/rename, правки в модели.

    basedpyright объявляет ``renameProvider.prepareProvider`` — JS-сторона
    регистрирует ``resolveRenameLocation`` (подсветка переименуемого), сам
    ответ сервера лежит целиком в текущей модели и применяется batch'ем
    Monaco (bulkEditService, одна undo-точка).
    """
    hub, widget = editor_pair(RENAME_SRC, registry_json=BP_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    wait_for(
        qtbot,
        lambda: _rename_round(widget, 1, 5, 'greet', 'howdy'),
        timeout=90.0,
        poll=1.0,
        message='rename-виджет так и не принял новое имя',
    )
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_VALUE) == RENAME_EXPECTED,
        timeout=30.0,
        poll=1.0,
        message='rename не применил правки к модели',
    )
    # вся правка — в текущей модели: basedpyright заведён процессом хаба
    assert any(
        'basedpyright' in cmd for cmd in {key[0] for key in hub._instances}
    )


def test_stage82_rename_cross_document(editor_pair, qtbot) -> None:
    """F2 кросс-документный: своя часть — Monaco, чужая — мост в Python.

    Прайминг F12 нужен, чтобы basedpyright проиндексировал helper.py (ленивая
    программа по импорту из main.py). Ответ сервера — documentChanges в двух
    файлах: main.py (текущая модель) Monaco применяет сам, helper.py уходит
    в Python сигналом ``workspace_edit_requested``; сам тест применяет чужую
    часть чистыми хелперами и проверяет, что диск не тронут.
    """
    hub, widget = editor_pair(
        CROSS_SRC,
        registry_json=BP_ONLY_REGISTRY,
        extra_files={'helper.py': HELPER_SRC},
    )
    wait_editor_ready(qtbot, widget)

    goto_hits: list[dict] = []
    widget.open_uri_requested.connect(goto_hits.append)
    edit_hits: list[dict] = []
    widget.workspace_edit_requested.connect(edit_hits.append)

    # прайминг: revealDefinition на 'finx()' (строка 3) — без ed.focus(),
    # иначе последующий rename-виджет отменится на blur (см. _js_rename_trigger)
    def _goto_round() -> bool:
        run_js(widget, """
(() => {
  const ed = window.qtmonaco.editor;
  ed.setPosition({ lineNumber: 3, column: 1 });
  ed.trigger('test', 'editor.action.revealDefinition', {});
  return 'GOTO_TRIGGERED';
})()
""")
        qtbot.wait(2000)  # basedpyright lazily индексирует helper.py
        return bool(goto_hits)

    wait_for(
        qtbot,
        _goto_round,
        timeout=120.0,
        poll=1.0,
        message='прайминг F12 не проиндексировал helper.py (нет open_uri_requested)',
    )

    wait_for(
        qtbot,
        lambda: _rename_round(widget, 3, 1, 'finx', 'finy'),
        timeout=90.0,
        poll=1.0,
        message='rename-виджет так и не принял новое имя (кросс-документ)',
    )
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_VALUE)
        == 'from helper import finy\n\nfiny()\n',
        timeout=30.0,
        poll=1.0,
        message='локальная часть кросс-документного rename не применилась',
    )

    wait_for(
        qtbot,
        lambda: bool(edit_hits),
        timeout=30.0,
        poll=0.5,
        message='чужая часть WorkspaceEdit не пришла в Python',
    )
    payload = edit_hits[0]
    assert payload.get('origin') == 'rename', payload
    assert payload.get('sourceUri') == widget.file_path.as_uri(), payload
    changes = normalize_document_changes(payload.get('workspaceEdit'))
    helper_uri = (widget.file_path.parent / 'helper.py').as_uri()
    helper_edits = [
        c['edits']
        for c in changes
        if is_text_document_edit(c) and c['textDocument']['uri'] == helper_uri
    ]
    assert helper_edits, payload
    # чужой файл на диске остаётся нетронутым (здесь нет DocumentManager —
    # сам сигнал и есть граница маршрута)
    assert (widget.file_path.parent / 'helper.py').read_text(
        encoding='utf-8'
    ) == HELPER_SRC
    # чистое применение чужой части (тот же код, что в DocumentManager)
    new_helper = apply_text_edits_to_text(HELPER_SRC, helper_edits[0])
    assert new_helper == 'def finy():\n    return 1\n', new_helper


def test_stage82_formatting_document(editor_pair, qtbot) -> None:
    """editor.action.formatDocument: ruff server -> одна undo-точка в модели."""
    fmt_src = 'x=1\nprint( x )\n'
    # getValue() сохраняет хвостовой EOL; ruff отдаёт один whole-document
    # TextEdit с newText 'x = 1\nprint(x)\n' (проверено stdio-пробой)
    fmt_expected = 'x = 1\nprint(x)\n'
    hub, widget = editor_pair(fmt_src, registry_json=RUFF_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    def _fmt_round() -> bool:
        run_js(widget, "(() => { window.qtmonaco.editor.trigger("
        "'test', 'editor.action.formatDocument', {}); return 'F'; })()")
        return run_js(widget, JS_VALUE) == fmt_expected

    wait_for(
        qtbot,
        _fmt_round,
        timeout=90.0,
        poll=2.0,
        message='форматирование ruff не применилось к модели',
    )
    assert any('ruff' in cmd for cmd in {key[0] for key in hub._instances})

    # одна undo-точка: bulkEditService оборачивает правки pushStackElement
    run_js(widget, "(() => { window.qtmonaco.editor.getModel().undo();"
    " return 'U'; })()")
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_VALUE) == fmt_src,
        timeout=30.0,
        poll=1.0,
        message='undo не откатил форматирование целиком (не одна точка?)',
    )


# --- codeAction / quickfix (editor.action.codeAction) ---

CA_SRC = 'print(path)\n'

JS_CODE_ACTION_DUMP = """
(() => {
  const c = window.qtmonaco.editor.getContribution(
    'editor.contrib.codeActionController');
  if (!c) return 'NO_CONTRIB';
  const st = c._activeCodeActions.value;
  if (!st || !Array.isArray(st.allActions)) return '';
  return JSON.stringify(st.allActions.map(i => ({
    title: i.action.title,
    kind: i.action.kind || null,
    hasEdit: !!(i.action.edit && i.action.edit.edits
      && i.action.edit.edits.length),
    cmd: i.action.command ? i.action.command.id : null,
  })));
})()
"""


def _js_code_action(line: int, col: int, apply_mode: str) -> str:
    return (
        '(() => { const ed = window.qtmonaco.editor;'
        f'  ed.setPosition({{ lineNumber: {line}, column: {col} }});'
        "  ed.trigger('test', 'editor.action.codeAction',"
        f"  {{ kind: 'quickfix', apply: '{apply_mode}' }}); return 'T'; }}"
        ')()'
    )


def _ca_undef_marker(widget: Monaco) -> bool:
    return any(
        'not defined' in str(m.get('message', ''))
        for m in read_markers(widget)
    )


def test_stage82_code_action_quickfix(editor_pair, qtbot) -> None:
    """Ctrl+. на undefined-имени: список fixed от basedpyright + применение.

    basedpyright для ``print(path)`` отдаёт (проверено stdio-пробой):
    «from os import path», «from sys import path», две ignore-комменты.
    Сначала ``apply:'never'`` — пулим список без применения, затем
    ``apply:'first'`` — первый фикс применяется в модель, диагностика после
    перезапуска линтера уходит.
    """
    hub, widget = editor_pair(CA_SRC, registry_json=BP_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    foreign: list[dict] = []
    widget.workspace_edit_requested.connect(foreign.append)

    # холодный basedpyright нестабилен в выдаче диагностик — запас по времени
    wait_for(
        qtbot,
        lambda: _ca_undef_marker(widget),
        timeout=180.0,
        poll=2.0,
        message='basedpyright так и не выдал «is not defined» для path',
    )

    run_js(widget, _js_code_action(1, 7, 'never'))
    actions: list[dict] = []

    def _actions_ready() -> bool:
        raw = run_js(widget, JS_CODE_ACTION_DUMP)
        if not isinstance(raw, str) or not raw.startswith('['):
            return False
        actions.clear()
        actions.extend(json.loads(raw))
        return any('import path' in str(a.get('title')) for a in actions)

    wait_for(
        qtbot,
        _actions_ready,
        timeout=90.0,
        poll=2.0,
        message='basedpyright не вернул quickfix-действия для path',
    )
    titles = [str(a.get('title')) for a in actions]
    assert 'from os import path' in titles, titles
    assert 'from sys import path' in titles, titles
    assert any('pyright: ignore' in t for t in titles), titles
    assert all(a.get('kind') == 'quickfix' for a in actions), titles
    # основную часть правок сервер кладёт в edit (провайдер перекладывает её
    # в Monaco-формат), своих команд у pyright в действиях нет
    assert actions[0].get('hasEdit') and not actions[0].get('cmd'), titles
    # 'never' ничего не применил и не дёргал чужую часть
    assert 'import path' not in str(run_js(widget, JS_VALUE))
    assert not foreign

    # применяем первое действие (fixed-порядок сервера -> сверяем по titles[0])
    run_js(widget, _js_code_action(1, 7, 'first'))
    first_title = str(actions[0]['title'])

    def _applied() -> bool:
        val = run_js(widget, JS_VALUE)
        return isinstance(val, str) and val.startswith(first_title)

    wait_for(
        qtbot,
        _applied,
        timeout=60.0,
        poll=2.0,
        message=f'quickfix «{first_title}» не применился к модели',
    )
    # после didChange basedpyright перезапускается — диагностика уходит
    def _diag_gone() -> bool:
        if _ca_undef_marker(widget):
            run_js(widget, _js_code_action(1, 7, 'never'))  # пнуть перезапуск
            return False
        return True

    wait_for(
        qtbot,
        _diag_gone,
        timeout=120.0,
        poll=3.0,
        message='после quickfix диагностика «is not defined» не ушла',
    )
    assert not foreign


# --- прямое применение чужих правок: apply_lsp_text_edits (мост Python -> JS) ---


def test_stage82_apply_lsp_text_edits_direct(editor_pair, qtbot, caplog) -> None:
    """Сообщение ``apply_lsp_text_edits``: правка чужой модели + uri-фартук.

    Так DocumentManager доставляет чужую часть WorkspaceEdit в файл, который
    ОТКРЫТ в другом редакторе: JS проверяет uri модели, правит через
    model.applyEdits (одна undo-точка) и отвечает ``_workspace_edit_applied``;
    на несовпадении uri — ok:false и warning в bcad.med.
    """
    src = 'a = 1\nb = a\n'
    hub, widget = editor_pair(src, registry_json=RUFF_ONLY_REGISTRY)
    wait_editor_ready(qtbot, widget)

    # LSP-координаты (0-based), как их присылает сервер
    edits = [{
        'range': {
            'start': {'line': 1, 'character': 0},
            'end': {'line': 1, 'character': 1},
        },
        'newText': 'c',
    }]
    widget.apply_lsp_text_edits(widget.file_path.as_uri(), edits)
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_VALUE) == 'a = 1\nc = a\n',
        timeout=30.0,
        poll=1.0,
        message='apply_lsp_text_edits не изменил модель',
    )
    run_js(widget, "(() => { window.qtmonaco.editor.getModel().undo();"
    " return 'U'; })()")
    wait_for(
        qtbot,
        lambda: run_js(widget, JS_VALUE) == 'a = 1\nb = a\n',
        timeout=30.0,
        poll=1.0,
        message='undo не откатил apply_lsp_text_edits (не одна точка?)',
    )

    # несовпадение uri: правка не применяется, JS отвечает ok:false,
    # Python пишет warning (bcad.med)
    caplog.set_level(logging.WARNING, logger='bcad.med')
    widget.apply_lsp_text_edits('file:///pyqtp-nonexistent/other.py', edits)
    wait_for(
        qtbot,
        lambda: any(
            'Monaco не применил workspace edit' in r.getMessage()
            for r in caplog.records
        ),
        timeout=30.0,
        poll=1.0,
        message='нет warning на отклонённый uri в apply_lsp_text_edits',
    )
    assert run_js(widget, JS_VALUE) == 'a = 1\nb = a\n'
