# Дорожная карта LSP-подсистемы IDE (qtmonaco + basedpyright + хаб)

Статус на момент написания: редактор на `qtmonaco` внедрён и работает
(`pyqtp/web/qtmonaco_editor.py`), LSP — только из коробки qtmonaco: один общий
`python -m pylsp --ws` на весь IDE (`qtmonaco.pylsp_provider`), провайдеры
(completion/hover/signature) зарегистрированы только для `python`,
диагностики (`publishDiagnostics`) в JS не обрабатываются вовсе.
Старый stdio-стек (`lsp_control/`, `EditorBridge`, `iide://`, `web/editor/`) удалён.

## Целевая архитектура

```
редактор (TS, 1..N WebSocket)            редактор (TS) ...
        │                                     │
        ▼                                     ▼
  ws-хаб LSP (websockets, 127.0.0.1:port, в процессе IDE)
        └── пул stdio-процессов по ключу (server_cmd, root_uri)
            JSON-RPC-мультиплексирование:
            - id клиента → "c{conn}:{id}", таблица ответов обратно;
            - нотификации сервера (publishDiagnostics и др.) → broadcast всем
              подключениям пула, TS фильтрует по uri модели;
            - InitializeResult кэшируется: первый клиент выполняет handshake,
              остальные получают реплей; сервер поднимается лениво,
              переживает закрытие редактора, убивается при выходе IDE.
```

Обоснование: basedpyright — Node-процесс 300–500 МБ, `--ws` не поддерживает
(только `--stdio`/`--socket`); схема «процесс на вкладку» неприемлема.
Хаб закрывает сразу три задачи: транспорт для basedpyright/clangd (этап 2),
несколько серверов на документ (этап 3 — редактор открывает N сокетов),
переиспользование процессов. Это повторение идей пула старого `LspManager`
(ключ `(root_uri, cmd)`), но с полноценным JSON-RPC-транспортом вместо
QWebChannel-костыля.

Зафиксированные решения:
- **Хаб быть должен** (Python, `pyqtp-ide/pyqtp/controls/lsp_control/`, каталог
  создаётся заново).
- `root_uri` документа: ближайший предок с `pyproject.toml` / `setup.py` / `.git`
  (для C++ — `compile_commands.json` / `CMakeLists.txt` / `.git`);
  иначе — каталог файла. Считается на Python, приходит редактору в стеке.
- pylsp из живой конфигурации уходит (зависимость `python-lsp-server` в pyproject
  qtmonaco пока остаётся — не мешает).
- Модель диагностики — owner'ная (по имени сервера), как в Monaco `setModelMarkers`.

## Этап 1 — диагностика pylsp (чистый TS, пересборка `js_build`)

**Статус: выполнено.** Реализовано в `lsp.ts` (`setModel`/`syncDidOpen`/дебаунс 300 мс
`didChange` full/`didClose`, подписка `publishDiagnostics` → `setModelMarkers(owner=<сервер>)`
с фильтром по uri и маппингом severity), `providers.ts` (`prepareDocument` больше не шлёт
`didOpen`), `main.ts` (`lspClient.setModel(...)` при `lsp_url`/смене модели/уходе в diff).
Проверено смоуком: чистый файл → 0 маркеров, синтаксическая ошибка → pyflakes Error +
pycodestyle Warning, перезапись файла → маркеры сняты. `js_build` пересобран.

Меняем `packages/qtmonaco/backend/src/`:

1. `lsp.ts`: подписка `connection.onNotification('textDocument/publishDiagnostics')`
   → конвертация в маркеры Monaco (severity LSP→Monaco, range, message, code) →
   `monaco.editor.setModelMarkers(model, owner=<имя сервера>, markers)`;
   фильтр «uri уведомления == uri модели» (для будущего broadcast из хаба).
2. Проактивная синхронизация документа (сейчас `didOpen` шлётся лениво из
   `prepareDocument` при каждом провайдере):
   при установке соединения — `didOpen`; `onDidChangeModelContent` — дебаунс
   (~300 мс) — `didChange` full; при `destroy`/смене модели — `didClose`.
3. `initialize`: вместо `capabilities: {}` — честный минимум
   (`publishDiagnostics`, `completion`, `hover`, `signatureHelp`).
4. Обновить `js_build` (`npm run build` + копию в `qtmonaco/js_build`, см. AGENTS.md).

Принка: в файле с неиспользуемым импортом висят маркеры F401/pycodestyle;
правка → маркеры пересчитываются; `ruff → pyright → app` зелёные.

## Этап 2 — basedpyright через Python-хаб

**Статус: выполнено.** Реализовано:
`packages/pyqtp-ide/pyqtp/controls/lsp_control/{lsp_hub,lsp_process_wrapper,lsp_registry}.py`
— `LspHub(BaseControl)` (websockets-сервер на свободном порту 127.0.0.1 в отдельном
потоке с asyncio-циклом; редактор подключается по `ws://host:<port>/<uri файла>`,
путь парсится в `language_for_uri`/`resolve_root_uri`), пул процессов
`(command, root_uri)`, кэш InitializeResult (`initialize` клиента gets закэшированный
ответ реального хендшейка хаба), id-мультиплексирование `c{conn}:{id}`, broadcast
`publishDiagnostics` всем подключениям пула, запросы сервера
(`workspace/configuration`, `client/registerCapability`, `window/workDoneProgress/create`)
обрабатывает хаб; процесс убивается по `shutdown`+`exit` при выходе IDE (graceful,
`terminate→kill` через 3 с). Реестр: `python → basedpyright-langserver --stdio`
(+зависимость `basedpyright` и явная `websockets` в pyqtp-ide). Vendored-патч
`qtmonaco/monaco.py`: `Monaco(parent=None, lsp_host=None)`, eager-спавн pylsp из
конструктора убран, lazy-fallback `_resolve_lsp_host()` для standalone-использования
qtmonaco. `QtMonacoEditorWidget(path, lsp_host=...)`, `MonacoDocumentView` берёт
`main_wnd.lsp_hub.ws_host_for(file_uri)`, `IdeMainWindow.init_controls()` регистрирует
`LspHub` до `DocumentManager`. TS не менялся: `initialize` клиента игнорируется хабом
(корень определяет хаб по uri в пути WS), конфиг — существующий
`update_workspace_configuration` → `didChangeConfiguration`.
Проверено: мини-тест на чистом asyncio (`/tmp/opencode/test_lsp_hub.py`: хендшейк,
диагностики, completion через мультиплексирование, 1 процесс на 2 подключения,
жизнь процесса после закрытия вкладки, убийство при shutdown), смоук маркеров
`/tmp/opencode/smoke_diag.py` и проба живого IDE `/tmp/opencode/probe_ide.py`
(2 вкладки одного корня = 1 процесс basedpyright, маркеры reportUndefinedVariable/
reportReturnType Error(sev 8) c source=basedpyright, после выхода IDE процессов нет).
`ruff` (13) → `pyright` (18) → app — baseline.
Важно: в Monaco маркеры имеют поле `resource` (не `uri`) — так фильтровать из Python.

Дополнение по живому тесту (чинка автодополнения): «не применяется выбранный
пункт» — raw-LSP-раскрытие `...item` в `providers.ts`: пункты basedpyright не
содержат monaco-`insertText`, при accept Monaco падал в `trimOverwrite`
(`undefined.replaceAll`) до вставки. Исправлено vendored-патчем
`mapCompletionItem` в `providers.ts`: `textEdit` (в т.ч. insert/replace) →
`range`/`insertText`, LSP-координаты → IRange (с учётом prepended-смещения),
таблица kind LSP→Monaco, `insertTextFormat=2` → `insertTextRules`,
`additionalTextEdits`, deprecated→`tags`, неизвестные серверные `command`
отбрасываются; добавлен ленивый `resolveCompletionItem` →
`completionItem/resolve` (документация/автоимпорты; сервер объявляет
resolveProvider). В HUB_CAPABILITIES `snippetSupport: false` — сервер отдаёт
plain-text (сниппеты можно включить отдельно, когда понадобится `$`-синтаксис).
Проверено: смоук accept `/tmp/opencode/smoke_completion.py` (headless Monaco:
`os.lis` → `os.listdir`) и живой IDE `/tmp/opencode/probe_ide_completion.py`
(вкладка `MonacoDocumentView`, тот же результат); `smoke_diag.py` — PASS,
ошибок в JS-консоли нет.

## Этап 3 — несколько серверов на документ + конфигурация

### 3.1 TS: мульти-соединения на редактор
`LspClient` → `LspClientManager`: список `(connection, capabilities, priority)`.
Provider'ы регистрируются раздельно на каждого сервера с его owner'ом —
Monaco сам мержит completion нескольких провайдеров и hover'ы, маркеры
изолированы owner'ом. Вручную выбирается единственный исполнитель для
`signatureHelp` / `formatting` / `codeAction` / `definition`
(capability + приоритет). `didOpen/didChange/didClose` — всем соединениям.

**Статус: выполнено.** Реализовано в `lsp.ts` (`LspClientManager`, гейт
провайдеров по capabilities из InitializeResult хаба, `claimSignatureHelp`)
и `main.ts` (`lsp_stack`); python-половина — статический реестр хаба (на 3.1) +
`lsp_hub.ws_hosts_for/lsp_stack_for` (`/uri/<server-name>` в пути WebSocket),
`monaco.py` (`lsp_host: str | list`, сообщение `lsp_stack`).
Проверено `smoke_stack.py`: маркеры от `basedpyright` + `ruff` (F821),
общий completion, применение пункта; `smoke_diag`/`smoke_completion` —
обратная совместимость одиночного `lsp_url`.
Provider'ы регистрируются раздельно на каждого сервера с его owner'ом —
Monaco сам мержит completion нескольких провайдеров и hover'ы, маркеры
изолированы owner'ом. Вручную выбирается единственный исполнитель для
`signatureHelp` / `formatting` / `codeAction` / `definition`
(capability + приоритет). `didOpen/didChange/didClose` — всем соединениям.

### 3.2 Python: реестр серверов (конфиг без пересборки TS)
Реестр `ext → [(command, capabilities, priority, enabled, debounce_ms, root_rule)]`
в конфигурации IDE (QSettings/JSON-файл контрола). При инициализации редактора
хаб/контрол шлёт в TS сообщение `lsp_stack` (стек + capabilities + приоритеты +
корень) — по мотивам старого `initLspStack`, но через коннектор qtmonaco
(новое имя в `main.ts` → `lspClients.init(stack)`).
Первый рабочий стек python: `basedpyright` (completion/hover/definition/diagnostics)
+ `ruff server --stdio` (diagnostics/formatting/codeActions).

**Статус: выполнено, включая GUI (3.4 — страница настроек, см. ниже и
`docs/SETTINGS_PLAN.md`).** `lsp_registry.LspRegistry`:
JSON-конфиг `languageId → {extensions, root_markers, servers:[{name, command,
enabled}]}` (порядок серверов = приоритет; `capabilities`/`debounce_ms` из
плана не понадобились: гейт провайдеров берётся из InitializeResult сервера,
дебаунс фиксированный в TS). Хранится JSON-строкой в QSettings контрола
`LspHub` (ключ `<key>/registry_json`; `settings_read`/`settings_write`;
дефолт в настройках не хранится); публичный API `registry_json()`,
`registry_apply_json()` (атомарная замена всего реестра, битый JSON ->
исключение, старый реестр цел), `registry_reset()`. Смена реестра применяется
к новым подключениям (открыть/перезакрыть редактор), живые процессы — до
`shutdown`.
Проверено `probe_registry_32.py` (19 проверок: фильтр `enabled`, roundtrip
JSON, пропуск битых записей, живые `initialize`-хендшейки через хаб —
реальные ruff/basedpyright и заглушка для отключённого/неизвестного сервера,
кастомные `root_markers`, чтение/запись QSettings).

### 3.3 Конфликты и UX
- Дубли diagnostics от двух серверов — owner-фильтры, настройка «какие источники
  показывать» (опционально).
- Горячая готовность серверов (~1–2 с запуск): TS буферизует didOpen/didChange до
  handshake, хаб — до готовности процесса; индикатор состояния — позже.
- 3.4: выполнено средствами общей системы страниц настроек (`docs/SETTINGS_PLAN.md`):
  страница «Языковые серверы (LSP)» (`lsp_settings_page.LspRegistrySettingsPage`,
  key `lsp`, order 50) регистрируется `LspHub` в `setup()`. Моноширинный
  `QPlainTextEdit` с `registry_json()`, кнопки окна настроек («Применить» =
  `registry_apply_json`, «Сбросить по умолчанию» = `registry_reset`, «Откатить» =
  перечитать); битый JSON — красный статус окна, действующий реестр цел.
  Вход: Ctrl+, («Настройки приложения...» в меню «Настройки»).

Принимается: в одном python-файле одновременно маркеры от basedpyright и ruff
(разные owner'ы), completion from basedpyright, форматирование from ruff;
`uv run basedpyright-langserver --help` не требуется в PATH для python (команды
реестра резолвятся из venv/PATH — зафиксировать в AGENTS.md).

## Этап 4 — C++

В основном конфигурация:
- запись `clangd --stdio` в реестр для `.cpp/.h/...`, правило корня —
  ближайший предок с `compile_commands.json`/`CMakeLists.txt`/`.git`;
- параметризовать захардкоженный `"python"` в `providers.ts`/регистрации
  провайдеров на `languageId` из стека (TS-мелочь);
- fallback для проектов без compdb: `--background-index` + `--compile-commands-dir`
  (опция реестра).

Принимается: в проекте с compdb — clangd-диагностика/ховер/переходы в `.cpp`;
`ruff → pyright → app` зелёные.

**Статус: выполнено.** `DEFAULT_LANGUAGES` пополнился записью `cpp`:
расширения `.c/.h/.hpp/.cpp/.cc/.cxx/.hxx` (единый languageId для всего
семейства — диалект определяет clangd), свои `root_markers`
`compile_commands.json`/`compile_flags.txt`/`CMakeLists.txt`/`.git`, сервер
`clangd --background-index` (флаг `--stdio` из плана не использовать: в
clangd 23 он удалён, stdio — транспорт по умолчанию; `--compile-commands-dir`
при необходимости добавляется через `command` в реестре). `LANGUAGES_REGISTRY`
(`pyqtp/web/monaco_languages.py`) — те же расширения в Monaco-язык `cpp`.
TS: `"python"` параметризован полностью; добавлен `createDefinitionProvider`
(Location/LocationLink, диапазон в текущей модели сдвигается на
prepended-заголовок); регистрация одиночных провайдеров обобщена —
`LspClientManager.claimProvider(kind, languageId, client)` для signatureHelp
и definition; python-сниппеты гейтятся `priority==0 && languageId=='python'`
(иначе полезли бы в cpp-стек).
Проверено `smoke_cpp.py` (fixture `/tmp/opencode/cpp_test_proj` с compdb):
язык модели `cpp`, маркер owner=`clangd` («Use of undeclared identifier»,
severity Error), F12 с места вызова прыгает на `add()` в этом же файле,
completion `s.si` даёт `size()`/`max_size()` из `<string>`; регрессии
`smoke_stack`/`smoke_diag`/`smoke_completion`/`probe_registry_32` зелёные,
ruff/pyright на базовых уровнях. Живой IDE: initialize-хендшейк `.../main.cpp/clangd`
возвращает реальные capabilities clangd.

## Сквозная проверка каждого этапа

`uv run ruff check --no-fix <изменённое>` (baseline 13) →
`cd packages && uv run pyright` (baseline 18) → ручной сценарий в живом IDE
(открыть файл → дождаться маркеров/подсказок → закрыть с проверкой диалога).
Для JS-этапов — пересборка `js_build` и смоук `/tmp/opencode/smoke_editor.py`
(при переносе в репозиторий — `packages/pyqtp-ide/examples/`).

## Риски

- Мультиплексирование JSON-RPC (этап 2) — единственное место с нетривиальной
  логикой; лечится мини-тестами на чистом asyncio (без GUI).
- `websockets` в Qt-приложении: hub крутится в главном потоке через asyncio-мост
  (`QEventDispatcherAIO` нет; проще — отдельный QThread с asyncio loop).
  Проверить на этапе 2.
- Пересборка `js_build` обязательна при всех TS-изменениях (31 МБ, но только
  `npm run build` ~30 с).
- Vendored-патчи qtmonaco усложняют будущий pull upstream — держать список
  патчей в AGENTS.md.
