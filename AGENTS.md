# PYQTP — PySide6 desktop application platform + IDE

uv workspace of three `pyqtp`-namespace packages plus one vendored library:

- `packages/pyqtp-common` — theme system (JSON themes, QSS, SVG icon recolor), styles, small utils.
- `packages/pyqtp-core` — the application framework: `PluggableMainWindow`, plugins/controls/views,
  docking backends, events/scripting/terminal/help views, `launcher.py`.
- `packages/pyqtp-ide` — the IDE app (`ide.py`) built on core; Monaco editor + LSP.
- `packages/qtmonaco` — vendored upstream lib (github.com/bec-project/qtmonaco) that provides the
  Monaco `QWebEngineView` + its own LSP-over-WebSocket stack; it is a workspace member installed
  editable as `qtmonaco`.

The git repository is at the **root** (branch `main`, no CI). `packages/qtmonaco` is vendored as a
**squash-subtree**: base = upstream tag `v0.11.0` (commit `5b21da6`, one squashed commit), local
patches = the next commit; there is no nested `.git` anymore. Pull upstream updates with
`git subtree pull --prefix=packages/qtmonaco https://github.com/bec-project/qtmonaco.git <vX.Y.Z> --squash`
(upstream tags are `v`-prefixed). Gotcha: `git apply` run **inside** `packages/qtmonaco` silently
prints "Skipped patch" for every file (subdirectory prefix scoping) — apply patches from the repo
root with `git apply --directory=packages/qtmonaco <patch>`.

## Commands

Everything runs through `uv` (`uv sync --all-packages --all-groups` at root, then `uv run ...` —
a plain `uv sync` prunes workspace members' dev groups, i.e. removes `ruff`/`pyright` from the venv).
Python is pinned to 3.11 (`.python-version`); core/ide require `~=3.11.0` and the vendored
`whl/build_kddw-*.whl` is `cp311` / linux-x86_64 only — do not bump Python. The root dep carries a
`; sys_platform == 'linux'` marker so win32 (listed in `[tool.uv] environments`) can re-resolve —
the wheel has no Windows build.

qtmonaco's JS frontend lives in `packages/qtmonaco/qtmonaco/js_build/` (31 MB, required at
runtime; not in uv, **not in git** — `**/js_build/` is gitignored, so build it once after a fresh
clone). Rebuild after cloning or touching `backend/` TS:
`cd packages/qtmonaco/backend && npm install && npm run build && mkdir -p ../qtmonaco/js_build && cp -r dist/* ../qtmonaco/js_build/`
(same steps as upstream CI; node ≥ 20 works, tested on mise node 26 + vite 6).

Verify in this order: **ruff → pyright → tests → run the app**.

- `uv run ruff check <path>` — configs live per package (`[tool.ruff]` in each package's pyproject).
  **`fix = true` is set, so a plain `ruff check` rewrites files**; use `--no-fix` to only report.
  Baseline failure is expected: 12 errors repo-wide (9 in the pyqtp packages, 3 in `qtmonaco`).
  Ruff only lints `pyqtp/**/*.py` — `examples/` and `tests/` are not linted.
- `cd packages && uv run pyright` — uses `packages/pyrightconfig.json` (`venvPath: ".."`), so run it
  from `packages/`. Baseline: 18 pre-existing errors (stale `bcad.*` imports + missing `PySide6QtAds`).
  The `tests/` dirs sit inside the checked package dirs, so they must stay type-clean (helper modules
  importable as top-level modules only from their own dir — keep shared helpers inside one test file).
  Single file: `uv run pyright pyqtp-common/pyqtp/theme/theme.py`.
- Tests: `uv run pytest` (`pytest` + `pytest-qt` are dev deps of core/ide). Config
  `[tool.pytest.ini_options]` is in the **root** pyproject; tests live in
  `packages/pyqtp-core/tests/` (settings window) and `packages/pyqtp-ide/tests/`
  (LSP registry + live editor + stage-7 editor UX + stage-8.1 references/symbols/highlight live tests
   + stage-8.2 rename/formatting/codeAction live tests
   + editor-options/theme + workspace-edit-apply (test_workspace_edit_apply.py) unit tests
   + stage-9.1 project tree panel (test_project_tree.py, offscreen GUI без LSP/WebEngine, без маркеров)). Markers `live`
  (real LSP server processes) and
  `webengine` (real QWebEngineView/Monaco) are deselected by default via `addopts` → 54 passed / 27
  deselected; run them with `uv run pytest -m 'live or webengine'` (~2 min, they work offscreen) or
  all 81 in one
  process with `uv run pytest -m ''` (~2 min, full run keeps passing despite `os._exit` in
  `pytest_sessionfinish` eating the summary line — count the dots). Test-suite gotchas:
  - root `conftest.py` sets `QT_QPA_PLATFORM=offscreen` + `QTWEBENGINE_DISABLE_SANDBOX=1` at import
    and overrides `qapp` as a **session-scoped** fixture (pytest-qt's DESTROY_ON_POLICY would kill
    QApplication between tests). GUI fixtures must depend on `qapp`, else `QMainWindow.__init__`
    qFatal-aborts the process with no Python exception to catch.
  - PySide6 6.6 returns JS booleans from `runJavaScript` as `''` (both `true` and `false`!) — every
    JS predicate in tests must return a string marker (`'READY'`) and compare on the Python side.
    Same trap in JS: `getFoldingModel()` returns a **promise** (no `getAllFolds()` — probe
    `model.regions.getStartLineNumber/getEndLineNumber/isCollapsed(i)`), and in this Monaco build
    `EditorOption.effectiveFontSize` is missing (use `EditorOption.fontInfo` → `.fontSize` +
    `.zoomLevel`, or `getOption(monaco.editor.EditorOption.multiCursorModifier)` = `'altKey'`).
    `editor.getValue()` **keeps the model's trailing EOL** — compare test expectations against the
    source text with the final `\n`, not `rstrip`.
  - The active Monaco theme is **not readable via JS** in this bundle (monaco-editor 0.55):
    `monaco.editor.getActiveTheme`/`onDidThemeChange` and `EditorOption.theme` don't exist
    (`autoDetectHighContrast` is absent from the public `EditorOption` enum too — don't try to
    assert it). Probe the computed background of `.monaco-editor .monaco-editor-background` instead:
    `rgb(30, 30, 30)` = `vs-dark`, `rgb(255, 255, 254)` = `vs`. NB `test_lsp_editor_stack`
    semantic-token tests depend on the default `vs-dark` (bare editors never get the app theme) —
    light theme must not leak into them.
  - Offscreen WebEngine delivers real `QTest` key presses only to the DOM, **not** to Monaco
    (Monaco has no `inputarea` there) — and neither synthetic keydowns nor
    `monaco.editor.trigger(...)` reach the editor's actions; only
    `editor.trigger(..., getAction(id))` on the instance works. So keybinding tests assert the
    chords are registered (`editor._standaloneKeybindingService.getKeybindings()` →
    `getCommandId()`/`getHash()`) and then run the command via the instance.
  - `QtMonacoEditorWidget` buffers early bridge traffic (including startup `set_text`) until
    `bridge_initialized`, so a test that `setValue`s text right after `editor_init` gets it
    overwritten a moment later — re-issue `setValue` inside the readiness predicate.
  - pytest-qt 4.5 `waitUntil` has no `retry_interval` — expensive predicates use the local
    `wait_for(...)` polling helper. Its callback must return `None`/`True`/`False` — a
    truthy-but-not-bool return (e.g. a `set`) raises `ValueError`, so wrap with `bool(...)`.
  - pytest 8 applies `testpaths` only when the invocation dir == rootdir, so `addopts` also carries
    `--ignore-glob=*/qtmonaco/tests/*` and `--ignore-glob=qtmonaco/tests/*`; the vendored qtmonaco
    tests must never be collected (they error at import).
  - `pytest_sessionfinish` in the root conftest does `os._exit(exitstatus)`: Qt/C++ destructors of
    leftover widgets (main window + KDDockWidgets wrappers) run in the end-of-session garbage
    collector over already-invalid C++ pointers — heap-corruption abort (134/139) **after** all
    tests passed. Standalone harnesses with the same lifecycle exit fine, so don't "fix" the
    lifecycle; keep the hook. `PYQTP_NO_OS_EXIT=1` skips the hook — for debugging real test
    failures whose traceback prints after the hook (results are the same, the process may then
    abort in Qt destructors). The same conftest also disables the **cyclic GC at import**
    (`gc.disable()`, escape hatch `PYQTP_GC=1`): a gen2 collection *mid-session* finalizes the
    leaked KDD/QWebEngine wrappers (gc_collect_main → DockWidgetWrapper dtor → QWebEnginePage
    dtor → SIGTRAP in libQt6WebEngineCore, confirmed via coredump) — extra webengine tests shift
    GC timing and trigger it; keep GC off for the whole session.
- Run the IDE: `uv run python ide.py -v` (run from `packages/pyqtp-ide/pyqtp/`; cwd is irrelevant
  since all members are installed editable). Core smoke app: `uv run python launcher.py` from
  `packages/pyqtp-core/pyqtp/`. Examples: `packages/pyqtp-core/examples/{central_dock,kddw_docs}`.
- App CLI (every control/plugin can add its own): `-v/--verbose` (events log → DEBUG),
  `--reset-settings` (clears `QSettings(org, app)`), hidden `-d <logger_name>`.
- Root pyproject pulls `mypy`, but there is no mypy config — type checking here is **pyright**.

## Structure notes that are not obvious

- **No `__init__.py` anywhere** (implicit namespace packages). `python -m pyqtp...` does not work;
  run script files directly. Cross-package imports (core/ide importing `pyqtp.theme`, `pyqtp.utils`
  from common) are **not declared** in any pyproject — they resolve only because uv installs all
  workspace members editable into one shared venv.
- Extension points rely on that namespace merge:
  - Plugins: put a module in any package's `pyqtp/plugins/`; `PluginControl.plugins_import_all`
    walks `pyqtp.plugins.__path__` with `pkgutil` and collects `BasePlugin` subclasses whose class
    names don't start with `_`/`Base`. Active plugins are stored in QSettings as dotted name strings
    and loaded via `resolve_name`.
  - Assets: `AssetsManager` searches `pyqtp/assets/__path__` across packages (`images/`, `help/`,
    `fonts/`), so assets can be contributed by any distribution.
- Model–View plugin model: `PluggableObject` → `BaseControl` (persistent, created in
  `init_controls()`, contributes argv via `settings_setup_argparser`) vs `BasePlugin`
  (user-toggleable/reloadable). `BaseView` = dockable panel registered with
  `main_wnd.view_register(view, widget_area=...)`; view key defaults to `__qualname__`.
  Lifecycle order: `settings_read` → `register` → `setup` → `setup_ui` → … →
  `complete_pending_transactions` → `on_shutdown` → `teardown`.
- Docking: **only the KDD backend works** (`PyKDDockWidgets`, from the vendored wheel).
  `launcher.run_main_window` hardcodes kdd on the D-Bus start path; the non-DBus path uses
  `BCAD_GUI_USE_KDD=1`, otherwise it selects `ViewsManagerType.ads` → `ImportError`
  (`PySide6QtAds` is not a dependency and not installed). `pyqtp-core/pyqtp/base/docking_system/ads/`
  is dead code slated for deletion — it still imports the pre-rename `bcad.*`; don't repair it or
  add the missing dependency.
  KDD python-bindings close semantics (probed on v2.5.0): bare `kdd.DockWidget` has
  no `closeRequested`/`closed`/`closeDockWidget` signal or method (the existing
  `dock.closeRequested.connect` sites in kdd_views_manager are dead code that would
  AttributeError if exercised); Qt-level close (close()/QCloseEvent) is invisible to the
  KDD core — it only hides the widget, the dock stays "open"; only forceClose() actually
  closes (it emits no QCloseEvent, so no re-entrancy). Central documents
  (view_central_register, used by the IDE DocumentManager) rely on DeleteOnClose +
  `_CentralDockCloseFilter`: eat the tab-X QCloseEvent (ignore + return True), dispatch
  close_cb via QTimer.singleShot(0,...) which then calls forceClose(); keep the filter
  referenced — it hangs off the dock as `_pyqtp_close_filter`. isOpenChanged(False)
  purges documents_dock; ensure_visible_requested is wired to dock.open() +
  setAsCurrentTab() (QWidget.raise_ does NOT switch tabs) + `_focus_central_view`
  (Qt focus must follow the tab or the keyboard stays on the source document
  after an LSP jump; Qt ignores setFocus() on NoFocus containers — typical
  views are exactly such, the child editor holds the focus — so the helper
  focuses the first focusable descendant; JS editor.focus() alone only
  focuses inside its own webview and does NOT move Qt-level focus).
- Панель журнала событий показывает только логгеры, зарегистрированные через
  `EventsControl.events_log_register(name=, group=)` (единственное место, где к логгеру
  добавляется `QLoggingHandler`). Каждый контрол регистрирует свой сам в `setup_relations()`
  (или в `setup()`): `bcad.sys` — 'Система' (UserPluginControl), `bcad.lsp` — 'LSP' (LspHub),
  `bcad.med` + `qtmonaco` — 'Редактор' (DocumentManager), `bcad.help` — 'Справка' (HelpControl),
  `bcad.dev`/`bcad.project` — 'Разработка'/'Проект' (EventsControl.setup, т.к. их пишет само ядро).
  Фильтр представления (`FilteredLogMessageModel.filterAcceptsRow`) принимает точные имена и
  дочерние (`name.*`) — поэтому достаточно зарегистрировать родительский `qtmonaco`.
  Сообщения, отлогированные до регистрации (например `settings_read`), в журнал не
  попадают — это принято как данность.
- Система страниц настроек (общая инфраструктура, pyqtp-core
  `pyqtp/controls/settings_control/`, план — `docs/SETTINGS_PLAN.md`):
  - `SettingsPage` — простой класс-обёртка (НЕ BaseView и не QWidget):
    `key` (по умолчанию — имя класса), атрибуты класса `title`/`order`, ленивый
    `create_page_widget()` и опциональные `load()/apply()/reset_defaults()`.
    Окно гейтит кнопки по `supports_apply/supports_reset` (сравнение методов
    с базовым классом).
  - `SettingsControl(BaseControl)` — реестр `page_register/page_unregister/
    page_get/pages_sorted` (сортировка по `(order, key)`, сигналы
    `page_registered/page_deregistered`) + ленивый singleton
    `SettingsWindow(QDialog)`: немодальное, слева список страниц + фильтр
    поиска, справа `QStackedWidget`, снизу Применить/Откатить/Сбросить по
    умолчанию/Закрыть. Контракт `apply()/reset_defaults()`: бросает
    `ValueError/TypeError` с человекочитаемым текстом → красный статус-лейбл
    окна, источник не тронут. Close/X/Esc = скрытие (в `closeEvent`
    `event.spontaneous()` отличает закрытие пользователем от programmatic
    закрытия при teardown); состояние окна — ключи QSettings
    `SettingsControl/{geometry,splitter,current_page}`.
  - Фасады `main_wnd.settings_page_register/unregister` живут на
    **BaseMainWindow** (не на PluggableMainWindow — там же, где в `init_controls`
    регистрируется SettingsControl), плюс `settings_page_register/unregister/
    settings_window_show` доступны в Python-консоли (`register_main_wnd_api`).
    Пункт «Настройки приложения...» (Ctrl+,) подсаживается на priority=10 в уже
    существующее меню «Настройки» (priority=9), которое создаёт UserPluginControl.
  - Страницы сейчас: core «Внешний вид» (`appearance_page.AppearanceSettingsPage`,
    key `appearance`, order 0 — `apply()` = `theme_manager.switch_to` + синхрон
    corner-комбика `theme_switch_toolbar.theme_combo_box` через blockSignals,
    reset = «Светлая», persistence — общим `theme_settings_write`), ide
    «Редактор» (`doc_control/editor_settings_page.EditorSettingsPage`, key
    `editor`, order 10 — UX-опции Monaco (`pyqtp/web/editor_options.py`:
    minimap/сворачивание/колесо+Ctrl/размер шрифта/тема редактора), владелец —
    `DocumentManager`
    (`editor_ux_options/apply_editor_ux_options/reset_editor_ux_options`,
    персистентность — ключи QSettings `EditorOptions/*`, применение —
    широковещательный `update_editor_options()` + отдельный `set_theme()` по
    всем открытым редакторам; опции шлёт и `open_document`, т.к.
    `QtMonacoEditorWidget` появляется позже
    поздних вьюх),
    «Языковые серверы (LSP)» (`lsp_settings_page.LspRegistrySettingsPage`, key
    `lsp`, order 50) — её `LspHub` регистрирует в `setup()`; кнопок применения
    на самой странице нет — кнопки окна вызывают `registry_apply_json/
    registry_reset/registry_json`.
  - Квирка pyright/ruff при подклассировании `QDialog`: имена параметров
    оверрайдов `showEvent/closeEvent` должны совпадать со стабами PySide6
    (`arg__1`), а `showEvent` требует `# noqa: N802` — в pep8-naming белом
    списке есть `closeEvent`, но нет `showEvent`.
- Панель проекта (этап 9.1, план — `docs/IDE_ROADMAP.md`): контрол
  `ProjectControl` (`pyqtp-ide/pyqtp/controls/project_control/project_control.py`,
  key = qualname, регистрируется в `IdeMainWindow.init_controls` после
  `DocumentManager`) хранит корень проекта (QSettings ключ
  `ProjectControl/root_path`, при закрытии снимается), даёт console-API
  `project_root/project_open/project_close` и пункты меню «Файл»:
  «Открыть папку...» (Ctrl+Shift+O, priority=4) / «Закрыть папку» (priority=8).
  Панель — `ProjectTreeView` (`pyqtp/views/project_tree_view.py`, key
  `ProjectControl_tree_view`, `widget_area='left'`): ленивый
  `QFileSystemModel` + `_TreePruneProxy` (точечные записи + `__pycache__`/
  `node_modules` скрыты; пока проект закрыт, гейт `_enabled=False` не
  пропускает НИЧЕГО — иначе `QFileSystemModel` без rootPath показывает всё
  ФС от `/`, а `QFileSystemModel.clear()` в PySide6 6.6 нет). Двойной клик/
  Enter по файлу → `file_open_requested(str)` → `doc_ctrl.open_document`;
  reveal активного документа — только по `document_opened` (сигнала о смене
  активной вкладки в core нет) и идёт асинхронно шагами через
  `QTimer.singleShot(0,...)`: `QFileSystemModel` наполняется изнутри, а
  KDD-док-менеджер у скрытой панели `canFetchMore` не опрашивает — первый
  уровень грузится вручную (`fetchMore` после `setRootPath`).

## Runtime gotchas

- `launcher.py` forces `QT_QPA_PLATFORM=xcb` on Linux/macOS, so a headless/offscreen smoke test of a
  GUI launch will not work — prefer importing modules over launching a window.
- Startup is single-instance over D-Bus by default. If the D-Bus iface is already registered, the new
  process activates the existing window and exits `0` — a silent success with no new window means an
  instance is already running. `BCAD_DISABLE_DBUS=1` skips D-Bus entirely.
- IDE editor = `pyqtp/web/qtmonaco_editor.py:QtMonacoEditorWidget` (subclass of `qtmonaco.Monaco`,
  a `QWebEngineView` loading `setHtml` of `qtmonaco/js_build/index.html` with a `file://` base URL
  and QWebChannel object `connector`). `views/monaco_document_view.py` wires `text_changed`/
  `get_text()` to the `TextDocumentView` modified/save contract.
- LSP in the live IDE goes through **`pyqtp-ide/pyqtp/controls/lsp_control/lsp_hub.py`**
  (`LspHub(BaseControl)`, registered in `IdeMainWindow.init_controls` before
  `DocumentManager`): a `websockets` server on a free 127.0.0.1 port inside its own
  thread running asyncio. Each editor connects per server to
  `ws://127.0.0.1:<port>/<urlencoded file URI>/<server-name>` (uri is quoted with
  `safe=''`, so the first raw `/` unambiguously starts the optional server-name
  segment; no name = first server of the language stack — stage-2 compat); the hub
  derives language/`root_uri` from that path, keeps a stdio process pool keyed
  `(command, root_uri)`, does the real `initialize` handshake and replays cached
  InitializeResult to clients, multiplexes request ids as `c{conn}:{id}`, broadcasts
  `publishDiagnostics` to the pool's connections, answers server-initiated requests
  (`workspace/configuration` etc.) itself, and on IDE shutdown terminates servers
  (`shutdown`+`exit`, then kill). Unknown/absent server name → stub handler with empty
  capabilities (no providers registered on the JS side).
- The server registry (`lsp_registry.py`, `LspRegistry`) is **configurable, not hardcoded**:
  JSON `languageId → {extensions, root_markers, servers: [{name, command, enabled}]}`
  (list order = priority), stored as a JSON string in QSettings key
  `<LspHub.key>/registry_json` (only when it differs from the built-in defaults, which are
  `python → basedpyright-langserver --stdio` + `ruff server` (pyqtp-ide deps, resolved via
  PATH — under `uv run` the venv/bin copy wins) and `cpp → clangd --background-index`
  (system binary; clangd ≥ 23 removed the `--stdio` flag — stdio is its default transport,
  so don't add it back; compdb is found by clangd itself, `root_markers` for cpp are
  `compile_commands.json`/`compile_flags.txt`/`CMakeLists.txt`/`.git`). The whole C/C++
  family (`.c/.h/.hpp/.cpp/.cc/.cxx/.hxx`) shares languageId `cpp` — both in the registry
  and in `pyqtp/web/monaco_languages.py` (Monaco highlight `cpp`).
  `LspHub.registry_json()`,
  `registry_apply_json(text)` (whole-registry swap; applies to new editor opens; running
  server processes live until shutdown) and `registry_reset()` are the programmatic API;
  the GUI editor is the LSP page in the app settings window (stage 3.4 of
  `docs/LSP_PLAN.md` — done, see the settings-system note above).
- `qtmonaco.pylsp_provider`
  (upstream `python -m pylsp --ws`) is now only the fallback when a bare `Monaco()` is
  used without `lsp_host`. The JS side keeps one `LspClientManager` per editor
  (qtmonaco `lsp.ts`): Python sends `lsp_stack` (list of `{url, owner, priority,
  languageId}`; a plain `lsp_url` string = single-entry stack), providers register once
  per client gated on the server's InitializeResult capabilities; completion/hover come
  from every supporting server (Monaco merges); signatureHelp and definition are each
  registered exactly once per language — the manager grants the slot to the first
  supporting server by priority (`claimProvider(kind, languageId, client)`); python
  snippets are gated `priority==0 && languageId=='python'`. Monaco markers expose the
  model uri as `resource` (not `uri`)
  when querying `getModelMarkers` from Python, and marker `owner` is the server name.
 - Cross-document navigation (stage 6, `docs/LSP_PLAN.md`): go-to-definition/references
   into **other files** arrives as JS bridge message `_open_uri_request` →
   `QtMonacoEditorWidget.open_uri_requested` signal (handled via the bridge's
   `hasattr` dispatch — vendored `monaco.py` untouched) → `MonacoDocumentView` connects it
   to `DocumentManager.open_definition_uri` (validates file scheme/existence, `open_document`
   opens-or-activates the tab, `set_cursor(..., move_to_position='center')` places the caret —
   buffered by the Connector until the new editor's bridge initializes). Stage 6.1
   follow-ups: tab activation works via `view_central_register` wiring (see the Docking
   note above); reveal/focus for not-yet-laid-out editors is handled by the
   `pendingCursor` patch in `main.ts` (vendored).
- Legacy editor stack (stdio `lsp_control/`, `EditorBridge` in `web/monaco_editor_widget.py`,
  `web/editor/` with `editor.html` + `min/`, the `iide://` scheme handler and its registration in
  `ide.py`) **was deleted**. The new `controls/lsp_control/` (stage 2 of `docs/LSP_PLAN.md`) is a
  different implementation (ws-hub), not a restoration of that stack. The extension→Monaco-language
  mapping the old stack shared now lives (slimmed to
  a flat dict) in `pyqtp/web/monaco_languages.py`.
- `packages/qtmonaco/` is a vendored workspace member (squash-subtree of upstream — see the git
  note at the top; there is no nested `.git`, so its changes are committed in the main repo
  normally, and the local patches below are already committed there). Local patches so far:
  `pyside6` extra in its pyproject relaxed to
  `PySide6>=6.6.2` (upstream `>=6.8` conflicts with `build_kddw` pin `pyside6==6.6.2`); TS LSP
  patches (marked `vendored-patch` in code) — document lifecycle `didOpen`/debounced `didChange`/
  `didClose` moved into `LspClient` (`lsp.ts`, incl. real initialize capabilities and `setModel()`
  wiring in `main.ts`) and removal of the lazy per-request `didOpen` from `prepareDocument()`
  (`providers.ts`); `textDocument/publishDiagnostics` → Monaco `setModelMarkers(owner=<server>)`
  with uri filter (stage 1 of `docs/LSP_PLAN.md`); LSP→Monaco completion-item mapping in
  `providers.ts` (`mapCompletionItem`: `textEdit`→range/insertText incl. insert/replace, LSP
  ranges→IRange, kind table, `insertTextFormat=2`→`insertTextRules`, `additionalTextEdits`,
  deprecated→`tags`, server `command` dropped; lazy `resolveCompletionItem` for docs/auto-imports)
  — the raw `...item` spread broke accept for servers that put text only in `textEdit`
  (basedpyright): monaco crashed in `trimOverwrite` (`undefined.replaceAll`), nothing inserted;
  `LspClientManager` + `lsp_stack` message for multi-server stacks (stage 3.1, `lsp.ts`/`main.ts`);
  `createDefinitionProvider` (Location/LocationLink mapping) and generalized `claimProvider`
  single-owner registration for signatureHelp+definition, snippets gate `priority==0 &&
  languageId=='python'` (stage 4); semantic highlighting (stage 5):
  `createSemanticTokensProvider` (`semanticTokens/full` → `Uint32Array` data verbatim,
  legend from the server's own InitializeResult — servers ignore the client legend;
  `claimProvider("semanticTokens")` single-owner; disabled when the model has
  prepended data) + `extractSemanticLegend`/draft-3.16 hybrid capabilities in `lsp.ts`,
  and `"semanticHighlighting.enabled": true` in `main.ts` defaultEditorOptions (built-in
  standalone themes have it off → the feature never starts otherwise);
  cross-document navigation (stage 6): `registerEditorOpener` in `main.ts` —
  same-model returns `false` (local jumps stay Monaco's), foreign resources send
  `_open_uri_request {uri, range}` to Python and return `true` (without an opener
  standalone Monaco silently does nothing for foreign resources);
  stage 6.1 `set_cursor` hardening in `main.ts` —
  `pendingCursor`/`scheduleRevealWhenLaidOut`: when `getLayoutInfo()` is zero (hidden
  tab / fresh dock), reveal is deferred to the first `onDidLayoutChange` with a real
  viewport; pending is cleared by `set_text`/`setActiveEditor`; `editor.focus()` on
  every set_cursor (caret of the activated document must be visible);
  autocomplete fix (stage 6.2) — `LspClient.flushDocumentChanges()` in `lsp.ts`
  (clear the didChange debounce timer and send immediately), wired as a required
  param into the 4 provider factories in `providers.ts`
  (completion/signatureHelp/hover/definition) and called before every
  `sendRequest`: Monaco fires providers on trigger chars ('.' '(') faster than
  the 300 ms didChange debounce, so the server completed over stale text (no '.'
  yet → no member suggestions); hub routing is sequential so the flushed
  didChange always precedes the request;
  built-in editor UX (stage 7, `docs/IDE_ROADMAP.md`): in `main.ts` —
  `monaco.editor.addKeybindingRules` for actions Monaco standalone doesn't bind
  (fontZoomIn/Out/Reset `Ctrl+=`/`Ctrl+-`/`Ctrl+0`, smartSelect expand/shrink
  `Ctrl+Shift+.`/`Ctrl+Shift+,`; find `Ctrl+F`/`Ctrl+H` and folding default chords
  stay built-in), own in-document jump history `navBack`/`navForward` (limit 80) +
  commands `qtmonaco.editor.navigation.goBack/goForward` bound to Alt+←/→
  (`when: 'editorFocus'`): recorded in `onDidChangeCursorPosition` when the line
  delta ≥ `NAV_MIN_LINES` (4) and the reason is not Undo/Redo, `_navNavigating`
  guards our own moves (otherwise every back would push a forward point), history
  is cleared on `model` swap and on editor replacement in `setActiveEditor`, and
  `seedNavHistory()` re-arms `navLastPos` from the current cursor after every model
  swap/editor replacement/whole-text `set_text` — without that seed the first big
  jump after opening a document has no start point and Alt+← does nothing (this was
  the live-IDE bug behind `test_stage7_navigation_first_jump_recorded`);
  `attachEditorListeners()` centralizes `onDidChangeModelContent` (the `_current_text`
  sync) + `onDidChangeCursorPosition` wiring for the real editor and the placeholder
  (called only from `setActiveEditor`, which also disposes the old cursor disposable);
  `defaultEditorOptions` now sets `multiCursorModifier: "alt"` (so Ctrl+click stays
  "go to definition") and `scrollBeyondLastLine: false` — the rest of the Monaco UX
  options (minimap/folding/font zoom/smart select/…) come from Python:
  `pyqtp-ide/pyqtp/web/editor_options.py` + `QtMonacoEditorWidget.update_editor_options()`;
  editor theme setting (stage 7): in `main.ts` —
  `appliedTheme` module var remembers the theme applied via the existing
  `case "theme"` message (vendored `Monaco.set_theme` API, unchanged), so
  `switchToStandaloneEditor` recreates the editor with the remembered theme
  instead of the hardcoded `vs-dark` (and `switchToDiffEditor` inherits it,
  still overridable by `open_diffs(options=...)`); `defaultEditorOptions` also
  sets `autoDetectHighContrast: false` — otherwise the OS high-contrast probe
  silently overrides the chosen theme with `hc-*`. Python side: the `theme` key
  (auto/light/dark) lives in the same `EditorOptions` QSettings dict but is
  NOT part of the `update_editor_options` payload — it resolves via pure
  `resolve_monaco_theme(options, app_is_dark=)` (`editor_options.py`) and is
  sent as `Monaco.set_theme('vs'|'vs-dark')`; `DocumentManager` applies it on
  `open_document`/broadcast and re-applies to open docs on
  `theme_manager.theme_changed` (auto mode follows `Theme.is_dark`, like the
  IPython console colors);
  stage 8.2 (`docs/IDE_ROADMAP.md`) — rename/formatting/codeAction providers in
  `providers.ts` (factories gated on server capabilities + `claimProvider`:
  `rename`/`prepareRename` and `formatting`+`rangeFormatting` are single-owner,
  codeAction registers on EVERY supporting server — Monaco merges them) and the
  WorkspaceEdit split (`splitWorkspaceEdit`/`foreignWorkspaceEditPayload` in
  `providers.ts`): local part becomes a Monaco
  `{edits:[{resource, textEdit:{range,text}}]}` WITHOUT `versionId`
  (bulkEditService would veto the whole edit on version mismatch), foreign part
  is sent verbatim as `documentChanges` to Python via bridge message
  `_apply_workspace_edit_request` (handled by DocumentManager →
  `workspace_edit.py`: open doc gets `apply_lsp_text_edits` into its editor,
  closed doc is patched on disk); codeAction foreign parts are deferred with a
  `fe{N}` token (`pendingForeignEdits` in `main.ts`, command id
  `qtmonaco.lsp.applyForeignEdit`), rename foreign parts apply immediately;
  `LspClient` keeps the raw `publishDiagnostics` cache for the codeAction
  context (`basedpyright` needs `data`) and `flushDocumentChanges()` before
  requests; new bridge case `apply_lsp_text_edits` in `main.ts` applies
  LSP-coordinate edits (lspHeader line offset) via `model.pushEditOperations`
  (plain `model.applyEdits` is NOT undoable — `computeUndoEdits` defaults to
  false — and pushEditOperations makes the whole batch one undo point).
  Monaco-0.55 quirks found here: the returned `CodeActionList` object MUST have
  `dispose()` (Monaco puts it in a DisposableStore), `CodeActionContext.only` is
  a single STRING in Monaco but LSP wants `string[]` (basedpyright crashed on
  `.some()`), and cold-start codeAction fetches racing diagnostics make
  basedpyright error «parserOutput» (harmless, provider catches it).
  python patch in `monaco.py` —
  `Monaco.__init__` takes an `lsp_host` param and no longer eagerly spawns
  `get_pylsp_host()` (lazy `_resolve_lsp_host()` fallback keeps standalone
  behavior; stage 2). Rebuild `js_build` after any TS change. Keep it
  out of ruff scope (its files are the 3 baseline errors); pyright excludes it via
  `packages/pyrightconfig.json` (imported types still resolve).

## Conventions

- Перед `git commit`/`git push` — всегда спрашивать подтверждение пользователя
  (показать что коммитится/пушится и сообщение коммита, внести свои предложения
  по составу среза и сообщению); пользователь может
  скорректировать состав среза или сообщение.
- Comments, docstrings and UI strings are Russian — keep them Russian.
- Loggers keep the pre-rename `bcad.*` names (`bcad.dev`, `bcad.sys`, `bcad.med`, `bcad.lsp`,
  `bcad.project`); that is intentional, not a bug to fix.
- Ruff selects `ALL` with a large ignore list, Google docstring convention, and
  `quote-style = "single"`; Qt camelCase overrides (`closeEvent`, `paintEvent`, …) are whitelisted —
  keep Qt method spellings unchanged.
