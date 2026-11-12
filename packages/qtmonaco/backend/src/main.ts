import * as monaco from "monaco-editor";
import { LspClientManager, type LspStackEntry } from "./lsp";
import { splitWorkspaceEdit } from "./providers";
import { initVimMode, type CMAdapter } from "monaco-vim";

const container = document.getElementById("container");
if (!container) {
  throw new Error("Container element not found");
}
const editorContainer: HTMLElement = container;

const defaultEditorOptions: monaco.editor.IStandaloneEditorConstructionOptions = {
  value: "",
  language: "python",
  automaticLayout: true,
  theme: "vs-dark",
  // vendored-patch (этап 5): у встроенных standalone-тем Monaco флаг
  // semanticHighlighting выключен, поэтому при значении по умолчанию
  // («configuredByTheme») DocumentSemanticTokensFeature не запускается вовсе —
  // включаем семантическую раскраску напрямую в настройке (она попадает в
  // singleton-configuration сервиса редактора, ключ editor.* см.
  // updateConfigurationService в monaco)
  "semanticHighlighting.enabled": true,
  // vendored-patch (этап 7): IDE-поведение по умолчанию, как в VS Code:
  // alt+клик добавляет множественные каретки (ctrl+клик тогда остаётся
  // за переходом к определению), прокрутка не уходит за последнюю строку
  multiCursorModifier: "alt",
  scrollBeyondLastLine: false,
  // vendored-patch (тема редактора): авто-детект высоко-контрастной темы по
  // настройкам ОС перехватывает управление темой (vs/vs-dark молча меняются
  // на hc-*) — выключаем, темой управляет только явная настройка из Python
  autoDetectHighContrast: false,
  // vendored-patch (этап 8.1): breadcrumb-виджета в standalone-сборке этого
  // Monaco нет (esm-дерево содержит только diffEditorBreadcrumbs), поэтому
  // навигацию по символам документа отдаёт documentSymbol-провайдер + sticky
  // scroll: заголовки текущих символов прилипают к верху вьюпорта
  // (contrib/stickyScroll строит модель из outline-модели того же провайдера).
  stickyScroll: { enabled: true },
};

// vendored-patch (тема редактора): запоминаем применённую тему — setTheme
// глобален для theme-сервиса, но пересоздание редактора (standalone после
// diff-режима) конструкторскими опциями откатил бы её обратно к vs-dark
let appliedTheme: string = defaultEditorOptions.theme ?? "vs-dark";

let editor = monaco.editor.create(editorContainer, defaultEditorOptions);
let diffEditor: monaco.editor.IStandaloneDiffEditor | null = null;
let contentChangeDisposable: monaco.IDisposable | null = null;
// vendored-patch (этап 7): слушатель каретки для истории переходов
let cursorChangeDisposable: monaco.IDisposable | null = null;

// vendored-patch (этап 9.7, закладки): отдельная decorations-коллекция закладок —
// не смешиваем с апстримной decorationsCollection (её использует highlight_lines);
// клик по дорожке закладок в gutter — запрос переключения на python-сторону
let bookmarkCollection: monaco.editor.IEditorDecorationsCollection | null = null;
let bookmarkLines: number[] = [];
let gutterClickDisposable: monaco.IDisposable | null = null;

(window as any).qtmonaco = {
  editor: editor,
  diffEditor: diffEditor,
  monaco: monaco,
  vimMode: null as CMAdapter | null,
  initialized: false,
};

let qtmonaco = (window as any).qtmonaco;

// vendored-patch (этап 11 D3): контекстное меню Monaco на глифе брейкпоинта
// глушим в пользу нашего Qt-меню (условия/удаление). capture-слушатель на
// document стоит ВЫШЕ всех обработчиков Monaco (сам корневой узел редактора
// Monaco умеет подменять, на нём слушатель терялся); срабатывает только на
// глифе текущего редактора. Сам правый клик уходит в python через
// onMouseDown-маршрут routeGutterClick (breakpoint_context_requested).
document.addEventListener("contextmenu", (ev: MouseEvent) => {
  qtmonaco.__pyqtp_ctx_prevented = false; // тестовый хук: перехватил ли наш слушатель
  const el = ev.target instanceof Element ? ev.target : null;
  const root = editor.getDomNode();
  if (el && root && root.contains(el) && el.closest(".pyqtp-breakpoint-glyph")) {
    ev.preventDefault();
    ev.stopPropagation();
    qtmonaco.__pyqtp_ctx_prevented = true;
  }
}, true);

// Build qt bridge
let bridge: any = null;

// vendored-patch (этап 3.1): вместо одиночного LspClient — менеджер стека
// LSP-серверов (lsp_stack из Python или одиночный lsp_url ради совместимости)
let lspManager: LspClientManager | null = null;
let lspHeader: string | null = null;

// vendored-patch (этап 8.2): отложенная чужая часть WorkspaceEdit. Локальную
// часть codeAction применяет сам контроллер Monaco через bulkEditService, а
// НАШУ команду он исполняет сразу после edit (esm/vs/editor/contrib/codeAction/
// browser/codeAction.js: сначала action.edit, затем action.command) — поэтому
// правки в чужих файлах (закрытые документы, другие вкладки) уходят Python
// только когда пользователь реально выбрал действие из quickfix-меню.
// Токены из map не вырезаем: каждое открывание меню порождает новые, а старые
// безвредны (ограниченный рост); rename использует прямой коллбэк ниже, без
// токена — F2 уже подтверждён пользователем.
const pendingForeignEdits = new Map<string, any>();
let foreignEditSeq = 0;

function registerForeignEditPayload(payload: any): string {
  const token = `fe${++foreignEditSeq}`;
  pendingForeignEdits.set(token, payload);
  return token;
}

function sendWorkspaceEditToPython(payload: any) {
  sendToPython("_apply_workspace_edit_request", payload);
}

monaco.editor.registerCommand("qtmonaco.lsp.applyForeignEdit", (_accessor: unknown, token?: unknown) => {
  if (typeof token !== "string") {
    console.warn("qtmonaco.lsp.applyForeignEdit called without token");
    return;
  }
  const payload = pendingForeignEdits.get(token);
  if (!payload) {
    console.warn(`qtmonaco.lsp.applyForeignEdit: unknown token ${token}`);
    return;
  }
  sendWorkspaceEditToPython(payload);
});

// для live-тестов/отладки: доступ к менеджеру стека LSP извне (lspManager —
// модульная переменная; clients менеджера private только на уровне TS) и к
// разбору WorkspaceEdit (зонд stage82 split в тестах)
Object.defineProperty(qtmonaco, "lspManager", { get: () => lspManager });
qtmonaco.splitWorkspaceEdit = splitWorkspaceEdit;

// vendored-patch (этап 9.7, закладки): рисование закладок decorations'ами Monaco.
// Состояние живёт на python-стороне (.pyqtp/bookmarks.json), сюда приходят только
// номера строк текущего документа. Коллекция своя: апстримная decorationsCollection
// занята highlight_lines; decorations гибнут вместе с моделью/редактором — поэтому
// переприменение после каждой смены модели, подмены текста и пересоздания редактора.
function applyBookmarks() {
  const model = editor.getModel();
  const decorations: monaco.editor.IModelDeltaDecoration[] = [];
  if (model) {
    const total = model.getLineCount();
    for (const line of bookmarkLines) {
      if (line >= 1 && line <= total) {
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            isWholeLine: true,
            className: "pyqtp-bookmark-line",
            linesDecorationsClassName: "pyqtp-bookmark-glyph",
          },
        });
      }
    }
  }
  if (bookmarkCollection) {
    bookmarkCollection.set(decorations);
  } else {
    bookmarkCollection = editor.createDecorationsCollection(decorations);
  }
}

function setBookmarks(lines: unknown) {
  bookmarkLines = Array.isArray(lines)
    ? lines.filter((line) => Number.isInteger(line) && line >= 1)
    : [];
  applyBookmarks();
}

// для live-тестов/отладки: установка закладок и чтение текущего набора строк
qtmonaco.setBookmarks = setBookmarks;
Object.defineProperty(qtmonaco, "bookmarkLines", { get: () => bookmarkLines });

// vendored-patch (этап 11 D1, отладчик): брейкпоинты в дорожке + подсветка строки
// останова (current-line). Состояние живёт на python-стороне (.pyqtp/breakpoints.json
// + DapControl), сюда приходят только данные этого документа. Коллекции свои
// (брейкпоинт/закладка/останов могут соседствовать на одной строке); переприменение
// то же, что у applyBookmarks: decorations гибнут вместе с моделью/редактором.
// vendored-patch (этап 11 D3): setBreakpoints принимает записи
// {'line': N[, 'condition']} (условные брейкпоинты), набор терпим и к сырым
// строкам (обратная совместимость); условный глиф получает доп. класс
// .pyqtp-breakpoint-cond (полый кружок в css), window.qtmonaco.breakpointLines
// остаётся списком строк.
let breakpointCollection: monaco.editor.IEditorDecorationsCollection | null = null;
let breakpointLines: number[] = [];
const breakpointConditions = new Map<number, string>(); // строка -> условие (D3)
let execLineCollection: monaco.editor.IEditorDecorationsCollection | null = null;
let execLine = 0; // 0 — подсветки останова нет

function applyBreakpoints() {
  const model = editor.getModel();
  const decorations: monaco.editor.IModelDeltaDecoration[] = [];
  if (model) {
    const total = model.getLineCount();
    for (const line of breakpointLines) {
      if (line >= 1 && line <= total) {
        const conditional = breakpointConditions.has(line);
        decorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            isWholeLine: true,
            className: "pyqtp-breakpoint-line",
            linesDecorationsClassName: conditional
              ? "pyqtp-breakpoint-glyph pyqtp-breakpoint-cond"
              : "pyqtp-breakpoint-glyph",
          },
        });
      }
    }
  }
  if (breakpointCollection) {
    breakpointCollection.set(decorations);
  } else {
    breakpointCollection = editor.createDecorationsCollection(decorations);
  }
}

function setBreakpoints(payload: unknown) {
  // vendored-patch (этап 11 D3): записи {'line', 'condition'} или сырые строки
  breakpointLines = [];
  breakpointConditions.clear();
  if (Array.isArray(payload)) {
    for (const item of payload) {
      if (typeof item === "number") {
        if (Number.isInteger(item) && item >= 1) {
          breakpointLines.push(item);
        }
        continue;
      }
      if (item && typeof item === "object") {
        const line = (item as any).line;
        if (!Number.isInteger(line) || line < 1) {
          continue;
        }
        breakpointLines.push(line);
        const condition = (item as any).condition;
        if (typeof condition === "string" && condition) {
          breakpointConditions.set(line, condition);
        }
      }
    }
  }
  applyBreakpoints();
}

function applyExecLine() {
  const model = editor.getModel();
  const decorations: monaco.editor.IModelDeltaDecoration[] = [];
  if (model && execLine >= 1 && execLine <= model.getLineCount()) {
    decorations.push({
      range: new monaco.Range(execLine, 1, execLine, 1),
      options: {
        isWholeLine: true,
        className: "pyqtp-exec-line",
        linesDecorationsClassName: "pyqtp-exec-gutter",
      },
    });
  }
  if (execLineCollection) {
    execLineCollection.set(decorations);
  } else {
    execLineCollection = editor.createDecorationsCollection(decorations);
  }
}

function setExecutionLine(payload: unknown) {
  const line = payload && typeof payload === "object" ? (payload as any).line : 0;
  execLine = Number.isInteger(line) && line >= 1 ? line : 0;
  applyExecLine();
}

// vendored-patch (этап 11 D3): hover-значения выражений в отладке. Свой
// hover-провайдер 'python' (один на webview) шлёт в python запрос
// _hover_value_request {requestId, expression, line}; python отвечает в
// текущем кадре останова через ``evaluate`` (context='hover') сообщением
// hover_value_reply. Вне останова гейт отвечает мгновенно found=False.
// Свой тайм-аут 2 с на JS-стороне: зависший ответ не должен вылезти late.
let hoverReqSeq = 0;
const pendingHover = new Map<string, (reply: { found: boolean; text: string } | null) => void>();

function requestHoverValue(expression: string, line: number) {
  hoverReqSeq += 1;
  const id = `h${hoverReqSeq}`;
  qtmonaco.__pyqtp_hover_id = id; // тестовый хук: id последнего запроса
  return new Promise<{ found: boolean; text: string } | null>((resolve) => {
    let done = false;
    const settle = (reply: { found: boolean; text: string } | null) => {
      if (done) {
        return;
      }
      done = true;
      clearTimeout(timer);
      pendingHover.delete(id);
      resolve(reply);
    };
    const timer = setTimeout(() => settle(null), 2000);
    pendingHover.set(id, settle);
    sendToPython("_hover_value_request", { requestId: id, expression, line });
  });
}

function resolveHoverReply(payload: unknown) {
  if (!payload || typeof payload !== "object") {
    return;
  }
  const id = (payload as any).requestId;
  const settle = typeof id === "string" ? pendingHover.get(id) : undefined;
  if (!settle) {
    return; // запоздалый ответ по уже отложенному запросу — молча мимо
  }
  settle({
    found: (payload as any).found === true,
    text: typeof (payload as any).text === "string" ? (payload as any).text : "",
  });
}

// провайдер регистрируется один раз на webview: Monaco вызывает его для
// наведения на python-модели; значение не найдено (не в отладке/не вычислено)
// — null, остаётся только обычный LSP-hover
monaco.languages.registerHoverProvider("python", {
  provideHover: async (model, position) => {
    const word = model.getWordAtPosition(position);
    if (!word || word.endColumn <= word.startColumn) {
      return null;
    }
    const expression = model.getValueInRange(
      new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
    );
    if (!expression) {
      return null;
    }
    const reply = await requestHoverValue(expression, position.lineNumber);
    qtmonaco.__pyqtp_last_hover =
      reply === null ? "TIMEOUT" : reply.found ? "FOUND" : "EMPTY"; // тестовый маркер
    if (!reply || !reply.found) {
      return null;
    }
    return {
      range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
      contents: [{ value: "```\n" + reply.text.replace(/`/g, "'") + "\n```" }],
    };
  },
});

// тестовый хук: тот же маршрут без настоящего наведения мышью (offscreen/webengine)
qtmonaco.__pyqtp_request_hover = (expression: unknown, line: unknown) => {
  const promise = requestHoverValue(
    typeof expression === "string" ? expression : String(expression ?? ""),
    typeof line === "number" ? line : 1,
  );
  return promise.then((reply) => {
    qtmonaco.__pyqtp_last_hover =
      reply === null ? "TIMEOUT" : reply.found ? "FOUND" : "EMPTY";
    return reply ? "READY" : "NULL";
  });
};

// для live-тестов/отладки: установка меток и чтение текущих наборов
qtmonaco.setBreakpoints = setBreakpoints;
qtmonaco.setExecutionLine = setExecutionLine;
Object.defineProperty(qtmonaco, "breakpointLines", { get: () => breakpointLines });
Object.defineProperty(qtmonaco, "execLine", { get: () => execLine });

// vendored-patch (этап 11): клик по дорожке строки разбираем по реально задетому
// глифу: красная точка брейкпоинта — переключение брейкпоинта, всё остальное
// (пустое место дорожки, глифы закладок/стрелка останова) — закладка (поведение
// 9.7 сохранено). Новый брейкпоинт ставится F9 на строке каретки: дорожка одна
// на две функции, пустое пространство принадлежит закладкам.
// vendored-patch (этап 11 D3): правый клик по глифу брейкпоинта — запрос
// контекстного меню условий (``_breakpoint_context_request``); правый клик по
// остальной дорожке не порождает ничего (обычное меню Monaco живое).
function routeGutterClick(
  line: number | undefined,
  element: HTMLElement | null,
  right = false,
) {
  if (!line) {
    return;
  }
  const isBreakpoint = !!element && element.classList.contains("pyqtp-breakpoint-glyph");
  if (right) {
    if (isBreakpoint) {
      sendToPython("_breakpoint_context_request", { line });
    }
    return;
  }
  sendToPython(isBreakpoint ? "_toggle_breakpoint_request" : "_toggle_bookmark_request", {
    line,
  });
}

// тестовый хук для offscreen-проб: настоящей мыши там нет — эмулируем попадание
// по глифу ('breakpoint' — брейкпоинт, любое иное значение — закладка;
// button=2|'right' — правый клик, этап 11 D3)
qtmonaco.__pyqtp_gutter_click = (line: unknown, kind: unknown, button?: unknown) => {
  const span = document.createElement("span");
  if (kind === "breakpoint") {
    span.className = "pyqtp-breakpoint-glyph";
  }
  const right = button === 2 || button === "right";
  routeGutterClick(typeof line === "number" ? line : undefined, span, right);
};

let decorationsCollection: monaco.editor.IEditorDecorationsCollection | null = null;

// vendored-patch (PYQTP, этап 6): reveal из set_cursor не работает, пока
// редактор не разложен (неактивная вкладка или свежий док — нулевой
// viewport): откладываем reveal до первого onDidLayoutChange с реальным
// размером и отдаём редактору фокус
let pendingCursor: { position: monaco.Position; moveTo: string } | null = null;
let pendingCursorDisposable: monaco.IDisposable | null = null;

function clearPendingCursor() {
  pendingCursor = null;
  if (pendingCursorDisposable) {
    pendingCursorDisposable.dispose();
    pendingCursorDisposable = null;
  }
}

function revealWithMoveTo(
  ed: monaco.editor.IStandaloneCodeEditor,
  position: monaco.Position,
  moveToPosition: string,
) {
  // If moveToPosition is provided, we move the cursor to that location
  if (moveToPosition === "center") {
    ed.revealPositionInCenter(position);
  } else if (moveToPosition === "top") {
    ed.revealPositionNearTop(position, monaco.editor.ScrollType.Smooth);
  } else {
    ed.revealPosition(position); // Ensure the cursor is visible
  }
}

function scheduleRevealWhenLaidOut(
  ed: monaco.editor.IStandaloneCodeEditor,
  position: monaco.Position,
  moveToPosition: string,
) {
  clearPendingCursor();
  pendingCursor = { position, moveTo: moveToPosition };
  pendingCursorDisposable = ed.onDidLayoutChange(() => {
    const info = ed.getLayoutInfo();
    if (pendingCursor && info.width > 0 && info.height > 0) {
      const { position: pendingPos, moveTo } = pendingCursor;
      clearPendingCursor();
      revealWithMoveTo(ed, pendingPos, moveTo);
      ed.focus();
    }
  });
}

function disposeContentChangeListener() {
  if (contentChangeDisposable) {
    contentChangeDisposable.dispose();
    contentChangeDisposable = null;
  }
}

function disposeVimMode() {
  if (qtmonaco.vimMode) {
    qtmonaco.vimMode.dispose();
    qtmonaco.vimMode = null;
  }
}

function disposeEditorModel(codeEditor: monaco.editor.IStandaloneCodeEditor) {
  const model = codeEditor.getModel();
  if (model) {
    model.dispose();
  }
}

function disposeDiffEditor() {
  if (!diffEditor) {
    return;
  }
  const diffModel = diffEditor.getModel();
  diffEditor.dispose();
  diffModel?.original.dispose();
  diffModel?.modified.dispose();
  diffEditor = null;
  qtmonaco.diffEditor = null;
}

// vendored-patch (этап 7): единая точка навешивания редакторских слушателей —
// к исходному редактору они приходят через setActiveEditor(editor) в колбэке
// QWebChannel, при замене редактора (diff-режим) — тоже через setActiveEditor.
function attachEditorListeners(target: monaco.editor.IStandaloneCodeEditor) {
  contentChangeDisposable = target.onDidChangeModelContent((_event) => {
    const model = target.getModel();
    if (model) {
      sendToPython("_current_text", model.getValue());
    }
  });
  // запись крупных прыжков каретки в историю переходов (этап 7)
  cursorChangeDisposable = target.onDidChangeCursorPosition((e) => {
    onNavCursorPositionChanged(e);
    // vendored-patch (этап 9.7, закладки): зеркалим каретку в python — штатное
    // зеркало _current_cursor у qtmonaco обновляется только на эхо set_cursor,
    // а переключению закладки и переходам next/prev нужна реальная строка
    sendToPython("_current_cursor", {
      line: e.position.lineNumber,
      column: e.position.column,
    });
  });
  // vendored-patch (этап 9.7, закладки / этап 11, брейкпоинты): клик по дорожке
  // строки в gutter — запрос переключения; разбор «брейкпоинт это или закладка»
  // в routeGutterClick, состояние на python, он пришлёт строки обратно.
  // vendored-patch (этап 11 D3): правая кнопка на глифе брейкпоинта — меню условий
  gutterClickDisposable = target.onMouseDown((e) => {
    if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_LINE_DECORATIONS) {
      return;
    }
    const line = e.target.position ? e.target.position.lineNumber : undefined;
    const right = !!e.event && e.event.rightButton === true;
    routeGutterClick(line, e.target.element ?? null, right);
  });
}

function disposeBookmarkListener() {
  if (gutterClickDisposable) {
    gutterClickDisposable.dispose();
    gutterClickDisposable = null;
  }
}

function setActiveEditor(newEditor: monaco.editor.IStandaloneCodeEditor) {
  disposeContentChangeListener();
  // vendored-patch (этап 7): слушатель каретки привязан к конкретному
  // редактору — при его замене старый disposable и старая история неактуальны
  if (cursorChangeDisposable) {
    cursorChangeDisposable.dispose();
    cursorChangeDisposable = null;
  }
  resetNavHistory();
  clearPendingCursor(); // vendored-patch (этап 6): pending reveal старого редактора не нужен
  decorationsCollection = null;
  bookmarkCollection = null; // vendored-patch (этап 9.7): коллекция привязана к редактору
  // vendored-patch (этап 11): коллекции брейкпоинтов/останова тоже привязаны
  breakpointCollection = null;
  execLineCollection = null;
  disposeBookmarkListener(); // vendored-patch (этап 9.7): слушатель тоже привязан к редактору
  editor = newEditor;
  qtmonaco.editor = editor;
  attachEditorListeners(editor);
  seedNavHistory();
  applyBookmarks(); // vendored-patch (этап 9.7): закладки переприменяем к новому редактору
  applyBreakpoints(); // vendored-patch (этап 11): то же для брейкпоинтов и строки останова
  applyExecLine();
}

function switchToStandaloneEditor() {
  if (!diffEditor) {
    return;
  }
  disposeVimMode();
  disposeContentChangeListener();
  disposeDiffEditor();
  setActiveEditor(
    // vendored-patch (тема редактора): тема переживает пересоздание редактора
    monaco.editor.create(editorContainer, { ...defaultEditorOptions, theme: appliedTheme }),
  );
}

function switchToDiffEditor(data: any) {
  disposeVimMode();
  disposeContentChangeListener();
  lspManager?.setModel(null); // vendored-patch: diff-режим вне LSP-жизни цикла документов
  if (diffEditor) {
    disposeDiffEditor();
  } else {
    disposeEditorModel(editor);
    editor.dispose();
  }

  const options: monaco.editor.IStandaloneDiffEditorConstructionOptions = {
    automaticLayout: true,
    // vendored-patch (тема редактора): diff наследует текущую тему
    theme: appliedTheme,
    ...(data.options || {}),
  };
  diffEditor = monaco.editor.createDiffEditor(editorContainer, options);
  qtmonaco.diffEditor = diffEditor;

  const language = data.language ?? undefined;
  const originalUri = data.originalUri ? monaco.Uri.parse(data.originalUri) : undefined;
  const modifiedUri = data.modifiedUri ? monaco.Uri.parse(data.modifiedUri) : undefined;
  const originalModel = monaco.editor.createModel(data.original, language, originalUri);
  const modifiedModel = monaco.editor.createModel(data.modified, language, modifiedUri);

  diffEditor.setModel({
    original: originalModel,
    modified: modifiedModel,
  });
  setActiveEditor(diffEditor.getModifiedEditor());
  sendToPython("_current_text", modifiedModel.getValue());
  sendToPython("_current_uri", modifiedModel.uri.toString());
}

// Define init function
function init() {
  // Add any initialization code here if needed
  console.log("Editor initialized");
  sendToPython("bridge_initialized", true);
  qtmonaco.initialized = true;
}

// Declare QWebChannel for TypeScript
declare global {
  interface Window {
    qt: {
      webChannelTransport: any;
    };
  }
  class QWebChannel {
    constructor(transport: any, callback: (channel: any) => void);
  }
}

window.onload = function () {
  console.log("Window loaded, initializing QWebChannel");
  new QWebChannel(window.qt.webChannelTransport, function (channel) {
    bridge = channel.objects.connector;

    // Check if bridge has the expected methods
    if (bridge) {
      bridge.javascript_data_sent.connect(updateFromPython);
      console.log("Successfully connected to javascript_data signal");
    } else {
      console.warn("Bridge javascript_data_sent.connect not available");
    }

    setActiveEditor(editor);

    init();
  });
};

function sendToPython(name: string, value: any) {
  if (bridge) {
    bridge._receive(name, JSON.stringify(value));
  }
}

// vendored-patch (этап 6): «переход к определению» (и другие Monaco-навигационные
// действия: ссылки, go to symbol, ctrl+click по импорту) на ресурс, отличный от
// текущей модели, без зарегистрированного opener'а в standalone не работает вовсе
// («default behavior is to do nothing» — monaco standaloneEditor.js). Открывать
// чужие файлы умеет только IDE-слой на python: при навигации на другой ресурс
// сообщаем ему uri и диапазон (monaco-нумерация, 1-based; selectionOrPosition
// приходит либо IRange, либо IPosition) и считаем запрос обработанным. Для той
// же модели возвращаем false — локальный переход остаётся за Monaco (этап 4).
// См. QtMonacoEditorWidget.open_uri_requested / DocumentManager.open_definition_uri.
monaco.editor.registerEditorOpener({
  openCodeEditor(source, resource, selectionOrPosition) {
    const model = source.getModel();
    if (model && model.uri.toString() === resource.toString()) {
      return false;
    }
    if (!bridge) {
      return false;
    }
    let range: monaco.IRange | null = null;
    if (selectionOrPosition && "startLineNumber" in selectionOrPosition) {
      range = selectionOrPosition;
    } else if (selectionOrPosition && "lineNumber" in selectionOrPosition) {
      range = {
        startLineNumber: selectionOrPosition.lineNumber,
        startColumn: selectionOrPosition.column ?? 1,
        endLineNumber: selectionOrPosition.lineNumber,
        endColumn: selectionOrPosition.column ?? 1,
      };
    }
    sendToPython("_open_uri_request", { uri: resource.toString(), range });
    return true;
  },
});

// vendored-patch (этап 7): ключевые связки, которых в standalone-сборке Monaco
// нет по умолчанию. find (ctrl+F/ctrl+H) и folding (ctrl+K-хорды) уже
// зарегистрированы самими contrib'ами; fontZoom-команды (
// editor.action.fontZoomIn/Out/Reset) есть, но без keybinding'ов;
// smartSelect — только Shift+Alt+стрелки (у нас ctrl+shift+. / ctrl+shift+,
// как в VS Code; alt+стрелки без shift при этом свободны).
// monaco.editor.addKeybindingRules — глобальные правила, они переживают
// пересоздание редактора (switchToStandaloneEditor).
monaco.editor.addKeybindingRules([
  {
    keybinding: monaco.KeyMod.CtrlCmd | monaco.KeyCode.Equal,
    command: "editor.action.fontZoomIn",
    when: "editorFocus",
  },
  {
    keybinding: monaco.KeyMod.CtrlCmd | monaco.KeyCode.Minus,
    command: "editor.action.fontZoomOut",
    when: "editorFocus",
  },
  {
    keybinding: monaco.KeyMod.CtrlCmd | monaco.KeyCode.Digit0,
    command: "editor.action.fontZoomReset",
    when: "editorFocus",
  },
  {
    keybinding: monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Period,
    command: "editor.action.smartSelect.expand",
    when: "editorFocus",
  },
  {
    keybinding: monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Comma,
    command: "editor.action.smartSelect.shrink",
    when: "editorFocus",
  },
]);

// vendored-patch (этап 7): собственная история переходов (go back / go forward).
// В standalone-бандле Monaco нет workbench-механизма navigation history —
// команд goBack/goForward нет вовсе (contrib/editorState — про отмену запросов,
// не про историю). Поэтому заводим свой стек позиций per-editor (каждая
// вкладка — отдельный QWebEngineView/JS-мир; кросс-документная история — на
// python-слое, вне этого патча). Записываем позицию ПЕРЕД крупным прыжком
// (дельта >= NAV_JUMP_MIN_LINES строк) — это отсекает обычное печатание и
// ходы стрелками; события Undo/Redo не пишем. Ходы самих goBack/goForward
// не должны порождать новых записей — флаг navNavigating.
const NAV_HISTORY_LIMIT = 80;
const NAV_JUMP_MIN_LINES = 4;

interface NavPoint {
  lineNumber: number;
  column: number;
}

const navBack: NavPoint[] = [];
const navForward: NavPoint[] = [];
let navLastPos: NavPoint | null = null;
let navNavigating = false;

function resetNavHistory() {
  navBack.length = 0;
  navForward.length = 0;
  navLastPos = null;
}

// vendored-patch (этап 7): стартовая позиция каретки (сразу после создания или
// замены модели, либо подстановки редактора) событий смены позиции не порождает —
// без отправной точки первый крупный переход (например LSP go-to-definition сразу
// после открытия файла) не попал бы в историю и Alt+LeftArrow молчал бы
function seedNavHistory() {
  if (navNavigating) {
    return;
  }
  const position = editor.getPosition();
  navLastPos = position
    ? { lineNumber: position.lineNumber, column: position.column }
    : null;
}

function pushNavPoint(stack: NavPoint[], point: NavPoint) {
  stack.push(point);
  if (stack.length > NAV_HISTORY_LIMIT) {
    stack.shift();
  }
}

function onNavCursorPositionChanged(e: monaco.editor.ICursorPositionChangedEvent) {
  const to = { lineNumber: e.position.lineNumber, column: e.position.column };
  if (navNavigating) {
    navLastPos = to;
    return;
  }
  const from = navLastPos;
  navLastPos = to;
  if (!from) {
    return;
  }
  if (from.lineNumber === to.lineNumber && from.column === to.column) {
    return;
  }
  const reason = e.reason;
  if (
    reason === monaco.editor.CursorChangeReason.Undo ||
    reason === monaco.editor.CursorChangeReason.Redo
  ) {
    return;
  }
  if (Math.abs(from.lineNumber - to.lineNumber) < NAV_JUMP_MIN_LINES) {
    return;
  }
  pushNavPoint(navBack, from);
  navForward.length = 0;
}

function navigateToNavPoint(point: NavPoint) {
  const position = new monaco.Position(point.lineNumber, point.column);
  navNavigating = true;
  try {
    editor.setPosition(position);
    editor.setSelection(new monaco.Selection(point.lineNumber, point.column, point.lineNumber, point.column));
    const layout = editor.getLayoutInfo();
    if (layout.width > 0 && layout.height > 0) {
      editor.revealPositionInCenterIfOutsideViewport(position);
    }
    editor.focus();
  } finally {
    navNavigating = false;
  }
  navLastPos = point;
}

function goNavBack() {
  const target = navBack.pop();
  if (!target) {
    return;
  }
  const current = editor.getPosition();
  if (current) {
    pushNavPoint(navForward, { lineNumber: current.lineNumber, column: current.column });
  }
  navigateToNavPoint(target);
}

function goNavForward() {
  const target = navForward.pop();
  if (!target) {
    return;
  }
  const current = editor.getPosition();
  if (current) {
    pushNavPoint(navBack, { lineNumber: current.lineNumber, column: current.column });
  }
  navigateToNavPoint(target);
}

monaco.editor.registerCommand("qtmonaco.editor.navigation.goBack", () => goNavBack());
monaco.editor.registerCommand("qtmonaco.editor.navigation.goForward", () => goNavForward());
monaco.editor.addKeybindingRules([
  {
    keybinding: monaco.KeyMod.Alt | monaco.KeyCode.LeftArrow,
    command: "qtmonaco.editor.navigation.goBack",
    when: "editorFocus",
  },
  {
    keybinding: monaco.KeyMod.Alt | monaco.KeyCode.RightArrow,
    command: "qtmonaco.editor.navigation.goForward",
    when: "editorFocus",
  },
]);

// vendored-patch (этап 3.1): общий поднятие стека LSP-клиентов из записей
// (lsp_stack от хаба IDE или одиночный lsp_url ради обратной совместимости)
function setupLspStack(entries: LspStackEntry[]) {
  if (lspManager) {
    lspManager.destroy();
  }
  if (!entries.length) {
    lspManager = null;
    return;
  }
  lspManager = new LspClientManager();
  lspManager.prependedData = lspHeader || "";
  lspManager.onSignatureHelp = (signatureHelp) => {
    sendToPython("_signature_help", signatureHelp);
  };
  // vendored-patch (этап 8.2): чужая (по текущей модели) часть WorkspaceEdit
  // из rename/codeAction уходит Python'у — он решает, открытый это документ
  // (сообщение apply_lsp_text_edits его редактору) или закрытый (правка диска),
  // см. pyqtp/controls/doc_control/workspace_edit.py
  lspManager.onWorkspaceEditRequest = sendWorkspaceEditToPython;
  lspManager.registerForeignEdit = registerForeignEditPayload;
  lspManager.setStack(entries);
  lspManager.setModel(editor.getModel()); // подхватываем текущую модель
}

function updateFromPython(name: string, value: string) {
  const data = JSON.parse(value);
  console.log(`Received update from Python: ${name} =`, data);
  switch (name) {
    case "set_text": {
      clearPendingCursor(); // vendored-patch (этап 6): старый pending reveal неактуален
      switchToStandaloneEditor();
      const model = editor.getModel();
      if (model) {
        // Only create a new model if the URI or language is changing
        if (
          (data.uri && model.uri.toString() !== monaco.Uri.parse(data.uri).toString()) ||
          (data.language && model.getLanguageId() !== data.language)
        ) {
          // If the URI or language is changing, dispose the old model
          lspManager?.setModel(null); // vendored-patch: didClose старой модели до её dispose
          model.dispose();
          let language = data.language ?? undefined;
          let uri = data.uri ? monaco.Uri.parse(data.uri) : undefined;

          const new_model = monaco.editor.createModel(data.data, language, uri);
          editor.setModel(new_model);
          lspManager?.setModel(new_model); // vendored-patch: LSP должен знать текущую модель
          resetNavHistory(); // vendored-patch (этап 7): позиции старой модели неактуальны
          seedNavHistory(); // vendored-patch (этап 7): старт истории от новой позиции
          applyBookmarks(); // vendored-patch (этап 9.7): decorations с model'ю сбрасываются
          applyBreakpoints();
          applyExecLine(); // vendored-patch (этап 11): брейкпоинты/останов — туда же
          sendToPython("_current_uri", new_model.uri.toString());
          break;
        }
        // If no new language or uri is specified, just update the text
        model.pushEditOperations(
          [],
          [
            {
              range: model.getFullModelRange(),
              text: data.data,
            },
          ],
          () => null,
        );
        // vendored-patch (этап 7): текст подменён целиком — старые позиции
        // модели неактуальны, историю отсчитываем от текущего курсора
        resetNavHistory();
        seedNavHistory();
        applyBookmarks(); // vendored-patch (этап 9.7): строки за пределами нового текста — в мусор
        applyBreakpoints();
        applyExecLine(); // vendored-patch (этап 11): тот же срез строк для меток отладки
        break;
      } else {
        // If no model exists, create a new one
        let language = data.language ?? undefined;
        let uri = data.uri ? monaco.Uri.parse(data.uri) : undefined;

        const new_model = monaco.editor.createModel(data.data, language, uri);
        editor.setModel(new_model);
        lspManager?.setModel(new_model); // vendored-patch: LSP должен знать текущую модель
        resetNavHistory(); // vendored-patch (этап 7): новая модель — новая история переходов
        seedNavHistory(); // vendored-patch (этап 7): старт истории от текущей позиции
        applyBookmarks(); // vendored-patch (этап 9.7): закладки на новую модель
        applyBreakpoints();
        applyExecLine(); // vendored-patch (этап 11): и метки отладки на новую модель
        sendToPython("_current_uri", new_model.uri.toString());
        break;
      }
    }

    case "open_diffs":
      switchToDiffEditor(data);
      break;

    case "read":
      // Readout the current value from the editor
      const currentValue = editor.getValue();
      sendToPython("_current_text", currentValue); // Send back the current value
      break;

    case "update_editor_options":
      // Update editor options
      diffEditor?.updateOptions(data);
      editor.updateOptions(data);
      break;

    case "set_cursor": {
      // Set the cursor position in the editor
      const position = data; // Assuming data is an object with line and column properties
      const model = editor.getModel();
      if (model) {
        const lineNumber = position.line || 1; // Default to line 1 if not provided
        const column = position.column || 1; // Default to column 1 if not provided
        const moveToPosition = position.moveToPosition || ""; // Optional moveToPosition flag
        const newPosition = new monaco.Position(lineNumber, column);
        const newSelection = new monaco.Selection(lineNumber, column, lineNumber, column);
        editor.setPosition(newPosition);
        editor.setSelection(newSelection);
        if (typeof moveToPosition === "string" && moveToPosition) {
          // vendored-patch (PYQTP, этап 6): у неактивной вкладки/свежего дока
          // редактор может быть не разложен (нулевой viewport) — reveal
          // «схлопнется», откладываем его до первой реальной раскладки
          const layout = editor.getLayoutInfo();
          if (layout.width > 0 && layout.height > 0) {
            revealWithMoveTo(editor, newPosition, moveToPosition);
          } else {
            scheduleRevealWhenLaidOut(editor, newPosition, moveToPosition);
          }
        }
        editor.focus(); // vendored-patch (этап 6): каретка активированного документа должна быть видна
        sendToPython("_current_cursor", { line: lineNumber, column: column }); // Send back the new cursor position
      }
      break;
    }

    case "apply_lsp_text_edits": {
      // vendored-patch (этап 8.2): правки в ЭТОМ документе, пришедшие из LSP
      // операции, запущенной в другом файле (rename/quickfix чужого документа).
      // DocumentManager маршрутизирует чужую часть WorkspaceEdit: открытый
      // документ получает сюда свои TextEdit в LSP-координатах сервера (0-based;
      // сервер видит текст с prepended-заголовком — отсюда вычет lspHeader).
      // undo: точка одна — см. pushEditOperations ниже (регистрирует пачку
      // одним undo-элементом). dirty-трекинг не трогаем: onDidChangeModelContent
      // уже шлёт _current_text в Python, а слушатель модели у LspClient —
      // didChange серверу.
      const payload = data && typeof data === "object" ? data : null;
      const wantUri = payload && payload.uri ? monaco.Uri.parse(String(payload.uri)).toString() : "";
      const model = editor.getModel();
      if (!model || !wantUri || model.uri.toString() !== wantUri) {
        console.warn("apply_lsp_text_edits: no model or uri mismatch");
        sendToPython("_workspace_edit_applied", { uri: wantUri, ok: false, error: "model not found" });
        break;
      }
      const lspEdits = payload && Array.isArray(payload.edits) ? payload.edits : [];
      const headerLines = lspHeader ? lspHeader.split("\n").length - 1 : 0;
      const ops: Array<{ range: monaco.Range; text: string }> = [];
      let invalid = lspEdits.length === 0;
      for (const te of lspEdits) {
        if (!te || !te.range || !te.range.start || !te.range.end) {
          invalid = true;
          break;
        }
        const startLine = te.range.start.line + 1 - headerLines;
        const endLine = te.range.end.line + 1 - headerLines;
        if (startLine < 1 || endLine < startLine) {
          invalid = true;
          break;
        }
        ops.push({
          range: new monaco.Range(
            startLine,
            te.range.start.character + 1,
            endLine,
            te.range.end.character + 1
          ),
          text: te.newText ?? "",
        });
      }
      if (invalid) {
        console.warn("apply_lsp_text_edits: invalid or empty edits payload", payload);
        sendToPython("_workspace_edit_applied", { uri: wantUri, ok: false, error: "invalid or empty edits" });
        break;
      }
      // undo: pushEditOperations регистрирует всю пачку ОДНИМ undo-элементом
      // (публичный API, тем же идёт bulkEditService). Обычный model.applyEdits
      // по умолчанию computeUndoEdits=false — правка вышла бы неundoable.
      model.pushEditOperations(
        [],
        ops.map((op) => ({ range: op.range, text: op.text })),
        () => null
      );
      sendToPython("_workspace_edit_applied", {
        uri: wantUri,
        ok: true,
        version: model.getVersionId(),
      });
      break;
    }
    case "highlight_lines":
      // Highlight a range of lines in the editor
      const highlight = data; // Expecting data with start and end line numbers
      const highlightRange = new monaco.Range(highlight.start, 1, highlight.end, 1);
      if (decorationsCollection) {
        decorationsCollection.clear(); // Clear previous decorations if any
      } else {
        decorationsCollection = editor.createDecorationsCollection(); // Create a new collection if it doesn't exist
      }
      const highlightDecoration = {
        range: highlightRange,
        options: {
          isWholeLine: true,
          linesDecorationsClassName: "highlighted-line",
        },
      };
      decorationsCollection.set([highlightDecoration]);
      break;
    case "remove_highlight":
      // Remove the highlight from the editor
      if (decorationsCollection) {
        decorationsCollection.clear(); // Clear all decorations
      }
      break;
    case "set_bookmarks":
      // vendored-patch (этап 9.7, закладки): номера строк-закладок текущего
      // документа (состояние хранит python, сюда приходит только список строк)
      setBookmarks(data);
      break;
    case "set_breakpoints":
      // vendored-patch (этап 11 D1): строки-брейкпоинты этого документа;
      // с D3 — записи {'line'[, 'condition']} (набор терпим и к сырым строкам)
      setBreakpoints(data);
      break;
    case "hover_value_reply":
      // vendored-patch (этап 11 D3): ответ python на hover-запрос значения
      // выражения {'requestId', 'found', 'text'} (гейт/evaluate на python)
      resolveHoverReply(data);
      break;
    case "set_execution_line":
      // vendored-patch (этап 11 D1): строка кадра останова ({line: N}, 0 — снять)
      setExecutionLine(data);
      break;
    case "delete_line": {
      // Delete a specific line in the editor
      const model = editor.getModel();
      if (!model) break;
      let lineToDelete: number | null = null;
      if (typeof data === "number") {
        lineToDelete = data; // If data is a number, use it directly
      } else if (data === "current") {
        const position = editor.getPosition();
        if (position) {
          lineToDelete = position.lineNumber; // Use the current line number
        }
      } else {
        console.warn("Invalid data for delete_line, expected a line number or 'current'");
        break;
      }
      if (lineToDelete !== null) {
        const lineRange = model.getLineContent(lineToDelete);
        const range = new monaco.Range(lineToDelete, 1, lineToDelete, lineRange.length + 1);
        const editOperation = { range: range, text: "" }; // Replace with empty string to delete
        model.pushEditOperations([], [editOperation], () => null); // Apply the edit operation
      }
      break;
    }
    case "insert": {
      // Insert text at the specified position
      const model = editor.getModel();
      if (!model) break;
      let position = null;
      if (data.line !== null) {
        position = new monaco.Position(data.line, data.column || 1);
      } else {
        position = editor.getPosition(); // Use current cursor position if no line is specified
      }

      if (position) {
        const insertText = data.text || ""; // Default to empty string if no text provided
        const editOperation = {
          range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column),
          text: insertText,
        };
        model.pushEditOperations([], [editOperation], () => null); // Apply the edit operation
      }
      break;
    }
    case "readonly":
      // Set the editor to read-only mode
      const isReadOnly = data === true;
      diffEditor?.updateOptions({ readOnly: isReadOnly });
      editor.updateOptions({ readOnly: isReadOnly });
      break;
    case "language": {
      const diffModel = diffEditor?.getModel();
      if (diffModel) {
        monaco.editor.setModelLanguage(diffModel.original, data);
        monaco.editor.setModelLanguage(diffModel.modified, data);
      } else {
        const model = editor.getModel();
        if (model) {
          monaco.editor.setModelLanguage(model, data);
        }
      }
      break;
    }
    case "add_action":
      // Add a new action to the editor
      let action = data;
      if (action && typeof action === "object") {
        action.contextMenuGroupId = "navigation";
        action.contextMenuOrder = 1.5;
        action.run = () => {
          sendToPython(`_context_menu_action`, {
            id: action.id,
            label: action.label,
          });
        };
        editor.addAction(action);
      }
      break;
    case "theme":
      // Set the theme - Monaco will use a fallback if the theme doesn't exist
      try {
        monaco.editor.setTheme(data);
        appliedTheme = data; // vendored-patch (тема редактора)
        console.log(`Applied theme: ${data}`);
        sendToPython("_theme", data); // Send back the theme name
      } catch (error) {
        console.warn(`Failed to apply theme "${data}":`, error);
        // Fallback to default theme
        monaco.editor.setTheme("vs-dark");
        appliedTheme = "vs-dark"; // vendored-patch (тема редактора)
        sendToPython("_theme", "vs-dark");
      }
      break;
    case "lsp_url": {
      // Обратная совместимость: одиночный адрес -> стек из одного сервера
      if (data && typeof data === "string") {
        console.log(`Setting up LSP stack (single entry): ${data}`);
        setupLspStack([{ url: data, owner: "pylsp", languageId: "python", priority: 0 }]);
      } else {
        lspManager?.destroy();
        lspManager = null;
      }
      break;
    }
    case "lsp_stack": {
      // vendored-patch (этап 3.1): стек LSP-серверов от хаба IDE
      if (Array.isArray(data)) {
        console.log(`Setting up LSP stack: ${data.map((e: LspStackEntry) => e.owner).join(", ")}`);
        setupLspStack(data);
      } else {
        console.warn("Invalid data provided for lsp_stack");
      }
      break;
    }
    case "set_lsp_header":
      // Set the LSP header for the client
      lspHeader = data; // Store the header for later use
      lspManager?.setPrependedData(data);
      break;
    case "get_lsp_header":
      // Get the current LSP header
      const headerData = lspManager ? lspManager.prependedData : "";
      sendToPython("_lsp_header", headerData);
      break;
    case "vim_mode":
      // Enable or disable Vim mode
      if (data === true) {
        if (!qtmonaco.vimMode) {
          var statusNode = document.getElementById("status");
          qtmonaco.vimMode = initVimMode(editor, statusNode);
          console.log("Vim mode enabled");
        }
      } else {
        if (qtmonaco.vimMode) {
          qtmonaco.vimMode.dispose(); // Dispose Vim mode if it exists
          qtmonaco.vimMode = null;
          console.log("Vim mode disabled");
        }
      }
      break;
    case "update_workspace_config":
      // Update workspace configuration
      if (!lspManager) {
        console.warn("LSP client not initialized, cannot update workspace config");
        sendToPython("_workspace_config_updated", { success: false, error: "LSP client not initialized" });
        break;
      }
      if (!data || typeof data !== "object") {
        console.warn("Invalid data provided for workspace config update");
        sendToPython("_workspace_config_updated", { success: false, error: "Invalid data provided" });
        break;
      }

      lspManager.updateSettings(data); // Mirror settings to all stack clients

      break;
  }
}
