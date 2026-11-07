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

Verify in this order: **ruff → pyright → run the app** (no test suite exists; pytest is only a dev dep).

- `uv run ruff check <path>` — configs live per package (`[tool.ruff]` in each package's pyproject).
  **`fix = true` is set, so a plain `ruff check` rewrites files**; use `--no-fix` to only report.
  Baseline failure is expected: 13 errors repo-wide (10 in the pyqtp packages, 3 in `qtmonaco`).
  Ruff only lints `pyqtp/**/*.py` — `examples/` are not linted.
- `cd packages && uv run pyright` — uses `packages/pyrightconfig.json` (`venvPath: ".."`), so run it
  from `packages/`. Baseline: 18 pre-existing errors (stale `bcad.*` imports + missing `PySide6QtAds`).
  Single file: `uv run pyright pyqtp-common/pyqtp/theme/theme.py`.
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
    «Языковые серверы (LSP)» (`lsp_settings_page.LspRegistrySettingsPage`, key
    `lsp`, order 50) — её `LspHub` регистрирует в `setup()`; кнопок применения
    на самой странице нет — кнопки окна вызывают `registry_apply_json/
    registry_reset/registry_json`.
  - Квирка pyright/ruff при подклассировании `QDialog`: имена параметров
    оверрайдов `showEvent/closeEvent` должны совпадать со стабами PySide6
    (`arg__1`), а `showEvent` требует `# noqa: N802` — в pep8-naming белом
    списке есть `closeEvent`, но нет `showEvent`.

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
  languageId=='python'` (stage 4);
  python patch in `monaco.py` —
  `Monaco.__init__` takes an `lsp_host` param and no longer eagerly spawns
  `get_pylsp_host()` (lazy `_resolve_lsp_host()` fallback keeps standalone
  behavior; stage 2). Rebuild `js_build` after any TS change. Keep it
  out of ruff scope (its files are the 3 baseline errors); pyright excludes it via
  `packages/pyrightconfig.json` (imported types still resolve).

## Conventions

- Comments, docstrings and UI strings are Russian — keep them Russian.
- Loggers keep the pre-rename `bcad.*` names (`bcad.dev`, `bcad.sys`, `bcad.med`, `bcad.lsp`,
  `bcad.project`); that is intentional, not a bug to fix.
- Ruff selects `ALL` with a large ignore list, Google docstring convention, and
  `quote-style = "single"`; Qt camelCase overrides (`closeEvent`, `paintEvent`, …) are whitelisted —
  keep Qt method spellings unchanged.
