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
