import * as monaco from "monaco-editor";
import type { MessageConnection } from "vscode-jsonrpc";
import { getPythonSnippets } from "./snippets";
import type { CompletionResult } from "./lsp";

/**
 * Helper to check if model position needs adjustment for prepended data
 */
export function adjustLineForPrependedData(position: monaco.Position, prependedData: string | null): number {
  let line = position.lineNumber - 1;

  if (prependedData) {
    const prependedLines = prependedData.split("\n").length - 1;
    line += prependedLines; // Adjust line number based on prepended data
  }

  return line;
}

/**
 * Helper to prepare document for LSP requests.
 *
 * vendored-patch: didOpen больше не отправляется отсюда — полную жизнь документа
 * (didOpen/didChange/didClose) ведёт LspClient (см. lsp.ts). Функция оставлена
 * как точка единого извлечения uri для запросов.
 */
export function prepareDocument(
  _connection: MessageConnection,
  model: monaco.editor.ITextModel,
  _prependModelData: (model: monaco.editor.ITextModel) => string
) {
  return model.uri.toString();
}

/**
 * vendored-patch: конвертация LSP CompletionItem в формат Monaco.
 *
 * Раньше item раскрывался в Monaco «как есть» (`...item`). Пункты basedpyright
 * (как и у многих LSP-серверов) не имеют `insertText`, а текст вставки лежит в
 * `textEdit`; координаты — в формате LSP ({start:{line,character}}), а не
 * Monaco IRange. При принятии пункта Monaco падал в trimOverwrite
 * (`undefined.replaceAll`) и вставка не происходила вообще.
 */

function undoAdjustLine(line: number, prependedData: string | null): number {
  if (prependedData) {
    return line - (prependedData.split("\n").length - 1);
  }
  return line;
}

function lspRangeToMonaco(r: any, prependedData: string | null): monaco.IRange {
  return {
    startLineNumber: undoAdjustLine(r.start.line, prependedData) + 1,
    startColumn: r.start.character + 1,
    endLineNumber: undoAdjustLine(r.end.line, prependedData) + 1,
    endColumn: r.end.character + 1,
  };
}

/**
 * vendored-patch (этап 8.1): общий разбор ответов вида Location[] / LocationLink[]
 * (definition — этап 4, references — этап 8.1). Для текущей модели диапазон
 * корректируется на prependedData, для чужих файлов берётся как есть — переход
 * по ним обслуживает opener из этапа 6 (registerEditorOpener → Python-мост).
 */
function mapLspLocations(items: any[], modelUri: string, prependedData: string | null): monaco.languages.Location[] {
  const locations: monaco.languages.Location[] = [];
  for (const item of items) {
    if (!item) continue;
    const targetUri: string | undefined = item.targetUri ?? item.uri;
    const targetRange: any = item.targetSelectionRange ?? item.targetRange ?? item.range;
    if (!targetUri || !targetRange?.start || !targetRange?.end) continue;
    const sameDocument = targetUri === modelUri;
    const range = sameDocument
      ? lspRangeToMonaco(targetRange, prependedData)
      : {
          startLineNumber: targetRange.start.line + 1,
          startColumn: targetRange.start.character + 1,
          endLineNumber: targetRange.end.line + 1,
          endColumn: targetRange.end.character + 1,
        };
    locations.push({ uri: monaco.Uri.parse(targetUri), range });
  }
  return locations;
}

const LSP_KIND_TO_MONACO: Record<number, monaco.languages.CompletionItemKind> = {
  1: monaco.languages.CompletionItemKind.Text,
  2: monaco.languages.CompletionItemKind.Method,
  3: monaco.languages.CompletionItemKind.Function,
  4: monaco.languages.CompletionItemKind.Constructor,
  5: monaco.languages.CompletionItemKind.Field,
  6: monaco.languages.CompletionItemKind.Variable,
  7: monaco.languages.CompletionItemKind.Class,
  8: monaco.languages.CompletionItemKind.Interface,
  9: monaco.languages.CompletionItemKind.Module,
  10: monaco.languages.CompletionItemKind.Property,
  11: monaco.languages.CompletionItemKind.Unit,
  12: monaco.languages.CompletionItemKind.Value,
  13: monaco.languages.CompletionItemKind.Enum,
  14: monaco.languages.CompletionItemKind.Keyword,
  15: monaco.languages.CompletionItemKind.Snippet,
  16: monaco.languages.CompletionItemKind.Color,
  17: monaco.languages.CompletionItemKind.File,
  18: monaco.languages.CompletionItemKind.Reference,
  19: monaco.languages.CompletionItemKind.Folder,
  20: monaco.languages.CompletionItemKind.EnumMember,
  21: monaco.languages.CompletionItemKind.Constant,
  22: monaco.languages.CompletionItemKind.Struct,
  23: monaco.languages.CompletionItemKind.Event,
  24: monaco.languages.CompletionItemKind.Operator,
  25: monaco.languages.CompletionItemKind.TypeParameter,
};

// documentation LSP (string | MarkupContent) -> Monaco (string | IMarkdownString)
function mapDocumentation(doc: any): string | monaco.IMarkdownString | undefined {
  if (!doc) return undefined;
  if (typeof doc === "string") return doc;
  if (Array.isArray(doc)) {
    const parts = doc.map((d: any) => (typeof d === "string" ? d : d?.value ?? "")).filter(Boolean);
    return parts.length ? { value: parts.join("\n\n") } : undefined;
  }
  if (typeof doc.value === "string") return { value: doc.value };
  return undefined;
}

// LSP TextEdit[] -> Monaco additionalTextEdits
function mapAdditionalEdits(edits: any[], prependedData: string | null): monaco.languages.CompletionItem["additionalTextEdits"] {
  return edits
    .filter((e) => e && e.range && typeof e.newText === "string")
    .map((e) => ({ range: lspRangeToMonaco(e.range, prependedData), text: e.newText }));
}

// Пункт с сохранённым сырым LSP-объектом для позднего completionItem/resolve
interface MappedCompletionItem extends monaco.languages.CompletionItem {
  __lspItem?: any;
}

function mapCompletionItem(
  item: any,
  position: monaco.Position,
  word: { startColumn: number; endColumn: number } | null,
  prependedData: string | null
): MappedCompletionItem {
  const insertFromTextEdit =
    item.textEdit && typeof item.textEdit.newText === "string" ? item.textEdit.newText : null;
  const insertText = item.insertText ?? insertFromTextEdit ?? String(item.label ?? "");

  let range: monaco.languages.CompletionItem["range"];
  const te = item.textEdit;
  if (te && te.insert && te.replace) {
    range = {
      insert: lspRangeToMonaco(te.insert, prependedData),
      replace: lspRangeToMonaco(te.replace, prependedData),
    };
  } else if (te && te.range) {
    range = lspRangeToMonaco(te.range, prependedData);
  } else {
    range = {
      startLineNumber: position.lineNumber,
      startColumn: word ? word.startColumn : position.column,
      endLineNumber: position.lineNumber,
      endColumn: word ? word.endColumn : position.column,
    };
  }

  const mapped: MappedCompletionItem = {
    label: String(item.label ?? insertText),
    kind: LSP_KIND_TO_MONACO[item.kind] ?? monaco.languages.CompletionItemKind.Text,
    insertText,
    range,
  };
  if (item.detail) mapped.detail = String(item.detail);
  if (item.sortText) mapped.sortText = String(item.sortText);
  if (item.filterText) mapped.filterText = String(item.filterText);
  if (item.preselect) mapped.preselect = true;
  if (item.deprecated || (Array.isArray(item.tags) && item.tags.includes(1))) {
    mapped.tags = [monaco.languages.CompletionItemTag.Deprecated];
  }
  if (item.insertTextFormat === 2) {
    mapped.insertTextRules = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;
  }
  const doc = mapDocumentation(item.documentation);
  if (doc !== undefined) mapped.documentation = doc;
  if (Array.isArray(item.additionalTextEdits) && item.additionalTextEdits.length) {
    mapped.additionalTextEdits = mapAdditionalEdits(item.additionalTextEdits, prependedData);
  }
  // `command` сервера (например pyright.triggerparameterhints) клиенту неизвестен —
  // не пропускаем, monaco попытался бы выполнить несуществующую команду.
  mapped.__lspItem = item;
  return mapped;
}

/**
 * Create and register the completion provider
 *
 * vendored-patch (этап 3.1): languageId — параметр (на редактор теперь может
 * висеть несколько LSP-серверов), prependedData — функция-геттер (живое
 * значение заголовка), addSnippets — добавлять наши python-сниппеты
 * (только приоритетный сервер python-стека: Monaco дублирует их в списке,
 * а в стеках других языков сниппеты python вообще не нужны — этап 4).
 * vendored-patch (фикс авокомплита): flushDocumentChanges — сбросить
 * дебаунс didChange перед запросом (Monaco дёргает провайдера по '.'
 * быстрее дебаунса, иначе completion считается по устаревшему тексту).
 */
export function createCompletionProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void,
  addSnippets: boolean = true
): monaco.IDisposable {
  return monaco.languages.registerCompletionItemProvider(languageId, {
    triggerCharacters: ["."],
    provideCompletionItems: async (model: monaco.editor.ITextModel, position: monaco.Position) => {
      // Always use the current connection, not a captured reference
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for completion");
        return { suggestions: [] };
      }

      // At this point, connection is guaranteed to be valid and non-null
      const validConnection = connection!;

      // vendored-patch (фикс авокомплита): сервер обязан получить напечатанный
      // текст (включая '.' или начало слова) ДО completion-запроса — иначе
      // basedpyright считает позиции по устаревшему документу
      flushDocumentChanges();

      // Extract the current word being typed
      const wordAtPosition = model.getWordUntilPosition(position);
      const currentWord = wordAtPosition ? wordAtPosition.word : "";
      console.log("Current word for snippet filtering:", currentWord);

      // Adjust line for prepended data
      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;

      // Prepare document
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: CompletionResult = await validConnection.sendRequest("textDocument/completion", {
          textDocument: { uri },
          position: { line, character },
          context: { triggerKind: 1 }, // 1 for Invoked
        });

        const items = Array.isArray(result) ? result : result.items;

        let suggestions: MappedCompletionItem[] = items.map((item: any) =>
          mapCompletionItem(item, position, wordAtPosition, prependedData)
        );

        // Make the suggestions unique
        suggestions = Array.from(
          new Map(
            suggestions.map((item) => [`${item.label}|${item.insertText ?? ""}|${item.kind ?? ""}`, item])
          ).values()
        );

        // Get filtered snippets with properly formatted CompletionItems
        // (vendored-patch: только от приоритетного сервера языка — Monaco
        // объединяет результаты всех completion-провайдеров)
        const snippets = (addSnippets ? getPythonSnippets(currentWord) : []).map((snippet) => {
          // Update the dummy range with the actual position information
          if (snippet.range) {
            snippet.range = {
              startLineNumber: position.lineNumber,
              startColumn: wordAtPosition.startColumn,
              endLineNumber: position.lineNumber,
              endColumn: wordAtPosition.endColumn,
            };
          }
          return snippet;
        });

        return { suggestions: [...suggestions, ...snippets] };
      } catch (e) {
        console.error("LSP completion failed:", e);

        // Check if the error indicates a disposed connection
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during completion request, forcing reconnection");
          forceReconnect();
        }

        return { suggestions: [] };
      }
    },
    // vendored-patch: basedpyright объявляет resolveProvider — документацию,
    // detail и auto-import (additionalTextEdits) подтягиваем lazily;range и
    // insertText из исходного пункта не трогаем.
    resolveCompletionItem: async (item: monaco.languages.CompletionItem, _token) => {
      const mapped = item as MappedCompletionItem;
      const connection = getCurrentConnection();
      if (!mapped.__lspItem || !isConnectionValid(connection) || !connection) return item;
      try {
        const resolved: any = await connection.sendRequest("completionItem/resolve", mapped.__lspItem);
        if (!resolved) return item;
        const merged: MappedCompletionItem = { ...mapped };
        const doc = mapDocumentation(resolved.documentation);
        if (doc !== undefined) merged.documentation = doc;
        if (resolved.detail) merged.detail = String(resolved.detail);
        if (Array.isArray(resolved.additionalTextEdits) && resolved.additionalTextEdits.length) {
          merged.additionalTextEdits = mapAdditionalEdits(resolved.additionalTextEdits, getPrependedData());
        }
        merged.__lspItem = resolved;
        return merged;
      } catch (e) {
        console.error("LSP completionItem/resolve failed:", e);
        return item;
      }
    },
  });
}

/**
 * Create and register the signature help provider
 *
 * vendored-patch (этап 3.1): languageId — параметр, prependedData — геттер.
 * Регистрируется только одним экземпляром на язык (LspClientManager отдаёт
 * «право» первому серверу с signatureProvider по приоритету).
 * vendored-patch (фикс авокомплита): flushDocumentChanges — сброс дебаунса
 * didChange перед запросом (триггеры '(' ',' '=' — та же гонка, что у completion).
 */
export function createSignatureHelpProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void,
  onSignatureHelp: ((data: monaco.languages.SignatureHelp) => void) | null
): monaco.IDisposable {
  return monaco.languages.registerSignatureHelpProvider(languageId, {
    signatureHelpTriggerCharacters: ["(", ",", "="],
    provideSignatureHelp: async (model, position, _token, context): Promise<monaco.languages.SignatureHelpResult> => {
      // Always use the current connection, not a captured reference
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for signature help");
        return { value: { signatures: [], activeSignature: 0, activeParameter: 0 }, dispose: () => {} };
      }

      // At this point, connection is guaranteed to be valid and non-null
      const validConnection = connection!;

      flushDocumentChanges();

      // Adjust line for prepended data
      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;

      // Prepare document
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/signatureHelp", {
          textDocument: { uri },
          position: { line, character },
          context: {
            triggerKind: context.triggerKind,
            triggerCharacter: context.triggerCharacter,
          },
        });

        if (!result || !result.signatures?.length) {
          return { value: { signatures: [], activeSignature: 0, activeParameter: 0 }, dispose: () => {} };
        }

        // Adapt LSP SignatureHelp to Monaco's SignatureHelp format with IMarkdownString support
        const signatureHelp: monaco.languages.SignatureHelp = {
          signatures: result.signatures.map((sig: any) => ({
            label: sig.label,
            documentation: sig.documentation
              ? typeof sig.documentation === "string"
                ? { value: sig.documentation }
                : sig.documentation
              : undefined,
            parameters:
              sig.parameters?.map((param: any) => ({
                label: typeof param.label === "string" ? param.label : param.label[0] + "-" + param.label[1],
                documentation: param.documentation
                  ? typeof param.documentation === "string"
                    ? { value: param.documentation }
                    : param.documentation
                  : undefined,
              })) || [],
          })),
          activeSignature: result.activeSignature ?? 0,
          activeParameter: result.activeParameter ?? 0,
        };

        // Call the signature help callback if it exists
        if (onSignatureHelp) {
          onSignatureHelp(signatureHelp);
        }

        return {
          value: signatureHelp,
          dispose: () => {},
        };
      } catch (e) {
        console.error("LSP signature help failed:", e);

        // Check if the error indicates a disposed connection
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during signature help request, forcing reconnection");
          forceReconnect();
        }

        return {
          value: { signatures: [], activeSignature: 0, activeParameter: 0 },
          dispose: () => {},
        };
      }
    },
  });
}

/**
 * Create and register the hover provider
 *
 * vendored-patch (этап 3.1): languageId — параметр, prependedData — геттер.
 */
export function createHoverProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void
): monaco.IDisposable {
  return monaco.languages.registerHoverProvider(languageId, {
    provideHover: async (model: monaco.editor.ITextModel, position: monaco.Position) => {
      // Always use the current connection, not a captured reference
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for hover");
        return null;
      }

      // At this point, connection is guaranteed to be valid and non-null
      const validConnection = connection!;

      // vendored-patch (фикс авокомплита): hover по свежаку после правки — без
      // сброса дебаунса didChange сервер бы наводился на устаревший текст
      flushDocumentChanges();

      // Adjust line for prepended data
      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;

      // Prepare document
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/hover", {
          textDocument: { uri },
          position: { line, character },
        });

        if (result?.contents) {
          return {
            contents: Array.isArray(result.contents)
              ? result.contents.map((content: any) => ({ value: content.value || content }))
              : [{ value: result.contents.value || result.contents }],
            range: result.range,
          };
        }
      } catch (e) {
        console.error("LSP hover failed:", e);

        // Check if the error indicates a disposed connection
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during hover request, forcing reconnection");
          forceReconnect();
        }
      }

      return null;
    },
  });
}

/**
 * Create and register the definition provider
 *
 * vendored-patch (этап 4): переход textDocument/definition. Сервер отвечает
 * Location, LocationLink (clangd объявляет linkSupport) или массивы; для
 * целевой точки в текущей модели диапазон сдвигается на длину prependedData,
 * для других файлов — берётся как есть. Регистрируется ровно один на язык
 * (как signatureHelp — LspClientManager отдаёт «право» первому серверу с
 * definitionProvider по приоритету, иначе Monaco показал бы дубли).
 * vendored-patch (фикс авокомплита): flushDocumentChanges — сброс дебаунса
 * didChange перед запросом (F12 сразу после набора текста).
 */
export function createDefinitionProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void
): monaco.IDisposable {
  return monaco.languages.registerDefinitionProvider(languageId, {
    provideDefinition: async (model: monaco.editor.ITextModel, position: monaco.Position) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for definition");
        return null;
      }

      const validConnection = connection!;

      flushDocumentChanges();

      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;
      const uri = prepareDocument(validConnection, model, prependModelData);
      const modelUri = model.uri.toString();

      try {
        const result: any = await validConnection.sendRequest("textDocument/definition", {
          textDocument: { uri },
          position: { line, character },
        });
        if (!result) return null;

        const items: any[] = Array.isArray(result) ? result : [result];
        const locations = mapLspLocations(items, modelUri, prependedData);
        return locations.length ? locations : null;
      } catch (e) {
        console.error("LSP definition failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during definition request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
  });
}

/**
 * vendored-patch (этап 8.1): textDocument/references. Встроенная команда
 * editor.action.goToReferences (связка Shift+F12 зарегистрирована самим
 * contrib'ом) при >1 результате открывает peek-виджет referenceSearch, при
 * одном — переходит сразу; переходы по чужим файлам идут через opener этапа 6
 * (_open_uri_request → Python). Range для текущей модели корректируется на
 * prependedData, для чужих — как есть (см. mapLspLocations).
 * Регистрируется ровно один провайдер на язык: Monaco не объединяет
 * references (в отличие от completion/hover) — иначе были бы дубли в списке.
 */
export function createReferencesProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void
): monaco.IDisposable {
  return monaco.languages.registerReferenceProvider(languageId, {
    provideReferences: async (
      model: monaco.editor.ITextModel,
      position: monaco.Position,
      context: { includeDeclaration: boolean }
    ) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for references");
        return null;
      }

      const validConnection = connection!;

      // позиции считаются по свежему документу (та же гонка с дебаунсом
      // didChange, что у completion — см. фикс авокомплита)
      flushDocumentChanges();

      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;
      const uri = prepareDocument(validConnection, model, prependModelData);
      const modelUri = model.uri.toString();

      try {
        const result: any = await validConnection.sendRequest("textDocument/references", {
          textDocument: { uri },
          position: { line, character },
          context: { includeDeclaration: !!context.includeDeclaration },
        });
        if (!Array.isArray(result) || !result.length) return null;

        const locations = mapLspLocations(result, modelUri, prependedData);
        return locations.length ? locations : null;
      } catch (e) {
        console.error("LSP references failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during references request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
  });
}

/**
 * vendored-patch (этап 8.1): маппинг числового SymbolKind из LSP (начинается с
 * 1) в Monaco-enum (с 0) — таблицы отличаются ровно сдвигом -1
 * (LSP File=1 … TypeParameter=26 → Monaco File=0 … TypeParameter=25).
 * Tags совпадают и как есть (Deprecated=1 в обеих).
 */
function mapLspSymbolKind(kind: number | undefined): monaco.languages.SymbolKind {
  const mapped = (kind ?? 13) - 1; // LSP Variable=13 как fallback
  if (mapped >= monaco.languages.SymbolKind.File && mapped <= monaco.languages.SymbolKind.TypeParameter) {
    return mapped as monaco.languages.SymbolKind;
  }
  return monaco.languages.SymbolKind.Variable;
}

/** Рекурсивный пересчёт иерархии DocumentSymbol в Monaco-формат (range/selectionRange —
 * через lspRangeToMonaco с коррекцией на prependedData). */
function mapDocumentSymbol(sym: any, prependedData: string | null): monaco.languages.DocumentSymbol {
  const range = sym.range ? lspRangeToMonaco(sym.range, prependedData) : lspRangeToMonaco(sym.selectionRange, prependedData);
  const mapped: monaco.languages.DocumentSymbol = {
    name: String(sym.name ?? ""),
    detail: String(sym.detail ?? ""),
    kind: mapLspSymbolKind(sym.kind),
    tags: Array.isArray(sym.tags) ? sym.tags : [],
    range,
    selectionRange: lspRangeToMonaco(sym.selectionRange ?? sym.range, prependedData),
  };
  if (sym.containerName) mapped.containerName = String(sym.containerName);
  if (Array.isArray(sym.children) && sym.children.length) {
    mapped.children = sym.children.map((child: any) => mapDocumentSymbol(child, prependedData));
  }
  return mapped;
}

/**
 * vendored-patch (этап 8.1): textDocument/documentSymbol. Хаб заявляет
 * hierarchicalDocumentSymbolSupport — basedpyright/clangd отдают иерархию;
 * плоский SymbolInformation[] тоже принимается (fallback). Провайдер
 * зажигает встроенный sticky scroll (contrib/stickyScroll читает outline-
 * модель) и команду _executeDocumentSymbolProvider; breadcrumb-виджета в
 * standalone-сборке этого Monaco нет (в esm-дереве только diffEditorBreadcrumbs).
 * Регистрируется ровно один на язык (outline-модель выбирает один провайдер).
 */
export function createDocumentSymbolProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void
): monaco.IDisposable {
  return monaco.languages.registerDocumentSymbolProvider(languageId, {
    provideDocumentSymbols: async (model: monaco.editor.ITextModel) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for documentSymbol");
        return null;
      }

      const validConnection = connection!;

      // символы снимались бы с устаревшего текста сразу после правки
      flushDocumentChanges();

      const prependedData = getPrependedData();
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/documentSymbol", {
          textDocument: { uri },
        });
        if (!Array.isArray(result) || !result.length) return null;

        if (result[0]?.selectionRange) {
          // иерархический DocumentSymbol[] (хаб заявляет hierarchicalDocumentSymbolSupport)
          return result.map((sym: any) => mapDocumentSymbol(sym, prependedData));
        }
        // плоский SymbolInformation[]: range из location, иерархии нет
        return result
          .filter((sym: any) => sym.location?.range)
          .map((sym: any) => ({
            name: String(sym.name ?? ""),
            detail: "",
            kind: mapLspSymbolKind(sym.kind),
            tags: Array.isArray(sym.tags) ? sym.tags : [],
            containerName: sym.containerName ? String(sym.containerName) : undefined,
            range: lspRangeToMonaco(sym.location.range, prependedData),
            selectionRange: lspRangeToMonaco(sym.location.range, prependedData),
          }));
      } catch (e) {
        console.error("LSP documentSymbol failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during documentSymbol request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
  });
}

/** LSP DocumentHighlightKind (Text=1, Read=2, Write=3) → Monaco (Text=0, Read=1, Write=2). */
function mapLspHighlightKind(kind: number | undefined): monaco.languages.DocumentHighlightKind {
  const mapped = (kind ?? 1) - 1;
  if (mapped === monaco.languages.DocumentHighlightKind.Read || mapped === monaco.languages.DocumentHighlightKind.Write) {
    return mapped as monaco.languages.DocumentHighlightKind;
  }
  return monaco.languages.DocumentHighlightKind.Text;
}

/**
 * vendored-patch (этап 8.1): textDocument/documentHighlight — подсветка
 * вхождений символа в текущем документе (классы decorations wordHighlight/
 * wordHighlightStrong/wordHighlightText; встроенный wordHighlighter работает
 * и без провайдера, но с серверным провайдером Monaco отдаёт приоритет ему —
 * семантически точнее, чем текстовые совпадения). Опция editor.occurrencesHighlight
 * по умолчанию 'singleFile', задержка 0 — отдельного включения не требуется.
 * Регистрируется ровно один на язык (дубли подсветки от двух серверов незачем).
 */
export function createDocumentHighlightProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void
): monaco.IDisposable {
  return monaco.languages.registerDocumentHighlightProvider(languageId, {
    provideDocumentHighlights: async (model: monaco.editor.ITextModel, position: monaco.Position) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        return null;
      }

      const validConnection = connection!;

      flushDocumentChanges();

      const prependedData = getPrependedData();
      const line = adjustLineForPrependedData(position, prependedData);
      const character = position.column - 1;
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/documentHighlight", {
          textDocument: { uri },
          position: { line, character },
        });
        if (!Array.isArray(result) || !result.length) return null;

        return result
          .filter((h: any) => h?.range?.start && h?.range?.end)
          .map((h: any) => ({
            range: lspRangeToMonaco(h.range, prependedData),
            kind: mapLspHighlightKind(h.kind),
          }));
      } catch (e) {
        console.error("LSP documentHighlight failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during documentHighlight request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
  });
}

/**
 * vendored-patch (этап 5): семантическая подсветка через
 * textDocument/semanticTokens/full.
 *
 * Monaco сам разбирает «относительную» LSP-кодировку токенов (5 чисел на
 * токен: deltaLine, deltaStartChar, length, tokenType, tokenModifiers) —
 * данные из ответа сервера передаются как есть. Легенда берётся из
 * InitializeResult конкретного сервера: basedpyright/clangd клиентскую
 * легенду игнорируют и кодируют токены собственным legend (в HUB_CAPABILITIES
 * на python-стороне — формальный гибрид полей 3.17 и черновика 3.16, его
 * требует ruff на tower-lsp).
 */
export function createSemanticTokensProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  getLegend: () => monaco.languages.SemanticTokensLegend,
  getPrependedData: () => string | null,
  forceReconnect: () => void
): monaco.IDisposable {
  return monaco.languages.registerDocumentSemanticTokensProvider(languageId, {
    getLegend,
    provideDocumentSemanticTokens: async (
      model: monaco.editor.ITextModel,
      _lastResultId: string | null,
      token: { isCancellationRequested: boolean }
    ) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        return null;
      }
      if (getPrependedData()) {
        // префикс-заголоку соответствует сдвиг всех строк токенов; перекодировка
        // относительных строк не поддерживается (этап 5) — подсветку отключаем
        return null;
      }

      const validConnection = connection!;
      try {
        const result: any = await validConnection.sendRequest("textDocument/semanticTokens/full", {
          textDocument: { uri: model.uri.toString() },
        });
        if (token.isCancellationRequested) {
          return null;
        }
        if (!result || !Array.isArray(result.data) || result.data.length === 0) {
          return null;
        }
        // результат отдаём целиком (delta-протокол клиентом не запрошен);
        // resultId эхом возвращаем — Monaco использует его для инкрементальных
        // перерисовок собственного кэша
        return {
          resultId: typeof result.resultId === "string" ? result.resultId : undefined,
          data: new Uint32Array(result.data),
        };
      } catch (e) {
        console.error("LSP semanticTokens/full failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during semanticTokens request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
    releaseDocumentSemanticTokens: () => {
      // серверного кэша по resultId у нас нет — освобождать нечего
    },
  });
}

// -----------------------------------------------------------------------------
// vendored-patch (этап 8.2): WorkspaceEdit — разбор ответа rename/formatting/
// codeAction на «свою» и «чужие» части.
//
// Ключевая причина разделения: StandaloneBulkEditService.apply (esm/vs/editor/
// standalone/browser/standaloneServices.js) бросает «bad edit - model not
// found» на любом edit для отсутствующей модели и «bad edit - only text edits
// are supported» на file-ops — правка применяется all-or-nothing, поэтому
// чужие файлы (закрытые документы, переименования на ФС) обязан принимать
// Python: он применяет их к открытым чужим редакторам сообщением
// ``apply_lsp_text_edits`` и к диску напрямую (pyqtp/controls/doc_control/
// workspace_edit.py).
//
// versionId у локальных правок намеренно НЕ проставляется: сервер строил
// ответ по снимку текста на момент последнего didChange (у него свои версии),
// а проверка версии у bulkEditService сравнила бы её с текущей версией модели
// и отменила всю правку («bad state - model changed in the meantime»).
// -----------------------------------------------------------------------------

export interface SplitWorkspaceEdit {
  // правки текущей модели — формат Monaco (bulkEditService/CodeAction.edit)
  localEdits: monaco.languages.IWorkspaceTextEdit[];
  // чужие TextDocumentEdit и ресурсные операции — как получено от сервера
  // (uri строкой, LSP-координаты), порядок массива сохранён
  foreignDocumentChanges: any[];
  // есть ли в исходном правке вообще, выходящем за пределы текущей модели
  hasForeign: boolean;
}

/** Нормализация uri для сравнения (файловые uri из LSP приходят строками). */
function uriKey(uriOrString: any): string {
  const s =
    uriOrString && typeof uriOrString === "object"
      ? String(uriOrString.toString())
      : String(uriOrString ?? "");
  return s.replace(/\/+$/, "");
}

/** Monaco-диапазон в LSP-координаты (+ строки префикс-заголовка). */
function monacoRangeToLsp(range: monaco.IRange, prependedData: string | null): any {
  const headerLines = prependedData ? prependedData.split("\n").length - 1 : 0;
  return {
    start: { line: range.startLineNumber - 1 + headerLines, character: range.startColumn - 1 },
    end: { line: range.endLineNumber - 1 + headerLines, character: range.endColumn - 1 },
  };
}

/**
 * Разбор LSP WorkspaceEdit (формы ``changes`` и ``documentChanges``) на локальную
 * часть (текущая модель) и чужую (открытые в других редакторах документы и
 * закрытые файлы — решает Python). ``changes`` нормализуется в документные
 * правки, порядок documentChanges сохраняется; ресурсные операции
 * (CreateFile/RenameFile/DeleteFile/ChangeAnnotation) уходят в чужую часть как
 * есть — на ФС их выполняет Python (Monaco их и не умеет).
 */
export function splitWorkspaceEdit(
  edit: any,
  currentUri: string,
  prependedData: string | null
): SplitWorkspaceEdit {
  const result: SplitWorkspaceEdit = { localEdits: [], foreignDocumentChanges: [], hasForeign: false };
  if (!edit || typeof edit !== "object") {
    return result;
  }
  const currentKey = uriKey(currentUri);

  const addTextDocumentEdit = (uri: string, edits: any[]) => {
    if (uriKey(uri) === currentKey) {
      for (const te of edits) {
        if (!te?.range?.start || !te?.range?.end) continue;
        result.localEdits.push({
          resource: monaco.Uri.parse(uri),
          // Monaco-поле текста — ``text`` (IWorkspaceTextEdit), у LSP — ``newText``
          textEdit: { range: lspRangeToMonaco(te.range, prependedData), text: te.newText ?? "" },
          // versionId намеренно опущен (см. комментарий блока выше)
        } as monaco.languages.IWorkspaceTextEdit);
      }
    } else {
      result.hasForeign = true;
      result.foreignDocumentChanges.push({ textDocument: { uri }, edits });
    }
  };

  if (edit.changes && typeof edit.changes === "object") {
    for (const [uri, edits] of Object.entries<any>(edit.changes)) {
      if (Array.isArray(edits)) {
        addTextDocumentEdit(uri, edits);
      }
    }
  }
  if (Array.isArray(edit.documentChanges)) {
    for (const change of edit.documentChanges) {
      if (!change || typeof change !== "object") continue;
      if (change.textDocument && Array.isArray(change.edits)) {
        // TextDocumentEdit (в т.ч. AnnotatedTextEdit в edits — аннотации
        // (changeAnnotationId) клиентом не используются, поля правок совместимы)
        addTextDocumentEdit(String(change.textDocument.uri), change.edits);
      } else if (typeof change.kind === "number") {
        // ресурсные операции 3.16: CreateFile(1)/RenameFile(2)/DeleteFile(3)
        // (+ speculative-варианты); анонсированные изменения (WillRename) вне
        // объёма — уходят Python как есть
        result.hasForeign = true;
        result.foreignDocumentChanges.push(change);
      }
    }
  }
  return result;
}

/** Payload для Python-моста (``_apply_workspace_edit_request``). */
export function foreignWorkspaceEditPayload(
  origin: string,
  sourceUri: string,
  split: SplitWorkspaceEdit
) {
  return {
    origin,
    sourceUri,
    workspaceEdit: { documentChanges: split.foreignDocumentChanges },
  };
}

/**
 * vendored-patch (этап 8.2): textDocument/rename + textDocument/prepareRename
 * (F2, встроенный виджет RenameInput Monaco). single-owner на язык (правки
 * rename Monaco применяет одним batch'ем — объединение результатов двух
 * серверов дублировало бы правки).
 *
 * ВНИМАНИЕ: в этом билде Monaco 0.55 контроллер вызывает
 * ``resolveRenameLocation(model, position, token)`` — БЕЗ аргумента options
 * (в monaco.d.ts сигнатура четырёхаргументная); аргументы разбираем хвостом.
 * ``supportsPrepare`` включает регистрацию resolveRenameLocation — иначе
 * Monaco сам возьмёт слово под кареткой (RenameSkeleton.resolveRenameLocation).
 */
export function createRenameProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void,
  supportsPrepare: boolean,
  onWorkspaceEditRequest: ((payload: any) => void) | null
): monaco.IDisposable {
  const provider: any = {
    provideRenameEdits: async (
      model: monaco.editor.ITextModel,
      position: monaco.Position,
      newName: string
    ) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        console.warn("No valid LSP connection available for rename");
        return null;
      }
      const validConnection = connection!;

      flushDocumentChanges();

      const prependedData = getPrependedData();
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/rename", {
          textDocument: { uri },
          position: {
            line: adjustLineForPrependedData(position, prependedData),
            character: position.column - 1,
          },
          newName,
        });
        if (!result) return null;

        const split = splitWorkspaceEdit(result, model.uri.toString(), prependedData);
        // чужая часть уходит Python СРАЗУ: пользователь уже подтвердил F2,
        // откладывать исполнение не на чём (в отличие от codeAction)
        if (split.hasForeign && onWorkspaceEditRequest) {
          onWorkspaceEditRequest(foreignWorkspaceEditPayload("rename", uri, split));
        }
        // локальную часть применяет сам контроллер rename через
        // bulkEditService (pushStackElement до/после — одна undo-группа);
        // пустые edits при наличии чужой части — валидный «ноль» для Monaco
        return { edits: split.localEdits };
      } catch (e) {
        console.error("LSP rename failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during rename request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    },
  };

  if (supportsPrepare) {
    // PrepareRenameResult: Range | {range, placeholder} | {range, defaultBehavior}
    provider.resolveRenameLocation = async (
      model: monaco.editor.ITextModel,
      position: monaco.Position,
      optionsOrToken: any,
      maybeToken?: any
    ) => {
      // этот билд передаёт (model, position, token) — токен ищем хвостом
      const token =
        maybeToken && typeof maybeToken === "object" && "isCancellationRequested" in maybeToken
          ? maybeToken
          : optionsOrToken;
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        return null;
      }
      const validConnection = connection!;
      const prependedData = getPrependedData();
      const uri = prepareDocument(validConnection, model, prependModelData);

      try {
        const result: any = await validConnection.sendRequest("textDocument/prepareRename", {
          textDocument: { uri },
          position: {
            line: adjustLineForPrependedData(position, prependedData),
            character: position.column - 1,
          },
        });
        if (token?.isCancellationRequested) return null;
        if (!result) return null;
        const lspRange = result.range ?? result;
        if (!lspRange?.start || !lspRange?.end) return null;
        const range = lspRangeToMonaco(lspRange, prependedData);
        const placeholder =
          typeof result.placeholder === "string" ? result.placeholder : undefined;
        const word = model.getWordUntilPosition(position);
        return {
          range,
          text: placeholder ?? (word?.word || model.getValueInRange(range)),
        };
      } catch (e) {
        console.error("LSP prepareRename failed:", e);
        // rejectReason не формируем: сервер вернул RangeError-ответ — Monaco
        // сам откатится на слово под кареткой (RenameSkeleton)
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during prepareRename request, forcing reconnection");
          forceReconnect();
        }
        return null;
      }
    };
  }

  return monaco.languages.registerRenameProvider(languageId, provider);
}

/**
 * vendored-patch (этап 8.2): textDocument/formatting и
 * textDocument/rangeFormatting (команды editor.action.formatDocument /
 * formatSelection; в linux-сборке формат документа забинден на Ctrl+Shift+I).
 * single-owner на язык (форматирование от двух серверов конфликтовало бы:
 * второй форматтер переписал бы результат первого). Композитный disposable
 * на обе регистрации.
 */
export function createFormattingProviders(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void,
  supportRange: boolean
): monaco.IDisposable {
  const disposables: monaco.IDisposable[] = [];

  const request = async (
    method: string,
    model: monaco.editor.ITextModel,
    lspRange: any | undefined,
    options: monaco.languages.FormattingOptions
  ): Promise<monaco.languages.TextEdit[]> => {
    const connection = getCurrentConnection();
    if (!isConnectionValid(connection)) {
      return [];
    }
    const validConnection = connection!;
    flushDocumentChanges();
    const prependedData = getPrependedData();
    const uri = prepareDocument(validConnection, model, prependModelData);
    const params: any = {
      textDocument: { uri },
      options: {
        tabSize: options?.tabSize ?? 4,
        insertSpaces: options?.insertSpaces ?? true,
      },
    };
    if (lspRange) {
      params.range = lspRange;
    }
    try {
      const result: any = await validConnection.sendRequest(method, params);
      if (!Array.isArray(result)) return [];
      return result
        .filter((te: any) => te?.range?.start && te?.range?.end)
        .map((te: any) => ({
          range: lspRangeToMonaco(te.range, prependedData),
          text: te.newText ?? "",
        }));
    } catch (e) {
      console.error(`LSP ${method} failed:`, e);
      if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
        console.warn(`Connection was disposed during ${method} request, forcing reconnection`);
        forceReconnect();
      }
      return [];
    }
  };

  disposables.push(
    monaco.languages.registerDocumentFormattingEditProvider(languageId, {
      provideDocumentFormattingEdits: (model, options) =>
        request("textDocument/formatting", model, undefined, options),
    })
  );
  if (supportRange) {
    disposables.push(
      monaco.languages.registerDocumentRangeFormattingEditProvider(languageId, {
        provideDocumentRangeFormattingEdits: (model, range, options) => {
          const sorted =
            range.startLineNumber < range.endLineNumber ||
            (range.startLineNumber === range.endLineNumber &&
              range.startColumn <= range.endColumn)
              ? range
              : {
                  startLineNumber: range.endLineNumber,
                  startColumn: range.endColumn,
                  endLineNumber: range.startLineNumber,
                  endColumn: range.startColumn,
                };
          return request(
            "textDocument/rangeFormatting",
            model,
            monacoRangeToLsp(sorted, getPrependedData()),
            options
          );
        },
      })
    );
  }
  return { dispose: () => disposables.forEach((d) => d.dispose()) };
}

/**
 * vendored-patch (этап 8.2): textDocument/codeAction + codeAction/resolve.
 * РЕГИСТРИРУЕТСЯ КАЖДЫМ поддерживающим сервером (без claimProvider): Monaco
 * сам объединяет действия всех провайдеров в общий список quickfix-меню.
 *
 * Диагностический контекст: в этом билде Monaco провайдеру приходят маркерные
 * IMarker (standaloneLanguages докидывает context.markers), но MarkerService.
 * _toMarker вырезает все поля, кроме известного набора, а basedpyright требует
 * roundtrip поля ``data`` его diagnostic'ов (иначе они не проходят
 * isDiagnosticWithActions). Поэтому контекст восстанавливаем из СЫРЫХ
 * publishDiagnostics, кэшированных клиентом (getPublishedDiagnostics), а не из
 * маркеров.
 *
 * Разделение правок: локальная часть — в ``action.edit`` (её применяет
 * контроллер codeAction через bulkEditService), чужая — откладывается до
 * выбора действия пользователем: регистрируется в ``registerForeignEdit``
 * (token → payload в main.ts) и подвешивается командой
 * ``qtmonaco.lsp.applyForeignEdit`` — она исполнится после локальной части
 * (codeAction.js: сначала edit, затем command). Действие с edit только для
 * чужих файлов остаётся в списке с этой командой (edit не ставим — иначе
 * bulkEditService упадёт на отсутствующей модели).
 */
export function createCodeActionProvider(
  languageId: string,
  getCurrentConnection: () => MessageConnection | null,
  isConnectionValid: (connection: MessageConnection | null) => boolean,
  prependModelData: (model: monaco.editor.ITextModel) => string,
  getPrependedData: () => string | null,
  forceReconnect: () => void,
  flushDocumentChanges: () => void,
  getPublishedDiagnostics: () => any[],
  registerForeignEdit: (payload: any) => string,
  supportsResolve: boolean,
  providedKinds?: string[]
): monaco.IDisposable {
  // Monaco-действие -> {сырое LSP-действие, uri модели на момент выдачи}
  // (resolveCodeAction приходит БЕЗ модели — uri помним с момента provide)
  const monacoToRaw = new WeakMap<object, { raw: any; modelUri: monaco.Uri }>();

  const toMonacoAction = (action: any): any => {
    const monacoAction: any = { title: action.title ?? "" };
    if (action.kind) monacoAction.kind = action.kind;
    if (action.isPreferred) monacoAction.isPreferred = true;
    if (action.diagnostics) {
      monacoAction.diagnostics = (action.diagnostics ?? [])
        .filter((d: any) => d?.range?.start)
        .map((d: any) => ({
          range: {
            startLineNumber: d.range.start.line + 1,
            startColumn: d.range.start.character + 1,
            endLineNumber: d.range.end.line + 1,
            endColumn: d.range.end.character + 1,
          },
          message: d.message ?? "",
        }));
    }
    if (action.disabled) {
      monacoAction.disabled =
        typeof action.disabled === "string"
          ? action.disabled
          : action.disabled?.reason || "disabled";
    }
    return monacoAction;
  };

  const attachEditSplit = (
    monacoAction: any,
    rawAction: any,
    split: SplitWorkspaceEdit,
    sourceUri: string
  ) => {
    if (split.localEdits.length) {
      monacoAction.edit = { edits: split.localEdits };
    }
    if (split.hasForeign) {
      const token = registerForeignEdit(
        foreignWorkspaceEditPayload("codeAction", sourceUri, split)
      );
      // цепочка: если у сервера была своя команда (workspace/executeCommand
      // у нас не реализован, на практике pyright/ruff кладут edit в действие),
      // сохраняем её в payload для отладки, а исполняемой делаем нашу
      if (rawAction.command) {
        console.warn("Ignoring server command of code action (foreign edit takes its slot):", rawAction.command);
      }
      monacoAction.command = {
        id: "qtmonaco.lsp.applyForeignEdit",
        title: monacoAction.title,
        arguments: [token],
      };
    }
  };

  const applyRawEdit = (monacoAction: any, rawAction: any, model: monaco.editor.ITextModel) => {
    const sourceUri = model.uri.toString();
    const split = splitWorkspaceEdit(rawAction.edit, sourceUri, getPrependedData());
    attachEditSplit(monacoAction, rawAction, split, sourceUri);
  };

  // Monaco требует, чтобы провайдер возвращал CodeActionList С методом
  // dispose() (CodeActionRegistry кладёт список в DisposableStore — без него
  // TypeError «dispose is not a function» рвёт состояние контроллера).
  const emptyActionList = () => ({ actions: [], dispose() {} });

  const provider: any = {
    provideCodeActions: async (
      model: monaco.editor.ITextModel,
      range: monaco.IRange,
      context: { only?: string; trigger?: number },
      token: { isCancellationRequested: boolean }
    ) => {
      const connection = getCurrentConnection();
      if (!isConnectionValid(connection)) {
        return emptyActionList();
      }
      const validConnection = connection!;

      flushDocumentChanges();

      const prependedData = getPrependedData();
      const uri = prepareDocument(validConnection, model, prependModelData);
      const sorted =
        range.startLineNumber < range.endLineNumber ||
        (range.startLineNumber === range.endLineNumber && range.startColumn <= range.endColumn)
          ? range
          : {
              startLineNumber: range.endLineNumber,
              startColumn: range.endColumn,
              endLineNumber: range.startLineNumber,
              endColumn: range.startColumn,
            };
      const lspRange = monacoRangeToLsp(sorted, prependedData);
      const intersects = (r: any) =>
        r?.start && r?.end &&
        (r.end.line > lspRange.start.line ||
          (r.end.line === lspRange.start.line && r.end.character >= lspRange.start.character)) &&
        (r.start.line < lspRange.end.line ||
          (r.start.line === lspRange.end.line && r.start.character <= lspRange.end.character));
      const diagnostics = getPublishedDiagnostics().filter((d) => intersects(d?.range));

      try {
        const result: any = await validConnection.sendRequest("textDocument/codeAction", {
          textDocument: { uri },
          range: lspRange,
          // Monaco отдаёт context.only ОДИН строкой ('quickfix'), а по LSP
          // context.only — массив kinds; basedpyright зовёт на нём .some() и
          // падает на строке («kinds.some is not a function»)
          context: {
            diagnostics,
            only: context?.only ? [String(context.only)] : undefined,
          },
        });
        if (token?.isCancellationRequested) return emptyActionList();
        const actions: any[] = Array.isArray(result) ? result : result?.actions ?? [];
        const monacoActions = actions
          .filter((a) => a && a.title)
          .map((raw) => {
            const monacoAction = toMonacoAction(raw);
            monacoToRaw.set(monacoAction, { raw, modelUri: model.uri });
            if (raw.edit) {
              applyRawEdit(monacoAction, raw, model);
            }
            return monacoAction;
          });
        return { actions: monacoActions, dispose() {} };
      } catch (e) {
        console.error("LSP codeAction failed:", e);
        if (e instanceof Error && (e.message.includes("disposed") || e.message.includes("closed"))) {
          console.warn("Connection was disposed during codeAction request, forcing reconnection");
          forceReconnect();
        }
        return emptyActionList();
      }
    },
  };

  if (supportsResolve) {
    // codeAction/resolve: сервер дописывает edit (у basedpyright organizeImports
    // edit приходит сразу, resolve — подстраховка для экономных серверов)
    provider.resolveCodeAction = async (monacoAction: any) => {
      const entry = monacoToRaw.get(monacoAction);
      const connection = getCurrentConnection();
      if (!entry || !isConnectionValid(connection)) {
        return monacoAction;
      }
      const validConnection = connection!;
      try {
        const resolved: any = await validConnection.sendRequest("codeAction/resolve", entry.raw);
        if (!resolved) return monacoAction;
        const merged = { ...entry.raw, ...resolved };
        if (merged.edit) {
          const split = splitWorkspaceEdit(
            merged.edit,
            entry.modelUri.toString(),
            getPrependedData()
          );
          attachEditSplit(monacoAction, merged, split, entry.modelUri.toString());
        }
        monacoToRaw.set(monacoAction, { raw: merged, modelUri: entry.modelUri });
        return monacoAction;
      } catch (e) {
        console.error("codeAction/resolve failed:", e);
        return monacoAction;
      }
    };
  }

  return monaco.languages.registerCodeActionProvider(
    languageId,
    provider,
    providedKinds && providedKinds.length
      ? { providedCodeActionKinds: providedKinds }
      : undefined
  );
}
