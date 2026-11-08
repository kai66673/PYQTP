import * as monaco from "monaco-editor";
import { listen } from "vscode-ws-jsonrpc";
import type { MessageConnection } from "vscode-jsonrpc";
import {
  createCompletionProvider,
  createSignatureHelpProvider,
  createHoverProvider,
  createDefinitionProvider,
  createReferencesProvider,
  createDocumentSymbolProvider,
  createDocumentHighlightProvider,
  createSemanticTokensProvider,
} from "./providers";

export type LspCompletionItem = {
  label: string;
  insertText?: string;
  documentation?: string | { value: string };
};

export type CompletionResult = { items: LspCompletionItem[] } | LspCompletionItem[];

// vendored-patch: типы LSP-диагностик для конвертации в маркеры Monaco
export interface LspDiagnostic {
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  severity?: number; // 1=Error, 2=Warning, 3=Information, 4=Hint
  code?: string | number;
  source?: string;
  message: string;
}

interface PublishDiagnosticsParams {
  uri: string;
  diagnostics: LspDiagnostic[];
}

// vendored-patch: дебаунс отправки didChange (полная синхронизация текста)
const DID_CHANGE_DEBOUNCE_MS = 300;

// vendored-patch: LSP DiagnosticSeverity -> monaco MarkerSeverity
function lspToMonacoSeverity(severity: number | undefined): monaco.MarkerSeverity {
  switch (severity) {
    case 2:
      return monaco.MarkerSeverity.Warning;
    case 3:
      return monaco.MarkerSeverity.Info;
    case 4:
      return monaco.MarkerSeverity.Hint;
    default:
      return monaco.MarkerSeverity.Error; // 1 или отсутствует (по спецификации — Error)
  }
}

// vendored-patch (этап 5): стандартный набор типов/модификаторов семантических
// токенов LSP — используется только в fallback capabilities.initialize (наш
// хаб отправляет такой же гибрид полей 3.17 и черновика 3.16, см.
// HUB_CAPABILITIES на python-стороне; реальные серверы клиентскую легенду
// игнорируют и кодируют токены собственным legend из InitializeResult)
const STANDARD_TOKEN_TYPES = [
  "namespace", "type", "class", "enum", "interface", "struct", "typeParameter",
  "parameter", "variable", "property", "enumMember", "event", "function",
  "method", "macro", "keyword", "modifier", "comment", "string", "number",
  "regexp", "operator", "decorator",
];
const STANDARD_TOKEN_MODIFIERS = [
  "documentation", "declaration", "definition", "static", "deprecated",
  "abstract", "async", "modification", "readonly", "default", "imported",
  "public", "private", "protected",
];

// vendored-patch (этап 5): легенда семантических токенов из capabilities
// сервера; Monaco обязан использовать ровно тот же список tokenTypes/
// tokenModifiers, которым сервер кодирует индексы в data (basedpyright и
// clangd игнорируют клиентскую легенду — используют свою из InitializeResult)
function extractSemanticLegend(
  provider: any
): monaco.languages.SemanticTokensLegend | null {
  const legend = provider?.legend;
  if (!legend || !Array.isArray(legend.tokenTypes) || legend.tokenTypes.length === 0) {
    return null;
  }
  return {
    tokenTypes: legend.tokenTypes.filter((t: unknown) => typeof t === "string"),
    tokenModifiers: Array.isArray(legend.tokenModifiers)
      ? legend.tokenModifiers.filter((m: unknown) => typeof m === "string")
      : [],
  };
}

class LspClient {
  private currentConnection: MessageConnection | null = null;
  private reconnectAttempts = 0;
  private readonly baseReconnectDelay = 1000; // Start with 1 second delay
  private readonly maxReconnectDelay = 10000; // Cap at 10 seconds
  private providersRegistered = false;
  private pylspUrl: string = "localhost:1234"; // Default host and port
  private providerDisposables: monaco.IDisposable[] = [];
  private reconnectTimeoutId: number | null = null;
  private isDestroyed = false;
  private healthCheckInterval: number | null = null;
  private webSocket: WebSocket | null = null;
  public prependedData: string | null = null; // Data to prepend to the model content
  public onSignatureHelp: ((data: monaco.languages.SignatureHelp) => void) | null = null; // Callback for signature help
  public settings: object | null = null; // Store workspace settings

  // vendored-patch: жизнь документа (didOpen/didChange/didClose) и диагностики
  public owner: string;
  // vendored-patch (этап 3.1): язык модели, приоритет в стеке серверов и
  // возможности сервера из InitializeResult (гейт регистрации провайдеров)
  public languageId: string;
  public priority: number;
  public serverCapabilities: any = null;
  private manager: LspClientManager | null;
  private initialized = false; // handshake initialize+initialized завершён на текущем подключении
  private trackedModel: monaco.editor.ITextModel | null = null;
  private modelChangeListener: monaco.IDisposable | null = null;
  private openedUri: string | null = null;
  private docVersion = 0;
  private changeTimerId: number | null = null;
  private serverSyncKind = 2; // textDocumentSync.change сервера (2 = full), уточняется из initialize

  constructor(
    pylspUrl: string = "localhost:1234",
    owner: string = "pylsp",
    languageId: string = "python",
    priority: number = 0,
    manager: LspClientManager | null = null
  ) {
    this.pylspUrl = pylspUrl;
    this.owner = owner;
    this.languageId = languageId;
    this.priority = priority;
    this.manager = manager;
    this.connect();
  }

  private connect() {
    // Don't reconnect if the client has been destroyed
    if (this.isDestroyed) {
      console.log("LSP client has been destroyed, skipping connection attempt");
      return;
    }

    this.webSocket = new WebSocket("ws://" + this.pylspUrl);
    const webSocket = this.webSocket;
    webSocket.onopen = () => {
      console.log("WebSocket connected");
      this.reconnectAttempts = 0; // Reset on successful connection
      this.onConnectionOpened();
    };

    webSocket.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    webSocket.onclose = (event) => {
      console.log("WebSocket disconnected:", event.code, event.reason);
      this.currentConnection = null;
      this.initialized = false; // vendored-patch: документ нужно будет переоткрыть после реконнекта
      this.stopHealthCheck(); // Stop health check when connection is lost

      // Don't reconnect if the client has been destroyed
      if (this.isDestroyed) {
        console.log("LSP client has been destroyed, skipping reconnection");
        return;
      }

      // Cancel any existing reconnect attempt
      if (this.reconnectTimeoutId !== null) {
        clearTimeout(this.reconnectTimeoutId);
        this.reconnectTimeoutId = null;
      }

      // Always attempt to reconnect with exponential backoff
      const delay = Math.min(
        this.baseReconnectDelay * Math.min(Math.pow(1.5, this.reconnectAttempts), 10),
        this.maxReconnectDelay
      );
      this.reconnectAttempts++;
      console.log(`Attempting to reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);

      this.reconnectTimeoutId = window.setTimeout(() => {
        this.reconnectTimeoutId = null;
        this.connect();
      }, delay);
    };

    listen({
      webSocket,
      onConnection: (connection: MessageConnection) => {
        this.currentConnection = connection;

        // vendored-patch: приём диагностик сервера (publishDiagnostics -> маркеры Monaco)
        connection.onNotification("textDocument/publishDiagnostics", (params: PublishDiagnosticsParams) =>
          this.onPublishDiagnostics(params)
        );

        connection.listen();

        // vendored-patch: реальные client capabilities вместо пустых — без них
        // некоторые серверы не шлют publishDiagnostics
        const initializeRequest = {
          processId: null,
          clientInfo: { name: "qtmonaco", version: "1.0" },
          rootUri: null,
          workspaceFolders: null,
          capabilities: {
            textDocument: {
              publishDiagnostics: { relatedInformation: true, versionSupport: false },
              completion: {
                completionItem: {
                  snippetSupport: false,
                  documentationFormat: ["markdown", "plaintext"],
                },
              },
              hover: { contentFormat: ["markdown", "plaintext"] },
              signatureHelp: {
                signatureInformation: { documentationFormat: ["markdown", "plaintext"] },
              },
              // vendored-patch (этап 5): семантическая подсветка. Набор полей —
              // гибрид LSP 3.17 (legend/tokenFormat) и черновика 3.16
              // (tokenTypes/tokenModifiers/formats — их требует ruff на
              // tower-lsp); серверы всё равно кодируют токены собственным
              // legend из InitializeResult, его и берёт JS-провайдер
              semanticTokens: {
                dynamicRegistration: false,
                requests: { range: false, full: { delta: false } },
                tokenFormat: "relative",
                multilineTokenSupport: false,
                overlappingTokenSupport: false,
                serverCancelSupport: false,
                legend: { tokenTypes: STANDARD_TOKEN_TYPES, tokenModifiers: STANDARD_TOKEN_MODIFIERS },
                tokenTypes: STANDARD_TOKEN_TYPES,
                tokenModifiers: STANDARD_TOKEN_MODIFIERS,
                formats: ["relative"],
              },
            },
            workspace: { didChangeConfiguration: {} },
          },
        };

        connection
          .sendRequest("initialize", initializeRequest)
          .then((result: any) => {
            connection.sendNotification("initialized", {});
            console.log("LSP connection initialized successfully");
            // vendored-patch: фиксируем режим синхронизации сервера и переоткрываем
            // документ (после реконнекта состояние документов на сервере потеряно)
            const sync = result?.capabilities?.textDocumentSync;
            this.serverSyncKind = typeof sync === "object" && sync ? (sync.change ?? 2) : (sync ?? 2);
            // vendored-patch (этап 3.1): запоминаем возможности сервера и
            // регистрируем провайдеры (один раз, по declared-флагам)
            this.serverCapabilities = result?.capabilities ?? null;
            this.ensureProvidersRegistered();
            this.initialized = true;
            this.openedUri = null;
            this.syncDidOpen();
            // Start health check for this connection
            this.startHealthCheck();
            this.onConnectionOpened();
          })
          .catch((error) => {
            console.error("Failed to initialize LSP:", error);
            this.initialized = false;
            // Close the connection to trigger a reconnect
            if (this.currentConnection === connection) {
              this.currentConnection = null;
              connection.dispose();
              webSocket.close();
            }
          });

        // vendored-patch (этап 3.1): регистрация провайдеров убрана отсюда —
        // они регистрируются один раз после initialize (см.
        // ensureProvidersRegistered), обработчики берут подключение через геттер
      },
    });
  }

  private onConnectionOpened() {
    console.log("LSP connection opened");
    // If there are stored settings, update the workspace configuration
    if (this.settings) {
      this.updateWorkspaceConfiguration(this.settings).catch((error) => {
        console.error("Failed to update workspace configuration on connection open:", error);
      });
    }
  }

  private prependModelData(model: monaco.editor.ITextModel) {
    return this.prependedData ? this.prependedData + model.getValue() : model.getValue();
  }

  // --- vendored-patch: жизнь документа и диагностики -------------------------

  /** Отследить текущую модель редактора: didClose старой, didOpen новой, подписка на правки. */
  public setModel(model: monaco.editor.ITextModel | null) {
    if (this.trackedModel === model) {
      return;
    }
    if (this.changeTimerId !== null) {
      clearTimeout(this.changeTimerId);
      this.changeTimerId = null;
    }
    if (this.modelChangeListener) {
      this.modelChangeListener.dispose();
      this.modelChangeListener = null;
    }
    if (this.trackedModel) {
      const oldUri = this.trackedModel.uri.toString();
      if (this.openedUri === oldUri) {
        this.sendNotificationSafe("textDocument/didClose", { textDocument: { uri: oldUri } });
        this.openedUri = null;
      }
      monaco.editor.setModelMarkers(this.trackedModel, this.owner, []);
    }
    this.trackedModel = model;
    if (model) {
      this.modelChangeListener = model.onDidChangeContent(() => this.scheduleDidChange());
      this.syncDidOpen();
    }
  }

  /** Отправить didOpen текущей модели (если подключение инициализировано). */
  private syncDidOpen() {
    if (!this.trackedModel || !this.initialized || !this.isConnectionValid(this.currentConnection)) {
      return;
    }
    const uri = this.trackedModel.uri.toString();
    if (this.openedUri === uri) {
      return;
    }
    if (this.openedUri) {
      this.sendNotificationSafe("textDocument/didClose", { textDocument: { uri: this.openedUri } });
      this.openedUri = null;
    }
    this.docVersion += 1;
    this.sendNotificationSafe("textDocument/didOpen", {
      textDocument: {
        uri,
        languageId: this.trackedModel.getLanguageId(),
        version: this.docVersion,
        text: this.prependModelData(this.trackedModel),
      },
    });
    this.openedUri = uri;
    console.log(`LSP didOpen sent for ${uri}`);
  }

  private scheduleDidChange() {
    if (this.changeTimerId !== null) {
      clearTimeout(this.changeTimerId);
    }
    this.changeTimerId = window.setTimeout(() => {
      this.changeTimerId = null;
      this.sendDidChange();
    }, DID_CHANGE_DEBOUNCE_MS);
  }

  /**
   * vendored-patch (фикс авокомплита): немедленная отправка накопленного
   * didChange. Monaco реагирует на триггерные символы ('.', '(' ...) почти
   * мгновенно, а дебаунс didChange — 300 мс: сервер получал устаревший текст,
   * и completion/signatureHelp считались по документу без только что
   * напечатанного символа («obj.» без точки → списка членов нет).
   */
  flushDocumentChanges() {
    if (this.changeTimerId !== null) {
      clearTimeout(this.changeTimerId);
      this.changeTimerId = null;
      this.sendDidChange();
    }
  }

  private sendDidChange() {
    if (!this.trackedModel || !this.initialized || !this.isConnectionValid(this.currentConnection)) {
      return;
    }
    const uri = this.trackedModel.uri.toString();
    if (this.openedUri !== uri) {
      this.syncDidOpen();
      return;
    }
    if (this.serverSyncKind === 1) {
      // Инкрементальный синхронизатор: шлём замену всего документа одним чанком
      // (наши целевые серверы — pylsp/basedpyright/clangd — объявляют full).
      console.warn(`LSP server declares incremental sync, sending full replacement for ${uri}`);
    }
    this.docVersion += 1;
    this.sendNotificationSafe("textDocument/didChange", {
      textDocument: { uri, version: this.docVersion },
      contentChanges: [{ text: this.prependModelData(this.trackedModel) }],
    });
    console.log(`LSP didChange sent for ${uri} (v${this.docVersion})`);
  }

  private sendNotificationSafe(method: string, params: any) {
    if (!this.isConnectionValid(this.currentConnection)) {
      return;
    }
    try {
      this.currentConnection!.sendNotification(method, params);
    } catch (e) {
      console.warn(`Failed to send ${method}:`, e);
    }
  }

  /** textDocument/publishDiagnostics -> маркеры Monaco с owner по имени сервера. */
  private onPublishDiagnostics(params: PublishDiagnosticsParams) {
    console.log(`LSP publishDiagnostics: ${params?.uri} (${params?.diagnostics?.length ?? 0} items)`);
    const model = this.trackedModel;
    // Фильтр по uri: в будущем хаб будет рассылать diagnostics всем подключениям
    if (!model || !params || !Array.isArray(params.diagnostics) || model.uri.toString() !== params.uri) {
      return;
    }
    const headerLines = this.prependedData ? this.prependedData.split("\n").length - 1 : 0;
    const markers: monaco.editor.IMarkerData[] = params.diagnostics
      .filter((d) => d.range && d.range.start.line >= headerLines) // диагностики по префикс-заголовку не относим к модели
      .map((d) => ({
        severity: lspToMonacoSeverity(d.severity),
        message: d.message,
        startLineNumber: d.range.start.line - headerLines + 1,
        startColumn: d.range.start.character + 1,
        endLineNumber: d.range.end.line - headerLines + 1,
        endColumn: d.range.end.character + 1,
        code: d.code !== undefined && d.code !== null ? String(d.code) : undefined,
        source: d.source ?? this.owner,
      }));
    monaco.editor.setModelMarkers(model, this.owner, markers);
  }

  // ---------------------------------------------------------------------------

  // vendored-patch (этап 3.1): провайдеры регистрируются один раз на клиент
  // (обработчики получают подключение через геттер, реконнект их не трогает)
  // и только под возможности, объявленные сервером в InitializeResult (хаб
  // воспроизводит его из кэша). completion/hover — от каждого поддерживающего
  // сервера (Monaco сам объединяет результаты), signatureHelp и definition —
  // ровно один на язык: менеджер отдаёт «право» первому поддерживающему
  // серверу по приоритету (механизм заявок с этапа 4 обобщён на оба типа).
  private ensureProvidersRegistered() {
    if (this.providersRegistered) {
      return;
    }
    this.providersRegistered = true; // даже при пустом наборе — повтор не нужен

    const caps: any = this.serverCapabilities || {};
    if (caps.completionProvider) {
      this.providerDisposables.push(
        createCompletionProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          // vendored-patch (фикс авокомплита): сброс дебаунса didChange перед
          // запросом — сервер обязан увидеть напечатанный '.' до completion
          this.flushDocumentChanges.bind(this),
          // vendored-patch (этап 4): наши python-сниппеты подмешивает только
          // приоритетный сервер python-стека (в cpp-стеке их быть не должно)
          this.priority === 0 && this.languageId === "python"
        )
      );
    }
    if (
      caps.signatureProvider &&
      (this.manager === null || this.manager.claimProvider("signatureHelp", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createSignatureHelpProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this),
          this.onSignatureHelp
        )
      );
    }
    if (caps.hoverProvider) {
      this.providerDisposables.push(
        createHoverProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this)
        )
      );
    }
    if (
      caps.definitionProvider &&
      (this.manager === null || this.manager.claimProvider("definition", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createDefinitionProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this)
        )
      );
    }
    // vendored-patch (этап 8.1): references / documentSymbol / documentHighlight —
    // каждый ровно один провайдер на язык (Monaco не объединяет references и
    // выбирает единственный outline-провайдер; дубли подсветки незачем), гейт по
    // InitializeResult сервера, тот же механизм заявок, что signatureHelp/definition.
    if (
      caps.referencesProvider &&
      (this.manager === null || this.manager.claimProvider("references", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createReferencesProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this)
        )
      );
    }
    if (
      caps.documentSymbolProvider &&
      (this.manager === null || this.manager.claimProvider("documentSymbol", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createDocumentSymbolProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this)
        )
      );
    }
    if (
      caps.documentHighlightProvider &&
      (this.manager === null || this.manager.claimProvider("documentHighlight", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createDocumentHighlightProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          this.prependModelData.bind(this),
          () => this.prependedData,
          this.forceReconnect.bind(this),
          this.flushDocumentChanges.bind(this)
        )
      );
    }
    // vendored-patch (этап 5): семантическая подсветка — тоже единственный
    // провайдер на язык (первый по приоритету сервер с full-поддержкой);
    // легенда берётся из InitializeResult этого же сервера
    const semanticLegend = caps.semanticTokensProvider?.full
      ? extractSemanticLegend(caps.semanticTokensProvider)
      : null;
    if (
      semanticLegend &&
      (this.manager === null || this.manager.claimProvider("semanticTokens", this.languageId, this))
    ) {
      this.providerDisposables.push(
        createSemanticTokensProvider(
          this.languageId,
          () => this.currentConnection,
          this.isConnectionValid.bind(this),
          () => semanticLegend,
          () => this.prependedData,
          this.forceReconnect.bind(this)
        )
      );
    }
    console.log(
      `Monaco providers registered for LSP server '${this.owner}' (${this.languageId})`
    );
  }

  /** Немедленно отправить workspace/didChangeConfiguration, если есть settings. */
  public applySettings() {
    if (this.settings && this.isConnectionValid(this.currentConnection)) {
      this.updateWorkspaceConfiguration(this.settings).catch((error) => {
        console.warn("Failed to push workspace configuration:", error);
      });
    }
  }

  private isConnectionValid(connection: MessageConnection | null): boolean {
    if (!connection) return false;

    try {
      // First check if connection has obvious disposal indicators
      if ((connection as any)._disposed || (connection as any).isDisposed?.()) {
        console.warn("Detected disposed connection via disposal flags");
        if (this.currentConnection === connection) {
          this.currentConnection = null;
          this.forceReconnect();
        }
        return false;
      }

      // Check if connection has a state property that indicates it's closed
      const state = (connection as any).state || (connection as any)._state;
      if (state === "closed" || state === "disposed" || state === 3) {
        // 3 = closed state
        console.warn("Detected closed connection via state check");
        if (this.currentConnection === connection) {
          this.currentConnection = null;
          this.forceReconnect();
        }
        return false;
      }

      return true;
    } catch (e) {
      console.warn("Error checking connection validity, treating as invalid:", e);
      if (this.currentConnection === connection) {
        this.currentConnection = null;
        this.forceReconnect();
      }
      return false;
    }
  }

  private forceReconnect() {
    if (this.isDestroyed) return;

    console.log("Forcing reconnection due to disposed connection");

    // Cancel any existing reconnect attempt
    if (this.reconnectTimeoutId !== null) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    // Start reconnecting immediately
    this.reconnectTimeoutId = window.setTimeout(() => {
      this.reconnectTimeoutId = null;
      this.connect();
    }, 100); // Very short delay to avoid blocking
  }

  private startHealthCheck() {
    // Clear any existing health check
    this.stopHealthCheck();

    // Check connection health every 5 seconds
    this.healthCheckInterval = window.setInterval(async () => {
      if (this.isDestroyed) {
        this.stopHealthCheck();
        return;
      }
      // First do the basic validity check
      if (this.currentConnection && !this.isConnectionValid(this.currentConnection)) {
        console.log("Health check detected invalid connection via basic checks");
        return; // forceReconnect() is already called by isConnectionValid()
      }
      if (this.webSocket?.readyState !== WebSocket.OPEN) {
        console.log("Health check detected invalid connection via WebSocket state");
        this.forceReconnect();
        return; // forceReconnect() will handle the reconnection
      }
    }, 5000);
  }

  private stopHealthCheck() {
    if (this.healthCheckInterval !== null) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
    }
  }

  public isConnected(): boolean {
    return this.isConnectionValid(this.currentConnection);
  }

  public getConnection(): MessageConnection | null {
    return this.currentConnection;
  }

  public setLspUrl(newUrl: string) {
    if (this.isDestroyed) {
      console.warn("Cannot change URL: LSP client has been destroyed");
      return;
    }

    console.log(`Changing LSP URL from ${this.pylspUrl} to ${newUrl}`);
    this.pylspUrl = newUrl;

    // Cancel any pending reconnect attempt
    if (this.reconnectTimeoutId !== null) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    // Close current connection to trigger reconnect with new URL
    if (this.currentConnection) {
      this.currentConnection.dispose();
      this.currentConnection = null;
    }
    this.initialized = false; // vendored-patch: новый сервер — заново handshake и didOpen

    // Reset reconnect attempts for new URL
    this.reconnectAttempts = 0;

    // Start connecting to the new URL
    this.connect();
  }

  /**
   * Updates the workspace configuration settings for the LSP server.
   * @param settings Configuration object with workspace settings
   * @returns Promise that resolves when the configuration is updated
   */
  private async updateWorkspaceConfiguration(settings: Record<string, any>): Promise<void> {
    if (!this.isConnectionValid(this.currentConnection)) {
      console.warn("Cannot update workspace configuration: No valid LSP connection");
      throw new Error("No valid LSP connection available");
    }

    try {
      console.log("Updating workspace configuration:", settings);

      // Send workspace/didChangeConfiguration notification
      await this.currentConnection!.sendNotification("workspace/didChangeConfiguration", {
        settings: settings,
      });

      console.log("Workspace configuration updated successfully");
    } catch (error) {
      console.error("Failed to update workspace configuration:", error);
      throw error;
    }
  }

  destroy() {
    // Mark as destroyed to prevent further connections
    this.isDestroyed = true;

    // Stop health check
    this.stopHealthCheck();

    // Cancel any pending reconnect attempts
    if (this.reconnectTimeoutId !== null) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    // vendored-patch: корректно закрываем документ и снимаем маркеры
    if (this.changeTimerId !== null) {
      clearTimeout(this.changeTimerId);
      this.changeTimerId = null;
    }
    if (this.modelChangeListener) {
      this.modelChangeListener.dispose();
      this.modelChangeListener = null;
    }
    if (this.trackedModel && this.openedUri) {
      this.sendNotificationSafe("textDocument/didClose", { textDocument: { uri: this.openedUri } });
      this.openedUri = null;
    }
    if (this.trackedModel) {
      monaco.editor.setModelMarkers(this.trackedModel, this.owner, []);
    }

    // Dispose of Monaco providers first
    this.providerDisposables.forEach((disposable) => disposable.dispose());
    this.providerDisposables = [];
    this.providersRegistered = false;
    // vendored-patch: освобождаем «права» на единственные провайдеры этого языка
    this.manager?.releaseProvider("signatureHelp", this.languageId, this);
    this.manager?.releaseProvider("definition", this.languageId, this);
    this.manager?.releaseProvider("semanticTokens", this.languageId, this);

    // Then dispose of the LSP connection
    if (this.currentConnection) {
      this.currentConnection.dispose();
      this.currentConnection = null;
    }

    console.log("LSP client destroyed");
  }
}

// vendored-patch (этап 3.1): на редактор теперь висит целый стек LSP-серверов
// (для python — basedpyright + ruff). Запись стека приходит из Python сообщением
// `lsp_stack` (see the lsp_stack case in main.ts). Каждый сервер — отдельный
// LspClient со своим подключением к хабу; жизнь документов (didOpen/didChange/
// didClose) разсылается всем клиентам, маркеры разведены по owner,
// completion/hover объединяет Monaco, signatureHelp/definition — один на язык.
export interface LspStackEntry {
  url: string;
  owner?: string;
  languageId?: string;
  priority?: number;
}

export class LspClientManager {
  private clients: LspClient[] = [];
  // владельцы единственных провайдеров на язык: ключ "<kind>:<languageId>"
  private providerClaims = new Map<string, LspClient>();

  // общие настройки, зеркалимся каждому клиенту
  prependedData: string | null = null;
  settings: object | null = null;
  onSignatureHelp: ((data: monaco.languages.SignatureHelp) => void) | null = null;

  /** Пересоздать клиентский набор по стеку (порядок = приоритет). */
  public setStack(entries: LspStackEntry[]) {
    this.destroy();
    const sorted = [...entries].sort(
      (a, b) => (a.priority ?? 0) - (b.priority ?? 0)
    );
    sorted.forEach((entry, i) => {
      const client = new LspClient(
        entry.url,
        entry.owner ?? `lsp${i}`,
        entry.languageId ?? "python",
        entry.priority ?? i,
        this
      );
      client.prependedData = this.prependedData;
      client.settings = this.settings;
      client.onSignatureHelp = this.onSignatureHelp;
      this.clients.push(client);
    });
    console.log(
      `LSP stack: ${this.clients.map((c) => `${c.owner}(#${c.priority})`).join(", ")}`
    );
  }

  /** Передать текущую модель всем клиентам (null — снять, напр. diff-режим). */
  public setModel(model: monaco.editor.ITextModel | null) {
    for (const client of this.clients) {
      client.setModel(model);
    }
  }

  public setPrependedData(data: string | null) {
    this.prependedData = data;
    for (const client of this.clients) {
      client.prependedData = data;
    }
  }

  public updateSettings(settings: object) {
    this.settings = settings;
    for (const client of this.clients) {
      client.settings = settings; // применится и к будущим переподключениям
      client.applySettings();
    }
  }

  /** Единственный провайдер на язык (signatureHelp, definition, semanticTokens)
   * — за первым по приоритету сервером, который его поддерживает (заявка
   * делается при регистрации; vendored-patch этапа 4 — обобщение с
   * signatureHelp). */
  public claimProvider(kind: string, languageId: string, client: LspClient): boolean {
    const key = `${kind}:${languageId}`;
    const claimant = this.providerClaims.get(key);
    if (claimant && claimant !== client) {
      return false;
    }
    this.providerClaims.set(key, client);
    return true;
  }

  public releaseProvider(kind: string, languageId: string, client: LspClient) {
    const key = `${kind}:${languageId}`;
    if (this.providerClaims.get(key) === client) {
      this.providerClaims.delete(key);
    }
  }

  public destroy() {
    for (const client of this.clients) {
      client.destroy();
    }
    this.clients = [];
    this.providerClaims.clear();
  }
}

export { LspClient };
export default LspClient;
